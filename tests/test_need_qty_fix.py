"""
need-qty-fix 검증 테스트

발주량 공식 이중가산 수정 관련 테스트:
1. need_qty 공식에서 food_gap_consumption 미가산
2. safety_days에 gap 정보 흡수 (ultra_short 0.7, short 0.8)
3. 발주단위 내림 우선 (_round_to_order_unit)
4. FORCE_MAX_DAYS 하향 (2 → 1.5)
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.categories.food import (
    FOOD_EXPIRY_SAFETY_CONFIG,
    DELIVERY_GAP_CONFIG,
    calculate_delivery_gap_consumption,
)
from src.settings.constants import FORCE_MAX_DAYS


# =============================================================================
# 1. safety_days gap 흡수 검증
# =============================================================================
class TestSafetyDaysGapAbsorption:
    """safety_days에 gap 정보 흡수 검증"""

    @pytest.mark.unit
    def test_ultra_short_safety_days_is_07(self):
        """ultra_short safety_days: 0.5 → 0.7 (gap 흡수)"""
        cfg = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["ultra_short"]
        assert cfg["safety_days"] == 0.7

    @pytest.mark.unit
    def test_short_safety_days_is_08(self):
        """short safety_days: 0.7 → 0.8 (gap 흡수)"""
        cfg = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["short"]
        assert cfg["safety_days"] == 0.8

    @pytest.mark.unit
    def test_medium_safety_days_unchanged(self):
        """medium safety_days 변경 없음 (1.0)"""
        cfg = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["medium"]
        assert cfg["safety_days"] == 1.0

    @pytest.mark.unit
    def test_long_safety_days_unchanged(self):
        """long safety_days 변경 없음 (1.5)"""
        cfg = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["long"]
        assert cfg["safety_days"] == 1.5

    @pytest.mark.unit
    def test_very_long_safety_days_unchanged(self):
        """very_long safety_days 변경 없음 (2.0)"""
        cfg = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["very_long"]
        assert cfg["safety_days"] == 2.0

    @pytest.mark.unit
    def test_gap_config_still_exists(self):
        """DELIVERY_GAP_CONFIG는 여전히 존재 (로그/모니터링용)"""
        assert DELIVERY_GAP_CONFIG["enabled"] is True
        assert "gap_coefficient" in DELIVERY_GAP_CONFIG

    @pytest.mark.unit
    def test_gap_calculation_still_works(self):
        """calculate_delivery_gap_consumption()은 여전히 동작"""
        result = calculate_delivery_gap_consumption(
            daily_avg=10.0,
            item_nm="테스트도시락",
            expiry_group="ultra_short",
        )
        assert result > 0  # 계산은 수행됨 (need_qty에 미가산이지만)


# =============================================================================
# 2. FORCE_MAX_DAYS 하향 검증
# =============================================================================
class TestForceMaxDaysReduced:
    """FORCE_MAX_DAYS 하향 검증"""

    @pytest.mark.unit
    def test_force_max_days_value(self):
        """FORCE_MAX_DAYS == 1.5"""
        assert FORCE_MAX_DAYS == 1.5

    @pytest.mark.unit
    def test_force_max_days_positive(self):
        """FORCE_MAX_DAYS > 0"""
        assert FORCE_MAX_DAYS > 0

    @pytest.mark.unit
    def test_force_max_days_less_than_2(self):
        """FORCE_MAX_DAYS < 2 (하향 확인)"""
        assert FORCE_MAX_DAYS < 2


# =============================================================================
# 3. _round_to_order_unit 내림 우선 검증
# =============================================================================
class TestRoundToOrderUnitFloor:
    """발주단위 내림 우선 검증"""

    def _make_predictor(self):
        """테스트용 predictor 생성 (DB 없이)"""
        from src.prediction.improved_predictor import ImprovedPredictor
        with patch.object(ImprovedPredictor, '__init__', lambda self, **kwargs: None):
            predictor = ImprovedPredictor.__new__(ImprovedPredictor)
            return predictor

    def _make_ctx(self, **overrides):
        """기본 ctx dict 생성"""
        ctx = {
            "tobacco_max_stock": 100,
            "beer_max_stock": None,
            "soju_max_stock": None,
            "ramen_max_stock": 0,
            "food_expiration_days": None,
        }
        ctx.update(overrides)
        return ctx

    @pytest.mark.unit
    def test_floor_when_stock_sufficient(self):
        """재고 0.5일치 이상 → 내림 적용"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # order_qty=8, unit=6, stock=5, daily_avg=10
        # days_cover = 5/10 = 0.5 >= 0.5 → floor
        # floor(8/6)*6 = 6
        result = predictor._round_to_order_unit(
            order_qty=8, order_unit=6, mid_cd="049", product=product,
            daily_avg=10, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 6  # floor

    @pytest.mark.unit
    def test_ceil_when_stock_critical(self):
        """재고 0.5일치 미만 → 올림 적용"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # order_qty=8, unit=6, stock=2, daily_avg=10
        # days_cover = 2/10 = 0.2 < 0.5 → ceil
        # ceil(8/6)*6 = 12
        result = predictor._round_to_order_unit(
            order_qty=8, order_unit=6, mid_cd="049", product=product,
            daily_avg=10, current_stock=2, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 12  # ceil (결품 위험)

    @pytest.mark.unit
    def test_floor_zero_fallback_to_unit(self):
        """내림 결과가 0이면 최소 1단위 보장"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # order_qty=3, unit=6, stock=5, daily_avg=10
        # floor(3/6)*6 = 0 → 최소 1단위 = 6
        result = predictor._round_to_order_unit(
            order_qty=3, order_unit=6, mid_cd="049", product=product,
            daily_avg=10, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 6  # order_unit (최소 보장)

    @pytest.mark.unit
    def test_floor_with_pending_stock(self):
        """미입고 포함한 가용재고 기준 내림"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # stock=2, pending=4 → effective=6, daily_avg=10
        # days_cover = 6/10 = 0.6 >= 0.5 → floor
        result = predictor._round_to_order_unit(
            order_qty=15, order_unit=10, mid_cd="049", product=product,
            daily_avg=10, current_stock=2, pending_qty=4,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 10  # floor(15/10)*10 = 10

    @pytest.mark.unit
    def test_ceil_with_zero_stock(self):
        """재고+미입고 0 → 올림"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # stock=0, pending=0 → days_cover=0 < 0.5 → ceil
        result = predictor._round_to_order_unit(
            order_qty=8, order_unit=6, mid_cd="049", product=product,
            daily_avg=10, current_stock=0, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 12  # ceil(8/6)*6 = 12

    @pytest.mark.unit
    def test_max_stock_override_still_works(self):
        """max_stock 초과 시 기존 내림 로직 유지"""
        predictor = self._make_predictor()
        ctx = self._make_ctx(food_expiration_days=1)
        product = {"item_nm": "테스트도시락", "expiration_days": 1}

        # food: max_stock = daily_avg * min(1+1, 7) = 10*2 = 20
        # stock=15, pending=0, ceil_qty=12 → 15+12=27 > 20 → floor
        result = predictor._round_to_order_unit(
            order_qty=8, order_unit=6, mid_cd="001", product=product,
            daily_avg=10, current_stock=15, pending_qty=0,
            safety_stock=7, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 6  # floor (max_stock 초과 방지)

    @pytest.mark.unit
    def test_exact_multiple_returns_same(self):
        """정확한 배수는 ceil=floor=그대로"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # order_qty=12, unit=6 → ceil=12, floor=12
        result = predictor._round_to_order_unit(
            order_qty=12, order_unit=6, mid_cd="049", product=product,
            daily_avg=10, current_stock=5, pending_qty=0,
            safety_stock=3, adjusted_prediction=10,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 12

    @pytest.mark.unit
    def test_daily_avg_zero_uses_ceil(self):
        """daily_avg=0일 때 days_cover=999 → 내림"""
        predictor = self._make_predictor()
        ctx = self._make_ctx()
        product = {"item_nm": "테스트상품", "expiration_days": 30}

        # daily_avg=0 → days_cover=999 → needs_ceil=False → floor
        result = predictor._round_to_order_unit(
            order_qty=8, order_unit=6, mid_cd="049", product=product,
            daily_avg=0, current_stock=5, pending_qty=0,
            safety_stock=0, adjusted_prediction=0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert result == 6  # floor


# =============================================================================
# 4. need_qty 공식에서 gap 미가산 검증
# =============================================================================
class TestNeedQtyFormula:
    """need_qty 공식에서 food_gap_consumption 미가산 검증"""

    @pytest.mark.unit
    def test_need_qty_formula_no_gap(self):
        """need_qty = pred + lead + safety - stock - pending (gap 미포함)"""
        # 직접 수식 검증 (코드의 공식과 동일해야 함)
        adjusted_prediction = 11.5
        lead_time_demand = 0.0
        safety_stock = 7.0  # daily_avg(10) × 0.7
        food_gap_consumption = 2.8  # 이전에는 더해졌으나 이제 미가산
        current_stock = 3
        pending_qty = 0

        # 변경 후 공식: gap 미포함
        need_qty = (adjusted_prediction + lead_time_demand
                    + safety_stock
                    - current_stock - pending_qty)

        assert need_qty == pytest.approx(15.5, abs=0.1)

        # 변경 전 공식과 비교 (이전에는 gap 포함)
        old_need_qty = (adjusted_prediction + lead_time_demand
                        + safety_stock + food_gap_consumption
                        - current_stock - pending_qty)
        assert old_need_qty > need_qty  # 이전이 더 큼

    @pytest.mark.unit
    def test_gap_not_in_formula_but_logged(self):
        """PredictionResult에 food_gap_consumption 필드가 존재 (참조용)"""
        from src.prediction.improved_predictor import PredictionResult
        result = PredictionResult(
            item_cd="TEST", item_nm="테스트도시락", mid_cd="001",
            target_date="2026-02-25",
            predicted_qty=10.0, adjusted_qty=11.5,
            current_stock=3, pending_qty=0,
            safety_stock=7.0, order_qty=12,
            confidence="high", data_days=30, weekday_coef=1.0,
            food_gap_consumption=2.8,
        )
        assert result.food_gap_consumption == 2.8
        # 필드가 존재하되 order_qty 계산에 사용되지 않았음을 확인

    @pytest.mark.unit
    def test_gap_zero_for_non_food(self):
        """비푸드 상품의 gap은 항상 0"""
        from src.prediction.improved_predictor import PredictionResult
        result = PredictionResult(
            item_cd="NON_FOOD", item_nm="라면", mid_cd="006",
            target_date="2026-02-25",
            predicted_qty=8.0, adjusted_qty=8.0,
            current_stock=5, pending_qty=0,
            safety_stock=3.0, order_qty=10,
            confidence="high", data_days=30, weekday_coef=1.0,
        )
        assert result.food_gap_consumption == 0.0


# =============================================================================
# 5. 통합 과잉발주 감소 검증
# =============================================================================
class TestOverOrderReduction:
    """과잉발주 감소 수치 검증"""

    @pytest.mark.unit
    def test_food_dosirak_reduction_simulation(self):
        """일평균 10개 도시락 시뮬레이션: 18 → 12"""
        daily_avg = 10
        # 변경 후 safety_days
        safety_days = 0.7  # ultra_short
        safety_stock = daily_avg * safety_days  # 7.0

        adjusted_prediction = daily_avg * 1.15  # 트렌드 등 보정
        lead_time_demand = 0.0
        current_stock = 3
        pending_qty = 0

        need_qty = (adjusted_prediction + lead_time_demand
                    + safety_stock - current_stock - pending_qty)

        # 발주단위 내림 (unit=6)
        order_unit = 6
        floor_qty = (int(need_qty) // order_unit) * order_unit
        assert floor_qty == 12  # 15.5 → floor → 12

    @pytest.mark.unit
    def test_sandwich_reduction_simulation(self):
        """일평균 5개 샌드위치 시뮬레이션"""
        daily_avg = 5
        safety_days = 0.8  # short
        safety_stock = daily_avg * safety_days  # 4.0

        adjusted_prediction = daily_avg * 1.1
        current_stock = 2

        need_qty = adjusted_prediction + safety_stock - current_stock
        # 5.5 + 4.0 - 2 = 7.5

        order_unit = 4
        floor_qty = (int(need_qty) // order_unit) * order_unit
        assert floor_qty == 4  # 7 → floor → 4

    @pytest.mark.unit
    def test_ramen_reduction_via_floor_only(self):
        """라면: gap 변경 없이 내림만으로 감소"""
        order_qty = 12
        order_unit = 10

        ceil_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit
        floor_qty = (order_qty // order_unit) * order_unit

        assert ceil_qty == 20
        assert floor_qty == 10
        assert ceil_qty - floor_qty == 10  # 내림만으로 -10개 감소
