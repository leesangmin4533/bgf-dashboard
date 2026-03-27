"""
유통기한 만료 재고 override 테스트

order_adjuster.apply_pending_and_stock()에서
expired_stock_overrides가 전달되면:
1. 해당 SKU의 재고에서 만료분을 차감
2. stock_changed=True로 강제하여 재계산 실행
3. 실제 DB는 수정하지 않음
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.order.order_adjuster import OrderAdjuster


class TestExpiredStockOverride:
    """유통기한 만료 재고 override 테스트"""

    def setup_method(self):
        self.adjuster = OrderAdjuster()

    def _make_item(self, **overrides):
        """기본 발주 항목 생성 헬퍼"""
        base = {
            "item_cd": "TEST001",
            "item_nm": "테스트상품",
            "mid_cd": "049",
            "final_order_qty": 3,
            "current_stock": 5,
            "pending_receiving_qty": 0,
            "predicted_sales": 6.0,
            "safety_stock": 3.0,
            "daily_avg": 2.0,
            "order_unit_qty": 1,
            "promo_type": "",
            "expiration_days": 365,
        }
        base.update(overrides)
        return base

    # =========================================================
    # Case 1: 만료 재고 없음 — 기존 동작 유지
    # =========================================================

    def test_no_expired_overrides_unchanged(self):
        """expired_stock_overrides가 None이면 기존 동작 그대로"""
        item = self._make_item(current_stock=5, final_order_qty=3)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},  # 동일 재고
            expired_stock_overrides=None,
        )
        assert len(result) == 1
        assert result[0]["final_order_qty"] == 3  # 원본 유지

    def test_empty_expired_overrides_unchanged(self):
        """빈 dict도 기존 동작 유지"""
        item = self._make_item(current_stock=5, final_order_qty=3)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
            expired_stock_overrides={},
        )
        assert len(result) == 1
        assert result[0]["final_order_qty"] == 3

    # =========================================================
    # Case 2: 만료 재고 있음 — 재계산 발생
    # =========================================================

    def test_expired_stock_triggers_recalculation(self):
        """만료 재고가 있으면 stock_changed=True로 재계산"""
        item = self._make_item(
            current_stock=5,
            pending_receiving_qty=0,
            final_order_qty=0,  # 재고 충분이라 0
            predicted_sales=6.0,
            safety_stock=3.0,
        )
        # 재고 5개 중 3개가 오늘 만료 → effective_stock = 2
        # need = 6 + 3 - 2 - 0 = 7
        result, discrepancies = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},  # prefetch 재고 동일
            expired_stock_overrides={"TEST001": 3},
        )
        assert len(result) == 1
        # 재계산 발생: need=7 > 0 → 발주량 > 0
        assert result[0]["final_order_qty"] > 0
        # 재고 discrepancy 기록됨
        assert len(discrepancies) == 1
        assert discrepancies[0]["stock_at_order"] == 2  # 5 - 3 = 2

    def test_expired_stock_zero_remaining(self):
        """만료분이 전체 재고 이상이면 재고=0"""
        item = self._make_item(
            current_stock=3,
            final_order_qty=0,
            predicted_sales=4.0,
            safety_stock=2.0,
        )
        # 재고 3개 전부 만료 → effective_stock = 0
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 3},
            expired_stock_overrides={"TEST001": 5},  # 만료분 > 실제 재고
        )
        assert len(result) == 1
        assert result[0]["final_order_qty"] > 0  # 재고 0 → 발주 필요
        assert result[0]["current_stock"] == 0  # max(0, 3-5) = 0

    # =========================================================
    # Case 3: 다른 SKU는 영향 없음
    # =========================================================

    def test_only_expired_sku_affected(self):
        """만료 재고가 없는 SKU는 영향 없음"""
        item_a = self._make_item(
            item_cd="EXPIRED001", current_stock=5, final_order_qty=0,
            predicted_sales=6.0, safety_stock=3.0,
        )
        item_b = self._make_item(
            item_cd="NORMAL001", current_stock=5, final_order_qty=2,
            predicted_sales=6.0, safety_stock=3.0,
        )
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item_a, item_b],
            pending_data={},
            stock_data={"EXPIRED001": 5, "NORMAL001": 5},
            expired_stock_overrides={"EXPIRED001": 3},  # EXPIRED001만 만료
        )
        # NORMAL001은 stock_changed=False → 원본 유지
        normal = [r for r in result if r["item_cd"] == "NORMAL001"]
        assert len(normal) == 1
        assert normal[0]["final_order_qty"] == 2  # 변경 없음

    # =========================================================
    # Case 4: stock_changed + expired 동시
    # =========================================================

    def test_stock_changed_and_expired_combined(self):
        """재고 변화 + 만료 재고 동시 적용"""
        item = self._make_item(
            current_stock=10,
            final_order_qty=0,
            predicted_sales=8.0,
            safety_stock=4.0,
        )
        # prefetch 재고 7 (변화 있음) + 만료 2개 → effective = 5
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 7},  # 10 → 7 (stock_changed)
            expired_stock_overrides={"TEST001": 2},  # 추가로 2개 만료
        )
        assert len(result) == 1
        assert result[0]["current_stock"] == 5  # 7 - 2 = 5

    # =========================================================
    # Case 5: 만료분이 0이거나 재고가 0
    # =========================================================

    def test_zero_expiring_qty_ignored(self):
        """만료분이 0이면 무시"""
        item = self._make_item(current_stock=5, final_order_qty=3)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
            expired_stock_overrides={"TEST001": 0},
        )
        assert result[0]["final_order_qty"] == 3  # 변경 없음

    def test_zero_stock_no_deduction(self):
        """재고가 0이면 차감 불필요"""
        item = self._make_item(current_stock=0, final_order_qty=5)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 0},
            expired_stock_overrides={"TEST001": 3},
        )
        # 재고 0이라 차감 없음 → stock_changed=False → 원본 유지
        assert result[0]["final_order_qty"] == 5

    # =========================================================
    # Case 6: DB 오염 없음 확인 (override만, 실제 DB 수정 없음)
    # =========================================================

    def test_original_item_not_mutated(self):
        """원본 item dict의 current_stock은 변경되지 않음"""
        original_item = self._make_item(current_stock=5, final_order_qty=0)
        original_stock_value = original_item["current_stock"]

        self.adjuster.apply_pending_and_stock(
            order_list=[original_item],
            pending_data={},
            stock_data={"TEST001": 5},
            expired_stock_overrides={"TEST001": 3},
        )
        # 원본 dict는 변경 안 됨 (adjusted_item = item.copy() 사용)
        assert original_item["current_stock"] == original_stock_value
