"""일일 체인 리포트 테스트"""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.application.services.daily_chain_report import DailyChainReport, SuggestedIssue


class TestBuildChainReport:
    """_build_chain_report 리포트 생성 테스트"""

    def test_with_anomalies_and_milestone(self):
        """이상 + 마일스톤 + AI 분석 모두 있는 경우"""
        data = {
            "anomalies": [
                {"priority": "P1", "title": "폐기율 상승"},
                {"priority": "P2", "title": "예측 정확도 하락"},
            ],
            "milestone": {
                "K1": {"value": 1.03, "status": "ACHIEVED"},
                "K2": {"value": 0.035, "status": "NOT_MET"},
                "K3": {"value": 0.02, "status": "ACHIEVED"},
                "K4": {"value": 1, "status": "ACHIEVED"},
            },
            "analysis": {
                "responded": True,
                "summary": "행사 종료 후 폐기 23건",
                "suggested_issues": [{"title": "냉각 로직 구현"}],
            },
        }
        report = DailyChainReport()._build_chain_report(data)
        assert "일일 체인 리포트" in report
        assert "이상 감지: 2건" in report
        assert "P1:1" in report
        assert "마일스톤: 3/4" in report
        assert "K2" in report
        assert "AI 분석" in report
        assert "신규 이슈 제안: 1건" in report

    def test_no_anomalies(self):
        """이상 없을 때"""
        data = {"anomalies": []}
        report = DailyChainReport()._build_chain_report(data)
        assert "이상 감지: 없음" in report

    def test_empty_data(self):
        """빈 데이터"""
        report = DailyChainReport()._build_chain_report({})
        assert "정상" in report

    def test_analysis_failure(self):
        """AI 분석 실패"""
        data = {
            "anomalies": [],
            "analysis": {"responded": False, "summary": "timeout"},
        }
        report = DailyChainReport()._build_chain_report(data)
        assert "실패" in report


class TestRegisterSuggestedIssues:
    """_register_suggested_issues 이슈체인 등록 테스트"""

    @patch("src.infrastructure.issue_chain_writer.IssueChainWriter.write_anomalies")
    def test_registers_valid_issues(self, mock_write):
        """유효한 제안 → IssueChainWriter 호출"""
        mock_write.return_value = 1

        suggested = [
            {
                "metric_name": "waste_rate",
                "issue_chain_file": "expiry-tracking.md",
                "title": "냉각 로직 미구현",
                "priority": "P2",
                "description": "D+1~D+7 WMA 블렌딩",
            }
        ]
        result = DailyChainReport()._register_suggested_issues(suggested)
        assert result == 1
        mock_write.assert_called_once()

        # 전달된 SuggestedIssue 검증
        issues = mock_write.call_args[0][0]
        assert len(issues) == 1
        assert issues[0].title == "냉각 로직 미구현"
        assert issues[0].issue_chain_file == "expiry-tracking.md"

    @patch("src.infrastructure.issue_chain_writer.IssueChainWriter.write_anomalies")
    def test_skips_invalid_issues(self, mock_write):
        """title/file 없는 제안은 스킵"""
        suggested = [{"metric_name": "test"}]  # title, issue_chain_file 없음
        result = DailyChainReport()._register_suggested_issues(suggested)
        assert result == 0

    def test_empty_suggested(self):
        """빈 리스트"""
        result = DailyChainReport()._register_suggested_issues([])
        assert result == 0


class TestRun:
    """run() 통합 테스트"""

    @patch("src.application.services.daily_chain_report.DAILY_CHAIN_REPORT_ENABLED", False)
    def test_disabled_skips(self):
        """ENABLED=False면 스킵"""
        result = DailyChainReport().run()
        assert result["sent"] is False

    @patch("src.application.services.daily_chain_report._PENDING_PATH")
    @patch.object(DailyChainReport, "_send_kakao")
    def test_no_pending_sends_normal_report(self, mock_kakao, mock_path):
        """pending 없어도 '정상' 리포트 발송"""
        mock_path.exists.return_value = False
        mock_path.unlink = MagicMock()

        result = DailyChainReport().run()
        assert result["sent"] is True
        mock_kakao.assert_called_once()

    @patch("src.application.services.daily_chain_report._PENDING_PATH")
    @patch.object(DailyChainReport, "_send_kakao")
    @patch.object(DailyChainReport, "_register_suggested_issues")
    def test_with_suggested_issues(self, mock_register, mock_kakao, mock_path):
        """suggested_issues 있으면 등록 호출"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "anomalies": [{"priority": "P2", "title": "test"}],
            "analysis": {
                "responded": True,
                "summary": "test",
                "suggested_issues": [
                    {"title": "새 이슈", "issue_chain_file": "prediction.md",
                     "metric_name": "test", "priority": "P2", "description": "desc"}
                ],
            },
        })
        mock_path.unlink = MagicMock()
        mock_register.return_value = 1

        result = DailyChainReport().run()
        assert result["registered_issues"] == 1
        mock_register.assert_called_once()
