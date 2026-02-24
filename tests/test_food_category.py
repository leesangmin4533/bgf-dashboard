"""
푸드류(food) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/food.py
- is_food_category(): 푸드류 카테고리 여부 확인
- get_food_expiry_group(): 유통기한별 그룹 분류 (food 버전)
- get_food_disuse_coefficient(): 폐기율 계수 반환
- FOOD_EXPIRY_FALLBACK 딕셔너리 검증
"""

import pytest

from src.prediction.categories.food import (
    is_food_category,
    get_food_expiry_group,
    get_food_disuse_coefficient,
    FOOD_CATEGORIES,
    FOOD_EXPIRY_SAFETY_CONFIG,
    FOOD_EXPIRY_FALLBACK,
    FOOD_DISUSE_COEFFICIENT,
)


# =============================================================================
# is_food_category 테스트
# =============================================================================
class TestIsFoodCategory:
    """푸드류 카테고리 판별 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", ["001", "002", "003", "004", "005", "012"])
    def test_all_food_codes(self, mid_cd):
        """모든 푸드류 코드(001~005, 012) True 반환"""
        assert is_food_category(mid_cd) is True

    @pytest.mark.unit
    def test_001_is_food(self):
        """'001' (도시락)은 푸드류"""
        assert is_food_category("001") is True

    @pytest.mark.unit
    def test_012_is_food(self):
        """'012' (빵)은 푸드류"""
        assert is_food_category("012") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "006", "007", "011", "013", "032", "049", "050", "072", "900",
        "", "1", "01", "0001",
    ])
    def test_non_food_categories(self, mid_cd):
        """푸드류가 아닌 카테고리는 False 반환"""
        assert is_food_category(mid_cd) is False

    @pytest.mark.unit
    def test_food_categories_list(self):
        """FOOD_CATEGORIES 상수에 정확한 코드 포함 확인"""
        expected = ['001', '002', '003', '004', '005', '012']
        assert FOOD_CATEGORIES == expected

    @pytest.mark.unit
    def test_food_categories_count(self):
        """푸드류 카테고리는 정확히 6개"""
        assert len(FOOD_CATEGORIES) == 6


# =============================================================================
# get_food_expiry_group 테스트 (food 버전의 shelf_life_group)
# =============================================================================
class TestGetFoodExpiryGroup:
    """푸드류 유통기한 그룹 분류 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (0, "ultra_short"),
        (1, "ultra_short"),
    ])
    def test_ultra_short_group(self, days, expected_group):
        """초단기 그룹 (0~1일): 당일 폐기 상품"""
        group_name, group_cfg = get_food_expiry_group(days)
        assert group_name == expected_group
        assert group_cfg["safety_days"] == 0.5

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (2, "short"),
        (3, "short"),
    ])
    def test_short_group(self, days, expected_group):
        """단기 그룹 (2~3일): 샌드위치, 일부 빵"""
        group_name, group_cfg = get_food_expiry_group(days)
        assert group_name == expected_group
        assert group_cfg["safety_days"] == 0.7

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (4, "medium"),
        (5, "medium"),
        (7, "medium"),
    ])
    def test_medium_group(self, days, expected_group):
        """중기 그룹 (4~7일): 디저트, 장기빵"""
        group_name, group_cfg = get_food_expiry_group(days)
        assert group_name == expected_group
        assert group_cfg["safety_days"] == 1.0

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (8, "long"),
        (15, "long"),
        (30, "long"),
    ])
    def test_long_group(self, days, expected_group):
        """장기 그룹 (8~30일): 일부 디저트"""
        group_name, group_cfg = get_food_expiry_group(days)
        assert group_name == expected_group
        assert group_cfg["safety_days"] == 1.5

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (31, "very_long"),
        (60, "very_long"),
        (9999, "very_long"),
    ])
    def test_very_long_group(self, days, expected_group):
        """초장기 그룹 (31일+)"""
        group_name, group_cfg = get_food_expiry_group(days)
        assert group_name == expected_group
        assert group_cfg["safety_days"] == 2.0

    @pytest.mark.unit
    def test_none_input_returns_medium(self):
        """유통기한 None이면 medium 그룹 반환 (food 특화)"""
        group_name, group_cfg = get_food_expiry_group(None)
        assert group_name == "medium"
        assert group_cfg["safety_days"] == 1.0

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected_group", [
        (1, "ultra_short"),   # ultra_short 상한 경계
        (2, "short"),         # short 하한 경계
        (3, "short"),         # short 상한 경계
        (4, "medium"),        # medium 하한 경계
        (7, "medium"),        # medium 상한 경계
        (8, "long"),          # long 하한 경계
        (30, "long"),         # long 상한 경계
        (31, "very_long"),    # very_long 하한 경계
    ])
    def test_boundary_values(self, days, expected_group):
        """경계값 테스트: 각 그룹의 최소/최대 경계"""
        group_name, _ = get_food_expiry_group(days)
        assert group_name == expected_group

    @pytest.mark.unit
    def test_returns_tuple(self):
        """반환값이 (그룹명, 그룹설정) 튜플인지 확인"""
        result = get_food_expiry_group(1)
        assert isinstance(result, tuple)
        assert len(result) == 2
        group_name, group_cfg = result
        assert isinstance(group_name, str)
        assert isinstance(group_cfg, dict)
        assert "safety_days" in group_cfg
        assert "min_days" in group_cfg
        assert "max_days" in group_cfg
        assert "description" in group_cfg

    @pytest.mark.unit
    def test_out_of_range_returns_very_long(self):
        """범위 밖 큰 값은 very_long 반환"""
        group_name, _ = get_food_expiry_group(99999)
        assert group_name == "very_long"

    @pytest.mark.unit
    def test_safety_days_increase_with_expiry(self):
        """유통기한이 길수록 안전재고 일수가 증가"""
        _, ultra_short_cfg = get_food_expiry_group(1)
        _, short_cfg = get_food_expiry_group(2)
        _, medium_cfg = get_food_expiry_group(5)
        _, long_cfg = get_food_expiry_group(10)
        _, very_long_cfg = get_food_expiry_group(50)

        assert ultra_short_cfg["safety_days"] < short_cfg["safety_days"]
        assert short_cfg["safety_days"] < medium_cfg["safety_days"]
        assert medium_cfg["safety_days"] < long_cfg["safety_days"]
        assert long_cfg["safety_days"] < very_long_cfg["safety_days"]


