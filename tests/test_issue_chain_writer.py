"""issue_chain_writer.py 단위 테스트 - 이슈 체인 파일 I/O"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from datetime import date

from src.infrastructure.issue_chain_writer import IssueChainWriter, _extract_keywords
from src.domain.ops_anomaly import OpsAnomaly


@pytest.fixture
def tmp_issues_dir(tmp_path):
    """임시 이슈 디렉토리 생성"""
    issues_dir = tmp_path / "05-issues"
    issues_dir.mkdir()
    return issues_dir


@pytest.fixture
def sample_issue_file(tmp_issues_dir):
    """샘플 이슈 체인 파일 생성"""
    filepath = tmp_issues_dir / "prediction.md"
    filepath.write_text(
        "# 예측 이슈 체인\n"
        "\n"
        "> 최종 갱신: 2026-04-01\n"
        "> 현재 상태: 정상\n"
        "\n"
        "---\n"
        "\n"
        "## [PLANNED] ML is_payday DB 반영 효과 검증 (P2)\n"
        "\n"
        "**목표**: 테스트 목표\n"
        "**동기**: 테스트 동기\n"
        "\n"
        "---\n",
        encoding="utf-8",
    )
    return filepath


@pytest.fixture
def writer(tmp_issues_dir):
    return IssueChainWriter(issues_dir=tmp_issues_dir)


class TestExtractKeywords:
    def test_korean_keywords(self):
        result = _extract_keywords("음료류 폐기율 상승 조사")
        assert "음료류" in result
        assert "폐기율" in result
        assert "상승" in result
        # "조사"는 불용어로 제외
        assert "조사" not in result

    def test_mixed_keywords(self):
        result = _extract_keywords("ML is_payday DB 반영 효과 확인")
        assert "is_payday" in result
        assert "ML" in result
        assert "확인" not in result  # 불용어


class TestIsDuplicate:
    def test_duplicate_detected(self, writer, sample_issue_file):
        content = sample_issue_file.read_text(encoding="utf-8")
        # "ML is_payday"가 이미 [PLANNED]로 있음
        keywords = _extract_keywords("ML is_payday 효과 분석")
        assert writer._is_duplicate(content, keywords)

    def test_no_duplicate(self, writer, sample_issue_file):
        content = sample_issue_file.read_text(encoding="utf-8")
        keywords = _extract_keywords("음료류 폐기율 급증")
        assert not writer._is_duplicate(content, keywords)


class TestInsertPlannedBlock:
    def test_insert_before_last_separator(self, writer, sample_issue_file):
        content = sample_issue_file.read_text(encoding="utf-8")
        block = "## [PLANNED] 테스트 이슈 (P2)\n\n**목표**: 테스트\n\n"
        result = writer._insert_planned_block(sample_issue_file, content, block)
        assert result is True

        new_content = sample_issue_file.read_text(encoding="utf-8")
        assert "테스트 이슈" in new_content
        # 오늘 날짜로 갱신 확인
        assert date.today().isoformat() in new_content

    def test_insert_no_separator(self, writer, tmp_issues_dir):
        filepath = tmp_issues_dir / "empty.md"
        filepath.write_text("# 빈 파일\n\n> 최종 갱신: 2026-01-01\n", encoding="utf-8")

        content = filepath.read_text(encoding="utf-8")
        block = "## [PLANNED] 새 이슈 (P1)\n\n"
        result = writer._insert_planned_block(filepath, content, block)
        assert result is True

        new_content = filepath.read_text(encoding="utf-8")
        assert "새 이슈" in new_content


class TestWriteAnomalies:
    def test_single_anomaly_registered(self, writer, sample_issue_file):
        anomaly = OpsAnomaly(
            metric_name="waste_rate",
            issue_chain_file="prediction.md",
            title="음료류 폐기율 급증",
            priority="P2",
            description="음료류 7일 폐기율 10%",
        )
        count = writer.write_anomalies([anomaly])
        assert count == 1

        content = sample_issue_file.read_text(encoding="utf-8")
        assert "음료류 폐기율 급증" in content
        assert "자동 감지" in content

    def test_duplicate_skipped(self, writer, sample_issue_file):
        """이미 존재하는 이슈와 키워드 중복 시 스킵"""
        anomaly = OpsAnomaly(
            metric_name="prediction_accuracy",
            issue_chain_file="prediction.md",
            title="ML is_payday 효과 재검증",  # 기존 is_payday + ML 키워드 매칭
            priority="P2",
            description="테스트",
        )
        count = writer.write_anomalies([anomaly])
        assert count == 0

    def test_missing_file_skipped(self, writer):
        """존재하지 않는 파일은 스킵"""
        anomaly = OpsAnomaly(
            metric_name="test",
            issue_chain_file="nonexistent.md",
            title="테스트 이슈",
            priority="P2",
            description="테스트",
        )
        count = writer.write_anomalies([anomaly])
        assert count == 0


class TestBuildBlock:
    def test_block_format(self, writer):
        anomaly = OpsAnomaly(
            metric_name="order_failure",
            issue_chain_file="order-execution.md",
            title="발주 실패 급증 조사",
            priority="P1",
            description="최근 7일 발주 실패 15건",
        )
        block = writer._build_block(anomaly)
        assert "[PLANNED]" in block
        assert "P1" in block
        assert "자동 감지" in block
        assert "order_failure" in block
