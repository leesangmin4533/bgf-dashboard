"""
log_parser 단위 테스트
"""

import os
import tempfile
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from src.analysis.log_parser import (
    parse_line,
    stream_log_file,
    LogParser,
    LogEntry,
    PhaseInfo,
    SessionInfo,
    format_duration,
    format_entries,
    format_session_summary,
    format_phase_timeline,
    format_file_stats,
    LOG_LINE_RE,
    PHASE_RE,
    KNOWN_PHASES,
)


# ──────────────────────────────────────────────────────────────
# parse_line 단위 테스트
# ──────────────────────────────────────────────────────────────
class TestParseLine:

    def test_standard_info_line(self):
        line = "2026-02-21 07:00:04 | INFO     | src.scheduler.daily_job | Optimized flow started"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.timestamp == datetime(2026, 2, 21, 7, 0, 4)
        assert entry.level == "INFO"
        assert entry.module == "src.scheduler.daily_job"
        assert entry.message == "Optimized flow started"
        assert entry.line_number == 1

    def test_error_line(self):
        line = "2026-02-21 10:44:50 | ERROR    | src.order.auto_order | 입력 실패: 발주 불가"
        entry = parse_line(line, 99)
        assert entry is not None
        assert entry.level == "ERROR"
        assert entry.module == "src.order.auto_order"
        assert "입력 실패" in entry.message

    def test_warning_line(self):
        line = "2026-02-21 07:05:00 | WARNING  | run_scheduler | 재수집 불가"
        entry = parse_line(line, 5)
        assert entry is not None
        assert entry.level == "WARNING"

    def test_debug_line(self):
        line = "2026-02-21 07:05:00 | DEBUG    | src.db.models | Some debug info"
        entry = parse_line(line, 10)
        assert entry is not None
        assert entry.level == "DEBUG"

    def test_phase_detection(self):
        line = "2026-02-21 07:00:10 | INFO     | src.scheduler.daily_job | [Phase 1] Data Collection"
        entry = parse_line(line, 2)
        assert entry is not None
        assert entry.phase == "1"
        assert entry.phase_name == "Data Collection"

    def test_phase_decimal(self):
        line = "2026-02-21 07:01:00 | INFO     | src.scheduler.daily_job | [Phase 1.15] Waste Slip Collection (전날+당일)"
        entry = parse_line(line, 3)
        assert entry is not None
        assert entry.phase == "1.15"

    def test_no_phase(self):
        line = "2026-02-21 07:00:05 | INFO     | src.collectors.sales_collector | 수집 완료: 150건"
        entry = parse_line(line, 4)
        assert entry is not None
        assert entry.phase is None

    def test_malformed_line(self):
        assert parse_line("this is not a log line", 1) is None

    def test_empty_line(self):
        assert parse_line("", 1) is None

    def test_separator_line(self):
        assert parse_line("=" * 60, 1) is None

    def test_short_module_name(self):
        line = "2026-02-21 07:00:00 | INFO     | __main__ | Starting"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.module == "__main__"

    def test_message_with_pipe(self):
        line = "2026-02-21 07:00:00 | INFO     | src.test | value=10 | extra=20"
        entry = parse_line(line, 1)
        assert entry is not None
        assert "value=10 | extra=20" in entry.message


# ──────────────────────────────────────────────────────────────
# 정규식 패턴 테스트
# ──────────────────────────────────────────────────────────────
class TestPatterns:

    def test_log_line_regex(self):
        m = LOG_LINE_RE.match("2026-02-21 07:00:04 | INFO     | module.name | msg")
        assert m is not None
        assert m.group(1) == "2026-02-21 07:00:04"
        assert m.group(2).strip() == "INFO"
        assert m.group(3) == "module.name"
        assert m.group(4) == "msg"

    def test_phase_regex_simple(self):
        m = PHASE_RE.search("[Phase 1] Data Collection")
        assert m is not None
        assert m.group(1) == "1"
        assert m.group(2) == "Data Collection"

    def test_phase_regex_decimal(self):
        m = PHASE_RE.search("[Phase 1.55] Waste Cause Analysis")
        assert m is not None
        assert m.group(1) == "1.55"

    def test_all_known_phases(self):
        """모든 KNOWN_PHASES가 PHASE_RE에 매칭되는지"""
        for pid, pname in KNOWN_PHASES.items():
            text = f"[Phase {pid}] {pname}"
            m = PHASE_RE.search(text)
            assert m is not None, f"Failed for Phase {pid}: {pname}"
            assert m.group(1) == pid


# ──────────────────────────────────────────────────────────────
# format_duration 테스트
# ──────────────────────────────────────────────────────────────
class TestFormatDuration:

    def test_seconds(self):
        assert format_duration(5) == "5s"
        assert format_duration(59) == "59s"

    def test_minutes(self):
        assert format_duration(60) == "1m"
        assert format_duration(90) == "1m30s"
        assert format_duration(125) == "2m05s"

    def test_hours(self):
        assert format_duration(3600) == "1h00m"
        assert format_duration(3720) == "1h02m"

    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_negative(self):
        assert format_duration(-5) == "0s"


