"""
ValidationRepository — 데이터 검증 결과 저장소

원본: src/db/repository.py ValidationRepository (lines 4725-4901)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ValidationRepository(BaseRepository):
    """데이터 검증 결과 저장소"""

    db_type = "store"

    def log_validation_result(
        self,
        result,
        validation_type: str = 'comprehensive'
    ):
        """검증 결과를 validation_log 테이블에 저장

        Args:
            result: ValidationResult 객체
            validation_type: 검증 유형 ('format', 'duplicate', 'consistency', 'anomaly', 'comprehensive')
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 오류별로 로그 저장
            for error in result.errors:
                cursor.execute("""
                    INSERT INTO validation_log (
                        validated_at, sales_date, store_id,
                        validation_type, is_passed,
                        error_code, error_message, affected_items, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    result.validated_at.isoformat(),
                    result.sales_date,
                    result.store_id,
                    validation_type,
                    0,  # is_passed = False
                    error.error_code,
                    error.error_message,
                    f'["{error.affected_item}"]',  # JSON array
                    None if not error.metadata else str(error.metadata)
                ))

            # 경고별로 로그 저장 (is_passed=1이지만 경고 있음)
            for warning in result.warnings:
                cursor.execute("""
                    INSERT INTO validation_log (
                        validated_at, sales_date, store_id,
                        validation_type, is_passed,
                        error_code, error_message, affected_items, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    result.validated_at.isoformat(),
                    result.sales_date,
                    result.store_id,
                    'anomaly',
                    1,  # is_passed = True (경고지만 통과)
                    warning.warning_code,
                    warning.warning_message,
                    f'["{warning.affected_item}"]',
                    str(warning.metadata) if warning.metadata else None
                ))

            # 전체 통과인 경우도 기록
            if result.is_valid and not result.errors:
                cursor.execute("""
                    INSERT INTO validation_log (
                        validated_at, sales_date, store_id,
                        validation_type, is_passed,
                        error_code, error_message, affected_items, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    result.validated_at.isoformat(),
                    result.sales_date,
                    result.store_id,
                    validation_type,
                    1,  # is_passed = True
                    'ALL_PASSED',
                    f'{result.passed_count}개 레코드 검증 통과',
                    '[]',
                    f'{{"total": {result.total_count}, "passed": {result.passed_count}}}'
                ))

            conn.commit()
            logger.info(f"검증 결과 저장: {result.sales_date} - {len(result.errors)} errors, {len(result.warnings)} warnings")

        finally:
            conn.close()

    def get_validation_summary(
        self,
        days: int = 7,
        store_id: str = "46704"
    ) -> Dict[str, Any]:
        """최근 N일 검증 통계

        Args:
            days: 조회 기간 (일)
            store_id: 점포 ID

        Returns:
            Dict: {total_validations, passed, failed, by_type: {...}}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 전체 통계
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN is_passed = 1 THEN 1 ELSE 0 END) as passed,
                    SUM(CASE WHEN is_passed = 0 THEN 1 ELSE 0 END) as failed
                FROM validation_log
                WHERE sales_date >= date('now', ?)
                AND store_id = ?
            """, (f'-{days} days', store_id))

            row = cursor.fetchone()

            # 유형별 통계
            cursor.execute("""
                SELECT
                    validation_type,
                    COUNT(*) as count,
                    SUM(CASE WHEN is_passed = 1 THEN 1 ELSE 0 END) as passed,
                    SUM(CASE WHEN is_passed = 0 THEN 1 ELSE 0 END) as failed
                FROM validation_log
                WHERE sales_date >= date('now', ?)
                AND store_id = ?
                GROUP BY validation_type
            """, (f'-{days} days', store_id))

            by_type = {r['validation_type']: dict(r) for r in cursor.fetchall()}

            return {
                'total_validations': row['total'] if row else 0,
                'passed': row['passed'] if row else 0,
                'failed': row['failed'] if row else 0,
                'by_type': by_type
            }
        finally:
            conn.close()

    def get_recent_errors(
        self,
        days: int = 7,
        store_id: str = "46704",
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """최근 오류 목록 조회

        Args:
            days: 조회 기간 (일)
            store_id: 점포 ID
            limit: 최대 조회 건수

        Returns:
            List[Dict]: 오류 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    validated_at,
                    sales_date,
                    validation_type,
                    error_code,
                    error_message,
                    affected_items
                FROM validation_log
                WHERE is_passed = 0
                AND sales_date >= date('now', ?)
                AND store_id = ?
                ORDER BY validated_at DESC
                LIMIT ?
            """, (f'-{days} days', store_id, limit))

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
