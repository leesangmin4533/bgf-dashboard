"""음료 카테고리별 판단 엔진

각 카테고리의 판단 규칙을 순수 함수로 구현합니다.
모든 함수는 I/O 없이 입력 데이터만으로 판단합니다.

판단 결과:
    KEEP:           발주 유지
    WATCH:          관찰 (경고 태그 부착 가능)
    STOP_RECOMMEND: 정지 권고 (운영자 확인 필요)
"""

from typing import List, Tuple

from .enums import BeverageCategory, BeverageLifecycle, BeverageDecisionType
from .models import BeverageSalesMetrics
from .constants import (
    CAT_A_SALE_RATE_THRESHOLD,
    CAT_A_CONSECUTIVE_WEEKS,
    CAT_A_ESTABLISHED_RATIO,
    CAT_B_SALE_RATE_THRESHOLD,
    CAT_B_CONSECUTIVE_WEEKS,
    CAT_B_SHELF_EFFICIENCY_THRESHOLD,
    CAT_C_SHELF_EFFICIENCY_THRESHOLD,
    CAT_D_ZERO_MONTHS,
    DEFAULT_MARGIN_RATE,
)


# ============================================================================
# 유틸리티 함수
# ============================================================================

def calc_sale_rate(sale_qty: int, disuse_qty: int) -> float:
    """판매율 = 판매수량 / (판매수량 + 폐기수량)"""
    total = sale_qty + disuse_qty
    if total == 0:
        return 0.0
    return round(sale_qty / total, 4)


def count_consecutive_low_weeks(
    weekly_sale_rates: List[float],
    threshold: float,
) -> int:
    """최신→과거 순으로 threshold 미만인 연속 주 수를 반환."""
    count = 0
    for rate in weekly_sale_rates:
        if rate < threshold:
            count += 1
        else:
            break
    return count


def count_consecutive_zero_months(
    monthly_sale_qtys: List[int],
) -> int:
    """최신→과거 순으로 판매 0인 연속 월 수를 반환."""
    count = 0
    for qty in monthly_sale_qtys:
        if qty == 0:
            count += 1
        else:
            break
    return count


def check_loss_exceeds_profit(
    sale_qty: int,
    disuse_qty: int,
    sell_price: int,
    margin_rate: float = None,
) -> bool:
    """폐기 원가 > 판매 마진 여부 (§2.6 폐기 손익 계산).

    폐기 원가 = disuse_qty × sell_price × (1 - margin_rate/100)
    판매 마진 = sale_qty × sell_price × margin_rate/100
    """
    if sell_price <= 0 or (sale_qty == 0 and disuse_qty == 0):
        return False

    rate = margin_rate if margin_rate is not None else DEFAULT_MARGIN_RATE
    disuse_cost = disuse_qty * sell_price * (1 - rate / 100)
    sale_margin = sale_qty * sell_price * rate / 100

    return disuse_cost > sale_margin


# ============================================================================
# 카테고리별 판단 함수
# ============================================================================

def judge_category_a(
    lifecycle: BeverageLifecycle,
    metrics: BeverageSalesMetrics,
    sell_price: int = 0,
    margin_rate: float = None,
) -> Tuple[BeverageDecisionType, str, bool]:
    """카테고리 A: 냉장 단기 유제품 (주 1회 판단)

    Rules:
        - 전 구간 공통: 폐기 원가 > 판매 마진 → 즉시 정지
        - 신상품(3주): 무조건 유지. 주간 하락률 50%+ → 급락 경고
        - 성장/하락기(3~8주): 판매율 50% 미만 2주 연속 → 정지
        - 정착기(8주+): 주간 판매량 < 소분류 평균의 30% → 정지
    """
    # 공통: 폐기 원가 > 판매 마진
    if check_loss_exceeds_profit(
        metrics.total_sale_qty, metrics.total_disuse_qty,
        sell_price, margin_rate,
    ):
        return (
            BeverageDecisionType.STOP_RECOMMEND,
            f"폐기원가 > 판매마진 (판매{metrics.total_sale_qty}개, 폐기{metrics.total_disuse_qty}개)",
            False,
        )

    if lifecycle == BeverageLifecycle.NEW:
        if metrics.sale_trend_pct <= -50.0 and metrics.prev_period_sale_qty > 0:
            return (
                BeverageDecisionType.WATCH,
                f"신상품 보호 중 — 급락 경고 (전주 대비 {metrics.sale_trend_pct:.0f}%)",
                True,
            )
        return (BeverageDecisionType.KEEP, "신상품 보호 기간", False)

    elif lifecycle == BeverageLifecycle.GROWTH_DECLINE:
        consec = count_consecutive_low_weeks(
            metrics.weekly_sale_rates, CAT_A_SALE_RATE_THRESHOLD,
        )
        if consec >= CAT_A_CONSECUTIVE_WEEKS:
            return (
                BeverageDecisionType.STOP_RECOMMEND,
                f"판매율 50% 미만 {consec}주 연속",
                False,
            )
        if consec == 1:
            return (
                BeverageDecisionType.WATCH,
                "판매율 50% 미만 1주 — 다음 주 재확인",
                False,
            )
        return (BeverageDecisionType.KEEP, "성장/하락기 정상", False)

    else:  # ESTABLISHED
        if metrics.category_avg_sale_qty > 0:
            ratio = metrics.total_sale_qty / metrics.category_avg_sale_qty
            if ratio < CAT_A_ESTABLISHED_RATIO:
                return (
                    BeverageDecisionType.STOP_RECOMMEND,
                    f"소분류 평균 대비 {ratio:.0%} (30% 미만)",
                    False,
                )
        return (BeverageDecisionType.KEEP, "정착기 정상", False)


