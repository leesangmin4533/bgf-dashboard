"""디저트 상품 생애주기 판별

첫 입고일 기준으로 신상품/성장하락기/정착기를 판별합니다.

생애주기:
    NEW:             카테고리 A=4주, B=3주, C/D=4주
    GROWTH_DECLINE:  ~8주
    ESTABLISHED:     8주 초과

소스 기반 오버라이드:
    products_bulk:   일괄등록 → 강제 ESTABLISHED (실제 주차 계산)
    none:            데이터 없음 → None (SKIP 시그널)
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple

from .enums import DessertCategory, DessertLifecycle, FirstReceivingSource


# 카테고리별 신상품 보호 기간 (주)
NEW_PRODUCT_WEEKS = {
    DessertCategory.A: 4,
    DessertCategory.B: 3,
    DessertCategory.C: 4,
    DessertCategory.D: 4,
}

# 성장/하락기 종료 시점 (주)
GROWTH_DECLINE_END_WEEKS = 8


def determine_lifecycle(
    first_receiving_date: Optional[str],
    reference_date: str,
    category: DessertCategory,
    source: Optional[FirstReceivingSource] = None,
) -> Tuple[Optional[DessertLifecycle], int]:
    """생애주기와 경과 주수를 판별합니다.

    Args:
        first_receiving_date: 첫 입고일 (YYYY-MM-DD). None이면 ESTABLISHED 처리.
        reference_date: 판단 기준일 (YYYY-MM-DD)
        category: 디저트 카테고리
        source: 첫 입고일 판별 소스. None이면 기존 로직 그대로 적용.

    Returns:
        (lifecycle_phase, weeks_since_intro)
        lifecycle_phase가 None이면 해당 상품을 SKIP해야 함.
    """
    # source 기반 오버라이드
    if source == FirstReceivingSource.PRODUCTS_BULK:
        # 일괄등록 → 강제 정착기, 단 실제 created_at 기준 주차 계산
        if first_receiving_date:
            try:
                first_dt = datetime.strptime(first_receiving_date, "%Y-%m-%d")
                ref_dt = datetime.strptime(reference_date, "%Y-%m-%d")
                weeks = max(0, (ref_dt - first_dt).days // 7)
            except (ValueError, TypeError):
                weeks = 999
        else:
            weeks = 999
        return DessertLifecycle.ESTABLISHED, weeks

    if source == FirstReceivingSource.NONE:
        return None, 0  # 데이터 없음 → 호출부에서 SKIP 처리

    # 기존 로직 (source=None 또는 정상 소스)
    if not first_receiving_date:
        return DessertLifecycle.ESTABLISHED, 999

    try:
        first_date = datetime.strptime(first_receiving_date, "%Y-%m-%d")
        ref_date = datetime.strptime(reference_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return DessertLifecycle.ESTABLISHED, 999

    delta_days = (ref_date - first_date).days
    if delta_days < 0:
        # 미래 입고일 → 신상품 취급
        return DessertLifecycle.NEW, 0

    weeks_since_intro = delta_days // 7

    new_weeks = NEW_PRODUCT_WEEKS.get(category, 4)

    if weeks_since_intro < new_weeks:
        return DessertLifecycle.NEW, weeks_since_intro
    elif weeks_since_intro < GROWTH_DECLINE_END_WEEKS:
        return DessertLifecycle.GROWTH_DECLINE, weeks_since_intro
    else:
        return DessertLifecycle.ESTABLISHED, weeks_since_intro
