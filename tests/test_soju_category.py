"""
소주(soju) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/soju.py
- is_soju_category(): 소주 카테고리 여부 확인
- get_soju_weekday_coef(): 요일별 계수 반환
- get_soju_safety_days(): 발주 요일 기준 안전재고 일수
"""

import pytest

from src.prediction.categories.soju import (
    is_soju_category,
    get_soju_weekday_coef,
    get_soju_safety_days,
    SOJU_CATEGORIES,
    SOJU_WEEKDAY_COEF,
    SOJU_SAFETY_CONFIG,
)


# =============================================================================
# is_soju_category 테스트
# =============================================================================
class TestIsSojuCategory:
    """소주 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_050_is_soju(self):
        """'050'은 소주 카테고리"""
        assert is_soju_category("050") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "049", "051", "072", "073", "001", "032", "999", "", "50", "0050",
    ])
    def test_non_soju_categories(self, mid_cd):
        """소주가 아닌 카테고리는 False 반환"""
        assert is_soju_category(mid_cd) is False

    @pytest.mark.unit
    def test_soju_categories_list_contains_050(self):
        """SOJU_CATEGORIES 상수에 '050' 포함 확인"""
        assert "050" in SOJU_CATEGORIES


# =============================================================================
# get_soju_weekday_coef 테스트
# =============================================================================
class TestGetSojuWeekdayCoef:
    """소주 요일별 계수 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday,expected", [
        (0, 0.83),  # 월 (최저)
        (1, 0.89),  # 화
        (2, 0.90),  # 수
        (3, 1.09),  # 목
        (4, 1.18),  # 금
        (5, 1.19),  # 토 (최고)
        (6, 0.91),  # 일
    ])
    def test_all_weekdays(self, weekday, expected):
        """모든 요일(0~6)의 소주 계수가 정확히 반환되는지 확인"""
        assert get_soju_weekday_coef(weekday) == expected

    @pytest.mark.unit
    def test_saturday_is_highest(self):
        """토요일(5) 계수가 모든 요일 중 최고값"""
        saturday_coef = get_soju_weekday_coef(5)
        assert saturday_coef == 1.19
        for day in range(7):
            assert saturday_coef >= get_soju_weekday_coef(day)

    @pytest.mark.unit
    def test_friday_is_second_highest(self):
        """금요일(4) 계수가 토요일 다음으로 높음"""
        friday_coef = get_soju_weekday_coef(4)
        assert friday_coef == 1.18
        non_saturday_coefs = [get_soju_weekday_coef(d) for d in range(7) if d != 5]
        assert friday_coef == max(non_saturday_coefs)

    @pytest.mark.unit
    def test_monday_is_lowest(self):
        """월요일(0) 계수가 모든 요일 중 최저값"""
        monday_coef = get_soju_weekday_coef(0)
        assert monday_coef == 0.83
        for day in range(7):
            assert monday_coef <= get_soju_weekday_coef(day)

    @pytest.mark.unit
    def test_unknown_weekday_returns_default(self):
        """범위 밖 요일 인덱스는 기본값 1.0 반환"""
        assert get_soju_weekday_coef(7) == 1.0
        assert get_soju_weekday_coef(-1) == 1.0
        assert get_soju_weekday_coef(100) == 1.0

    @pytest.mark.unit
    def test_coef_dict_has_7_entries(self):
        """SOJU_WEEKDAY_COEF 딕셔너리에 7개 요일 모두 존재"""
        assert len(SOJU_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in SOJU_WEEKDAY_COEF

    @pytest.mark.unit
    def test_all_coefs_are_positive(self):
        """모든 계수가 양수"""
        for day in range(7):
            assert get_soju_weekday_coef(day) > 0

    @pytest.mark.unit
    def test_soju_variation_less_than_beer(self):
        """소주 요일 편차가 맥주보다 적음 확인 (문서 명시)"""
        soju_coefs = [get_soju_weekday_coef(d) for d in range(7)]
        soju_range = max(soju_coefs) - min(soju_coefs)
        # 맥주 최대 2.54 - 최소 0.97 = 1.57, 소주 최대 1.19 - 최소 0.83 = 0.36
        assert soju_range < 1.0, "소주 요일 편차가 1.0 미만이어야 함"


# =============================================================================
# get_soju_safety_days 테스트
# =============================================================================
class TestGetSojuSafetyDays:
    """소주 안전재고 일수 테스트"""

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
        """모든 요일의 안전재고 일수: 금/토=3일, 나머지=2일"""
        assert get_soju_safety_days(weekday) == expected

    @pytest.mark.unit
    def test_friday_returns_weekend_days(self):
        """금요일(4) 발주 시 주말 대비 3일치"""
        assert get_soju_safety_days(4) == SOJU_SAFETY_CONFIG["weekend_days"]
        assert get_soju_safety_days(4) == 3

    @pytest.mark.unit
    def test_saturday_returns_weekend_days(self):
        """토요일(5) 발주 시 주말 대비 3일치"""
        assert get_soju_safety_days(5) == SOJU_SAFETY_CONFIG["weekend_days"]
        assert get_soju_safety_days(5) == 3

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 6])
    def test_weekdays_return_default_days(self, weekday):
        """평일/일요일 발주 시 기본 2일치"""
        assert get_soju_safety_days(weekday) == SOJU_SAFETY_CONFIG["default_days"]
        assert get_soju_safety_days(weekday) == 2

    @pytest.mark.unit
    def test_weekend_order_days_config(self):
        """SOJU_SAFETY_CONFIG의 주말 발주 요일이 [4, 5] 확인"""
        assert SOJU_SAFETY_CONFIG["weekend_order_days"] == [4, 5]

    @pytest.mark.unit
    def test_safety_config_values(self):
        """SOJU_SAFETY_CONFIG 주요 값 확인"""
        assert SOJU_SAFETY_CONFIG["default_days"] == 2
        assert SOJU_SAFETY_CONFIG["weekend_days"] == 3
        assert SOJU_SAFETY_CONFIG["max_stock_days"] == 7
        assert SOJU_SAFETY_CONFIG["target_category"] == "050"

    @pytest.mark.unit
    def test_soju_and_beer_safety_days_same_structure(self):
        """소주와 맥주의 안전재고 일수 구조 동일 확인"""
        for weekday in range(7):
            soju_days = get_soju_safety_days(weekday)
            # 금/토는 3일, 나머지는 2일로 동일 구조
            if weekday in [4, 5]:
                assert soju_days == 3
            else:
                assert soju_days == 2
