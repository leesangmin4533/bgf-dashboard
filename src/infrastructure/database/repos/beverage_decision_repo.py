"""
BeverageDecisionRepository -- 음료 발주 유지/정지 판단 결과 저장소 (store DB)

dessert_decisions 테이블의 category_type='beverage' 레코드를 관리합니다.
"""

from datetime import datetime
from typing import Dict, List, Optional, Set, Any

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BeverageDecisionRepository(BaseRepository):
    """음료 발주 유지/정지 판단 결과 저장소"""

    db_type = "store"

    def save_decisions_batch(self, results: List[Dict[str, Any]]) -> int:
        """판단 결과 배치 UPSERT"""
        if not results:
            return 0

        conn = self._get_conn()
        saved = 0
        now = datetime.now().isoformat()

        for r in results:
            try:
                conn.execute("""
                    INSERT INTO dessert_decisions (
                        store_id, item_cd, item_nm, mid_cd,
                        dessert_category, expiration_days, small_nm,
                        lifecycle_phase, first_receiving_date, first_receiving_source,
                        weeks_since_intro,
                        judgment_period_start, judgment_period_end,
                        total_order_qty, total_sale_qty, total_disuse_qty,
                        sale_amount, disuse_amount, sell_price, sale_rate,
                        category_avg_sale_qty,
                        prev_period_sale_qty, sale_trend_pct,
                        consecutive_low_weeks, consecutive_zero_months,
                        decision, decision_reason, is_rapid_decline_warning,
                        judgment_cycle, category_type, created_at
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?
                    )
                    ON CONFLICT(store_id, item_cd, judgment_period_end)
                    DO UPDATE SET
                        item_nm = excluded.item_nm,
                        dessert_category = excluded.dessert_category,
                        lifecycle_phase = excluded.lifecycle_phase,
                        total_order_qty = excluded.total_order_qty,
                        total_sale_qty = excluded.total_sale_qty,
                        total_disuse_qty = excluded.total_disuse_qty,
                        sale_amount = excluded.sale_amount,
                        disuse_amount = excluded.disuse_amount,
                        sale_rate = excluded.sale_rate,
                        category_avg_sale_qty = excluded.category_avg_sale_qty,
                        consecutive_low_weeks = excluded.consecutive_low_weeks,
                        consecutive_zero_months = excluded.consecutive_zero_months,
                        decision = excluded.decision,
                        decision_reason = excluded.decision_reason,
                        is_rapid_decline_warning = excluded.is_rapid_decline_warning,
                        category_type = excluded.category_type,
                        created_at = excluded.created_at
                """, (
                    r.get("store_id", self.store_id),
                    r["item_cd"],
                    r.get("item_nm", ""),
                    r.get("mid_cd", ""),
                    r["dessert_category"],
                    r.get("expiration_days"),
                    r.get("small_nm"),
                    r["lifecycle_phase"],
                    r.get("first_receiving_date"),
                    r.get("first_receiving_source"),
                    r.get("weeks_since_intro", 0),
                    r["judgment_period_start"],
                    r["judgment_period_end"],
                    r.get("total_order_qty", 0),
                    r.get("total_sale_qty", 0),
                    r.get("total_disuse_qty", 0),
                    r.get("sale_amount", 0),
                    r.get("disuse_amount", 0),
                    r.get("sell_price", 0),
                    r.get("sale_rate", 0.0),
                    r.get("category_avg_sale_qty", 0.0),
                    r.get("prev_period_sale_qty", 0),
                    r.get("sale_trend_pct", 0.0),
                    r.get("consecutive_low_weeks", 0),
                    r.get("consecutive_zero_months", 0),
                    r["decision"],
                    r.get("decision_reason", ""),
                    1 if r.get("is_rapid_decline_warning") else 0,
                    r["judgment_cycle"],
                    "beverage",
                    now,
                ))
                saved += 1
            except Exception as e:
                logger.warning(f"beverage_decisions 저장 실패 ({r.get('item_cd')}): {e}")

        conn.commit()
        return saved

    def get_latest_decisions(
        self,
        store_id: Optional[str] = None,
        beverage_category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """각 상품별 최신 판단 결과 조회 (category_type='beverage')"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        query = """
            SELECT d.*
            FROM dessert_decisions d
            INNER JOIN (
                SELECT item_cd, MAX(judgment_period_end) as max_date
                FROM dessert_decisions
                WHERE store_id = ? AND category_type = 'beverage'
                GROUP BY item_cd
            ) latest ON d.item_cd = latest.item_cd
                    AND d.judgment_period_end = latest.max_date
            WHERE d.store_id = ? AND d.category_type = 'beverage'
        """
        params = [sid, sid]

        if beverage_category:
            query += " AND d.dessert_category = ?"
            params.append(beverage_category)

        query += " ORDER BY d.dessert_category, d.item_nm"

        cursor = conn.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_confirmed_stop_items(self, store_id: Optional[str] = None) -> Set[str]:
        """운영자가 CONFIRMED_STOP으로 확인한 음료 상품 코드 반환 (OrderFilter용)"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        cursor = conn.execute("""
            SELECT DISTINCT d.item_cd
            FROM dessert_decisions d
            INNER JOIN (
                SELECT item_cd, MAX(judgment_period_end) as max_date
                FROM dessert_decisions
                WHERE store_id = ? AND category_type = 'beverage'
                GROUP BY item_cd
            ) latest ON d.item_cd = latest.item_cd
                    AND d.judgment_period_end = latest.max_date
            WHERE d.store_id = ?
              AND d.category_type = 'beverage'
              AND d.operator_action = 'CONFIRMED_STOP'
        """, (sid, sid))

        return {row[0] for row in cursor.fetchall()}

    def get_stop_recommended_items(self, store_id: Optional[str] = None) -> Set[str]:
        """STOP_RECOMMEND 상태인 음료 상품 코드 반환 (발주 즉시 차단용)"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        cursor = conn.execute("""
            SELECT DISTINCT d.item_cd
            FROM dessert_decisions d
            INNER JOIN (
                SELECT item_cd, MAX(judgment_period_end) as max_date
                FROM dessert_decisions
                WHERE store_id = ? AND category_type = 'beverage'
                GROUP BY item_cd
            ) latest ON d.item_cd = latest.item_cd
                    AND d.judgment_period_end = latest.max_date
            WHERE d.store_id = ?
              AND d.category_type = 'beverage'
              AND d.decision = 'STOP_RECOMMEND'
        """, (sid, sid))

        return {row[0] for row in cursor.fetchall()}

    def get_pending_stop_count(
        self,
        store_id: Optional[str] = None,
    ) -> int:
        """미확인 STOP_RECOMMEND 건수 (배너/뱃지용)"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        cursor = conn.execute("""
            SELECT COUNT(*)
            FROM dessert_decisions d
            INNER JOIN (
                SELECT item_cd, MAX(judgment_period_end) as max_date
                FROM dessert_decisions
                WHERE store_id = ? AND category_type = 'beverage'
                GROUP BY item_cd
            ) latest ON d.item_cd = latest.item_cd
                   AND d.judgment_period_end = latest.max_date
            WHERE d.store_id = ?
              AND d.category_type = 'beverage'
              AND d.decision = 'STOP_RECOMMEND'
              AND d.operator_action IS NULL
        """, (sid, sid))

        return cursor.fetchone()[0]

    def get_decision_summary(
        self,
        store_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """카테고리별 판단 결과 집계"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        cursor = conn.execute("""
            SELECT d.dessert_category, d.decision, COUNT(*) as cnt
            FROM dessert_decisions d
            INNER JOIN (
                SELECT item_cd, MAX(judgment_period_end) as max_date
                FROM dessert_decisions
                WHERE store_id = ? AND category_type = 'beverage'
                GROUP BY item_cd
            ) latest ON d.item_cd = latest.item_cd
                    AND d.judgment_period_end = latest.max_date
            WHERE d.store_id = ? AND d.category_type = 'beverage'
            GROUP BY d.dessert_category, d.decision
        """, (sid, sid))

        by_category: Dict[str, Dict[str, int]] = {}
        current: Dict[str, int] = {"KEEP": 0, "WATCH": 0, "STOP_RECOMMEND": 0, "SKIP": 0}

        for row in cursor.fetchall():
            cat, decision, cnt = row
            if cat not in by_category:
                by_category[cat] = {"KEEP": 0, "WATCH": 0, "STOP_RECOMMEND": 0}
            by_category[cat][decision] = cnt
            if decision in current:
                current[decision] += cnt

        return {
            "current": current,
            "by_category": by_category,
        }

    def batch_update_operator_action(
        self,
        item_cds: List[str],
        action: str,
        operator_note: Optional[str] = None,
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """여러 상품의 operator_action을 일괄 업데이트"""
        if not item_cds:
            return []

        sid = store_id or self.store_id
        conn = self._get_conn()
        now = datetime.now().isoformat()
        results = []

        placeholders = ",".join("?" * len(item_cds))
        cursor = conn.execute(f"""
            SELECT d.id, d.item_cd
            FROM dessert_decisions d
            INNER JOIN (
                SELECT item_cd, MAX(judgment_period_end) as max_date
                FROM dessert_decisions
                WHERE store_id = ? AND category_type = 'beverage'
                GROUP BY item_cd
            ) latest ON d.item_cd = latest.item_cd
                   AND d.judgment_period_end = latest.max_date
            WHERE d.store_id = ?
              AND d.category_type = 'beverage'
              AND d.item_cd IN ({placeholders})
              AND d.decision = 'STOP_RECOMMEND'
              AND d.operator_action IS NULL
        """, [sid, sid] + list(item_cds))

        targets = cursor.fetchall()

        for row in targets:
            decision_id, item_cd = row
            try:
                conn.execute("""
                    UPDATE dessert_decisions
                    SET operator_action = ?,
                        operator_note = ?,
                        action_taken_at = ?
                    WHERE id = ?
                """, (action, operator_note or "batch", now, decision_id))
                results.append({
                    "item_cd": item_cd,
                    "action": action,
                    "action_taken_at": now,
                })
            except Exception as e:
                logger.warning(f"batch operator_action 실패 ({item_cd}): {e}")

        conn.commit()
        return results

    def get_item_decision_history(
        self,
        item_cd: str,
        store_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """특정 음료 상품의 판단 이력 (최신순)"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        cursor = conn.execute("""
            SELECT * FROM dessert_decisions
            WHERE store_id = ? AND item_cd = ? AND category_type = 'beverage'
            ORDER BY judgment_period_end DESC
            LIMIT ?
        """, (sid, item_cd, limit))

        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def update_operator_action(
        self,
        decision_id: int,
        action: str,
        note: Optional[str] = None,
    ) -> bool:
        """운영자 최종 확인 결과 기록"""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        try:
            cursor = conn.execute("""
                UPDATE dessert_decisions
                SET operator_action = ?,
                    operator_note = ?,
                    action_taken_at = ?
                WHERE id = ? AND category_type = 'beverage'
            """, (action, note, now, decision_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"beverage operator_action 업데이트 실패 (id={decision_id}): {e}")
            return False

    def get_weekly_trend(
        self,
        store_id: Optional[str] = None,
        weeks: int = 8,
    ) -> List[Dict[str, Any]]:
        """주차별 KEEP/WATCH/STOP 건수 추이"""
        sid = store_id or self.store_id
        conn = self._get_conn()

        cursor = conn.execute("""
            SELECT
                CAST(strftime('%W', judgment_period_end) AS INTEGER) as week_num,
                decision,
                COUNT(*) as cnt
            FROM dessert_decisions
            WHERE store_id = ?
              AND category_type = 'beverage'
              AND judgment_period_end >= date('now', ? || ' days')
              AND decision IN ('KEEP', 'WATCH', 'STOP_RECOMMEND')
            GROUP BY week_num, decision
            ORDER BY week_num
        """, (sid, str(-weeks * 7)))

        week_data: Dict[int, Dict[str, int]] = {}
        for row in cursor.fetchall():
            wn, decision, cnt = row
            if wn not in week_data:
                week_data[wn] = {"KEEP": 0, "WATCH": 0, "STOP_RECOMMEND": 0}
            week_data[wn][decision] = cnt

        result = []
        for wn in sorted(week_data.keys()):
            entry = {"week": f"W{wn}"}
            entry.update(week_data[wn])
            result.append(entry)

        return result
