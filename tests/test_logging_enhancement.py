# -*- coding: utf-8 -*-
"""
logging-enhancement PDCA 테스트

검증 대상:
  1. waste_cause_analyzer.py: silent exception -> logger 호출 검증
  2. waste_report.py: silent return -> logger 호출 검증
  3. logger.py: log_with_context() 유틸리티 검증
  4. print() 제거 검증 (grep 기반)
"""

import logging
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# =========================================================================
# 1. log_with_context() 유틸리티 테스트
# =========================================================================


class TestLogWithContext:
    """log_with_context() 헬퍼 함수 테스트"""

    def test_basic_context(self):
        """기본 컨텍스트 키=값 포맷 검증"""
        from src.utils.logger import log_with_context

        mock_logger = MagicMock()
        log_with_context(
            mock_logger, "warning", "DB 조회 실패",
            item_cd="123", store_id="S001"
        )
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args[0][0]
        assert "DB 조회 실패" in msg
        assert "item_cd=123" in msg
        assert "store_id=S001" in msg

    def test_no_context(self):
        """컨텍스트 없이 호출 시 메시지만 출력"""
        from src.utils.logger import log_with_context

        mock_logger = MagicMock()
        log_with_context(mock_logger, "info", "작업 완료")
        mock_logger.info.assert_called_once_with("작업 완료", exc_info=False)

    def test_none_values_excluded(self):
        """None 값 컨텍스트 제외 검증"""
        from src.utils.logger import log_with_context

        mock_logger = MagicMock()
        log_with_context(
            mock_logger, "debug", "테스트",
            item_cd="123", store_id=None
        )
        msg = mock_logger.debug.call_args[0][0]
        assert "item_cd=123" in msg
        assert "store_id" not in msg

    def test_exc_info_passed(self):
        """exc_info 전달 검증"""
        from src.utils.logger import log_with_context

        mock_logger = MagicMock()
        log_with_context(mock_logger, "error", "에러", exc_info=True, phase="1.55")
        mock_logger.error.assert_called_once()
        call_kwargs = mock_logger.error.call_args[1]
        assert call_kwargs["exc_info"] is True

    def test_invalid_level_fallback(self):
        """존재하지 않는 레벨 -> info 폴백"""
        from src.utils.logger import log_with_context

        mock_logger = MagicMock()
        mock_logger.nonexistent = None  # getattr 폴백 테스트
        log_with_context(mock_logger, "nonexistent", "테스트")
        mock_logger.info.assert_called_once()


# =========================================================================
# 2. waste_cause_analyzer.py: silent exception -> logger 검증
# =========================================================================


