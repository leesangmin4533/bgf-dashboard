"""
맥주(beer) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/beer.py
- is_beer_category(): 맥주 카테고리 여부 확인
- get_beer_weekday_coef(): 요일별 계수 반환
- get_beer_safety_days(): 발주 요일 기준 안전재고 일수
"""

import pytest

from src.prediction.categories.beer import (
    is_beer_category,
    get_beer_weekday_coef,
    get_beer_safety_days,
    BEER_CATEGORIES,
    BEER_WEEKDAY_COEF,
    BEER_SAFETY_CONFIG,
)


# =============================================================================
# is_beer_category 테스트
# =============================================================================
class TestIsBeerCategory:
    """맥주 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_049_is_beer(self):
        """'049'는 맥주 카테고리"""
        assert is_beer_category("049") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "050", "072", "073", "001", "032", "044", "999", "", "49", "0049",
    ])
    def test_non_beer_categories(self, mid_cd):
        """맥주가 아닌 카테고리는 False 반환"""
        assert is_beer_category(mid_cd) is False

    @pytest.mark.unit
    def test_beer_categories_list_contains_049(self):
        """BEER_CATEGORIES 상수에 '049' 포함 확인"""
        assert "049" in BEER_CATEGORIES


# =============================================================================
# get_beer_weekday_coef 테스트
# =============================================================================
class TestGetBeerWeekdayCoef:
    """맥주 요일별 계수 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday,expected", [
        (0, 1.15),  # 월
        (1, 1.22),  # 화
        (2, 1.21),  # 수
        (3, 1.37),  # 목
        (4, 2.54),  # 금
        (5, 2.37),  # 토
        (6, 0.97),  # 일
    ])
    def test_all_weekdays(self, weekday, expected):
        """모든 요일(0~6)의 계수가 정확히 반환되는지 확인"""
        assert get_beer_weekday_coef(weekday) == expected

    @pytest.mark.unit
    def test_friday_is_highest(self):
        """금요일(4) 계수가 모든 요일 중 최고값"""
        friday_coef = get_beer_weekday_coef(4)
        assert friday_coef == 2.54
        for day in range(7):
            assert friday_coef >= get_beer_weekday_coef(day)

    @pytest.mark.unit
    def test_saturday_is_second_highest(self):
        """토요일(5) 계수가 금요일 다음으로 높음"""
        saturday_coef = get_beer_weekday_coef(5)
        assert saturday_coef == 2.37
        non_friday_coefs = [get_beer_weekday_coef(d) for d in range(7) if d != 4]
        assert saturday_coef == max(non_friday_coefs)

    @pytest.mark.unit
    def test_sunday_is_lowest(self):
        """일요일(6) 계수가 모든 요일 중 최저값"""
        sunday_coef = get_beer_weekday_coef(6)
        assert sunday_coef == 0.97
        for day in range(7):
            assert sunday_coef <= get_beer_weekday_coef(day)

    @pytest.mark.unit
    def test_unknown_weekday_returns_default(self):
        """범위 밖 요일 인덱스는 기본값 1.0 반환"""
        assert get_beer_weekday_coef(7) == 1.0
        assert get_beer_weekday_coef(-1) == 1.0
        assert get_beer_weekday_coef(100) == 1.0

    @pytest.mark.unit
    def test_coef_dict_has_7_entries(self):
        """BEER_WEEKDAY_COEF 딕셔너리에 7개 요일 모두 존재"""
        assert len(BEER_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in BEER_WEEKDAY_COEF

    @pytest.mark.unit
    def test_all_coefs_are_positive(self):
        """모든 계수가 양수"""
        for day in range(7):
            assert get_beer_weekday_coef(day) > 0


# =============================================================================
# get_beer_safety_days 테스트
# =============================================================================
class TestGetBeerSafetyDays:
    """맥주 안전재고 일수 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday,expected", [
        (0, 2),  # 월 → 2일
        (1, 2),  # 화 → 2일
        (2, 2),  # 수 → 2일
        (3, 2),  # 목 → 2일
        (4, 3),  # 금 → 3일 (주말 대비)
        (5, 3),  # 토 → 3일 (주말 대비)
        (6, 2),  # 일 → 2일
    ])
    def test_all_weekday_safety_days(self, weekday, expected):
        """모든 요일의 안전재고 일수 확인: 금/토=3일, 나머지=2일"""
        assert get_beer_safety_days(weekday) == expected

    @pytest.mark.unit
    def test_friday_returns_weekend_days(self):
        """금요일(4) 발주 시 주말 대비 3일치"""
        assert get_beer_safety_days(4) == BEER_SAFETY_CONFIG["weekend_days"]
        assert get_beer_safety_days(4) == 3

    @pytest.mark.unit
    def test_saturday_returns_weekend_days(self):
        """토요일(5) 발주 시 주말 대비 3일치"""
        assert get_beer_safety_days(5) == BEER_SAFETY_CONFIG["weekend_days"]
        assert get_beer_safety_days(5) == 3

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 6])
    def test_weekdays_return_default_days(self, weekday):
        """평일/일요일 발주 시 기본 2일치"""
        assert get_beer_safety_days(weekday) == BEER_SAFETY_CONFIG["default_days"]
        assert get_beer_safety_days(weekday) == 2

    @pytest.mark.unit
    def test_weekend_order_days_config(self):
        """BEER_SAFETY_CONFIG의 주말 발주 요일이 [4, 5] 확인"""
        assert BEER_SAFETY_CONFIG["weekend_order_days"] == [4, 5]

    @pytest.mark.unit
    def test_safety_config_values(self):
        """BEER_SAFETY_CONFIG 주요 값 확인"""
        assert BEER_SAFETY_CONFIG["default_days"] == 2
        assert BEER_SAFETY_CONFIG["weekend_days"] == 3
        assert BEER_SAFETY_CONFIG["max_stock_days"] == 7
        assert BEER_SAFETY_CONFIG["target_category"] == "049"
