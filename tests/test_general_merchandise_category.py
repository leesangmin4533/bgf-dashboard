"""
잡화/비식품(general_merchandise) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/general_merchandise.py
- is_general_merchandise_category(): 잡화 카테고리 여부 확인 (14개 코드)
- safety_days: 1.0
- max_stock_days: 3.0 (매우 엄격)
- ultra_low_threshold: 0.3 (일평균 < 0.3 → safety_stock=0)
- min_stock: 1
- 요일계수 미적용 (항상 1.0)
- skip_order 로직
- 극소량(ultra_low) 특수 처리 검증
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch

from src.prediction.categories.general_merchandise import (
    is_general_merchandise_category,
    get_safety_stock_with_general_merchandise_pattern,
    analyze_general_merchandise_pattern,
    calculate_general_merchandise_dynamic_safety,
    GeneralMerchandisePatternResult,
    GENERAL_MERCHANDISE_CATEGORIES,
    GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG,
    DEFAULT_WEEKDAY_COEF,
)


# =============================================================================
# is_general_merchandise_category 테스트
# =============================================================================
class TestIsGeneralMerchandiseCategory:
    """잡화/비식품 카테고리 판별 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "054", "055", "058", "059", "061", "062", "063", "064",
        "066", "067", "068", "069", "070", "071",
    ])
    def test_all_14_target_categories_return_true(self, mid_cd):
        """14개 대상 카테고리 모두 True 반환"""
        assert is_general_merchandise_category(mid_cd) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "001", "049", "050", "052", "053", "056", "060", "065",
        "072", "073", "999", "", "54", "0054",
    ])
    def test_non_target_categories_return_false(self, mid_cd):
        """대상이 아닌 카테고리는 False 반환"""
        assert is_general_merchandise_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_has_14_entries(self):
        """GENERAL_MERCHANDISE_CATEGORIES에 14개 코드 존재"""
        assert len(GENERAL_MERCHANDISE_CATEGORIES) == 14

    @pytest.mark.unit
    def test_target_categories_content_matches_config(self):
        """CATEGORIES와 CONFIG의 target_categories 일치"""
        assert set(GENERAL_MERCHANDISE_CATEGORIES) == set(
            GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["target_categories"]
        )

    @pytest.mark.unit
    def test_gap_codes_not_included(self):
        """060, 065는 잡화 범위(054-071)이지만 대상에 미포함"""
        assert is_general_merchandise_category("060") is False
        assert is_general_merchandise_category("065") is False


# =============================================================================
# safety_days, max_stock_days, ultra_low_threshold 상수 테스트
# =============================================================================
class TestGeneralMerchandiseConstants:
    """잡화/비식품 상수 검증"""

    @pytest.mark.unit
    def test_safety_days_is_1(self):
        """default_safety_days가 1.0"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["default_safety_days"] == 1.0

    @pytest.mark.unit
    def test_max_stock_days_is_3(self):
        """max_stock_days가 3.0 (매우 엄격)"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 3.0

    @pytest.mark.unit
    def test_ultra_low_threshold_is_0_3(self):
        """ultra_low_threshold가 0.3"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["ultra_low_threshold"] == 0.3

    @pytest.mark.unit
    def test_min_stock_is_1(self):
        """min_stock이 1"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["min_stock"] == 1

    @pytest.mark.unit
    def test_max_stock_enabled_is_true(self):
        """max_stock_enabled가 True"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["max_stock_enabled"] is True

    @pytest.mark.unit
    def test_analysis_days_is_30(self):
        """분석 기간이 30일"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["analysis_days"] == 30

    @pytest.mark.unit
    def test_min_data_days_is_7(self):
        """최소 데이터 일수가 7일 (잡화는 데이터 부족하므로 낮은 임계값)"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["min_data_days"] == 7

    @pytest.mark.unit
    def test_max_stock_days_stricter_than_others(self):
        """max_stock_days(3.0)가 다른 카테고리(7~14일)보다 엄격"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["max_stock_days"] <= 3.0


