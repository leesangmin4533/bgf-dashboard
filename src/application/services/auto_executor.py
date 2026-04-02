"""
AutoExecutor — action_proposals 자동 실행기

자전 시스템 3번째 고리: PENDING 제안을 분류별로 처리.
- HIGH: 자동 실행 → EXECUTED
- LOW: 카카오 승인 요청 → PENDING_APPROVAL
- INFO: 알림만 → NOTIFIED
- SKIP: 무시

실패해도 예외 전파 없음 (발주 플로우 보호).
"""

from datetime import date
from typing import Dict, List, Any

from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.repos.action_proposal_repo import ActionProposalRepository
from src.settings.constants import (
    AUTO_EXEC_HIGH, AUTO_EXEC_LOW, AUTO_EXEC_INFO,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AutoExecutor:
    """action_proposals PENDING → 분류별 처리"""

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.repo = ActionProposalRepository(store_id=store_id)

    def run(self) -> Dict[str, Any]:
        """PENDING 조회 → HIGH 자동실행 + LOW 승인요청 + INFO 알림"""
        today = date.today().isoformat()
        result = {"executed": 0, "approval_requested": 0, "notified": 0, "skipped": 0}

        try:
            pending = self.repo.get_pending(self.store_id, today)
            if not pending:
                return result

            high = [p for p in pending if p["action_type"] in AUTO_EXEC_HIGH]
            low = [p for p in pending if p["action_type"] in AUTO_EXEC_LOW]
            info = [p for p in pending if p["action_type"] in AUTO_EXEC_INFO]

            # HIGH: 자동 실행
            for p in high:
                try:
                    if self._execute_action(p):
                        self.repo.mark_executed(p["id"])
                        result["executed"] += 1
                        logger.info(
                            f"[AutoExec] {self.store_id} {p['action_type']} "
                            f"id={p['id']} 자동 실행 완료"
                        )
                except Exception as e:
                    logger.warning(
                        f"[AutoExec] {self.store_id} {p['action_type']} "
                        f"id={p['id']} 실행 실패: {e}"
                    )

            # LOW: 승인 요청
            if low:
                try:
                    self._request_approval(low)
                    for p in low:
                        self.repo.mark_resolved(p["id"], status="PENDING_APPROVAL")
                    result["approval_requested"] += len(low)
                except Exception as e:
                    logger.warning(f"[AutoExec] {self.store_id} 승인 요청 실패: {e}")

            # INFO: 알림만
            if info:
                for p in info:
                    try:
                        self.repo.mark_resolved(p["id"], status="NOTIFIED")
                    except Exception:
                        pass
                result["notified"] += len(info)

            if result["executed"] + result["approval_requested"] > 0:
                logger.info(
                    f"[AutoExec] {self.store_id} 처리 완료: "
                    f"실행={result['executed']}, 승인요청={result['approval_requested']}, "
                    f"알림={result['notified']}"
                )

        except Exception as e:
            logger.error(f"[AutoExec] {self.store_id} 실패 (무시): {e}")

        return result

    def _execute_action(self, proposal: Dict[str, Any]) -> bool:
        """HIGH 항목 자동 실행. 성공 시 True."""
        action_type = proposal["action_type"]
        if action_type == "CLEAR_EXPIRED_BATCH":
            return self._clear_expired_batch()
        logger.warning(f"[AutoExec] 미지원 action_type: {action_type}")
        return False

    def _clear_expired_batch(self) -> bool:
        """만료 배치 remaining_qty → 0 보정"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            updated = conn.execute(
                """
                UPDATE inventory_batches
                SET remaining_qty = 0
                WHERE store_id = ? AND status = 'expired' AND remaining_qty > 0
                """,
                (self.store_id,),
            ).rowcount
            conn.commit()
            if updated > 0:
                logger.info(f"[AutoExec] CLEAR_EXPIRED_BATCH: {updated}건 보정")
            return True
        finally:
            conn.close()

    def _request_approval(self, proposals: List[Dict[str, Any]]) -> None:
        """LOW 항목 카카오 승인 요청"""
        from src.application.services.kakao_proposal_formatter import KakaoProposalFormatter
        from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY

        msg = KakaoProposalFormatter().format(self.store_id, proposals)
        msg = "[승인 필요]\n" + msg
        KakaoNotifier(DEFAULT_REST_API_KEY).send_message(msg)
        logger.info(f"[AutoExec] {self.store_id} 승인 요청 {len(proposals)}건 발송")