def judge_category_b(
    lifecycle: BeverageLifecycle,
    metrics: BeverageSalesMetrics,
    sell_price: int = 0,
    margin_rate: float = None,
) -> Tuple[BeverageDecisionType, str, bool]:
    """카테고리 B: 냉장 중기 음료 (2주 1회 판단)

    Rules:
        - 신상품(4주): 보호 기간
        - 이후: 판매율 40% 미만 3주 연속 또는 매대효율 < 0.20 → 정지
    """
    if lifecycle == BeverageLifecycle.NEW:
        return (BeverageDecisionType.KEEP, "신상품 보호 기간", False)

    # 판매율 연속 미달
    consec = count_consecutive_low_weeks(
        metrics.weekly_sale_rates, CAT_B_SALE_RATE_THRESHOLD,
    )
    if consec >= CAT_B_CONSECUTIVE_WEEKS:
        return (
            BeverageDecisionType.STOP_RECOMMEND,
            f"판매율 40% 미만 {consec}주 연속",
            False,
        )

    # 매대효율 기준
    if metrics.shelf_efficiency < CAT_B_SHELF_EFFICIENCY_THRESHOLD and metrics.small_cd_median_sale_qty > 0:
        return (
            BeverageDecisionType.STOP_RECOMMEND,
            f"매대효율 {metrics.shelf_efficiency:.2f} (소분류 중위값 대비 20% 미만)",
            False,
        )

    if consec >= 2:
        return (
            BeverageDecisionType.WATCH,
            f"판매율 40% 미만 {consec}주 연속 — 주시 필요",
            False,
        )
    return (BeverageDecisionType.KEEP, "정상", False)


def judge_category_c(
    lifecycle: BeverageLifecycle,
    metrics: BeverageSalesMetrics,
    shelf_threshold: float = CAT_C_SHELF_EFFICIENCY_THRESHOLD,
) -> Tuple[BeverageDecisionType, str, bool]:
    """카테고리 C: 상온 장기 음료 (월 1회 판단)

    Rules:
        - 신상품(6주): 보호 기간
        - 이후: 매대효율 < 0.15 (비수기: 0.075) → 정지
    """
    if lifecycle == BeverageLifecycle.NEW:
        return (BeverageDecisionType.KEEP, "신상품 보호 기간", False)

    if metrics.shelf_efficiency < shelf_threshold and metrics.small_cd_median_sale_qty > 0:
        return (
            BeverageDecisionType.STOP_RECOMMEND,
            f"매대효율 {metrics.shelf_efficiency:.2f} (소분류 중위값 대비 {shelf_threshold:.0%} 미만)",
            False,
        )
    return (BeverageDecisionType.KEEP, "정상", False)


def judge_category_d(
    lifecycle: BeverageLifecycle,
    metrics: BeverageSalesMetrics,
) -> Tuple[BeverageDecisionType, str, bool]:
    """카테고리 D: 초장기/비소모품 (월 1회 판단)

    Rules:
        - 월간 판매량 0개 3개월 연속 → 정지
    """
    consec = metrics.consecutive_zero_months
    if consec >= CAT_D_ZERO_MONTHS:
        return (
            BeverageDecisionType.STOP_RECOMMEND,
            f"무판매 {consec}개월 연속",
            False,
        )
    if consec >= 2:
        return (
            BeverageDecisionType.WATCH,
            f"무판매 {consec}개월 연속 — 다음 달 재확인",
            False,
        )
    return (BeverageDecisionType.KEEP, "정상", False)


# ============================================================================
# 통합 판단 디스패처
# ============================================================================

def judge_item(
    category: BeverageCategory,
    lifecycle: BeverageLifecycle,
    metrics: BeverageSalesMetrics,
    sell_price: int = 0,
    margin_rate: float = None,
    shelf_threshold_override: float = None,
) -> Tuple[BeverageDecisionType, str, bool]:
    """카테고리에 맞는 판단 함수를 호출합니다.

    Args:
        category: 음료 카테고리 (A/B/C/D)
        lifecycle: 상품 생애주기
        metrics: 판매 집계 데이터
        sell_price: 판매가 (폐기 손익 계산용)
        margin_rate: 마진율 (None이면 기본값 60%)
        shelf_threshold_override: 매대효율 기준 오버라이드 (계절 보정용)

    Returns:
        (decision_type, reason, is_rapid_decline_warning)
    """
    if category == BeverageCategory.A:
        return judge_category_a(lifecycle, metrics, sell_price, margin_rate)
    elif category == BeverageCategory.B:
        return judge_category_b(lifecycle, metrics, sell_price, margin_rate)
    elif category == BeverageCategory.C:
        threshold = shelf_threshold_override or CAT_C_SHELF_EFFICIENCY_THRESHOLD
        return judge_category_c(lifecycle, metrics, threshold)
    elif category == BeverageCategory.D:
        return judge_category_d(lifecycle, metrics)
    else:
        return judge_category_a(lifecycle, metrics, sell_price, margin_rate)
