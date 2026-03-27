"""
OrderAdjuster.apply_pending_and_stock() None 값 처리 테스트

dict에서 꺼낸 값이 None / 정상 숫자 / 키 없음 3가지 케이스를 검증.
수정 전 현재 상태에서 어떤 케이스가 실패하는지 확인 목적.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.order.order_adjuster import OrderAdjuster


class TestApplyPendingAndStockNoneValues:
    """apply_pending_and_stock()의 None 값 안전성 테스트"""

    def setup_method(self):
        self.adjuster = OrderAdjuster()

    def _make_item(self, **overrides):
        """기본 발주 항목 생성 헬퍼"""
        base = {
            "item_cd": "TEST001",
            "item_nm": "테스트상품",
            "mid_cd": "049",
            "final_order_qty": 5,
            "current_stock": 2,
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
    # Case 1: 정상 숫자 — 기준 동작 확인
    # =========================================================

    def test_normal_values_no_change(self):
        """재고/미입고 변화 없으면 원본 qty 유지"""
        item = self._make_item(
            current_stock=2,
            pending_receiving_qty=0,
            final_order_qty=5,
        )
        result, discrepancies = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},           # 변화 없음
            stock_data={},             # 변화 없음
        )
        assert len(result) == 1
        assert result[0]["final_order_qty"] == 5

    def test_normal_values_stock_increased(self):
        """실시간 재고 증가 → 발주량 감소"""
        item = self._make_item(
            current_stock=2,
            pending_receiving_qty=0,
            predicted_sales=6.0,
            safety_stock=3.0,
            final_order_qty=7,
        )
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 10},  # 재고 2→10
        )
        # 재고 크게 증가 → 발주량 감소 또는 제외
        assert len(result) <= 1
        if result:
            assert result[0]["final_order_qty"] < 7

    # =========================================================
    # Case 2: dict 값이 None인 경우 (핵심 버그 케이스)
    # =========================================================

    def test_predicted_sales_is_none(self):
        """predicted_sales가 None이면 TypeError 발생하는지 확인"""
        item = self._make_item(predicted_sales=None)
        # stock_data를 줘서 stock_changed=True가 되도록
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},  # 재고 변화 → recalculate 트리거
        )
        # None이 산술 연산에 들어가면 TypeError
        # 정상 동작이면 결과가 나와야 함
        assert isinstance(result, list)

    def test_safety_stock_is_none(self):
        """safety_stock이 None이면 TypeError 발생하는지 확인"""
        item = self._make_item(safety_stock=None)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_daily_avg_is_none(self):
        """daily_avg가 None이면 TypeError 발생하는지 확인"""
        item = self._make_item(daily_avg=None)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_order_unit_qty_is_none(self):
        """order_unit_qty가 None이면 TypeError 발생하는지 확인"""
        item = self._make_item(order_unit_qty=None)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_current_stock_is_none(self):
        """current_stock이 None이면 TypeError 발생하는지 확인"""
        item = self._make_item(current_stock=None)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_pending_receiving_qty_is_none(self):
        """pending_receiving_qty가 None이면 TypeError 발생하는지 확인"""
        item = self._make_item(pending_receiving_qty=None)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={"TEST001": 3},  # pending 변화
            stock_data={},
        )
        assert isinstance(result, list)

    def test_final_order_qty_is_none(self):
        """final_order_qty가 None이면 비교 연산에서 TypeError"""
        item = self._make_item(final_order_qty=None)
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_multiple_none_fields(self):
        """여러 필드가 동시에 None인 경우"""
        item = self._make_item(
            predicted_sales=None,
            safety_stock=None,
            daily_avg=None,
        )
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    # =========================================================
    # Case 3: dict에 키 자체가 없는 경우
    # =========================================================

    def test_missing_predicted_sales_key(self):
        """predicted_sales 키 자체가 없는 경우"""
        item = self._make_item()
        del item["predicted_sales"]
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_missing_safety_stock_key(self):
        """safety_stock 키 자체가 없는 경우"""
        item = self._make_item()
        del item["safety_stock"]
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_missing_daily_avg_key(self):
        """daily_avg 키 자체가 없는 경우"""
        item = self._make_item()
        del item["daily_avg"]
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_missing_order_unit_qty_key(self):
        """order_unit_qty 키 자체가 없는 경우"""
        item = self._make_item()
        del item["order_unit_qty"]
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_missing_multiple_keys(self):
        """여러 키가 동시에 없는 경우"""
        item = {
            "item_cd": "TEST001",
            "item_nm": "테스트상품",
            "mid_cd": "049",
            "final_order_qty": 5,
            # predicted_sales, safety_stock, daily_avg, order_unit_qty 등 전부 없음
        }
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    def test_completely_empty_item(self):
        """item_cd만 있는 최소 dict"""
        item = {"item_cd": "TEST001"}
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 5},
        )
        assert isinstance(result, list)

    # =========================================================
    # 엣지 케이스: 빈 입력
    # =========================================================

    def test_empty_order_list(self):
        """빈 발주 목록"""
        result, discrepancies = self.adjuster.apply_pending_and_stock(
            order_list=[],
            pending_data={"TEST001": 3},
            stock_data={"TEST001": 5},
        )
        assert result == []
        assert discrepancies == []

    def test_item_without_item_cd(self):
        """item_cd가 없는 항목은 스킵"""
        item = {"item_nm": "이름만있음", "final_order_qty": 5}
        result, _ = self.adjuster.apply_pending_and_stock(
            order_list=[item],
            pending_data={},
            stock_data={},
        )
        assert result == []
