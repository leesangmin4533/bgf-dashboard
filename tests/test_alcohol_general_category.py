"""
일반주류(alcohol_general) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/alcohol_general.py
- is_alcohol_general_category(): 일반주류 카테고리 여부 확인
- 요일별 안전재고일수: 금/토 → 3.0일, 나머지 → 2.0일
- max_stock_days: 14.0
- DEFAULT_WEEKDAY_COEF: 월 0.80 ~ 일 2.00 (주말 집중)
- skip_order 로직
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch

from src.prediction.categories.alcohol_general import (
    is_alcohol_general_category,
    get_safety_stock_with_alcohol_general_pattern,
    analyze_alcohol_general_pattern,
    AlcoholGeneralPatternResult,
    ALCOHOL_GENERAL_TARGET_CATEGORIES,
    ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG,
    ALCOHOL_WEEKDAY_SAFETY,
    DEFAULT_WEEKDAY_COEF,
)


# =============================================================================
# is_alcohol_general_category 테스트
# =============================================================================
class TestIsAlcoholGeneralCategory:
    """일반주류 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_052_is_alcohol_general(self):
        """'052'(양주)는 일반주류 카테고리"""
        assert is_alcohol_general_category("052") is True

    @pytest.mark.unit
    def test_053_is_alcohol_general(self):
        """'053'(와인)은 일반주류 카테고리"""
        assert is_alcohol_general_category("053") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "049", "050", "001", "072", "054", "999", "", "52", "0052",
    ])
    def test_non_alcohol_general_categories(self, mid_cd):
        """일반주류가 아닌 카테고리는 False 반환"""
        assert is_alcohol_general_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_has_2_entries(self):
        """ALCOHOL_GENERAL_TARGET_CATEGORIES에 2개 코드 존재"""
        assert len(ALCOHOL_GENERAL_TARGET_CATEGORIES) == 2

    @pytest.mark.unit
    def test_target_categories_content_matches_config(self):
        """TARGET_CATEGORIES와 CONFIG의 target_categories 일치"""
        assert set(ALCOHOL_GENERAL_TARGET_CATEGORIES) == set(
            ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["target_categories"]
        )


# =============================================================================
# 요일별 안전재고일수 (ALCOHOL_WEEKDAY_SAFETY) 테스트
# =============================================================================
class TestAlcoholWeekdaySafety:
    """요일별 안전재고일수 검증"""

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday,expected", [
        (0, 2.0),  # 월
        (1, 2.0),  # 화
        (2, 2.0),  # 수
        (3, 2.0),  # 목
        (4, 3.0),  # 금
        (5, 3.0),  # 토
        (6, 2.0),  # 일
    ])
    def test_weekday_safety_days(self, weekday, expected):
        """모든 요일의 안전재고일수 확인: 금/토=3.0, 나머지=2.0"""
        assert ALCOHOL_WEEKDAY_SAFETY[weekday] == expected

    @pytest.mark.unit
    def test_friday_saturday_have_weekend_safety(self):
        """금요일(4), 토요일(5)은 3.0일 (주말 대비)"""
        assert ALCOHOL_WEEKDAY_SAFETY[4] == 3.0
        assert ALCOHOL_WEEKDAY_SAFETY[5] == 3.0

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 6])
    def test_non_weekend_order_days_have_default(self, weekday):
        """금/토 외 요일은 기본 2.0일"""
        assert ALCOHOL_WEEKDAY_SAFETY[weekday] == 2.0

    @pytest.mark.unit
    def test_weekday_safety_has_7_entries(self):
        """ALCOHOL_WEEKDAY_SAFETY에 7개 요일 모두 존재"""
        assert len(ALCOHOL_WEEKDAY_SAFETY) == 7
        for day in range(7):
            assert day in ALCOHOL_WEEKDAY_SAFETY

    @pytest.mark.unit
    def test_config_weekend_safety_days_matches(self):
        """CONFIG의 weekend_safety_days(3.0)와 ALCOHOL_WEEKDAY_SAFETY 금/토 일치"""
        assert ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["weekend_safety_days"] == 3.0
        assert ALCOHOL_WEEKDAY_SAFETY[4] == ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["weekend_safety_days"]

    @pytest.mark.unit
    def test_config_default_safety_days_matches(self):
        """CONFIG의 default_safety_days(2.0)와 ALCOHOL_WEEKDAY_SAFETY 평일 일치"""
        assert ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["default_safety_days"] == 2.0
        assert ALCOHOL_WEEKDAY_SAFETY[0] == ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["default_safety_days"]


