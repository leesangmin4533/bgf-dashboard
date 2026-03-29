"""디저트 카테고리별 판단 엔진

각 카테고리의 판단 규칙을 순수 함수로 구현합니다.
모든 함수는 I/O 없이 입력 데이터만으로 판단합니다.

판단 결과:
    KEEP:           발주 유지
    WATCH:          관찰 (경고 태그 부착 가능)
    REDUCE_ORDER:   발주 감량 (50% 감소)
    STOP_RECOMMEND: 정지 권고 (운영자 확인 필요)
"""

from typing import List, Tuple

from .enums import DessertCategory, DessertLifecycle, DessertDecisionType
from .models import DessertSalesMetrics


# ============================================================================
# 유틸리티 함수
# ============================================================================

def calc_sale_rate(sale_qty: int, disuse_qty: int) -> float:
    """판매율 = 판매수량 / (판매수량 + 폐기수량)

    분모 0이면 (판매도 폐기도 없음) 0.0 반환.
    """
    total = sale_qty + disuse_qty
    if total == 0:
        return 0.0
    return round(sale_qty / total, 4)


def calc_waste_rate(sale_qty: int, disuse_qty: int) -> float:
    """폐기율 = 폐기수량 / 판매수량

    판매 0이면 폐기 있을 시 무한대(999.0) 반환, 둘 다 0이면 0.0.
    """
    if sale_qty == 0:
        return 999.0 if disuse_qty > 0 else 0.0
    return round(disuse_qty / sale_qty, 4)


def count_consecutive_low_weeks(
    weekly_sale_rates: List[float],
    threshold: float,
) -> int:
    """최신→과거 순으로 threshold 미만인 연속 주 수를 반환.

    Args:
        weekly_sale_rates: 주별 판매율 리스트 (인덱스 0이 가장 최신 주)
        threshold: 기준 판매율 (예: 0.5 = 50%)

    Returns:
        연속 미달 주 수
    """
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
    """최신→과거 순으로 판매 0인 연속 월 수를 반환.

    Args:
        monthly_sale_qtys: 월별 판매수량 리스트 (인덱스 0이 가장 최신 월)
    """
    count = 0
    for qty in monthly_sale_qtys:
        if qty == 0:
            count += 1
        else:
            break
    return count


# ============================================================================
# 카테고리별 판단 함수
# ============================================================================

