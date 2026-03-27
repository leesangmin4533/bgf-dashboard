"""음료 상품 생애주기 판별

디저트와 동일한 로직, 상수만 다름:
    NEW:             카테고리 A=3주, B=4주, C/D=6주
    GROWTH_DECLINE:  A=8주, B=10주, C/D=12주
    ESTABLISHED:     이후
"""

from datetime import datetime
from typing import Optional, Tuple

from .enums import BeverageCategory, BeverageLifecycle, FirstReceivingSource, NEW_PRODUCT_WEEKS, GROWTH_DECLINE_END_WEEKS


def determine_lifecycle(
    first_receiving_date: Optional[str],
    reference_date: str,
    category: BeverageCategory,
    source: Optional[FirstReceivingSource] = None,
) -> Tuple[Optional[BeverageLifecycle], int]:
    """생애주기와 경과 주수를 판별합니다.

    Returns:
        (lifecycle_phase, weeks_since_intro)
        lifecycle_phase가 None이면 해당 상품을 SKIP해야 함.
    """
    if source == FirstReceivingSource.PRODUCTS_BULK:
        if first_receiving_date:
            try:
                first_dt = datetime.strptime(first_receiving_date, "%Y-%m-%d")
                ref_dt = datetime.strptime(reference_date, "%Y-%m-%d")
                weeks = max(0, (ref_dt - first_dt).days // 7)
            except (ValueError, TypeError):
                weeks = 999
        else:
            weeks = 999
        return BeverageLifecycle.ESTABLISHED, weeks

    if source == FirstReceivingSource.NONE:
        return None, 0

    if not first_receiving_date:
        return BeverageLifecycle.ESTABLISHED, 999

    try:
        first_date = datetime.strptime(first_receiving_date, "%Y-%m-%d")
        ref_date = datetime.strptime(reference_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return BeverageLifecycle.ESTABLISHED, 999

    delta_days = (ref_date - first_date).days
    if delta_days < 0:
        return BeverageLifecycle.NEW, 0

    weeks_since_intro = delta_days // 7

    new_weeks = NEW_PRODUCT_WEEKS.get(category, 6)
    growth_end = GROWTH_DECLINE_END_WEEKS.get(category, 12)

    if weeks_since_intro < new_weeks:
        return BeverageLifecycle.NEW, weeks_since_intro
    elif weeks_since_intro < growth_end:
        return BeverageLifecycle.GROWTH_DECLINE, weeks_since_intro
    else:
        return BeverageLifecycle.ESTABLISHED, weeks_since_intro