# ──────────────────────────────────────────────────────────────
# stream_log_file + LogParser 통합 테스트 (임시 파일)
# ──────────────────────────────────────────────────────────────

SAMPLE_LOG = """2026-02-20 23:59:00 | INFO     | __main__ | Previous day log
2026-02-21 07:00:00 | INFO     | __main__ | ============================================================
2026-02-21 07:00:01 | INFO     | src.scheduler.daily_job | Optimized flow started
2026-02-21 07:00:01 | INFO     | src.scheduler.daily_job | Dates: 2026-02-20, 2026-02-21
2026-02-21 07:00:01 | INFO     | src.scheduler.daily_job | Auto-order: True
2026-02-21 07:00:02 | INFO     | src.scheduler.daily_job | [Phase 1] Data Collection
2026-02-21 07:00:05 | INFO     | src.collectors.sales_collector | 수집 완료: 150건
2026-02-21 07:00:10 | INFO     | src.scheduler.daily_job | DB saved: 150 items
2026-02-21 07:02:30 | INFO     | src.scheduler.daily_job | [Phase 1.1] Receiving Collection
2026-02-21 07:02:35 | INFO     | src.collectors.receiving_collector | 입고 수집 완료
2026-02-21 07:02:50 | INFO     | src.scheduler.daily_job | [Phase 1.15] Waste Slip Collection (전날+당일)
2026-02-21 07:03:00 | WARNING  | src.collectors.waste_slip_collector | 전표 없음
2026-02-21 07:03:10 | INFO     | src.scheduler.daily_job | [Phase 2] Auto Order
2026-02-21 07:03:15 | INFO     | src.order.auto_order | 발주 시작 store=46513
2026-02-21 07:05:00 | ERROR    | src.order.auto_order | 입력 실패: 발주 불가
2026-02-21 07:05:05 | ERROR    | src.order.auto_order | 세션 만료
2026-02-21 07:06:00 | INFO     | src.scheduler.daily_job | [Phase 3] Fail Reason Collection
2026-02-21 07:06:10 | INFO     | src.scheduler.daily_job | 완료
2026-02-21 18:00:00 | INFO     | src.scheduler.daily_job | Optimized flow started
2026-02-21 18:00:01 | INFO     | src.scheduler.daily_job | [Phase 1] Data Collection
2026-02-21 18:00:10 | INFO     | src.scheduler.daily_job | 수집 완료
2026-02-22 07:00:00 | INFO     | __main__ | Next day start
""".strip()


@pytest.fixture
def sample_log_dir(tmp_path):
    """임시 로그 디렉토리 + 샘플 로그 파일"""
    log_file = tmp_path / "bgf_auto.log"
    log_file.write_text(SAMPLE_LOG, encoding="utf-8")
    return tmp_path


class TestStreamLogFile:

    def test_stream_all(self, sample_log_dir):
        entries = list(stream_log_file("main", log_dir=sample_log_dir))
        assert len(entries) > 0
        # 날짜가 범위 내
        for e in entries:
            assert e.timestamp.year == 2026

    def test_stream_date_filter(self, sample_log_dir):
        entries = list(stream_log_file("main", start_date="2026-02-21", end_date="2026-02-21", log_dir=sample_log_dir))
        assert len(entries) > 0
        for e in entries:
            assert e.timestamp.strftime("%Y-%m-%d") == "2026-02-21"

    def test_stream_date_stops_at_boundary(self, sample_log_dir):
        """end_date 이후 라인은 포함되지 않음"""
        entries = list(stream_log_file("main", start_date="2026-02-21", end_date="2026-02-21", log_dir=sample_log_dir))
        for e in entries:
            assert e.timestamp.date() == datetime(2026, 2, 21).date()

    def test_stream_nonexistent_file(self, sample_log_dir):
        entries = list(stream_log_file("prediction", log_dir=sample_log_dir))
        assert entries == []


