"""
ExecutionVerifier — 자전 시스템 실행 결과 검증

자전 시스템 4번째 고리: 전날 EXECUTED 건의 실제 효과를 검증.
다음날 Phase 1.67 시작 시 호출 → BGF 재수집 후 데이터로 비교.

실패해도 예외 전파 없음 (발주 플로우 보호).
"""

from typing import Dict, List, Any

from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.repos.action_proposal_repo import ActionProposalRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExecutionVerifier:
    """전날 EXECUTED 건 검증"""

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.repo = ActionProposalRepository(store_id=store_id)

    def verify_previous_day(self) -> Dict[str, Any]:
        """전날 EXECUTED 건 조회 → 검증 → verified_result 업데이트"""
        result = {"verified": 0, "success": 0, "failed": 0}

        try:
            executed = self.repo.get_executed_yesterday(self.store_id)
            if not executed:
                return result

            for p in executed:
                try:
                    vr = self._verify(p)
                    self.repo.mark_verified(p["id"], vr)
                    result["verified"] += 1
                    if vr in result:
                        result[vr] += 1
                except Exception as e:
                    logger.warning(
                        f"[Verify] {self.store_id} id={p['id']} 검증 실패: {e}"
                    )
                    try:
                        self.repo.mark_verified(p["id"], "failed")
                    except Exception:
                        pass
                    result["verified"] += 1
                    result["failed"] += 1

            if result["failed"] > 0:
                self._alert_failures(result)

            if result["verified"] > 0:
                logger.info(
                    f"[Verify] {self.store_id} 전날 검증: "
                    f"{result['success']}건 성공, {result['failed']}건 실패"
                )

        except Exception as e:
            logger.error(f"[Verify] {self.store_id} 검증 실패 (무시): {e}")

        return result

    def _verify(self, proposal: Dict[str, Any]) -> str:
        """action_type별 검증 → 'success' / 'failed' / 'skipped'"""
        action_type = proposal.get("action_type", "")

        if action_type == "CLEAR_EXPIRED_BATCH":
            return self._verify_expired_batch()

        return "skipped"

    def _verify_expired_batch(self) -> str:
        """만료 배치 잔여 재확인"""
        conn = DBRouter.get_store_connection(self.store_id)
        try:
            cnt = conn.execute(
                """
                SELECT COUNT(*) FROM inventory_batches
                WHERE store_id = ? AND status = 'expired' AND remaining_qty > 0
                """,
                (self.store_id,),
            ).fetchone()[0]
            return "success" if cnt == 0 else "failed"
        finally:
            conn.close()

    def _alert_failures(self, result: Dict[str, Any]) -> None:
        """검증 실패 시 카카오 알림"""
        try:
            from src.notification.kakao_notifier import (
                KakaoNotifier, DEFAULT_REST_API_KEY,
            )
            msg = (
                f"[자전시스템 검증]\n"
                f"매장 {self.store_id}: 전날 실행 {result['verified']}건 중 "
                f"{result['failed']}건 실패\n확인 필요"
            )
            KakaoNotifier(DEFAULT_REST_API_KEY).send_message(msg)
        except Exception as e:
            logger.warning(f"[Verify] {self.store_id} 실패 알림 발송 오류: {e}")
