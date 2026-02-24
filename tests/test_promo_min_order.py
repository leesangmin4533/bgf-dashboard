"""
행사(1+1, 2+1) 기반 최소 발주수량 배수 보정 테스트

문제: 발주 자체가 행사 배수에 맞지 않으면 고객이 행사를 이용할 수 없음
     - 1+1: 1개만 발주하면 고객이 1+1 행사를 이용할 수 없음 -> 최소 2개 발주 필요
     - 2+1: 1~2개 발주하면 2+1 행사 불가 -> 최소 3개 발주 필요
해결: improved_predictor + auto_order 양쪽에서 행사 배수 보정 적용
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helper: promo_status mock 생성
# ---------------------------------------------------------------------------
def _make_promo_status(current_promo="", promo_avg=3.0, normal_avg=3.0):
    """PromoStatus 목업 생성 (비교 연산에 필요한 속성 모두 설정)"""
    status = MagicMock()
    status.current_promo = current_promo
    status.promo_avg = promo_avg
    status.normal_avg = normal_avg
    # 행사 종료/시작 관련 속성 (비교 연산 대비)
    status.days_until_end = None  # None이면 종료 임박 로직 스킵
    status.next_promo = ""
    status.next_start_date = None
    return status


def _make_predictor_with_promo(promo_status):
    """ImprovedPredictor 최소 인스턴스 + promo mock 세팅"""
    from src.prediction.improved_predictor import ImprovedPredictor
    predictor = object.__new__(ImprovedPredictor)
    predictor._ml_predictor = None
    predictor._store_id = "test"

    # _promo_adjuster.promo_manager.get_promotion_status를 mock
    promo_mgr = MagicMock()
    promo_mgr.get_promotion_status.return_value = promo_status
    promo_adjuster = MagicMock()
    promo_adjuster.promo_manager = promo_mgr
    predictor._promo_adjuster = promo_adjuster

    return predictor


# ---------------------------------------------------------------------------
# improved_predictor._apply_promotion_adjustment 테스트
# ---------------------------------------------------------------------------
class TestPromoAdjustmentPredictor:
    """improved_predictor의 행사 배수 보정 테스트"""

    def test_1plus1_order1_becomes_2(self):
        """1+1 행사, 발주=1 -> 최소 2개"""
        promo_status = _make_promo_status(current_promo="1+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=1, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 2

    def test_2plus1_order1_becomes_3(self):
        """2+1 행사, 발주=1 -> 최소 3개"""
        promo_status = _make_promo_status(current_promo="2+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST002", product={}, order_qty=1, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 3

    def test_2plus1_order2_becomes_3(self):
        """2+1 행사, 발주=2 -> 최소 3개"""
        promo_status = _make_promo_status(current_promo="2+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST003", product={}, order_qty=2, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 3

    def test_1plus1_order0_stays_0(self):
        """1+1 행사, 발주=0 -> 보정 안함 (0 유지)"""
        promo_status = _make_promo_status(current_promo="1+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST004", product={}, order_qty=0, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 0

    def test_no_promo_order1_stays_1(self):
        """행사 없음, 발주=1 -> 변경 없음"""
        promo_status = _make_promo_status(current_promo="")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST005", product={}, order_qty=1, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 1

    def test_1plus1_order3_stays_3(self):
        """1+1 행사, 발주=3 -> 이미 promo_unit(2) 이상이므로 변경 없음"""
        promo_status = _make_promo_status(current_promo="1+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST006", product={}, order_qty=3, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 3

    def test_2plus1_order5_stays_5(self):
        """2+1 행사, 발주=5 -> 이미 promo_unit(3) 이상이므로 변경 없음"""
        promo_status = _make_promo_status(current_promo="2+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST007", product={}, order_qty=5, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 5

    def test_1plus1_with_stock1_order1_becomes_2(self):
        """1+1 행사, 재고=1, 발주=1 -> 발주 자체를 2로 올림 (핵심 수정 케이스)
        기존: expected_stock=1+0+1=2 >= 2 -> 보정 없음 (발주 1)
        수정: order_qty=1 < promo_unit=2 -> 발주 2"""
        promo_status = _make_promo_status(current_promo="1+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST008", product={}, order_qty=1, weekday_coef=1.0,
            safety_stock=1.0, current_stock=1, pending_qty=0, daily_avg=3.0
        )
        assert result == 2

    def test_2plus1_with_stock2_order1_becomes_3(self):
        """2+1 행사, 재고=2, 발주=1 -> 발주 자체를 3으로 올림 (핵심 수정 케이스)
        기존: expected_stock=2+0+1=3 >= 3 -> 보정 없음 (발주 1)
        수정: order_qty=1 < promo_unit=3 -> 발주 3"""
        promo_status = _make_promo_status(current_promo="2+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST009", product={}, order_qty=1, weekday_coef=1.0,
            safety_stock=1.0, current_stock=2, pending_qty=0, daily_avg=3.0
        )
        assert result == 3

    def test_expected_stock_fallback_still_works(self):
        """재고+미입고+발주 < promo_unit 인 경우 기존 보정도 여전히 동작
        재고=0, 미입고=0, 발주=0 -> order_qty>0 조건 불충족 -> 0 유지"""
        promo_status = _make_promo_status(current_promo="1+1")
        p = _make_predictor_with_promo(promo_status)
        result = p._apply_promotion_adjustment(
            item_cd="TEST010", product={}, order_qty=0, weekday_coef=1.0,
            safety_stock=1.0, current_stock=0, pending_qty=0, daily_avg=3.0
        )
        assert result == 0


# ---------------------------------------------------------------------------
# auto_order._recalculate_need_qty 행사 배수 테스트
# ---------------------------------------------------------------------------
class TestRecalculateNeedQtyPromo:
    """_recalculate_need_qty에 promo_type 전달 시 행사 배수 보정 테스트"""

    def _make_system(self):
        """AutoOrderSystem 최소 인스턴스 생성 (DB 미접속)"""
        from src.order.auto_order import AutoOrderSystem
        sys_obj = object.__new__(AutoOrderSystem)
        return sys_obj

    def test_1plus1_need_1_becomes_2(self):
        """1+1 행사: need=0.8 -> 올림 1개 -> 행사보정 -> 2개"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=1, safety_stock=0.8,
            new_stock=1, new_pending=0, daily_avg=1.0,
            order_unit_qty=1, promo_type="1+1"
        )
        assert result == 2

    def test_2plus1_need_2_becomes_3(self):
        """2+1 행사: need=1.5 -> 올림 2개 -> 행사보정 -> 3개"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=2, safety_stock=1.5,
            new_stock=1, new_pending=1, daily_avg=2.0,
            order_unit_qty=1, promo_type="2+1"
        )
        assert result == 3

    def test_need_zero_no_promo_adjustment(self):
        """need <= 0이면 행사보정 안함 (0 유지)"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=1, safety_stock=0.5,
            new_stock=3, new_pending=0, daily_avg=1.0,
            order_unit_qty=1, promo_type="1+1"
        )
        assert result == 0

    def test_no_promo_type_no_adjustment(self):
        """promo_type 없으면 보정 안함"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=2, safety_stock=1.0,
            new_stock=2, new_pending=0, daily_avg=2.0,
            order_unit_qty=1, promo_type=""
        )
        assert result == 1  # need=1 -> 1개 (보정 없음)

    def test_promo_already_above_unit(self):
        """1+1 행사지만 이미 promo_unit 이상이면 변경 없음"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=5, safety_stock=2.0,
            new_stock=1, new_pending=0, daily_avg=5.0,
            order_unit_qty=1, promo_type="1+1"
        )
        assert result == 6  # need=6 > promo_unit=2 -> 변경 없음

    def test_2plus1_with_order_unit_qty(self):
        """2+1 행사 + 발주배수(6): need=1 -> 올림 -> 발주배수6 -> 행사보정(이미 6>=3)"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=2, safety_stock=1.0,
            new_stock=1, new_pending=1, daily_avg=2.0,
            order_unit_qty=6, promo_type="2+1"
        )
        assert result == 6  # need=1 -> 배수올림 6 >= promo_unit=3

    def test_1plus1_with_order_unit_qty_1(self):
        """1+1 행사 + 발주배수(1): need=0.8 -> 1개 -> 행사보정 -> 2개"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=2, safety_stock=0.8,
            new_stock=1, new_pending=1, daily_avg=2.0,
            order_unit_qty=1, promo_type="1+1"
        )
        assert result == 2

    def test_backward_compatible_without_promo_type(self):
        """promo_type 파라미터 없이 호출 (기존 호환성)"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3, safety_stock=2.0,
            new_stock=1, new_pending=0, daily_avg=3.0
        )
        assert result == 4  # 기존과 동일


# ---------------------------------------------------------------------------
# recommendation dict에 promo_type 포함 테스트
# ---------------------------------------------------------------------------
class TestRecommendationPromoType:
    """_convert_prediction_result_to_dict에서 promo_type이 포함되는지 테스트"""

    def _make_system_with_cache(self, product_detail=None):
        """AutoOrderSystem + product_detail 캐시 세팅"""
        from src.order.auto_order import AutoOrderSystem
        sys_obj = object.__new__(AutoOrderSystem)
        sys_obj._product_detail_cache = {
            "TEST_ITEM": product_detail
        }
        sys_obj._product_repo = MagicMock()
        return sys_obj

    @dataclass
    class FakeResult:
        item_cd: str = "TEST_ITEM"
        item_nm: str = "테스트상품"
        mid_cd: str = "001"
        current_stock: int = 0
        pending_qty: int = 0
        adjusted_qty: float = 3.0
        predicted_qty: float = 2.5
        weekday_coef: float = 1.0
        safety_stock: float = 1.0
        order_qty: int = 3
        target_date: str = "2026-02-24"
        confidence: float = 0.8
        data_days: int = 30

    def test_promo_type_in_dict(self):
        """product_detail에 promo_type 있으면 dict에 포함"""
        s = self._make_system_with_cache(
            product_detail={"orderable_day": "1234567", "order_unit_qty": 1, "promo_type": "1+1"}
        )
        result = s._convert_prediction_result_to_dict(self.FakeResult())
        assert result["promo_type"] == "1+1"

    def test_no_promo_type_defaults_empty(self):
        """product_detail에 promo_type 없으면 빈 문자열"""
        s = self._make_system_with_cache(
            product_detail={"orderable_day": "1234567", "order_unit_qty": 1}
        )
        result = s._convert_prediction_result_to_dict(self.FakeResult())
        assert result["promo_type"] == ""

    def test_no_product_detail_defaults_empty(self):
        """product_detail 자체가 None이면 빈 문자열"""
        s = self._make_system_with_cache(product_detail=None)
        result = s._convert_prediction_result_to_dict(self.FakeResult())
        assert result["promo_type"] == ""

    def test_promo_type_2plus1(self):
        """2+1 행사 타입 포함"""
        s = self._make_system_with_cache(
            product_detail={"orderable_day": "1234567", "order_unit_qty": 1, "promo_type": "2+1"}
        )
        result = s._convert_prediction_result_to_dict(self.FakeResult())
        assert result["promo_type"] == "2+1"
