"""
냉동/아이스크림(frozen_ice) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/frozen_ice.py
- is_frozen_ice_category(): 냉동/아이스크림 카테고리 여부 확인
- DEFAULT_SEASONAL_COEF: 1월 0.60, 7월 1.60 검증
- _get_seasonal_safety_days(): 여름(6-8월) -> 2.0, 겨울(12-2월) -> 1.0, 기본 -> 1.5
- max_stock_days: 7.0
- skip_order 로직
"""

import sys
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prediction.categories.frozen_ice import (
    is_frozen_ice_category,
    FROZEN_ICE_CATEGORIES,
    FROZEN_ICE_WEEKDAY_COEF,
    DEFAULT_SEASONAL_COEF,
    FROZEN_SAFETY_CONFIG,
    FROZEN_ICE_DYNAMIC_SAFETY_CONFIG,
    _get_seasonal_safety_days,
    analyze_frozen_ice_pattern,
    get_safety_stock_with_frozen_ice_pattern,
    FrozenIcePatternResult,
)


# =============================================================================
# is_frozen_ice_category 테스트
# =============================================================================
class TestIsFrozenIceCategory:
    """냉동/아이스크림 카테고리 판별 테스트"""

    @pytest.mark.unit
    def test_021_is_frozen_ice(self):
        """'021' (일반아이스크림)은 냉동/아이스크림 카테고리"""
        assert is_frozen_ice_category("021") is True

    @pytest.mark.unit
    def test_034_is_frozen_ice(self):
        """'034' (냉동즉석식)은 냉동/아이스크림 카테고리"""
        assert is_frozen_ice_category("034") is True

    @pytest.mark.unit
    def test_100_is_frozen_ice(self):
        """'100' (RI아이스크림)은 냉동/아이스크림 카테고리"""
        assert is_frozen_ice_category("100") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "010", "049", "050", "072", "001", "013", "999", "", "21", "0021",
    ])
    def test_non_frozen_ice_categories(self, mid_cd):
        """냉동/아이스크림이 아닌 카테고리는 False 반환"""
        assert is_frozen_ice_category(mid_cd) is False

    @pytest.mark.unit
    def test_frozen_ice_categories_list(self):
        """FROZEN_ICE_CATEGORIES 상수에 3개 코드 포함 확인"""
        expected = {"021", "034", "100"}
        assert set(FROZEN_ICE_CATEGORIES) == expected
        assert len(FROZEN_ICE_CATEGORIES) == 3


# =============================================================================
# DEFAULT_SEASONAL_COEF 테스트
# =============================================================================
class TestDefaultSeasonalCoef:
    """월별 기본 계절 계수 테스트"""

    @pytest.mark.unit
    def test_january_coef(self):
        """1월 계절 계수: 0.60 (겨울 최저)"""
        assert DEFAULT_SEASONAL_COEF[1] == 0.60

    @pytest.mark.unit
    def test_july_coef(self):
        """7월 계절 계수: 1.60 (여름 최고)"""
        assert DEFAULT_SEASONAL_COEF[7] == 1.60

    @pytest.mark.unit
    def test_december_coef(self):
        """12월 계절 계수: 0.60 (겨울)"""
        assert DEFAULT_SEASONAL_COEF[12] == 0.60

    @pytest.mark.unit
    def test_summer_coefs_above_1(self):
        """여름(6-8월) 계수는 모두 1.0 이상"""
        for month in [6, 7, 8]:
            assert DEFAULT_SEASONAL_COEF[month] >= 1.0

    @pytest.mark.unit
    def test_winter_coefs_below_1(self):
        """겨울(12-2월) 계수는 모두 1.0 미만"""
        for month in [12, 1, 2]:
            assert DEFAULT_SEASONAL_COEF[month] < 1.0

    @pytest.mark.unit
    def test_all_12_months_present(self):
        """12개월 모두 계수가 존재"""
        assert len(DEFAULT_SEASONAL_COEF) == 12
        for month in range(1, 13):
            assert month in DEFAULT_SEASONAL_COEF

    @pytest.mark.unit
    def test_all_coefs_are_positive(self):
        """모든 계절 계수가 양수"""
        for month in range(1, 13):
            assert DEFAULT_SEASONAL_COEF[month] > 0

    @pytest.mark.unit
    def test_july_is_highest(self):
        """7월이 최고 계절 계수"""
        july_coef = DEFAULT_SEASONAL_COEF[7]
        for month in range(1, 13):
            assert july_coef >= DEFAULT_SEASONAL_COEF[month]


