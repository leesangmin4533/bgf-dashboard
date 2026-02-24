"""
예측 로그 저장 및 정확도 추적

PredictionLogger: prediction_logs 테이블에 예측 결과 저장, 정확도 계산
- improved_predictor.py에서 분리 (Phase 6-1)
"""

import sqlite3
from datetime import datetime
from typing import Optional, Dict, List

from src.utils.logger import get_logger

logger = get_logger(__name__)


class PredictionLogger:
    """예측 로그 관리"""

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if db_path is None:
            from src.infrastructure.database.connection import DBRouter
            db_path = str(DBRouter.get_store_db_path(store_id)) if store_id else str(DBRouter.get_legacy_db_path())
        self.db_path = str(db_path)
        self.store_id = store_id

    def _get_connection(self, timeout: int = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def log_prediction(self, result) -> None:
        """예측 결과 로그 저장

        Args:
            result: 예측 결과 객체 (PredictionResult)
        """
        conn = self._get_connection(timeout=30)
        try:
            cursor = conn.cursor()

            now = datetime.now()
            prediction_date = now.strftime("%Y-%m-%d")  # 예측 수행일

            # 테이블에 새 컬럼이 있는지 확인
            cursor.execute("PRAGMA table_info(prediction_logs)")
            columns = [col[1] for col in cursor.fetchall()]

            has_stock_source = 'stock_source' in columns

            if 'weekday_coef' in columns:
                if has_stock_source:
                    cursor.execute("""
                        INSERT INTO prediction_logs (
                            prediction_date, item_cd, mid_cd, target_date,
                            predicted_qty, adjusted_qty,
                            weekday_coef, confidence, current_stock, safety_stock,
                            order_qty, model_type, store_id,
                            stock_source, pending_source, is_stock_stale,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        prediction_date,
                        result.item_cd,
                        result.mid_cd,
                        result.target_date,
                        result.predicted_qty,
                        result.adjusted_qty,
                        result.weekday_coef,
                        result.confidence,
                        result.current_stock,
                        result.safety_stock,
                        result.order_qty,
                        result.model_type,
                        self.store_id,
                        getattr(result, 'stock_source', ''),
                        getattr(result, 'pending_source', ''),
                        1 if getattr(result, 'is_stock_stale', False) else 0,
                        now.isoformat()
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO prediction_logs (
                            prediction_date, item_cd, mid_cd, target_date,
                            predicted_qty, adjusted_qty,
                            weekday_coef, confidence, current_stock, safety_stock,
                            order_qty, model_type, store_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        prediction_date,
                        result.item_cd,
                        result.mid_cd,
                        result.target_date,
                        result.predicted_qty,
                        result.adjusted_qty,
                        result.weekday_coef,
                        result.confidence,
                        result.current_stock,
                        result.safety_stock,
                        result.order_qty,
                        result.model_type,
                        self.store_id,
                        now.isoformat()
                    ))
            else:
                # 기존 스키마 호환
                cursor.execute("""
                    INSERT INTO prediction_logs (
                        prediction_date, item_cd, mid_cd, target_date,
                        predicted_qty, model_type, store_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    prediction_date,
                    result.item_cd,
                    result.mid_cd,
                    result.target_date,
                    result.adjusted_qty,
                    result.model_type,
                    self.store_id,
                    now.isoformat()
                ))

            conn.commit()
        finally:
            conn.close()

    def log_predictions_batch(self, results: List) -> int:
        """여러 예측 결과를 한 번에 저장 (배치 처리)

        Args:
            results: PredictionResult 리스트

        Returns:
            저장된 건수
        """
        if not results:
            return 0

        conn = self._get_connection(timeout=60)
        try:
            cursor = conn.cursor()

            now = datetime.now()
            prediction_date = now.strftime("%Y-%m-%d")

            # 테이블에 새 컬럼이 있는지 확인
            cursor.execute("PRAGMA table_info(prediction_logs)")
            columns = [col[1] for col in cursor.fetchall()]

            has_stock_source = 'stock_source' in columns

            saved_count = 0
            if 'weekday_coef' in columns:
                for result in results:
                    try:
                        if has_stock_source:
                            cursor.execute("""
                                INSERT INTO prediction_logs (
                                    prediction_date, item_cd, mid_cd, target_date,
                                    predicted_qty, adjusted_qty,
                                    weekday_coef, confidence, current_stock, safety_stock,
                                    order_qty, model_type, store_id,
                                    stock_source, pending_source, is_stock_stale,
                                    created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                prediction_date,
                                result.item_cd,
                                result.mid_cd,
                                result.target_date,
                                result.predicted_qty,
                                result.adjusted_qty,
                                result.weekday_coef,
                                result.confidence,
                                result.current_stock,
                                result.safety_stock,
                                result.order_qty,
                                result.model_type,
                                self.store_id,
                                getattr(result, 'stock_source', ''),
                                getattr(result, 'pending_source', ''),
                                1 if getattr(result, 'is_stock_stale', False) else 0,
                                now.isoformat()
                            ))
                        else:
                            cursor.execute("""
                                INSERT INTO prediction_logs (
                                    prediction_date, item_cd, mid_cd, target_date,
                                    predicted_qty, adjusted_qty,
                                    weekday_coef, confidence, current_stock, safety_stock,
                                    order_qty, model_type, store_id, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                prediction_date,
                                result.item_cd,
                                result.mid_cd,
                                result.target_date,
                                result.predicted_qty,
                                result.adjusted_qty,
                                result.weekday_coef,
                                result.confidence,
                                result.current_stock,
                                result.safety_stock,
                                result.order_qty,
                                result.model_type,
                                self.store_id,
                                now.isoformat()
                            ))
                        saved_count += 1
                    except Exception as e:
                        logger.warning(f"예측 로그 저장 실패 ({result.item_cd}): {e}")
            else:
                for result in results:
                    try:
                        cursor.execute("""
                            INSERT INTO prediction_logs (
                                prediction_date, item_cd, mid_cd, target_date,
                                predicted_qty, model_type, store_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            prediction_date,
                            result.item_cd,
                            result.mid_cd,
                            result.target_date,
                            result.adjusted_qty,
                            result.model_type,
                            self.store_id,
                            now.isoformat()
                        ))
                        saved_count += 1
                    except Exception as e:
                        logger.warning(f"예측 로그 저장 실패 ({result.item_cd}): {e}")

            conn.commit()
            return saved_count
        finally:
            conn.close()

    def log_predictions_batch_if_needed(self, results: List) -> int:
        """오늘 날짜로 이미 기록이 있으면 스킵, 없으면 저장

        Phase 1.7(독립 예측 로깅)에서 이미 기록한 경우
        Phase 2(자동발주)에서 중복 INSERT를 방지한다.

        Args:
            results: PredictionResult 리스트

        Returns:
            저장된 건수 (이미 있으면 0)
        """
        if not results:
            return 0

        conn = self._get_connection(timeout=30)
        try:
            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            from src.db.store_query import store_filter
            sf, sp = store_filter(None, self.store_id)
            cursor.execute(
                f"SELECT COUNT(*) FROM prediction_logs WHERE prediction_date = ? {sf}",
                (today,) + sp
            )
            if cursor.fetchone()[0] > 0:
                return 0
        finally:
            conn.close()

        return self.log_predictions_batch(results)

    def calculate_accuracy(self, days: int = 7) -> Dict[str, object]:
        """예측 정확도 계산

        Args:
            days: 비교 기간 (기본 7일)

        Returns:
            정확도 통계 딕셔너리 (MAPE, MAE, 적중률 등)
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # 컬럼 존재 여부 확인
            cursor.execute("PRAGMA table_info(prediction_logs)")
            columns = [col[1] for col in cursor.fetchall()]

            if 'adjusted_qty' in columns:
                qty_column = 'adjusted_qty'
            else:
                qty_column = 'predicted_qty'

            from src.db.store_query import store_filter
            sf, sp = store_filter("pl", self.store_id)

            query = f"""
            SELECT
                pl.item_cd,
                pl.predicted_qty,
                pl.{qty_column} as adj_qty,
                ds.sale_qty as actual_qty,
                ABS(pl.{qty_column} - ds.sale_qty) as error
            FROM prediction_logs pl
            JOIN daily_sales ds
                ON pl.item_cd = ds.item_cd
                AND pl.target_date = ds.sales_date
            WHERE pl.target_date >= date('now', '-' || ? || ' days') {sf}
            """

            cursor.execute(query, (days,) + sp)
            rows = cursor.fetchall()

            if not rows:
                return {"message": "아직 비교할 데이터가 없습니다"}

            # 통계 계산
            total = len(rows)
            errors = [row['error'] for row in rows]
            mae = sum(errors) / total

            # MAPE 계산
            ape_list = []
            within_1 = 0
            within_2 = 0

            for row in rows:
                actual = row['actual_qty'] or 1
                adj = row['adj_qty'] or 0
                ape = abs(adj - actual) / max(actual, 1) * 100
                ape_list.append(ape)

                if row['error'] <= 1:
                    within_1 += 1
                if row['error'] <= 2:
                    within_2 += 1

            mape = sum(ape_list) / total if ape_list else 0

            return {
                "total_predictions": total,
                "mape": round(mape, 2),
                "mae": round(mae, 2),
                "accuracy_within_1": round(within_1 / total * 100, 1),
                "accuracy_within_2": round(within_2 / total * 100, 1),
            }
        finally:
            conn.close()
