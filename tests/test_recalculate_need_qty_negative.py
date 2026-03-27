"""
OrderAdjuster.recalculate_need_qty() 음수/비정상 입력 테스트

핵심 공식 (L76):
    need = predicted_sales + safety_stock - effective_stock - effective_pending

음수 클램프 적용 (L68-74):
    predicted_sales < 0 → 0으로 보정 + warning 로그
    safety_stock < 0 → 0으로 보정 + warning 로그
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.order.order_adjuster import OrderAdjuster


class TestRecalculateNeedQtyNegative:
    """recalculate_need_qty()의 음수/비정상 입력 테스트"""

    def setup_method(self):
        self.adjuster = OrderAdjuster()

    # =========================================================
    # Case 1: predicted_sales 정상 양수 — 기준 동작
    # =========================================================

    def test_normal_positive_predicted_sales(self):
        """정상: predicted=6, safety=3, stock=2, pending=0 → need=7"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=6.0,
            safety_stock=3.0,
            new_stock=2,
            new_pending=0,
            daily_avg=2.0,
        )
        assert result == 7  # 6+3-2-0=7

    def test_normal_need_zero(self):
        """정상: 재고 충분 → 0"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=2.0,
            safety_stock=1.0,
            new_stock=5,
            new_pending=0,
            daily_avg=2.0,
        )
        assert result == 0  # 2+1-5=−2 → 0

    # =========================================================
    # Case 2: predicted_sales가 0
    # =========================================================

    def test_zero_predicted_sales_with_safety(self):
        """predicted=0이지만 safety_stock 있으면 발주"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.0,
            safety_stock=3.0,
            new_stock=0,
            new_pending=0,
            daily_avg=0.0,
        )
        # need=0+3-0-0=3
        assert result >= 1

    def test_zero_predicted_sales_zero_safety(self):
        """predicted=0, safety=0 → 발주 불필요"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=0.0,
            safety_stock=0.0,
            new_stock=0,
            new_pending=0,
            daily_avg=0.0,
        )
        assert result == 0

    # =========================================================
    # Case 3: predicted_sales가 음수
    # =========================================================

    def test_negative_predicted_sales_clamped_to_zero(self):
        """predicted=-5 → 0으로 클램프, need=0+3-0-0=3

        수정 후: 음수 predicted_sales는 0으로 보정되어
        safety_stock만으로 발주량 결정
        """
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=-5.0,
            safety_stock=3.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        # 수정 후: predicted=-5→0, need=0+3-0-0=3
        assert result == 3

    def test_small_negative_predicted_sales(self):
        """predicted=-0.5, safety=3, stock=0 → need=2.5"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=-0.5,
            safety_stock=3.0,
            new_stock=0,
            new_pending=0,
            daily_avg=1.0,
        )
        # -0.5+3-0-0=2.5 → 양수이므로 발주 발생
        assert result >= 1

    def test_large_negative_predicted_sales_clamped(self):
        """predicted=-100 → 0으로 클램프, need=0+10-0-0=10"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=-100.0,
            safety_stock=10.0,
            new_stock=0,
            new_pending=0,
            daily_avg=5.0,
        )
        # 수정 후: predicted=-100→0, need=0+10-0-0=10
        assert result == 10

    # =========================================================
    # Case 4: safety_stock이 음수
    # =========================================================

    def test_negative_safety_stock_clamped_to_zero(self):
        """safety_stock=-3 → 0으로 클램프, need=6+0-0-0=6

        수정 후: 음수 safety_stock은 0으로 보정
        """
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=6.0,
            safety_stock=-3.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        # 수정 후: safety=-3→0, need=6+0-0-0=6
        assert result == 6

    def test_large_negative_safety_stock_clamped(self):
        """safety_stock=-100 → 0으로 클램프, need=6+0-0-0=6"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=6.0,
            safety_stock=-100.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        # 수정 후: safety=-100→0, need=6+0-0-0=6
        assert result == 6

    # =========================================================
    # Case 5: 둘 다 음수
    # =========================================================

    def test_both_negative(self):
        """predicted=-5, safety=-3 → need 크게 음수"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=-5.0,
            safety_stock=-3.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        # -5+(-3)-0-0=-8 → 0
        assert result == 0

    def test_both_small_negative(self):
        """predicted=-0.1, safety=-0.1, stock=0 → need=-0.2 → 0"""
        result = self.adjuster.recalculate_need_qty(
            predicted_sales=-0.1,
            safety_stock=-0.1,
            new_stock=0,
            new_pending=0,
            daily_avg=0.5,
        )
        assert result == 0

    # =========================================================
    # Case 6: 음수가 결과에 미치는 영향 (비교 테스트)
    # =========================================================

    def test_negative_vs_zero_predicted_comparison(self):
        """음수 predicted는 0으로 클램프되므로 0 predicted와 동일한 발주"""
        qty_zero = self.adjuster.recalculate_need_qty(
            predicted_sales=0.0,
            safety_stock=5.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        qty_neg = self.adjuster.recalculate_need_qty(
            predicted_sales=-3.0,
            safety_stock=5.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        # 음수 predicted → need 더 작음 → 발주량 같거나 적음
        assert qty_neg <= qty_zero

    def test_negative_safety_vs_zero_comparison(self):
        """음수 safety는 0으로 클램프되므로 0 safety와 동일한 발주"""
        qty_zero = self.adjuster.recalculate_need_qty(
            predicted_sales=5.0,
            safety_stock=0.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        qty_neg = self.adjuster.recalculate_need_qty(
            predicted_sales=5.0,
            safety_stock=-3.0,
            new_stock=0,
            new_pending=0,
            daily_avg=2.0,
        )
        assert qty_neg <= qty_zero
