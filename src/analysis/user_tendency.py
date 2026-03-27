"""
카테고리별 점주 발주 개입 성향 분석 (UserTendencyAnalyzer)

order_analysis.db의 order_diffs 데이터를 분석하여
카테고리(mid_cd)별 점주 개입 패턴을 판정하고 매장 DB에 저장한다.

판정 기준 (order_diffs 기반):
- remove_rate >= 0.3 AND add_rate < 0.3  → 'passive'    (소극형: AI 발주를 많이 삭제)
- add_rate >= 0.3 AND remove_rate < 0.3  → 'aggressive' (적극형: 독자 추가가 많음)
- remove_rate >= 0.3 AND add_rate >= 0.3 → 'selective'  (선택적 개입: 삭제+추가 둘 다 많음)
- remove_rate < 0.1 AND add_rate < 0.1   → 'trust'      (AI신뢰형: 거의 변경 없음)
- 그 외                                  → 'balanced'   (균형형)
- 전체 개입건수 < 10                     → 'insufficient_data'
"""

import sqlite3
from datetime import datetime
from typing import Optional

import pandas as pd

from src.infrastructure.database.connection import DBRouter
from src.infrastructure.database.repos.order_analysis_repo import (
    OrderAnalysisRepository,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 판정 임계값
REMOVE_HIGH = 0.3       # 삭제 비율 높음 기준
ADD_HIGH = 0.3          # 추가 비율 높음 기준
TRUST_LOW = 0.1         # 신뢰형 기준 (삭제/추가 둘 다 낮음)
MIN_INTERVENTION = 10   # 최소 개입 건수

# 하위 호환용 export (기존 테스트에서 참조)
PASSIVE_THRESHOLD = 0.7
AGGRESSIVE_THRESHOLD = 1.3
MIN_SITE_COUNT = MIN_INTERVENTION


class UserTendencyAnalyzer:
    """카테고리별 점주 발주 개입 성향 분석 (order_diffs 기반)"""

    def __init__(self, store_id: str, analysis_repo: Optional[OrderAnalysisRepository] = None):
        self.store_id = store_id
        self._analysis_repo = analysis_repo or OrderAnalysisRepository()

    def _get_store_conn(self) -> sqlite3.Connection:
        """매장 DB 연결"""
        return DBRouter.get_connection(
            store_id=self.store_id, table="user_order_tendency"
        )

    def _get_analysis_conn(self) -> sqlite3.Connection:
        """order_analysis.db 연결"""
        return self._analysis_repo.get_connection()

    def analyze(self, period_days: int = 90) -> dict:
        """order_diffs 기반 카테고리별 성향 계산 후 user_order_tendency에 UPSERT

        Returns:
            {mid_cd: tendency, ...}
        """
        analysis_conn = self._get_analysis_conn()
        try:
            # 1) order_diffs에서 카테고리별 diff_type 집계
            diff_df = pd.read_sql(
                """
                SELECT
                    mid_cd,
                    COUNT(CASE WHEN diff_type = 'removed' THEN 1 END) AS removed_count,
                    COUNT(CASE WHEN diff_type = 'added' THEN 1 END) AS added_count,
                    COUNT(CASE WHEN diff_type = 'qty_changed' THEN 1 END) AS qty_changed_count,
                    COUNT(CASE WHEN diff_type = 'qty_changed'
                          AND confirmed_order_qty > auto_order_qty THEN 1 END) AS qty_up_count,
                    COUNT(CASE WHEN diff_type = 'qty_changed'
                          AND confirmed_order_qty < auto_order_qty THEN 1 END) AS qty_down_count
                FROM order_diffs
                WHERE store_id = ?
                  AND order_date >= date('now', ?)
                  AND mid_cd IS NOT NULL
                  AND mid_cd != ''
                GROUP BY mid_cd
                """,
                analysis_conn,
                params=(self.store_id, f"-{period_days} days"),
            )
        finally:
            analysis_conn.close()

        if diff_df.empty:
            logger.info(f"[{self.store_id}] order_diffs 데이터 없음")
            return {}

        # 2) 매장 DB에서 카테고리별 stock_qty=0 비율
        store_conn = self._get_store_conn()
        try:
            stock_df = pd.read_sql(
                """
                SELECT
                    mid_cd,
                    ROUND(
                        AVG(CASE WHEN stock_qty = 0 THEN 1.0 ELSE 0.0 END),
                        4
                    ) AS zero_stock_rate
                FROM daily_sales
                WHERE sales_date >= date('now', ?)
                GROUP BY mid_cd
                """,
                store_conn,
                params=(f"-{period_days} days",),
            )
        except Exception:
            stock_df = pd.DataFrame(columns=["mid_cd", "zero_stock_rate"])
        finally:
            store_conn.close()

        # 3) 병합
        df = diff_df.merge(stock_df, on="mid_cd", how="left")
        df["zero_stock_rate"] = df["zero_stock_rate"].fillna(0.0)

        # 4) 성향 판정 + UPSERT
        results = {}
        now = datetime.now().isoformat()

        upsert_conn = self._get_store_conn()
        try:
            cursor = upsert_conn.cursor()

            for _, row in df.iterrows():
                mid_cd = row["mid_cd"]
                removed = int(row["removed_count"])
                added = int(row["added_count"])
                qty_changed = int(row["qty_changed_count"])
                qty_up = int(row["qty_up_count"])
                qty_down = int(row["qty_down_count"])
                zsr = float(row["zero_stock_rate"])

                total_interventions = removed + added + qty_changed

                # 비율 계산
                if total_interventions > 0:
                    remove_rate = round(removed / total_interventions, 4)
                    add_rate = round(added / total_interventions, 4)
                else:
                    remove_rate = 0.0
                    add_rate = 0.0

                qty_up_rate = round(qty_up / qty_changed, 4) if qty_changed > 0 else None

                # 판정
                if total_interventions < MIN_INTERVENTION:
                    tendency = "insufficient_data"
                elif remove_rate >= REMOVE_HIGH and add_rate >= ADD_HIGH:
                    tendency = "selective"
                elif remove_rate >= REMOVE_HIGH:
                    tendency = "passive"
                elif add_rate >= ADD_HIGH:
                    tendency = "aggressive"
                elif remove_rate < TRUST_LOW and add_rate < TRUST_LOW:
                    tendency = "trust"
                else:
                    tendency = "balanced"

                results[mid_cd] = tendency

                # UPSERT
                cursor.execute(
                    """
                    INSERT INTO user_order_tendency
                        (store_id, mid_cd, period_days,
                         removed_count, added_count,
                         qty_changed_count, qty_up_count, qty_down_count,
                         remove_rate, add_rate, qty_up_rate,
                         tendency, zero_stock_rate, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, mid_cd) DO UPDATE SET
                        period_days = excluded.period_days,
                        removed_count = excluded.removed_count,
                        added_count = excluded.added_count,
                        qty_changed_count = excluded.qty_changed_count,
                        qty_up_count = excluded.qty_up_count,
                        qty_down_count = excluded.qty_down_count,
                        remove_rate = excluded.remove_rate,
                        add_rate = excluded.add_rate,
                        qty_up_rate = excluded.qty_up_rate,
                        tendency = excluded.tendency,
                        zero_stock_rate = excluded.zero_stock_rate,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.store_id, mid_cd, period_days,
                        removed, added, qty_changed, qty_up, qty_down,
                        remove_rate, add_rate, qty_up_rate,
                        tendency, zsr, now,
                    ),
                )

            upsert_conn.commit()

            # 로그
            tendency_counts = {}
            for v in results.values():
                tendency_counts[v] = tendency_counts.get(v, 0) + 1
            summary_str = ", ".join(f"{k}={v}" for k, v in sorted(tendency_counts.items()))
            logger.info(
                f"[{self.store_id}] 성향 분석 완료 (order_diffs 기반): "
                f"{len(results)}개 카테고리 ({summary_str})"
            )
            return results
        finally:
            upsert_conn.close()

    def get_passive_categories(self) -> list[str]:
        """소극형 mid_cd 목록 반환 (safety_stock 보정 연동용)"""
        conn = self._get_store_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mid_cd FROM user_order_tendency
                WHERE store_id = ? AND tendency = 'passive'
                ORDER BY remove_rate DESC
                """,
                (self.store_id,),
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_tendency(self, mid_cd: str) -> Optional[str]:
        """특정 카테고리의 성향 반환"""
        conn = self._get_store_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tendency FROM user_order_tendency
                WHERE store_id = ? AND mid_cd = ?
                """,
                (self.store_id, mid_cd),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def get_summary(self) -> pd.DataFrame:
        """전체 성향 요약 테이블 반환 (대시보드용)"""
        conn = self._get_store_conn()
        try:
            return pd.read_sql(
                """
                SELECT
                    mid_cd,
                    removed_count,
                    added_count,
                    qty_changed_count,
                    qty_up_count,
                    qty_down_count,
                    remove_rate,
                    add_rate,
                    qty_up_rate,
                    tendency,
                    zero_stock_rate,
                    updated_at
                FROM user_order_tendency
                WHERE store_id = ?
                ORDER BY
                    CASE tendency
                        WHEN 'passive' THEN 1
                        WHEN 'selective' THEN 2
                        WHEN 'balanced' THEN 3
                        WHEN 'aggressive' THEN 4
                        WHEN 'trust' THEN 5
                        ELSE 6 END,
                    remove_rate DESC
                """,
                conn,
                params=(self.store_id,),
            )
        finally:
            conn.close()
