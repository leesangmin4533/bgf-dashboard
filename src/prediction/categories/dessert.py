"""
디저트 카테고리 예측 모듈

디저트(014)의 유통기한 기반 + 회전율 조정 안전재고 계산:
- 유통기한 그룹별 안전재고일수: short(~15일) 0.3일, medium(16~30일) 0.5일, long(31일+) 0.7일
- 회전율별 조정배수: 고회전(5+) 1.0, 중회전(2~5) 0.9, 저회전(<2) 0.7
- 재고 상한선: 유통기한 연동 (short: 유통기한-1, medium: min(유통기한-1, 5), long: 5일)

공식: 일평균 x 안전재고일수(유통기한별) x 회전율배수 x 요일계수
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# 디저트 설정
# =============================================================================
DESSERT_TARGET_CATEGORIES = ["014"]

DESSERT_EXPIRY_SAFETY_CONFIG = {
    "enabled": True,
    "analysis_days": 30,
    "min_data_days": 14,
    "default_safety_days": 0.5,

    "expiry_groups": {
        "short": {
            "min_days": 0,
            "max_days": 15,
            "safety_days": 0.3,
            "description": "단기 유통 디저트 (생크림빵, 마카롱)",
        },
        "medium": {
            "min_days": 16,
            "max_days": 30,
            "safety_days": 0.5,
            "description": "중기 유통 디저트 (쿠키, 케이크)",
        },
        "long": {
            "min_days": 31,
            "max_days": 9999,
            "safety_days": 0.7,
            "description": "장기 유통 디저트 (장기보관 제과)",
        },
    },

    "fallback_expiry_days": 14,
}

# 회전율별 조정 배수
DESSERT_TURNOVER_ADJUST = {
    "high_turnover": {
        "min_daily_avg": 5.0,
        "adjust": 1.0,
    },
    "medium_turnover": {
        "min_daily_avg": 2.0,
        "adjust": 0.9,
    },
    "low_turnover": {
        "min_daily_avg": 0.0,
        "adjust": 0.7,
    },
}

# 디저트 기본 요일계수 (Python weekday: 월=0, 일=6)
DEFAULT_DESSERT_WEEKDAY_COEF = {
    0: 1.00,  # 월
    1: 1.00,  # 화
    2: 1.00,  # 수
    3: 1.00,  # 목
    4: 1.00,  # 금
    5: 1.05,  # 토
    6: 1.05,  # 일
}


@dataclass
class DessertPatternResult:
    """디저트 패턴 분석 결과"""
    item_cd: str
    mid_cd: str

    # 유통기한 정보
    expiration_days: int
    expiry_group: str
    data_source: str

    # 판매 정보
    daily_avg: float
    turnover_level: str

    # 안전재고 계산
    safety_days: float
    turnover_adjust: float
    weekday_coef: float
    final_safety_stock: float

    # 상한선 & 스킵
    max_stock: float
    skip_order: bool
    skip_reason: str


# =============================================================================
# Private 함수
# =============================================================================
def _get_db_path() -> str:
    """DB 경로 반환

    Returns:
        bgf_sales.db 절대 경로 문자열
    """
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


def _get_dessert_expiry_group(expiration_days: int) -> Tuple[str, dict]:
    """유통기한으로 디저트 그룹 분류

    Args:
        expiration_days: 유통기한 (일)

    Returns:
        (그룹명, 그룹설정)
    """
    groups = DESSERT_EXPIRY_SAFETY_CONFIG["expiry_groups"]
    for group_name, group_cfg in groups.items():
        if group_cfg["min_days"] <= expiration_days <= group_cfg["max_days"]:
            return group_name, group_cfg
    return "long", groups["long"]


def _get_dessert_expiration_days(item_cd: str, db_path: Optional[str] = None) -> Tuple[int, str]:
    """유통기한 조회 (DB → fallback)

    Args:
        item_cd: 상품코드
        db_path: DB 경로

    Returns:
        (유통기한_일수, 데이터소스)
    """
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT expiration_days
                FROM product_details
                WHERE item_cd = ?
            """, (item_cd,))
            row = cursor.fetchone()

            if row and row[0] is not None and row[0] > 0:
                return int(row[0]), "db"
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[디저트] DB 유통기한 조회 실패 ({item_cd}): {e}")

    fallback = DESSERT_EXPIRY_SAFETY_CONFIG["fallback_expiry_days"]
    return fallback, "fallback"


