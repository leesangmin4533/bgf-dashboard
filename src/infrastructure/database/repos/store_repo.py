"""
StoreRepository — 매장 정보 저장소

신규 파일: stores 테이블 CRUD
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StoreRepository(BaseRepository):
    """매장 정보 저장소"""

    db_type = "common"

    def get_all_stores(self):
        """전체 매장 목록 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stores ORDER BY store_id")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_active_stores(self):
        """활성 매장 목록 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stores WHERE is_active = 1 ORDER BY store_id")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_store(self, store_id):
        """매장 단건 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stores WHERE store_id = ?", (store_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