def judge_category_a(
    lifecycle: DessertLifecycle,
    metrics: DessertSalesMetrics,
) -> Tuple[DessertDecisionType, str, bool]:
    """카테고리 A: 냉장디저트 (주 1회 판단)

    Rules:
        - 신상품(4주): 보호 기간 — KEEP/WATCH만 (REDUCE_ORDER/STOP 차단)
        - 폐기율 150%+ → STOP_RECOMMEND, 100~150% → REDUCE_ORDER
        - 성장/하락기(4~8주): 판매율 30% 미만 2주 연속 → STOP, 50% 미만 2주 → REDUCE
        - 정착기(8주+): 카테고리 평균 30% 미만 → STOP

    Returns:
        (decision, reason, is_rapid_decline_warning)
    """
    # 신상품: 보호 기간 — REDUCE_ORDER/STOP 차단, WATCH만 허용
    if lifecycle == DessertLifecycle.NEW:
        if metrics.sale_trend_pct <= -50.0 and metrics.prev_period_sale_qty > 0:
            return (
                DessertDecisionType.WATCH,
                f"신상품 보호 중 — 급락 경고 (전주 대비 {metrics.sale_trend_pct:.0f}%)",
                True,
            )
        return (DessertDecisionType.KEEP, "신상품 보호 기간", False)

    # 폐기율 기반 판단 (신상품 이후 적용)
    waste_rate = calc_waste_rate(metrics.total_sale_qty, metrics.total_disuse_qty)
    if waste_rate >= 1.5:
        return (
            DessertDecisionType.STOP_RECOMMEND,
            f"폐기율 {waste_rate:.0%} (150% 이상)",
            False,
        )
    if waste_rate >= 1.0:
        return (
            DessertDecisionType.REDUCE_ORDER,
            f"폐기율 {waste_rate:.0%} (100~150%) → 발주 감량",
            False,
        )

    if lifecycle == DessertLifecycle.GROWTH_DECLINE:
        # 성장/하락기: 판매율 30% 미만 2주 → STOP, 50% 미만 2주 → REDUCE
        consec_30 = count_consecutive_low_weeks(metrics.weekly_sale_rates, 0.3)
        if consec_30 >= 2:
            return (
                DessertDecisionType.STOP_RECOMMEND,
                f"판매율 30% 미만 {consec_30}주 연속",
                False,
            )
        consec_50 = count_consecutive_low_weeks(metrics.weekly_sale_rates, 0.5)
        if consec_50 >= 2:
            return (
                DessertDecisionType.REDUCE_ORDER,
                f"판매율 50% 미만 {consec_50}주 연속 → 발주 감량",
                False,
            )
        if consec_50 == 1:
            return (
                DessertDecisionType.WATCH,
                "판매율 50% 미만 1주 — 다음 주 재확인",
                False,
            )
        return (DessertDecisionType.KEEP, "성장/하락기 정상", False)

    else:  # ESTABLISHED
        # 정착기: 카테고리 평균의 30% 미만
        if metrics.category_avg_sale_qty > 0:
            ratio = metrics.total_sale_qty / metrics.category_avg_sale_qty
            if ratio < 0.3:
                return (
                    DessertDecisionType.STOP_RECOMMEND,
                    f"카테고리 평균 대비 {ratio:.0%} (30% 미만)",
                    False,
                )
        return (DessertDecisionType.KEEP, "정착기 정상", False)


def judge_category_b(
    lifecycle: DessertLifecycle,
    metrics: DessertSalesMetrics,
) -> Tuple[DessertDecisionType, str, bool]:
    """카테고리 B: 상온디저트-단기 (2주 1회 판단)

    Rules:
        - 신상품(3주): 보호 기간 — KEEP/WATCH만
        - 폐기율 150%+ → STOP, 100~150% → REDUCE
        - 이후: 판매율 40% 미만 3주 연속 → 정지
    """
    if lifecycle == DessertLifecycle.NEW:
        return (DessertDecisionType.KEEP, "신상품 보호 기간", False)

    # 폐기율 기반 판단
    waste_rate = calc_waste_rate(metrics.total_sale_qty, metrics.total_disuse_qty)
    if waste_rate >= 1.5:
        return (
            DessertDecisionType.STOP_RECOMMEND,
            f"폐기율 {waste_rate:.0%} (150% 이상)",
            False,
        )
    if waste_rate >= 1.0:
        return (
            DessertDecisionType.REDUCE_ORDER,
            f"폐기율 {waste_rate:.0%} (100~150%) → 발주 감량",
            False,
        )

    consec = count_consecutive_low_weeks(metrics.weekly_sale_rates, 0.4)

    # v2w: 2주 연속 저조 시 STOP (기존 3주 → 2주 단축)
    try:
        from src.settings.constants import DESSERT_2WEEK_EVALUATION_ENABLED
        stop_threshold = 2 if DESSERT_2WEEK_EVALUATION_ENABLED else 3
    except ImportError:
        stop_threshold = 3

    if consec >= stop_threshold:
        return (
            DessertDecisionType.STOP_RECOMMEND,
            f"판매율 40% 미만 {consec}주 연속",
            False,
        )
    if consec >= 1:
        return (
            DessertDecisionType.WATCH,
            f"판매율 40% 미만 {consec}주 연속 — 주시 필요",
            False,
        )
    return (DessertDecisionType.KEEP, "정상", False)


