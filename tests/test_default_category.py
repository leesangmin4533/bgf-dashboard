"""
일반 상품(default) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/default.py
- get_shelf_life_group(): 유통기한 그룹 분류
- get_weekday_coefficient(): 카테고리별 요일 계수
- get_safety_stock_days(): 안전재고 일수 계산
- analyze_default_pattern(): 종합 패턴 분석 결과
"""

import pytest

from src.prediction.categories.default import (
    get_shelf_life_group,
    get_weekday_coefficient,
    get_safety_stock_days,
    analyze_default_pattern,
    DefaultPatternResult,
    SHELF_LIFE_CONFIG,
    WEEKDAY_COEFFICIENTS,
    DEFAULT_WEEKDAY_COEFFICIENTS,
    SAFETY_STOCK_MULTIPLIER,
    CATEGORY_FIXED_SAFETY_DAYS,
)


# =============================================================================
# get_shelf_life_group 테스트
# =============================================================================
class TestGetShelfLifeGroup:
    """유통기한 그룹 분류 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected", [
        (1, "ultra_short"),
        (2, "ultra_short"),
        (3, "ultra_short"),
    ])
    def test_ultra_short_group(self, days, expected):
        """초단기 그룹 (1~3일): 도시락, 김밥, 샌드위치"""
        assert get_shelf_life_group(days) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected", [
        (4, "short"),
        (5, "short"),
        (7, "short"),
    ])
    def test_short_group(self, days, expected):
        """단기 그룹 (4~7일): 우유, 빵"""
        assert get_shelf_life_group(days) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected", [
        (8, "medium"),
        (15, "medium"),
        (30, "medium"),
    ])
    def test_medium_group(self, days, expected):
        """중기 그룹 (8~30일): 요구르트, 디저트"""
        assert get_shelf_life_group(days) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected", [
        (31, "long"),
        (60, "long"),
        (90, "long"),
    ])
    def test_long_group(self, days, expected):
        """장기 그룹 (31~90일): 과자, 음료"""
        assert get_shelf_life_group(days) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected", [
        (91, "ultra_long"),
        (180, "ultra_long"),
        (9999, "ultra_long"),
    ])
    def test_ultra_long_group(self, days, expected):
        """초장기 그룹 (91일+): 라면, 통조림"""
        assert get_shelf_life_group(days) == expected

    @pytest.mark.unit
    def test_none_input_returns_ultra_long(self):
        """유통기한 정보 없으면 ultra_long 반환"""
        assert get_shelf_life_group(None) == "ultra_long"

    @pytest.mark.unit
    @pytest.mark.parametrize("days,expected", [
        (3, "ultra_short"),   # ultra_short 상한 경계
        (4, "short"),         # short 하한 경계
        (7, "short"),         # short 상한 경계
        (8, "medium"),        # medium 하한 경계
        (30, "medium"),       # medium 상한 경계
        (31, "long"),         # long 하한 경계
        (90, "long"),         # long 상한 경계
        (91, "ultra_long"),   # ultra_long 하한 경계
    ])
    def test_boundary_values(self, days, expected):
        """경계값 테스트: 각 그룹의 최소/최대 경계"""
        assert get_shelf_life_group(days) == expected

    @pytest.mark.unit
    def test_out_of_range_returns_ultra_long(self):
        """범위 밖 값(예: 0 이하)은 ultra_long 반환"""
        assert get_shelf_life_group(0) == "ultra_long"
        assert get_shelf_life_group(-1) == "ultra_long"

    @pytest.mark.unit
    def test_very_large_value(self):
        """매우 큰 유통기한 값도 ultra_long 반환"""
        assert get_shelf_life_group(99999) == "ultra_long"


# =============================================================================
# get_weekday_coefficient 테스트
# =============================================================================
class TestGetWeekdayCoefficient:
    """카테고리별 요일 계수 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", range(7))
    def test_beer_category_049(self, weekday):
        """맥주(049) 카테고리의 요일별 계수 반환"""
        expected = WEEKDAY_COEFFICIENTS["049"][weekday]
        assert get_weekday_coefficient("049", weekday) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", range(7))
    def test_soju_category_050(self, weekday):
        """소주(050) 카테고리의 요일별 계수 반환"""
        expected = WEEKDAY_COEFFICIENTS["050"][weekday]
        assert get_weekday_coefficient("050", weekday) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", range(7))
    def test_unknown_category_uses_default(self, weekday):
        """매핑에 없는 카테고리는 기본 요일 계수 사용"""
        expected = DEFAULT_WEEKDAY_COEFFICIENTS[weekday]
        assert get_weekday_coefficient("999", weekday) == expected

    @pytest.mark.unit
    def test_beer_friday_is_highest(self):
        """맥주(049) 금요일(index=5) 계수가 가장 높음 확인"""
        # default.py의 049 계수: [일, 월, 화, 수, 목, 금, 토]
        # index 5 = 금요일 = 2.54
        friday_coef = get_weekday_coefficient("049", 5)
        assert friday_coef == 2.54
        for day in range(7):
            if day != 5:
                assert friday_coef >= get_weekday_coefficient("049", day)

    @pytest.mark.unit
    def test_all_known_categories_have_7_coefficients(self):
        """모든 등록 카테고리에 7일치 계수가 있는지 확인"""
        for mid_cd, coefs in WEEKDAY_COEFFICIENTS.items():
            assert len(coefs) == 7, f"카테고리 {mid_cd}의 계수 개수가 7이 아님: {len(coefs)}"

    @pytest.mark.unit
    def test_default_coefficients_have_7_values(self):
        """기본 요일 계수도 7개 값 확인"""
        assert len(DEFAULT_WEEKDAY_COEFFICIENTS) == 7

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", ["072", "016", "044", "060"])
    def test_various_known_categories(self, mid_cd):
        """다양한 등록 카테고리 요일 계수 조회 확인"""
        for weekday in range(7):
            result = get_weekday_coefficient(mid_cd, weekday)
            assert result == WEEKDAY_COEFFICIENTS[mid_cd][weekday]


