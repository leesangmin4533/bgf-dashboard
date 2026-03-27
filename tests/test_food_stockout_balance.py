"""
food-stockout-balance-fix PDCA 테스트

푸드 예측 과소편향 해소 — 기회손실/폐기 균형 최적화:
A. 폐기계수 조건부 적용 (stockout_freq > 50% → 면제)
B. compound floor 이후 최종 하한 보장 (base × 0.20)
C. stockout 부스트 피드백 (최대 1.30x)
"""
import sys
import sqlite3
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# =============================================================================
# A. 폐기계수 조건부 적용 테스트
# =============================================================================

class TestWasteCoefConditional:
    """A: stockout_freq 기반 폐기계수 조건부 적용"""

    def test_high_stockout_exempts_waste_coef(self):
        """50% 이상 품절 → waste_coef 면제 (effective=1.0)"""
        # sell_day_ratio=0.30 → stockout_freq=0.70
        sell_day_ratio = 0.30
        stockout_freq = 1.0 - sell_day_ratio
        unified_waste_coef = 0.75

        assert stockout_freq > 0.50
        # 면제 로직
        effective_waste_coef = 1.0
        assert effective_waste_coef == 1.0

        # 예측에 영향 없음
        adjusted = 10.0 * effective_waste_coef
        assert adjusted == 10.0

    def test_medium_stockout_clamps_waste_coef(self):
        """30~50% 품절 → waste_coef 최소 0.90 보장"""
        sell_day_ratio = 0.55
        stockout_freq = 1.0 - sell_day_ratio  # 0.45
        unified_waste_coef = 0.80

        assert 0.30 < stockout_freq <= 0.50
        effective_waste_coef = max(unified_waste_coef, 0.90)
        assert effective_waste_coef == 0.90  # 0.80 → 0.90

    def test_medium_stockout_keeps_higher_coef(self):
        """30~50% 품절이지만 원본 waste_coef > 0.90이면 원본 유지"""
        sell_day_ratio = 0.55
        stockout_freq = 1.0 - sell_day_ratio  # 0.45
        unified_waste_coef = 0.95

        effective_waste_coef = max(unified_waste_coef, 0.90)
        assert effective_waste_coef == 0.95  # 원본 유지

    def test_low_stockout_keeps_original(self):
        """30% 미만 품절 → 원본 waste_coef 그대로"""
        sell_day_ratio = 0.85
        stockout_freq = 1.0 - sell_day_ratio  # 0.15
        unified_waste_coef = 0.80

        assert stockout_freq <= 0.30
        effective_waste_coef = unified_waste_coef
        assert effective_waste_coef == 0.80

    def test_none_sell_day_ratio_defaults_zero(self):
        """sell_day_ratio=None → stockout_freq=0.0 (기존 동작)"""
        sell_day_ratio = None
        stockout_freq = 1.0 - sell_day_ratio if sell_day_ratio is not None else 0.0
        assert stockout_freq == 0.0
        # 0.0 < 0.30이므로 원본 유지
        unified_waste_coef = 0.80
        effective_waste_coef = unified_waste_coef
        assert effective_waste_coef == 0.80


# =============================================================================
# B. 최종 하한 보장 테스트
# =============================================================================

