"""
라면(ramen) 발주가능요일 기반 예측 테스트

대상: src/prediction/categories/ramen.py (orderable_day 로직)
- _is_orderable_today(): 발주가능요일 확인 (snack_confection에서 import)
- _calculate_order_interval(): 발주간격 계산 (snack_confection에서 import)
- analyze_ramen_pattern(): orderable_day 기반 안전재고
- RamenPatternResult: 신규 필드 (skip_reason, orderable_day, order_interval, is_orderable_today)
"""

import pytest
from unittest.mock import patch
from datetime import datetime

from src.prediction.categories.ramen import (
    RamenPatternResult,
    analyze_ramen_pattern,
    get_safety_stock_with_ramen_pattern,
    RAMEN_DYNAMIC_SAFETY_CONFIG,
)
from src.prediction.categories.snack_confection import (
    _is_orderable_today,
    _calculate_order_interval,
)
from src.settings.constants import RAMEN_DEFAULT_ORDERABLE_DAYS


# =============================================================================
# TestRamenIsOrderableToday: 발주가능요일 확인 (snack_confection 공용 함수)
# =============================================================================
class TestRamenIsOrderableToday:
    """라면에서 사용하는 _is_orderable_today 테스트"""

    @pytest.mark.unit
    def test_orderable_on_matching_day(self):
        """발주일에 True 반환"""
        # 월요일(0)일 때 "월화수목금토" → True
        with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 23, 10, 0)  # 월요일
            assert _is_orderable_today("월화수목금토") is True

    @pytest.mark.unit
    def test_not_orderable_on_non_matching_day(self):
        """비발주일에 False 반환"""
        # 일요일(6)일 때 "월화수목금토" → False
        with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 22, 10, 0)  # 일요일
            assert _is_orderable_today("월화수목금토") is False

    @pytest.mark.unit
    def test_empty_orderable_day_returns_true(self):
        """빈 문자열 → True (안전 폴백)"""
        assert _is_orderable_today("") is True

    @pytest.mark.unit
    def test_none_orderable_day_returns_true(self):
        """None → True (안전 폴백)"""
        assert _is_orderable_today(None) is True


# =============================================================================
# TestRamenCalculateOrderInterval: 발주간격 계산
# =============================================================================
class TestRamenCalculateOrderInterval:
    """라면에서 사용하는 _calculate_order_interval 테스트"""

    @pytest.mark.unit
    def test_tue_thu_sat_interval(self):
        """'화목토' → 간격 3일 (토→화 = 3일)"""
        assert _calculate_order_interval("화목토") == 3

    @pytest.mark.unit
    def test_mon_wed_fri_interval(self):
        """'월수금' → 간격 3일 (금→월 = 3일)"""
        assert _calculate_order_interval("월수금") == 3

    @pytest.mark.unit
    def test_daily_except_sunday_interval(self):
        """'월화수목금토' → 간격 2일 (토→월 = 2일)"""
        assert _calculate_order_interval("월화수목금토") == 2

    @pytest.mark.unit
    def test_mon_thu_interval(self):
        """'월목' → 간격 4일 (목→월 = 4일)"""
        assert _calculate_order_interval("월목") == 4

    @pytest.mark.unit
    def test_once_a_week(self):
        """'화' → 간격 7일 (주 1회)"""
        assert _calculate_order_interval("화") == 7

    @pytest.mark.unit
    def test_empty_returns_default(self):
        """빈 문자열 → 기본값 2"""
        assert _calculate_order_interval("") == 2


# =============================================================================
# TestRamenSafetyWithOrderInterval: 발주간격 기반 안전재고
# =============================================================================
class TestRamenSafetyWithOrderInterval:
    """안전재고 = daily_avg x order_interval 확인"""

    @pytest.mark.unit
    def test_safety_stock_uses_interval(self):
        """safety_stock = daily_avg x order_interval"""
        # 화목토 → interval=3, daily_avg=5.0 → safety=15.0
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql:
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            # 30일 데이터, 일평균 5.0 → total_sales=150
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 5, 10) for d in range(1, 31)
            ]
            result = analyze_ramen_pattern(
                "TEST001", db_path=":memory:", store_id="S001",
                orderable_day="화목토"
            )
            assert result.order_interval == 3
            assert result.safety_days == 3.0
            assert result.final_safety_stock == result.daily_avg * 3

    @pytest.mark.unit
    def test_safety_stock_with_daily_schedule(self):
        """'월화수목금토' → interval=2, safety=daily_avg x 2"""
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql:
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 4, 8) for d in range(1, 31)
            ]
            result = analyze_ramen_pattern(
                "TEST002", db_path=":memory:", store_id="S001",
                orderable_day="월화수목금토"
            )
            assert result.order_interval == 2
            assert result.safety_days == 2.0
            assert result.final_safety_stock == result.daily_avg * 2

    @pytest.mark.unit
    def test_max_stock_still_applies(self):
        """max_stock_days(4.0) 상한선 유지 확인"""
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql:
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 10, 50) for d in range(1, 31)
            ]
            result = analyze_ramen_pattern(
                "TEST003", db_path=":memory:", store_id="S001",
                orderable_day="화목토", current_stock=50, pending_qty=0,
            )
            # daily_avg=10, max_stock=10*4=40, current_stock=50 >= 40 → skip
            assert result.max_stock == 40.0
            assert result.skip_order is True
            assert "상한선 초과" in result.skip_reason


