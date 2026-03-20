"""디저트 REDUCE_ORDER 통합 테스트"""
import pytest
from src.prediction.categories.dessert_decision.enums import (
    DessertCategory, DessertLifecycle, DessertDecisionType,
)
from src.prediction.categories.dessert_decision.judge import (
    judge_category_a, judge_category_b, judge_category_c, judge_category_d,
    judge_item, calc_waste_rate, calc_sale_rate,
)
from src.prediction.categories.dessert_decision.models import DessertSalesMetrics


def _make_metrics(**overrides) -> DessertSalesMetrics:
    defaults = dict(
        period_start="2026-03-13", period_end="2026-03-20",
        total_order_qty=10, total_sale_qty=5, total_disuse_qty=2,
        sale_amount=5000, disuse_amount=2000, sale_rate=0.71,
        category_avg_sale_qty=10.0, prev_period_sale_qty=5,
        sale_trend_pct=0.0, consecutive_low_weeks=0,
        consecutive_zero_months=0, weekly_sale_rates=[0.8, 0.7, 0.6, 0.5],
    )
    defaults.update(overrides)
    return DessertSalesMetrics(**defaults)


class TestCalcWasteRate:
    def test_normal(self):
        assert calc_waste_rate(10, 5) == 0.5

    def test_zero_sale_with_disuse(self):
        assert calc_waste_rate(0, 3) == 999.0

    def test_zero_both(self):
        assert calc_waste_rate(0, 0) == 0.0

    def test_exact_100pct(self):
        assert calc_waste_rate(5, 5) == 1.0

    def test_exact_150pct(self):
        assert calc_waste_rate(4, 6) == 1.5


class TestCategoryAReduceOrder:
    """카테고리 A (냉장디저트) REDUCE_ORDER 관련 테스트"""

    def test_normal_keep(self):
        """케이스 1: 정상 흐름 -> KEEP"""
        m = _make_metrics(total_sale_qty=10, total_disuse_qty=2)
        d, r, w = judge_category_a(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.KEEP

    def test_waste_rate_100_reduce(self):
        """케이스 2: 폐기율 100~150% -> REDUCE_ORDER"""
        # 판매 3, 폐기 4 -> 폐기율 133%
        m = _make_metrics(total_sale_qty=3, total_disuse_qty=4)
        d, r, w = judge_category_a(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.REDUCE_ORDER
        assert "폐기율" in r

    def test_waste_rate_150_stop(self):
        """폐기율 150%+ -> STOP_RECOMMEND"""
        # 판매 2, 폐기 3 -> 폐기율 150%
        m = _make_metrics(total_sale_qty=2, total_disuse_qty=3)
        d, r, w = judge_category_a(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.STOP_RECOMMEND
        assert "폐기율" in r

    def test_growth_sale_rate_50_reduce(self):
        """판매율 50% 미만 2주 -> REDUCE (성장/하락기)"""
        m = _make_metrics(
            total_sale_qty=10, total_disuse_qty=2,
            weekly_sale_rates=[0.3, 0.4, 0.8, 0.7],  # 2주 연속 50% 미만
        )
        d, r, w = judge_category_a(DessertLifecycle.GROWTH_DECLINE, m)
        assert d == DessertDecisionType.REDUCE_ORDER

    def test_growth_sale_rate_30_stop(self):
        """판매율 30% 미만 2주 -> STOP (성장/하락기)"""
        m = _make_metrics(
            total_sale_qty=10, total_disuse_qty=2,
            weekly_sale_rates=[0.2, 0.1, 0.8, 0.7],  # 2주 연속 30% 미만
        )
        d, r, w = judge_category_a(DessertLifecycle.GROWTH_DECLINE, m)
        assert d == DessertDecisionType.STOP_RECOMMEND

    def test_new_product_protection_blocks_reduce(self):
        """케이스 4-3: 신상품 보호 중 REDUCE/STOP 차단"""
        # 폐기율 200%이지만 NEW -> KEEP
        m = _make_metrics(total_sale_qty=1, total_disuse_qty=5)
        d, r, w = judge_category_a(DessertLifecycle.NEW, m)
        assert d in (DessertDecisionType.KEEP, DessertDecisionType.WATCH)
        assert d != DessertDecisionType.REDUCE_ORDER
        assert d != DessertDecisionType.STOP_RECOMMEND

    def test_new_product_rapid_decline_watch(self):
        """신상품 급락 경고 -> WATCH"""
        m = _make_metrics(
            total_sale_qty=1, total_disuse_qty=5,
            sale_trend_pct=-60.0, prev_period_sale_qty=5,
        )
        d, r, w = judge_category_a(DessertLifecycle.NEW, m)
        assert d == DessertDecisionType.WATCH
        assert w is True


class TestCategoryBReduceOrder:
    def test_waste_rate_reduce(self):
        m = _make_metrics(total_sale_qty=3, total_disuse_qty=4)
        d, r, w = judge_category_b(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.REDUCE_ORDER

    def test_new_protection(self):
        m = _make_metrics(total_sale_qty=1, total_disuse_qty=5)
        d, r, w = judge_category_b(DessertLifecycle.NEW, m)
        assert d == DessertDecisionType.KEEP


class TestCategoryCReduceOrder:
    def test_waste_rate_reduce(self):
        m = _make_metrics(total_sale_qty=3, total_disuse_qty=4)
        d, r, w = judge_category_c(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.REDUCE_ORDER

    def test_new_protection(self):
        m = _make_metrics(total_sale_qty=1, total_disuse_qty=5)
        d, r, w = judge_category_c(DessertLifecycle.NEW, m)
        assert d == DessertDecisionType.KEEP


class TestCategoryDReduceOrder:
    def test_waste_rate_reduce(self):
        m = _make_metrics(total_sale_qty=3, total_disuse_qty=4)
        d, r, w = judge_category_d(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.REDUCE_ORDER

    def test_waste_rate_stop(self):
        m = _make_metrics(total_sale_qty=2, total_disuse_qty=3)
        d, r, w = judge_category_d(DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.STOP_RECOMMEND

    def test_new_protection(self):
        """NEW 보호: 카테고리 D도 신상품이면 KEEP만 반환"""
        m = _make_metrics(total_sale_qty=2, total_disuse_qty=3)
        d, r, w = judge_category_d(DessertLifecycle.NEW, m)
        assert d == DessertDecisionType.KEEP


class TestJudgeItemDispatcher:
    def test_dispatches_to_correct_category(self):
        m = _make_metrics(total_sale_qty=3, total_disuse_qty=4)
        for cat in DessertCategory:
            d, r, w = judge_item(cat, DessertLifecycle.ESTABLISHED, m)
            assert d == DessertDecisionType.REDUCE_ORDER, f"Cat {cat.value} should REDUCE"


class TestEnumReduceOrder:
    def test_reduce_order_exists(self):
        assert hasattr(DessertDecisionType, 'REDUCE_ORDER')
        assert DessertDecisionType.REDUCE_ORDER.value == "REDUCE_ORDER"

    def test_ordering(self):
        vals = [e.value for e in DessertDecisionType]
        assert vals.index("REDUCE_ORDER") > vals.index("WATCH")
        assert vals.index("REDUCE_ORDER") < vals.index("STOP_RECOMMEND")