# =============================================================================
# DEFAULT_WEEKDAY_COEF 테스트 (요일계수 미적용)
# =============================================================================
class TestGeneralMerchandiseWeekdayCoef:
    """요일계수 검증 (항상 1.0)"""

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """DEFAULT_WEEKDAY_COEF에 7개 요일 모두 존재"""
        assert len(DEFAULT_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in DEFAULT_WEEKDAY_COEF

    @pytest.mark.unit
    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5, 6])
    def test_all_weekday_coefs_are_1_00(self, weekday):
        """모든 요일 계수가 1.00 (요일계수 미적용)"""
        assert DEFAULT_WEEKDAY_COEF[weekday] == 1.00

    @pytest.mark.unit
    def test_all_coefs_equal_no_pattern(self):
        """모든 요일 계수가 동일 (데이터 부족 → 노이즈 방지)"""
        values = list(DEFAULT_WEEKDAY_COEF.values())
        assert len(set(values)) == 1
        assert values[0] == 1.00


# =============================================================================
# skip_order 로직 테스트
# =============================================================================
class TestGeneralMerchandiseSkipOrder:
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
                ("MERCH001", date, 2, "061")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            # daily_avg ~= 2, max_stock = 2 * 3 = 6
            # current_stock(5) + pending(3) = 8 >= 6 → skip
            result = analyze_general_merchandise_pattern(
                item_cd="MERCH001",
                mid_cd="061",
                db_path=tmp.name,
                current_stock=5,
                pending_qty=3,
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
                ("MERCH002", date, 2, "062")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_general_merchandise_pattern(
                item_cd="MERCH002",
                mid_cd="062",
                db_path=tmp.name,
                current_stock=1,
                pending_qty=0,
            )
            assert result.skip_order is False
            assert result.skip_reason == ""
        finally:
            os.unlink(tmp.name)


# =============================================================================
# 극소량(ultra_low) 특수 처리 테스트
# =============================================================================
class TestGeneralMerchandiseUltraLow:
    """극소량 판매(일평균 < 0.3) 특수 처리 테스트"""

    @pytest.mark.db
    def test_ultra_low_safety_stock_is_zero(self, in_memory_db):
        """일평균 < 0.3이면 final_safety_stock=0"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        # 극소량 판매: 30일간 총 3개 → 일평균 0.1
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            qty = 1 if i % 10 == 0 else 0
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("MERCH_LOW", date, qty, "067")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_general_merchandise_pattern(
                item_cd="MERCH_LOW",
                mid_cd="067",
                db_path=tmp.name,
                current_stock=1,
                pending_qty=0,
            )
            assert result.is_ultra_low is True
            assert result.final_safety_stock == 0.0
        finally:
            os.unlink(tmp.name)

    @pytest.mark.db
    def test_ultra_low_zero_stock_force_order(self, in_memory_db):
        """극소량이고 현재재고=0, 미입고=0이면 1개 강제 발주"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            qty = 1 if i % 10 == 0 else 0
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("MERCH_FORCE", date, qty, "069")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            safety, pattern = calculate_general_merchandise_dynamic_safety(
                item_cd="MERCH_FORCE",
                mid_cd="069",
                db_path=tmp.name,
                current_stock=0,
                pending_qty=0,
            )
            assert pattern.is_ultra_low is True
            assert pattern.skip_order is False
            assert pattern.final_safety_stock == 1.0  # min_stock 강제 적용
            assert safety == 1.0
        finally:
            os.unlink(tmp.name)

    @pytest.mark.db
    def test_non_ultra_low_normal_safety(self, in_memory_db):
        """일평균 >= 0.3이면 정상 안전재고 (is_ultra_low=False)"""
        from datetime import datetime, timedelta
        import sqlite3, tempfile, os

        today = datetime.now()
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("MERCH_NORM", date, 2, "055")
            )
        in_memory_db.commit()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_general_merchandise_pattern(
                item_cd="MERCH_NORM",
                mid_cd="055",
                db_path=tmp.name,
                current_stock=0,
                pending_qty=0,
            )
            assert result.is_ultra_low is False
            assert result.daily_avg >= 0.3
            # safety = daily_avg * 1.0
            assert result.final_safety_stock > 0
        finally:
            os.unlink(tmp.name)

    @pytest.mark.unit
    def test_ultra_low_threshold_value(self):
        """ultra_low_threshold가 0.3"""
        assert GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["ultra_low_threshold"] == 0.3