# =============================================================================
# DEFAULT_WEEKDAY_COEF 및 상수 테스트
# =============================================================================
class TestAlcoholGeneralConstants:
    """일반주류 상수 검증"""

    @pytest.mark.unit
    def test_max_stock_days_is_10(self):
        """max_stock_days가 10.0일"""
        assert ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 10.0

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """DEFAULT_WEEKDAY_COEF에 7개 요일 모두 존재"""
        assert len(DEFAULT_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in DEFAULT_WEEKDAY_COEF

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday,expected", [
        (0, 0.80),  # 월
        (1, 0.80),  # 화
        (2, 0.85),  # 수
        (3, 0.90),  # 목
        (4, 1.10),  # 금
        (5, 1.50),  # 토
        (6, 2.00),  # 일
    ])
    def test_weekday_coef_exact_values(self, weekday, expected):
        """각 요일의 계수가 정확한 값인지 확인"""
        assert DEFAULT_WEEKDAY_COEF[weekday] == expected

    @pytest.mark.unit
    def test_sunday_is_highest_coef(self):
        """일요일(6) 계수 2.00이 최고값"""
        sunday_coef = DEFAULT_WEEKDAY_COEF[6]
        assert sunday_coef == 2.00
        for day in range(7):
            assert sunday_coef >= DEFAULT_WEEKDAY_COEF[day]

    @pytest.mark.unit
    def test_monday_is_lowest_coef(self):
        """월요일(0) 계수 0.80이 최저값"""
        monday_coef = DEFAULT_WEEKDAY_COEF[0]
        assert monday_coef == 0.80
        for day in range(7):
            assert monday_coef <= DEFAULT_WEEKDAY_COEF[day]

    @pytest.mark.unit
    def test_weekend_coefs_higher_than_weekdays(self):
        """주말(금/토/일) 계수가 평일(월~목) 계수보다 높음"""
        weekday_max = max(DEFAULT_WEEKDAY_COEF[d] for d in range(4))  # 월~목
        weekend_min = min(DEFAULT_WEEKDAY_COEF[d] for d in [4, 5, 6])  # 금~일
        assert weekend_min > weekday_max

    @pytest.mark.unit
    def test_coef_increases_toward_weekend(self):
        """수 < 목 < 금 < 토 < 일 순으로 계수 증가"""
        assert DEFAULT_WEEKDAY_COEF[2] < DEFAULT_WEEKDAY_COEF[3]  # 수 < 목
        assert DEFAULT_WEEKDAY_COEF[3] < DEFAULT_WEEKDAY_COEF[4]  # 목 < 금
        assert DEFAULT_WEEKDAY_COEF[4] < DEFAULT_WEEKDAY_COEF[5]  # 금 < 토
        assert DEFAULT_WEEKDAY_COEF[5] < DEFAULT_WEEKDAY_COEF[6]  # 토 < 일

    @pytest.mark.unit
    def test_analysis_days_is_30(self):
        """분석 기간이 30일"""
        assert ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["analysis_days"] == 30

    @pytest.mark.unit
    def test_min_data_days_is_14(self):
        """최소 데이터 일수가 14일"""
        assert ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["min_data_days"] == 14


# =============================================================================
# skip_order 로직 테스트
# =============================================================================
class TestAlcoholGeneralSkipOrder:
    """발주 스킵 로직 테스트 (DB 의존)"""

    @pytest.mark.db
    def test_skip_when_stock_exceeds_max(self, in_memory_db):
        """재고+미입고 >= 상한선이면 skip_order=True"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("WINE001", date, 2, "053")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            # daily_avg ~= 2, max_stock = 2 * 10 = 20
            # current_stock(20) + pending(10) = 30 >= 20 → skip
            result = analyze_alcohol_general_pattern(
                item_cd="WINE001",
                mid_cd="053",
                db_path=tmp.name,
                current_stock=20,
                pending_qty=10,
            )
            assert result.skip_order is True
            assert "상한선 초과" in result.skip_reason
        finally:
            os.unlink(tmp.name)

    @pytest.mark.db
    def test_no_skip_when_stock_below_max(self, in_memory_db):
        """재고+미입고 < 상한선이면 skip_order=False"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("LIQUOR001", date, 2, "052")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_alcohol_general_pattern(
                item_cd="LIQUOR001",
                mid_cd="052",
                db_path=tmp.name,
                current_stock=3,
                pending_qty=2,
            )
            assert result.skip_order is False
            assert result.skip_reason == ""
        finally:
            os.unlink(tmp.name)


# =============================================================================
# get_safety_stock_with_alcohol_general_pattern 비대상 fallback 테스트
# =============================================================================
class TestAlcoholGeneralFallback:
    """비대상 카테고리 fallback 테스트"""

    @pytest.mark.unit
    def test_non_alcohol_returns_none_pattern(self):
        """일반주류가 아닌 카테고리는 pattern=None 반환"""
        with patch(
            "src.prediction.categories.alcohol_general.is_alcohol_general_category",
            return_value=False,
        ):
            with patch(
                "src.prediction.categories.default.get_safety_stock_days",
                return_value=2.0,
            ):
                safety, pattern = get_safety_stock_with_alcohol_general_pattern(
                    mid_cd="001",
                    daily_avg=5.0,
                    expiration_days=1,
                )
                assert pattern is None
                assert safety == 5.0 * 2.0

    @pytest.mark.unit
    def test_alcohol_without_item_cd_uses_default_days(self):
        """item_cd 없으면 default_safety_days(2.0) 적용"""
        safety, pattern = get_safety_stock_with_alcohol_general_pattern(
            mid_cd="052",
            daily_avg=3.0,
            expiration_days=365,
            item_cd=None,
        )
        expected = 3.0 * ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        assert safety == expected
        assert pattern is None


# =============================================================================
# AlcoholGeneralPatternResult dataclass 테스트
# =============================================================================
class TestAlcoholGeneralPatternResult:
    """데이터클래스 구조 검증"""

    @pytest.mark.unit
    def test_dataclass_fields(self):
        """AlcoholGeneralPatternResult 필드 존재 및 값 확인"""
        result = AlcoholGeneralPatternResult(
            item_cd="WINE001",
            mid_cd="053",
            daily_avg=1.5,
            weekday_coef=1.50,
            safety_days=3.0,
            final_safety_stock=6.75,
            max_stock=21.0,
            skip_order=False,
            skip_reason="",
            data_days=25,
        )
        assert result.item_cd == "WINE001"
        assert result.mid_cd == "053"
        assert result.daily_avg == 1.5
        assert result.weekday_coef == 1.50
        assert result.safety_days == 3.0
        assert result.final_safety_stock == 6.75
        assert result.max_stock == 21.0
        assert result.skip_order is False
        assert result.data_days == 25
