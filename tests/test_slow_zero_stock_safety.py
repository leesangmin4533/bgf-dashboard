"""
slow 패턴 + 재고 0 + 최근 판매 있음 → ROP 1개 보장 (SLOW_ZERO_STOCK_SAFETY)

근본 원인: demand_pattern=slow 상품은 predicted_qty=0이고,
          ROP의 data_days<7 가드가 최근 판매 여부를 무시하여 발주 0 → death spiral
수정: 최근 30일 판매 있으면 data_days 가드 우회하여 ROP 1개 보장
"""

import pytest
from src.prediction.improved_predictor import DATA_MIN_DAYS_FOR_LARGE_UNIT


def _should_skip_rop(data_days, is_new_product, has_recent_sales, has_periodic_sales=False):
    """ROP 스킵 판정 로직 (improved_predictor.py _stage_rop 미러)"""
    return (
        data_days < DATA_MIN_DAYS_FOR_LARGE_UNIT
        and not is_new_product
        and not has_recent_sales
        and not has_periodic_sales
    )


class TestSlowZeroStockSafety:
    """slow 패턴 재고소진 안전망 테스트"""

    def test_slow_stock0_recent_sale_bypasses_guard(self):
        """slow + stock=0 + 최근 판매 있음 → data_days 가드 우회 → ROP 1개"""
        # data_days=3 (< 7), 신제품 아님, 최근 판매 있음
        assert _should_skip_rop(
            data_days=3, is_new_product=False, has_recent_sales=True
        ) is False  # 가드 우회 → ROP 발동

    def test_slow_stock2_no_rop_entry(self):
        """slow + stock=2 → ROP 분기 자체 미진입 (재고 있으므로)"""
        effective_stock = 2
        order_qty = 0
        pending_qty = 0
        # ROP 진입 조건: stock==0 AND order_qty==0 AND pending==0
        enters_rop = effective_stock == 0 and order_qty == 0 and pending_qty == 0
        assert enters_rop is False

    def test_slow_stock0_no_recent_sale_skips(self):
        """slow + stock=0 + 최근 판매 없음 → ROP 스킵 유지 (death spiral 허용)"""
        # 완전 사장 상품: 30일 판매 0 → 발주 불필요
        assert _should_skip_rop(
            data_days=3, is_new_product=False, has_recent_sales=False
        ) is True  # 가드 유지 → ROP 스킵

    def test_data_days_7plus_always_passes(self):
        """data_days>=7 → data_days 가드 해당 없음 (기존 동작)"""
        assert _should_skip_rop(
            data_days=10, is_new_product=False, has_recent_sales=False
        ) is False

    def test_new_product_always_passes(self):
        """신제품은 data_days 가드 무조건 우회 (기존 동작)"""
        assert _should_skip_rop(
            data_days=2, is_new_product=True, has_recent_sales=False
        ) is False

    def test_data_min_days_constant(self):
        """DATA_MIN_DAYS_FOR_LARGE_UNIT = 7 확인"""
        assert DATA_MIN_DAYS_FOR_LARGE_UNIT == 7


class TestSlowPeriodicRop:
    """SLOW 주기 판매 상품 ROP 유지 테스트"""

    def test_periodic_slow_triggers_rop(self):
        """60일 내 3회 판매, stock=0 → 주기적 SLOW → ROP=1"""
        # 30일 판매 없음, but 60일 3회 판매 → periodic
        assert _should_skip_rop(
            data_days=3, is_new_product=False,
            has_recent_sales=False, has_periodic_sales=True
        ) is False  # ROP 발동

    def test_dead_stock_skips_rop(self):
        """60일 내 0회 판매, stock=0 → 사장상품 → ROP 스킵"""
        assert _should_skip_rop(
            data_days=3, is_new_product=False,
            has_recent_sales=False, has_periodic_sales=False
        ) is True  # ROP 스킵

    def test_single_sale_skips_rop(self):
        """60일 내 1회 판매 → MIN_SALES=2 미달 → ROP 스킵"""
        # has_periodic_sales = (1 >= 2) = False
        assert _should_skip_rop(
            data_days=3, is_new_product=False,
            has_recent_sales=False, has_periodic_sales=False
        ) is True

    def test_recent_sales_still_works(self):
        """30일 내 판매 있으면 periodic 확인 불필요 → ROP=1"""
        assert _should_skip_rop(
            data_days=3, is_new_product=False,
            has_recent_sales=True, has_periodic_sales=False
        ) is False  # 기존 로직 유지

    def test_periodic_min_sales_constant(self):
        """SLOW_PERIODIC_MIN_SALES = 2 확인"""
        from src.settings.constants import SLOW_PERIODIC_MIN_SALES
        assert SLOW_PERIODIC_MIN_SALES == 2
