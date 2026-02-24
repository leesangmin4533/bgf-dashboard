"""
ExternalFactorRepository — 외부 요인 저장소

원본: src/db/repository.py ExternalFactorRepository (lines 815-882)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExternalFactorRepository(BaseRepository):
    """외부 요인 저장소 (날씨, 공휴일 등 - 추후 확장)"""

    db_type = "common"

    def save_factor(
        self, factor_date: str, factor_type: str,
        factor_key: str, factor_value: str
    ) -> None:
        """외부 요인 저장

        Args:
            factor_date: 요인 날짜 (YYYY-MM-DD)
            factor_type: 요인 유형 (weather, holiday, event, promotion)
            factor_key: 요인 키 (temperature, precipitation 등)
            factor_value: 요인 값
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO external_factors
                (factor_date, factor_type, factor_key, factor_value, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(factor_date, factor_type, factor_key) DO UPDATE SET
                    factor_value = excluded.factor_value
                """,
                (factor_date, factor_type, factor_key, factor_value, self._now())
            )

            conn.commit()
        finally:
            conn.close()

    def get_factors(
        self, factor_date: str, factor_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """외부 요인 조회

        Args:
            factor_date: 요인 날짜 (YYYY-MM-DD)
            factor_type: 요인 유형 (None이면 전체)

        Returns:
            외부 요인 데이터 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            if factor_type:
                cursor.execute(
                    """
                    SELECT * FROM external_factors
                    WHERE factor_date = ? AND factor_type = ?
                    """,
                    (factor_date, factor_type)
                )
            else:
                cursor.execute(
                    "SELECT * FROM external_factors WHERE factor_date = ?",
                    (factor_date,)
                )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
