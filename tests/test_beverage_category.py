"""
음료류(beverage) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/beverage.py
- is_beverage_category(): 음료류 카테고리 여부 확인
- 회전율별 안전재고: high(5+) -> 1.5, medium(2-5) -> 1.5, low(<2) -> 1.0
- max_stock_days: 7.0
- skip_order 로직
- DEFAULT_WEEKDAY_COEF 값 (주말 증가)
"""

import sys
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prediction.categories.beverage import (
    is_beverage_category,
    BEVERAGE_CATEGORIES,
    BEVERAGE_SAFETY_CONFIG,
    BEVERAGE_DYNAMIC_SAFETY_CONFIG,
    DEFAULT_WEEKDAY_COEF,
    _get_turnover_level,
    analyze_beverage_pattern,
    get_safety_stock_with_beverage_pattern,
    BeveragePatternResult,
)


# =============================================================================
# is_beverage_category 테스트
# =============================================================================
class TestIsBeverageCategory:
    """음료류 카테고리 판별 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", ["010", "039", "043", "045", "048"])
    def test_beverage_categories_true(self, mid_cd):
        """음료류 카테고리 코드는 True 반환"""
        assert is_beverage_category(mid_cd) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "049", "050", "072", "001", "013", "999", "", "10", "0010",
    ])
    def test_non_beverage_categories(self, mid_cd):
        """음료류가 아닌 카테고리는 False 반환"""
        assert is_beverage_category(mid_cd) is False

    @pytest.mark.unit
    def test_beverage_categories_list(self):
        """BEVERAGE_CATEGORIES 상수에 5개 코드 포함 확인"""
        expected = {"010", "039", "043", "045", "048"}
        assert set(BEVERAGE_CATEGORIES) == expected
        assert len(BEVERAGE_CATEGORIES) == 5


# =============================================================================
# 회전율별 안전재고 설정 테스트
# =============================================================================
class TestBeverageSafetyConfig:
    """음료류 회전율별 안전재고 설정 테스트"""

    @pytest.mark.unit
    def test_high_turnover_config(self):
        """고회전(5+개/일): safety_days=1.5"""
        cfg = BEVERAGE_SAFETY_CONFIG["high_turnover"]
        assert cfg["min_daily_avg"] == 5.0
        assert cfg["safety_days"] == 1.5

    @pytest.mark.unit
    def test_medium_turnover_config(self):
        """중회전(2-5개/일): safety_days=1.5"""
        cfg = BEVERAGE_SAFETY_CONFIG["medium_turnover"]
        assert cfg["min_daily_avg"] == 2.0
        assert cfg["safety_days"] == 1.5

    @pytest.mark.unit
    def test_low_turnover_config(self):
        """저회전(<2개/일): safety_days=1.0"""
        cfg = BEVERAGE_SAFETY_CONFIG["low_turnover"]
        assert cfg["min_daily_avg"] == 0.0
        assert cfg["safety_days"] == 1.0

    @pytest.mark.unit
    def test_safety_days_decrease_with_lower_turnover(self):
        """회전율 낮을수록 안전재고일수 감소"""
        high = BEVERAGE_SAFETY_CONFIG["high_turnover"]["safety_days"]
        medium = BEVERAGE_SAFETY_CONFIG["medium_turnover"]["safety_days"]
        low = BEVERAGE_SAFETY_CONFIG["low_turnover"]["safety_days"]
        assert high >= medium > low

    @pytest.mark.unit
    def test_max_stock_days(self):
        """최대재고 일수는 7.0일"""
        assert BEVERAGE_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 7.0


# =============================================================================
# _get_turnover_level 테스트
# =============================================================================
class TestGetTurnoverLevel:
    """회전율 레벨 판정 테스트"""

    @pytest.mark.unit
    def test_high_turnover_at_5(self):
        """일평균 5.0 → high_turnover"""
        level, days, desc = _get_turnover_level(5.0)
        assert level == "high_turnover"
        assert days == 1.5

    @pytest.mark.unit
    def test_high_turnover_above_5(self):
        """일평균 10.0 → high_turnover"""
        level, days, desc = _get_turnover_level(10.0)
        assert level == "high_turnover"
        assert days == 1.5

    @pytest.mark.unit
    def test_medium_turnover_at_2(self):
        """일평균 2.0 → medium_turnover"""
        level, days, desc = _get_turnover_level(2.0)
        assert level == "medium_turnover"
        assert days == 1.5

    @pytest.mark.unit
    def test_medium_turnover_at_4_9(self):
        """일평균 4.9 → medium_turnover"""
        level, days, desc = _get_turnover_level(4.9)
        assert level == "medium_turnover"
        assert days == 1.5

    @pytest.mark.unit
    def test_low_turnover_below_2(self):
        """일평균 1.9 → low_turnover"""
        level, days, desc = _get_turnover_level(1.9)
        assert level == "low_turnover"
        assert days == 1.0

    @pytest.mark.unit
    def test_low_turnover_zero(self):
        """일평균 0 → low_turnover"""
        level, days, desc = _get_turnover_level(0.0)
        assert level == "low_turnover"
        assert days == 1.0

    @pytest.mark.unit
    def test_turnover_returns_description(self):
        """모든 레벨에서 설명 문자열 반환"""
        for avg in [0.5, 3.0, 7.0]:
            _, _, desc = _get_turnover_level(avg)
            assert isinstance(desc, str)
            assert len(desc) > 0


# =============================================================================
# DEFAULT_WEEKDAY_COEF 테스트
# =============================================================================
class TestDefaultWeekdayCoef:
    """음료류 기본 요일별 계수 테스트"""

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """요일별 계수 딕셔너리에 7개 요일 모두 존재"""
        assert len(DEFAULT_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in DEFAULT_WEEKDAY_COEF

    @pytest.mark.unit
    def test_saturday_is_highest(self):
        """토요일(5) 계수가 최고값 (1.15)"""
        assert DEFAULT_WEEKDAY_COEF[5] == 1.15
        for day in range(7):
            assert DEFAULT_WEEKDAY_COEF[5] >= DEFAULT_WEEKDAY_COEF[day]

    @pytest.mark.unit
    def test_sunday_is_second_highest(self):
        """일요일(6) 계수가 1.10"""
        assert DEFAULT_WEEKDAY_COEF[6] == 1.10

    @pytest.mark.unit
    def test_mon_tue_slightly_lower(self):
        """월/화(0,1) 계수가 소폭 감소 (0.95)"""
        assert DEFAULT_WEEKDAY_COEF[0] == 0.95
        assert DEFAULT_WEEKDAY_COEF[1] == 0.95

    @pytest.mark.unit
    def test_midweek_is_1_0(self):
        """수/목/금(2,3,4) 계수는 1.0"""
        assert DEFAULT_WEEKDAY_COEF[2] == 1.00
        assert DEFAULT_WEEKDAY_COEF[3] == 1.00
        assert DEFAULT_WEEKDAY_COEF[4] == 1.00

    @pytest.mark.unit
    def test_all_coefs_are_positive(self):
        """모든 계수가 양수"""
        for day in range(7):
            assert DEFAULT_WEEKDAY_COEF[day] > 0


# =============================================================================
# analyze_beverage_pattern 테스트 (DB 의존)
# =============================================================================
class TestAnalyzeBeveragePattern:
    """음료류 상품 패턴 분석 테스트"""

    @pytest.fixture
    def beverage_db(self, tmp_path):
        """음료류 테스트 DB 생성 (일평균 약 6개 - 고회전)"""
        db_file = tmp_path / "test_beverage.db"
        conn = sqlite3.connect(str(db_file))

        conn.execute("""
            CREATE TABLE daily_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT NOT NULL,
                sales_date TEXT NOT NULL,
                sale_qty INTEGER DEFAULT 0,
                stock_qty INTEGER DEFAULT 0,
                mid_cd TEXT,
                UNIQUE(item_cd, sales_date)
            )
        """)

        # 30일치 판매 데이터 (일평균 약 6개)
        today = datetime.now()
        for days_ago in range(30):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("ITEM_DRINK_A", date, 6, "010")
            )

        conn.commit()
        conn.close()
        return str(db_file)

    @pytest.fixture
    def low_turnover_db(self, tmp_path):
        """저회전 음료 테스트 DB (일평균 약 1개)"""
        db_file = tmp_path / "test_beverage_low.db"
        conn = sqlite3.connect(str(db_file))

        conn.execute("""
            CREATE TABLE daily_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT NOT NULL,
                sales_date TEXT NOT NULL,
                sale_qty INTEGER DEFAULT 0,
                stock_qty INTEGER DEFAULT 0,
                mid_cd TEXT,
                UNIQUE(item_cd, sales_date)
            )
        """)

        today = datetime.now()
        for days_ago in range(30):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("ITEM_DRINK_LOW", date, 1, "043")
            )

        conn.commit()
        conn.close()
        return str(db_file)

    @pytest.mark.db
    def test_analyze_returns_dataclass(self, beverage_db):
        """analyze_beverage_pattern이 BeveragePatternResult 반환"""
        result = analyze_beverage_pattern(
            "ITEM_DRINK_A", mid_cd="010", db_path=beverage_db
        )
        assert isinstance(result, BeveragePatternResult)

    @pytest.mark.db
    def test_high_turnover_safety_days(self, beverage_db):
        """고회전(6개/일) 상품 → safety_days=1.5"""
        result = analyze_beverage_pattern(
            "ITEM_DRINK_A", mid_cd="010", db_path=beverage_db
        )
        assert result.turnover_level == "high_turnover"
        assert result.safety_days == 1.5

    @pytest.mark.db
    def test_max_stock_is_7_days(self, beverage_db):
        """max_stock = 일평균 x 7.0일"""
        result = analyze_beverage_pattern(
            "ITEM_DRINK_A", mid_cd="010", db_path=beverage_db
        )
        expected_max = result.daily_avg * 7.0
        assert abs(result.max_stock - round(expected_max, 2)) < 0.01

    @pytest.mark.db
    def test_skip_order_when_over_max_stock(self, beverage_db):
        """재고+미입고 >= max_stock이면 skip_order=True"""
        result = analyze_beverage_pattern(
            "ITEM_DRINK_A", mid_cd="010", db_path=beverage_db,
            current_stock=100, pending_qty=100
        )
        assert result.skip_order is True
        assert "상한선 초과" in result.skip_reason

    @pytest.mark.db
    def test_no_skip_when_under_max_stock(self, beverage_db):
        """재고+미입고 < max_stock이면 skip_order=False"""
        result = analyze_beverage_pattern(
            "ITEM_DRINK_A", mid_cd="010", db_path=beverage_db,
            current_stock=0, pending_qty=0
        )
        assert result.skip_order is False
        assert result.skip_reason == ""

    @pytest.mark.db
    def test_low_turnover_safety_days(self, low_turnover_db):
        """저회전(1개/일) 상품 → safety_days=1.0"""
        result = analyze_beverage_pattern(
            "ITEM_DRINK_LOW", mid_cd="043", db_path=low_turnover_db
        )
        assert result.turnover_level == "low_turnover"
        assert result.safety_days == 1.0


# =============================================================================
# get_safety_stock_with_beverage_pattern 테스트
# =============================================================================
class TestGetSafetyStockWithBeveragePattern:
    """통합 안전재고 계산 테스트"""

    @pytest.mark.unit
    def test_non_beverage_returns_default_fallback(self):
        """음료류 카테고리가 아니면 default 모듈로 위임 (패턴=None)"""
        safety, pattern = get_safety_stock_with_beverage_pattern(
            mid_cd="049",  # 맥주 - 음료류 아님
            daily_avg=5.0,
            expiration_days=90,
        )
        assert pattern is None
        assert safety > 0

    @pytest.mark.unit
    def test_beverage_without_item_cd_uses_default_days(self):
        """item_cd 없이 음료류 카테고리 호출 시 기본 안전재고일수 적용"""
        default_days = BEVERAGE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        daily_avg = 5.0

        safety, pattern = get_safety_stock_with_beverage_pattern(
            mid_cd="010",
            daily_avg=daily_avg,
            expiration_days=30,
            item_cd=None,
        )
        assert pattern is None
        assert abs(safety - daily_avg * default_days) < 0.01

    @pytest.mark.unit
    def test_dynamic_config_enabled(self):
        """동적 안전재고 설정이 활성화 상태인지 확인"""
        assert BEVERAGE_DYNAMIC_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_dynamic_config_analysis_days(self):
        """분석 기간이 30일인지 확인"""
        assert BEVERAGE_DYNAMIC_SAFETY_CONFIG["analysis_days"] == 30