# =============================================================================
# _get_seasonal_safety_days 테스트
# =============================================================================
class TestGetSeasonalSafetyDays:
    """계절별 안전재고 일수 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("month", [6, 7, 8])
    def test_summer_months_return_2_0(self, month):
        """여름(6-8월) → safety_days=2.0"""
        assert _get_seasonal_safety_days(month) == 2.0

    @pytest.mark.unit
    @pytest.mark.parametrize("month", [12, 1, 2])
    def test_winter_months_return_1_0(self, month):
        """겨울(12-2월) → safety_days=1.0"""
        assert _get_seasonal_safety_days(month) == 1.0

    @pytest.mark.unit
    @pytest.mark.parametrize("month", [3, 4, 5, 9, 10, 11])
    def test_default_months_return_1_5(self, month):
        """봄/가을(3-5, 9-11월) → safety_days=1.5 (기본)"""
        assert _get_seasonal_safety_days(month) == 1.5

    @pytest.mark.unit
    def test_safety_config_summer_months(self):
        """FROZEN_SAFETY_CONFIG의 여름 해당 월이 [6, 7, 8] 확인"""
        assert FROZEN_SAFETY_CONFIG["summer_months"] == [6, 7, 8]

    @pytest.mark.unit
    def test_safety_config_winter_months(self):
        """FROZEN_SAFETY_CONFIG의 겨울 해당 월이 [12, 1, 2] 확인"""
        assert FROZEN_SAFETY_CONFIG["winter_months"] == [12, 1, 2]

    @pytest.mark.unit
    def test_safety_config_values(self):
        """FROZEN_SAFETY_CONFIG 주요 설정값 확인"""
        assert FROZEN_SAFETY_CONFIG["summer_safety_days"] == 2.0
        assert FROZEN_SAFETY_CONFIG["winter_safety_days"] == 1.0
        assert FROZEN_SAFETY_CONFIG["default_safety_days"] == 1.5


# =============================================================================
# FROZEN_ICE_WEEKDAY_COEF 테스트
# =============================================================================
class TestFrozenIceWeekdayCoef:
    """냉동/아이스크림 요일별 계수 테스트"""

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """요일별 계수 딕셔너리에 7개 요일 모두 존재"""
        assert len(FROZEN_ICE_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in FROZEN_ICE_WEEKDAY_COEF

    @pytest.mark.unit
    def test_sunday_is_highest(self):
        """일요일(6) 계수가 최고값 (1.40)"""
        assert FROZEN_ICE_WEEKDAY_COEF[6] == 1.40

    @pytest.mark.unit
    def test_saturday_is_high(self):
        """토요일(5) 계수가 1.30"""
        assert FROZEN_ICE_WEEKDAY_COEF[5] == 1.30

    @pytest.mark.unit
    def test_mon_tue_are_lowest(self):
        """월/화(0,1) 계수가 최저값 (0.90)"""
        assert FROZEN_ICE_WEEKDAY_COEF[0] == 0.90
        assert FROZEN_ICE_WEEKDAY_COEF[1] == 0.90


# =============================================================================
# analyze_frozen_ice_pattern 테스트 (DB 의존)
# =============================================================================
class TestAnalyzeFrozenIcePattern:
    """냉동/아이스크림 패턴 분석 테스트"""

    @pytest.fixture
    def frozen_db(self, tmp_path):
        """냉동/아이스크림 테스트 DB 생성"""
        db_file = tmp_path / "test_frozen.db"
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

        # products 테이블 (요일/계절 학습용 JOIN 대상)
        conn.execute("""
            CREATE TABLE products (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT
            )
        """)
        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("ITEM_ICE_A", "아이스크림A", "021")
        )

        # 30일치 판매 데이터 (일평균 약 5개)
        today = datetime.now()
        for days_ago in range(30):
            date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("ITEM_ICE_A", date, 5, "021")
            )

        conn.commit()
        conn.close()
        return str(db_file)

    @pytest.mark.db
    def test_analyze_returns_dataclass(self, frozen_db):
        """analyze_frozen_ice_pattern이 FrozenIcePatternResult 반환"""
        result = analyze_frozen_ice_pattern(
            "ITEM_ICE_A", mid_cd="021", db_path=frozen_db
        )
        assert isinstance(result, FrozenIcePatternResult)

    @pytest.mark.db
    def test_daily_avg_calculation(self, frozen_db):
        """일평균 판매량 계산 확인"""
        result = analyze_frozen_ice_pattern(
            "ITEM_ICE_A", mid_cd="021", db_path=frozen_db
        )
        assert result.daily_avg > 0
        assert result.total_sales > 0

    @pytest.mark.db
    def test_max_stock_is_7_days(self, frozen_db):
        """max_stock = 일평균 x 7.0일"""
        result = analyze_frozen_ice_pattern(
            "ITEM_ICE_A", mid_cd="021", db_path=frozen_db
        )
        expected_max = result.daily_avg * FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["max_stock_days"]
        assert abs(result.max_stock - round(expected_max, 2)) < 0.01

    @pytest.mark.db
    def test_skip_order_when_over_max_stock(self, frozen_db):
        """재고+미입고 >= max_stock이면 skip_order=True"""
        result = analyze_frozen_ice_pattern(
            "ITEM_ICE_A", mid_cd="021", db_path=frozen_db,
            current_stock=100, pending_qty=100
        )
        assert result.skip_order is True
        assert "상한선 초과" in result.skip_reason

    @pytest.mark.db
    def test_no_skip_when_under_max_stock(self, frozen_db):
        """재고+미입고 < max_stock이면 skip_order=False"""
        result = analyze_frozen_ice_pattern(
            "ITEM_ICE_A", mid_cd="021", db_path=frozen_db,
            current_stock=0, pending_qty=0
        )
        assert result.skip_order is False
        assert result.skip_reason == ""

    @pytest.mark.db
    def test_seasonal_safety_days_applied(self, frozen_db):
        """현재 월에 맞는 계절별 안전재고일수 적용 확인"""
        result = analyze_frozen_ice_pattern(
            "ITEM_ICE_A", mid_cd="021", db_path=frozen_db
        )
        current_month = datetime.now().month
        expected_safety_days = _get_seasonal_safety_days(current_month)
        assert result.safety_days == expected_safety_days


# =============================================================================
# get_safety_stock_with_frozen_ice_pattern 테스트
# =============================================================================
class TestGetSafetyStockWithFrozenIcePattern:
    """통합 안전재고 계산 테스트"""

    @pytest.mark.unit
    def test_non_frozen_ice_returns_default_fallback(self):
        """냉동/아이스크림이 아니면 default 모듈로 위임 (패턴=None)"""
        safety, pattern = get_safety_stock_with_frozen_ice_pattern(
            mid_cd="049",  # 맥주 - 냉동/아이스크림 아님
            daily_avg=5.0,
            expiration_days=90,
        )
        assert pattern is None
        assert safety > 0

    @pytest.mark.unit
    def test_frozen_ice_without_item_cd_uses_default_days(self):
        """item_cd 없이 냉동/아이스크림 카테고리 호출 시 기본 안전재고일수 적용"""
        default_days = FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        daily_avg = 5.0

        safety, pattern = get_safety_stock_with_frozen_ice_pattern(
            mid_cd="021",
            daily_avg=daily_avg,
            expiration_days=365,
            item_cd=None,
        )
        assert pattern is None
        assert abs(safety - daily_avg * default_days) < 0.01

    @pytest.mark.unit
    def test_dynamic_config_max_stock_days(self):
        """동적 안전재고 설정의 max_stock_days가 7.0"""
        assert FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 7.0

    @pytest.mark.unit
    def test_dynamic_config_enabled(self):
        """동적 안전재고 설정이 활성화 상태"""
        assert FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_dynamic_config_seasonal_enabled(self):
        """계절 패턴 학습이 활성화 상태"""
        assert FROZEN_ICE_DYNAMIC_SAFETY_CONFIG["seasonal_enabled"] is True
