"""
ActionProposalRepository — 액션 제안 저장소

자동화 2단계: integrity 이상 → 원인 분석 → 액션 제안 저장.
매장별 DB(store).
"""

from typing import Dict, List, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ActionProposalRepository(BaseRepository):
    """action_proposals 테이블 CRUD (db_type="store")"""

    db_type = "store"

    def save(self, proposal: Dict[str, Any]) -> int:
        """제안 저장"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO action_proposals
                    (proposal_date, store_id, item_cd, action_type,
                     reason, suggestion, evidence, status)
                VALUES
                    (:proposal_date, :store_id, :item_cd, :action_type,
                     :reason, :suggestion, :evidence, :status)
                """,
                proposal,
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_pending(self, store_id: str, proposal_date: str) -> List[Dict[str, Any]]:
        """오늘 PENDING 상태 제안 목록"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM action_proposals
                WHERE store_id = ? AND proposal_date = ? AND status = 'PENDING'
                ORDER BY id
                """,
                (store_id, proposal_date),
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def mark_resolved(self, proposal_id: int, status: str = "APPROVED") -> None:
        """APPROVED 또는 DISMISSED 처리"""
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE action_proposals
                SET status = ?, resolved_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (status, proposal_id),
            )
            conn.commit()
        finally:
            conn.close()
