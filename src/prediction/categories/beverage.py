"""
음료류 카테고리 예측 모듈

음료류(010, 039, 043, 045, 048)의 동적 안전재고 계산:
- 대상: 제조음료(010), 과일야채음료(039), 차음료(043), 아이스드링크(045), 얼음(048)
- 회전율 기반: 고회전(5+개/일) 1.5일, 중회전(2-5개/일) 1.5일, 저회전(<2개/일) 1.0일
- 주말 소폭 증가 패턴 + 요일계수 DB 학습
- 최대재고: 7일치
- 요일계수: DB 학습 기반 (데이터 부족 시 DEFAULT_WEEKDAY_COEF 사용)

공식: 일평균 x 안전재고일수(회전율별) x 요일계수
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple, List

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 음료류 설정
# =============================================================================
BEVERAGE_CATEGORIES = ["010", "039", "043", "045", "048"]
# 010: 제조음료, 039: 과일야채음료, 043: 차음료, 045: 아이스드링크, 048: 얼음

# 음료류 기본 요일별 계수 (주말 소폭 증가)
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
DEFAULT_WEEKDAY_COEF = {
    0: 0.95,  # 월 (소폭 감소)
    1: 0.95,  # 화 (소폭 감소)
    2: 1.00,  # 수
    3: 1.00,  # 목
    4: 1.00,  # 금
    5: 1.15,  # 토 (주말 증가)
    6: 1.10,  # 일 (주말 증가)
}

# 음료류 회전율별 안전재고 설정
BEVERAGE_SAFETY_CONFIG = {
    "high_turnover": {       # 고회전: 일평균 5개 이상
        "min_daily_avg": 5.0,
        "safety_days": 1.5,
        "description": "고회전 음료 (매일발주+익일배송)"
    },
    "medium_turnover": {     # 중회전: 일평균 2-5개
        "min_daily_avg": 2.0,
        "safety_days": 1.5,
        "description": "중회전 음료 (매일발주+익일배송)"
    },
    "low_turnover": {        # 저회전: 일평균 2개 미만
        "min_daily_avg": 0.0,
        "safety_days": 1.0,
        "description": "저회전 음료 (재고 최소화)"
    },
}

# 음료류 동적 안전재고 설정
BEVERAGE_DYNAMIC_SAFETY_CONFIG = {
    "target_categories": ["010", "039", "043", "045", "048"],
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 1.5,
    "max_stock_enabled": True,
    "max_stock_days": 7.0,
}


def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db의 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class BeveragePatternResult:
    """음료류 판매 패턴 분석 결과"""
    item_cd: str

    # 데이터 기간 정보
    actual_data_days: int          # 실제 데이터 일수
    analysis_days: int             # 분석 기준 일수
    has_enough_data: bool          # 최소 데이터 충족 여부

    # 판매량 정보
    total_sales: int               # 총 판매량
    daily_avg: float               # 일평균 판매량

    # 회전율 정보
    turnover_level: str            # high_turnover / medium_turnover / low_turnover / unknown
    safety_days: float             # 적용된 안전재고 일수

    # 요일 정보
    order_weekday: int             # 발주 요일 (0=월, 6=일)
    weekday_coef: float            # 적용된 요일 계수
    weekday_coef_source: str       # 'learned' 또는 'default'

    # 안전재고
    safety_stock: float            # 안전재고 수량

    # 재고 상한선
    max_stock: float               # 최대 재고 상한선 (7일치)
    current_stock: int             # 현재 재고
    pending_qty: int               # 미입고 수량

    # 발주 스킵
    skip_order: bool               # 발주 스킵 여부
    skip_reason: str               # 스킵 사유

    # 최종 결과
    final_safety_stock: float      # 최종 안전재고
    turnover_description: str      # 회전율 설명


def is_beverage_category(mid_cd: str) -> bool:
    """음료류 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        음료류 카테고리(010, 039, 043, 045, 048)이면 True
    """
    return mid_cd in BEVERAGE_CATEGORIES


def _learn_weekday_pattern(mid_cd: str, db_path: str = None, min_data_days: int = 14, store_id: Optional[str] = None) -> list:
    """매장 판매 데이터에서 요일별 계수를 학습

    최근 30일 데이터에서 요일별 평균 판매량을 계산하여 계수 산출.
    데이터 부족 시 DEFAULT_WEEKDAY_COEF 반환.

    Args:
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 필요 데이터 일수 (기본 14일)
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        [월, 화, 수, 목, 금, 토, 일] 7개 계수 (Python weekday 순서)
    """
    if db_path is None:
        db_path = _get_db_path()

    default_coefs = [DEFAULT_WEEKDAY_COEF[i] for i in range(7)]

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        # 최근 30일 데이터에서 요일별 판매량 집계
        if store_id:
            cursor.execute("""
                SELECT
                    CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                    COUNT(DISTINCT sales_date) as day_count,
                    SUM(sale_qty) as total_qty
                FROM daily_sales
                WHERE mid_cd = ?
                AND store_id = ?
                AND sales_date >= date('now', '-30 days')
                GROUP BY dow
                ORDER BY dow
            """, (mid_cd, store_id))
        else:
            cursor.execute("""
                SELECT
                    CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                    COUNT(DISTINCT sales_date) as day_count,
                    SUM(sale_qty) as total_qty
                FROM daily_sales
                WHERE mid_cd = ?
                AND sales_date >= date('now', '-30 days')
                GROUP BY dow
                ORDER BY dow
            """, (mid_cd,))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return default_coefs

        # 총 데이터 일수 확인
        total_days = sum(row[1] for row in rows)
        if total_days < min_data_days:
            return default_coefs

        # 요일별 평균 판매량 계산 (SQLite: 0=일, 1=월, ..., 6=토)
        weekday_avg = {}
        for row in rows:
            sqlite_dow = row[0]   # 0=일, 1=월, ..., 6=토
            day_count = row[1]
            total_qty = row[2] or 0
            avg_qty = total_qty / day_count if day_count > 0 else 0

            # SQLite weekday -> Python weekday 변환 (0=일->6, 1=월->0, ..., 6=토->5)
            python_dow = (sqlite_dow - 1) % 7
            weekday_avg[python_dow] = avg_qty

        # 전체 평균 계산
        all_avgs = [v for v in weekday_avg.values() if v > 0]
        if not all_avgs:
            return default_coefs

        overall_avg = sum(all_avgs) / len(all_avgs)
        if overall_avg <= 0:
            return default_coefs

        # 계수 계산 (전체 평균 대비 비율, 0.5~2.5 범위로 클램프)
        learned_coefs = []
        for i in range(7):
            if i in weekday_avg and overall_avg > 0:
                raw_coef = weekday_avg[i] / overall_avg
                clamped_coef = max(0.5, min(2.5, raw_coef))
                learned_coefs.append(round(clamped_coef, 3))
            else:
                learned_coefs.append(DEFAULT_WEEKDAY_COEF[i])

        return learned_coefs

    except Exception as e:
        logger.warning(f"[음료] 요일 패턴 학습 실패 (mid_cd={mid_cd}): {e}")
        return default_coefs


def _get_turnover_level(daily_avg: float) -> Tuple[str, float, str]:
    """회전율 레벨 판정

    일평균 판매량 기준으로 고회전/중회전/저회전을 결정.

    Args:
        daily_avg: 일평균 판매량

    Returns:
        (회전율_레벨, 안전재고_일수, 설명) 튜플
    """
    config = BEVERAGE_SAFETY_CONFIG

    if daily_avg >= config["high_turnover"]["min_daily_avg"]:
        return (
            "high_turnover",
            config["high_turnover"]["safety_days"],
            config["high_turnover"]["description"],
        )
    elif daily_avg >= config["medium_turnover"]["min_daily_avg"]:
        return (
            "medium_turnover",
            config["medium_turnover"]["safety_days"],
            config["medium_turnover"]["description"],
        )
    else:
        return (
            "low_turnover",
            config["low_turnover"]["safety_days"],
            config["low_turnover"]["description"],
        )


def analyze_beverage_pattern(
    item_cd: str,
    mid_cd: str = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> BeveragePatternResult:
    """음료류 상품 패턴 분석

    회전율 기반 안전재고와 요일별 학습 계수를 결합하여 분석.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드 (None이면 DB에서 조회)
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        BeveragePatternResult: 분석 결과 데이터클래스
    """
    config = BEVERAGE_DYNAMIC_SAFETY_CONFIG
    if db_path is None:
        db_path = _get_db_path()

    analysis_days = config["analysis_days"]
    min_data_days = config["min_data_days"]
    default_safety_days = config["default_safety_days"]
    max_stock_days = config["max_stock_days"]

    # === mid_cd 조회 (필요 시) ===
    if mid_cd is None:
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            cursor = conn.cursor()
            if store_id:
                cursor.execute("""
                    SELECT mid_cd FROM daily_sales
                    WHERE item_cd = ? AND store_id = ? LIMIT 1
                """, (item_cd, store_id))
            else:
                cursor.execute("""
                    SELECT mid_cd FROM daily_sales
                    WHERE item_cd = ? LIMIT 1
                """, (item_cd,))
            row = cursor.fetchone()
            conn.close()
            mid_cd = row[0] if row else "010"
        except Exception as e:
            logger.warning(f"[음료] mid_cd 조회 실패 ({item_cd}): {e}")
            mid_cd = "010"

    # === 요일 계수 학습 ===
    learned_coefs = _learn_weekday_pattern(mid_cd, db_path, min_data_days, store_id=store_id)
    order_weekday = datetime.now().weekday()

    # 학습 성공 여부 판단 (기본값과 다르면 학습된 것)
    default_list = [DEFAULT_WEEKDAY_COEF[i] for i in range(7)]
    weekday_coef_source = "learned" if learned_coefs != default_list else "default"
    weekday_coef = learned_coefs[order_weekday]

    # === DB에서 판매 데이터 조회 ===
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()

        if store_id:
            cursor.execute("""
                SELECT COUNT(DISTINCT sales_date) as data_days,
                       COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE item_cd = ?
                AND store_id = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, store_id, analysis_days))
        else:
            cursor.execute("""
                SELECT COUNT(DISTINCT sales_date) as data_days,
                       COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE item_cd = ?
                AND sales_date >= date('now', '-' || ? || ' days')
            """, (item_cd, analysis_days))

        row = cursor.fetchone()
        conn.close()

        actual_data_days = row[0] or 0
        total_sales = row[1] or 0
    except Exception as e:
        logger.warning(f"[음료] 판매 데이터 조회 실패 ({item_cd}): {e}")
        actual_data_days = 0
        total_sales = 0

    has_enough_data = actual_data_days >= min_data_days

    # === 일평균 계산 ===
    daily_avg = (total_sales / actual_data_days) if actual_data_days > 0 else 0.0

    # === 회전율 레벨 및 안전재고 일수 결정 ===
    if has_enough_data:
        turnover_level, safety_days, turnover_description = _get_turnover_level(daily_avg)
    else:
        # 데이터 부족: 기본값 적용
        turnover_level = "unknown"
        safety_days = default_safety_days
        turnover_description = "데이터 부족 (기본값 적용)"

    # === 안전재고 계산 (일평균 x 안전재고일수 x 요일계수) ===
    safety_stock = daily_avg * safety_days * weekday_coef

    # === 최대 재고 상한선 (7일치) ===
    max_stock = daily_avg * max_stock_days if daily_avg > 0 else 0

    # === 발주 스킵 판단 ===
    skip_order = False
    skip_reason = ""

    if config["max_stock_enabled"] and max_stock > 0:
        if current_stock + pending_qty >= max_stock:
            skip_order = True
            skip_reason = (
                f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"
            )

    return BeveragePatternResult(
        item_cd=item_cd,
        actual_data_days=actual_data_days,
        analysis_days=analysis_days,
        has_enough_data=has_enough_data,
        total_sales=total_sales,
        daily_avg=round(daily_avg, 2),
        turnover_level=turnover_level,
        safety_days=safety_days,
        order_weekday=order_weekday,
        weekday_coef=round(weekday_coef, 3),
        weekday_coef_source=weekday_coef_source,
        safety_stock=round(safety_stock, 2),
        max_stock=round(max_stock, 2),
        current_stock=current_stock,
        pending_qty=pending_qty,
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_safety_stock=round(safety_stock, 2),
        turnover_description=turnover_description,
    )


