"""
로그 역추적성 강화 테스트 — Session ID, 배치 마커, log_parser 호환성
"""

import logging
import re
import threading
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────
# Session ID 라이프사이클
# ──────────────────────────────────────────────────────────────

class TestSessionId:
    """Session ID set/get/clear 라이프사이클"""

    def test_default_session_id(self):
        """set 호출 전 기본값은 '--------'"""
        from src.utils.logger import clear_session_id, get_session_id
        clear_session_id()
        assert get_session_id() == "--------"

    def test_set_session_id_auto(self):
        """None으로 호출하면 8자 hex 자동 생성"""
        from src.utils.logger import clear_session_id, set_session_id
        clear_session_id()
        sid = set_session_id()
        assert len(sid) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", sid)

    def test_set_session_id_manual(self):
        """수동 지정 시 그대로 반환"""
        from src.utils.logger import clear_session_id, get_session_id, set_session_id
        clear_session_id()
        set_session_id("abcd1234")
        assert get_session_id() == "abcd1234"

    def test_clear_session_id(self):
        """clear 후 기본값 복귀"""
        from src.utils.logger import clear_session_id, get_session_id, set_session_id
        set_session_id("test1234")
        assert get_session_id() == "test1234"
        clear_session_id()
        assert get_session_id() == "--------"

    def test_session_id_thread_isolation(self):
        """서로 다른 스레드에서 독립적인 session_id"""
        from src.utils.logger import clear_session_id, get_session_id, set_session_id
        clear_session_id()
        set_session_id("main0001")

        child_sid = [None]

        def child():
            child_sid[0] = get_session_id()

        t = threading.Thread(target=child)
        t.start()
        t.join()

        assert get_session_id() == "main0001"
        # 자식 스레드는 설정 안 했으므로 기본값
        assert child_sid[0] == "--------"
        clear_session_id()


# ──────────────────────────────────────────────────────────────
# SessionFilter
# ──────────────────────────────────────────────────────────────

