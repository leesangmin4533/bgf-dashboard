"""
NewProductDailyTrackingRepository — 신제품 일별 판매/재고/발주 추적 저장소

NewProductMonitor가 매일 수집한 데이터를 저장·조회
"""

from typing import List, Dict, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class NewProductDailyTrackingRepository(BaseRepository):
    """신제품 일별 추적 데이터 저장소"""

    db_type = "store"

    def save(
        self,
        item_cd: str,
        tracking_date: str,
        sales_qty: int = 0,
        stock_qty: int = 0,
        order_qty: int = 0,
        store_id: Optional[str] = None,
    ) -> int:
        """일별 추적 UPSERT (item_cd + tracking_date + store_id 기준)

        Args:
            item_cd: 상품코드
            tracking_date: 추적일 (YYYY-MM-DD)
            sales_qty: 판매 수량
            stock_qty: 재고 수량
            order_qty: 발주 수량
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            cursor.execute(
                """
                INSERT INTO new_product_daily_tracking
                (item_cd, tracking_date, sales_qty, stock_qty, order_qty,
                 store_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_cd, tracking_date, store_id) DO UPDATE SET
                    sales_qty = excluded.sales_qty,
                    stock_qty = excluded.stock_qty,
                    order_qty = excluded.order_qty
                """,
                (item_cd, tracking_date, sales_qty, stock_qty, order_qty,
                 store_id or self.store_id, now),
            )

            record_id = cursor.lastrowid
            conn.commit()
            return record_id
        finally:
            conn.close()

    def get_tracking_history(
        self, item_cd: str, store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """상품별 추적 이력 (차트 데이터)

        Args:
            item_cd: 상품코드
            store_id: 매장 코드 (선택)

        Returns:
            추적 이력 목록 (날짜순)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT * FROM new_product_daily_tracking
                WHERE item_cd = ?
            """
            params: list = [item_cd]

            sid = store_id or self.store_id
            if sid:
                query += " AND store_id = ?"
                params.append(sid)

            query += " ORDER BY tracking_date ASC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_sold_days_count(
        self, item_cd: str, store_id: Optional[str] = None
    ) -> int:
        """판매일수 (sales_qty > 0인 날짜 수)

        Args:
            item_cd: 상품코드
            store_id: 매장 코드 (선택)

        Returns:
            판매가 발생한 일수
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT COUNT(*) FROM new_product_daily_tracking
                WHERE item_cd = ? AND sales_qty > 0
            """
            params: list = [item_cd]

            sid = store_id or self.store_id
            if sid:
                query += " AND store_id = ?"
                params.append(sid)

            cursor.execute(query, params)
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def get_total_sold_qty(
        self, item_cd: str, store_id: Optional[str] = None
    ) -> int:
        """총 판매수량

        Args:
            item_cd: 상품코드
            store_id: 매장 코드 (선택)

        Returns:
            총 판매수량
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            query = """
                SELECT COALESCE(SUM(sales_qty), 0) FROM new_product_daily_tracking
                WHERE item_cd = ?
            """
            params: list = [item_cd]

            sid = store_id or self.store_id
            if sid:
                query += " AND store_id = ?"
                params.append(sid)

            cursor.execute(query, params)
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
