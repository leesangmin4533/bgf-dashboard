"""운영 지표 -> 이슈 자동 등록 파이프라인 오케스트레이터

매장별 운영 지표 수집 -> 이상 판정 -> 이슈 등록 -> 알림
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.settings.store_context import StoreContext
from src.analysis.ops_metrics import OpsMetrics
from src.domain.ops_anomaly import detect_anomalies, OpsAnomaly
from src.infrastructure.issue_chain_writer import IssueChainWriter
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"


class OpsIssueDetector:
    """매장별 운영 지표 수집 -> 이상 판정 -> 이슈 등록 -> 알림"""

    def run_all_stores(self) -> dict:
        """전체 활성 매장 순회"""
        active_stores = StoreContext.get_all_active()
        all_anomalies = []

        for ctx in active_stores:
            try:
                anomalies = self._detect_for_store(ctx.store_id)
                # store_id 태깅
                for a in anomalies:
                    a.store_id = ctx.store_id
                all_anomalies.extend(anomalies)
            except Exception as e:
                logger.warning(f"[OpsIssueDetector] {ctx.store_id} 감지 실패: {e}")

        # 매장 간 중복 제거 (동일 지표가 여러 매장에서 감지)
        unique = self._deduplicate_cross_store(all_anomalies)

        # 이슈 체인 등록
        writer = IssueChainWriter()
        registered = writer.write_anomalies(unique)

        # CLAUDE.md 테이블 동기화 + 알림 + Claude 자동 대응용 파일 저장
        if registered > 0:
            self._sync_table()
            self._send_alert(unique)

        # 파이프라인 체인: anomaly 유무와 무관하게 항상 pending 저장
        # (milestone/claude_responder/chain_report가 이어서 처리)
        self._save_pending_issues(unique)

        return {
            "total_anomalies": len(all_anomalies),
            "unique_anomalies": len(unique),
            "registered": registered,
        }

    def _detect_for_store(self, store_id: str) -> list:
        """단일 매장 지표 수집 -> 판정"""
        metrics = OpsMetrics(store_id).collect_all()
        return detect_anomalies(metrics)

    def _deduplicate_cross_store(self, anomalies: list) -> list:
        """여러 매장에서 동일 지표 감지 시 1건만 등록 (가장 심각한 것 유지)"""
        priority_rank = {"P1": 0, "P2": 1, "P3": 2}
        seen = {}  # key: (metric_name, issue_chain_file) -> OpsAnomaly

        for a in anomalies:
            key = (a.metric_name, a.issue_chain_file, a.title)
            if key not in seen:
                seen[key] = a
            else:
                # 더 높은 우선순위(낮은 rank) 유지
                existing = seen[key]
                if priority_rank.get(a.priority, 9) < priority_rank.get(existing.priority, 9):
                    seen[key] = a

        return list(seen.values())

    def _sync_table(self):
        """sync_issue_table.py 호출 -> CLAUDE.md 이슈 테이블 갱신"""
        script = _SCRIPTS_DIR / "sync_issue_table.py"
        if not script.exists():
            logger.warning(f"[OpsIssueDetector] sync_issue_table.py 없음: {script}")
            return

        try:
            subprocess.run(
                [sys.executable, str(script)],
                cwd=str(script.parent.parent),
                timeout=30,
                capture_output=True,
            )
            logger.info("[OpsIssueDetector] CLAUDE.md 이슈 테이블 동기화 완료")
        except Exception as e:
            logger.warning(f"[OpsIssueDetector] sync_issue_table.py 실패 (무시): {e}")

    def _save_pending_issues(self, anomalies: list) -> None:
        """Claude 자동 대응용 pending_issues.json 저장"""
        try:
            from src.settings.constants import CLAUDE_AUTO_RESPOND_ENABLED
            if not CLAUDE_AUTO_RESPOND_ENABLED:
                return

            path = _SCRIPTS_DIR.parent / ".claude" / "pending_issues.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "generated_at": datetime.now().isoformat(),
                "anomalies": [
                    {
                        "metric_name": a.metric_name,
                        "title": a.title,
                        "priority": a.priority,
                        "description": a.description,
                        "evidence": a.evidence,
                    }
                    for a in anomalies
                ],
            }
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"[OpsIssueDetector] pending_issues.json 저장 ({len(anomalies)}건)")
        except Exception as e:
            logger.warning(f"[OpsIssueDetector] pending_issues 저장 실패 (무시): {e}")

    def _send_alert(self, anomalies: list):
        """카카오 알림 발송"""
        try:
            from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY

            p1_list = [a for a in anomalies if a.priority == "P1"]
            p2_list = [a for a in anomalies if a.priority == "P2"]

            lines = ["[운영 이상 감지]"]
            if p1_list:
                lines.append(f"P1 긴급: {len(p1_list)}건")
                for a in p1_list[:3]:
                    lines.append(f"  - {a.title}")
            if p2_list:
                lines.append(f"P2 중요: {len(p2_list)}건")
                for a in p2_list[:3]:
                    lines.append(f"  - {a.title}")

            message = "\n".join(lines)
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
            notifier.send_message(message, category="daily_chain")
            logger.info(f"[OpsIssueDetector] 카카오 알림 발송 ({len(anomalies)}건)")
        except Exception as e:
            logger.warning(f"[OpsIssueDetector] 카카오 알림 실패 (무시): {e}")
