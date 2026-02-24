"""
즉석식품(instant_meal) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/instant_meal.py
- is_instant_meal_category(): 즉석식품 카테고리 여부 확인
- _classify_expiry_group(): 유통기한별 그룹 분류 (fresh/chilled/shelf_stable/long_life)
- 각 그룹별 safety_days: fresh 0.5, chilled 1.0, shelf_stable 1.5, long_life 2.0
- max_stock 계산: fresh=유통기한 기반, 나머지=일수 기반
- skip_order 로직
"""

import sys
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from prediction.categories.instant_meal import (
    is_instant_meal_category,
    INSTANT_MEAL_CATEGORIES,
    INSTANT_MEAL_WEEKDAY_COEF,
    INSTANT_EXPIRY_GROUPS,
    INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG,
    _classify_expiry_group,
    _calculate_max_stock,
    analyze_instant_meal_pattern,
    get_safety_stock_with_instant_meal_pattern,
    InstantMealPatternResult,
)


# =============================================================================
# is_instant_meal_category 테스트
# =============================================================================
class TestIsInstantMealCategory:
    """즉석식품 카테고리 판별 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", ["027", "028", "031", "033", "035"])
    def test_instant_meal_categories_true(self, mid_cd):
        """즉석식품 카테고리 코드는 True 반환"""
        assert is_instant_meal_category(mid_cd) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "010", "049", "050", "072", "001", "013", "999", "", "27", "0027",
    ])
    def test_non_instant_meal_categories(self, mid_cd):
        """즉석식품이 아닌 카테고리는 False 반환"""
        assert is_instant_meal_category(mid_cd) is False

    @pytest.mark.unit
    def test_instant_meal_categories_list(self):
        """INSTANT_MEAL_CATEGORIES 상수에 5개 코드 포함 확인"""
        expected = {"027", "028", "031", "033", "035"}
        assert set(INSTANT_MEAL_CATEGORIES) == expected
        assert len(INSTANT_MEAL_CATEGORIES) == 5


# =============================================================================
# _classify_expiry_group 테스트
# =============================================================================
class TestClassifyExpiryGroup:
    """유통기한 그룹 분류 테스트"""

    @pytest.mark.unit
    def test_3_days_is_fresh(self):
        """유통기한 3일 → fresh"""
        group, cfg = _classify_expiry_group(3)
        assert group == "fresh"
        assert cfg["safety_days"] == 0.5

    @pytest.mark.unit
    def test_7_days_is_fresh(self):
        """유통기한 7일 → fresh (경계값)"""
        group, cfg = _classify_expiry_group(7)
        assert group == "fresh"

    @pytest.mark.unit
    def test_15_days_is_chilled(self):
        """유통기한 15일 → chilled"""
        group, cfg = _classify_expiry_group(15)
        assert group == "chilled"
        assert cfg["safety_days"] == 1.0

    @pytest.mark.unit
    def test_8_days_is_chilled(self):
        """유통기한 8일 → chilled (경계값)"""
        group, cfg = _classify_expiry_group(8)
        assert group == "chilled"

    @pytest.mark.unit
    def test_30_days_is_chilled(self):
        """유통기한 30일 → chilled (경계값)"""
        group, cfg = _classify_expiry_group(30)
        assert group == "chilled"

    @pytest.mark.unit
    def test_60_days_is_shelf_stable(self):
        """유통기한 60일 → shelf_stable"""
        group, cfg = _classify_expiry_group(60)
        assert group == "shelf_stable"
        assert cfg["safety_days"] == 1.5

    @pytest.mark.unit
    def test_31_days_is_shelf_stable(self):
        """유통기한 31일 → shelf_stable (경계값)"""
        group, cfg = _classify_expiry_group(31)
        assert group == "shelf_stable"

    @pytest.mark.unit
    def test_200_days_is_long_life(self):
        """유통기한 200일 → long_life"""
        group, cfg = _classify_expiry_group(200)
        assert group == "long_life"
        assert cfg["safety_days"] == 2.0

    @pytest.mark.unit
    def test_181_days_is_long_life(self):
        """유통기한 181일 → long_life (경계값)"""
        group, cfg = _classify_expiry_group(181)
        assert group == "long_life"

    @pytest.mark.unit
    def test_none_fallback_to_chilled(self):
        """유통기한 None → chilled (폴백)"""
        group, cfg = _classify_expiry_group(None)
        assert group == "chilled"
        assert cfg["safety_days"] == 1.0

    @pytest.mark.unit
    def test_1_day_is_fresh(self):
        """유통기한 1일 → fresh (최소값)"""
        group, cfg = _classify_expiry_group(1)
        assert group == "fresh"


# =============================================================================
# 그룹별 safety_days 설정값 검증
# =============================================================================
class TestInstantExpiryGroupsConfig:
    """유통기한 그룹별 안전재고 설정 테스트"""

    @pytest.mark.unit
    def test_fresh_safety_days(self):
        """fresh 그룹: safety_days=0.5"""
        assert INSTANT_EXPIRY_GROUPS["fresh"]["safety_days"] == 0.5

    @pytest.mark.unit
    def test_chilled_safety_days(self):
        """chilled 그룹: safety_days=1.0"""
        assert INSTANT_EXPIRY_GROUPS["chilled"]["safety_days"] == 1.0

    @pytest.mark.unit
    def test_shelf_stable_safety_days(self):
        """shelf_stable 그룹: safety_days=1.5"""
        assert INSTANT_EXPIRY_GROUPS["shelf_stable"]["safety_days"] == 1.5

    @pytest.mark.unit
    def test_long_life_safety_days(self):
        """long_life 그룹: safety_days=2.0"""
        assert INSTANT_EXPIRY_GROUPS["long_life"]["safety_days"] == 2.0

    @pytest.mark.unit
    def test_safety_days_increase_with_expiry(self):
        """유통기한 길수록 안전재고일수 증가"""
        groups = ["fresh", "chilled", "shelf_stable", "long_life"]
        days = [INSTANT_EXPIRY_GROUPS[g]["safety_days"] for g in groups]
        assert days == sorted(days), "안전재고일수가 단조 증가해야 함"

    @pytest.mark.unit
    def test_fresh_max_stock_rule_is_expiry_based(self):
        """fresh 그룹의 max_stock_rule은 'expiry_based'"""
        assert INSTANT_EXPIRY_GROUPS["fresh"]["max_stock_rule"] == "expiry_based"

    @pytest.mark.unit
    def test_non_fresh_groups_have_max_stock_days(self):
        """chilled/shelf_stable/long_life 그룹에 max_stock_days 존재"""
        for group in ["chilled", "shelf_stable", "long_life"]:
            assert "max_stock_days" in INSTANT_EXPIRY_GROUPS[group]


# =============================================================================
# _calculate_max_stock 테스트
# =============================================================================
class TestCalculateMaxStock:
    """최대 재고 상한선 계산 테스트"""

    @pytest.mark.unit
    def test_fresh_expiry_based(self):
        """fresh 그룹: max_stock = 유통기한 x 일평균"""
        group_cfg = INSTANT_EXPIRY_GROUPS["fresh"]
        daily_avg = 5.0
        expiration_days = 3
        result = _calculate_max_stock("fresh", group_cfg, daily_avg, expiration_days)
        assert result == 5.0 * 3  # 15.0

    @pytest.mark.unit
    def test_chilled_days_based(self):
        """chilled 그룹: max_stock = max_stock_days x 일평균"""
        group_cfg = INSTANT_EXPIRY_GROUPS["chilled"]
        daily_avg = 5.0
        result = _calculate_max_stock("chilled", group_cfg, daily_avg, 15)
        expected = daily_avg * group_cfg["max_stock_days"]
        assert result == expected

    @pytest.mark.unit
    def test_shelf_stable_days_based(self):
        """shelf_stable 그룹: max_stock = 7.0 x 일평균"""
        group_cfg = INSTANT_EXPIRY_GROUPS["shelf_stable"]
        daily_avg = 3.0
        result = _calculate_max_stock("shelf_stable", group_cfg, daily_avg, 60)
        expected = daily_avg * group_cfg["max_stock_days"]
        assert result == expected

    @pytest.mark.unit
    def test_long_life_days_based(self):
        """long_life 그룹: max_stock = 7.0 x 일평균"""
        group_cfg = INSTANT_EXPIRY_GROUPS["long_life"]
        daily_avg = 2.0
        result = _calculate_max_stock("long_life", group_cfg, daily_avg, 200)
        expected = daily_avg * group_cfg["max_stock_days"]
        assert result == expected

    @pytest.mark.unit
    def test_zero_daily_avg_returns_zero(self):
        """일평균 0이면 max_stock=0"""
        group_cfg = INSTANT_EXPIRY_GROUPS["fresh"]
        result = _calculate_max_stock("fresh", group_cfg, 0.0, 5)
        assert result == 0

    @pytest.mark.unit
    def test_negative_daily_avg_returns_zero(self):
        """일평균 음수면 max_stock=0"""
        group_cfg = INSTANT_EXPIRY_GROUPS["chilled"]
        result = _calculate_max_stock("chilled", group_cfg, -1.0, 15)
        assert result == 0


# =============================================================================
# INSTANT_MEAL_WEEKDAY_COEF 테스트
# =============================================================================
class TestInstantMealWeekdayCoef:
    """즉석식품 요일별 계수 테스트"""

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """요일별 계수 딕셔너리에 7개 요일 모두 존재"""
        assert len(INSTANT_MEAL_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in INSTANT_MEAL_WEEKDAY_COEF

    @pytest.mark.unit
    def test_wed_thu_slightly_higher(self):
        """수/목(2,3) 계수가 소폭 증가 (1.05)"""
        assert INSTANT_MEAL_WEEKDAY_COEF[2] == 1.05
        assert INSTANT_MEAL_WEEKDAY_COEF[3] == 1.05

    @pytest.mark.unit
    def test_sat_sun_slightly_lower(self):
        """토/일(5,6) 계수가 소폭 감소 (0.95)"""
        assert INSTANT_MEAL_WEEKDAY_COEF[5] == 0.95
        assert INSTANT_MEAL_WEEKDAY_COEF[6] == 0.95

    @pytest.mark.unit
    def test_mon_tue_fri_are_1_0(self):
        """월/화/금(0,1,4) 계수는 1.0"""
        assert INSTANT_MEAL_WEEKDAY_COEF[0] == 1.00
        assert INSTANT_MEAL_WEEKDAY_COEF[1] == 1.00
        assert INSTANT_MEAL_WEEKDAY_COEF[4] == 1.00


# =============================================================================
# analyze_instant_meal_pattern 테스트 (DB 의존)
# =============================================================================
class TestAnalyzeInstantMealPattern:
    """즉석식품 패턴 분석 테스트"""

    @pytest.fixture
    def instant_meal_db(self, tmp_path):
        """즉석식품 테스트 DB 생성"""
        db_file = tmp_path / "test_instant.db"
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
        conn.execute("""
            CREATE TABLE product_details (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT,
                order_unit_qty INTEGER DEFAULT 1,
                expiration_days INTEGER,
                orderable_day TEXT DEFAULT '일월화수목금토'
            )
        """)
        conn.execute("""
            CREATE TABLE products (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT
            )
        """)

        # 상품 정보 (유통기한 5일 - fresh)
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, expiration_days) "
            "VALUES (?, ?, ?, ?)",
            ("ITEM_FRESH_A", "냉장즉석A", "035", 5)
        )
        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("ITEM_FRESH_A", "냉장즉석A", "035")
        )

        # 상품 정보 (유통기한 60일 - shelf_stable)
        conn.execute(
            "INSERT INTO product_details (item_cd, item_nm, mid_cd, expiration_days) "
            "VALUES (?, ?, ?, ?)",
            ("ITEM_SHELF_B", "상온즉석B", "033", 60)
        )
        conn.execute(
            "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("ITEM_SHELF_B", "상온즉석B", "033")
        )

        # 30일치 판매 데이터 (일평균 약 4개)
        today = datetime.now()
        for item_cd in ["ITEM_FRESH_A", "ITEM_SHELF_B"]:
            for days_ago in range(30):
                date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                mid_cd = "035" if item_cd == "ITEM_FRESH_A" else "033"
                conn.execute(
                    "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                    "VALUES (?, ?, ?, ?)",
                    (item_cd, date, 4, mid_cd)
                )

        conn.commit()
        conn.close()
        return str(db_file)

    @pytest.mark.db
    def test_analyze_returns_dataclass(self, instant_meal_db):
        """analyze_instant_meal_pattern이 InstantMealPatternResult 반환"""
        result = analyze_instant_meal_pattern(
            "ITEM_FRESH_A", mid_cd="035", db_path=instant_meal_db
        )
        assert isinstance(result, InstantMealPatternResult)

    @pytest.mark.db
    def test_fresh_group_classification(self, instant_meal_db):
        """유통기한 5일 상품 → fresh 그룹, safety_days=0.5"""
        result = analyze_instant_meal_pattern(
            "ITEM_FRESH_A", mid_cd="035", db_path=instant_meal_db
        )
        assert result.expiry_group == "fresh"
        assert result.safety_days == 0.5
        assert result.expiration_days == 5

    @pytest.mark.db
    def test_shelf_stable_group_classification(self, instant_meal_db):
        """유통기한 60일 상품 → shelf_stable 그룹, safety_days=1.5"""
        result = analyze_instant_meal_pattern(
            "ITEM_SHELF_B", mid_cd="033", db_path=instant_meal_db
        )
        assert result.expiry_group == "shelf_stable"
        assert result.safety_days == 1.5
        assert result.expiration_days == 60

    @pytest.mark.db
    def test_fresh_max_stock_is_expiry_based(self, instant_meal_db):
        """fresh 그룹 max_stock = 유통기한(5일) x 일평균"""
        result = analyze_instant_meal_pattern(
            "ITEM_FRESH_A", mid_cd="035", db_path=instant_meal_db
        )
        expected_max = result.daily_avg * 5  # 유통기한 5일
        assert abs(result.max_stock - round(expected_max, 2)) < 0.01

    @pytest.mark.db
    def test_shelf_stable_max_stock_is_days_based(self, instant_meal_db):
        """shelf_stable 그룹 max_stock = max_stock_days(7.0) x 일평균"""
        result = analyze_instant_meal_pattern(
            "ITEM_SHELF_B", mid_cd="033", db_path=instant_meal_db
        )
        max_stock_days = INSTANT_EXPIRY_GROUPS["shelf_stable"]["max_stock_days"]
        expected_max = result.daily_avg * max_stock_days
        assert abs(result.max_stock - round(expected_max, 2)) < 0.01

    @pytest.mark.db
    def test_skip_order_when_over_max_stock(self, instant_meal_db):
        """재고+미입고 >= max_stock이면 skip_order=True"""
        result = analyze_instant_meal_pattern(
            "ITEM_FRESH_A", mid_cd="035", db_path=instant_meal_db,
            current_stock=100, pending_qty=100
        )
        assert result.skip_order is True
        assert "상한선 초과" in result.skip_reason

    @pytest.mark.db
    def test_no_skip_when_under_max_stock(self, instant_meal_db):
        """재고+미입고 < max_stock이면 skip_order=False"""
        result = analyze_instant_meal_pattern(
            "ITEM_FRESH_A", mid_cd="035", db_path=instant_meal_db,
            current_stock=0, pending_qty=0
        )
        assert result.skip_order is False
        assert result.skip_reason == ""

    @pytest.mark.db
    def test_unknown_item_fallback_to_chilled(self, instant_meal_db):
        """DB에 유통기한 없는 상품 → chilled 그룹으로 폴백"""
        result = analyze_instant_meal_pattern(
            "UNKNOWN_ITEM", mid_cd="027", db_path=instant_meal_db
        )
        assert result.expiry_group == "chilled"
        assert result.expiry_source == "fallback"
        assert result.expiration_days is None

    @pytest.mark.db
    def test_expiry_source_db(self, instant_meal_db):
        """DB에 유통기한 있으면 expiry_source='db'"""
        result = analyze_instant_meal_pattern(
            "ITEM_FRESH_A", mid_cd="035", db_path=instant_meal_db
        )
        assert result.expiry_source == "db"


# =============================================================================
# get_safety_stock_with_instant_meal_pattern 테스트
# =============================================================================
class TestGetSafetyStockWithInstantMealPattern:
    """통합 안전재고 계산 테스트"""

    @pytest.mark.unit
    def test_non_instant_meal_returns_default_fallback(self):
        """즉석식품이 아니면 default 모듈로 위임 (패턴=None)"""
        safety, pattern = get_safety_stock_with_instant_meal_pattern(
            mid_cd="049",  # 맥주 - 즉석식품 아님
            daily_avg=5.0,
            expiration_days=90,
        )
        assert pattern is None
        assert safety > 0

    @pytest.mark.unit
    def test_instant_meal_without_item_cd_uses_default_days(self):
        """item_cd 없이 즉석식품 카테고리 호출 시 기본 안전재고일수 적용"""
        default_days = INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        daily_avg = 5.0

        safety, pattern = get_safety_stock_with_instant_meal_pattern(
            mid_cd="027",
            daily_avg=daily_avg,
            expiration_days=5,
            item_cd=None,
        )
        assert pattern is None
        assert abs(safety - daily_avg * default_days) < 0.01

    @pytest.mark.unit
    def test_dynamic_config_enabled(self):
        """동적 안전재고 설정이 활성화 상태인지 확인"""
        assert INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG["enabled"] is True

    @pytest.mark.unit
    def test_dynamic_config_target_categories(self):
        """동적 안전재고 설정의 대상 카테고리 확인"""
        expected = ["027", "028", "031", "033", "035"]
        assert INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG["target_categories"] == expected