# =============================================================================
# get_food_disuse_coefficient 테스트
# =============================================================================
class TestGetFoodDisuseCoefficient:
    """폐기율 계수 반환 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("rate,expected", [
        (0.00, 1.0),    # 0%
        (0.03, 1.0),    # 3%
        (0.049, 1.0),   # 4.9%
        (0.05, 0.95),   # 5% (경계)
        (0.08, 0.95),   # 8%
        (0.10, 0.85),   # 10% (경계)
        (0.12, 0.85),   # 12%
        (0.15, 0.75),   # 15% (경계)
        (0.18, 0.75),   # 18%
        (0.20, 0.7),    # 20% (경계)
        (0.50, 0.7),    # 50%
        (0.99, 0.7),    # 99%
    ])
    def test_disuse_rate_coefficients(self, rate, expected):
        """폐기율 범위별 정확한 계수 반환"""
        assert get_food_disuse_coefficient(rate) == expected

    @pytest.mark.unit
    def test_none_rate_returns_1(self):
        """폐기율이 None이면 1.0 반환"""
        assert get_food_disuse_coefficient(None) == 1.0

    @pytest.mark.unit
    def test_coefficient_decreases_with_higher_rate(self):
        """폐기율이 높을수록 계수가 감소 (보수적 발주)"""
        rates = [0.01, 0.07, 0.12, 0.17, 0.25]
        coefs = [get_food_disuse_coefficient(r) for r in rates]
        for i in range(len(coefs) - 1):
            assert coefs[i] >= coefs[i + 1], (
                f"폐기율 {rates[i]}의 계수({coefs[i]})가 "
                f"{rates[i+1]}의 계수({coefs[i+1]})보다 작음"
            )


# =============================================================================
# FOOD_EXPIRY_FALLBACK 딕셔너리 테스트
# =============================================================================
class TestFoodExpiryFallback:
    """푸드류 기본 유통기한 Fallback 딕셔너리 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd,expected_days", [
        ("001", 1),  # 도시락
        ("002", 1),  # 주먹밥
        ("003", 1),  # 김밥
        ("004", 2),  # 샌드위치
        ("005", 3),  # 햄버거 (alert/config.py: shelf_life_default=3)
        ("012", 3),  # 빵
    ])
    def test_all_food_fallback_values(self, mid_cd, expected_days):
        """모든 푸드 카테고리의 Fallback 유통기한 확인"""
        assert FOOD_EXPIRY_FALLBACK[mid_cd] == expected_days

    @pytest.mark.unit
    def test_default_fallback_exists(self):
        """'default' 키가 존재하고 7일로 설정"""
        assert "default" in FOOD_EXPIRY_FALLBACK
        assert FOOD_EXPIRY_FALLBACK["default"] == 7

    @pytest.mark.unit
    def test_all_food_categories_have_fallback(self):
        """모든 FOOD_CATEGORIES에 대한 Fallback이 존재"""
        for mid_cd in FOOD_CATEGORIES:
            assert mid_cd in FOOD_EXPIRY_FALLBACK, (
                f"카테고리 {mid_cd}에 대한 Fallback 유통기한이 없음"
            )

    @pytest.mark.unit
    def test_fallback_values_are_positive_integers(self):
        """모든 Fallback 값이 양의 정수"""
        for key, value in FOOD_EXPIRY_FALLBACK.items():
            assert isinstance(value, int), f"{key}의 값이 정수가 아님: {type(value)}"
            assert value > 0, f"{key}의 값이 양수가 아님: {value}"

    @pytest.mark.unit
    def test_ultra_short_items_have_1_day(self):
        """당일 폐기 상품(도시락, 주먹밥, 김밥)은 1일"""
        ultra_short_codes = ["001", "002", "003"]
        for code in ultra_short_codes:
            assert FOOD_EXPIRY_FALLBACK[code] == 1

    @pytest.mark.unit
    def test_hamburger_has_3_days(self):
        """햄버거(005)는 3일 (alert/config.py: shelf_life_default=3, 74시간)"""
        assert FOOD_EXPIRY_FALLBACK["005"] == 3

    @pytest.mark.unit
    def test_sandwich_has_2_days(self):
        """샌드위치(004)는 2일"""
        assert FOOD_EXPIRY_FALLBACK["004"] == 2

    @pytest.mark.unit
    def test_bread_has_3_days(self):
        """빵(012)은 3일"""
        assert FOOD_EXPIRY_FALLBACK["012"] == 3


