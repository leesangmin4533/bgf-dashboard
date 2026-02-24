"""
스낵류(015~030) 발주가능요일 기반 예측 테스트

대상: src/prediction/categories/snack_confection.py
- _is_orderable_today(): 오늘 발주 가능 여부
- _calculate_order_interval(): 발주간격 계산
- 발주간격 기반 안전재고 계산
- 비발주일 스킵 로직
- SNACK_DEFAULT_ORDERABLE_DAYS 상수
- SnackConfectionPatternResult 신규 필드
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch
from datetime import datetime

from src.prediction.categories.snack_confection import (
    _is_orderable_today,
    _calculate_order_interval,
    analyze_snack_confection_pattern,
    get_safety_stock_with_snack_confection_pattern,
    SnackConfectionPatternResult,
    SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG,
)
from src.settings.constants import SNACK_DEFAULT_ORDERABLE_DAYS


# =============================================================================
# _is_orderable_today 테스트 (FR-01)
# =============================================================================
class TestIsOrderableToday:
    """오늘 발주 가능 여부 판별 테스트"""

    @pytest.mark.unit
    def test_orderable_on_matching_day(self):
        """화요일에 '화목토' -> True"""
        # 화요일 = weekday 1
        with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _is_orderable_today("화목토") is True

    @pytest.mark.unit
    def test_not_orderable_on_non_matching_day(self):
        """수요일에 '화목토' -> False"""
        with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 25)  # 수요일
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _is_orderable_today("화목토") is False

    @pytest.mark.unit
    def test_empty_orderable_day_returns_true(self):
        """빈 문자열 -> True (안전 폴백)"""
        assert _is_orderable_today("") is True

    @pytest.mark.unit
    def test_none_orderable_day_returns_true(self):
        """None -> True (안전 폴백)"""
        assert _is_orderable_today(None) is True

    @pytest.mark.unit
    def test_full_week_always_orderable(self):
        """'월화수목금토' -> 월~토 항상 True"""
        for weekday_num in range(6):  # 월(0) ~ 토(5)
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 23 + weekday_num)  # 월~토
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                assert _is_orderable_today("월화수목금토") is True

    @pytest.mark.unit
    def test_sunday_excluded(self):
        """일요일에 '월화수목금토' -> False"""
        with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 1)  # 일요일
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _is_orderable_today("월화수목금토") is False


# =============================================================================
# _calculate_order_interval 테스트 (FR-02)
# =============================================================================
class TestCalculateOrderInterval:
    """발주간격(최대 갭) 계산 테스트"""

    @pytest.mark.unit
    def test_tue_thu_sat_interval(self):
        """'화목토' -> 최대간격 3 (토->화)"""
        assert _calculate_order_interval("화목토") == 3

    @pytest.mark.unit
    def test_mon_wed_fri_interval(self):
        """'월수금' -> 최대간격 3 (금->월)"""
        assert _calculate_order_interval("월수금") == 3

    @pytest.mark.unit
    def test_daily_interval(self):
        """'월화수목금토' -> 최대간격 2 (토->월)"""
        assert _calculate_order_interval("월화수목금토") == 2

    @pytest.mark.unit
    def test_mon_thu_interval(self):
        """'월목' -> 최대간격 4 (목->월)"""
        assert _calculate_order_interval("월목") == 4

    @pytest.mark.unit
    def test_once_a_week(self):
        """'화' -> 최대간격 7 (주 1회)"""
        assert _calculate_order_interval("화") == 7

    @pytest.mark.unit
    def test_empty_returns_default(self):
        """'' -> 기본값 2"""
        assert _calculate_order_interval("") == 2

    @pytest.mark.unit
    def test_none_returns_default(self):
        """None -> 기본값 2"""
        assert _calculate_order_interval(None) == 2

    @pytest.mark.unit
    def test_full_week_with_sunday(self):
        """'일월화수목금토' -> 최대간격 1 (매일)"""
        assert _calculate_order_interval("일월화수목금토") == 1

    @pytest.mark.unit
    def test_alternate_days(self):
        """'월수금일' -> 최대간격 2"""
        assert _calculate_order_interval("월수금일") == 2


# =============================================================================
# 발주간격 기반 안전재고 테스트 (FR-02)
# =============================================================================
class TestSnackSafetyWithOrderInterval:
    """발주간격 기반 안전재고 계산 테스트"""

    @pytest.mark.unit
    def test_safety_stock_uses_interval(self):
        """간격 3(화목토) + 일평균 3 -> 안전재고 ~= 3 x 3 x weekday_coef"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    # 30일 데이터, 총 90개 판매 (일평균 3)
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK001",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="화목토",
                    )
                    # safety = daily_avg(3) x interval(3) x weekday_coef(1.0) = 9.0
                    assert result.order_interval == 3
                    assert result.safety_days == 3.0
                    assert result.final_safety_stock == 9.0

    @pytest.mark.unit
    def test_safety_stock_with_weekday_coef(self):
        """간격 2 x 일평균 3 x 요일계수 1.2 = 7.2"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.2 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK002",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="월화수목금토",
                    )
                    # interval=2, daily=3, coef=1.2 -> 3*2*1.2 = 7.2
                    assert result.order_interval == 2
                    assert result.final_safety_stock == 7.2

    @pytest.mark.unit
    def test_max_stock_still_applies(self):
        """max_stock(5일분) 상한은 유지됨"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)  # 일평균 3

                    # max_stock = 3 * 5.0 = 15
                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK003",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="화목토",
                        current_stock=10,
                        pending_qty=6,
                    )
                    # stock(10) + pending(6) = 16 >= max_stock(15) -> skip
                    assert result.skip_order is True
                    assert "상한선 초과" in result.skip_reason


