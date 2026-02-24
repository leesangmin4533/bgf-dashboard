"""
디저트(014) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/dessert.py
- is_dessert_category(): 디저트 카테고리 여부 확인
- 유통기한 그룹 분류 (_get_dessert_expiry_group)
- 회전율별 조정배수 (_get_turnover_level)
- 재고 상한선 계산 (_calculate_max_stock_days)
- skip_order 로직
- get_safety_stock_with_dessert_pattern(): 비대상 fallback
- snack_confection에서 014 제외 확인
"""

import pytest

from src.prediction.categories.dessert import (
    is_dessert_category,
    DessertPatternResult,
    DESSERT_TARGET_CATEGORIES,
    DESSERT_EXPIRY_SAFETY_CONFIG,
    DESSERT_TURNOVER_ADJUST,
    DEFAULT_DESSERT_WEEKDAY_COEF,
    _get_dessert_expiry_group,
    _get_turnover_level,
    _calculate_max_stock_days,
    get_safety_stock_with_dessert_pattern,
)

from src.prediction.categories.snack_confection import (
    is_snack_confection_category,
    SNACK_CONFECTION_TARGET_CATEGORIES,
)


class TestIsDessertCategory:
    """디저트 카테고리 판별 테스트"""

    def test_014_returns_true(self):
        """014는 디저트 카테고리"""
        assert is_dessert_category("014") is True

    @pytest.mark.parametrize("mid_cd", ["015", "016", "017", "018", "019", "020", "029", "030"])
    def test_snack_categories_return_false(self, mid_cd):
        """과자/간식 카테고리는 False"""
        assert is_dessert_category(mid_cd) is False

    def test_empty_returns_false(self):
        """빈 문자열은 False"""
        assert is_dessert_category("") is False

    @pytest.mark.parametrize("mid_cd", ["001", "049", "072", "900"])
    def test_other_categories_return_false(self, mid_cd):
        """다른 카테고리도 False"""
        assert is_dessert_category(mid_cd) is False


class TestGetDessertExpiryGroup:
    """유통기한 그룹 분류 테스트"""

    def test_short_group_8days(self):
        """8일 → short, 0.3일"""
        group, cfg = _get_dessert_expiry_group(8)
        assert group == "short"
        assert cfg["safety_days"] == 0.3

    def test_short_group_15days(self):
        """15일 → short, 0.3일"""
        group, cfg = _get_dessert_expiry_group(15)
        assert group == "short"
        assert cfg["safety_days"] == 0.3

    def test_medium_group_16days(self):
        """16일 → medium, 0.5일"""
        group, cfg = _get_dessert_expiry_group(16)
        assert group == "medium"
        assert cfg["safety_days"] == 0.5

    def test_medium_group_30days(self):
        """30일 → medium, 0.5일"""
        group, cfg = _get_dessert_expiry_group(30)
        assert group == "medium"
        assert cfg["safety_days"] == 0.5

    def test_long_group_31days(self):
        """31일 → long, 0.7일"""
        group, cfg = _get_dessert_expiry_group(31)
        assert group == "long"
        assert cfg["safety_days"] == 0.7

    def test_long_group_90days(self):
        """90일 → long, 0.7일"""
        group, cfg = _get_dessert_expiry_group(90)
        assert group == "long"
        assert cfg["safety_days"] == 0.7

    def test_1day_is_short(self):
        """1일 → short (min_days=0)"""
        group, _ = _get_dessert_expiry_group(1)
        assert group == "short"


class TestGetTurnoverLevel:
    """회전율 레벨 테스트"""

    def test_high_turnover(self):
        """일평균 5.0 → high, 1.0"""
        level, adjust = _get_turnover_level(5.0)
        assert level == "high"
        assert adjust == 1.0

    def test_high_turnover_10(self):
        """일평균 10.0 → high, 1.0"""
        level, adjust = _get_turnover_level(10.0)
        assert level == "high"
        assert adjust == 1.0

    def test_medium_turnover(self):
        """일평균 3.0 → medium, 0.9"""
        level, adjust = _get_turnover_level(3.0)
        assert level == "medium"
        assert adjust == 0.9

    def test_medium_turnover_boundary(self):
        """일평균 2.0 → medium, 0.9"""
        level, adjust = _get_turnover_level(2.0)
        assert level == "medium"
        assert adjust == 0.9

    def test_low_turnover(self):
        """일평균 1.0 → low, 0.7"""
        level, adjust = _get_turnover_level(1.0)
        assert level == "low"
        assert adjust == 0.7

    def test_zero_turnover(self):
        """일평균 0 → low, 0.7"""
        level, adjust = _get_turnover_level(0.0)
        assert level == "low"
        assert adjust == 0.7


