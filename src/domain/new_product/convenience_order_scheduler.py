"""
간편식/디저트 3일 발주 스케줄러

3일발주 달성 기준: 대상기간 내 최소 3회 발주
- 판매 확인 후 재발주: 이전 발주분이 판매되었으면 즉시 재발주 가능
- 미판매 시 순차 발주: 대상기간을 3등분하여 간격을 두고 발주
- 과발주 방지: 재고 > 유통기한 × 일평균판매 × 1.5 이면 스킵
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from src.settings.constants import (
    NEW_PRODUCT_DS_MIN_ORDERS,
    NEW_PRODUCT_DS_OVERSTOCK_RATIO,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


def plan_3day_orders(
    item_cd: str,
    period_start: str,
    period_end: str,
    shelf_life_days: int = 3,
) -> List[str]:
    """대상기간 내 3회 발주 일정 계획

    대상기간을 3등분하여 각 구간의 시작일에 발주 예정

    Args:
        item_cd: 상품코드
        period_start: 대상기간 시작일 (YYYY-MM-DD 또는 YY.MM.DD)
        period_end: 대상기간 종료일
        shelf_life_days: 유통기한 (일)

    Returns:
        계획된 발주일 리스트 ['YYYY-MM-DD', ...]
    """
    start = _parse_date(period_start)
    end = _parse_date(period_end)

    if start is None or end is None:
        return []

    total_days = (end - start).days
    if total_days <= 0:
        return [start.strftime("%Y-%m-%d")]

    interval = max(1, total_days // NEW_PRODUCT_DS_MIN_ORDERS)

    plan_dates = []
    for i in range(NEW_PRODUCT_DS_MIN_ORDERS):
        order_date = start + timedelta(days=i * interval)
        if order_date <= end:
            plan_dates.append(order_date.strftime("%Y-%m-%d"))

    return plan_dates


def should_order_today(
    item_cd: str,
    today: str,
    order_plan: List[str],
    current_stock: int,
    daily_avg_sales: float,
    shelf_life_days: int = 3,
    orders_placed: int = 0,
    last_order_sold: bool = True,
) -> Tuple[bool, str]:
    """오늘 발주 여부 판단

    Args:
        item_cd: 상품코드
        today: 오늘 날짜 (YYYY-MM-DD)
        order_plan: 계획된 발주일 리스트
        current_stock: 현재 재고
        daily_avg_sales: 일평균 판매량
        shelf_life_days: 유통기한
        orders_placed: 이미 발주한 횟수
        last_order_sold: 이전 발주분이 판매되었는지

    Returns:
        (should_order, reason)
    """
    # 이미 3회 이상 발주 완료
    if orders_placed >= NEW_PRODUCT_DS_MIN_ORDERS:
        return False, "이미 3회 발주 완료"

    # 과발주 방지: 재고가 유통기한×일평균×1.5 이상이면 스킵
    overstock_threshold = shelf_life_days * daily_avg_sales * NEW_PRODUCT_DS_OVERSTOCK_RATIO
    if current_stock > overstock_threshold and overstock_threshold > 0:
        return False, f"재고 과잉 ({current_stock} > {overstock_threshold:.1f})"

    # 이전 발주분이 판매되었으면 즉시 재발주 가능
    if orders_placed > 0 and last_order_sold:
        return True, "이전 발주분 판매 완료 → 즉시 재발주"

    # 계획된 발주일이면 발주
    if today in order_plan:
        return True, f"계획 발주일 ({today})"

    # 계획 발주일이 지났는데 아직 발주 안 한 경우 (뒤늦은 발주)
    for plan_date in order_plan:
        if plan_date <= today and orders_placed < order_plan.index(plan_date) + 1:
            return True, f"지연 발주 (계획일 {plan_date})"

    return False, "발주 대상일 아님"


def get_remaining_orders_needed(
    orders_placed: int,
) -> int:
    """잔여 필요 발주 횟수"""
    return max(0, NEW_PRODUCT_DS_MIN_ORDERS - orders_placed)


def _parse_date(date_str: str) -> Optional[datetime]:
    """다양한 날짜 형식 파싱 (YY.MM.DD, YYYY-MM-DD, YYYYMMDD)"""
    if not date_str:
        return None

    for fmt in ("%Y-%m-%d", "%y.%m.%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue

    return None