def judge_category_c(
    lifecycle: DessertLifecycle,
    metrics: DessertSalesMetrics,
) -> Tuple[DessertDecisionType, str, bool]:
    """카테고리 C: 상온디저트-장기 (월 1회 판단)

    Rules:
        - 신상품(4주): 보호 기간 — KEEP/WATCH만
        - 폐기율 150%+ → STOP, 100~150% → REDUCE
        - 이후: 월간 판매량 < 카테고리 평균의 20% → 정지
    """
    if lifecycle == DessertLifecycle.NEW:
        return (DessertDecisionType.KEEP, "신상품 보호 기간", False)

    # 폐기율 기반 판단
    waste_rate = calc_waste_rate(metrics.total_sale_qty, metrics.total_disuse_qty)
    if waste_rate >= 1.5:
        return (
            DessertDecisionType.STOP_RECOMMEND,
            f"폐기율 {waste_rate:.0%} (150% 이상)",
            False,
        )
    if waste_rate >= 1.0:
        return (
            DessertDecisionType.REDUCE_ORDER,
            f"폐기율 {waste_rate:.0%} (100~150%) → 발주 감량",
            False,
        )

    if metrics.category_avg_sale_qty > 0:
        ratio = metrics.total_sale_qty / metrics.category_avg_sale_qty
        if ratio < 0.2:
            return (
                DessertDecisionType.STOP_RECOMMEND,
                f"카테고리 평균 대비 {ratio:.0%} (20% 미만)",
                False,
            )
    return (DessertDecisionType.KEEP, "정상", False)


def judge_category_d(
    lifecycle: DessertLifecycle,
    metrics: DessertSalesMetrics,
) -> Tuple[DessertDecisionType, str, bool]:
    """카테고리 D: 냉장젤리/푸딩 (월 1회 판단)

    Rules:
        - 신상품 보호: KEEP/WATCH만 허용
        - 폐기율 150%+ → STOP, 100~150% → REDUCE
        - 월간 판매량 0개가 2개월 연속 → 정지
    """
    # 신상품 보호 기간
    if lifecycle == DessertLifecycle.NEW:
        return (DessertDecisionType.KEEP, "신상품 보호 기간", False)

    # 폐기율 기반 판단
    waste_rate = calc_waste_rate(metrics.total_sale_qty, metrics.total_disuse_qty)
    if waste_rate >= 1.5:
        return (
            DessertDecisionType.STOP_RECOMMEND,
            f"폐기율 {waste_rate:.0%} (150% 이상)",
            False,
        )
    if waste_rate >= 1.0:
        return (
            DessertDecisionType.REDUCE_ORDER,
            f"폐기율 {waste_rate:.0%} (100~150%) → 발주 감량",
            False,
        )

    consec = metrics.consecutive_zero_months
    if consec >= 2:
        return (
            DessertDecisionType.STOP_RECOMMEND,
            f"무판매 {consec}개월 연속",
            False,
        )
    if consec == 1:
        return (
            DessertDecisionType.WATCH,
            "무판매 1개월 — 다음 달 재확인",
            False,
        )
    return (DessertDecisionType.KEEP, "정상", False)


# ============================================================================
# 통합 판단 디스패처
# ============================================================================

_JUDGMENT_FUNCTIONS = {
    DessertCategory.A: judge_category_a,
    DessertCategory.B: judge_category_b,
    DessertCategory.C: judge_category_c,
    DessertCategory.D: judge_category_d,
}


def judge_item(
    category: DessertCategory,
    lifecycle: DessertLifecycle,
    metrics: DessertSalesMetrics,
) -> Tuple[DessertDecisionType, str, bool]:
    """카테고리에 맞는 판단 함수를 호출합니다.

    Args:
        category: 디저트 카테고리 (A/B/C/D)
        lifecycle: 상품 생애주기
        metrics: 판매 집계 데이터

    Returns:
        (decision_type, reason, is_rapid_decline_warning)
    """
    judge_fn = _JUDGMENT_FUNCTIONS.get(category, judge_category_a)
    return judge_fn(lifecycle, metrics)
