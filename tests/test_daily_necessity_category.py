"""
생활용품(daily_necessity) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/daily_necessity.py
- is_daily_necessity_category(): 생활용품 카테고리 여부 확인
- safety_days: 2.0 고정
- min_stock: 1 (결품 방지)
- max_stock_days: 14.0
- DEFAULT_WEEKDAY_COEF: 모두 1.00 (요일 패턴 없음)
- skip_order 로직
- get_safety_stock_with_daily_necessity_pattern(): 비대상 fallback
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch

from src.prediction.categories.daily_necessity import (
    is_daily_necessity_category,
    get_safety_stock_with_daily_necessity_pattern,
    analyze_daily_necessity_pattern,
    calculate_daily_necessity_dynamic_safety,
    DailyNecessityPatternResult,
    DAILY_NECESSITY_CATEGORIES,
    DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG,
    DEFAULT_WEEKDAY_COEF,
)


# =============================================================================
# is_daily_necessity_category 테스트
# =============================================================================
class TestIsDailyNecessityCategory:
    """생활용품 카테고리 판별 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", ["036", "037", "056", "057", "086"])
    def test_target_categories_return_true(self, mid_cd):
        """대상 카테고리(036, 037, 056, 057, 086)는 True 반환"""
        assert is_daily_necessity_category(mid_cd) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "001", "049", "050", "052", "054", "072", "999", "", "36", "0036",
    ])
    def test_non_target_categories_return_false(self, mid_cd):
        """대상이 아닌 카테고리는 False 반환"""
        assert is_daily_necessity_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_has_5_entries(self):
        """DAILY_NECESSITY_CATEGORIES에 5개 코드 존재"""
        assert len(DAILY_NECESSITY_CATEGORIES) == 5

    @pytest.mark.unit
    def test_target_categories_content_matches_config(self):
        """DAILY_NECESSITY_CATEGORIES와 CONFIG의 target_categories 일치"""
        assert set(DAILY_NECESSITY_CATEGORIES) == set(
            DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["target_categories"]
        )

    @pytest.mark.unit
    def test_category_036_is_medical(self):
        """036(의약외품) 확인"""
        assert is_daily_necessity_category("036") is True

    @pytest.mark.unit
    def test_category_086_is_safety_medicine(self):
        """086(안전상비의약품) 확인"""
        assert is_daily_necessity_category("086") is True


# =============================================================================
# safety_days, min_stock, max_stock_days 상수 테스트
# =============================================================================
class TestDailyNecessityConstants:
    """생활용품 상수 검증"""

    @pytest.mark.unit
    def test_safety_days_is_1_5(self):
        """default_safety_days가 1.5 (고정)"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["default_safety_days"] == 1.5

    @pytest.mark.unit
    def test_min_stock_is_1(self):
        """min_stock이 1 (결품 방지)"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["min_stock"] == 1

    @pytest.mark.unit
    def test_max_stock_days_is_10(self):
        """max_stock_days가 10.0"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 10.0

    @pytest.mark.unit
    def test_max_stock_enabled_is_true(self):
        """max_stock_enabled가 True"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["max_stock_enabled"] is True

    @pytest.mark.unit
    def test_analysis_days_is_30(self):
        """분석 기간이 30일"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["analysis_days"] == 30

    @pytest.mark.unit
    def test_min_data_days_is_14(self):
        """최소 데이터 일수가 14일"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["min_data_days"] == 14

    @pytest.mark.unit
    def test_enabled_is_true(self):
        """enabled가 True"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["enabled"] is True


# =============================================================================
# DEFAULT_WEEKDAY_COEF 테스트 (요일 패턴 없음 - 전부 1.00)
# =============================================================================
class TestDailyNecessityWeekdayCoef:
    """요일계수 검증 (균일 1.00)"""

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """DEFAULT_WEEKDAY_COEF에 7개 요일 모두 존재"""
        assert len(DEFAULT_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in DEFAULT_WEEKDAY_COEF

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5, 6])
    def test_all_weekday_coefs_are_1_00(self, weekday):
        """모든 요일 계수가 1.00 (요일 패턴 없음)"""
        assert DEFAULT_WEEKDAY_COEF[weekday] == 1.00

    @pytest.mark.unit
    def test_no_weekend_premium(self):
        """주말에도 프리미엄 없음 (생활용품 특성)"""
        assert DEFAULT_WEEKDAY_COEF[5] == DEFAULT_WEEKDAY_COEF[0]  # 토 == 월
        assert DEFAULT_WEEKDAY_COEF[6] == DEFAULT_WEEKDAY_COEF[0]  # 일 == 월

    @pytest.mark.unit
    def test_all_coefs_equal(self):
        """모든 요일 계수가 동일"""
        values = list(DEFAULT_WEEKDAY_COEF.values())
        assert len(set(values)) == 1  # 모든 값이 같음


# =============================================================================
# skip_order 로직 테스트
# =============================================================================
class TestDailyNecessitySkipOrder:
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
                ("DAILY001", date, 2, "036")
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
            result = analyze_daily_necessity_pattern(
                item_cd="DAILY001",
                mid_cd="036",
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
                ("DAILY002", date, 2, "057")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_daily_necessity_pattern(
                item_cd="DAILY002",
                mid_cd="057",
                db_path=tmp.name,
                current_stock=3,
                pending_qty=1,
            )
            assert result.skip_order is False
            assert result.skip_reason == ""
        finally:
            os.unlink(tmp.name)

    @pytest.mark.db
    def test_zero_stock_overrides_skip(self, in_memory_db):
        """현재재고 0이면 skip_order가 해제됨 (결품 방지 최우선)"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        # 극소량 판매 데이터 (일평균 ~0.5)
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            qty = 1 if i % 2 == 0 else 0
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("DAILY003", date, qty, "037")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            # calculate_daily_necessity_dynamic_safety는 current_stock=0이면
            # skip_order를 해제하는 특수 로직이 있음
            safety, pattern = calculate_daily_necessity_dynamic_safety(
                item_cd="DAILY003",
                mid_cd="037",
                db_path=tmp.name,
                current_stock=0,
                pending_qty=0,
            )
            # 현재재고 0 → skip 해제
            assert pattern.skip_order is False
        finally:
            os.unlink(tmp.name)


