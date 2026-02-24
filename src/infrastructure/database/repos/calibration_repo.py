"""
CalibrationRepository — 파라미터 보정 이력 저장소

원본: src/db/repository.py CalibrationRepository 클래스에서 1:1 추출
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CalibrationRepository(BaseRepository):
    """파라미터 보정 이력 저장소"""

    db_type = "store"

    def save_calibration(
        self,
        calibration_date: str,
        param_name: str,
        old_value: float,
        new_value: float,
        reason: str = "",
        accuracy_before: Optional[float] = None,
        accuracy_after: Optional[float] = None,
        sample_size: Optional[int] = None
    ) -> int:
        """보정 이력 저장

        Args:
            calibration_date: 보정 실행일 (YYYY-MM-DD)
            param_name: 파라미터 이름
            old_value: 이전 값
            new_value: 새 값
            reason: 보정 사유
            accuracy_before: 보정 전 적중률
            accuracy_after: 보정 후 기대 적중률
            sample_size: 검증 샘플 수

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO calibration_history
                (calibration_date, param_name, old_value, new_value, reason,
                 accuracy_before, accuracy_after, sample_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (calibration_date, param_name, old_value, new_value, reason,
                 accuracy_before, accuracy_after, sample_size, self._now())
            )
            row_id = cursor.lastrowid
            conn.commit()
            return row_id
        finally:
            conn.close()

    def get_recent_calibrations(self, days: int = 30) -> List[Dict[str, Any]]:
        """최근 보정 이력 조회

        Args:
            days: 조회할 최근 일수

        Returns:
            보정 이력 목록 (최신순)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM calibration_history
                WHERE calibration_date >= date('now', '-' || ? || ' days')
                ORDER BY created_at DESC
                """,
                (days,)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_param_history(self, param_name: str, days: int = 90) -> List[Dict[str, Any]]:
        """특정 파라미터의 변경 이력

        Args:
            param_name: 파라미터 이름
            days: 조회할 최근 일수

        Returns:
            해당 파라미터의 보정 이력 목록 (날짜순)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM calibration_history
                WHERE param_name = ?
                AND calibration_date >= date('now', '-' || ? || ' days')
                ORDER BY calibration_date
                """,
                (param_name, days)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
