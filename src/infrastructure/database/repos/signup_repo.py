"""회원가입 요청 Repository"""

import sqlite3
from typing import Optional

from werkzeug.security import generate_password_hash

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SignupRequestRepository(BaseRepository):
    """회원가입 요청 CRUD (common.db)"""

    db_type = "common"

    def create_request(self, store_id: str, password: str, phone: str) -> int:
        """가입 요청 생성. 반환: request id."""
        now = self._now()
        pw_hash = generate_password_hash(password)
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO signup_requests
                   (store_id, password_hash, phone, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (store_id, pw_hash, phone, now),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_pending(self) -> list[dict]:
        """대기중 가입 요청 목록 (최신순)."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, store_id, phone, status, created_at
                   FROM signup_requests
                   WHERE status = 'pending'
                   ORDER BY created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_pending_count(self) -> int:
        """대기중 건수."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM signup_requests WHERE status = 'pending'"
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def get_by_id(self, request_id: int) -> Optional[dict]:
        """ID로 요청 조회."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM signup_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def approve(self, request_id: int, reviewed_by: int) -> bool:
        """승인 처리."""
        now = self._now()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """UPDATE signup_requests
                   SET status = 'approved', reviewed_at = ?, reviewed_by = ?
                   WHERE id = ? AND status = 'pending'""",
                (now, reviewed_by, request_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def reject(self, request_id: int, reviewed_by: int, reason: Optional[str] = None) -> bool:
        """거절 처리."""
        now = self._now()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """UPDATE signup_requests
                   SET status = 'rejected', reviewed_at = ?, reviewed_by = ?,
                       reject_reason = ?
                   WHERE id = ? AND status = 'pending'""",
                (now, reviewed_by, reason, request_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def has_pending_for_store(self, store_id: str) -> bool:
        """해당 매장에 대기중 요청이 있는지 확인."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM signup_requests WHERE store_id = ? AND status = 'pending'",
                (store_id,),
            ).fetchone()
            return (row[0] if row else 0) > 0
        finally:
            conn.close()
