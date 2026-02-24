"""
발주 이력 저장소 (OrderRepository)

order_history 테이블 CRUD
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderRepository(BaseRepository):
    """발주 이력 저장소"""

    db_type = "store"

    def save_order(
        self,
        order_date: str,
        item_cd: str,
        mid_cd: Optional[str] = None,
        predicted_qty: int = 0,
        recommended_qty: int = 0,
        actual_order_qty: Optional[int] = None,
        current_stock: int = 0,
        order_unit: Optional[str] = None,
        status: str = "pending",
        store_id: Optional[str] = None
    ) -> int:
        """발주 이력 저장

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            item_cd: 상품 코드
            mid_cd: 중분류 코드
            predicted_qty: 예측 판매량
            recommended_qty: 추천 발주량
            actual_order_qty: 실제 발주량
            current_stock: 발주 시점 재고
            order_unit: 발주 단위
            status: 발주 상태 (pending, ordered, cancelled)
            store_id: 매장 코드

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO order_history
                (order_date, item_cd, mid_cd, predicted_qty, recommended_qty,
                 actual_order_qty, current_stock, order_unit, status, store_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_date, item_cd, mid_cd, predicted_qty, recommended_qty,
                 actual_order_qty, current_stock, order_unit, status, store_id, self._now())
            )

            order_id = cursor.lastrowid
            conn.commit()
            return order_id
        finally:
            conn.close()

    def update_status(self, order_id: int, status: str, actual_qty: Optional[int] = None) -> None:
        """발주 상태 업데이트

        Args:
            order_id: 발주 이력 ID
            status: 변경할 상태
            actual_qty: 실제 발주 수량
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            if actual_qty is not None:
                cursor.execute(
                    "UPDATE order_history SET status = ?, actual_order_qty = ? WHERE id = ?",
                    (status, actual_qty, order_id)
                )
            else:
                cursor.execute(
                    "UPDATE order_history SET status = ? WHERE id = ?",
                    (status, order_id)
                )

            conn.commit()
        finally:
            conn.close()

    def get_orders_by_date(self, order_date: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """특정 날짜 발주 이력 조회

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            해당 날짜의 발주 이력 목록 (상품명, 중분류명 포함)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter("oh", store_id)

            cursor.execute(
                f"""
                SELECT oh.*, p.item_nm, mc.mid_nm
                FROM order_history oh
                LEFT JOIN products p ON oh.item_cd = p.item_cd
                LEFT JOIN mid_categories mc ON oh.mid_cd = mc.mid_cd
                WHERE oh.order_date = ? {sf}
                ORDER BY oh.mid_cd, oh.item_cd
                """,
                (order_date,) + sp
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pending_orders(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """대기 중인 발주 목록

        Args:
            store_id: 매장 코드

        Returns:
            pending 상태의 발주 이력 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter("oh", store_id)

            cursor.execute(
                f"""
                SELECT oh.*, p.item_nm
                FROM order_history oh
                LEFT JOIN products p ON oh.item_cd = p.item_cd
                WHERE oh.status = 'pending' {sf}
                ORDER BY oh.order_date, oh.item_cd
                """,
                sp
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
