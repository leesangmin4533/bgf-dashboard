"""Claude 자동 대응 모듈 테스트"""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.infrastructure.claude_responder import ClaudeResponder


class TestRespondIfNeeded:
    """respond_if_needed 메인 흐름 테스트"""

    @patch("src.infrastructure.claude_responder.CLAUDE_AUTO_RESPOND_ENABLED", False)
    def test_disabled_skips_everything(self):
        """ENABLED=False면 즉시 스킵"""
        result = ClaudeResponder().respond_if_needed()
        assert result["responded"] is False

    @patch("src.infrastructure.claude_responder._PENDING_PATH")
    def test_no_pending_file_skips(self, mock_path):
        """pending_issues.json 없으면 스킵"""
        mock_path.exists.return_value = False
        result = ClaudeResponder().respond_if_needed()
        assert result["responded"] is False

    @patch("src.infrastructure.claude_responder._PENDING_PATH")
    def test_empty_anomalies_skips(self, mock_path):
        """anomalies가 빈 리스트면 스킵"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({"anomalies": []})
        result = ClaudeResponder().respond_if_needed()
        assert result["responded"] is False

    @patch("src.infrastructure.claude_responder._OUTPUT_DIR")
    @patch("src.infrastructure.claude_responder._PENDING_PATH")
    @patch.object(ClaudeResponder, "_call_claude")
    @patch.object(ClaudeResponder, "_send_kakao")
    @patch.object(ClaudeResponder, "_get_latest_milestone")
    def test_calls_claude_with_pending(
        self, mock_milestone, mock_kakao, mock_claude, mock_pending, mock_outdir
    ):
        """pending 있으면 Claude 호출"""
        mock_pending.exists.return_value = True
        mock_pending.read_text.return_value = json.dumps({
            "generated_at": "2026-04-06T23:55:00",
            "anomalies": [
                {"metric_name": "waste_rate", "title": "001 폐기율", "priority": "P1",
                 "description": "test", "evidence": {"rate_7d": 0.05}}
            ],
        })
        mock_milestone.return_value = None
        mock_claude.return_value = "핵심: K2 미달\n원인: 행사 종료\n제안: 냉각 로직"

        # output_dir mock
        mock_file = MagicMock()
        mock_outdir.__truediv__ = MagicMock(return_value=mock_file)
        mock_file.parent.mkdir = MagicMock()

        result = ClaudeResponder().respond_if_needed()
        assert result["responded"] is True
        mock_claude.assert_called_once()
        mock_kakao.assert_called_once()

    @patch("src.infrastructure.claude_responder._PENDING_PATH")
    @patch.object(ClaudeResponder, "_call_claude")
    def test_claude_failure_graceful(self, mock_claude, mock_pending):
        """Claude 호출 실패 시 graceful 처리"""
        mock_pending.exists.return_value = True
        mock_pending.read_text.return_value = json.dumps({
            "anomalies": [{"metric_name": "test", "title": "t", "priority": "P2",
                          "description": "d", "evidence": {}}],
        })
        mock_claude.side_effect = RuntimeError("timeout")

        result = ClaudeResponder().respond_if_needed()
        assert result["responded"] is False
        assert "timeout" in result["summary"]


class TestExtractSummary:
    """_extract_summary 테스트"""

    def test_first_3_lines(self):
        text = "라인1\n라인2\n라인3\n라인4\n라인5"
        result = ClaudeResponder()._extract_summary(text)
        assert "라인1" in result
        assert "라인2" in result
        assert "라인3" in result
        assert "라인4" not in result

    def test_skips_empty_lines(self):
        text = "\n\n핵심 요약\n\n원인 분석\n제안"
        result = ClaudeResponder()._extract_summary(text)
        assert "핵심 요약" in result
        assert "원인 분석" in result
        assert "제안" in result

    def test_truncates_long_text(self):
        text = "A" * 500
        result = ClaudeResponder()._extract_summary(text, max_chars=100)
        assert len(result) <= 100
        assert result.endswith("...")


class TestBuildPrompt:
    """_build_prompt 테스트"""

    def test_includes_anomalies(self):
        issues = {
            "anomalies": [
                {"priority": "P1", "title": "폐기율 상승", "evidence": {"rate": 0.05}}
            ],
        }
        prompt = ClaudeResponder()._build_prompt(issues)
        assert "P1" in prompt
        assert "폐기율 상승" in prompt

    def test_includes_milestone(self):
        issues = {
            "anomalies": [{"priority": "P2", "title": "test"}],
            "milestone": {
                "K1": {"value": 1.03, "status": "ACHIEVED"},
                "K2": {"value": 0.035, "status": "NOT_MET"},
            },
        }
        prompt = ClaudeResponder()._build_prompt(issues)
        assert "K1" in prompt
        assert "ACHIEVED" in prompt
        assert "NOT_MET" in prompt

    def test_no_milestone_ok(self):
        issues = {"anomalies": [{"priority": "P2", "title": "test"}]}
        prompt = ClaudeResponder()._build_prompt(issues)
        assert "분석 요청" in prompt