class TestLogParserSessions:

    def test_list_sessions(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        sessions = parser.list_sessions("2026-02-21")
        # "Optimized flow started"가 2번 (07:00, 18:00)
        assert len(sessions) == 2

    def test_extract_session_morning(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "07:00")
        assert session.start_time is not None
        assert session.start_time.hour == 7
        assert session.total_errors == 2
        assert session.total_warnings == 1

    def test_extract_session_evening(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "18:00")
        assert session.start_time is not None
        assert session.start_time.hour == 18
        assert session.total_errors == 0

    def test_session_phases(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "07:00")
        phases = session.phases
        assert len(phases) >= 4  # Phase 1, 1.1, 1.15, 2, 3
        phase_ids = [p.phase_id for p in phases]
        assert "1" in phase_ids
        assert "2" in phase_ids
        assert "3" in phase_ids

    def test_session_not_found(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2099-01-01", "07:00")
        assert session.overall_status == "not_found"

    def test_session_store_id(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "07:00")
        assert session.store_id == "46513"

    def test_phase_error_count(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "07:00")
        phase2 = next((p for p in session.phases if p.phase_id == "2"), None)
        assert phase2 is not None
        assert phase2.error_count == 2
        assert phase2.status == "2 ERRORS"

    def test_phase_ok_status(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "07:00")
        phase1 = next((p for p in session.phases if p.phase_id == "1"), None)
        assert phase1 is not None
        assert phase1.status == "OK"


class TestLogParserPhases:

    def test_get_phase_summary(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        phases = parser.get_phase_summary("2026-02-21", "07:00")
        assert len(phases) >= 4

    def test_get_phase_logs(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.get_phase_logs("2026-02-21", "2", "07:00")
        assert len(entries) >= 2  # Phase 2 로그 (발주 시작 + 에러 2건)
        for e in entries:
            assert e.level in ("INFO", "ERROR", "WARNING")

    def test_get_phase_logs_empty(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.get_phase_logs("2026-02-21", "99")
        assert entries == []


class TestLogParserFilter:

    def test_filter_errors(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        errors = parser.filter_by_level("ERROR", start_date="2026-02-21", end_date="2026-02-21")
        assert len(errors) == 2
        for e in errors:
            assert e.level == "ERROR"

    def test_filter_errors_and_warnings(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.filter_by_level("ERROR,WARNING", start_date="2026-02-21", end_date="2026-02-21")
        assert len(entries) == 3  # 2 errors + 1 warning
        levels = {e.level for e in entries}
        assert "ERROR" in levels
        assert "WARNING" in levels

    def test_filter_max_results(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.filter_by_level("ERROR", start_date="2026-02-21", end_date="2026-02-21", max_results=1)
        assert len(entries) == 1


class TestLogParserSearch:

    def test_search_keyword(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.search("발주 불가", start_date="2026-02-21", end_date="2026-02-21")
        assert len(entries) >= 1
        assert "발주 불가" in entries[0].message

    def test_search_item_code(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.search("46513", start_date="2026-02-21", end_date="2026-02-21")
        assert len(entries) >= 1

    def test_search_case_insensitive(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.search("optimized flow", start_date="2026-02-21", end_date="2026-02-21")
        assert len(entries) >= 1

    def test_search_no_results(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.search("NONEXISTENT_STRING_XYZ", start_date="2026-02-21", end_date="2026-02-21")
        assert entries == []

    def test_search_max_results(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.search("INFO", start_date="2026-02-21", end_date="2026-02-21", max_results=3)
        assert len(entries) <= 3


class TestLogParserStats:

    def test_file_stats(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        stats = parser.get_file_stats("main")
        assert stats["exists"] is True
        assert stats["line_count"] > 0
        assert stats["size_mb"] >= 0
        assert stats["first_date"] is not None

    def test_file_stats_missing(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        stats = parser.get_file_stats("prediction")
        assert stats["exists"] is False

    def test_all_stats(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        all_stats = parser.get_all_stats()
        assert len(all_stats) == 5  # 5개 로그 파일


class TestLogParserTail:

    def test_tail(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.tail("main", 5)
        assert len(entries) <= 5
        assert len(entries) > 0

    def test_tail_missing_file(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        entries = parser.tail("prediction", 5)
        assert entries == []


# ──────────────────────────────────────────────────────────────
# 포맷팅 테스트
# ──────────────────────────────────────────────────────────────
class TestFormatting:

    def test_format_entries_empty(self):
        assert format_entries([]) == "(no results)"

    def test_format_entries_truncation(self):
        entries = [
            LogEntry(
                timestamp=datetime(2026, 2, 21, 7, 0, 0),
                level="INFO",
                module="test",
                message="msg" * 100,
                raw_line="",
                line_number=1,
            )
        ]
        result = format_entries(entries, truncate_message=50)
        assert "..." in result

    def test_format_entries_max_lines(self):
        entries = [
            LogEntry(
                timestamp=datetime(2026, 2, 21, 7, 0, i),
                level="INFO",
                module="test",
                message=f"line {i}",
                raw_line="",
                line_number=i,
            )
            for i in range(10)
        ]
        result = format_entries(entries, max_lines=3)
        assert "truncated" in result
        assert "3/10" in result

    def test_format_session_summary_not_found(self):
        session = SessionInfo(session_id="2099-01-01_07:00", overall_status="not_found")
        result = format_session_summary(session)
        assert "not found" in result

    def test_format_session_summary(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        session = parser.extract_session("2026-02-21", "07:00")
        result = format_session_summary(session)
        assert "Session:" in result
        assert "Phase Timeline:" in result
        assert "Data Collection" in result

    def test_format_phase_timeline_empty(self):
        assert "no phases" in format_phase_timeline([])

    def test_format_file_stats(self, sample_log_dir):
        parser = LogParser(log_dir=sample_log_dir)
        stats = parser.get_all_stats()
        result = format_file_stats(stats)
        assert "Log File Statistics" in result
        assert "bgf_auto.log" in result