# =============================================================================
# 비발주일 스킵 테스트 (FR-01)
# =============================================================================
class TestSnackNonOrderableSkip:
    """비발주일 발주 스킵 테스트"""

    @pytest.mark.unit
    def test_skip_on_non_orderable_day(self):
        """수요일에 '화목토' -> skip_order=True"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 25)  # 수요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK004",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="화목토",
                    )
                    assert result.skip_order is True
                    assert result.is_orderable_today is False

    @pytest.mark.unit
    def test_skip_reason_contains_orderable_day(self):
        """스킵 사유에 orderable_day 정보 포함"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 25)  # 수요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK005",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="화목토",
                    )
                    assert "비발주일" in result.skip_reason
                    assert "화목토" in result.skip_reason

    @pytest.mark.unit
    def test_no_skip_on_orderable_day(self):
        """화요일에 '화목토' -> skip_order=False (재고 부족 시)"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK006",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="화목토",
                        current_stock=0,
                        pending_qty=0,
                    )
                    assert result.skip_order is False
                    assert result.is_orderable_today is True


# =============================================================================
# SNACK_DEFAULT_ORDERABLE_DAYS 상수 테스트 (FR-03)
# =============================================================================
class TestSnackDefaultOrderableDays:
    """스낵류 기본 발주가능요일 상수 테스트"""

    @pytest.mark.unit
    def test_constant_value(self):
        """SNACK_DEFAULT_ORDERABLE_DAYS == '월화수목금토'"""
        assert SNACK_DEFAULT_ORDERABLE_DAYS == "월화수목금토"

    @pytest.mark.unit
    def test_no_sunday(self):
        """일요일 미포함"""
        assert "일" not in SNACK_DEFAULT_ORDERABLE_DAYS

    @pytest.mark.unit
    def test_fallback_when_empty(self):
        """orderable_day=None -> SNACK_DEFAULT_ORDERABLE_DAYS 적용"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK007",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day=None,
                    )
                    assert result.orderable_day == SNACK_DEFAULT_ORDERABLE_DAYS

    @pytest.mark.unit
    def test_db_value_priority(self):
        """DB값 '화목토' -> DB값 우선"""
        with patch("src.prediction.categories.snack_confection._learn_weekday_pattern") as mock_learn:
            mock_learn.return_value = {i: 1.0 for i in range(7)}
            with patch("src.prediction.categories.snack_confection.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 2, 24)  # 화요일
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                with patch("src.prediction.categories.snack_confection.sqlite3") as mock_sql:
                    mock_conn = mock_sql.connect.return_value
                    mock_cursor = mock_conn.cursor.return_value
                    mock_cursor.fetchone.return_value = (30, 90)

                    result = analyze_snack_confection_pattern(
                        item_cd="SNACK008",
                        mid_cd="016",
                        db_path="/fake/db",
                        orderable_day="화목토",
                    )
                    assert result.orderable_day == "화목토"


# =============================================================================
# SnackConfectionPatternResult 신규 필드 테스트
# =============================================================================
class TestSnackDataclassFields:
    """dataclass 신규 필드 검증"""

    @pytest.mark.unit
    def test_new_fields_exist(self):
        """orderable_day, order_interval, is_orderable_today 필드 존재"""
        result = SnackConfectionPatternResult(
            item_cd="TEST001",
            mid_cd="016",
            daily_avg=3.0,
            turnover_level="medium",
            safety_days=3.0,
            final_safety_stock=9.0,
            max_stock=15.0,
            skip_order=False,
            skip_reason="",
            weekday_coef=1.0,
            orderable_day="화목토",
            order_interval=3,
            is_orderable_today=True,
        )
        assert result.orderable_day == "화목토"
        assert result.order_interval == 3
        assert result.is_orderable_today is True