# =============================================================================
# get_safety_stock_with_general_merchandise_pattern 비대상 fallback 테스트
# =============================================================================
class TestGeneralMerchandiseFallback:
    """비대상 카테고리 fallback 테스트"""

    @pytest.mark.unit
    def test_non_merchandise_returns_none_pattern(self):
        """잡화가 아닌 카테고리는 pattern=None 반환"""
        with patch(
            "src.prediction.categories.general_merchandise.is_general_merchandise_category",
            return_value=False,
        ):
            with patch(
                "src.prediction.categories.default.get_safety_stock_days",
                return_value=2.0,
            ):
                safety, pattern = get_safety_stock_with_general_merchandise_pattern(
                    mid_cd="001",
                    daily_avg=5.0,
                    expiration_days=1,
                )
                assert pattern is None
                assert safety == 5.0 * 2.0

    @pytest.mark.unit
    def test_merchandise_without_item_cd_normal(self):
        """item_cd 없고 daily_avg >= 0.3이면 default_safety_days(1.0) 적용"""
        safety, pattern = get_safety_stock_with_general_merchandise_pattern(
            mid_cd="061",
            daily_avg=2.0,
            expiration_days=365,
            item_cd=None,
        )
        expected = 2.0 * GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        assert safety == expected
        assert pattern is None

    @pytest.mark.unit
    def test_merchandise_without_item_cd_ultra_low(self):
        """item_cd 없고 daily_avg < 0.3이면 safety=0"""
        safety, pattern = get_safety_stock_with_general_merchandise_pattern(
            mid_cd="070",
            daily_avg=0.1,
            expiration_days=365,
            item_cd=None,
        )
        assert safety == 0.0
        assert pattern is None


# =============================================================================
# GeneralMerchandisePatternResult dataclass 테스트
# =============================================================================
class TestGeneralMerchandisePatternResult:
    """데이터클래스 구조 검증"""

    @pytest.mark.unit
    def test_dataclass_fields(self):
        """GeneralMerchandisePatternResult 필드 존재 및 값 확인"""
        result = GeneralMerchandisePatternResult(
            item_cd="MERCH001",
            mid_cd="061",
            daily_avg=0.5,
            safety_days=1.0,
            final_safety_stock=0.5,
            min_stock=1,
            max_stock=1.5,
            skip_order=False,
            skip_reason="",
            is_ultra_low=False,
        )
        assert result.item_cd == "MERCH001"
        assert result.mid_cd == "061"
        assert result.daily_avg == 0.5
        assert result.safety_days == 1.0
        assert result.final_safety_stock == 0.5
        assert result.min_stock == 1
        assert result.max_stock == 1.5
        assert result.skip_order is False
        assert result.is_ultra_low is False

    @pytest.mark.unit
    def test_dataclass_ultra_low_true(self):
        """is_ultra_low=True인 결과 객체 생성 확인"""
        result = GeneralMerchandisePatternResult(
            item_cd="MERCH_UL",
            mid_cd="067",
            daily_avg=0.1,
            safety_days=1.0,
            final_safety_stock=0.0,
            min_stock=1,
            max_stock=1.0,
            skip_order=False,
            skip_reason="",
            is_ultra_low=True,
        )
        assert result.is_ultra_low is True
        assert result.final_safety_stock == 0.0
