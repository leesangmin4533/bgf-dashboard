"""일일 파이프라인 통합 리포트 + 이슈체인 자동 등록

23:55~23:58 파이프라인 결과를 통합하여 카카오 1건 발송.
Claude 분석에서 suggested_issues가 있으면 이슈체인에 자동 등록.
"""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from src.settings.constants import DAILY_CHAIN_REPORT_ENABLED
from src.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_DIR = Path(__file__).parent.parent.parent.parent  # bgf_auto/
_PENDING_PATH = _PROJECT_DIR / ".claude" / "pending_issues.json"
_SCRIPTS_DIR = _PROJECT_DIR / "scripts"


@dataclass
class SuggestedIssue:
    """Claude가 제안한 신규 이슈 (IssueChainWriter 호환)"""
    metric_name: str
    issue_chain_file: str
    title: str
    priority: str
    description: str
    evidence: dict = field(default_factory=dict)
    store_id: str = ""


class DailyChainReport:
    """일일 파이프라인 통합 리포트"""

    def run(self) -> dict:
        """pending 읽기 → 통합 리포트 → 이슈체인 등록 → 카카오"""
        if not DAILY_CHAIN_REPORT_ENABLED:
            return {"sent": False}

        data = self._load_pending()

        # 통합 리포트 텍스트
        report_text = self._build_chain_report(data)

        # Claude 제안 이슈 → 이슈체인 등록
        registered = 0
        suggested = (data.get("analysis") or {}).get("suggested_issues", [])
        if suggested:
            registered = self._register_suggested_issues(suggested)

        # CLAUDE.md 이슈 테이블 동기화 (등록 있을 때만)
        if registered > 0:
            self._sync_table()

        # 카카오 발송
        self._send_kakao(report_text)

        # pending 삭제
        _PENDING_PATH.unlink(missing_ok=True)

        logger.info(
            f"[ChainReport] 발송 완료 — 이상 {len(data.get('anomalies', []))}건, "
            f"이슈 등록 {registered}건"
        )
        return {
            "sent": True,
            "anomaly_count": len(data.get("anomalies", [])),
            "registered_issues": registered,
            "report": report_text,
        }

    def _load_pending(self) -> dict:
        """pending_issues.json 로드 (없으면 빈 dict)"""
        if not _PENDING_PATH.exists():
            return {}
        try:
            return json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[ChainReport] pending 파싱 실패: {e}")
            return {}

    def _build_chain_report(self, data: dict) -> str:
        """통합 리포트 텍스트 생성"""
        today = date.today().strftime("%m-%d")
        lines = [f"[일일 체인 리포트] {today}", ""]

        # 이상 감지
        anomalies = data.get("anomalies", [])
        if anomalies:
            p1 = sum(1 for a in anomalies if a.get("priority") == "P1")
            p2 = sum(1 for a in anomalies if a.get("priority") == "P2")
            lines.append(f"이상 감지: {len(anomalies)}건 (P1:{p1} P2:{p2})")
            for a in anomalies[:3]:
                lines.append(f"  - [{a.get('priority')}] {a.get('title', '?')}")
        else:
            lines.append("이상 감지: 없음")

        # 마일스톤
        milestone = data.get("milestone", {})
        if milestone:
            achieved = sum(
                1 for v in milestone.values()
                if isinstance(v, dict) and v.get("status") == "ACHIEVED"
            )
            total = sum(1 for v in milestone.values() if isinstance(v, dict))
            not_met = [
                k for k, v in milestone.items()
                if isinstance(v, dict) and v.get("status") != "ACHIEVED"
            ]
            lines.append(f"마일스톤: {achieved}/{total} 달성")
            if not_met:
                lines.append(f"  미달: {', '.join(not_met)}")

        # AI 분석
        analysis = data.get("analysis", {})
        if analysis.get("responded"):
            summary = analysis.get("summary", "")
            if summary:
                lines.append(f"AI 분석: {summary[:100]}")

            suggested = analysis.get("suggested_issues", [])
            if suggested:
                lines.append(f"신규 이슈 제안: {len(suggested)}건")
                for s in suggested[:2]:
                    lines.append(f"  - {s.get('title', '?')}")
        elif analysis.get("summary"):
            # 실패했지만 에러 메시지 있음
            lines.append(f"AI 분석: 실패 ({analysis['summary'][:50]})")

        # 이상 없고 마일스톤도 없으면 최소 리포트
        if not anomalies and not milestone and not analysis:
            lines.append("모든 지표 정상")

        return "\n".join(lines)

    def _register_suggested_issues(self, suggested: list) -> int:
        """Claude 제안 이슈를 이슈체인에 등록"""
        from src.infrastructure.issue_chain_writer import IssueChainWriter

        issues = []
        for s in suggested:
            if not s.get("title") or not s.get("issue_chain_file"):
                continue
            issues.append(SuggestedIssue(
                metric_name=s.get("metric_name", "ai_suggested"),
                issue_chain_file=s["issue_chain_file"],
                title=s["title"],
                priority=s.get("priority", "P3"),
                description=s.get("description", "Claude AI 분석에서 자동 제안"),
            ))

        if not issues:
            return 0

        writer = IssueChainWriter()
        registered = writer.write_anomalies(issues)
        if registered > 0:
            logger.info(f"[ChainReport] 이슈체인 등록: {registered}건")
        return registered

    def _sync_table(self) -> None:
        """sync_issue_table.py 호출 → CLAUDE.md 이슈 테이블 갱신"""
        script = _SCRIPTS_DIR / "sync_issue_table.py"
        if not script.exists():
            return
        try:
            subprocess.run(
                [sys.executable, str(script)],
                cwd=str(script.parent.parent),
                timeout=30,
                capture_output=True,
            )
        except Exception as e:
            logger.warning(f"[ChainReport] sync 실패 (무시): {e}")

    def _send_kakao(self, report_text: str) -> None:
        """카카오 통합 리포트 발송"""
        try:
            from src.notification.kakao_notifier import KakaoNotifier
            notifier = KakaoNotifier()
            notifier.send_message(report_text, category="daily_chain")
        except Exception as e:
            logger.warning(f"[ChainReport] 카카오 실패 (무시): {e}")