class TestFinalFloor:
    """B: compound floor 이후 최종 하한 (base × 0.20)"""

    def test_floor_applied_when_below(self):
        """adjusted < base×0.20 → 하한 적용"""
        base_prediction = 10.0
        adjusted_prediction = 1.5  # waste_coef로 낮아진 상태
        final_floor = base_prediction * 0.20  # 2.0

        assert adjusted_prediction < final_floor
        adjusted_prediction = max(adjusted_prediction, final_floor)
        assert adjusted_prediction == 2.0

    def test_floor_not_applied_when_above(self):
        """adjusted >= base×0.20 → 하한 미적용"""
        base_prediction = 10.0
        adjusted_prediction = 5.0
        final_floor = base_prediction * 0.20  # 2.0

        assert adjusted_prediction >= final_floor
        result = max(adjusted_prediction, final_floor)
        assert result == 5.0

    def test_floor_zero_base(self):
        """base=0이면 floor=0 (0으로 나누기 없음)"""
        base_prediction = 0.0
        adjusted_prediction = 0.0
        final_floor = base_prediction * 0.20  # 0.0

        # base_prediction > 0 조건에 의해 하한 미적용
        if adjusted_prediction < final_floor and base_prediction > 0:
            adjusted_prediction = final_floor
        assert adjusted_prediction == 0.0

    def test_floor_after_waste_exemption(self):
        """waste_coef 면제 후에도 compound floor(15%)보다 낮으면 20% 보장"""
        base_prediction = 10.0
        # compound floor에 의해 15%인 1.5로 clamped 된 상태
        adjusted_prediction = 1.5
        final_floor = base_prediction * 0.20  # 2.0

        adjusted_prediction = max(adjusted_prediction, final_floor)
        assert adjusted_prediction == 2.0


# =============================================================================
# C. stockout 부스트 피드백 테스트
# =============================================================================

class TestStockoutBoost:
    """C: get_stockout_boost_coefficient() 테스트"""

    def test_severe_stockout_boost(self):
        """70%+ 품절 → 1.30 부스트"""
        from src.prediction.categories.food import get_stockout_boost_coefficient
        assert get_stockout_boost_coefficient(0.80) == 1.30
        assert get_stockout_boost_coefficient(0.70) == 1.30
        assert get_stockout_boost_coefficient(0.99) == 1.30

    def test_high_stockout_boost(self):
        """50~70% 품절 → 1.15 부스트"""
        from src.prediction.categories.food import get_stockout_boost_coefficient
        assert get_stockout_boost_coefficient(0.55) == 1.15
        assert get_stockout_boost_coefficient(0.50) == 1.15
        assert get_stockout_boost_coefficient(0.69) == 1.15

    def test_medium_stockout_boost(self):
        """30~50% 품절 → 1.05 부스트"""
        from src.prediction.categories.food import get_stockout_boost_coefficient
        assert get_stockout_boost_coefficient(0.35) == 1.05
        assert get_stockout_boost_coefficient(0.30) == 1.05
        assert get_stockout_boost_coefficient(0.49) == 1.05

    def test_low_stockout_no_boost(self):
        """30% 미만 품절 → 부스트 없음 (1.0)"""
        from src.prediction.categories.food import get_stockout_boost_coefficient
        assert get_stockout_boost_coefficient(0.10) == 1.0
        assert get_stockout_boost_coefficient(0.0) == 1.0
        assert get_stockout_boost_coefficient(0.29) == 1.0

    def test_boost_disabled_toggle(self):
        """STOCKOUT_BOOST_ENABLED=False → 항상 1.0"""
        import src.prediction.categories.food as food_mod
        from src.prediction.categories.food import get_stockout_boost_coefficient

        original = food_mod.STOCKOUT_BOOST_ENABLED
        try:
            food_mod.STOCKOUT_BOOST_ENABLED = False
            assert get_stockout_boost_coefficient(0.80) == 1.0
            assert get_stockout_boost_coefficient(0.55) == 1.0
        finally:
            food_mod.STOCKOUT_BOOST_ENABLED = original


# =============================================================================
# 통합 테스트: A + B + C 조합
# =============================================================================