# =============================================================================
# min_stock 보장 테스트
# =============================================================================
class TestDailyNecessityMinStock:
    """최소 보유 수량 보장 테스트"""

    @pytest.mark.db
    def test_min_stock_guaranteed_for_low_sales(self, in_memory_db):
        """판매량이 적어도 final_safety_stock >= 1"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        # 매우 적은 판매 (일평균 ~0.1)
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            qty = 1 if i % 10 == 0 else 0
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("DAILY004", date, qty, "056")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_daily_necessity_pattern(
                item_cd="DAILY004",
                mid_cd="056",
                db_path=tmp.name,
                current_stock=0,
                pending_qty=0,
            )
            # min_stock=1이므로 final_safety_stock >= 1.0
            assert result.final_safety_stock >= 1.0
            assert result.min_stock == 1
        finally:
            os.unlink(tmp.name)

    @pytest.mark.unit
    def test_min_stock_value_in_config(self):
        """CONFIG의 min_stock이 1"""
        assert DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["min_stock"] == 1


# =============================================================================
# get_safety_stock_with_daily_necessity_pattern 비대상 fallback 테스트
# =============================================================================
class TestDailyNecessityFallback:
    """비대상 카테고리 fallback 테스트"""

    @pytest.mark.unit
    def test_non_necessity_returns_none_pattern(self):
        """생활용품이 아닌 카테고리는 pattern=None 반환"""
        with patch(
            "src.prediction.categories.daily_necessity.is_daily_necessity_category",
            return_value=False,
        ):
            with patch(
                "src.prediction.categories.default.get_safety_stock_days",
                return_value=1.5,
            ):
                safety, pattern = get_safety_stock_with_daily_necessity_pattern(
                    mid_cd="001",
                    daily_avg=10.0,
                    expiration_days=1,
                )
                assert pattern is None
                assert safety == 10.0 * 1.5

    @pytest.mark.unit
    def test_necessity_without_item_cd_uses_default_days(self):
        """item_cd 없으면 default_safety_days(1.5) 적용"""
        safety, pattern = get_safety_stock_with_daily_necessity_pattern(
            mid_cd="036",
            daily_avg=3.0,
            expiration_days=365,
            item_cd=None,
        )
        expected = 3.0 * DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        assert safety == expected
        assert pattern is None

    @pytest.mark.unit
    def test_necessity_without_item_cd_min_stock_guarantee(self):
        """item_cd 없고 판매량 극소일 때도 min_stock(1) 보장"""
        safety, pattern = get_safety_stock_with_daily_necessity_pattern(
            mid_cd="057",
            daily_avg=0.1,
            expiration_days=365,
            item_cd=None,
        )
        # 0.1 * 1.5 = 0.15 < 1 → min_stock=1로 올림
        assert safety >= 1.0
        assert pattern is None


# =============================================================================
# DailyNecessityPatternResult dataclass 테스트
# =============================================================================
class TestDailyNecessityPatternResult:
    """데이터클래스 구조 검증"""

    @pytest.mark.unit
    def test_dataclass_fields(self):
        """DailyNecessityPatternResult 필드 존재 및 값 확인"""
        result = DailyNecessityPatternResult(
            item_cd="DAILY001",
            mid_cd="036",
            daily_avg=2.5,
            safety_days=2.0,
            final_safety_stock=5.0,
            min_stock=1,
            max_stock=35.0,
            skip_order=False,
            skip_reason="",
            weekday_coef=1.0,
            data_days=28,
        )
        assert result.item_cd == "DAILY001"
        assert result.mid_cd == "036"
        assert result.daily_avg == 2.5
        assert result.safety_days == 2.0
        assert result.final_safety_stock == 5.0
        assert result.min_stock == 1
        assert result.max_stock == 35.0
        assert result.skip_order is False
        assert result.weekday_coef == 1.0
        assert result.data_days == 28
