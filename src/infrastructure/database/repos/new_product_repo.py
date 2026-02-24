"""
신상품 도입 현황 Repository

테이블:
- new_product_status: 주차별 도입 현황
- new_product_items: 미도입/미달성 개별 상품
- new_product_monthly: 월별 합계
"""

import sqlite3
from typing import Optional, List, Dict, Set

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class NewProductStatusRepository(BaseRepository):
    """신상품 도입 현황 저장소"""

    db_type = "store"

    # ─── 주차별 현황 ───

    def save_weekly_status(
        self,
        store_id: str,
        month_ym: str,
        week_no: int,
        data: dict,
    ) -> None:
        """주차별 도입 현황 UPSERT"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO new_product_status
                   (store_id, month_ym, week_no, period,
                    doip_rate, item_cnt, item_ad_cnt, doip_cnt, midoip_cnt,
                    ds_rate, ds_item_cnt, ds_cnt, mids_cnt,
                    doip_score, ds_score, tot_score, supp_pay_amt,
                    sta_dd, end_dd, week_cont, collected_at)
                   VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?)""",
                (
                    store_id, month_ym, week_no, data.get("period"),
                    data.get("doip_rate"), data.get("item_cnt"),
                    data.get("item_ad_cnt"), data.get("doip_cnt"),
                    data.get("midoip_cnt"),
                    data.get("ds_rate"), data.get("ds_item_cnt"),
                    data.get("ds_cnt"), data.get("mids_cnt"),
                    data.get("doip_score"), data.get("ds_score"),
                    data.get("tot_score"), data.get("supp_pay_amt"),
                    data.get("sta_dd"), data.get("end_dd"),
                    data.get("week_cont"), self._now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_weekly_status(
        self, store_id: str, month_ym: str
    ) -> List[Dict]:
        """월별 주차 현황 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_status
                   WHERE store_id = ? AND month_ym = ?
                   ORDER BY week_no""",
                (store_id, month_ym),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_current_status(self, store_id: str, month_ym: str) -> Optional[Dict]:
        """현재 월의 최신 주차 현황"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_status
                   WHERE store_id = ? AND month_ym = ?
                   ORDER BY week_no DESC LIMIT 1""",
                (store_id, month_ym),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ─── 미도입/미달성 상품 ───

    def save_items(
        self,
        store_id: str,
        month_ym: str,
        week_no: int,
        item_type: str,
        items: List[Dict],
    ) -> int:
        """미도입/미달성 상품 목록 저장

        Args:
            item_type: 'midoip' (미도입) | 'mids' (3일발주 미달성)
        Returns:
            저장된 건수
        """
        conn = self._get_conn()
        try:
            count = 0
            for item in items:
                conn.execute(
                    """INSERT OR REPLACE INTO new_product_items
                       (store_id, month_ym, week_no, item_type, item_cd,
                        item_nm, small_nm, ord_pss_nm, week_cont, ds_yn,
                        is_ordered, collected_at)
                       VALUES (?,?,?,?,?, ?,?,?,?,?, 0,?)""",
                    (
                        store_id, month_ym, week_no, item_type,
                        item.get("item_cd", ""),
                        item.get("item_nm"), item.get("small_nm"),
                        item.get("ord_pss_nm"), item.get("week_cont"),
                        item.get("ds_yn"), self._now(),
                    ),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def get_missing_items(
        self,
        store_id: str,
        month_ym: str,
        week_no: Optional[int] = None,
        item_type: str = "midoip",
        orderable_only: bool = False,
    ) -> List[Dict]:
        """미도입/미달성 상품 조회

        Args:
            orderable_only: True면 발주가능 상품만
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sql = """SELECT * FROM new_product_items
                     WHERE store_id = ? AND month_ym = ? AND item_type = ?"""
            params: list = [store_id, month_ym, item_type]

            if week_no is not None:
                sql += " AND week_no = ?"
                params.append(week_no)

            if orderable_only:
                sql += " AND ord_pss_nm IN ('발주가능', '가능')"

            sql += " ORDER BY week_no, item_cd"
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_unordered_items(
        self,
        store_id: str,
        month_ym: str,
        item_type: str = "midoip",
    ) -> List[Dict]:
        """아직 발주하지 않은 상품 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_items
                   WHERE store_id = ? AND month_ym = ?
                     AND item_type = ? AND is_ordered = 0
                     AND ord_pss_nm IN ('발주가능', '가능')
                   ORDER BY week_no, item_cd""",
                (store_id, month_ym, item_type),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_items_in_verification(
        self, store_id: str, month_ym: str
    ) -> Set[str]:
        """검증기간 중인 신상품 item_cd 집합 (예측 제외용)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT DISTINCT item_cd FROM new_product_items
                   WHERE store_id = ? AND month_ym = ?
                     AND item_type = 'midoip'
                     AND is_ordered = 1""",
                (store_id, month_ym),
            )
            return {row["item_cd"] for row in cursor.fetchall()}
        finally:
            conn.close()

    def mark_as_ordered(
        self,
        store_id: str,
        month_ym: str,
        item_cd: str,
        item_type: str = "midoip",
    ) -> None:
        """발주 완료 표시"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE new_product_items
                   SET is_ordered = 1, ordered_at = ?
                   WHERE store_id = ? AND month_ym = ?
                     AND item_cd = ? AND item_type = ?""",
                (self._now(), store_id, month_ym, item_cd, item_type),
            )
            conn.commit()
        finally:
            conn.close()

    # ─── 월별 합계 ───

    def save_monthly(
        self, store_id: str, month_ym: str, data: dict
    ) -> None:
        """월별 합계 UPSERT"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO new_product_monthly
                   (store_id, month_ym,
                    doip_item_cnt, doip_cnt, doip_rate, doip_score,
                    ds_item_cnt, ds_cnt, ds_rate, ds_score,
                    tot_score, supp_pay_amt,
                    next_min_score, next_max_score, next_supp_pay_amt,
                    collected_at)
                   VALUES (?,?, ?,?,?,?, ?,?,?,?, ?,?, ?,?,?, ?)""",
                (
                    store_id, month_ym,
                    data.get("doip_item_cnt"), data.get("doip_cnt"),
                    data.get("doip_rate"), data.get("doip_score"),
                    data.get("ds_item_cnt"), data.get("ds_cnt"),
                    data.get("ds_rate"), data.get("ds_score"),
                    data.get("tot_score"), data.get("supp_pay_amt"),
                    data.get("next_min_score"), data.get("next_max_score"),
                    data.get("next_supp_pay_amt"), self._now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_monthly_summary(
        self, store_id: str, month_ym: str
    ) -> Optional[Dict]:
        """월별 합계 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_monthly
                   WHERE store_id = ? AND month_ym = ?""",
                (store_id, month_ym),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