# =============================================================================
# FOOD_EXPIRY_SAFETY_CONFIG 구조 테스트
# =============================================================================
class TestFoodExpirySafetyConfig:
    """푸드류 유통기한 안전재고 설정 구조 확인"""

    @pytest.mark.unit
    def test_config_enabled(self):
        """동적 안전재고 기능 활성화 상태"""
        assert FOOD_EXPIRY_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_default_safety_days(self):
        """기본 안전재고 일수 1.0"""
        assert FOOD_EXPIRY_SAFETY_CONFIG["default_safety_days"] == 1.0

    @pytest.mark.unit
    def test_expiry_groups_exist(self):
        """5개 유통기한 그룹 모두 존재"""
        groups = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]
        expected_groups = ["ultra_short", "short", "medium", "long", "very_long"]
        for g in expected_groups:
            assert g in groups, f"그룹 '{g}'이 설정에 없음"

    @pytest.mark.unit
    def test_expiry_groups_have_required_keys(self):
        """각 그룹에 필수 키(min_days, max_days, safety_days, description) 존재"""
        groups = FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]
        required_keys = ["min_days", "max_days", "safety_days", "description"]
        for group_name, group_cfg in groups.items():
            for key in required_keys:
                assert key in group_cfg, (
                    f"그룹 '{group_name}'에 '{key}' 키가 없음"
                )