class TestWasteCauseAnalyzerLogging:
    """waste_cause_analyzer.py의 silent exception이 로깅되는지 검증"""

    @patch("src.analysis.waste_cause_analyzer.DBRouter")
    def test_product_details_failure_logs_warning(self, mock_router):
        """product_details 조회 실패 시 logger.warning 호출"""
        from src.analysis.waste_cause_analyzer import WasteCauseAnalyzer

        mock_common = MagicMock()
        mock_common.execute.side_effect = Exception("DB locked")
        mock_router.get_common_connection.return_value = mock_common

        mock_store = MagicMock()
        mock_store.execute.return_value.fetchone.return_value = None
        mock_store.execute.return_value.fetchall.return_value = []
        mock_router.get_store_connection.return_value = mock_store

        analyzer = WasteCauseAnalyzer(store_id="TEST01", params={"enabled": True})

        with patch("src.analysis.waste_cause_analyzer.logger") as mock_logger:
            ctx = analyzer._gather_context("ITEM001", "001", "2026-02-23")
            mock_logger.warning.assert_called()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "product_details" in warning_msg
            assert "ITEM001" in warning_msg

    @patch("src.analysis.waste_cause_analyzer.DBRouter")
    def test_weather_failure_logs_debug(self, mock_router):
        """기온 데이터 조회 실패 시 logger.debug 호출"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("table not found")
        mock_router.get_common_connection.return_value = mock_conn

        from src.analysis.waste_cause_analyzer import WasteCauseAnalyzer

        analyzer = WasteCauseAnalyzer(store_id="TEST01", params={})

        with patch("src.analysis.waste_cause_analyzer.logger") as mock_logger:
            result = analyzer._get_weather_context("2026-02-23")
            assert result is None
            mock_logger.debug.assert_called()
            assert "기온 데이터" in mock_logger.debug.call_args[0][0]

    @patch("src.analysis.waste_cause_analyzer.DBRouter")
    def test_promo_failure_logs_debug(self, mock_router):
        """프로모션 컨텍스트 조회 실패 시 logger.debug 호출"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("no such table")
        mock_router.get_store_connection.return_value = mock_conn

        from src.analysis.waste_cause_analyzer import WasteCauseAnalyzer

        analyzer = WasteCauseAnalyzer(store_id="TEST01", params={})

        with patch("src.analysis.waste_cause_analyzer.logger") as mock_logger:
            result = analyzer._get_promo_context("ITEM001", "2026-02-23")
            assert result is None
            mock_logger.debug.assert_called()
            debug_msg = mock_logger.debug.call_args[0][0]
            assert "ITEM001" in debug_msg
            assert "TEST01" in debug_msg

    @patch("src.analysis.waste_cause_analyzer.DBRouter")
    def test_holiday_failure_logs_debug(self, mock_router):
        """휴일 정보 조회 실패 시 logger.debug 호출"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("connection refused")
        mock_router.get_common_connection.return_value = mock_conn

        from src.analysis.waste_cause_analyzer import WasteCauseAnalyzer

        analyzer = WasteCauseAnalyzer(store_id="TEST01", params={})

        with patch("src.analysis.waste_cause_analyzer.logger") as mock_logger:
            result = analyzer._get_holiday_context("2026-02-23")
            assert result is None
            mock_logger.debug.assert_called()
            assert "휴일" in mock_logger.debug.call_args[0][0]

    def test_feedback_adjuster_load_params_logs_warning(self):
        """WasteFeedbackAdjuster._load_params() 실패 시 logger.warning"""
        from src.analysis.waste_cause_analyzer import WasteFeedbackAdjuster

        with patch("src.analysis.waste_cause_analyzer.logger") as mock_logger:
            with patch("builtins.open", side_effect=PermissionError("access denied")):
                with patch("pathlib.Path.exists", return_value=True):
                    adjuster = WasteFeedbackAdjuster.__new__(WasteFeedbackAdjuster)
                    result = adjuster._load_params()
                    assert result == {}
                    mock_logger.warning.assert_called()
                    assert "eval_params.json" in mock_logger.warning.call_args[0][0]


# =========================================================================
# 3. waste_report.py: silent return -> logger 검증
# =========================================================================


class TestWasteReportLogging:
    """waste_report.py의 silent return이 로깅되는지 검증"""

    @patch("src.analysis.waste_report.WasteReportGenerator._get_store_conn")
    def test_food_waste_for_date_logs_warning(self, mock_get_conn):
        """_get_food_waste_for_date() 실패 시 logger.warning"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("SQL error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from src.analysis.waste_report import WasteReportGenerator

        gen = WasteReportGenerator.__new__(WasteReportGenerator)
        gen.store_id = "TEST01"

        with patch("src.analysis.waste_report.logger") as mock_logger:
            result = gen._get_food_waste_for_date("2026-02-23")
            assert result == []
            mock_logger.warning.assert_called()
            assert "TEST01" in mock_logger.warning.call_args[0][0]

    @patch("src.analysis.waste_report.WasteReportGenerator._get_store_conn")
    def test_food_waste_summary_logs_warning(self, mock_get_conn):
        """_get_food_waste_summary() 실패 시 logger.warning"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("SQL error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from src.analysis.waste_report import WasteReportGenerator

        gen = WasteReportGenerator.__new__(WasteReportGenerator)
        gen.store_id = "TEST01"

        with patch("src.analysis.waste_report.logger") as mock_logger:
            result = gen._get_food_waste_summary(days_back=7)
            assert result == []
            mock_logger.warning.assert_called()
            assert "TEST01" in mock_logger.warning.call_args[0][0]

    @patch("src.analysis.waste_report.WasteReportGenerator._get_store_conn")
    def test_weekly_waste_trend_logs_warning(self, mock_get_conn):
        """_get_weekly_waste_trend() 실패 시 logger.warning"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("timeout")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from src.analysis.waste_report import WasteReportGenerator

        gen = WasteReportGenerator.__new__(WasteReportGenerator)
        gen.store_id = "TEST01"

        with patch("src.analysis.waste_report.logger") as mock_logger:
            result = gen._get_weekly_waste_trend(weeks=12)
            assert result == []
            mock_logger.warning.assert_called()

    @patch("src.analysis.waste_report.WasteReportGenerator._get_store_conn")
    def test_top_waste_items_logs_warning(self, mock_get_conn):
        """_get_top_waste_items() 실패 시 logger.warning"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from src.analysis.waste_report import WasteReportGenerator

        gen = WasteReportGenerator.__new__(WasteReportGenerator)
        gen.store_id = "TEST01"

        with patch("src.analysis.waste_report.logger") as mock_logger:
            result = gen._get_top_waste_items(limit=10)
            assert result == []
            mock_logger.warning.assert_called()


# =========================================================================
# 4. print() 제거 검증 (grep 기반 정적 분석)
# =========================================================================


class TestNoPrintInProduction:
    """프로덕션 코드에서 print() 제거 검증 (비-__main__ 블록)"""

    @pytest.fixture
    def src_root(self):
        return Path(__file__).parent.parent / "src"

    def _find_print_outside_main(self, filepath: Path) -> list:
        """__main__ 블록 밖의 print() 호출을 찾는다."""
        if not filepath.exists():
            return []

        lines = filepath.read_text(encoding="utf-8").split("\n")
        in_main_block = False
        results = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("if __name__"):
                in_main_block = True
            if not in_main_block and re.match(r'\s+print\(', line):
                results.append((i, line.strip()))

        return results

    def test_daily_report_no_print(self, src_root):
        """daily_report.py에 비-__main__ print() 없음"""
        hits = self._find_print_outside_main(src_root / "analysis" / "daily_report.py")
        assert hits == [], f"print() found at: {hits}"

    def test_trend_report_no_print(self, src_root):
        """trend_report.py에 비-__main__ print() 없음"""
        hits = self._find_print_outside_main(src_root / "analysis" / "trend_report.py")
        assert hits == [], f"print() found at: {hits}"

    def test_store_manager_no_print(self, src_root):
        """store_manager.py에 비-__main__ print() 없음"""
        hits = self._find_print_outside_main(src_root / "config" / "store_manager.py")
        assert hits == [], f"print() found at: {hits}"

    def test_waste_cause_analyzer_no_silent_pass(self, src_root):
        """waste_cause_analyzer.py에 silent 'except: pass' 없음"""
        filepath = src_root / "analysis" / "waste_cause_analyzer.py"
        content = filepath.read_text(encoding="utf-8")
        # except Exception: 바로 다음에 pass만 있는 패턴 검색
        silent_passes = re.findall(r'except\s+Exception\s*:\s*\n\s+pass\b', content)
        assert silent_passes == [], f"Silent pass blocks found: {len(silent_passes)}"

    def test_waste_report_no_silent_return(self, src_root):
        """waste_report.py에 silent 'except Exception:\\n return []' 없음"""
        filepath = src_root / "analysis" / "waste_report.py"
        content = filepath.read_text(encoding="utf-8")
        # except Exception: 바로 다음에 return []만 있는 패턴
        silent_returns = re.findall(r'except\s+Exception\s*:\s*\n\s+return\s+\[\]', content)
        assert silent_returns == [], f"Silent return blocks found: {len(silent_returns)}"