# =============================================================================
# TestRamenNonOrderableSkip: 비발주일 스킵
# =============================================================================
class TestRamenNonOrderableSkip:
    """비발주일 발주 스킵 동작 확인"""

    @pytest.mark.unit
    def test_skip_on_non_orderable_day(self):
        """비발주일 → skip_order=True"""
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql, \
             patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 22, 10, 0)  # 일요일
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 5, 10) for d in range(1, 31)
            ]
            result = analyze_ramen_pattern(
                "TEST004", db_path=":memory:", store_id="S001",
                orderable_day="월화수목금토"
            )
            assert result.skip_order is True
            assert result.is_orderable_today is False
            assert "비발주일" in result.skip_reason

    @pytest.mark.unit
    def test_no_skip_on_orderable_day(self):
        """발주일 → skip_order=False (재고 상한 미초과 시)"""
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql, \
             patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 23, 10, 0)  # 월요일
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 3, 5) for d in range(1, 31)
            ]
            result = analyze_ramen_pattern(
                "TEST005", db_path=":memory:", store_id="S001",
                orderable_day="월화수목금토", current_stock=0
            )
            assert result.skip_order is False
            assert result.is_orderable_today is True
            assert result.skip_reason == ""

    @pytest.mark.unit
    def test_skip_reason_contains_orderable_day(self):
        """skip_reason에 orderable_day 문자열 포함"""
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql, \
             patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 22, 10, 0)  # 일요일
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 5, 10) for d in range(1, 31)
            ]
            result = analyze_ramen_pattern(
                "TEST006", db_path=":memory:", store_id="S001",
                orderable_day="월화수목금토"
            )
            assert "월화수목금토" in result.skip_reason


# =============================================================================
# TestRamenDefaultOrderableDays: 기본 발주가능요일 상수
# =============================================================================
class TestRamenDefaultOrderableDays:
    """RAMEN_DEFAULT_ORDERABLE_DAYS 상수 확인"""

    @pytest.mark.unit
    def test_constant_value(self):
        """기본값 '월화수목금토' 확인"""
        assert RAMEN_DEFAULT_ORDERABLE_DAYS == "월화수목금토"

    @pytest.mark.unit
    def test_no_sunday(self):
        """일요일 미포함 확인"""
        assert "일" not in RAMEN_DEFAULT_ORDERABLE_DAYS

    @pytest.mark.unit
    def test_db_value_priority(self):
        """DB orderable_day 값이 기본값보다 우선"""
        with patch("src.prediction.categories.ramen.sqlite3") as mock_sql:
            mock_conn = mock_sql.connect.return_value
            mock_cursor = mock_conn.cursor.return_value
            mock_cursor.fetchall.return_value = [
                (f"2026-02-{d:02d}", 3, 5) for d in range(1, 31)
            ]
            # "화목토" 명시 → RAMEN_DEFAULT_ORDERABLE_DAYS 대신 "화목토" 적용
            result = analyze_ramen_pattern(
                "TEST007", db_path=":memory:", store_id="S001",
                orderable_day="화목토"
            )
            assert result.orderable_day == "화목토"
            assert result.order_interval == 3


# =============================================================================
# TestRamenDataclassFields: RamenPatternResult 신규 필드
# =============================================================================
class TestRamenDataclassFields:
    """RamenPatternResult 신규 필드 존재 확인"""

    @pytest.mark.unit
    def test_new_fields_exist(self):
        """orderable_day, order_interval, is_orderable_today, skip_reason 필드 확인"""
        result = RamenPatternResult(
            item_cd="TEST",
            actual_data_days=30,
            analysis_days=30,
            has_enough_data=True,
            total_sales=150,
            daily_avg=5.0,
            turnover_level="high",
            safety_days=2.0,
            current_stock=10,
            max_stock=20.0,
            is_over_max_stock=False,
            final_safety_stock=10.0,
            skip_order=False,
            skip_reason="",
            orderable_day="월화수목금토",
            order_interval=2,
            is_orderable_today=True,
        )
        assert result.skip_reason == ""
        assert result.orderable_day == "월화수목금토"
        assert result.order_interval == 2
        assert result.is_orderable_today is True