def calculate_beverage_dynamic_safety(
    item_cd: str,
    mid_cd: str = None,
    daily_avg: Optional[float] = None,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[BeveragePatternResult]]:
    """음료류 동적 안전재고 계산

    회전율별 안전재고일수와 DB 학습 요일계수를 결합하여 계산.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량 (None이면 DB에서 조회)
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (최종 안전재고, 패턴 분석 결과) 튜플
    """
    config = BEVERAGE_DYNAMIC_SAFETY_CONFIG

    if not config["enabled"]:
        # 비활성화 시 기본값 반환
        default_safety = (daily_avg or 0) * config["default_safety_days"]
        return default_safety, None

    # 패턴 분석
    pattern = analyze_beverage_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )

    return pattern.final_safety_stock, pattern


def get_safety_stock_with_beverage_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[BeveragePatternResult]]:
    """안전재고 계산 (음료류 동적 패턴 포함)

    통합 진입점. 음료류이면 회전율+요일계수 기반 동적 안전재고를 계산하고,
    아니면 기본 로직으로 위임.

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 음료_패턴_정보) 튜플
        - 음료류가 아니면 패턴_정보는 None
    """
    # 음료류가 아니면 기본값 반환
    if not is_beverage_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 음료류: 동적 안전재고 계산
    if item_cd and BEVERAGE_DYNAMIC_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_beverage_dynamic_safety(
            item_cd, mid_cd, daily_avg, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = BEVERAGE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
