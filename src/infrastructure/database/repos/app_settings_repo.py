"""
AppSettingsRepository — 앱 설정 저장소

원본: src/db/repository.py AppSettingsRepository (lines 4471-4517)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AppSettingsRepository(BaseRepository):
    """앱 설정 저장소 (프로세스 간 공유, DB 영속)

    대시보드 토글 등 설정값을 SQLite에 저장하여
    Flask 서버와 CLI/스케줄러 프로세스 간 설정을 공유한다.
    """

    db_type = "store"

    def get(self, key: str, default: bool = True) -> bool:
        """설정값 조회 (bool 반환)

        Args:
            key: 설정 키 (예: 'EXCLUDE_AUTO_ORDER')
            default: DB에 값이 없을 때 기본값

        Returns:
            설정값 (True/False)
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            return row[0].lower() == "true"
        except Exception:
            return default
        finally:
            conn.close()

    def set(self, key: str, value: bool) -> None:
        """설정값 저장 (INSERT OR REPLACE)

        Args:
            key: 설정 키
            value: 설정값 (True/False)
        """
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO app_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))""",
                (key, "true" if value else "false")
            )
            conn.commit()
        finally:
            conn.close()
