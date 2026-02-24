"""
발주 분석 DB 저장소 (data/order_analysis.db)

운영 DB(stores/*.db)와 완전 분리된 분석 전용 DB.
자동발주 스냅샷, 사용자 수정 차이(diff), 일별 요약을 저장한다.

BaseRepository를 상속하지 않고 독립 커넥션 관리.
DB 파일은 첫 사용 시 자동 생성 (lazy init).
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 프로젝트 루트(bgf_auto/) 기준 data 디렉토리
# __file__ = src/infrastructure/database/repos/order_analysis_repo.py
# .parent x5 = bgf_auto/
DATA_DIR = Path(__file__).parent.parent.parent.parent.parent / "data"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS order_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    predicted_qty INTEGER DEFAULT 0,
    recommended_qty INTEGER DEFAULT 0,
    final_order_qty INTEGER DEFAULT 0,
    current_stock INTEGER DEFAULT 0,
    pending_qty INTEGER DEFAULT 0,
    eval_decision TEXT,
    order_unit_qty INTEGER DEFAULT 1,
    order_success INTEGER DEFAULT 0,
    confidence TEXT,
    delivery_type TEXT,
    data_days INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, item_cd)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_store_date
    ON order_snapshots(store_id, order_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_item
    ON order_snapshots(item_cd);

CREATE TABLE IF NOT EXISTS order_diffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    receiving_date TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    diff_type TEXT NOT NULL,
    auto_order_qty INTEGER DEFAULT 0,
    predicted_qty INTEGER DEFAULT 0,
    eval_decision TEXT,
    confirmed_order_qty INTEGER DEFAULT 0,
    receiving_qty INTEGER DEFAULT 0,
    qty_diff INTEGER DEFAULT 0,
    receiving_diff INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, item_cd, receiving_date)
);

CREATE INDEX IF NOT EXISTS idx_diff_store_date
    ON order_diffs(store_id, order_date);
CREATE INDEX IF NOT EXISTS idx_diff_type
    ON order_diffs(diff_type);
CREATE INDEX IF NOT EXISTS idx_diff_item
    ON order_diffs(item_cd);

CREATE TABLE IF NOT EXISTS order_diff_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    receiving_date TEXT NOT NULL,
    total_auto_items INTEGER DEFAULT 0,
    total_confirmed_items INTEGER DEFAULT 0,
    items_unchanged INTEGER DEFAULT 0,
    items_qty_changed INTEGER DEFAULT 0,
    items_added INTEGER DEFAULT 0,
    items_removed INTEGER DEFAULT 0,
    items_not_comparable INTEGER DEFAULT 0,
    total_auto_qty INTEGER DEFAULT 0,
    total_confirmed_qty INTEGER DEFAULT 0,
    total_receiving_qty INTEGER DEFAULT 0,
    match_rate REAL DEFAULT 0,
    has_receiving_data INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, receiving_date)
);

CREATE INDEX IF NOT EXISTS idx_summary_store_date
    ON order_diff_summary(store_id, order_date);

CREATE TABLE IF NOT EXISTS stock_discrepancy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    discrepancy_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    stock_at_prediction INTEGER DEFAULT 0,
    pending_at_prediction INTEGER DEFAULT 0,
    stock_at_order INTEGER DEFAULT 0,
    pending_at_order INTEGER DEFAULT 0,
    stock_diff INTEGER DEFAULT 0,
    pending_diff INTEGER DEFAULT 0,
    stock_source TEXT,
    is_stock_stale INTEGER DEFAULT 0,
    original_order_qty INTEGER DEFAULT 0,
    recalculated_order_qty INTEGER DEFAULT 0,
    order_impact INTEGER DEFAULT 0,
    description TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, item_cd)
);

CREATE INDEX IF NOT EXISTS idx_stock_disc_store_date
    ON stock_discrepancy_log(store_id, order_date);
CREATE INDEX IF NOT EXISTS idx_stock_disc_type
    ON stock_discrepancy_log(discrepancy_type);
CREATE INDEX IF NOT EXISTS idx_stock_disc_severity
    ON stock_discrepancy_log(severity);
"""


