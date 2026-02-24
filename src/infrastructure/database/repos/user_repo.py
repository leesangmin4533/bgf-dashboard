"""대시보드 사용자 인증 Repository"""

import sqlite3
from typing import Optional

from werkzeug.security import generate_password_hash, check_password_hash

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DashboardUserRepository(BaseRepository):
    """대시보드 사용자 CRUD (common.db)"""

    db_type = "common"

    def create_user(
        self,
        username: str,
        password: str,
        store_id: str,
        role: str = "viewer",
        full_name: Optional[str] = None,
    ) -> int:
        """사용자 생성. 반환: user id."""
        now = self._now()
        pw_hash = generate_password_hash(password)
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO dashboard_users
                   (username, password_hash, store_id, role, is_active,
                    full_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (username, pw_hash, store_id, role, full_name, now, now),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def create_user_with_hash(
        self,
        username: str,
        password_hash: str,
        store_id: str,
        role: str = "viewer",
        full_name: Optional[str] = None,
    ) -> int:
        """해싱된 비밀번호로 사용자 생성 (signup 승인용). 반환: user id."""
        now = self._now()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO dashboard_users
                   (username, password_hash, store_id, role, is_active,
                    full_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (username, password_hash, store_id, role, full_name, now, now),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_by_username(self, username: str) -> Optional[dict]:
        """username으로 사용자 조회 (password_hash 포함)."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM dashboard_users WHERE username = ?",
                (username,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_by_id(self, user_id: int) -> Optional[dict]:
        """id로 사용자 조회 (password_hash 포함)."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM dashboard_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def verify_password(self, username: str, password: str) -> Optional[dict]:
        """인증 검증. 성공 시 user dict 반환, 실패 시 None."""
        user = self.get_by_username(username)
        if not user:
            return None
        if not user.get("is_active"):
            return None
        if not check_password_hash(user["password_hash"], password):
            return None
        return user

    def update_last_login(self, user_id: int) -> None:
        """마지막 로그인 시각 갱신."""
        now = self._now()
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE dashboard_users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_users(self) -> list[dict]:
        """전체 사용자 목록 (password_hash 제외)."""
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, username, store_id, role, is_active,
                          full_name, phone, created_at, updated_at, last_login_at
                   FROM dashboard_users
                   ORDER BY id"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_user(self, user_id: int, **kwargs) -> bool:
        """사용자 정보 수정. 지원 필드: password, role, is_active, full_name.

        Returns:
            True if updated, False if user not found.
        """
        sets = []
        params = []

        if "password" in kwargs:
            sets.append("password_hash = ?")
            params.append(generate_password_hash(kwargs["password"]))
        if "role" in kwargs:
            sets.append("role = ?")
            params.append(kwargs["role"])
        if "is_active" in kwargs:
            sets.append("is_active = ?")
            params.append(int(kwargs["is_active"]))
        if "full_name" in kwargs:
            sets.append("full_name = ?")
            params.append(kwargs["full_name"])
        if "phone" in kwargs:
            sets.append("phone = ?")
            params.append(kwargs["phone"])

        if not sets:
            return False

        sets.append("updated_at = ?")
        params.append(self._now())
        params.append(user_id)

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                f"UPDATE dashboard_users SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def delete_user(self, user_id: int) -> bool:
        """사용자 삭제. Returns True if deleted."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM dashboard_users WHERE id = ?",
                (user_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
