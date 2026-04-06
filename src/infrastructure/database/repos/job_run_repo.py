"""JobRunRepository — 스케줄 잡 실행 로그 저장소 (job-health-monitor)."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class JobRunRepository(BaseRepository):
    """job_runs 테이블 접근.

    공통 DB. 스레드 간 공유 금지 — 호출마다 fresh connection.
    """

    db_type = "common"

    def insert_running(
        self,
        job_name: str,
        store_id: Optional[str],
        scheduled_for: str,
        started_at: str,
    ) -> int:
        """running 상태로 삽입. id 반환."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO job_runs
                    (job_name, store_id, scheduled_for, started_at, status)
                VALUES (?, ?, ?, ?, 'running')
                """,
                (job_name, store_id, scheduled_for, started_at),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_end(
        self,
        run_id: int,
        status: str,
        ended_at: str,
        duration_sec: float,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        """종료 상태로 업데이트."""
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE job_runs
                SET status = ?, ended_at = ?, duration_sec = ?,
                    error_message = ?, error_type = ?
                WHERE id = ?
                """,
                (status, ended_at, duration_sec, error_message, error_type, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_missed(
        self,
        job_name: str,
        store_id: Optional[str],
        scheduled_for: str,
    ) -> Optional[int]:
        """missed 상태 삽입. 부분 UNIQUE 인덱스로 중복 방지.

        Returns:
            lastrowid, 중복이면 None.
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO job_runs
                        (job_name, store_id, scheduled_for, started_at, ended_at,
                         status, duration_sec)
                    VALUES (?, ?, ?, ?, ?, 'missed', 0)
                    """,
                    (job_name, store_id, scheduled_for, now, now),
                )
                conn.commit()
                return cur.lastrowid
            except Exception as e:
                # IntegrityError (중복) → 조용히 무시
                logger.debug(f"[JobRunRepo] missed 중복: {job_name}/{store_id}/{scheduled_for}: {e}")
                return None
        finally:
            conn.close()

    def mark_alerted(self, run_id: int) -> bool:
        """원자적 알림 플래그. 이미 알림된 경우 False."""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE job_runs SET alerted = 1 WHERE id = ? AND alerted = 0",
                (run_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_last_success(
        self,
        job_name: str,
        store_id: Optional[str],
        scheduled_for: str,
    ) -> Optional[Dict[str, Any]]:
        """특정 예정 시각에 대한 success 기록 조회."""
        conn = self._get_conn()
        try:
            conn.row_factory = __import__("sqlite3").Row
            cur = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE job_name = ? AND scheduled_for = ?
                  AND (store_id = ? OR (store_id IS NULL AND ? IS NULL))
                  AND status = 'success'
                LIMIT 1
                """,
                (job_name, scheduled_for, store_id, store_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_recent_failed_unalerted(self, hours: int = 1) -> List[Dict[str, Any]]:
        """최근 N시간 내 failed/missed 중 미알림 건."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        conn = self._get_conn()
        try:
            conn.row_factory = __import__("sqlite3").Row
            cur = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE status IN ('failed', 'missed', 'timeout')
                  AND alerted = 0
                  AND started_at >= ?
                ORDER BY started_at DESC
                """,
                (since,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def purge_old(self, retention_days: int) -> int:
        """retention 경과 레코드 삭제. 삭제 건수 반환."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM job_runs WHERE started_at < ?",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def summary_last_24h(self) -> Dict[str, int]:
        """최근 24시간 요약 (대시보드/일일 리포트용)."""
        since = (datetime.now() - timedelta(hours=24)).isoformat()
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """
                SELECT status, COUNT(*) FROM job_runs
                WHERE started_at >= ?
                GROUP BY status
                """,
                (since,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
        finally:
            conn.close()
