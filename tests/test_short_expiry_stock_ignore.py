"""
단기유통(유통기한 1일) 재고 무시 테스트

문제: 주먹밥(mid_cd=002, expiration_days=1)이 어제 재고=1로 인해
      발주 스킵됨. 유통기한 1일 상품은 어제 재고가 오늘 폐기 대상이므로
      재고와 무관하게 발주되어야 함.
해결: order_adjuster, improved_predictor에서 expiration_days <= 1이면
      current_stock을 0으로 처리.
"""
import pytest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.order.order_adjuster import OrderAdjuster


class TestShortExpiryStockIgnore:
    """유통기한 1일 이하 상품의 재고 무시 로직 테스트"""

    def setup_method(self):
        self.adjuster = OrderAdjuster()

    # ----- 핵심: 유통기한 1일 + 재고 있어도 발주 -----

    def test_food_1day_expiry_ignores_stock(self):
        """유통기한 1일 푸드 상품: 재고=1이어도 발주해야 함"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.9, safety_stock=0.6,
            new_stock=1, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="002"
        )
        assert result >= 1, f"유통기한 1일인데 재고 때문에 발주 스킵됨: {result}"

    def test_food_1day_expiry_stock_2_still_orders(self):
        """유통기한 1일 푸드: 재고=2여도 발주"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=1.0, safety_stock=0.7,
            new_stock=2, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="002"
        )
        assert result >= 1

    def test_food_1day_expiry_zero_stock_orders(self):
        """유통기한 1일 푸드: 재고=0이면 당연히 발주"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.9, safety_stock=0.6,
            new_stock=0, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="002"
        )
        assert result >= 1

    # ----- 다른 푸드 카테고리도 동일 적용 -----

    def test_food_001_1day_expiry_ignores_stock(self):
        """도시락(001) 유통기한 1일: 재고 무시"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=1.0, safety_stock=0.7,
            new_stock=1, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="001"
        )
        assert result >= 1

    def test_food_003_1day_expiry_ignores_stock(self):
        """샌드위치(003) 유통기한 1일: 재고 무시"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.8, safety_stock=0.5,
            new_stock=1, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="003"
        )
        assert result >= 1

    # ----- 유통기한 2일 이상은 기존 로직 유지 -----

    def test_food_2day_expiry_uses_stock_normally(self):
        """유통기한 2일 푸드: 재고 정상 차감 (기존 로직)"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.9, safety_stock=0.6,
            new_stock=2, new_pending=0, daily_avg=1.0,
            expiration_days=2, mid_cd="002"
        )
        # need = 0.9 + 0.6 - 2 - 0 = -0.5 → 0
        assert result == 0

    def test_food_3day_expiry_uses_stock_normally(self):
        """유통기한 3일 푸드: 재고 정상 차감"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=1.0, safety_stock=0.7,
            new_stock=2, new_pending=0, daily_avg=1.0,
            expiration_days=3, mid_cd="003"
        )
        assert result == 0

    # ----- 비푸드 카테고리는 영향 없음 -----

    def test_non_food_1day_expiry_uses_stock(self):
        """비푸드(mid_cd=015) 유통기한 1일이어도 재고 정상 차감"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.9, safety_stock=0.6,
            new_stock=2, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="015"
        )
        assert result == 0

    def test_tobacco_ignores_expiry_logic(self):
        """담배(050): 유통기한 무관, 재고 정상 차감"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=3.0, safety_stock=1.0,
            new_stock=5, new_pending=0, daily_avg=3.0,
            expiration_days=None, mid_cd="050"
        )
        assert result == 0

    # ----- 미입고 할인은 기존 유지 -----

    def test_1day_expiry_pending_discount_still_applies(self):
        """유통기한 1일: 재고 무시 + 미입고 50% 할인 동시 적용"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=1.0, safety_stock=0.7,
            new_stock=1, new_pending=2, daily_avg=1.0,
            expiration_days=1, mid_cd="002"
        )
        # effective_stock=0 (무시), effective_pending=1 (50%할인)
        # need = 1.0 + 0.7 - 0 - 1 = 0.7 → 1
        assert result >= 1

    def test_1day_expiry_large_pending_blocks(self):
        """유통기한 1일: 미입고가 충분히 크면 발주 불필요"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=1.0, safety_stock=0.5,
            new_stock=0, new_pending=4, daily_avg=1.0,
            expiration_days=1, mid_cd="002"
        )
        # effective_pending = 4 * 0.5 = 2
        # need = 1.0 + 0.5 - 0 - 2 = -0.5 → 0
        assert result == 0

    # ----- expiration_days=None 처리 -----

    def test_none_expiry_uses_stock_normally(self):
        """expiration_days=None: 재고 정상 차감"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=1.0, safety_stock=0.5,
            new_stock=2, new_pending=0, daily_avg=1.0,
            expiration_days=None, mid_cd="002"
        )
        assert result == 0


class TestShortExpiryViaAutoOrder:
    """AutoOrderSystem._recalculate_need_qty 위임 확인"""

    def _make_system(self):
        from src.order.auto_order import AutoOrderSystem
        sys_obj = object.__new__(AutoOrderSystem)
        sys_obj._adjuster = OrderAdjuster()
        return sys_obj

    def test_auto_order_delegates_short_expiry(self):
        """AutoOrderSystem 경유 호출도 재고 무시 적용"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.9, safety_stock=0.6,
            new_stock=1, new_pending=0, daily_avg=1.0,
            expiration_days=1, mid_cd="002"
        )
        assert result >= 1

    def test_auto_order_normal_expiry_unchanged(self):
        """유통기한 2일 이상: 기존 동작 그대로"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.9, safety_stock=0.6,
            new_stock=2, new_pending=0, daily_avg=1.0,
            expiration_days=2, mid_cd="002"
        )
        assert result == 0