# =============================================================================
# get_safety_stock_days 테스트
# =============================================================================
class TestGetSafetyStockDays:
    """안전재고 일수 계산 테스트"""

    @pytest.mark.unit
    def test_tobacco_fixed_safety_days(self):
        """담배(072)는 고정 안전재고 2.0일 반환"""
        result = get_safety_stock_days("072", daily_avg=10.0, expiration_days=None)
        assert result == CATEGORY_FIXED_SAFETY_DAYS["072"]
        assert result == 2.0

    @pytest.mark.unit
    def test_tobacco_ignores_turnover_and_expiration(self):
        """담배(072)는 회전율/유통기한 무시하고 고정값 반환"""
        result_high = get_safety_stock_days("072", daily_avg=100.0, expiration_days=1)
        result_low = get_safety_stock_days("072", daily_avg=0.1, expiration_days=365)
        assert result_high == result_low == 2.0

    @pytest.mark.unit
    @pytest.mark.parametrize("daily_avg,expected_multiplier_key", [
        (5.0, "high_turnover"),
        (10.0, "high_turnover"),
        (2.0, "medium_turnover"),
        (4.9, "medium_turnover"),
        (0.0, "low_turnover"),
        (1.9, "low_turnover"),
    ])
    def test_turnover_based_multiplier(self, daily_avg, expected_multiplier_key):
        """회전율 기반 배수 적용 확인 (비고정 카테고리)"""
        mid_cd = "016"  # 스낵 (고정 안전재고 아님)
        expiration_days = 60  # long 그룹 → safety_days = 1.5

        result = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        shelf_group = get_shelf_life_group(expiration_days)
        base_days = SHELF_LIFE_CONFIG[shelf_group]["safety_stock_days"]
        multiplier = SAFETY_STOCK_MULTIPLIER[expected_multiplier_key]["multiplier"]
        expected = base_days * multiplier
        assert result == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("expiration_days,shelf_group,base_days", [
        (1, "ultra_short", 0.3),
        (5, "short", 0.5),
        (15, "medium", 0.7),
        (60, "long", 1.0),
        (180, "ultra_long", 1.0),
        (None, "ultra_long", 1.0),
    ])
    def test_shelf_life_based_base_days(self, expiration_days, shelf_group, base_days):
        """유통기한 그룹별 기본 안전재고 일수 확인"""
        mid_cd = "016"  # 고정 카테고리 아님
        daily_avg = 1.0  # 저회전 (multiplier = 0.5)

        result = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        expected = base_days * SAFETY_STOCK_MULTIPLIER["low_turnover"]["multiplier"]
        assert result == expected


