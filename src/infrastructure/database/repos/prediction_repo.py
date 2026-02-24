"""
예측 로그 저장소 (PredictionRepository)

prediction_logs 테이블 CRUD
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PredictionRepository(BaseRepository):
    """예측 로그 저장소"""

    db_type = "store"

    def save_prediction(
        self,
        prediction_date: str,
        target_date: str,
        item_cd: str,
        mid_cd: Optional[str] = None,
        predicted_qty: int = 0,
        model_type: str = "rule_based",
        store_id: Optional[str] = None
    ) -> int:
        """예측 로그 저장

        Args:
            prediction_date: 예측 수행일 (YYYY-MM-DD)
            target_date: 예측 대상일 (YYYY-MM-DD)
            item_cd: 상품 코드
            mid_cd: 중분류 코드
            predicted_qty: 예측 판매량
            model_type: 모델 유형 (rule_based, ml_xgboost 등)
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO prediction_logs
                (prediction_date, target_date, item_cd, mid_cd, predicted_qty,
                 model_type, store_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (prediction_date, target_date, item_cd, mid_cd, predicted_qty,
                 model_type, store_id, self._now())
            )

            log_id = cursor.lastrowid
            conn.commit()
            return log_id
        finally:
            conn.close()

    def update_actual(self, target_date: str, item_cd: str, actual_qty: int, store_id: Optional[str] = None) -> None:
        """실제 판매량 업데이트 (예측 정확도 계산용)

        Args:
            target_date: 예측 대상일 (YYYY-MM-DD)
            item_cd: 상품 코드
            actual_qty: 실제 판매량
            store_id: 매장 코드
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""
                UPDATE prediction_logs
                SET actual_qty = ?
                WHERE target_date = ? AND item_cd = ? {sf}
                """,
                (actual_qty, target_date, item_cd) + sp
            )

            conn.commit()
        finally:
            conn.close()

    def get_accuracy_report(self, days: int = 7, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """예측 정확도 리포트

        Args:
            days: 조회할 최근 일수
            store_id: 매장 코드

        Returns:
            일별 MAE/MAPE 등 정확도 지표 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""
                SELECT
                    target_date,
                    COUNT(*) as total_predictions,
                    AVG(ABS(predicted_qty - actual_qty)) as mae,
                    AVG(CASE WHEN actual_qty > 0
                        THEN ABS(predicted_qty - actual_qty) * 100.0 / actual_qty
                        ELSE 0 END) as mape
                FROM prediction_logs
                WHERE actual_qty IS NOT NULL {sf}
                GROUP BY target_date
                ORDER BY target_date DESC
                LIMIT ?
                """,
                sp + (days,)
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
