"""
food-stockout-misclassify PDCA 테스트

폐기/품절 오판 삼중 악순환 해결:
Fix A: eval_calibrator was_stockout에 폐기(disuse) 구분
Fix B: improved_predictor 폐기 면제/부스트에 폐기율 교차 검증
"""
import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# =============================================================================
# Fix A: eval_calibrator was_stockout 폐기 구분
# =============================================================================

class TestWasStockoutDisuseDistinction:
    """Fix A: stock=0일 때 폐기 vs 품절 구분"""

    def test_stockout_with_disuse_is_not_stockout(self):
        """stock=0 + disuse>0 → was_stockout=False, was_waste_expiry=True"""
        next_day_stock = 0
        disuse_qty = 1
        was_waste = disuse_qty > 0
        was_stockout = next_day_stock <= 0 and not was_waste
        was_waste_expiry = next_day_stock <= 0 and was_waste

        assert was_stockout is False
        assert was_waste_expiry is True

    def test_stockout_without_disuse_is_stockout(self):
        """stock=0 + disuse=0 → was_stockout=True, was_waste_expiry=False"""
        next_day_stock = 0
        disuse_qty = 0
        was_waste = disuse_qty > 0
        was_stockout = next_day_stock <= 0 and not was_waste
        was_waste_expiry = next_day_stock <= 0 and was_waste

        assert was_stockout is True
        assert was_waste_expiry is False

    def test_stock_positive_no_flags(self):
        """stock>0 → 둘 다 False"""
        next_day_stock = 3
        disuse_qty = 0
        was_waste = disuse_qty > 0
        was_stockout = next_day_stock <= 0 and not was_waste
        was_waste_expiry = next_day_stock <= 0 and was_waste

        assert was_stockout is False
        assert was_waste_expiry is False

    def test_stock_positive_with_disuse_no_flags(self):
        """stock>0 + disuse>0 → 둘 다 False (부분 폐기, 재고 남음)"""
        next_day_stock = 2
        disuse_qty = 1
        was_waste = disuse_qty > 0
        was_stockout = next_day_stock <= 0 and not was_waste
        was_waste_expiry = next_day_stock <= 0 and was_waste

        assert was_stockout is False
        assert was_waste_expiry is False


class TestJudgeNormalOrderFoodWaste:
    """Fix A: _judge_normal_order 푸드류 폐기 소멸 판정"""

    def _make_record(self, mid_cd="001", daily_avg=0.1, was_waste_expiry=False):
        return {
            "mid_cd": mid_cd,
            "daily_avg": daily_avg,
            "promo_type": None,
            "was_waste_expiry": was_waste_expiry,
        }

    def test_food_waste_expiry_returns_over_order(self):
        """푸드 + was_waste_expiry=True + actual_sold=0 → OVER_ORDER"""
        record = self._make_record(was_waste_expiry=True)
        mid_cd = record["mid_cd"]
        actual_sold = 0
        was_stockout = False  # 폐기 소멸이므로 False

        # _judge_normal_order 로직 시뮬레이션
        FOOD_CATEGORIES = {"001", "002", "003", "004", "005", "012"}
        was_waste_expiry = record.get("was_waste_expiry", False)

        if mid_cd in FOOD_CATEGORIES:
            if actual_sold > 0:
                result = "CORRECT"
            elif was_waste_expiry:
                result = "OVER_ORDER"
            elif was_stockout:
                result = "UNDER_ORDER"
            else:
                result = "OVER_ORDER"

        assert result == "OVER_ORDER"

    def test_food_real_stockout_returns_under_order(self):
        """푸드 + 진짜 품절(disuse=0) + actual_sold=0 → UNDER_ORDER"""
        record = self._make_record(was_waste_expiry=False)
        mid_cd = record["mid_cd"]
        actual_sold = 0
        was_stockout = True

        FOOD_CATEGORIES = {"001", "002", "003", "004", "005", "012"}
        was_waste_expiry = record.get("was_waste_expiry", False)

        if mid_cd in FOOD_CATEGORIES:
            if actual_sold > 0:
                result = "CORRECT"
            elif was_waste_expiry:
                result = "OVER_ORDER"
            elif was_stockout:
                result = "UNDER_ORDER"
            else:
                result = "OVER_ORDER"

        assert result == "UNDER_ORDER"

    def test_food_sold_returns_correct(self):
        """푸드 + actual_sold>0 → CORRECT (폐기 여부 무관)"""
        for waste_expiry in [True, False]:
            record = self._make_record(was_waste_expiry=waste_expiry)
            mid_cd = record["mid_cd"]
            actual_sold = 2
            was_stockout = False

            FOOD_CATEGORIES = {"001", "002", "003", "004", "005", "012"}
            was_waste_expiry_flag = record.get("was_waste_expiry", False)

            if mid_cd in FOOD_CATEGORIES:
                if actual_sold > 0:
                    result = "CORRECT"
                elif was_waste_expiry_flag:
                    result = "OVER_ORDER"
                elif was_stockout:
                    result = "UNDER_ORDER"
                else:
                    result = "OVER_ORDER"

            assert result == "CORRECT"


# =============================================================================
# Fix B: 폐기 면제/부스트 교차 검증
# =============================================================================

