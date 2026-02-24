"""
생활용품 카테고리 예측 모듈

생활용품(036, 037, 056, 057, 086)의 동적 안전재고 계산:
- 결품 방지 최우선 (필수 생활용품 → 품절 시 고객 이탈)
- 안정적 수요, 비식품이므로 폐기 리스크 없음
- 요일 패턴 거의 없음 (균일 계수 1.0)
- 최소 1개 항상 보유, 현재재고 0이면 강제 발주

공식: 일평균 × 안전재고일수(1.5일 고정), 최소 1개 보장
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 생활용품 설정
# =============================================================================
DAILY_NECESSITY_CATEGORIES = ["036", "037", "056", "057", "086"]
# 036: 의약외품, 037: 건강기능, 056: 목욕세면, 057: 위생용품, 086: 안전상비의약품

DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["036", "037", "056", "057", "086"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 1.5,
    "min_stock": 1,
    "max_stock_enabled": True,
    "max_stock_days": 10.0,
}

# 생활용품 요일별 계수 (요일 패턴 거의 없음 - 균일)
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
DEFAULT_WEEKDAY_COEF = {
    0: 1.00,  # 월
    1: 1.00,  # 화
    2: 1.00,  # 수
    3: 1.00,  # 목
    4: 1.00,  # 금
    5: 1.00,  # 토
    6: 1.00,  # 일
}


@dataclass
class DailyNecessityPatternResult:
    """생활용품 패턴 분석 결과"""
    item_cd: str                  # 상품코드
    mid_cd: str                   # 중분류 코드
    daily_avg: float              # 일평균 판매량
    safety_days: float            # 안전재고 일수 (1.5 고정)
    final_safety_stock: float     # 최종 안전재고 수량
    min_stock: int                # 최소 보유 수량 (1)
    max_stock: float              # 최대 재고 상한선 (일평균 × 10일)
    skip_order: bool              # 발주 스킵 여부
    skip_reason: str              # 스킵 사유
    weekday_coef: float           # 적용된 요일 계수
    data_days: int                # 실제 데이터 일수


def is_daily_necessity_category(mid_cd: str) -> bool:
    """생활용품 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        생활용품 카테고리(036, 037, 056, 057, 086)이면 True
    """
    return mid_cd in DAILY_NECESSITY_CATEGORIES


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db의 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 14,
                           store_id: Optional[str] = None) -> dict:
    """매장 판매 데이터에서 요일별 계수 학습

    생활용품은 요일 패턴이 거의 없으므로 대부분 기본값(1.0)이 반환됨.
    데이터가 충분하면 실제 패턴을 학습하되, 0.5~2.5 범위로 클램핑.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 데이터 일수 (기본 14일)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        요일별 계수 딕셔너리 {0: 계수, 1: 계수, ..., 6: 계수}
    """
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        if store_id:
            cursor.execute("""
                SELECT CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                       SUM(sale_qty) as total, COUNT(DISTINCT sales_date) as days
                FROM daily_sales
                WHERE mid_cd = ? AND store_id = ? AND sales_date >= date('now', '-30 days')
                GROUP BY dow
            """, (mid_cd, store_id))
        else:
            cursor.execute("""
                SELECT CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                       SUM(sale_qty) as total, COUNT(DISTINCT sales_date) as days
                FROM daily_sales
                WHERE mid_cd = ? AND sales_date >= date('now', '-30 days')
                GROUP BY dow
            """, (mid_cd,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return dict(DEFAULT_WEEKDAY_COEF)

        # SQLite %w: 0=일, 1=월, ..., 6=토 → Python weekday: 0=월, ..., 6=일
        sqlite_to_py = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}
        dow_avgs = {}
        total_days = 0

        for dow_sqlite, total_qty, day_count in rows:
            py_dow = sqlite_to_py.get(dow_sqlite, dow_sqlite)
            dow_avgs[py_dow] = total_qty / max(1, day_count)
            total_days += day_count

        if total_days < min_data_days:
            return dict(DEFAULT_WEEKDAY_COEF)

        overall_avg = sum(dow_avgs.values()) / max(1, len(dow_avgs))
        if overall_avg <= 0:
            return dict(DEFAULT_WEEKDAY_COEF)

        learned = {}
        for d in range(7):
            if d in dow_avgs and overall_avg > 0:
                coef = dow_avgs[d] / overall_avg
                learned[d] = max(0.5, min(2.5, round(coef, 2)))
            else:
                learned[d] = DEFAULT_WEEKDAY_COEF.get(d, 1.0)

        return learned

    except Exception as e:
        logger.warning(f"[생활용품] 요일 패턴 학습 실패 (mid_cd={mid_cd}): {e}")
        return dict(DEFAULT_WEEKDAY_COEF)


def analyze_daily_necessity_pattern(
    item_cd: str,
    mid_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> DailyNecessityPatternResult:
    """생활용품 상품 패턴 분석

    결품 방지가 최우선이므로 안전재고를 넉넉히 설정하고,
    장기유통(14일 상한)으로 과잉 재고 부담이 적음.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        DailyNecessityPatternResult: 분석 결과 데이터클래스
    """
    config = DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    # 요일 계수 학습
    weekday_coefs = _learn_weekday_pattern(mid_cd, db_path, config["min_data_days"], store_id=store_id)
    order_weekday = datetime.now().weekday()  # 0=월, 6=일
    weekday_coef = weekday_coefs.get(order_weekday, 1.0)

    # DB에서 일평균 판매량 조회
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    if store_id:
        cursor.execute("""
            SELECT COUNT(DISTINCT sales_date) as data_days,
                   COALESCE(SUM(sale_qty), 0) as total_sales
            FROM daily_sales
            WHERE item_cd = ? AND store_id = ?
            AND sales_date >= date('now', '-' || ? || ' days')
        """, (item_cd, store_id, config["analysis_days"]))
    else:
        cursor.execute("""
            SELECT COUNT(DISTINCT sales_date) as data_days,
                   COALESCE(SUM(sale_qty), 0) as total_sales
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-' || ? || ' days')
        """, (item_cd, config["analysis_days"]))

    row = cursor.fetchone()
    conn.close()

    data_days = row[0] or 0
    total_sales = row[1] or 0

    # 일평균 계산
    daily_avg = (total_sales / data_days) if data_days > 0 else 0.0

    # 안전재고 일수 (고정 1.5일 - 수요 안정적이므로 회전율 분기 불필요)
    safety_days = config["default_safety_days"]

    # 안전재고 수량
    final_safety_stock = daily_avg * safety_days

    # 최소 보유 수량 보장
    min_stock = config["min_stock"]
    if final_safety_stock < min_stock:
        final_safety_stock = float(min_stock)

    # 최대 재고 상한선 (일평균 × 10일, 장기유통이므로 여유)
    max_stock = daily_avg * config["max_stock_days"] if daily_avg > 0 else float(min_stock)

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return DailyNecessityPatternResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        daily_avg=round(daily_avg, 2),
        safety_days=safety_days,
        final_safety_stock=round(final_safety_stock, 2),
        min_stock=min_stock,
        max_stock=round(max_stock, 2),
        skip_order=skip_order,
        skip_reason=skip_reason,
        weekday_coef=round(weekday_coef, 2),
        data_days=data_days,
    )


def calculate_daily_necessity_dynamic_safety(
    item_cd: str,
    mid_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, DailyNecessityPatternResult]:
    """생활용품 동적 안전재고 계산

    결품 방지가 최우선이므로 현재재고가 0이면 최소 1개 강제 발주.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_daily_necessity_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )

    # 특수 로직: 현재재고 0이면 최소 1개 강제 발주 (결품 방지 최우선)
    if current_stock == 0 and pattern.skip_order:
        # skip_order 해제 - 재고 0이면 무조건 발주
        pattern = DailyNecessityPatternResult(
            item_cd=pattern.item_cd,
            mid_cd=pattern.mid_cd,
            daily_avg=pattern.daily_avg,
            safety_days=pattern.safety_days,
            final_safety_stock=max(pattern.final_safety_stock, float(pattern.min_stock)),
            min_stock=pattern.min_stock,
            max_stock=pattern.max_stock,
            skip_order=False,
            skip_reason="",
            weekday_coef=pattern.weekday_coef,
            data_days=pattern.data_days,
        )

    return pattern.final_safety_stock, pattern


def get_safety_stock_with_daily_necessity_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, Optional[DailyNecessityPatternResult]]:
    """안전재고 계산 (생활용품 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 생활용품_패턴_정보)
        - 생활용품이 아니면 패턴_정보는 None
    """
    # 생활용품이 아니면 기본값 반환
    if not is_daily_necessity_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 생활용품: 동적 안전재고 계산
    if item_cd and DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_daily_necessity_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    safety = daily_avg * default_days
    min_stock = DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["min_stock"]
    if safety < min_stock:
        safety = float(min_stock)
    return safety, None
