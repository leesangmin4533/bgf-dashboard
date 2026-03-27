"""
OrderBehaviorLogger — AI 발주 vs 실제 발주 비교 로깅

10:10 pending_sync 완료 후 호출.
order_snapshots(AI 제안) vs dsResult(실제 확정) 비교 후
order_behavior_log에 저장.
"""

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderBehaviorLogger:
    """AI 발주 vs 실제 발주 비교 로거"""

    def __init__(self, store_id: str):
        self.store_id = store_id

    def log_behavior(
        self,
        order_date: str,
        actual_orders: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        AI 제안값과 실제 발주 비교 후 order_behavior_log 저장.

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            actual_orders: dsResult 파싱된 리스트
                [{item_cd, mid_cd, actual_qty, ord_input_id, buy_qty}, ...]

        Returns:
            {logged, accept, increase, decrease, remove, add}
        """
        stats = {
            "logged": 0, "accept": 0, "increase": 0,
            "decrease": 0, "remove": 0, "add": 0,
        }

        if not actual_orders:
            return stats

        # 날짜 포맷 통일
        od_hyphen = order_date
        od_compact = order_date.replace("-", "")
        if len(order_date) == 8 and "-" not in order_date:
            od_hyphen = f"{order_date[:4]}-{order_date[4:6]}-{order_date[6:]}"
            od_compact = order_date

        # 1. order_snapshots에서 AI 제안값 조회
        ai_map = self._get_ai_snapshots(od_hyphen, od_compact)

        # 2. actual_orders를 item_cd 맵으로
        actual_map: Dict[str, Dict] = {}
        for r in actual_orders:
            ic = str(r.get("item_cd", ""))
            if ic:
                actual_map[ic] = r

        # 3. 비교 및 로그 생성
        log_items: List[Dict[str, Any]] = []
        all_items = set(ai_map.keys()) | set(actual_map.keys())

        for item_cd in all_items:
            ai = ai_map.get(item_cd, {})
            actual = actual_map.get(item_cd, {})

            ai_final = int(ai.get("final_order_qty", 0) or 0)
            actual_qty = int(actual.get("actual_qty", 0) or 0)
            diff = actual_qty - ai_final

            # diff_ratio (0 나누기 방지)
            if ai_final > 0:
                diff_ratio = round(diff / ai_final, 3)
            elif actual_qty > 0:
                diff_ratio = 1.0  # AI 0 → 실제 N = 100% 추가
            else:
                diff_ratio = 0.0

            # action 판정
            if actual_qty == 0 and ai_final > 0:
                action = "remove"
            elif actual_qty > 0 and ai_final == 0:
                action = "add"
            elif ai_final > 0 and actual_qty > ai_final * 1.1:
                action = "increase"
            elif ai_final > 0 and actual_qty < ai_final * 0.9:
                action = "decrease"
            else:
                action = "accept"

            stats[action] += 1

            log_items.append({
                "item_cd": item_cd,
                "mid_cd": actual.get("mid_cd") or ai.get("mid_cd", ""),
                "ai_predicted_qty": ai.get("predicted_qty"),
                "ai_recommended_qty": ai.get("recommended_qty"),
                "ai_final_qty": ai_final,
                "ai_eval_decision": ai.get("eval_decision"),
                "actual_qty": actual_qty,
                "ord_input_id": actual.get("ord_input_id", ""),
                "already_received_qty": int(actual.get("buy_qty", 0) or 0),
                "diff": diff,
                "diff_ratio": diff_ratio,
                "action": action,
            })

        # 4. DB 저장
        saved = self._save_logs(od_hyphen, log_items)
        stats["logged"] = saved

        logger.info(
            f"[behavior_log] {self.store_id}/{od_hyphen}: "
            f"logged={saved}, accept={stats['accept']}, "
            f"increase={stats['increase']}, decrease={stats['decrease']}, "
            f"remove={stats['remove']}, add={stats['add']}"
        )

        return stats

    def _get_ai_snapshots(
        self, od_hyphen: str, od_compact: str
    ) -> Dict[str, Dict]:
        """order_snapshots에서 AI 제안값 조회 (order_analysis.db)"""
        try:
            from pathlib import Path
            db_path = Path("data/order_analysis.db")
            if not db_path.exists():
                return {}

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT item_cd, mid_cd, predicted_qty, recommended_qty,
                           final_order_qty, eval_decision
                    FROM order_snapshots
                    WHERE store_id = ? AND order_date IN (?, ?)
                    """,
                    (self.store_id, od_hyphen, od_compact),
                ).fetchall()

                result = {}
                for r in rows:
                    result[r["item_cd"]] = dict(r)
                return result
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[behavior_log] AI snapshot 조회 실패: {e}")
            return {}

    def _save_logs(
        self, order_date: str, items: List[Dict[str, Any]]
    ) -> int:
        """order_behavior_log UPSERT"""
        conn = DBRouter.get_connection(
            store_id=self.store_id, table="order_behavior_log"
        )
        try:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            saved = 0

            for item in items:
                try:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO order_behavior_log
                            (store_id, order_date, item_cd, mid_cd,
                             ai_predicted_qty, ai_recommended_qty,
                             ai_final_qty, ai_eval_decision,
                             actual_qty, ord_input_id,
                             already_received_qty,
                             diff, diff_ratio, action, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            self.store_id, order_date, item["item_cd"],
                            item.get("mid_cd"),
                            item.get("ai_predicted_qty"),
                            item.get("ai_recommended_qty"),
                            item.get("ai_final_qty"),
                            item.get("ai_eval_decision"),
                            item.get("actual_qty"),
                            item.get("ord_input_id"),
                            item.get("already_received_qty"),
                            item.get("diff"),
                            item.get("diff_ratio"),
                            item.get("action"),
                            now,
                        ),
                    )
                    saved += 1
                except Exception as e:
                    logger.debug(f"[behavior_log] 저장 실패 {item.get('item_cd')}: {e}")

            conn.commit()
            return saved
        finally:
            conn.close()

    def get_summary(
        self, days: int = 30
    ) -> List[Dict[str, Any]]:
        """카테고리별 행동 패턴 요약"""
        conn = DBRouter.get_connection(
            store_id=self.store_id, table="order_behavior_log"
        )
        try:
            rows = conn.execute(
                """
                SELECT
                    mid_cd,
                    COUNT(*) as total,
                    SUM(CASE WHEN action='accept' THEN 1 ELSE 0 END) as accept_cnt,
                    SUM(CASE WHEN action='increase' THEN 1 ELSE 0 END) as increase_cnt,
                    SUM(CASE WHEN action='decrease' THEN 1 ELSE 0 END) as decrease_cnt,
                    SUM(CASE WHEN action='remove' THEN 1 ELSE 0 END) as remove_cnt,
                    SUM(CASE WHEN action='add' THEN 1 ELSE 0 END) as add_cnt,
                    ROUND(AVG(diff_ratio), 3) as avg_diff_ratio
                FROM order_behavior_log
                WHERE store_id = ?
                  AND order_date >= date('now', ? || ' days')
                GROUP BY mid_cd
                ORDER BY total DESC
                """,
                (self.store_id, f"-{days}"),
            ).fetchall()

            return [
                {
                    "mid_cd": r[0], "total": r[1],
                    "accept": r[2], "increase": r[3],
                    "decrease": r[4], "remove": r[5], "add": r[6],
                    "avg_diff_ratio": r[7],
                }
                for r in rows
            ]
        finally:
            conn.close()
