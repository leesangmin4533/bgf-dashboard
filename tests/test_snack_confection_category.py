"""
과자/간식(snack_confection) 카테고리 모듈 단위 테스트

대상: src/prediction/categories/snack_confection.py
- is_snack_confection_category(): 과자 카테고리 여부 확인 (014 디저트 제외)
- 회전율별 안전재고일수 (_get_turnover_level)
- max_stock_days, DEFAULT_WEEKDAY_COEF 상수 검증
- skip_order 로직
- get_safety_stock_with_snack_confection_pattern(): 비대상 fallback
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.categories.snack_confection import (
    is_snack_confection_category,
    _get_turnover_level,
    get_safety_stock_with_snack_confection_pattern,
    analyze_snack_confection_pattern,
    SnackConfectionPatternResult,
    SNACK_CONFECTION_TARGET_CATEGORIES,
    SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG,
    SNACK_SAFETY_CONFIG,
    DEFAULT_WEEKDAY_COEF,
)


# =============================================================================
# is_snack_confection_category 테스트
# =============================================================================
class TestIsSnackConfectionCategory:
    """과자/간식 카테고리 판별 테스트"""

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", ["015", "016", "017", "018", "019", "020", "029", "030"])
    def test_target_categories_return_true(self, mid_cd):
        """대상 카테고리(015~020, 029, 030)는 True 반환 (014 디저트 분리됨)"""
        assert is_snack_confection_category(mid_cd) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("mid_cd", [
        "001", "006", "014", "049", "050", "052", "072", "999", "", "14", "0014",
    ])
    def test_non_target_categories_return_false(self, mid_cd):
        """대상이 아닌 카테고리는 False 반환 (014 디저트 포함)"""
        assert is_snack_confection_category(mid_cd) is False

    @pytest.mark.unit
    def test_target_categories_constant_has_8_entries(self):
        """SNACK_CONFECTION_TARGET_CATEGORIES 상수에 8개 코드 존재 (014 디저트 분리)"""
        assert len(SNACK_CONFECTION_TARGET_CATEGORIES) == 8

    @pytest.mark.unit
    def test_target_categories_content_matches(self):
        """TARGET_CATEGORIES와 CONFIG의 target_categories 일치"""
        assert set(SNACK_CONFECTION_TARGET_CATEGORIES) == set(
            SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["target_categories"]
        )


# =============================================================================
# _get_turnover_level 테스트 (회전율별 안전재고)
# =============================================================================
class TestGetTurnoverLevel:
    """회전율 레벨 및 안전재고일수 결정 테스트"""

    @pytest.mark.unit
    def test_high_turnover_at_threshold(self):
        """일평균 5.0 이상: high 레벨, safety_days=1.5"""
        level, days = _get_turnover_level(5.0)
        assert level == "high"
        assert days == 1.2

    @pytest.mark.unit
    def test_high_turnover_above_threshold(self):
        """일평균 10.0: high 레벨"""
        level, days = _get_turnover_level(10.0)
        assert level == "high"
        assert days == 1.2

    @pytest.mark.unit
    def test_medium_turnover_at_threshold(self):
        """일평균 2.0: medium 레벨, safety_days=1.0"""
        level, days = _get_turnover_level(2.0)
        assert level == "medium"
        assert days == 0.8

    @pytest.mark.unit
    def test_medium_turnover_mid_range(self):
        """일평균 3.5: medium 레벨"""
        level, days = _get_turnover_level(3.5)
        assert level == "medium"
        assert days == 0.8

    @pytest.mark.unit
    def test_medium_turnover_just_below_high(self):
        """일평균 4.99: medium 레벨 (high 경계 미만)"""
        level, days = _get_turnover_level(4.99)
        assert level == "medium"
        assert days == 0.8

    @pytest.mark.unit
    def test_low_turnover_below_medium(self):
        """일평균 1.99: low 레벨, safety_days=0.6"""
        level, days = _get_turnover_level(1.99)
        assert level == "low"
        assert days == 0.6

    @pytest.mark.unit
    def test_low_turnover_zero(self):
        """일평균 0.0: low 레벨"""
        level, days = _get_turnover_level(0.0)
        assert level == "low"
        assert days == 0.6

    @pytest.mark.unit
    def test_low_turnover_very_small(self):
        """일평균 0.1: low 레벨"""
        level, days = _get_turnover_level(0.1)
        assert level == "low"
        assert days == 0.6

    @pytest.mark.unit
    def test_safety_config_high_min_daily_avg(self):
        """SNACK_SAFETY_CONFIG high_turnover 임계값 확인"""
        assert SNACK_SAFETY_CONFIG["high_turnover"]["min_daily_avg"] == 5.0
        assert SNACK_SAFETY_CONFIG["high_turnover"]["safety_days"] == 1.2

    @pytest.mark.unit
    def test_safety_config_medium_min_daily_avg(self):
        """SNACK_SAFETY_CONFIG medium_turnover 임계값 확인"""
        assert SNACK_SAFETY_CONFIG["medium_turnover"]["min_daily_avg"] == 2.0
        assert SNACK_SAFETY_CONFIG["medium_turnover"]["safety_days"] == 0.8

    @pytest.mark.unit
    def test_safety_config_low_min_daily_avg(self):
        """SNACK_SAFETY_CONFIG low_turnover 임계값 확인"""
        assert SNACK_SAFETY_CONFIG["low_turnover"]["min_daily_avg"] == 0.0
        assert SNACK_SAFETY_CONFIG["low_turnover"]["safety_days"] == 0.6


# =============================================================================
# DEFAULT_WEEKDAY_COEF 및 max_stock_days 상수 테스트
# =============================================================================
class TestSnackConfectionConstants:
    """과자/간식 상수 검증"""

    @pytest.mark.unit
    def test_max_stock_days_is_5(self):
        """max_stock_days가 5.0일인지 확인 (7.0→5.0 축소)"""
        assert SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["max_stock_days"] == 5.0

    @pytest.mark.unit
    def test_default_safety_days(self):
        """default_safety_days가 0.8일인지 확인 (1.0→0.8 축소)"""
        assert SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["default_safety_days"] == 0.8

    @pytest.mark.unit
    def test_weekday_coef_has_7_entries(self):
        """DEFAULT_WEEKDAY_COEF에 7개 요일 모두 존재"""
        assert len(DEFAULT_WEEKDAY_COEF) == 7
        for day in range(7):
            assert day in DEFAULT_WEEKDAY_COEF

    @pytest.mark.unit
    def test_weekday_coef_values(self):
        """요일별 계수가 올바른 값인지 확인"""
        assert DEFAULT_WEEKDAY_COEF[0] == 1.06  # 월
        assert DEFAULT_WEEKDAY_COEF[1] == 0.99  # 화
        assert DEFAULT_WEEKDAY_COEF[2] == 1.04  # 수
        assert DEFAULT_WEEKDAY_COEF[3] == 0.84  # 목
        assert DEFAULT_WEEKDAY_COEF[4] == 1.01  # 금

    @pytest.mark.unit
    def test_weekday_coef_weekend(self):
        """주말(토, 일) 계수 확인"""
        assert DEFAULT_WEEKDAY_COEF[5] == 1.20  # 토 (1.34→1.20 보수적 축소)
        assert DEFAULT_WEEKDAY_COEF[6] == 0.74  # 일

    @pytest.mark.unit
    def test_weekday_coef_all_positive(self):
        """모든 요일 계수가 양수"""
        for day in range(7):
            assert DEFAULT_WEEKDAY_COEF[day] > 0

    @pytest.mark.unit
    def test_analysis_days_is_30(self):
        """분석 기간이 30일"""
        assert SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["analysis_days"] == 30

    @pytest.mark.unit
    def test_min_data_days_is_14(self):
        """최소 데이터 일수가 14일"""
        assert SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["min_data_days"] == 14


# =============================================================================
# skip_order 로직 테스트
# =============================================================================
class TestSnackConfectionSkipOrder:
    """발주 스킵 로직 테스트 (DB 의존)"""

    @pytest.mark.db
    def test_skip_when_stock_exceeds_max(self, in_memory_db):
        """재고+미입고 >= 상한선이면 skip_order=True"""
        # 30일 판매 데이터 삽입 (일평균 약 3개)
        from datetime import datetime, timedelta
        today = datetime.now()
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("SNACK001", date, 3, "015")
            )
        in_memory_db.commit()

        # daily_avg ~= 3, max_stock = 3 * 5 = 15
        # current_stock(15) + pending(10) = 25 >= 15 → skip
        db_path = ":memory:"
        # in_memory_db를 파일로 복사하여 사용
        import sqlite3
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_snack_confection_pattern(
                item_cd="SNACK001",
                mid_cd="015",
                db_path=tmp.name,
                current_stock=15,
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
        today = datetime.now()
        for i in range(30):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            in_memory_db.execute(
                "INSERT OR REPLACE INTO daily_sales (item_cd, sales_date, sale_qty, mid_cd) "
                "VALUES (?, ?, ?, ?)",
                ("SNACK002", date, 3, "017")
            )
        in_memory_db.commit()

        import sqlite3, tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        file_conn = sqlite3.connect(tmp.name)
        in_memory_db.backup(file_conn)
        file_conn.close()

        try:
            result = analyze_snack_confection_pattern(
                item_cd="SNACK002",
                mid_cd="017",
                db_path=tmp.name,
                current_stock=2,
                pending_qty=1,
            )
            assert result.skip_order is False
            assert result.skip_reason == ""
        finally:
            os.unlink(tmp.name)


# =============================================================================
# get_safety_stock_with_snack_confection_pattern 비대상 fallback 테스트
# =============================================================================
class TestSnackConfectionFallback:
    """비대상 카테고리 fallback 테스트"""

    @pytest.mark.unit
    def test_non_snack_returns_none_pattern(self):
        """과자 카테고리가 아니면 pattern=None 반환"""
        with patch(
            "src.prediction.categories.snack_confection.is_snack_confection_category",
            return_value=False,
        ):
            with patch(
                "src.prediction.categories.default.get_safety_stock_days",
                return_value=2.0,
            ):
                safety, pattern = get_safety_stock_with_snack_confection_pattern(
                    mid_cd="001",
                    daily_avg=5.0,
                    expiration_days=1,
                )
                assert pattern is None
                assert safety == 5.0 * 2.0  # daily_avg * default_safety_days

    @pytest.mark.unit
    def test_snack_without_item_cd_uses_default_days(self):
        """item_cd 없으면 default_safety_days 적용"""
        safety, pattern = get_safety_stock_with_snack_confection_pattern(
            mid_cd="015",
            daily_avg=4.0,
            expiration_days=30,
            item_cd=None,
        )
        expected = 4.0 * SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG["default_safety_days"]
        assert safety == expected
        assert pattern is None


# =============================================================================
# SnackConfectionPatternResult dataclass 테스트
# =============================================================================
class TestSnackConfectionPatternResult:
    """데이터클래스 구조 검증"""

    @pytest.mark.unit
    def test_dataclass_fields(self):
        """SnackConfectionPatternResult 필드 존재 확인"""
        result = SnackConfectionPatternResult(
            item_cd="TEST001",
            mid_cd="015",
            daily_avg=3.0,
            turnover_level="medium",
            safety_days=1.5,
            final_safety_stock=4.5,
            max_stock=21.0,
            skip_order=False,
            skip_reason="",
            weekday_coef=1.0,
            orderable_day="월화수목금토",
            order_interval=2,
            is_orderable_today=True,
        )
        assert result.item_cd == "TEST001"
        assert result.mid_cd == "015"
        assert result.daily_avg == 3.0
        assert result.turnover_level == "medium"
        assert result.safety_days == 1.5
        assert result.final_safety_stock == 4.5
        assert result.max_stock == 21.0
        assert result.skip_order is False
        assert result.skip_reason == ""
        assert result.weekday_coef == 1.0
        assert result.orderable_day == "월화수목금토"
        assert result.order_interval == 2
        assert result.is_orderable_today is True