def _get_turnover_level(daily_avg: float) -> Tuple[str, float]:
    """일평균 판매량 기반 회전율 레벨 및 조정배수 결정

    Args:
        daily_avg: 일평균 판매량

    Returns:
        (회전율 레벨, 조정배수)
    """
    if daily_avg >= DESSERT_TURNOVER_ADJUST["high_turnover"]["min_daily_avg"]:
        return "high", DESSERT_TURNOVER_ADJUST["high_turnover"]["adjust"]
    elif daily_avg >= DESSERT_TURNOVER_ADJUST["medium_turnover"]["min_daily_avg"]:
        return "medium", DESSERT_TURNOVER_ADJUST["medium_turnover"]["adjust"]
    else:
        return "low", DESSERT_TURNOVER_ADJUST["low_turnover"]["adjust"]


def _learn_weekday_pattern(db_path: Optional[str] = None, min_data_days: int = 14,
                           store_id: Optional[str] = None) -> dict:
    """매장 판매 데이터에서 014 전용 요일별 계수 학습

    최근 30일 데이터로 요일별 평균 산출, 전체평균 대비 비율.
    0.5~2.5 클램프. min_data_days 미만이면 기본값.

    Args:
        db_path: DB 경로 (None이면 자동 탐색)
        min_data_days: 최소 데이터 일수
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        {0: coef, 1: coef, ..., 6: coef} (Python weekday: 월=0, 일=6)
    """
    if db_path is None:
        db_path = _get_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cursor = conn.cursor()
            if store_id:
                cursor.execute("""
                    SELECT CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                           SUM(sale_qty) as total, COUNT(DISTINCT sales_date) as days
                    FROM daily_sales
                    WHERE mid_cd = '014' AND store_id = ? AND sales_date >= date('now', '-30 days')
                    GROUP BY dow
                """, (store_id,))
            else:
                cursor.execute("""
                    SELECT CAST(strftime('%w', sales_date) AS INTEGER) as dow,
                           SUM(sale_qty) as total, COUNT(DISTINCT sales_date) as days
                    FROM daily_sales
                    WHERE mid_cd = '014' AND sales_date >= date('now', '-30 days')
                    GROUP BY dow
                """)
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return dict(DEFAULT_DESSERT_WEEKDAY_COEF)

        # SQLite dow: 0=일,1=월,...,6=토 → Python weekday: 월=0,...,일=6
        sqlite_to_py = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}
        dow_avgs = {}
        total_days = 0
        for dow_sqlite, total_qty, day_count in rows:
            py_dow = sqlite_to_py.get(dow_sqlite, dow_sqlite)
            dow_avgs[py_dow] = total_qty / max(1, day_count)
            total_days += day_count

        if total_days < min_data_days:
            return dict(DEFAULT_DESSERT_WEEKDAY_COEF)

        overall_avg = sum(dow_avgs.values()) / max(1, len(dow_avgs))
        if overall_avg <= 0:
            return dict(DEFAULT_DESSERT_WEEKDAY_COEF)

        learned = {}
        for d in range(7):
            if d in dow_avgs and overall_avg > 0:
                coef = dow_avgs[d] / overall_avg
                learned[d] = max(0.5, min(2.5, round(coef, 2)))
            else:
                learned[d] = DEFAULT_DESSERT_WEEKDAY_COEF.get(d, 1.0)
        return learned
    except Exception as e:
        logger.warning(f"[디저트] 요일 패턴 학습 실패: {e}")
        return dict(DEFAULT_DESSERT_WEEKDAY_COEF)


def _calculate_max_stock_days(expiry_group: str, expiration_days: int) -> float:
    """유통기한 그룹별 재고 상한 일수 계산

    Args:
        expiry_group: 유통기한 그룹 (short/medium/long)
        expiration_days: 유통기한 (일)

    Returns:
        상한 일수
    """
    if expiry_group == "short":
        return max(expiration_days - 1, 2)
    elif expiry_group == "medium":
        return min(expiration_days - 1, 5)
    else:
        return 5.0


