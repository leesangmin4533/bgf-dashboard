"""
EvalOutcomeRepository — 사전 발주 평가 결과 추적 저장소

원본: src/db/repository.py EvalOutcomeRepository 클래스에서 1:1 추출
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EvalOutcomeRepository(BaseRepository):
    """사전 발주 평가 결과 추적 저장소"""

    db_type = "store"

    def save_eval_result(
        self,
        eval_date: str,
        item_cd: str,
        mid_cd: str,
        decision: str,
        exposure_days: float,
        popularity_score: float,
        daily_avg: float,
        current_stock: int,
        pending_qty: int,
        weekday: Optional[int] = None,
        delivery_batch: Optional[str] = None,
        sell_price: Optional[int] = None,
        margin_rate: Optional[float] = None,
        promo_type: Optional[str] = None,
        trend_score: Optional[float] = None,
        stockout_freq: Optional[float] = None,
        store_id: Optional[str] = None
    ) -> int:
        """평가 결과 저장 (사후 검증 필드는 나중에 업데이트)

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            item_cd: 상품 코드
            mid_cd: 중분류 코드
            decision: 결정 (FORCE_ORDER, URGENT_ORDER, NORMAL_ORDER, PASS, SKIP)
            exposure_days: 예측 노출일수
            popularity_score: 인기도 점수
            daily_avg: 일평균 판매량
            current_stock: 평가 시점 재고
            pending_qty: 미입고 수량
            weekday: 요일 (0=월 ~ 6=일)
            delivery_batch: 배송 차수 (1차/2차/None)
            sell_price: 매가 (원)
            margin_rate: 이익율 (0~1)
            promo_type: 행사 타입 (1+1, 2+1 등)
            trend_score: 트렌드 점수 (7일/30일 비율)
            stockout_freq: 품절 빈도 (30일 중 비율)
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO eval_outcomes
                (eval_date, item_cd, mid_cd, decision, exposure_days,
                 popularity_score, daily_avg, current_stock, pending_qty,
                 weekday, delivery_batch, sell_price, margin_rate,
                 promo_type, trend_score, stockout_freq, store_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, eval_date, item_cd) DO UPDATE SET
                    decision = excluded.decision,
                    exposure_days = excluded.exposure_days,
                    popularity_score = excluded.popularity_score,
                    daily_avg = excluded.daily_avg,
                    current_stock = excluded.current_stock,
                    pending_qty = excluded.pending_qty,
                    weekday = excluded.weekday,
                    delivery_batch = excluded.delivery_batch,
                    sell_price = excluded.sell_price,
                    margin_rate = excluded.margin_rate,
                    promo_type = excluded.promo_type,
                    trend_score = excluded.trend_score,
                    stockout_freq = excluded.stockout_freq
                """,
                (eval_date, item_cd, mid_cd, decision, exposure_days,
                 popularity_score, daily_avg, current_stock, pending_qty,
                 weekday, delivery_batch, sell_price, margin_rate,
                 promo_type, trend_score, stockout_freq, store_id, self._now())
            )
            row_id = cursor.lastrowid
            conn.commit()
            return row_id
        finally:
            conn.close()

    def save_eval_results_batch(
        self,
        eval_date: str,
        results: List[Dict[str, Any]],
        store_id: Optional[str] = None
    ) -> int:
        """평가 결과 일괄 저장

        Args:
            eval_date: 평가일
            results: [{"item_cd", "mid_cd", "decision", "exposure_days",
                       "popularity_score", "daily_avg", "current_stock", "pending_qty",
                       "weekday", "delivery_batch", "sell_price", "margin_rate",
                       "promo_type", "trend_score", "stockout_freq"}, ...]
            store_id: 매장 코드

        Returns:
            저장된 건수
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            count = 0
            for r in results:
                cursor.execute(
                    """
                    INSERT INTO eval_outcomes
                    (eval_date, item_cd, mid_cd, decision, exposure_days,
                     popularity_score, daily_avg, current_stock, pending_qty,
                     weekday, delivery_batch, sell_price, margin_rate,
                     promo_type, trend_score, stockout_freq, store_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, eval_date, item_cd) DO UPDATE SET
                        decision = excluded.decision,
                        exposure_days = excluded.exposure_days,
                        popularity_score = excluded.popularity_score,
                        daily_avg = excluded.daily_avg,
                        current_stock = excluded.current_stock,
                        pending_qty = excluded.pending_qty,
                        weekday = excluded.weekday,
                        delivery_batch = excluded.delivery_batch,
                        sell_price = excluded.sell_price,
                        margin_rate = excluded.margin_rate,
                        promo_type = excluded.promo_type,
                        trend_score = excluded.trend_score,
                        stockout_freq = excluded.stockout_freq
                    """,
                    (eval_date, r["item_cd"], r.get("mid_cd", ""),
                     r["decision"], r.get("exposure_days", 0),
                     r.get("popularity_score", 0), r.get("daily_avg", 0),
                     r.get("current_stock", 0), r.get("pending_qty", 0),
                     r.get("weekday"), r.get("delivery_batch"),
                     r.get("sell_price"), r.get("margin_rate"),
                     r.get("promo_type"), r.get("trend_score"),
                     r.get("stockout_freq"), store_id, now)
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def update_outcome(
        self,
        eval_date: str,
        item_cd: str,
        actual_sold_qty: int,
        next_day_stock: int,
        was_stockout: bool,
        was_waste: bool,
        outcome: str,
        disuse_qty: Optional[int] = None,
        store_id: Optional[str] = None
    ) -> bool:
        """사후 검증 결과 업데이트

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            item_cd: 상품 코드
            actual_sold_qty: 실제 판매량
            next_day_stock: 다음날 재고
            was_stockout: 품절 발생 여부
            was_waste: 폐기 발생 여부
            outcome: 결과 (CORRECT, UNDER_ORDER, OVER_ORDER, MISS)
            disuse_qty: 폐기 수량
            store_id: 매장 코드

        Returns:
            업데이트 성공 여부
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                UPDATE eval_outcomes
                SET actual_sold_qty = ?,
                    next_day_stock = ?,
                    was_stockout = ?,
                    was_waste = ?,
                    outcome = ?,
                    disuse_qty = ?,
                    verified_at = ?
                WHERE eval_date = ? AND item_cd = ? {sf}
                """,
                (actual_sold_qty, next_day_stock,
                 1 if was_stockout else 0, 1 if was_waste else 0,
                 outcome, disuse_qty, self._now(), eval_date, item_cd) + sp
            )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated
        finally:
            conn.close()

    def bulk_update_outcomes(
        self,
        updates: list,
        store_id: Optional[str] = None,
    ) -> int:
        """사후 검증 결과 벌크 업데이트 (소급 검증용)

        Args:
            updates: [(actual_sold_qty, next_day_stock, was_stockout, was_waste,
                       outcome, disuse_qty, verified_at, eval_date, item_cd), ...]
            store_id: 매장 코드

        Returns:
            업데이트된 행 수
        """
        if not updates:
            return 0
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            total_updated = 0
            for row in updates:
                cursor.execute(
                    f"""
                    UPDATE eval_outcomes
                    SET actual_sold_qty = ?,
                        next_day_stock = ?,
                        was_stockout = ?,
                        was_waste = ?,
                        outcome = ?,
                        disuse_qty = ?,
                        verified_at = ?
                    WHERE eval_date = ? AND item_cd = ? {sf}
                    """,
                    row + sp,
                )
                total_updated += cursor.rowcount
            conn.commit()
            return total_updated
        finally:
            conn.close()

    def update_order_result(
        self,
        eval_date: str,
        item_cd: str,
        predicted_qty: Optional[int] = None,
        actual_order_qty: Optional[int] = None,
        order_status: Optional[str] = None,
        store_id: Optional[str] = None
    ) -> bool:
        """발주 실행 결과 업데이트 (예측량, 실제 발주량, 발주 상태)

        execute() 완료 후 호출하여 predicted_qty, actual_order_qty, order_status 기록

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            item_cd: 상품 코드
            predicted_qty: 예측기 산출 발주량
            actual_order_qty: 실제 발주된 수량
            order_status: 발주 상태 (pending, success, fail)
            store_id: 매장 코드

        Returns:
            업데이트 성공 여부
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            # 설정된 필드만 동적으로 업데이트
            updates = []
            params = []
            if predicted_qty is not None:
                updates.append("predicted_qty = ?")
                params.append(predicted_qty)
            if actual_order_qty is not None:
                updates.append("actual_order_qty = ?")
                params.append(actual_order_qty)
            if order_status is not None:
                updates.append("order_status = ?")
                params.append(order_status)

            if not updates:
                return False

            params.extend([eval_date, item_cd])
            cursor.execute(
                f"""
                UPDATE eval_outcomes
                SET {', '.join(updates)}
                WHERE eval_date = ? AND item_cd = ? {sf}
                """,
                params + list(sp)
            )
            updated = cursor.rowcount > 0
            conn.commit()
            return updated
        finally:
            conn.close()

    def get_unverified(self, eval_date: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """검증되지 않은 평가 결과 조회

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            outcome이 NULL인 평가 결과 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                SELECT * FROM eval_outcomes
                WHERE eval_date = ? AND outcome IS NULL {sf}
                ORDER BY item_cd
                """,
                (eval_date,) + sp
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_unverified_range(
        self,
        start_date: str,
        end_date: str,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """기간 내 검증되지 않은 평가 결과 일괄 조회 (소급 검증용)

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD, 미포함)
            store_id: 매장 코드

        Returns:
            outcome이 NULL인 평가 결과 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                SELECT * FROM eval_outcomes
                WHERE eval_date >= ? AND eval_date < ?
                  AND outcome IS NULL {sf}
                ORDER BY eval_date, item_cd
                """,
                (start_date, end_date) + sp,
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_outcomes_by_date(self, eval_date: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """특정 날짜의 모든 평가 결과 조회

        Args:
            eval_date: 평가일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            해당 날짜의 전체 평가 결과 목록 (상품명 포함)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter("eo", store_id)
            cursor.execute(
                f"""
                SELECT eo.*, p.item_nm
                FROM eval_outcomes eo
                LEFT JOIN products p ON eo.item_cd = p.item_cd
                WHERE eo.eval_date = ? {sf}
                ORDER BY eo.decision, eo.item_cd
                """,
                (eval_date,) + sp
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_accuracy_stats(self, days: int = 30, store_id: Optional[str] = None) -> Dict[str, Any]:
        """
        결정별 적중률 통계 (최근 N일)

        Args:
            days: 조회할 최근 일수
            store_id: 매장 코드

        Returns:
            {
                "total_verified": int,
                "by_decision": {
                    "FORCE_ORDER": {"total": N, "correct": N, "accuracy": 0.XX},
                    ...
                },
                "overall_accuracy": float
            }
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                SELECT
                    decision,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'CORRECT' THEN 1 ELSE 0 END) as correct,
                    SUM(CASE WHEN outcome = 'UNDER_ORDER' THEN 1 ELSE 0 END) as under_order,
                    SUM(CASE WHEN outcome = 'OVER_ORDER' THEN 1 ELSE 0 END) as over_order,
                    SUM(CASE WHEN outcome = 'MISS' THEN 1 ELSE 0 END) as miss
                FROM eval_outcomes
                WHERE outcome IS NOT NULL
                AND eval_date >= date('now', '-' || ? || ' days') {sf}
                GROUP BY decision
                """,
                (days,) + sp
            )
            by_decision = {}
            total_verified = 0
            total_correct = 0
            for row in cursor.fetchall():
                d = dict(row)
                decision = d["decision"]
                total = d["total"]
                correct = d["correct"]
                by_decision[decision] = {
                    "total": total,
                    "correct": correct,
                    "under_order": d["under_order"],
                    "over_order": d["over_order"],
                    "miss": d["miss"],
                    "accuracy": round(correct / total, 4) if total > 0 else 0.0,
                }
                total_verified += total
                total_correct += correct

            return {
                "total_verified": total_verified,
                "by_decision": by_decision,
                "overall_accuracy": round(total_correct / total_verified, 4) if total_verified > 0 else 0.0,
                "period_days": days,
            }
        finally:
            conn.close()

    def get_correlation_data(self, days: int = 30, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        상관관계 분석용 데이터 조회 (일평균, 판매일비율, 트렌드 vs 실제판매)

        Args:
            days: 조회할 최근 일수
            store_id: 매장 코드

        Returns:
            [{"item_cd", "daily_avg", "popularity_score", "actual_sold_qty", "was_stockout"}, ...]
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                SELECT item_cd, daily_avg, popularity_score,
                       exposure_days, actual_sold_qty, was_stockout, decision, outcome
                FROM eval_outcomes
                WHERE outcome IS NOT NULL
                AND actual_sold_qty IS NOT NULL
                AND eval_date >= date('now', '-' || ? || ' days') {sf}
                """,
                (days,) + sp
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_skip_stockout_stats(self, days: int = 30, store_id: Optional[str] = None) -> Dict[str, Any]:
        """SKIP 결정 후 품절 발생 통계

        Args:
            days: 조회할 최근 일수
            store_id: 매장 코드

        Returns:
            SKIP 총 건수, 품절 발생 수, 미스율 등 통계
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_skip,
                    SUM(CASE WHEN was_stockout = 1 THEN 1 ELSE 0 END) as stockout_after_skip,
                    AVG(exposure_days) as avg_exposure_days,
                    MIN(exposure_days) as min_exposure_days
                FROM eval_outcomes
                WHERE decision = 'SKIP'
                AND outcome IS NOT NULL
                AND eval_date >= date('now', '-' || ? || ' days') {sf}
                """,
                (days,) + sp
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                total = d["total_skip"] or 0
                stockout = d["stockout_after_skip"] or 0
                return {
                    "total_skip": total,
                    "stockout_after_skip": stockout,
                    "miss_rate": round(stockout / total, 4) if total > 0 else 0.0,
                    "avg_exposure_days": d["avg_exposure_days"],
                    "min_exposure_days": d["min_exposure_days"],
                }
            return {"total_skip": 0, "stockout_after_skip": 0, "miss_rate": 0.0}
        finally:
            conn.close()

    def get_upgrade_stats(self, days: int = 30, store_id: Optional[str] = None) -> Dict[str, Any]:
        """품절빈도 업그레이드 적중 통계 (URGENT/NORMAL 중 품절 방지 건수)

        Args:
            days: 조회할 최근 일수
            store_id: 매장 코드

        Returns:
            업그레이드 총 건수, 적중 수, 과잉발주 수, 적중률 등 통계
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_upgraded,
                    SUM(CASE WHEN outcome = 'CORRECT' THEN 1 ELSE 0 END) as correct,
                    SUM(CASE WHEN outcome = 'OVER_ORDER' THEN 1 ELSE 0 END) as over_order
                FROM eval_outcomes
                WHERE decision IN ('URGENT_ORDER', 'NORMAL_ORDER')
                AND outcome IS NOT NULL
                AND eval_date >= date('now', '-' || ? || ' days') {sf}
                """,
                (days,) + sp
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                total = d["total_upgraded"] or 0
                correct = d["correct"] or 0
                return {
                    "total_upgraded": total,
                    "correct": correct,
                    "over_order": d["over_order"] or 0,
                    "accuracy": round(correct / total, 4) if total > 0 else 0.0,
                }
            return {"total_upgraded": 0, "correct": 0, "over_order": 0, "accuracy": 0.0}
        finally:
            conn.close()
