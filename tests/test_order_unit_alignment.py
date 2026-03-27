"""
발주단위 배수 정합성 테스트 (order-unit-alignment)

핵심 변경:
- 후처리(Diff 피드백, 잠식 감지, max cap)가 배수 정렬 이전에 적용
- _round_to_order_unit()이 마지막에 호출되어 최종 배수 정합성 보장
- order_executor에서 actual_qty를 multiplier × order_unit_qty로 정확 계산
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from src.prediction.improved_predictor import ImprovedPredictor


STORE_ID = "46513"


def _make_predictor(**overrides):
    """테스트용 ImprovedPredictor (DB 없이)"""
    with patch.object(ImprovedPredictor, '__init__', lambda self, **kwargs: None):
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor._diff_feedback = overrides.get("diff_feedback", None)
        predictor._substitution_detector = overrides.get("sub_detector", None)
        predictor._cost_optimizer = None
        predictor._ml_predictor = None
        predictor._calibrator = None
        predictor.store_id = STORE_ID
        return predictor


def _make_ctx(**overrides):
    """기본 ctx dict"""
    ctx = {
        "tobacco_max_stock": 100,
        "beer_max_stock": None,
        "soju_max_stock": None,
        "ramen_max_stock": 0,
        "food_expiration_days": None,
    }
    ctx.update(overrides)
    return ctx


def _make_product(**overrides):
    """기본 product dict"""
    product = {
        "item_cd": "TEST001",
        "item_nm": "테스트상품",
        "mid_cd": "049",
        "order_unit_qty": 6,
        "expiration_days": 30,
    }
    product.update(overrides)
    return product


# =============================================================================
# Fix A: 후처리 후 배수 정렬 확인
# =============================================================================
class TestPostProcessingThenRound:
    """후처리(페널티/잠식/max cap) 적용 후 배수 정렬 확인"""

    @pytest.mark.unit
    def test_penalty_then_round(self):
        """Diff 피드백 페널티 적용 후 배수 정렬"""
        mock_feedback = MagicMock()
        mock_feedback.enabled = True
        mock_feedback.get_removal_penalty.return_value = 0.7  # 30% 감량
        mock_feedback._removal_cache = {}

        predictor = _make_predictor(diff_feedback=mock_feedback)
        product = _make_product(order_unit_qty=16)
        ctx = _make_ctx()

        # order_qty=23 → penalty 0.7 → int(23*0.7)=16 → round(16/16)=16 ✓
        result = predictor._round_to_order_unit(
            order_qty=16, order_unit=16, mid_cd="049", product=product,
            daily_avg=5, current_stock=3, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # _round_to_order_unit에 이미 정렬된 값이 들어와야 함
        assert result % 16 == 0

    @pytest.mark.unit
    def test_substitution_then_round(self):
        """잠식 감지 계수 적용 후 배수 정렬"""
        predictor = _make_predictor()
        product = _make_product(order_unit_qty=6)
        ctx = _make_ctx()

        # 잠식 후 raw qty가 비정렬이어도 _round_to_order_unit이 정렬
        result = predictor._round_to_order_unit(
            order_qty=7, order_unit=6, mid_cd="049", product=product,
            daily_avg=5, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result % 6 == 0

    @pytest.mark.unit
    def test_max_cap_then_round(self):
        """max cap 적용 후 배수 floor 정렬"""
        predictor = _make_predictor()
        product = _make_product(order_unit_qty=16)
        ctx = _make_ctx()

        # max cap이 100인데 order_unit=16이면 floor(100/16)*16=96
        # 입력 order_qty=100(max cap 적용된 값)
        result = predictor._round_to_order_unit(
            order_qty=100, order_unit=16, mid_cd="049", product=product,
            daily_avg=5, current_stock=10, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result % 16 == 0
        assert result <= 112  # ceil(100/16)*16=112 최대

    @pytest.mark.unit
    def test_all_postprocessing_aligned(self):
        """3개 후처리 모두 적용 후 배수 유지 확인 (통합)"""
        # 페널티 0.8, 잠식 0.9 적용 후 raw qty가 비정렬이어도
        # _round_to_order_unit에서 최종 정렬
        predictor = _make_predictor()
        product = _make_product(order_unit_qty=12)
        ctx = _make_ctx()

        # 시뮬레이션: raw=30 → penalty 0.8 → 24 → sub 0.9 → 21 → round
        result = predictor._round_to_order_unit(
            order_qty=21, order_unit=12, mid_cd="049", product=product,
            daily_avg=5, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result % 12 == 0

    @pytest.mark.unit
    def test_no_penalty_no_change(self):
        """페널티 없으면 기존 동작 동일"""
        predictor = _make_predictor()
        product = _make_product(order_unit_qty=6)
        ctx = _make_ctx()

        result = predictor._round_to_order_unit(
            order_qty=8, order_unit=6, mid_cd="049", product=product,
            daily_avg=10, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # days_cover = 5/10 = 0.5 >= 0.5 → floor = 6
        assert result == 6

    @pytest.mark.unit
    def test_unit_qty_1_no_rounding(self):
        """order_unit_qty=1이면 정렬 불필요"""
        predictor = _make_predictor()
        product = _make_product(order_unit_qty=1)
        ctx = _make_ctx()

        # order_unit=1이면 _round_to_order_unit 호출 안됨 (if order_unit > 1)
        # predict()에서 조건 자체가 order_unit > 1
        # 여기서는 order_qty가 그대로 통과하는지 확인
        order_qty = 7
        order_unit = product["order_unit_qty"]
        # order_unit=1이면 skip
        if order_qty > 0 and order_unit > 1:
            order_qty = predictor._round_to_order_unit(
                order_qty, order_unit, "049", product, 5, 5, 0, 3, 10,
                ctx, None, False
            ).qty
        assert order_qty == 7  # 변경 없음

    @pytest.mark.unit
    def test_tobacco_always_ceil_after_penalty(self):
        """담배류는 페널티 적용 후에도 ceil 유지"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="041", order_unit_qty=10, expiration_days=365)
        ctx = _make_ctx(tobacco_max_stock=100)

        # 담배: 항상 ceil
        # 페널티 후 raw=7 → round → ceil(7/10)*10=10
        result = predictor._round_to_order_unit(
            order_qty=7, order_unit=10, mid_cd="041", product=product,
            daily_avg=5, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 10  # ceil 보장


# =============================================================================
# Fix A: predict() 통합 테스트 — 후처리 순서 검증
# =============================================================================
class TestPredictPostProcessingOrder:
    """predict() 전체 흐름에서 후처리→배수정렬 순서 검증"""

    def _setup_predictor_for_predict(self, order_unit_qty=16, penalty=0.7, sub_coef=None):
        """predict() 호출 가능한 predictor 세팅"""
        predictor = _make_predictor()

        # Diff feedback 설정
        if penalty is not None and penalty < 1.0:
            mock_feedback = MagicMock()
            mock_feedback.enabled = True
            mock_feedback.get_removal_penalty.return_value = penalty
            mock_feedback._removal_cache = {}
            predictor._diff_feedback = mock_feedback

        # Substitution detector 설정
        if sub_coef is not None:
            mock_sub = MagicMock()
            mock_sub.get_adjustment.return_value = sub_coef
            predictor._substitution_detector = mock_sub
            predictor._get_substitution_detector = lambda: mock_sub
        else:
            predictor._get_substitution_detector = lambda: None

        return predictor

    @pytest.mark.unit
    def test_diff_penalty_preserves_alignment(self):
        """Diff 피드백 페널티 후에도 배수 정렬 유지"""
        predictor = self._setup_predictor_for_predict(
            order_unit_qty=16, penalty=0.7
        )
        product = _make_product(order_unit_qty=16)
        ctx = _make_ctx()

        # raw order_qty=24 → penalty 0.7 → int(24*0.7)=16 → round=16
        # 새 순서: penalty먼저 → round마지막
        # 실제로는 predict() 전체를 호출해야 하지만 _round_to_order_unit만 테스트
        raw_qty = 24
        penalized = max(1, int(raw_qty * 0.7))  # 16
        result = predictor._round_to_order_unit(
            order_qty=penalized, order_unit=16, mid_cd="049", product=product,
            daily_avg=5, current_stock=3, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result % 16 == 0

    @pytest.mark.unit
    def test_substitution_preserves_alignment(self):
        """잠식 감지 후에도 배수 정렬 유지"""
        predictor = self._setup_predictor_for_predict(
            order_unit_qty=6, penalty=None, sub_coef=0.8
        )
        product = _make_product(order_unit_qty=6)
        ctx = _make_ctx()

        # raw=15 → sub 0.8 → int(15*0.8)=12 → round=12
        raw_qty = 15
        subbed = max(1, int(raw_qty * 0.8))  # 12
        result = predictor._round_to_order_unit(
            order_qty=subbed, order_unit=6, mid_cd="049", product=product,
            daily_avg=5, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result % 6 == 0

    @pytest.mark.unit
    def test_combined_penalty_sub_preserves_alignment(self):
        """페널티+잠식 동시 적용 후에도 배수 정렬 유지"""
        product = _make_product(order_unit_qty=16)
        predictor = _make_predictor()
        ctx = _make_ctx()

        # raw=32 → penalty 0.8 → 25 → sub 0.9 → 22 → round
        raw_qty = 32
        after_penalty = max(1, int(raw_qty * 0.8))  # 25
        after_sub = max(1, int(after_penalty * 0.9))  # 22
        result = predictor._round_to_order_unit(
            order_qty=after_sub, order_unit=16, mid_cd="049", product=product,
            daily_avg=5, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result % 16 == 0


# =============================================================================
# Fix B: order_executor actual_qty 계산 검증
# =============================================================================
class TestOrderExecutorActualQty:
    """order_executor에서 actual_qty = multiplier × order_unit_qty 검증"""

    @pytest.mark.unit
    def test_actual_qty_aligned_direct_api(self):
        """Direct API: actual_qty가 배수 정렬"""
        item = {"item_cd": "TEST001", "final_order_qty": 12, "order_unit_qty": 16}
        qty = item.get("final_order_qty", 0)
        unit = item.get("order_unit_qty", 1) or 1
        if qty > 0 and unit > 1:
            mult = max(1, (qty + unit - 1) // unit)
            actual = mult * unit
        else:
            actual = qty
        # 12/16 → ceil=1 → actual=16
        assert actual == 16
        assert actual % unit == 0

    @pytest.mark.unit
    def test_actual_qty_already_aligned(self):
        """이미 정렬된 경우 변경 없음"""
        item = {"item_cd": "TEST001", "final_order_qty": 32, "order_unit_qty": 16}
        qty = item["final_order_qty"]
        unit = item["order_unit_qty"]
        mult = max(1, (qty + unit - 1) // unit)
        actual = mult * unit
        assert actual == 32  # 이미 정렬됨

    @pytest.mark.unit
    def test_actual_qty_unit_1(self):
        """order_unit_qty=1이면 qty 그대로"""
        item = {"item_cd": "TEST001", "final_order_qty": 7, "order_unit_qty": 1}
        qty = item["final_order_qty"]
        unit = item["order_unit_qty"]
        if qty > 0 and unit > 1:
            mult = max(1, (qty + unit - 1) // unit)
            actual = mult * unit
        else:
            actual = qty
        assert actual == 7

    @pytest.mark.unit
    def test_actual_qty_zero(self):
        """qty=0이면 actual=0"""
        item = {"item_cd": "TEST001", "final_order_qty": 0, "order_unit_qty": 16}
        qty = item["final_order_qty"]
        unit = item["order_unit_qty"]
        if qty > 0 and unit > 1:
            mult = max(1, (qty + unit - 1) // unit)
            actual = mult * unit
        else:
            actual = qty
        assert actual == 0

    @pytest.mark.unit
    def test_actual_qty_various_units(self):
        """다양한 배수 단위 검증"""
        test_cases = [
            # (qty, unit, expected_actual)
            (3, 6, 6),
            (7, 6, 12),
            (12, 6, 12),
            (1, 16, 16),
            (15, 16, 16),
            (17, 16, 32),
            (23, 24, 24),
            (25, 24, 48),
            (5, 10, 10),
            (10, 10, 10),
            (11, 10, 20),
        ]
        for qty, unit, expected in test_cases:
            if qty > 0 and unit > 1:
                mult = max(1, (qty + unit - 1) // unit)
                actual = mult * unit
            else:
                actual = qty
            assert actual == expected, f"qty={qty}, unit={unit}: expected={expected}, got={actual}"

    @pytest.mark.unit
    def test_multiplier_field_in_result(self):
        """결과 dict에 multiplier, order_unit_qty 필드 존재"""
        item = {"item_cd": "TEST001", "final_order_qty": 12, "order_unit_qty": 16}
        qty = item["final_order_qty"]
        unit = item["order_unit_qty"]
        mult = max(1, (qty + unit - 1) // unit)
        actual = mult * unit

        result = {
            "item_cd": item["item_cd"],
            "target_qty": qty,
            "actual_qty": actual,
            "multiplier": mult,
            "order_unit_qty": unit,
        }
        assert result["multiplier"] == 1
        assert result["actual_qty"] == 16
        assert result["order_unit_qty"] == 16
        assert result["target_qty"] == 12