# =============================================================================
# analyze_default_pattern 테스트
# =============================================================================
class TestAnalyzeDefaultPattern:
    """일반 상품 종합 패턴 분석 결과 테스트"""

    @pytest.mark.unit
    def test_returns_dataclass(self):
        """DefaultPatternResult 데이터클래스 반환 확인"""
        result = analyze_default_pattern(
            item_cd="TEST001",
            mid_cd="016",
            daily_avg=3.0,
            expiration_days=60,
        )
        assert isinstance(result, DefaultPatternResult)

    @pytest.mark.unit
    def test_result_fields_populated(self):
        """모든 필드가 올바르게 채워지는지 확인"""
        result = analyze_default_pattern(
            item_cd="TEST001",
            mid_cd="016",
            daily_avg=3.0,
            expiration_days=60,
        )
        assert result.item_cd == "TEST001"
        assert result.mid_cd == "016"
        assert result.daily_avg == 3.0
        assert result.expiration_days == 60
        assert result.shelf_group == "long"  # 60일 → long
        assert result.turnover_multiplier == 1.0  # 3.0 → medium_turnover

    @pytest.mark.unit
    def test_safety_stock_calculation(self):
        """안전재고 수량 = 일평균 x 안전재고일수"""
        daily_avg = 5.0
        expiration_days = 60  # long → base 1.0
        mid_cd = "016"

        result = analyze_default_pattern("ITEM1", mid_cd, daily_avg, expiration_days)
        # high turnover (5.0 >= 5.0) → multiplier = 1.0
        # safety_days = 1.0 * 1.0 = 1.0
        # safety_stock = 5.0 * 1.0 = 5.0
        assert result.safety_days == 1.0
        assert result.safety_stock == 5.0
        assert result.turnover_multiplier == 1.0

    @pytest.mark.unit
    def test_none_expiration_defaults_to_ultra_long(self):
        """유통기한 None이면 ultra_long 그룹"""
        result = analyze_default_pattern("ITEM1", "016", 1.0)
        assert result.shelf_group == "ultra_long"
        assert result.expiration_days is None

    @pytest.mark.unit
    def test_low_turnover_result(self):
        """저회전 상품(일평균 < 2.0) 결과 확인"""
        result = analyze_default_pattern("ITEM1", "016", 0.5, 15)
        # medium group → base 0.7, low_turnover → multiplier 0.5
        assert result.shelf_group == "medium"
        assert result.turnover_multiplier == 0.5
        assert result.safety_days == 0.35
        assert result.safety_stock == round(0.5 * 0.35, 2)

    @pytest.mark.unit
    def test_values_are_rounded(self):
        """결과값이 소수점 2자리로 반올림되는지 확인"""
        result = analyze_default_pattern("ITEM1", "016", 3.333, 60)
        assert result.daily_avg == round(3.333, 2)
        # safety_days와 safety_stock도 round 적용
        assert result.safety_days == round(result.safety_days, 2)
        assert result.safety_stock == round(result.safety_stock, 2)

    @pytest.mark.unit
    def test_fixed_category_safety_days(self):
        """고정 카테고리(담배 072)의 안전재고 일수"""
        result = analyze_default_pattern("TOBACCO1", "072", 8.0, None)
        # 담배는 고정 2.0일, turnover는 high(8.0 >= 5.0) → 1.5
        assert result.safety_days == 2.0
        assert result.safety_stock == round(8.0 * 2.0, 2)