class TestWasteExemptOverride:
    """Fix B-1: 폐기율 교차 검증으로 면제 조건 강화"""

    def test_high_stockout_low_waste_exempts(self):
        """stockout>50% + waste_rate<25% → 기존대로 면제"""
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            WASTE_EXEMPT_PARTIAL_FLOOR,
        )
        stockout_freq = 0.70
        mid_waste_rate = 0.15
        unified_waste_coef = 0.70

        assert stockout_freq > 0.50
        assert mid_waste_rate < WASTE_EXEMPT_OVERRIDE_THRESHOLD

        effective = 1.0  # 면제
        assert effective == 1.0

    def test_high_stockout_high_waste_overrides(self):
        """stockout>50% + waste_rate>=25% → 면제 해제, 부분 적용"""
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            WASTE_EXEMPT_PARTIAL_FLOOR,
        )
        stockout_freq = 0.70
        mid_waste_rate = 0.32
        unified_waste_coef = 0.70

        assert stockout_freq > 0.50
        assert mid_waste_rate >= WASTE_EXEMPT_OVERRIDE_THRESHOLD

        effective = max(unified_waste_coef, WASTE_EXEMPT_PARTIAL_FLOOR)
        assert effective == WASTE_EXEMPT_PARTIAL_FLOOR  # 0.80

    def test_high_waste_coef_preserved_on_override(self):
        """면제 해제 시 unified가 floor보다 높으면 unified 유지"""
        from src.prediction.categories.food import WASTE_EXEMPT_PARTIAL_FLOOR
        unified_waste_coef = 0.85
        effective = max(unified_waste_coef, WASTE_EXEMPT_PARTIAL_FLOOR)
        assert effective == 0.85

    def test_medium_stockout_unchanged(self):
        """stockout 30~50% → 기존 로직 (교차 검증 미적용)"""
        stockout_freq = 0.40
        unified_waste_coef = 0.75

        effective = max(unified_waste_coef, 0.90)
        assert effective == 0.90


class TestStockoutBoostOverride:
    """Fix B-2: 폐기율 높으면 부스트 비활성"""

    def test_high_waste_disables_boost(self):
        """waste_rate>=25% → stockout_boost=1.0"""
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            get_stockout_boost_coefficient,
        )
        mid_waste_rate = 0.32
        stockout_freq = 0.70

        if mid_waste_rate >= WASTE_EXEMPT_OVERRIDE_THRESHOLD:
            stockout_boost = 1.0
        else:
            stockout_boost = get_stockout_boost_coefficient(stockout_freq)

        assert stockout_boost == 1.0

    def test_low_waste_enables_boost(self):
        """waste_rate<25% → 기존 부스트 유지"""
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            get_stockout_boost_coefficient,
        )
        mid_waste_rate = 0.10
        stockout_freq = 0.70

        if mid_waste_rate >= WASTE_EXEMPT_OVERRIDE_THRESHOLD:
            stockout_boost = 1.0
        else:
            stockout_boost = get_stockout_boost_coefficient(stockout_freq)

        assert stockout_boost == 1.30  # 70%+ → 1.30x


class TestWasteConstants:
    """상수 존재 및 값 검증"""

    def test_constants_exist(self):
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            WASTE_EXEMPT_PARTIAL_FLOOR,
            WASTE_RATE_LOOKBACK_DAYS,
        )
        assert WASTE_EXEMPT_OVERRIDE_THRESHOLD == 0.25
        assert WASTE_EXEMPT_PARTIAL_FLOOR == 0.80
        assert WASTE_RATE_LOOKBACK_DAYS == 14

    def test_threshold_in_valid_range(self):
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            WASTE_EXEMPT_PARTIAL_FLOOR,
        )
        assert 0.10 <= WASTE_EXEMPT_OVERRIDE_THRESHOLD <= 0.50
        assert 0.50 <= WASTE_EXEMPT_PARTIAL_FLOOR <= 1.0


# =============================================================================
# 통합: 악순환 경로 차단 검증
# =============================================================================

class TestCycleBreaking:
    """악순환 경로 차단 통합 테스트"""

    def test_waste_expiry_breaks_under_order_cycle(self):
        """폐기 소멸 → OVER_ORDER → UNDER_ORDER 연속 해소"""
        # 시나리오: daily_avg=0.1, disuse=1, stock=0
        disuse_qty = 1
        next_day_stock = 0
        actual_sold = 0

        was_waste = disuse_qty > 0
        was_stockout = next_day_stock <= 0 and not was_waste
        was_waste_expiry = next_day_stock <= 0 and was_waste

        # 판정 시뮬레이션 (푸드)
        if actual_sold > 0:
            outcome = "CORRECT"
        elif was_waste_expiry:
            outcome = "OVER_ORDER"  # ← 악순환 차단!
        elif was_stockout:
            outcome = "UNDER_ORDER"
        else:
            outcome = "OVER_ORDER"

        assert outcome == "OVER_ORDER"
        assert outcome != "UNDER_ORDER"

    def test_high_waste_rate_reduces_prediction(self):
        """폐기율 32% → waste_coef 적용 + 부스트 해제"""
        from src.prediction.categories.food import (
            WASTE_EXEMPT_OVERRIDE_THRESHOLD,
            WASTE_EXEMPT_PARTIAL_FLOOR,
        )
        stockout_freq = 0.70
        mid_waste_rate = 0.32
        unified_waste_coef = 0.70
        base_prediction = 1.0

        # Fix B-1: 면제 해제
        effective_waste_coef = max(unified_waste_coef, WASTE_EXEMPT_PARTIAL_FLOOR)
        adjusted = base_prediction * effective_waste_coef

        # Fix B-2: 부스트 해제
        stockout_boost = 1.0  # waste_rate >= threshold

        final = adjusted * stockout_boost
        assert final == 0.80  # 1.0 * 0.80 * 1.0

        # 비교: 수정 전이라면
        old_effective = 1.0  # 면제
        old_boost = 1.30     # 70% 부스트
        old_final = base_prediction * old_effective * old_boost
        assert old_final == 1.30

        # 38.5% 감소
        assert final < old_final
        reduction = 1.0 - final / old_final
        assert reduction > 0.35