# =============================================================================
# Public 함수
# =============================================================================
def is_dessert_category(mid_cd: str) -> bool:
    """디저트 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        디저트 카테고리(014)이면 True
    """
    return mid_cd in DESSERT_TARGET_CATEGORIES


def analyze_dessert_pattern(
    item_cd: str,
    mid_cd: str = "014",
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> DessertPatternResult:
    """디저트 상품 패턴 분석

    유통기한 기반 안전재고일수 + 회전율 조정배수 + 요일계수를 결합하여
    최종 안전재고를 산출하고, 재고 상한선 초과 시 발주를 스킵한다.

    Args:
        item_cd: 상품코드
        mid_cd: 중분류 코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        DessertPatternResult
    """
    config = DESSERT_EXPIRY_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    # 1. 유통기한 조회
    expiration_days, data_source = _get_dessert_expiration_days(item_cd, db_path)

    # 2. 유통기한 그룹 → 안전재고일수
    expiry_group, group_cfg = _get_dessert_expiry_group(expiration_days)
    safety_days = group_cfg["safety_days"]

    # 3. 일평균 판매량 (최근 30일)
    daily_avg = 0.0
    has_enough_data = False
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
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
            data_days = row[0] or 0
            total_sales = row[1] or 0
            has_enough_data = data_days >= config["min_data_days"]
            daily_avg = (total_sales / data_days) if data_days > 0 else 0.0
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[디저트] 판매 데이터 조회 실패 ({item_cd}): {e}")

    # 4. 회전율 레벨 → 조정배수
    if has_enough_data:
        turnover_level, turnover_adjust = _get_turnover_level(daily_avg)
    else:
        turnover_level = "unknown"
        turnover_adjust = 1.0

    # 5. 요일계수 학습 (014만)
    weekday = datetime.now().weekday()
    learned_coef = _learn_weekday_pattern(db_path, config["min_data_days"], store_id=store_id)
    weekday_coef = learned_coef.get(weekday, 1.0)

    # 6. 최종 안전재고
    final_safety_stock = daily_avg * safety_days * turnover_adjust * weekday_coef

    # 7. 재고 상한선 (유통기한 연동)
    max_stock_days = _calculate_max_stock_days(expiry_group, expiration_days)
    max_stock = daily_avg * max_stock_days if daily_avg > 0 else 0

    # 8. 스킵 판단
    skip_order = False
    skip_reason = ""
    if max_stock > 0 and current_stock + pending_qty >= max_stock:
        skip_order = True
        skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return DessertPatternResult(
        item_cd=item_cd,
        mid_cd=mid_cd,
        expiration_days=expiration_days,
        expiry_group=expiry_group,
        data_source=data_source,
        daily_avg=round(daily_avg, 2),
        turnover_level=turnover_level,
        safety_days=safety_days,
        turnover_adjust=turnover_adjust,
        weekday_coef=round(weekday_coef, 2),
        final_safety_stock=round(final_safety_stock, 2),
        max_stock=round(max_stock, 1),
        skip_order=skip_order,
        skip_reason=skip_reason,
    )


def calculate_dessert_dynamic_safety(
    item_cd: str,
    mid_cd: str = "014",
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, DessertPatternResult]:
    """디저트 동적 안전재고 계산

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
    pattern = analyze_dessert_pattern(
        item_cd, mid_cd, db_path, current_stock, pending_qty, store_id=store_id
    )
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_dessert_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None,
) -> Tuple[float, Optional[DessertPatternResult]]:
    """안전재고 계산 (디저트 유통기한+회전율 패턴 포함)

    ImprovedPredictor에서 호출하는 통합 인터페이스.

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 디저트_패턴_정보)
        - 디저트가 아니면 패턴_정보는 None
    """
    # 디저트가 아니면 기본값 반환
    if not is_dessert_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 디저트: 동적 안전재고 계산
    if item_cd and DESSERT_EXPIRY_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_dessert_dynamic_safety(
            item_cd, mid_cd, None, current_stock, pending_qty, store_id=store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = DESSERT_EXPIRY_SAFETY_CONFIG["default_safety_days"]
    return daily_avg * default_days, None
