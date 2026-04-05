"""파이프라인 상태 기록 테스트"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path

from src.infrastructure.pipeline_status import record_step, load_status, clear_status


@pytest.fixture
def tmp_status(tmp_path):
    """임시 상태 파일 경로로 패치"""
    status_path = tmp_path / "pipeline_status.json"
    with patch("src.infrastructure.pipeline_status._STATUS_PATH", status_path):
        yield status_path


class TestRecordStep:

    def test_creates_new_file(self, tmp_status):
        """파일 없으면 새로 생성"""
        record_step("ops_detect", True, "이상 2건", {"count": 2})
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert "ops_detect" in data["steps"]
        assert data["steps"]["ops_detect"]["success"] is True
        assert data["steps"]["ops_detect"]["summary"] == "이상 2건"

    def test_appends_to_existing(self, tmp_status):
        """기존 파일에 단계 추가"""
        record_step("ops_detect", True, "이상 0건")
        record_step("claude_respond", True, "분석 완료")
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert "ops_detect" in data["steps"]
        assert "claude_respond" in data["steps"]

    def test_records_failure(self, tmp_status):
        """실패 기록"""
        record_step("chain_report", False, "DB 연결 실패")
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert data["steps"]["chain_report"]["success"] is False

    def test_overwrites_same_step(self, tmp_status):
        """같은 단계 재기록 시 덮어쓰기"""
        record_step("ops_detect", False, "실패")
        record_step("ops_detect", True, "재시도 성공")
        data = json.loads(tmp_status.read_text(encoding="utf-8"))
        assert data["steps"]["ops_detect"]["success"] is True


class TestLoadAndClear:

    def test_load_empty(self, tmp_status):
        """파일 없으면 빈 dict"""
        result = load_status()
        assert result == {}

    def test_load_existing(self, tmp_status):
        """기존 데이터 로드"""
        record_step("ops_detect", True, "OK")
        result = load_status()
        assert "steps" in result

    def test_clear(self, tmp_status):
        """초기화"""
        record_step("ops_detect", True, "OK")
        clear_status()
        assert not tmp_status.exists()