class OrderAnalysisRepository:
    """발주 분석 DB 저장소 (data/order_analysis.db)"""

    _initialized = False

    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: 테스트용 DB 경로. None이면 data/order_analysis.db 사용.
        """
        self._db_path = db_path

    def _get_db_path(self) -> str:
        if self._db_path:
            return self._db_path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return str(DATA_DIR / "order_analysis.db")

    def get_connection(self) -> sqlite3.Connection:
        """분석 DB 연결 (자동 스키마 초기화)"""
        db_path = self._get_db_path()
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        if not OrderAnalysisRepository._initialized or self._db_path:
            conn.executescript(_SCHEMA_SQL)
            if not self._db_path:
                OrderAnalysisRepository._initialized = True
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat()

    # ── 스냅샷 ──────────────────────────────────────────

    def save_order_snapshot(
        self, store_id: str, order_date: str, items: List[Dict[str, Any]]
    ) -> int:
        """자동발주 스냅샷 저장

        Args:
            store_id: 매장 코드
            order_date: 발주일 (YYYY-MM-DD)
            items: 스냅샷 레코드 리스트

        Returns:
            저장된 건수
        """
        if not items:
            return 0

        conn = self.get_connection()
        try:
            now = self._now()
            saved = 0
            for item in items:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO order_snapshots
                        (store_id, order_date, item_cd, item_nm, mid_cd,
                         predicted_qty, recommended_qty, final_order_qty,
                         current_stock, pending_qty, eval_decision,
                         order_unit_qty, order_success, confidence,
                         delivery_type, data_days,
                         created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            store_id,
                            order_date,
                            item.get("item_cd"),
                            item.get("item_nm"),
                            item.get("mid_cd"),
                            item.get("predicted_qty", 0),
                            item.get("recommended_qty", 0),
                            item.get("final_order_qty", 0),
                            item.get("current_stock", 0),
                            item.get("pending_qty", 0),
                            item.get("eval_decision"),
                            item.get("order_unit_qty", 1),
                            item.get("order_success", 0),
                            str(item.get("confidence", "")),
                            item.get("delivery_type"),
                            item.get("data_days"),
                            now,
                        ),
                    )
                    saved += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
            return saved
        finally:
            conn.close()

    def get_snapshot_by_date(
        self, store_id: str, order_date: str
    ) -> List[Dict[str, Any]]:
        """특정 발주일 스냅샷 조회"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM order_snapshots
                WHERE store_id = ? AND order_date = ?""",
                (store_id, order_date),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ── Diff ─────────────────────────────────────────────

    def save_diffs(self, diffs: List[Dict[str, Any]]) -> int:
        """차이 상세 저장"""
        if not diffs:
            return 0

        conn = self.get_connection()
        try:
            now = self._now()
            saved = 0
            for d in diffs:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO order_diffs
                        (store_id, order_date, receiving_date, item_cd, item_nm,
                         mid_cd, diff_type, auto_order_qty, predicted_qty,
                         eval_decision, confirmed_order_qty, receiving_qty,
                         qty_diff, receiving_diff, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            d.get("store_id"),
                            d.get("order_date"),
                            d.get("receiving_date"),
                            d.get("item_cd"),
                            d.get("item_nm"),
                            d.get("mid_cd"),
                            d.get("diff_type"),
                            d.get("auto_order_qty", 0),
                            d.get("predicted_qty", 0),
                            d.get("eval_decision"),
                            d.get("confirmed_order_qty", 0),
                            d.get("receiving_qty", 0),
                            d.get("qty_diff", 0),
                            d.get("receiving_diff", 0),
                            now,
                        ),
                    )
                    saved += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
            return saved
        finally:
            conn.close()

    def save_summary(self, summary: Dict[str, Any]) -> int:
        """일별 요약 저장"""
        if not summary:
            return 0

        conn = self.get_connection()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO order_diff_summary
                (store_id, order_date, receiving_date,
                 total_auto_items, total_confirmed_items,
                 items_unchanged, items_qty_changed,
                 items_added, items_removed, items_not_comparable,
                 total_auto_qty, total_confirmed_qty, total_receiving_qty,
                 match_rate, has_receiving_data, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    summary.get("store_id"),
                    summary.get("order_date"),
                    summary.get("receiving_date"),
                    summary.get("total_auto_items", 0),
                    summary.get("total_confirmed_items", 0),
                    summary.get("items_unchanged", 0),
                    summary.get("items_qty_changed", 0),
                    summary.get("items_added", 0),
                    summary.get("items_removed", 0),
                    summary.get("items_not_comparable", 0),
                    summary.get("total_auto_qty", 0),
                    summary.get("total_confirmed_qty", 0),
                    summary.get("total_receiving_qty", 0),
                    summary.get("match_rate", 0.0),
                    summary.get("has_receiving_data", 1),
                    self._now(),
                ),
            )
            conn.commit()
            return 1
        finally:
            conn.close()

    # ── 조회 ─────────────────────────────────────────────

    def get_diffs_by_date(
        self, store_id: str, order_date: str
    ) -> List[Dict[str, Any]]:
        """특정 발주일의 diff 조회"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM order_diffs
                WHERE store_id = ? AND order_date = ?
                ORDER BY diff_type, item_cd""",
                (store_id, order_date),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_summary_by_date(
        self, store_id: str, order_date: str
    ) -> Optional[Dict[str, Any]]:
        """특정 발주일의 요약 조회"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM order_diff_summary
                WHERE store_id = ? AND order_date = ?""",
                (store_id, order_date),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_diffs_by_period(
        self, store_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """기간별 diff 조회"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM order_diffs
                WHERE store_id = ? AND order_date BETWEEN ? AND ?
                ORDER BY order_date, diff_type, item_cd""",
                (store_id, start_date, end_date),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_summaries_by_period(
        self, store_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """기간별 요약 조회"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM order_diff_summary
                WHERE store_id = ? AND order_date BETWEEN ? AND ?
                ORDER BY order_date""",
                (store_id, start_date, end_date),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ── 분석 쿼리 ────────────────────────────────────────

    def get_most_modified_items(
        self, store_id: str, days: int = 30, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """가장 많이 수정된 상품 Top N"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT item_cd, item_nm, mid_cd,
                    COUNT(*) as modify_count,
                    SUM(CASE WHEN diff_type = 'qty_changed' THEN 1 ELSE 0 END) as qty_changes,
                    SUM(CASE WHEN diff_type = 'added' THEN 1 ELSE 0 END) as additions,
                    SUM(CASE WHEN diff_type = 'removed' THEN 1 ELSE 0 END) as removals,
                    AVG(qty_diff) as avg_qty_diff
                FROM order_diffs
                WHERE store_id = ?
                    AND order_date >= date('now', ?)
                    AND diff_type IN ('qty_changed', 'added', 'removed')
                GROUP BY item_cd
                ORDER BY modify_count DESC
                LIMIT ?""",
                (store_id, f"-{days} days", limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_modification_trend(
        self, store_id: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """일별 수정 추이"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT order_date,
                    SUM(items_unchanged) as unchanged,
                    SUM(items_qty_changed) as qty_changed,
                    SUM(items_added) as added,
                    SUM(items_removed) as removed,
                    AVG(match_rate) as avg_match_rate
                FROM order_diff_summary
                WHERE store_id = ?
                    AND order_date >= date('now', ?)
                GROUP BY order_date
                ORDER BY order_date""",
                (store_id, f"-{days} days"),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_category_modification_stats(
        self, store_id: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """중분류별 수정 통계"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT mid_cd,
                    COUNT(*) as total_diffs,
                    SUM(CASE WHEN diff_type = 'qty_changed' THEN 1 ELSE 0 END) as qty_changes,
                    SUM(CASE WHEN diff_type = 'added' THEN 1 ELSE 0 END) as additions,
                    SUM(CASE WHEN diff_type = 'removed' THEN 1 ELSE 0 END) as removals,
                    AVG(qty_diff) as avg_qty_diff
                FROM order_diffs
                WHERE store_id = ?
                    AND order_date >= date('now', ?)
                    AND diff_type IN ('qty_changed', 'added', 'removed')
                GROUP BY mid_cd
                ORDER BY total_diffs DESC""",
                (store_id, f"-{days} days"),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ── 유효 데이터 필터링 ─────────────────────────────

    def get_valid_summaries_by_period(
        self, store_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """입고 데이터가 있는 날짜만 요약 조회 (has_receiving_data=1)"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM order_diff_summary
                WHERE store_id = ? AND order_date BETWEEN ? AND ?
                    AND has_receiving_data = 1
                ORDER BY order_date""",
                (store_id, start_date, end_date),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_filtered_match_rate(
        self, store_id: str, days: int = 30
    ) -> Dict[str, Any]:
        """유효 날짜만으로 평균 일치율 계산

        Returns:
            {
                'avg_match_rate': float,
                'valid_dates': int,
                'invalid_dates': int,
                'total_unchanged': int,
                'total_items': int,
            }
        """
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT
                    COUNT(*) as total_dates,
                    SUM(CASE WHEN has_receiving_data = 1 THEN 1 ELSE 0 END) as valid_dates,
                    SUM(CASE WHEN has_receiving_data = 0 THEN 1 ELSE 0 END) as invalid_dates,
                    SUM(CASE WHEN has_receiving_data = 1 THEN items_unchanged ELSE 0 END) as total_unchanged,
                    SUM(CASE WHEN has_receiving_data = 1 THEN total_auto_items + items_added ELSE 0 END) as total_items,
                    AVG(CASE WHEN has_receiving_data = 1 THEN match_rate END) as avg_match_rate
                FROM order_diff_summary
                WHERE store_id = ? AND order_date >= date('now', ?)""",
                (store_id, f"-{days} days"),
            )
            row = cursor.fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    # ── Diff 피드백 통계 (Phase 3) ──────────────────────

    def get_item_removal_stats(
        self, store_id: str, days: int = 14
    ) -> List[Dict[str, Any]]:
        """상품별 제거 통계 (피드백 페널티용)

        Returns:
            [{item_cd, item_nm, mid_cd, removal_count, total_appearances}, ...]
        """
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT item_cd, item_nm, mid_cd,
                    SUM(CASE WHEN diff_type = 'removed' THEN 1 ELSE 0 END) as removal_count,
                    COUNT(*) as total_appearances
                FROM order_diffs
                WHERE store_id = ?
                    AND order_date >= date('now', ?)
                    AND diff_type IN ('removed', 'unchanged', 'qty_changed')
                GROUP BY item_cd
                HAVING removal_count > 0
                ORDER BY removal_count DESC""",
                (store_id, f"-{days} days"),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_item_addition_stats(
        self, store_id: str, days: int = 14
    ) -> List[Dict[str, Any]]:
        """상품별 추가 통계 (피드백 부스트용)

        Returns:
            [{item_cd, item_nm, mid_cd, addition_count, avg_qty}, ...]
        """
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT item_cd, item_nm, mid_cd,
                    COUNT(*) as addition_count,
                    AVG(confirmed_order_qty) as avg_qty
                FROM order_diffs
                WHERE store_id = ?
                    AND diff_type = 'added'
                    AND order_date >= date('now', ?)
                GROUP BY item_cd
                HAVING addition_count > 0
                ORDER BY addition_count DESC""",
                (store_id, f"-{days} days"),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ── 재고 불일치 로그 ────────────────────────────────

    def save_stock_discrepancies(
        self,
        store_id: str,
        order_date: str,
        discrepancies: List[Dict[str, Any]],
    ) -> int:
        """재고 불일치 진단 결과 저장

        Args:
            store_id: 매장 코드
            order_date: 발주일 (YYYY-MM-DD)
            discrepancies: StockDiscrepancyDiagnoser.diagnose() 결과 + item 메타

        Returns:
            저장된 건수
        """
        if not discrepancies:
            return 0

        conn = self.get_connection()
        try:
            now = self._now()
            saved = 0
            for d in discrepancies:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO stock_discrepancy_log
                        (store_id, order_date, item_cd, item_nm, mid_cd,
                         discrepancy_type, severity,
                         stock_at_prediction, pending_at_prediction,
                         stock_at_order, pending_at_order,
                         stock_diff, pending_diff,
                         stock_source, is_stock_stale,
                         original_order_qty, recalculated_order_qty,
                         order_impact, description, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            store_id,
                            order_date,
                            d.get("item_cd"),
                            d.get("item_nm"),
                            d.get("mid_cd"),
                            d.get("discrepancy_type"),
                            d.get("severity"),
                            d.get("stock_at_prediction", 0),
                            d.get("pending_at_prediction", 0),
                            d.get("stock_at_order", 0),
                            d.get("pending_at_order", 0),
                            d.get("stock_diff", 0),
                            d.get("pending_diff", 0),
                            d.get("stock_source", ""),
                            1 if d.get("is_stock_stale") else 0,
                            d.get("original_order_qty", 0),
                            d.get("recalculated_order_qty", 0),
                            d.get("order_impact", 0),
                            d.get("description", ""),
                            now,
                        ),
                    )
                    saved += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
            return saved
        finally:
            conn.close()

    def get_discrepancies_by_date(
        self, store_id: str, order_date: str
    ) -> List[Dict[str, Any]]:
        """특정 발주일의 재고 불일치 조회"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """SELECT * FROM stock_discrepancy_log
                WHERE store_id = ? AND order_date = ?
                ORDER BY severity DESC, discrepancy_type""",
                (store_id, order_date),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_discrepancy_summary(
        self, store_id: str, days: int = 7
    ) -> Dict[str, Any]:
        """재고 불일치 요약 통계

        Args:
            store_id: 매장 코드
            days: 조회 기간

        Returns:
            {
                'total': int,
                'by_type': {type: count, ...},
                'by_severity': {sev: count, ...},
                'avg_stock_diff': float,
                'avg_order_impact': float,
                'top_items': [{item_cd, count}, ...],
            }
        """
        conn = self.get_connection()
        try:
            # 유형별 집계
            cursor = conn.execute(
                """SELECT discrepancy_type, severity,
                    COUNT(*) as cnt,
                    AVG(ABS(stock_diff)) as avg_stock_diff,
                    AVG(ABS(order_impact)) as avg_order_impact
                FROM stock_discrepancy_log
                WHERE store_id = ? AND order_date >= date('now', ?)
                    AND discrepancy_type != 'NONE'
                GROUP BY discrepancy_type, severity
                ORDER BY cnt DESC""",
                (store_id, f"-{days} days"),
            )
            rows = [dict(row) for row in cursor.fetchall()]

            by_type: Dict[str, int] = {}
            by_severity: Dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
            total = 0
            total_stock_diff = 0.0
            total_order_impact = 0.0

            for r in rows:
                cnt = r["cnt"]
                total += cnt
                by_type[r["discrepancy_type"]] = by_type.get(r["discrepancy_type"], 0) + cnt
                by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + cnt
                total_stock_diff += (r["avg_stock_diff"] or 0) * cnt
                total_order_impact += (r["avg_order_impact"] or 0) * cnt

            # 상위 상품
            cursor2 = conn.execute(
                """SELECT item_cd, COUNT(*) as cnt
                FROM stock_discrepancy_log
                WHERE store_id = ? AND order_date >= date('now', ?)
                    AND discrepancy_type != 'NONE'
                GROUP BY item_cd
                ORDER BY cnt DESC
                LIMIT 10""",
                (store_id, f"-{days} days"),
            )
            top_items = [dict(row) for row in cursor2.fetchall()]

            return {
                "total": total,
                "by_type": by_type,
                "by_severity": by_severity,
                "avg_stock_diff": round(total_stock_diff / total, 2) if total else 0.0,
                "avg_order_impact": round(total_order_impact / total, 2) if total else 0.0,
                "top_items": top_items,
            }
        finally:
            conn.close()
