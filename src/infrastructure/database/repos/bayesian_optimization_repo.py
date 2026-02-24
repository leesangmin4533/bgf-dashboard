"""
BayesianOptimizationRepository -- 베이지안 최적화 이력 저장소

DB: 매장별 store DB (db_type="store")
테이블: bayesian_optimization_log
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BayesianOptimizationRepository(BaseRepository):
    """베이지안 최적화 이력 저장소"""

    db_type = "store"

    def save_optimization_log(
        self,
        store_id: str,
        optimization_date: str,
        objective_value: float,
        accuracy_error: float,
        waste_rate_error: float,
        stockout_rate: float,
        over_order_ratio: float,
        params_before: str,
        params_after: str,
        params_delta: str,
        algorithm: str,
        n_trials: int,
        best_trial: int,
        eval_period_start: str,
        eval_period_end: str,
    ) -> int:
        """최적화 결과 저장

        Args:
            store_id: 매장 코드
            optimization_date: 최적화 실행일 (YYYY-MM-DD)
            objective_value: 최적 목적함수 값
            accuracy_error: 정확도 에러 (1 - accuracy@1)
            waste_rate_error: 폐기율 초과분
            stockout_rate: 품절율
            over_order_ratio: 과잉발주율
            params_before: 최적화 전 파라미터 (JSON)
            params_after: 최적화 후 파라미터 (JSON)
            params_delta: 변경된 파라미터 (JSON)
            algorithm: 사용 알고리즘 ('skopt' or 'optuna')
            n_trials: 시행 횟수
            best_trial: 최적 시행 번호
            eval_period_start: 평가 기간 시작일
            eval_period_end: 평가 기간 종료일

        Returns:
            저장된 row ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO bayesian_optimization_log (
                    store_id, optimization_date, objective_value,
                    accuracy_error, waste_rate_error, stockout_rate, over_order_ratio,
                    params_before, params_after, params_delta,
                    algorithm, n_trials, best_trial,
                    eval_period_start, eval_period_end,
                    applied
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(store_id, optimization_date) DO UPDATE SET
                    objective_value = excluded.objective_value,
                    accuracy_error = excluded.accuracy_error,
                    waste_rate_error = excluded.waste_rate_error,
                    stockout_rate = excluded.stockout_rate,
                    over_order_ratio = excluded.over_order_ratio,
                    params_before = excluded.params_before,
                    params_after = excluded.params_after,
                    params_delta = excluded.params_delta,
                    algorithm = excluded.algorithm,
                    n_trials = excluded.n_trials,
                    best_trial = excluded.best_trial,
                    eval_period_start = excluded.eval_period_start,
                    eval_period_end = excluded.eval_period_end,
                    applied = 1,
                    rolled_back = 0,
                    rollback_reason = NULL
                """,
                (
                    store_id, optimization_date, objective_value,
                    accuracy_error, waste_rate_error, stockout_rate, over_order_ratio,
                    params_before, params_after, params_delta,
                    algorithm, n_trials, best_trial,
                    eval_period_start, eval_period_end,
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid
            logger.info(
                f"[{store_id}] Bayesian 최적화 로그 저장: "
                f"date={optimization_date}, loss={objective_value:.4f}, algo={algorithm}"
            )
            return row_id
        finally:
            conn.close()

    def get_latest_applied(self, store_id: str) -> Optional[Dict[str, Any]]:
        """가장 최근 적용된(applied=1, rolled_back=0) 최적화 기록 조회

        Args:
            store_id: 매장 코드

        Returns:
            최근 적용 기록 dict 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                SELECT id, store_id, optimization_date, iteration, objective_value,
                       accuracy_error, waste_rate_error, stockout_rate, over_order_ratio,
                       params_before, params_after, params_delta,
                       algorithm, n_trials, best_trial,
                       eval_period_start, eval_period_end,
                       applied, rolled_back, rollback_reason, created_at
                FROM bayesian_optimization_log
                WHERE store_id = ? AND applied = 1 AND rolled_back = 0
                ORDER BY optimization_date DESC
                LIMIT 1
                """,
                (store_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            cols = [desc[0] for desc in cursor.description]
            return dict(zip(cols, row))
        finally:
            conn.close()

    def mark_applied(self, store_id: str, optimization_date: str) -> None:
        """applied = 1로 업데이트

        Args:
            store_id: 매장 코드
            optimization_date: 최적화 실행일
        """
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE bayesian_optimization_log
                SET applied = 1
                WHERE store_id = ? AND optimization_date = ?
                """,
                (store_id, optimization_date),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_rolled_back(
        self, store_id: str, optimization_date: str, reason: str
    ) -> None:
        """rolled_back = 1, rollback_reason 업데이트

        Args:
            store_id: 매장 코드
            optimization_date: 최적화 실행일
            reason: 롤백 사유
        """
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE bayesian_optimization_log
                SET rolled_back = 1, rollback_reason = ?
                WHERE store_id = ? AND optimization_date = ?
                """,
                (reason, store_id, optimization_date),
            )
            conn.commit()
            logger.warning(
                f"[{store_id}] Bayesian 최적화 롤백: "
                f"date={optimization_date}, reason={reason}"
            )
        finally:
            conn.close()

    def mark_confirmed(self, store_id: str, optimization_date: str) -> None:
        """롤백 모니터링 통과 확인

        iteration을 +1하여 확정 기록으로 표시한다.

        Args:
            store_id: 매장 코드
            optimization_date: 최적화 실행일
        """
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE bayesian_optimization_log
                SET iteration = iteration + 1
                WHERE store_id = ? AND optimization_date = ?
                """,
                (store_id, optimization_date),
            )
            conn.commit()
            logger.info(
                f"[{store_id}] Bayesian 최적화 확정: date={optimization_date}"
            )
        finally:
            conn.close()

    def get_optimization_history(
        self, store_id: str, days: int = 90
    ) -> List[Dict[str, Any]]:
        """최근 N일 최적화 이력 조회 (대시보드용)

        Args:
            store_id: 매장 코드
            days: 조회 기간 (기본 90일)

        Returns:
            최적화 이력 리스트
        """
        conn = self._get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor = conn.execute(
                """
                SELECT id, store_id, optimization_date, iteration,
                       objective_value, accuracy_error, waste_rate_error,
                       stockout_rate, over_order_ratio,
                       params_delta, algorithm, n_trials, best_trial,
                       eval_period_start, eval_period_end,
                       applied, rolled_back, rollback_reason, created_at
                FROM bayesian_optimization_log
                WHERE store_id = ? AND optimization_date >= ?
                ORDER BY optimization_date DESC
                """,
                (store_id, cutoff),
            )
            cols = [desc[0] for desc in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_param_evolution(
        self, store_id: str, param_name: str, days: int = 90
    ) -> List[Dict[str, Any]]:
        """특정 파라미터의 최적화 변화 추이

        params_delta JSON에서 특정 파라미터 추출.

        Args:
            store_id: 매장 코드
            param_name: 파라미터 이름 (예: "eval.weight_daily_avg")
            days: 조회 기간

        Returns:
            [{"date": str, "old": float, "new": float}, ...]
        """
        import json

        history = self.get_optimization_history(store_id, days)
        evolution = []
        for record in history:
            delta_json = record.get("params_delta", "{}")
            try:
                delta = json.loads(delta_json) if delta_json else {}
            except (json.JSONDecodeError, TypeError):
                delta = {}
            if param_name in delta:
                evolution.append({
                    "date": record["optimization_date"],
                    "old": delta[param_name].get("old"),
                    "new": delta[param_name].get("new"),
                })
        return evolution