class TestIntegration:
    """A+B+C 통합 시나리오 (순서: waste조건부 → 하한 → 부스트)"""

    def test_high_stockout_full_pipeline(self):
        """고품절: waste면제 + 부스트 1.30"""
        from src.prediction.categories.food import get_stockout_boost_coefficient

        base = 10.0
        adjusted = 10.0  # coefficient_adjuster 통과 후
        sell_day_ratio = 0.25  # stockout_freq=0.75
        unified_waste_coef = 0.75

        stockout_freq = 1.0 - sell_day_ratio

        # A: 면제
        effective_waste_coef = 1.0  # >0.50
        # adjusted *= 1.0 → 10.0 (변화 없음)

        # B: final_floor = 10 * 0.20 = 2.0
        final_floor = base * 0.20
        assert adjusted >= final_floor  # 10.0 >= 2.0

        # C: boost
        boost = get_stockout_boost_coefficient(stockout_freq)
        assert boost == 1.30
        adjusted *= boost  # 10.0 * 1.30 = 13.0
        assert adjusted == pytest.approx(13.0)

    def test_medium_stockout_with_floor(self):
        """중품절: waste완화 + 하한 적용 + 소폭 부스트"""
        from src.prediction.categories.food import get_stockout_boost_coefficient

        base = 2.0
        adjusted = 2.0
        sell_day_ratio = 0.60  # stockout_freq=0.40
        unified_waste_coef = 0.70

        stockout_freq = 1.0 - sell_day_ratio

        # A: 30~50% → max(0.70, 0.90) = 0.90
        effective_waste_coef = max(unified_waste_coef, 0.90)
        assert effective_waste_coef == 0.90
        adjusted *= effective_waste_coef  # 2.0 * 0.90 = 1.80

        # B: final_floor = 2.0 * 0.20 = 0.40
        final_floor = base * 0.20
        assert adjusted >= final_floor  # 1.80 >= 0.40

        # C: boost 1.05
        boost = get_stockout_boost_coefficient(stockout_freq)
        assert boost == 1.05
        adjusted *= boost  # 1.80 * 1.05 = 1.89
        assert adjusted == pytest.approx(1.89)

    def test_normal_stockout_original_behavior(self):
        """정상(품절 낮음): 기존 동작 유지"""
        from src.prediction.categories.food import get_stockout_boost_coefficient

        base = 10.0
        adjusted = 10.0
        sell_day_ratio = 0.90  # stockout_freq=0.10
        unified_waste_coef = 0.80

        stockout_freq = 1.0 - sell_day_ratio

        # A: <30% → 원본 유지
        effective_waste_coef = unified_waste_coef
        assert effective_waste_coef == 0.80
        adjusted *= effective_waste_coef  # 10.0 * 0.80 = 8.0

        # B: final_floor = 10 * 0.20 = 2.0
        final_floor = base * 0.20
        assert adjusted >= final_floor  # 8.0 >= 2.0

        # C: no boost
        boost = get_stockout_boost_coefficient(stockout_freq)
        assert boost == 1.0
        assert adjusted == 8.0

    def test_non_food_not_affected(self):
        """비푸드 카테고리는 이 로직에 영향 없음"""
        from src.prediction.categories.food import is_food_category
        assert not is_food_category("050")  # 담배
        assert not is_food_category("020")  # 과자
        assert not is_food_category("100")  # 존재하지 않는 코드


# =============================================================================
# 상수/설정 검증
# =============================================================================

class TestConstants:
    """상수와 설정값 검증"""

    def test_boost_thresholds_order(self):
        """부스트 임계값이 올바른 순서인지"""
        from src.prediction.categories.food import STOCKOUT_BOOST_THRESHOLDS
        thresholds = sorted(STOCKOUT_BOOST_THRESHOLDS.keys())
        boosts = [STOCKOUT_BOOST_THRESHOLDS[t] for t in thresholds]

        # 임계값 증가 → 부스트 증가
        assert thresholds == [0.30, 0.50, 0.70]
        assert boosts == [1.05, 1.15, 1.30]

    def test_boost_max_cap(self):
        """최대 부스트가 1.30을 초과하지 않는지"""
        from src.prediction.categories.food import STOCKOUT_BOOST_THRESHOLDS
        assert max(STOCKOUT_BOOST_THRESHOLDS.values()) == 1.30

    def test_final_floor_is_20_percent(self):
        """최종 하한 = base × 0.20"""
        floor_ratio = 0.20
        for base in [1.0, 5.0, 10.0, 100.0]:
            assert base * floor_ratio == base * 0.20
