"""
ManualOrderItemRepository -- 수동(일반) 발주 상품 저장소

발주 현황 조회 > 일반 탭(rdGubun='1')에서 수집한 수동 발주 상품 관리.
- 푸드 카테고리(001~005, 012): 예측 발주량 차감에 사용
- 비푸드 카테고리: 기록용
"""

from datetime import datetime, date
from typing import Dict, List, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

FOOD_MID_CODES = ("001", "002", "003", "004", "005", "012")


class ManualOrderItemRepository(BaseRepository):
    """수동(일반) 발주 상품 저장소

    사이트 '발주 현황 조회 > 일반' 탭의 ORD_CNT > 0 상품을 저장.
    매일 Phase 1.2에서 refresh()로 당일 데이터 교체.
    """

    db_type = "store"

    def refresh(
        self,
        items: List[Dict[str, Any]],
        order_date: str,
        store_id: Optional[str] = None,
    ) -> int:
        """당일 수동 발주 데이터 갱신 (DELETE + INSERT)

        Args:
            items: collect_normal_order_items() 결과
            order_date: 발주일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            저장된 상품 수
        """
        sid = store_id or self.store_id
        conn = self._get_conn()
        now = self._now()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM manual_order_items WHERE order_date = ? AND store_id = ?",
                (order_date, sid),
            )

            if not items:
                conn.commit()
                logger.info(f"수동발주 0건 - 기존 데이터 삭제 완료 ({order_date})")
                return 0

            valid = []
            for item in items:
                item_cd = item.get("item_cd")
                if not item_cd:
                    continue
                ord_cnt = int(item.get("ord_cnt", 0) or 0)
                ord_unit_qty = int(item.get("ord_unit_qty", 1) or 1)
                order_qty = int(item.get("order_qty", 0) or 0) or (ord_cnt * ord_unit_qty)
                valid.append((
                    sid,
                    item_cd,
                    item.get("item_nm"),
                    item.get("mid_cd"),
                    item.get("mid_nm"),
                    order_qty,
                    ord_cnt,
                    ord_unit_qty,
                    item.get("ord_input_id"),
                    int(item.get("ord_amt", 0) or 0),
                    order_date,
                    now,
                ))

            cursor.executemany(
                """INSERT OR REPLACE INTO manual_order_items
                   (store_id, item_cd, item_nm, mid_cd, mid_nm,
                    order_qty, ord_cnt, ord_unit_qty, ord_input_id,
                    ord_amt, order_date, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                valid,
            )
            conn.commit()
            return len(valid)
        finally:
            conn.close()

    def get_today_food_orders(
        self,
        store_id: Optional[str] = None,
        target_date: Optional[date] = None,
    ) -> Dict[str, int]:
        """푸드 카테고리 수동 발주 반환

        Args:
            store_id: 매장 코드
            target_date: 조회 기준일 (기본값: 당일)

        Returns:
            {item_cd: order_qty, ...} 푸드만
        """
        sid = store_id or self.store_id
        today = (target_date or date.today()).strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            placeholders = ",".join("?" for _ in FOOD_MID_CODES)
            rows = conn.execute(
                f"""SELECT item_cd, order_qty FROM manual_order_items
                    WHERE order_date = ? AND store_id = ?
                      AND mid_cd IN ({placeholders})
                      AND order_qty > 0""",
                (today, sid, *FOOD_MID_CODES),
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()

    def get_today_orders(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """당일 전체 수동 발주 반환 (기록 조회용)"""
        sid = store_id or self.store_id
        today = date.today().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT item_cd, item_nm, mid_cd, mid_nm,
                          order_qty, ord_cnt, ord_unit_qty,
                          ord_input_id, ord_amt, order_date, collected_at
                   FROM manual_order_items
                   WHERE order_date = ? AND store_id = ?
                   ORDER BY mid_cd, item_cd""",
                (today, sid),
            ).fetchall()
            return [
                {
                    "item_cd": r[0], "item_nm": r[1], "mid_cd": r[2],
                    "mid_nm": r[3], "order_qty": r[4], "ord_cnt": r[5],
                    "ord_unit_qty": r[6], "ord_input_id": r[7],
                    "ord_amt": r[8], "order_date": r[9], "collected_at": r[10],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_today_summary(self, store_id: Optional[str] = None) -> Dict[str, Any]:
        """당일 수동 발주 요약"""
        sid = store_id or self.store_id
        today = date.today().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN mid_cd IN ('001','002','003','004','005','012')
                              THEN 1 ELSE 0 END),
                          SUM(order_qty),
                          SUM(ord_amt)
                   FROM manual_order_items
                   WHERE order_date = ? AND store_id = ? AND order_qty > 0""",
                (today, sid),
            ).fetchone()
            return {
                "total_count": row[0] or 0,
                "food_count": row[1] or 0,
                "non_food_count": (row[0] or 0) - (row[1] or 0),
                "total_qty": row[2] or 0,
                "total_amt": row[3] or 0,
            }
        finally:
            conn.close()