class TestCalculateMaxStockDays:
    """재고 상한 일수 계산 테스트"""

    def test_short_expiry_10days(self):
        """short, 유통기한 10일 → 9일"""
        assert _calculate_max_stock_days("short", 10) == 9

    def test_short_expiry_2days(self):
        """short, 유통기한 2일 → max(1, 2) = 2"""
        assert _calculate_max_stock_days("short", 2) == 2

    def test_medium_expiry_20days(self):
        """medium, 유통기한 20일 → min(19, 5) = 5"""
        assert _calculate_max_stock_days("medium", 20) == 5

    def test_medium_expiry_4days(self):
        """medium, 유통기한 4일 → min(3, 5) = 3"""
        assert _calculate_max_stock_days("medium", 4) == 3

    def test_long_always_5(self):
        """long → 항상 5"""
        assert _calculate_max_stock_days("long", 60) == 5.0
        assert _calculate_max_stock_days("long", 100) == 5.0


class TestGetSafetyStockWithDessertPattern:
    """통합 인터페이스 테스트"""

    def test_non_dessert_returns_none_pattern(self):
        """디저트가 아닌 카테고리 → pattern=None"""
        safety, pattern = get_safety_stock_with_dessert_pattern(
            mid_cd="015", daily_avg=3.0, expiration_days=30
        )
        assert pattern is None
        assert safety >= 0

    def test_dessert_without_item_cd_uses_default(self):
        """상품코드 없이 호출 → 기본 안전재고일수 적용"""
        safety, pattern = get_safety_stock_with_dessert_pattern(
            mid_cd="014", daily_avg=5.0, expiration_days=14
        )
        # item_cd가 None이면 기본값: daily_avg * default_safety_days(0.5)
        assert pattern is None
        assert safety == pytest.approx(5.0 * 0.5, abs=0.01)


class TestSnackConfectionExcludes014:
    """snack_confection에서 014 제외 확인"""

    def test_014_not_in_snack_target(self):
        """014는 snack_confection 대상에서 제외"""
        assert "014" not in SNACK_CONFECTION_TARGET_CATEGORIES

    def test_014_not_snack_category(self):
        """is_snack_confection_category("014") == False"""
        assert is_snack_confection_category("014") is False

    @pytest.mark.parametrize("mid_cd", ["015", "016", "017", "018", "019", "020", "029", "030"])
    def test_other_snack_still_included(self, mid_cd):
        """나머지 과자/간식 카테고리는 여전히 True"""
        assert is_snack_confection_category(mid_cd) is True


class TestConstants:
    """상수 검증 테스트"""

    def test_target_categories_only_014(self):
        """디저트 대상 카테고리는 014만"""
        assert DESSERT_TARGET_CATEGORIES == ["014"]

    def test_expiry_groups_exist(self):
        """short/medium/long 3개 그룹 존재"""
        groups = DESSERT_EXPIRY_SAFETY_CONFIG["expiry_groups"]
        assert "short" in groups
        assert "medium" in groups
        assert "long" in groups

    def test_safety_days_ordering(self):
        """안전재고일수: short < medium < long"""
        groups = DESSERT_EXPIRY_SAFETY_CONFIG["expiry_groups"]
        assert groups["short"]["safety_days"] < groups["medium"]["safety_days"]
        assert groups["medium"]["safety_days"] < groups["long"]["safety_days"]

    def test_turnover_adjust_ordering(self):
        """조정배수: low < medium < high"""
        assert DESSERT_TURNOVER_ADJUST["low_turnover"]["adjust"] < \
               DESSERT_TURNOVER_ADJUST["medium_turnover"]["adjust"]
        assert DESSERT_TURNOVER_ADJUST["medium_turnover"]["adjust"] < \
               DESSERT_TURNOVER_ADJUST["high_turnover"]["adjust"]

    def test_weekday_coef_has_7_days(self):
        """기본 요일계수 7일 존재"""
        assert len(DEFAULT_DESSERT_WEEKDAY_COEF) == 7

    def test_fallback_expiry_days(self):
        """기본 유통기한 fallback = 14일"""
        assert DESSERT_EXPIRY_SAFETY_CONFIG["fallback_expiry_days"] == 14
