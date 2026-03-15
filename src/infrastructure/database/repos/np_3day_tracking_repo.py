"""
신상품 3일발주 분산 추적 Repository

테이블: new_product_3day_tracking
- 주차별 상품의 분산 발주 추적 (발주간격, 다음발주일, 스킵횟수 등)
- v60: base_name 그룹핑 지원 (1/2차 상품 통합 추적)
"""

import json
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class NewProduct3DayTrackingRepository(BaseRepository):
    """신상품 3일발주 분산 추적 저장소"""

    db_type = "store"

    def upsert_tracking(
        self,
        store_id: str,
        week_label: str,
        week_start: str,
        week_end: str,
        product_code: str,
        product_name: str = "",
        sub_category: str = "",
        bgf_order_count: int = 0,
        order_interval_days: int = 0,
        next_order_date: str = "",
        base_name: str = "",
        product_codes: str = "",
    ) -> None:
        """추적 레코드 UPSERT (신규 삽입 또는 기존 업데이트)"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO new_product_3day_tracking
                   (store_id, week_label, week_start, week_end,
                    product_code, product_name, sub_category,
                    bgf_order_count, our_order_count,
                    order_interval_days, next_order_date,
                    skip_count, last_sale_after_order,
                    last_checked_at, is_completed, created_at,
                    base_name, product_codes, selected_code)
                   VALUES (?,?,?,?, ?,?,?, ?,0, ?,?, 0,0, ?,0,?, ?,?,?)
                   ON CONFLICT(store_id, week_label, base_name)
                   DO UPDATE SET
                    bgf_order_count = excluded.bgf_order_count,
                    product_name = COALESCE(excluded.product_name, product_name),
                    product_code = COALESCE(excluded.product_code, product_code),
                    sub_category = COALESCE(excluded.sub_category, sub_category),
                    product_codes = COALESCE(excluded.product_codes, product_codes),
                    order_interval_days = excluded.order_interval_days,
                    last_checked_at = excluded.last_checked_at""",
                (
                    store_id, week_label, week_start, week_end,
                    product_code, product_name, sub_category,
                    bgf_order_count,
                    order_interval_days, next_order_date,
                    self._now(), self._now(),
                    base_name, product_codes, "",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_active_items(
        self,
        store_id: str,
        week_label: str,
    ) -> List[Dict]:
        """현재 주차의 미완료 추적 항목 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_3day_tracking
                   WHERE store_id = ? AND week_label = ?
                     AND is_completed = 0
                   ORDER BY next_order_date, product_code""",
                (store_id, week_label),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_tracking(
        self,
        store_id: str,
        week_label: str,
        product_code: str,
    ) -> Optional[Dict]:
        """특정 상품의 추적 레코드 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_3day_tracking
                   WHERE store_id = ? AND week_label = ? AND product_code = ?""",
                (store_id, week_label, product_code),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_group_tracking(
        self,
        store_id: str,
        week_label: str,
        base_name: str,
    ) -> Optional[Dict]:
        """base_name 기준 그룹 추적 레코드 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM new_product_3day_tracking
                   WHERE store_id = ? AND week_label = ? AND base_name = ?""",
                (store_id, week_label, base_name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def record_order(
        self,
        store_id: str,
        week_label: str,
        product_code: str,
        next_order_date: str = "",
    ) -> None:
        """발주 완료 기록 (our_order_count 증가, next_order_date 갱신)"""
        conn = self._get_conn()
        try:
            now = self._now()
            conn.execute(
                """UPDATE new_product_3day_tracking
                   SET our_order_count = our_order_count + 1,
                       next_order_date = ?,
                       last_ordered_at = ?,
                       last_checked_at = ?
                   WHERE store_id = ? AND week_label = ? AND product_code = ?""",
                (next_order_date, now, now,
                 store_id, week_label, product_code),
            )
            conn.commit()
        finally:
            conn.close()

    def record_order_by_base_name(
        self,
        store_id: str,
        week_label: str,
        base_name: str,
        next_order_date: str = "",
        selected_code: str = "",
    ) -> None:
        """base_name 기준 발주 완료 기록"""
        conn = self._get_conn()
        try:
            now = self._now()
            conn.execute(
                """UPDATE new_product_3day_tracking
                   SET our_order_count = our_order_count + 1,
                       next_order_date = ?,
                       selected_code = ?,
                       last_ordered_at = ?,
                       last_checked_at = ?
                   WHERE store_id = ? AND week_label = ? AND base_name = ?""",
                (next_order_date, selected_code, now, now,
                 store_id, week_label, base_name),
            )
            conn.commit()
        finally:
            conn.close()

    def record_skip(
        self,
        store_id: str,
        week_label: str,
        product_code: str,
    ) -> None:
        """스킵 기록 (skip_count 증가)"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE new_product_3day_tracking
                   SET skip_count = skip_count + 1,
                       last_checked_at = ?
                   WHERE store_id = ? AND week_label = ? AND product_code = ?""",
                (self._now(), store_id, week_label, product_code),
            )
            conn.commit()
        finally:
            conn.close()

    def update_sale_after_order(
        self,
        store_id: str,
        week_label: str,
        product_code: str,
        sale_qty: int,
    ) -> None:
        """마지막 발주 이후 판매량 업데이트"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE new_product_3day_tracking
                   SET last_sale_after_order = ?,
                       last_checked_at = ?
                   WHERE store_id = ? AND week_label = ? AND product_code = ?""",
                (sale_qty, self._now(), store_id, week_label, product_code),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_completed(
        self,
        store_id: str,
        week_label: str,
        product_code: str,
    ) -> None:
        """발주 완료 (3회 달성) 표시"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE new_product_3day_tracking
                   SET is_completed = 1, last_checked_at = ?
                   WHERE store_id = ? AND week_label = ? AND product_code = ?""",
                (self._now(), store_id, week_label, product_code),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_completed_by_base_name(
        self,
        store_id: str,
        week_label: str,
        base_name: str,
    ) -> None:
        """base_name 기준 발주 완료 표시"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE new_product_3day_tracking
                   SET is_completed = 1, last_checked_at = ?
                   WHERE store_id = ? AND week_label = ? AND base_name = ?""",
                (self._now(), store_id, week_label, base_name),
            )
            conn.commit()
        finally:
            conn.close()
