"""
AISummaryRepository — AI 요약 결과 저장소

공용 DB(common.db)에 ai_summaries 테이블 CRUD.
v68 하네스 엔지니어링에서 추가.
"""

from typing import Dict, List, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AISummaryRepository(BaseRepository):
    """ai_summaries 테이블 CRUD (db_type="common")"""

    db_type = "common"

    def upsert_summary(
        self,
        summary_date: str,
        summary_type: str,
        store_id: str,
        summary_text: str,
        anomaly_count: int,
        model_used: str,
        token_count: int = 0,
        cost_usd: float = 0.0,
    ) -> int:
        """요약 저장 또는 갱신 (같은 날 재실행 시 UPSERT)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai_summaries
                    (summary_date, summary_type, store_id, summary_text,
                     anomaly_count, model_used, token_count, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(summary_date, summary_type, store_id)
                DO UPDATE SET
                    summary_text  = excluded.summary_text,
                    anomaly_count = excluded.anomaly_count,
                    model_used    = excluded.model_used,
                    token_count   = excluded.token_count,
                    cost_usd      = excluded.cost_usd,
                    created_at    = datetime('now', 'localtime')
                """,
                (summary_date, summary_type, store_id, summary_text,
                 anomaly_count, model_used, token_count, cost_usd),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_latest_by_store(
        self, store_id: str, summary_type: str
    ) -> Optional[Dict[str, Any]]:
        """최신 요약 조회 (어제 요약과 트렌드 비교용)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM ai_summaries
                WHERE store_id = ? AND summary_type = ?
                ORDER BY summary_date DESC
                LIMIT 1
                """,
                (store_id, summary_type),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_today_all_stores(
        self, summary_date: str, summary_type: str
    ) -> List[Dict[str, Any]]:
        """오늘 전체 매장 요약 (카카오 통합 알림용)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT store_id, summary_text, anomaly_count
                FROM ai_summaries
                WHERE summary_date = ? AND summary_type = ?
                  AND store_id IS NOT NULL
                ORDER BY anomaly_count DESC
                """,
                (summary_date, summary_type),
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def get_daily_cost(self, summary_date: str) -> float:
        """일일 AI 비용 합산 (비용 상한 체크용)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0.0) AS total
                FROM ai_summaries
                WHERE summary_date = ?
                """,
                (summary_date,),
            )
            row = cursor.fetchone()
            return row["total"] if row else 0.0
        finally:
            conn.close()
