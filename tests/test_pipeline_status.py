"""파이프라인 상태 기록 + 체인 검증 테스트"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path

from src.infrastructure.pipeline_status import (
    record_step, load_status, clear_status,
    check_dependencies, get_pipeline_summary,
)


@pytest.fixture
def tmp_status(tmp_path):
    """임시 상태 파일 경로로 패치"""
    status_path = tmp_path / "pipeline_status.json"
    with patch("src.infrastructure.pipeline_status._STATUS_PATH", status_path):
        yield status_path


class TestRecordStep:

    def test_creates_new_file(self, tmp_status):
        record_step("ops_detect", True, "이상 2건", {"count": 2})
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert data["steps"]["ops_detect"]["success"] is True

    def test_appends_to_existing(self, tmp_status):
        record_step("ops_detect", True, "이상 0건")
        record_step("claude_respond", True, "분석 완료")
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert "ops_detect" in data["steps"]
        assert "claude_respond" in data["steps"]

    def test_records_failure(self, tmp_status):
        record_step("chain_report", False, "DB 연결 실패")
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert data["steps"]["chain_report"]["success"] is False

    @patch("src.infrastructure.pipeline_status._send_failure_alert")
    def test_failure_triggers_alert(self, mock_alert, tmp_status):
        """실패 시 즉시 카카오 알림 호출"""
        record_step("ops_detect", False, "DB 타임아웃")
        mock_alert.assert_called_once_with("ops_detect", "DB 타임아웃")

    @patch("src.infrastructure.pipeline_status._send_failure_alert")
    def test_success_no_alert(self, mock_alert, tmp_status):
        """성공 시 알림 없음"""
        record_step("ops_detect", True, "정상")
        mock_alert.assert_not_called()


class TestCheckDependencies:

    def test_no_deps_can_proceed(self, tmp_status):
        """의존 없는 단계는 항상 진행"""
        result = check_dependencies("ops_detect")
        assert result["can_proceed"] is True

    def test_dep_success_can_proceed(self, tmp_status):
        """선행 단계 성공 → 진행 가능"""
        record_step("ops_detect", True, "정상")
        result = check_dependencies("claude_respond")
        assert result["can_proceed"] is True

    def test_dep_failed_cannot_proceed(self, tmp_status):
        """선행 단계 실패 → 진행 불가"""
        record_step("ops_detect", False, "DB 오류")
        result = check_dependencies("claude_respond")
        assert result["can_proceed"] is False
        assert "ops_detect" in result["failed_deps"][0]

    def test_dep_not_run_cannot_proceed(self, tmp_status):
        """선행 단계 미실행 → 진행 불가"""
        result = check_dependencies("claude_respond")
        assert result["can_proceed"] is False
        assert "미실행" in result["failed_deps"][0]


class TestGetPipelineSummary:

    def test_no_steps(self, tmp_status):
        assert "미실행" in get_pipeline_summary()

    def test_all_success(self, tmp_status):
        record_step("ops_detect", True, "OK")
        record_step("claude_respond", True, "OK")
        assert "정상" in get_pipeline_summary()

    def test_with_failure(self, tmp_status):
        record_step("ops_detect", True, "OK")
        record_step("claude_respond", False, "실패")
        summary = get_pipeline_summary()
        assert "1/2" in summary
        assert "claude_respond" in summary


class TestLoadAndClear:

    def test_load_empty(self, tmp_status):
        assert load_status() == {}

    def test_clear(self, tmp_status):
        record_step("ops_detect", True, "OK")
        clear_status()
        assert not tmp_status.exists()