class TestSessionFilter:
    """SessionFilter 로그 레코드 주입"""

    def test_filter_injects_session_id(self):
        """Filter가 record.session_id를 주입"""
        from src.utils.logger import SessionFilter, clear_session_id, set_session_id
        clear_session_id()
        set_session_id("filt1234")

        f = SessionFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        result = f.filter(record)
        assert result is True
        assert record.session_id == "filt1234"
        clear_session_id()

    def test_filter_default_when_no_session(self):
        """세션 미설정 시 기본값 주입"""
        from src.utils.logger import SessionFilter, clear_session_id
        clear_session_id()

        f = SessionFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        f.filter(record)
        assert record.session_id == "--------"

    def test_log_format_includes_session_id(self):
        """LOG_FORMAT에 session_id가 포함되어 포맷팅 가능"""
        from src.utils.logger import (
            DATE_FORMAT, LOG_FORMAT, SessionFilter, clear_session_id, set_session_id,
        )
        clear_session_id()
        set_session_id("fmt12345")

        formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
        f = SessionFilter()

        record = logging.LogRecord(
            name="test.module", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        f.filter(record)
        formatted = formatter.format(record)

        assert "fmt12345" in formatted
        assert "test.module" in formatted
        assert "test message" in formatted
        clear_session_id()


# ──────────────────────────────────────────────────────────────
# log_parser 새 포맷 호환성
# ──────────────────────────────────────────────────────────────

class TestLogParserFormat:
    """log_parser의 새/이전 포맷 파싱"""

    def test_parse_new_format_with_session_id(self):
        """session_id가 포함된 새 포맷 파싱"""
        from src.analysis.log_parser import parse_line
        line = "2026-02-28 13:24:41 | INFO     | a1b2c3d4 | src.order.direct_api_saver | dataset 채우기 완료: 29건"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.session_id == "a1b2c3d4"
        assert entry.module == "src.order.direct_api_saver"
        assert "dataset 채우기 완료" in entry.message

    def test_parse_old_format_without_session_id(self):
        """session_id가 없는 이전 포맷도 정상 파싱 (하위 호환)"""
        from src.analysis.log_parser import parse_line
        line = "2026-02-28 07:00:04 | INFO     | src.scheduler.daily_job | [Phase 1] Data Collection"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.session_id is None
        assert entry.module == "src.scheduler.daily_job"
        assert entry.phase == "1"

    def test_parse_new_format_with_dashes(self):
        """session_id가 '--------' (기본값)인 경우"""
        from src.analysis.log_parser import parse_line
        line = "2026-02-28 13:00:00 | WARNING  | -------- | src.utils.logger | test"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.session_id == "--------"
        assert entry.module == "src.utils.logger"

    def test_parse_new_format_phase_detection(self):
        """새 포맷에서도 Phase 감지"""
        from src.analysis.log_parser import parse_line
        line = "2026-02-28 13:17:29 | INFO     | abcd5678 | src.scheduler.daily_job | [Phase 2] Auto Order"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.session_id == "abcd5678"
        assert entry.phase == "2"
        assert "Auto Order" in entry.phase_name

    def test_parse_error_level(self):
        """ERROR 레벨 파싱"""
        from src.analysis.log_parser import parse_line
        line = "2026-02-28 14:00:00 | ERROR    | 12345678 | src.order.auto_order | 발주 실패"
        entry = parse_line(line, 1)
        assert entry is not None
        assert entry.level == "ERROR"
        assert entry.session_id == "12345678"


# ──────────────────────────────────────────────────────────────
# 배치 마커
# ──────────────────────────────────────────────────────────────

class TestBatchMarker:
    """direct_api_saver 배치 마커 로그"""

    def test_single_batch_marker(self):
        """단일 배치에 [batch=B001] 마커"""
        from src.order.direct_api_saver import DirectApiOrderSaver

        saver = DirectApiOrderSaver.__new__(DirectApiOrderSaver)
        saver.driver = MagicMock()
        saver.max_batch = 50
        saver.dry_run = False
        saver._save_template = None
        saver._save_endpoint = None
        saver._template_data = None

        orders = [{"item_cd": f"item_{i}", "qty": 1} for i in range(3)]

        with patch.object(saver, '_save_via_transaction', return_value=MagicMock(
            success=True, saved_count=3, elapsed_ms=100, method='direct_api',
            message='ok',
        )), \
             patch('src.order.direct_api_saver.logger') as mock_logger:
            import time
            saver._save_single_batch(orders, "2026-03-01", time.time())

            # [batch=B001] 마커가 로그에 포함되어야 함
            log_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("batch=B001" in call for call in log_calls), \
                f"Expected [batch=B001] in log calls: {log_calls}"

    def test_chunked_batch_markers(self):
        """분할 배치에 B001, B002 마커"""
        from src.order.direct_api_saver import DirectApiOrderSaver

        saver = DirectApiOrderSaver.__new__(DirectApiOrderSaver)
        saver.driver = MagicMock()
        saver.max_batch = 5
        saver.dry_run = False
        saver._save_template = None
        saver._save_endpoint = None

        orders = [{"item_cd": f"item_{i}", "qty": 1} for i in range(8)]

        mock_result = MagicMock(
            success=True, saved_count=5, elapsed_ms=50,
            method='direct_api', message='ok',
        )

        import time as _time

        with patch.object(saver, '_save_via_transaction', return_value=mock_result), \
             patch('src.order.direct_api_saver.logger') as mock_logger, \
             patch('src.order.direct_api_saver.time') as mock_time:
            mock_time.time.return_value = _time.time()
            mock_time.sleep = MagicMock()
            saver._save_chunked(orders, "2026-03-01", _time.time())

            log_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("batch=B001" in call for call in log_calls), \
                f"Expected batch=B001: {log_calls}"
            assert any("batch=B002" in call for call in log_calls), \
                f"Expected batch=B002: {log_calls}"
