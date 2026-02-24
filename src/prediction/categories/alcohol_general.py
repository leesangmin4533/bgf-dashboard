"""
일반주류 카테고리 예측 모듈

일반주류(052 양주, 053 와인)의 동적 안전재고 계산:
- beer/soju와 유사한 주말 집중 패턴
- 낮은 판매량 + 높은 단가 → 보수적 발주
- 요일별 안전재고일수: 평일 2.0일, 금/토 3.0일
- 상한선: 일평균 x 10일 (주류는 유통기한 무한이므로 여유있게)

공식: 일평균 x 안전재고일수(요일별) x 요일계수
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 일반주류 설정
# =============================================================================
ALCOHOL_GENERAL_TARGET_CATEGORIES = ["052", "053"]

ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["052", "053"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 2.0,
    "weekend_safety_days": 3.0,
    "max_stock_enabled": True,
    "max_stock_days": 10.0,
}

# 일반주류 요일별 안전재고일수 (beer/soju 구조 재사용)
# 금/토: 3.0일, 나머지: 2.0일
ALCOHOL_WEEKDAY_SAFETY = {
    0: 2.0,  # 월
    1: 2.0,  # 화
    2: 2.0,  # 수
    3: 2.0,  # 목
    4: 3.0,  # 금
    5: 3.0,  # 토
    6: 2.0,  # 일
}

# 일반주류 요일별 계수 - 주말 집중 패턴 (beer/soju보다 편차 큼)
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
DEFAULT_WEEKDAY_COEF = {
    0: 0.80,  # 월
    1: 0.80,  # 화
    2: 0.85,  # 수
    3: 0.90,  # 목
    4: 1.10,  # 금
    5: 1.50,  # 토
    6: 2.00,  # 일
}


@dataclass
class AlcoholGeneralPatternResult:
    """일반주류 패턴 분석 결과"""
    item_cd: str                  # 상품코드
    mid_cd: str                   # 중분류 코드
    daily_avg: float              # 일평균 판매량
    weekday_coef: float           # 적용된 요일 계수
    safety_days: float            # 적용된 안전재고 일수
    final_safety_stock: float     # 최종 안전재고 (개수)
    max_stock: float              # 최대 재고 상한선
    skip_order: bool              # 발주 스킵 여부
    skip_reason: str              # 스킵 사유
    data_days: int                # 실제 데이터 일수


def is_alcohol_general_category(mid_cd: str) -> bool:
    """일반주류 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        일반주류 카테고리(052 양주, 053 와인)이면 True
    """
    return mid_cd in ALCOHOL_GENERAL_TARGET_CATEGORIES


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 14,
                           store_id: Optional[str] = None) -> dict:
    """매장 판매 데이터에서 요일별 계수 학습.

    최근 30일 데이터로 요일별 평균 산출, 전체평균 대비 비율.
    0.5~2.5 클램프. min_data_days 미만이면 기본값.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 데이터 일수
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        {0: coef, 1: coef, ..., 6: coef} (Python weekday: 월=0, 일=6)
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

        # SQLite dow: 0=일,1=월,...,6=토 → Python weekday: 월=0,...,일=6
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
        logger.warning(f"[주류일반] 요일 패턴 학습 실패 (mid_cd={mid_cd}): {e}")
        return dict(DEFAULT_WEEKDAY_COEF)


def analyze_alcohol_general_pattern(
    item_cd: str,
    mid_cd: str = "",
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> AlcoholGeneralPatternResult:
    """일반주류 상품 패턴 분석

    beer/soju와 유사한 요일 패턴 기반으로 안전재고를 계산한다.
    금/토에는 3.0일치, 나머지 요일에는 2.0일치를 적용하고,
    현재 재고+미입고가 상한선 이상이면 발주를 스킵한다.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        AlcoholGeneralPatternResult
    """
    config = ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    # 오늘 요일
    weekday = datetime.now().weekday()  # 월=0, 일=6

    # 요일별 안전재고일수 분기 (beer/soju 구조 재사용)
    if weekday in (4, 5):  # 금, 토
        safety_days = config["weekend_safety_days"]
    else:
        safety_days = config["default_safety_days"]

    # 요일계수 학습
    learned_coef = _learn_weekday_pattern(mid_cd, db_path, config["min_data_days"], store_id=store_id)
    weekday_coef = learned_coef.get(weekday, 1.0)

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

    # 데이터 부족 시 기본 안전재고일수 적용
    if data_days < config["min_data_days"]:
        safety_days = config["default_safety_days"]

    # 안전재고 수량 (요일계수 적용)
    final_safety_stock = daily_avg * safety_days * weekday_coef

    # 최대 재고 상한선
    max_stock = daily_avg * config["max_stock_days"] if daily_avg > 0 else 0

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return AlcoholGeneralPatternResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        daily_avg=round(daily_avg, 2),
        weekday_coef=round(weekday_coef, 2),
        safety_days=safety_days,
        final_safety_stock=round(final_safety_stock, 2),
        max_stock=round(max_stock, 1),
        skip_order=skip_order,
        skip_reason=skip_reason,
        data_days=data_days,
    )


def calculate_alcohol_general_dynamic_safety(
    item_cd: str,
    mid_cd: str = "",
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, AlcoholGeneralPatternResult]:
    """일반주류 동적 안전재고 계산

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
    pattern = analyze_alcohol_general_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_alcohol_general_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, Optional[AlcoholGeneralPatternResult]]:
    """안전재고 계산 (일반주류 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 일반주류_패턴_정보)
        - 일반주류가 아니면 패턴_정보는 None
    """
    # 일반주류가 아니면 기본값 반환
    if not is_alcohol_general_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 일반주류: 동적 안전재고 계산
    if item_cd and ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_alcohol_general_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
