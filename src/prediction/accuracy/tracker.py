"""
예측 정확도 추적 모듈

지표:
1. MAPE (Mean Absolute Percentage Error): 평균 절대 백분율 오차
2. SMAPE (Symmetric MAPE): 대칭 평균 절대 백분율 오차
3. wMAPE (Weighted MAPE): 가중 평균 절대 백분율 오차
4. MAE (Mean Absolute Error): 평균 절대 오차
5. Accuracy@N: N개 이내 오차 비율
6. Bias: 과대예측 vs 과소예측 경향
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import math


def _get_legacy_db_path() -> str:
    """레거시 DB 경로 반환 (store_id 없을 때 폴백용)"""
    from src.infrastructure.database.connection import resolve_db_path
    return resolve_db_path()


@dataclass
class AccuracyMetrics:
    """정확도 지표"""
    period_start: str              # 분석 시작일
    period_end: str                # 분석 종료일
    total_predictions: int         # 총 예측 건수

    # 오차 지표
    mape: float                    # MAPE (%) - actual>0 기준
    mae: float                     # MAE (개수)
    rmse: float                    # RMSE
    smape: float                   # SMAPE (%) - 대칭 MAPE
    wmape: float                   # wMAPE (%) - 가중 MAPE

    # 적중률
    accuracy_exact: float          # 정확히 맞춘 비율 (%)
    accuracy_within_1: float       # ±1개 이내 (%)
    accuracy_within_2: float       # ±2개 이내 (%)
    accuracy_within_3: float       # ±3개 이내 (%)

    # 편향 분석
    over_prediction_rate: float    # 과대예측 비율 (%)
    under_prediction_rate: float   # 과소예측 비율 (%)
    avg_over_amount: float         # 평균 과대예측량
    avg_under_amount: float        # 평균 과소예측량


@dataclass
class CategoryAccuracy:
    """카테고리별 정확도"""
    mid_cd: str
    mid_nm: str
    metrics: AccuracyMetrics
    sample_items: List[Dict[str, Any]] = field(default_factory=list)  # 대표 상품 정확도


class AccuracyTracker:
    """정확도 추적기"""

    def __init__(self, db_path: Optional[str] = None, store_id: Optional[str] = None) -> None:
        if db_path is None and store_id:
            from src.infrastructure.database.connection import DBRouter
            db_path = str(DBRouter.get_store_db_path(store_id))
        self.db_path = db_path or _get_legacy_db_path()
        self.store_id = store_id

    def _get_connection(self) -> sqlite3.Connection:
        """DB 연결 (매장 DB일 경우 common.db ATTACH)"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        if self.store_id:
            from src.infrastructure.database.connection import attach_common_with_views
            attach_common_with_views(conn, self.store_id)
        return conn

    def calculate_metrics(
        self,
        predictions: List[Dict[str, Any]],
        actuals: List[Dict[str, Any]]
    ) -> AccuracyMetrics:
        """
        예측/실제 데이터로 정확도 지표 계산

        Args:
            predictions: [{"item_cd": ..., "predicted_qty": ..., "date": ...}, ...]
            actuals: [{"item_cd": ..., "actual_qty": ..., "date": ...}, ...]

        Returns:
            AccuracyMetrics
        """
        if not predictions or not actuals:
            return self._empty_metrics()

        # 매칭을 위한 딕셔너리
        actual_map = {}
        for a in actuals:
            key = (a.get("item_cd"), a.get("date", ""))
            actual_map[key] = a.get("actual_qty", 0)

        # 매칭된 데이터
        matched = []
        for p in predictions:
            key = (p.get("item_cd"), p.get("date", ""))
            if key in actual_map:
                matched.append({
                    "predicted": p.get("predicted_qty", 0),
                    "actual": actual_map[key]
                })

        if not matched:
            return self._empty_metrics()

        # 지표 계산
        total = len(matched)
        errors = []
        abs_errors = []
        squared_errors = []
        pct_errors = []
        smape_errors = []          # SMAPE 개별 값
        sum_abs_errors = 0         # wMAPE 분자: SUM(|pred - actual|)
        sum_actuals = 0            # wMAPE 분모: SUM(actual)

        exact_count = 0
        within_1 = 0
        within_2 = 0
        within_3 = 0

        over_predictions = []  # 과대예측
        under_predictions = []  # 과소예측

        for m in matched:
            pred = m["predicted"]
            actual = m["actual"]
            error = pred - actual
            abs_error = abs(error)

            errors.append(error)
            abs_errors.append(abs_error)
            squared_errors.append(error ** 2)

            # MAPE용 백분율 오차 (실제값이 0이면 스킵)
            if actual > 0:
                pct_errors.append(abs_error / actual * 100)

            # SMAPE 계산 (pred와 actual 둘 다 0이면 SMAPE=0)
            denom = (abs(pred) + abs(actual)) / 2.0
            if denom > 0:
                smape_errors.append(abs_error / denom * 100)
            else:
                smape_errors.append(0.0)  # 둘 다 0 → 완벽히 맞음

            # wMAPE 누적
            sum_abs_errors += abs_error
            sum_actuals += actual

            # 적중률
            if error == 0:
                exact_count += 1
            if abs_error <= 1:
                within_1 += 1
            if abs_error <= 2:
                within_2 += 1
            if abs_error <= 3:
                within_3 += 1

            # 편향
            if error > 0:
                over_predictions.append(error)
            elif error < 0:
                under_predictions.append(abs(error))

        # 계산
        mape = sum(pct_errors) / len(pct_errors) if pct_errors else 0
        mae = sum(abs_errors) / total
        rmse = math.sqrt(sum(squared_errors) / total)
        smape = sum(smape_errors) / len(smape_errors) if smape_errors else 0
        wmape = (sum_abs_errors / sum_actuals * 100) if sum_actuals > 0 else 0

        over_rate = len(over_predictions) / total * 100 if total > 0 else 0
        under_rate = len(under_predictions) / total * 100 if total > 0 else 0
        avg_over = sum(over_predictions) / len(over_predictions) if over_predictions else 0
        avg_under = sum(under_predictions) / len(under_predictions) if under_predictions else 0

        # 날짜 범위
        dates = [p.get("date", "") for p in predictions if p.get("date")]
        period_start = min(dates) if dates else ""
        period_end = max(dates) if dates else ""

        return AccuracyMetrics(
            period_start=period_start,
            period_end=period_end,
            total_predictions=total,
            mape=round(mape, 2),
            mae=round(mae, 2),
            rmse=round(rmse, 2),
            smape=round(smape, 2),
            wmape=round(wmape, 2),
            accuracy_exact=round(exact_count / total * 100, 1),
            accuracy_within_1=round(within_1 / total * 100, 1),
            accuracy_within_2=round(within_2 / total * 100, 1),
            accuracy_within_3=round(within_3 / total * 100, 1),
            over_prediction_rate=round(over_rate, 1),
            under_prediction_rate=round(under_rate, 1),
            avg_over_amount=round(avg_over, 2),
            avg_under_amount=round(avg_under, 2)
        )

    def _empty_metrics(self) -> AccuracyMetrics:
        """빈 메트릭 반환"""
        return AccuracyMetrics(
            period_start="", period_end="",
            total_predictions=0,
            mape=0, mae=0, rmse=0,
            smape=0, wmape=0,
            accuracy_exact=0, accuracy_within_1=0,
            accuracy_within_2=0, accuracy_within_3=0,
            over_prediction_rate=0, under_prediction_rate=0,
            avg_over_amount=0, avg_under_amount=0
        )

    def get_accuracy_by_date(
        self,
        target_date: str
    ) -> AccuracyMetrics:
        """
        특정 날짜의 예측 정확도 조회

        Args:
            target_date: 대상 날짜 (YYYY-MM-DD)

        Returns:
            AccuracyMetrics
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # prediction_logs에서 예측 조회
            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT item_cd, predicted_qty, actual_qty
                FROM prediction_logs
                WHERE prediction_date = ?
                AND actual_qty IS NOT NULL
                {store_filter}
            """, (target_date,) + store_params)

            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return self._empty_metrics()

        predictions = []
        actuals = []

        for item_cd, predicted, actual in rows:
            predictions.append({
                "item_cd": item_cd,
                "predicted_qty": predicted,
                "date": target_date
            })
            actuals.append({
                "item_cd": item_cd,
                "actual_qty": actual,
                "date": target_date
            })

        return self.calculate_metrics(predictions, actuals)

    def get_accuracy_by_period(
        self,
        days: int = 7
    ) -> AccuracyMetrics:
        """
        최근 N일간 정확도 조회
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT item_cd, predicted_qty, actual_qty, prediction_date
                FROM prediction_logs
                WHERE prediction_date >= ?
                AND prediction_date <= ?
                AND actual_qty IS NOT NULL
                {store_filter}
            """, (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")) + store_params)

            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return self._empty_metrics()

        predictions = []
        actuals = []

        for item_cd, predicted, actual, pred_date in rows:
            predictions.append({
                "item_cd": item_cd,
                "predicted_qty": predicted,
                "date": pred_date
            })
            actuals.append({
                "item_cd": item_cd,
                "actual_qty": actual,
                "date": pred_date
            })

        return self.calculate_metrics(predictions, actuals)

    def get_accuracy_by_category(
        self,
        days: int = 7
    ) -> List[CategoryAccuracy]:
        """
        카테고리별 정확도 조회

        Returns:
            카테고리별 정확도 리스트 (정확도 낮은 순 정렬)
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # 카테고리별 예측 데이터 조회
            store_filter = "AND pl.store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT
                    pl.item_cd,
                    pl.predicted_qty,
                    pl.actual_qty,
                    pl.prediction_date,
                    p.mid_cd,
                    COALESCE(m.mid_nm, p.mid_cd) as mid_nm
                FROM prediction_logs pl
                LEFT JOIN products p ON pl.item_cd = p.item_cd
                LEFT JOIN mid_categories m ON p.mid_cd = m.mid_cd
                WHERE pl.prediction_date >= ?
                AND pl.prediction_date <= ?
                AND pl.actual_qty IS NOT NULL
                {store_filter}
            """, (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")) + store_params)

            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return []

        # 카테고리별 그룹핑
        category_data = {}
        for item_cd, predicted, actual, pred_date, mid_cd, mid_nm in rows:
            if mid_cd not in category_data:
                category_data[mid_cd] = {
                    "mid_nm": mid_nm,
                    "predictions": [],
                    "actuals": []
                }

            category_data[mid_cd]["predictions"].append({
                "item_cd": item_cd,
                "predicted_qty": predicted,
                "date": pred_date
            })
            category_data[mid_cd]["actuals"].append({
                "item_cd": item_cd,
                "actual_qty": actual,
                "date": pred_date
            })

        # 카테고리별 정확도 계산
        results = []
        for mid_cd, data in category_data.items():
            metrics = self.calculate_metrics(data["predictions"], data["actuals"])
            results.append(CategoryAccuracy(
                mid_cd=mid_cd or "UNKNOWN",
                mid_nm=data["mid_nm"] or "미분류",
                metrics=metrics
            ))

        # MAPE 높은 순 (정확도 낮은 순) 정렬
        results.sort(key=lambda x: x.metrics.mape, reverse=True)

        return results

    def _get_ranked_items(
        self,
        days: int = 7,
        limit: int = 10,
        min_samples: int = 3,
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        예측 정확도 기준 상품 목록 (worst/best 공용)

        min_samples 이상 데이터가 있는 상품이 없으면
        자동으로 임계값을 낮춰서 (2→1) 재시도합니다.

        Args:
            ascending: True이면 best(SMAPE 낮은 순), False이면 worst(높은 순)

        Returns:
            [{"item_cd", "item_nm", "mape", "mae", "bias", "avg_predicted", "avg_actual"}, ...]
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        order = "ASC" if ascending else "DESC"

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            store_filter = "AND pl.store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            rows = []
            for threshold in (min_samples, 2, 1):
                cursor.execute(f"""
                    SELECT
                        pl.item_cd,
                        COALESCE(p.item_nm, pl.item_cd) as item_nm,
                        p.mid_cd,
                        AVG(ABS(pl.predicted_qty - pl.actual_qty)) as avg_error,
                        AVG(CASE WHEN pl.actual_qty > 0
                            THEN ABS(pl.predicted_qty - pl.actual_qty) * 100.0 / pl.actual_qty
                            END) as mape,
                        AVG(CASE
                            WHEN (ABS(pl.predicted_qty) + ABS(pl.actual_qty)) > 0
                            THEN ABS(pl.predicted_qty - pl.actual_qty) * 200.0
                                 / (ABS(pl.predicted_qty) + ABS(pl.actual_qty))
                            ELSE 0 END) as smape,
                        AVG(pl.predicted_qty - pl.actual_qty) as bias,
                        COUNT(*) as count,
                        AVG(pl.predicted_qty) as avg_predicted,
                        AVG(pl.actual_qty) as avg_actual
                    FROM prediction_logs pl
                    LEFT JOIN products p ON pl.item_cd = p.item_cd
                    WHERE pl.prediction_date >= ?
                    AND pl.prediction_date <= ?
                    AND pl.actual_qty IS NOT NULL
                    {store_filter}
                    GROUP BY pl.item_cd
                    HAVING count >= ?
                    ORDER BY smape {order}
                    LIMIT ?
                """, (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
                     + store_params + (threshold, limit))
                rows = cursor.fetchall()
                if rows:
                    break
        finally:
            conn.close()

        results = []
        for item_cd, item_nm, mid_cd, mae, mape, smape, bias, count, avg_pred, avg_act in rows:
            results.append({
                "item_cd": item_cd,
                "item_nm": item_nm,
                "mid_cd": mid_cd,
                "mape": round(mape, 1) if mape is not None else 0,
                "smape": round(smape, 1),
                "mae": round(mae, 2),
                "bias": round(bias, 2),
                "sample_count": count,
                "avg_predicted": round(avg_pred, 1) if avg_pred is not None else 0,
                "avg_actual": round(avg_act, 1) if avg_act is not None else 0,
            })

        return results

    def get_worst_items(
        self,
        days: int = 7,
        limit: int = 10,
        min_samples: int = 3,
    ) -> List[Dict[str, Any]]:
        """예측 정확도가 가장 낮은 상품 목록 (SMAPE 내림차순)"""
        return self._get_ranked_items(days, limit, min_samples, ascending=False)

    def get_best_items(
        self,
        days: int = 7,
        limit: int = 10,
        min_samples: int = 3,
    ) -> List[Dict[str, Any]]:
        """예측 정확도가 가장 높은 상품 목록 (SMAPE 오름차순)"""
        return self._get_ranked_items(days, limit, min_samples, ascending=True)

    def update_actual_sales(
        self,
        target_date: Optional[str] = None
    ) -> int:
        """
        prediction_logs에 실제 판매량 업데이트

        1단계: daily_sales에 매칭되는 상품 → actual_qty = sale_qty
        2단계: 해당 날짜에 판매 데이터가 수집되었으나 매칭 안 되는 상품 → actual_qty = 0
               (판매 기록이 없다 = 판매 0개)

        Args:
            target_date: 대상 날짜 (None이면 어제)

        Returns:
            업데이트된 건수
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            store_filter_pl = "AND store_id = ?" if self.store_id else ""
            store_filter_ds = "AND ds.store_id = ?" if self.store_id else ""
            store_filter_ds2 = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            # 1단계: daily_sales에 매칭되는 상품 → actual_qty = sale_qty
            cursor.execute(f"""
                UPDATE prediction_logs
                SET actual_qty = (
                    SELECT ds.sale_qty
                    FROM daily_sales ds
                    WHERE ds.item_cd = prediction_logs.item_cd
                    AND ds.sales_date = prediction_logs.target_date
                    {store_filter_ds}
                )
                WHERE target_date = ?
                AND actual_qty IS NULL
                {store_filter_pl}
                AND EXISTS (
                    SELECT 1 FROM daily_sales ds
                    WHERE ds.item_cd = prediction_logs.item_cd
                    AND ds.sales_date = prediction_logs.target_date
                    {store_filter_ds}
                )
            """, store_params + (target_date,) + store_params + store_params)

            updated_matched = cursor.rowcount

            # 2단계: 해당 날짜에 판매 데이터가 수집된 것이 확인되면,
            # daily_sales에 없는 상품 = 판매 0개 → actual_qty = 0 설정
            # (판매 데이터가 아예 수집되지 않은 날짜는 건드리지 않음)
            has_sales_data = cursor.execute(f"""
                SELECT COUNT(*) FROM daily_sales
                WHERE sales_date = ? {store_filter_ds2}
            """, (target_date,) + store_params).fetchone()[0]

            updated_zero = 0
            if has_sales_data > 0:
                cursor.execute(f"""
                    UPDATE prediction_logs
                    SET actual_qty = 0
                    WHERE target_date = ?
                    AND actual_qty IS NULL
                    {store_filter_pl}
                    AND NOT EXISTS (
                        SELECT 1 FROM daily_sales ds
                        WHERE ds.item_cd = prediction_logs.item_cd
                        AND ds.sales_date = prediction_logs.target_date
                        {store_filter_ds}
                    )
                """, (target_date,) + store_params + store_params)
                updated_zero = cursor.rowcount

            conn.commit()
        finally:
            conn.close()

        return updated_matched + updated_zero

    def backfill_actual_sales(self, lookback_days: int = 7) -> int:
        """
        최근 N일 중 actual_qty IS NULL인 레코드 소급 업데이트
        오늘(i=0)부터 N일 전까지 포함

        Args:
            lookback_days: 소급 일수 (기본 7일)

        Returns:
            총 업데이트된 건수
        """
        total = 0
        for i in range(lookback_days):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            updated = self.update_actual_sales(d)
            total += updated
        return total

    def get_daily_mape_trend(
        self,
        days: int = 14
    ) -> List[Dict[str, Any]]:
        """
        일별 MAPE/SMAPE 추이

        Returns:
            [{"date": ..., "mape": ..., "smape": ..., "count": ..., "sold_count": ...}, ...]
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()
            cursor.execute(f"""
                SELECT
                    prediction_date,
                    AVG(CASE WHEN actual_qty > 0
                        THEN ABS(predicted_qty - actual_qty) * 100.0 / actual_qty
                        END) as mape,
                    AVG(CASE
                        WHEN (ABS(predicted_qty) + ABS(actual_qty)) > 0
                        THEN ABS(predicted_qty - actual_qty) * 200.0
                             / (ABS(predicted_qty) + ABS(actual_qty))
                        ELSE 0 END) as smape,
                    COUNT(*) as total_count,
                    SUM(CASE WHEN actual_qty > 0 THEN 1 ELSE 0 END) as sold_count
                FROM prediction_logs
                WHERE prediction_date >= ?
                AND prediction_date <= ?
                AND actual_qty IS NOT NULL
                {store_filter}
                GROUP BY prediction_date
                ORDER BY prediction_date
            """, (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")) + store_params)

            rows = cursor.fetchall()
        finally:
            conn.close()

        return [
            {
                "date": date,
                "mape": round(mape, 1) if mape is not None else 0,
                "smape": round(smape, 1) if smape is not None else 0,
                "count": total_count,
                "sold_count": sold_count or 0,
            }
            for date, mape, smape, total_count, sold_count in rows
        ]

    # ── v55: Rule vs ML 분리 정확도 분석 ──────────────────────────

    def get_rule_vs_ml_accuracy(self, days: int = 14) -> Dict[str, Any]:
        """Rule 단독 MAE vs 블렌딩(Rule+ML) MAE 비교

        prediction_logs에 rule_order_qty가 저장된 레코드만 대상으로
        Rule 단독 정확도와 최종 블렌딩 정확도를 비교합니다.

        Args:
            days: 분석 기간 (기본 14일)

        Returns:
            {
                "total_records": int,
                "rule_mae": float,
                "blended_mae": float,
                "ml_contribution": float,  # (rule_mae - blended_mae) / rule_mae * 100
                "avg_ml_weight": float,
                "by_group": {group_name: {rule_mae, blended_mae, count, avg_ml_weight}, ...}
            }
        """
        from src.prediction.ml.feature_builder import get_category_group

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            store_filter = "AND pl.store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT
                    pl.item_cd,
                    pl.order_qty,
                    pl.rule_order_qty,
                    pl.ml_order_qty,
                    pl.ml_weight_used,
                    pl.actual_qty,
                    p.mid_cd
                FROM prediction_logs pl
                LEFT JOIN products p ON pl.item_cd = p.item_cd
                WHERE pl.prediction_date >= ?
                AND pl.prediction_date <= ?
                AND pl.actual_qty IS NOT NULL
                AND pl.rule_order_qty IS NOT NULL
                {store_filter}
            """, (start_date.strftime("%Y-%m-%d"),
                  end_date.strftime("%Y-%m-%d")) + store_params)

            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return {"total_records": 0, "message": "아직 Rule vs ML 비교 데이터가 없습니다"}

        # 전체 및 그룹별 집계
        total_rule_errors = []
        total_blended_errors = []
        total_ml_weights = []
        group_data: Dict[str, Dict[str, list]] = {}

        for item_cd, order_qty, rule_oq, ml_oq, ml_w, actual, mid_cd in rows:
            if actual is None:
                continue

            rule_error = abs((rule_oq or 0) - actual)
            blended_error = abs((order_qty or 0) - actual)

            total_rule_errors.append(rule_error)
            total_blended_errors.append(blended_error)
            if ml_w is not None:
                total_ml_weights.append(ml_w)

            group = get_category_group(mid_cd or "")
            if group not in group_data:
                group_data[group] = {
                    "rule_errors": [], "blended_errors": [], "ml_weights": []
                }
            group_data[group]["rule_errors"].append(rule_error)
            group_data[group]["blended_errors"].append(blended_error)
            if ml_w is not None:
                group_data[group]["ml_weights"].append(ml_w)

        n = len(total_rule_errors)
        rule_mae = sum(total_rule_errors) / n
        blended_mae = sum(total_blended_errors) / n
        avg_w = sum(total_ml_weights) / len(total_ml_weights) if total_ml_weights else 0
        contribution = ((rule_mae - blended_mae) / rule_mae * 100) if rule_mae > 0 else 0

        by_group = {}
        for gname, gd in sorted(group_data.items()):
            gc = len(gd["rule_errors"])
            g_rule = sum(gd["rule_errors"]) / gc
            g_blend = sum(gd["blended_errors"]) / gc
            g_w = sum(gd["ml_weights"]) / len(gd["ml_weights"]) if gd["ml_weights"] else 0
            by_group[gname] = {
                "rule_mae": round(g_rule, 3),
                "blended_mae": round(g_blend, 3),
                "count": gc,
                "avg_ml_weight": round(g_w, 4),
            }

        return {
            "total_records": n,
            "rule_mae": round(rule_mae, 3),
            "blended_mae": round(blended_mae, 3),
            "ml_contribution": round(contribution, 2),
            "avg_ml_weight": round(avg_w, 4),
            "by_group": by_group,
        }

    def get_ml_weight_effectiveness(self, days: int = 14) -> Dict[str, Any]:
        """ML 가중치 구간별 MAE 분석

        ml_weight_used 값을 구간(0~0.05, 0.05~0.15, 0.15~0.25, 0.25+)별로
        그룹핑하여 MAE를 비교합니다. 어떤 가중치 구간이 가장 효과적인지 파악용.

        Args:
            days: 분석 기간 (기본 14일)

        Returns:
            {
                "total_records": int,
                "buckets": [
                    {"range": "0.00-0.05", "count": int, "mae": float,
                     "rule_mae": float, "improvement": float}, ...
                ]
            }
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if self.store_id else ""
            store_params = (self.store_id,) if self.store_id else ()

            cursor.execute(f"""
                SELECT
                    order_qty,
                    rule_order_qty,
                    ml_weight_used,
                    actual_qty
                FROM prediction_logs
                WHERE prediction_date >= ?
                AND prediction_date <= ?
                AND actual_qty IS NOT NULL
                AND ml_weight_used IS NOT NULL
                {store_filter}
            """, (start_date.strftime("%Y-%m-%d"),
                  end_date.strftime("%Y-%m-%d")) + store_params)

            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return {"total_records": 0, "message": "아직 ML 가중치 효과 데이터가 없습니다"}

        # 구간 정의
        bucket_ranges = [
            (0.00, 0.05, "0.00-0.05"),
            (0.05, 0.15, "0.05-0.15"),
            (0.15, 0.25, "0.15-0.25"),
            (0.25, 1.01, "0.25+"),
        ]

        bucket_data: Dict[str, Dict[str, list]] = {
            label: {"blended_errors": [], "rule_errors": []}
            for _, _, label in bucket_ranges
        }

        for order_qty, rule_oq, ml_w, actual in rows:
            if actual is None or ml_w is None:
                continue

            blended_error = abs((order_qty or 0) - actual)
            rule_error = abs((rule_oq or 0) - actual)

            for lo, hi, label in bucket_ranges:
                if lo <= ml_w < hi:
                    bucket_data[label]["blended_errors"].append(blended_error)
                    bucket_data[label]["rule_errors"].append(rule_error)
                    break

        buckets = []
        for _, _, label in bucket_ranges:
            bd = bucket_data[label]
            cnt = len(bd["blended_errors"])
            if cnt == 0:
                buckets.append({"range": label, "count": 0, "mae": 0, "rule_mae": 0, "improvement": 0})
                continue

            mae = sum(bd["blended_errors"]) / cnt
            r_mae = sum(bd["rule_errors"]) / cnt
            improvement = ((r_mae - mae) / r_mae * 100) if r_mae > 0 else 0

            buckets.append({
                "range": label,
                "count": cnt,
                "mae": round(mae, 3),
                "rule_mae": round(r_mae, 3),
                "improvement": round(improvement, 2),
            })

        return {
            "total_records": len(rows),
            "buckets": buckets,
        }
