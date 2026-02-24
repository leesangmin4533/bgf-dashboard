"""
DashboardService -- 웹 대시보드 데이터 서비스

api_home.py의 raw SQL을 Repository/Service 호출로 대체합니다.
매장별 격리된 데이터를 제공합니다.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 중분류 코드 -> 카테고리명
_MID_CD_NAMES = {
    "001": "도시락", "002": "주먹밥", "003": "김밥",
    "004": "샌드위치", "005": "햄버거", "012": "빵",
    "013": "유제품", "047": "우유/두유",
    "006": "조리면", "010": "음료", "026": "반찬",
    "046": "신선식품", "021": "냉동식품", "034": "아이스크림",
    "049": "맥주", "050": "소주",
}


class DashboardService:
    """웹 대시보드 데이터 서비스

    Usage:
        service = DashboardService(store_id="46513")
        status = service.get_last_order()
        expiry = service.get_expiry_risk()
    """

    def __init__(self, store_id: Optional[str] = None, db_path: Optional[str] = None):
        self.store_id = store_id
        self._db_path = db_path  # Flask 테스트 호환용

    def _sf(self, alias: str = "") -> str:
        """store_id 필터 SQL 조각 (AND ...)"""
        if not self.store_id:
            return ""
        col = f"{alias}.store_id" if alias else "store_id"
        return f"AND {col} = ?"

    def _sw(self, alias: str = "") -> str:
        """store_id WHERE SQL 조각 (WHERE ...)"""
        if not self.store_id:
            return ""
        col = f"{alias}.store_id" if alias else "store_id"
        return f"WHERE {col} = ?"

    def _sp(self) -> tuple:
        """store_id 파라미터 튜플"""
        return (self.store_id,) if self.store_id else ()

    def _get_conn(self) -> sqlite3.Connection:
        """매장 DB 연결 (+ common.db ATTACH)

        db_path가 주어졌으면 그것을 사용 (Flask app.config["DB_PATH"] 호환).
        아니면 DBRouter를 통해 매장 DB에 연결 + common.db ATTACH.
        """
        if self._db_path:
            return sqlite3.connect(self._db_path, timeout=10)
        if self.store_id:
            try:
                from src.infrastructure.database.connection import attach_common_with_views
                conn = DBRouter.get_store_connection(self.store_id)
                return attach_common_with_views(conn, self.store_id)
            except Exception:
                pass
        return DBRouter.get_connection("store")

    def get_last_order(self) -> Dict[str, Any]:
        """최근 성공 수집 + 최근 발주 정보"""
        conn = self._get_conn()
        try:
            row = conn.execute(f"""
                SELECT sales_date, collected_at, total_items
                FROM collection_logs
                WHERE status = 'success' {self._sf()}
                ORDER BY collected_at DESC LIMIT 1
            """, self._sp()).fetchone()

            today = datetime.now().strftime("%Y-%m-%d")
            order_row = conn.execute(f"""
                SELECT COUNT(*),
                       SUM(CASE WHEN status = 'ordered' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status != 'ordered' THEN 1 ELSE 0 END)
                FROM order_tracking WHERE order_date = ? {self._sf()}
            """, (today,) + self._sp()).fetchone()
        finally:
            conn.close()

        return {
            "date": row[0] if row else None,
            "time": (
                row[1].split("T")[1][:8]
                if row and row[1] and "T" in str(row[1])
                else None
            ),
            "total_items": row[2] if row else 0,
            "success_count": order_row[1] or 0 if order_row else 0,
            "fail_count": order_row[2] or 0 if order_row else 0,
        }

    def get_today_summary(self, cached_predictions=None) -> Dict[str, Any]:
        """오늘 발주 요약

        Returns:
            order_items: 오늘 입고 예정 SKU 수 (order_date = 오늘)
            total_qty: 오늘 입고 예정 총 수량
            ordered_items: 오늘 발주한 SKU 수 (created_at = 오늘, order_date = 내일)
            ordered_qty: 오늘 발주한 총 수량
        """
        if cached_predictions:
            order_items = [p for p in cached_predictions if p.order_qty > 0]
            categories = set(p.mid_cd for p in order_items)
            skip_items = [p for p in cached_predictions if p.order_qty <= 0]
            return {
                "order_items": len(order_items),
                "total_qty": sum(p.order_qty for p in order_items),
                "skip_items": len(skip_items),
                "categories": len(categories),
                "ordered_items": len(order_items),
                "ordered_qty": sum(p.order_qty for p in order_items),
            }

        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            # 오늘 입고 예정 (order_date = 오늘)
            row = conn.execute(f"""
                SELECT COUNT(*), COALESCE(SUM(order_qty), 0),
                       COUNT(DISTINCT mid_cd)
                FROM order_tracking WHERE order_date = ? {self._sf()}
            """, (today,) + self._sp()).fetchone()

            # 오늘 실제 발주한 건 (created_at이 오늘, order_date > 오늘)
            ordered = conn.execute(f"""
                SELECT COUNT(*), COALESCE(SUM(order_qty), 0)
                FROM order_tracking
                WHERE substr(created_at, 1, 10) = ?
                  AND order_date > ?
                  {self._sf()}
            """, (today, today) + self._sp()).fetchone()
        finally:
            conn.close()

        return {
            "order_items": row[0] or 0,
            "total_qty": row[1] or 0,
            "skip_items": 0,
            "categories": row[2] or 0,
            "ordered_items": ordered[0] or 0,
            "ordered_qty": ordered[1] or 0,
        }

    def get_expiry_risk(self) -> Dict[str, Any]:
        """폐기 위험 상품 (배치 + order_tracking + daily_sales)"""
        # 간이 상태 전이
        try:
            from src.infrastructure.database.repos import OrderTrackingRepository
            tracking_repo = OrderTrackingRepository(store_id=self.store_id)
            tracking_repo.auto_update_statuses(store_id=self.store_id)
        except Exception:
            pass

        conn = self._get_conn()
        try:
            batch_rows = conn.execute(f"""
                SELECT item_nm, expiry_date, remaining_qty,
                       COALESCE(item_cd, '') AS item_cd,
                       COALESCE(mid_cd, '') AS mid_cd,
                       'batch' AS source
                FROM inventory_batches
                WHERE status = 'active'
                  AND expiry_date >= date('now')
                  AND expiry_date <= datetime('now', '+7 days')
                  AND remaining_qty > 0
                  {self._sf()}
                ORDER BY expiry_date ASC
                LIMIT 200
            """, self._sp()).fetchall()

            food_rows = conn.execute(f"""
                SELECT item_nm, expiry_time, remaining_qty,
                       COALESCE(item_cd, '') AS item_cd,
                       COALESCE(mid_cd, '') AS mid_cd,
                       'food' AS source
                FROM order_tracking
                WHERE status IN ('ordered', 'arrived', 'selling')
                  AND datetime(expiry_time) >= datetime('now', 'start of day')
                  AND datetime(expiry_time) <= datetime('now', '+7 days')
                  AND remaining_qty > 0
                  {self._sf()}
                ORDER BY expiry_time ASC
                LIMIT 200
            """, self._sp()).fetchall()

            sp2 = self._sp() * 2 if self.store_id else self._sp()
            daily_food_rows = conn.execute(f"""
                SELECT ds.item_cd,
                       COALESCE(p.item_nm, ds.item_cd) AS item_nm,
                       ds.mid_cd, ds.stock_qty, ds.sales_date
                FROM daily_sales ds
                LEFT JOIN products p ON ds.item_cd = p.item_cd
                WHERE ds.mid_cd IN ('001','002','003','004','005','012')
                  AND ds.stock_qty > 0
                  AND ds.sales_date = (
                      SELECT MAX(d2.sales_date) FROM daily_sales d2
                      WHERE d2.item_cd = ds.item_cd
                      {'AND d2.store_id = ?' if self.store_id else ''}
                  )
                  {self._sf('ds')}
                ORDER BY ds.mid_cd, ds.stock_qty DESC
                LIMIT 100
            """, sp2).fetchall()
        finally:
            conn.close()

        items = []
        seen = set()
        food_item_cds = set()

        for r in food_rows:
            key = (r[3], r[1])
            if key in seen:
                continue
            seen.add(key)
            food_item_cds.add(r[3])
            items.append({
                "item_nm": r[0], "expiry_date": r[1],
                "remaining_qty": r[2], "item_cd": r[3],
                "mid_cd": r[4],
                "cat_name": _MID_CD_NAMES.get(r[4], r[4]),
                "source": "food",
            })

        default_shelf_days = {
            "001": 1, "002": 1, "003": 1,
            "004": 2, "005": 1, "012": 3,
        }
        now = datetime.now()

        product_exp_days = {}
        try:
            conn2 = DBRouter.get_common_connection()
            for r in daily_food_rows:
                item_cd = r[0]
                if item_cd not in food_item_cds and item_cd not in product_exp_days:
                    row = conn2.execute(
                        "SELECT expiration_days FROM product_details "
                        "WHERE item_cd = ?",
                        (item_cd,)
                    ).fetchone()
                    if row and row[0] and row[0] > 0:
                        product_exp_days[item_cd] = row[0]
            conn2.close()
        except Exception:
            pass

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        max_future = now + timedelta(days=7)

        for r in daily_food_rows:
            item_cd = r[0]
            if item_cd in food_item_cds:
                continue
            food_item_cds.add(item_cd)
            mid = r[2]
            sales_date = r[4]
            days = product_exp_days.get(
                item_cd, default_shelf_days.get(mid, 1)
            )
            try:
                sd = datetime.strptime(sales_date, "%Y-%m-%d")
                est_expiry_dt = sd + timedelta(days=days)
                est_expiry = est_expiry_dt.strftime("%Y-%m-%d 00:00")
            except Exception:
                est_expiry = sales_date
                est_expiry_dt = None

            if est_expiry_dt and est_expiry_dt > max_future:
                continue
            if est_expiry_dt and est_expiry_dt < today_start:
                continue

            items.append({
                "item_nm": r[1], "expiry_date": est_expiry,
                "remaining_qty": r[3], "item_cd": item_cd,
                "mid_cd": mid,
                "cat_name": _MID_CD_NAMES.get(mid, mid),
                "source": "food",
            })

        for r in batch_rows:
            key = (r[3], r[1])
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "item_nm": r[0], "expiry_date": r[1],
                "remaining_qty": r[2], "item_cd": r[3],
                "mid_cd": r[4],
                "cat_name": _MID_CD_NAMES.get(r[4], r[4]),
                "source": "batch",
            })

        items.sort(key=lambda x: x["expiry_date"] or "9999")
        return {"count": len(items), "items": items}

    def get_pipeline_status(self, script_task=None) -> Dict[str, Any]:
        """파이프라인 단계별 마지막 시각"""
        conn = self._get_conn()
        try:
            coll = conn.execute(f"""
                SELECT collected_at FROM collection_logs
                WHERE status = 'success' {self._sf()}
                ORDER BY collected_at DESC LIMIT 1
            """, self._sp()).fetchone()

            pred = conn.execute(f"""
                SELECT prediction_date FROM prediction_logs
                {self._sw()}
                ORDER BY id DESC LIMIT 1
            """, self._sp()).fetchone()

            ordr = conn.execute(f"""
                SELECT created_at FROM order_tracking
                {self._sw()}
                ORDER BY id DESC LIMIT 1
            """, self._sp()).fetchone()

            eval_row = conn.execute(f"""
                SELECT created_at FROM eval_outcomes
                {self._sw()}
                ORDER BY id DESC LIMIT 1
            """, self._sp()).fetchone()
        finally:
            conn.close()

        stage = "idle"
        if script_task and script_task.get("process"):
            proc = script_task["process"]
            if proc.poll() is None:
                stage = "running"

        return {
            "stage": stage,
            "last_collection": coll[0] if coll else None,
            "last_prediction": pred[0] if pred else None,
            "last_evaluation": eval_row[0] if eval_row else None,
            "last_order": ordr[0] if ordr else None,
        }

    def get_recent_events(self) -> List[Dict[str, Any]]:
        """최근 이벤트 10건"""
        conn = self._get_conn()
        try:
            events = []

            for row in conn.execute(f"""
                SELECT collected_at, total_items, status
                FROM collection_logs
                {self._sw()}
                ORDER BY id DESC LIMIT 5
            """, self._sp()).fetchall():
                events.append({
                    "time": row[0],
                    "type": "collect",
                    "message": (
                        f"데이터 수집 "
                        f"{'완료' if row[2] == 'success' else '실패'} "
                        f"({row[1]}건)"
                    ),
                    "status": row[2],
                })

            for row in conn.execute(f"""
                SELECT order_date, COUNT(*),
                       SUM(CASE WHEN status='ordered' THEN 1 ELSE 0 END),
                       MAX(created_at)
                FROM order_tracking
                {self._sw()}
                GROUP BY order_date ORDER BY order_date DESC LIMIT 5
            """, self._sp()).fetchall():
                total = row[1]
                success = row[2] or 0
                events.append({
                    "time": row[3] or row[0],
                    "type": "order",
                    "message": f"발주 완료 ({success}/{total} 성공)",
                    "status": "success" if success == total else "warning",
                })
        finally:
            conn.close()

        events.sort(key=lambda e: e["time"] or "", reverse=True)
        return events[:10]

    def get_fail_reasons(self) -> Dict[str, Any]:
        """오늘 발주 실패 사유 요약"""
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            fail_row = conn.execute(f"""
                SELECT COUNT(*) FROM eval_outcomes
                WHERE eval_date = ? AND order_status = 'fail'
                  {self._sf()}
            """, (today,) + self._sp()).fetchone()
            fail_count = fail_row[0] if fail_row else 0

            rows = conn.execute(f"""
                SELECT item_cd, item_nm, mid_cd, stop_reason,
                       orderable_status, checked_at
                FROM order_fail_reasons
                WHERE eval_date = ? {self._sf()}
                ORDER BY id
            """, (today,) + self._sp()).fetchall()
        except Exception as e:
            logger.warning(f"발주 실패 사유 조회 오류: {e}")
            return {"fail_count": 0, "checked_count": 0, "items": []}
        finally:
            conn.close()

        items = [
            {
                "item_cd": r[0], "item_nm": r[1], "mid_cd": r[2],
                "stop_reason": r[3], "orderable_status": r[4],
                "checked_at": r[5],
            }
            for r in rows
        ]

        return {
            "fail_count": fail_count,
            "checked_count": len(items),
            "items": items,
        }

    def get_order_trend_7d(self) -> List[int]:
        """최근 7일 발주 수량 트렌드 (스파크라인용)"""
        conn = self._get_conn()
        try:
            rows = conn.execute(f"""
                SELECT order_date, COALESCE(SUM(order_qty), 0)
                FROM order_tracking
                WHERE order_date >= date('now', '-7 days')
                  {self._sf()}
                GROUP BY order_date
                ORDER BY order_date ASC
            """, self._sp()).fetchall()
        finally:
            conn.close()

        if not rows:
            return []
        return [r[1] for r in rows]

    def get_store_comparison(self) -> List[Dict[str, Any]]:
        """매장 간 비교 요약 (전체 매장 대상)

        Returns:
            [{"store_id", "store_name", "today_orders", "today_qty",
              "categories", "last_collection", "last_order"}, ...]
        """
        from src.settings.store_context import StoreContext

        stores = StoreContext.get_all_active()
        results = []

        for ctx in stores:
            svc = DashboardService(store_id=ctx.store_id)
            try:
                summary = svc.get_today_summary()
                pipeline = svc.get_pipeline_status()
                results.append({
                    "store_id": ctx.store_id,
                    "store_name": ctx.store_name,
                    "today_orders": summary.get("order_items", 0),
                    "today_qty": summary.get("total_qty", 0),
                    "categories": summary.get("categories", 0),
                    "last_collection": pipeline.get("last_collection"),
                    "last_order": pipeline.get("last_order"),
                })
            except Exception as e:
                logger.warning(f"매장 비교 실패: {ctx.store_id}: {e}")
                results.append({
                    "store_id": ctx.store_id,
                    "store_name": ctx.store_name,
                    "today_orders": 0,
                    "today_qty": 0,
                    "categories": 0,
                    "last_collection": None,
                    "last_order": None,
                    "error": str(e),
                })

        return results
