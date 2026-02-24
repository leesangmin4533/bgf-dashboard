"""
맥주 카테고리 예측 모듈

맥주(049)의 동적 안전재고 계산:
- 요일 패턴: 금요일 급증 (2.54배), 토요일 (2.37배)
- 안전재고: 평일 2일, 금/토 3일 (일요일 발주 불가 대비)
- 상한선: 일평균 × 7일 (냉장고 공간 제약)

공식: 일평균 × 요일계수 (예측), 일평균 × 안전재고일수 (안전재고)
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple


# =============================================================================
# 맥주 설정
# =============================================================================
BEER_CATEGORIES = ['049']  # 맥주

# 맥주 요일별 계수 (DB 기반 실측값)
# 0=월, 1=화, 2=수, 3=목, 4=금, 5=토, 6=일 (Python weekday 기준)
BEER_WEEKDAY_COEF = {
    0: 1.15,  # 월
    1: 1.22,  # 화
    2: 1.21,  # 수
    3: 1.37,  # 목
    4: 2.54,  # 금 (급증)
    5: 2.37,  # 토 (급증)
    6: 0.97,  # 일
}

# 맥주 안전재고 설정
BEER_SAFETY_CONFIG = {
    "enabled": True,              # 동적 안전재고 적용 여부
    "target_category": "049",     # 맥주

    # === 데이터 기간 설정 ===
    "analysis_days": 30,          # 기본 분석 기간 (30일) - SQL 파라미터용 int
    "min_data_days": 7,           # 최소 데이터 일수 - 비교용 int

    # === 안전재고 일수 ===
    "default_days": 2,            # 기본 안전재고 (월~목, 일) - 정수 일수
    "weekend_days": 3,            # 주말 대비 안전재고 (금/토) - 정수 일수
    "weekend_order_days": [4, 5], # 금(4), 토(5) 발주 시 3일치

    # === 최대 재고 상한선 (냉장고 공간 제약) ===
    "max_stock_enabled": True,
    "max_stock_days": 7,          # 최대 재고 상한선 (일평균 × 7일)
}


def _get_db_path() -> str:
    """DB 경로 반환"""
    return str(Path(__file__).parent.parent.parent.parent / "data" / "bgf_sales.db")


@dataclass
class BeerPatternResult:
    """맥주 패턴 분석 결과"""
    item_cd: str
    daily_avg: float              # 일평균 판매량
    actual_data_days: int         # 실제 데이터 일수
    has_enough_data: bool         # 데이터 충분 여부

    # 요일 정보
    order_weekday: int            # 발주 요일 (0=월, 6=일)
    weekday_coef: float           # 적용된 요일 계수

    # 안전재고
    safety_days: int              # 안전재고 일수 (2 또는 3)
    safety_stock: float           # 안전재고 수량

    # 재고 상한선
    max_stock: float              # 최대 재고 상한선
    current_stock: int            # 현재 재고
    pending_qty: int              # 미입고 수량
    available_space: float        # 여유분 (상한선 - 현재 - 미입고)

    # 발주 스킵
    skip_order: bool              # 발주 스킵 여부
    skip_reason: str              # 스킵 사유

    # 최종 결과
    final_safety_stock: float     # 최종 안전재고


def is_beer_category(mid_cd: str) -> bool:
    """맥주 카테고리 여부 확인

    Args:
        mid_cd: 중분류 코드

    Returns:
        맥주 카테고리이면 True
    """
    return mid_cd in BEER_CATEGORIES


def get_beer_weekday_coef(weekday: int) -> float:
    """
    맥주 요일별 계수 반환

    Args:
        weekday: Python weekday (0=월, 6=일)

    Returns:
        요일 계수
    """
    return BEER_WEEKDAY_COEF.get(weekday, 1.0)


def get_beer_safety_days(order_weekday: int) -> int:
    """
    발주 요일 기준 안전재고 일수 반환

    Args:
        order_weekday: 발주 요일 (0=월, 6=일)

    Returns:
        안전재고 일수 (금/토: 3일, 그 외: 2일)
    """
    config = BEER_SAFETY_CONFIG
    if order_weekday in config["weekend_order_days"]:
        return config["weekend_days"]
    return config["default_days"]


def analyze_beer_pattern(
    item_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> BeerPatternResult:
    """
    맥주 상품 패턴 분석

    Args:
        item_cd: 상품코드
        db_path: DB 경로 (None이면 자동 탐색)
        current_stock: 현재 재고
        pending_qty: 미입고 수량

    Returns:
        BeerPatternResult
    """
    config = BEER_SAFETY_CONFIG

    if db_path is None:
        db_path = _get_db_path()

    # 오늘 요일 (발주 기준)
    order_weekday = datetime.now().weekday()  # 0=월, 6=일

    # 요일 계수
    weekday_coef = get_beer_weekday_coef(order_weekday)

    # 안전재고 일수 (금/토: 3일, 그 외: 2일)
    safety_days = get_beer_safety_days(order_weekday)

    # DB에서 일평균 판매량 조회
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    if store_id:
        cursor.execute("""
            SELECT COUNT(DISTINCT sales_date) as data_days,
                   COALESCE(SUM(sale_qty), 0) as total_sales,
                   MAX(stock_qty) as latest_stock
            FROM daily_sales
            WHERE item_cd = ?
            AND store_id = ?
            AND sales_date >= date('now', '-' || ? || ' days')
        """, (item_cd, store_id, config["analysis_days"]))
    else:
        cursor.execute("""
            SELECT COUNT(DISTINCT sales_date) as data_days,
                   COALESCE(SUM(sale_qty), 0) as total_sales,
                   MAX(stock_qty) as latest_stock
            FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-' || ? || ' days')
        """, (item_cd, config["analysis_days"]))

    row = cursor.fetchone()
    conn.close()

    data_days = row[0] or 0
    total_sales = row[1] or 0
    has_enough_data = data_days >= config["min_data_days"]

    # 일평균 계산
    daily_avg = (total_sales / data_days) if data_days > 0 else 0.0

    # 안전재고 수량
    safety_stock = daily_avg * safety_days

    # 최대 재고 상한선
    max_stock = daily_avg * config["max_stock_days"]

    # 여유분 계산
    available_space = max_stock - current_stock - pending_qty

    # 발주 스킵 판단
    skip_order = False
    skip_reason = ""

    if current_stock + pending_qty >= max_stock and max_stock > 0:
        skip_order = True
        skip_reason = f"상한선 초과 ({current_stock}+{pending_qty} >= {max_stock:.0f})"

    return BeerPatternResult(
        item_cd=item_cd,
        daily_avg=round(daily_avg, 2),
        actual_data_days=data_days,
        has_enough_data=has_enough_data,
        order_weekday=order_weekday,
        weekday_coef=weekday_coef,
        safety_days=safety_days,
        safety_stock=round(safety_stock, 2),
        max_stock=round(max_stock, 2),
        current_stock=current_stock,
        pending_qty=pending_qty,
        available_space=round(available_space, 2),
        skip_order=skip_order,
        skip_reason=skip_reason,
        final_safety_stock=round(safety_stock, 2)
    )


def calculate_beer_dynamic_safety(
    item_cd: str,
    db_path: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, BeerPatternResult]:
    """
    맥주 동적 안전재고 계산

    Args:
        item_cd: 상품코드
        db_path: DB 경로
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_수량, 패턴_결과)
    """
    pattern = analyze_beer_pattern(item_cd, db_path, current_stock, pending_qty, store_id)
    return pattern.final_safety_stock, pattern


def get_safety_stock_with_beer_pattern(
    mid_cd: str,
    daily_avg: float,
    expiration_days: int,
    item_cd: Optional[str] = None,
    current_stock: int = 0,
    pending_qty: int = 0,
    store_id: Optional[str] = None
) -> Tuple[float, Optional[BeerPatternResult]]:
    """
    안전재고 계산 (맥주 동적 패턴 포함)

    Args:
        mid_cd: 중분류 코드
        daily_avg: 일평균 판매량
        expiration_days: 유통기한 일수
        item_cd: 상품코드
        current_stock: 현재 재고
        pending_qty: 미입고 수량
        store_id: 매장 ID (None이면 전체 매장)

    Returns:
        (안전재고_개수, 맥주_패턴_정보)
        - 맥주가 아니면 패턴_정보는 None
    """
    # 맥주가 아니면 기본값 반환
    if not is_beer_category(mid_cd):
        from .default import get_safety_stock_days
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        return daily_avg * safety_days, None

    # 맥주: 동적 안전재고 계산
    if item_cd and BEER_SAFETY_CONFIG["enabled"]:
        final_safety, pattern = calculate_beer_dynamic_safety(
            item_cd, None, current_stock, pending_qty, store_id
        )
        return final_safety, pattern

    # 기본값 (상품코드 없거나 비활성화 시)
    default_days = BEER_SAFETY_CONFIG["default_days"]
    return daily_avg * default_days, None
