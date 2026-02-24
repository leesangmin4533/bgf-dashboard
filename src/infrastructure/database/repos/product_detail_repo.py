"""
ProductDetailRepository — 상품 상세 정보 저장소

원본: src/db/repository.py ProductDetailRepository (lines 885-1151)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ProductDetailRepository(BaseRepository):
    """상품 상세 정보 저장소 (발주 관련)"""

    db_type = "common"

    def exists(self, item_cd: str) -> bool:
        """상품 상세 정보 존재 여부 확인

        Args:
            item_cd: 상품 코드

        Returns:
            존재하면 True
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT 1 FROM product_details WHERE item_cd = ?",
                (item_cd,)
            )
            result = cursor.fetchone()
            return result is not None
        finally:
            conn.close()

    def save(self, item_cd: str, info: Dict[str, Any]) -> None:
        """상품 상세 정보 저장 (upsert)

        Args:
            item_cd: 상품 코드
            info: 상품 상세 정보 (유통기한, 발주 요일, 단위 등)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            cursor.execute(
                """
                INSERT INTO product_details
                (item_cd, item_nm, expiration_days, orderable_day, orderable_status,
                 order_unit_name, order_unit_qty, case_unit_qty, lead_time_days,
                 sell_price, margin_rate,
                 fetched_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_cd) DO UPDATE SET
                    item_nm = excluded.item_nm,
                    expiration_days = excluded.expiration_days,
                    orderable_day = excluded.orderable_day,
                    orderable_status = excluded.orderable_status,
                    order_unit_name = excluded.order_unit_name,
                    order_unit_qty = excluded.order_unit_qty,
                    case_unit_qty = excluded.case_unit_qty,
                    lead_time_days = excluded.lead_time_days,
                    sell_price = COALESCE(excluded.sell_price, product_details.sell_price),
                    margin_rate = COALESCE(excluded.margin_rate, product_details.margin_rate),
                    fetched_at = excluded.fetched_at,
                    updated_at = excluded.updated_at
                """,
                (
                    item_cd,
                    info.get("item_nm") or info.get("product_name"),
                    self._to_int(info.get("expiration_days")),
                    info.get("orderable_day", "일월화수목금토"),
                    info.get("orderable_status", ""),
                    info.get("order_unit_name", "낱개"),
                    self._to_int(info.get("order_unit_qty", 1)),
                    self._to_int(info.get("case_unit_qty", 1)),
                    self._to_int(info.get("lead_time_days", 1)),
                    self._to_price(info.get("sell_price")),
                    self._to_float(info.get("margin_rate")),
                    now,  # fetched_at
                    now,  # created_at
                    now   # updated_at
                )
            )

            conn.commit()
        finally:
            conn.close()

    def get(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """상품 상세 정보 조회

        Args:
            item_cd: 상품 코드

        Returns:
            상품 상세 정보 dict 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM product_details WHERE item_cd = ?",
                (item_cd,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all(self) -> List[Dict[str, Any]]:
        """모든 상품 상세 정보 조회

        Returns:
            전체 상품 상세 정보 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM product_details ORDER BY item_cd")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_items_without_details(self) -> List[str]:
        """상세 정보가 없는 상품 코드 목록

        Returns:
            product_details에 데이터가 없는 상품코드 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT p.item_cd FROM products p
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE pd.item_cd IS NULL
                """
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_active_items_missing_expiration(self, days: int = 30,
                                             store_id: Optional[str] = None) -> List[str]:
        """유통기한이 없는 활성 상품 코드 목록

        최근 N일 내 판매 실적이 있는 상품 중
        product_details에 없거나 expiration_days가 NULL인 상품 조회

        Args:
            days: 최근 판매 기준 일수 (기본 30일)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            유통기한 미등록 활성 상품코드 목록
        """
        # 방어적 프로그래밍: days는 최소 1
        days = max(1, days)

        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND ds.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT DISTINCT p.item_cd
                FROM products p
                INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE ds.sales_date >= date('now', ? || ' days')
                  AND (pd.item_cd IS NULL OR pd.expiration_days IS NULL)
                {store_filter}
                ORDER BY p.item_cd
                """,
                (str(-days),) + store_params
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_items_missing_margin(self, mid_codes: List[str], days: int = 30,
                                 store_id: Optional[str] = None) -> List[str]:
        """매가/이익율이 없는 활성 상품 코드 목록

        지정 카테고리 내 최근 N일 판매 실적이 있는 상품 중
        margin_rate가 NULL인 상품 조회

        Args:
            mid_codes: 중분류 코드 목록
            days: 최근 판매 기준 일수 (기본 30일)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            margin_rate 미등록 활성 상품코드 목록
        """
        if not mid_codes:
            return []
        days = max(1, days)

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(mid_codes))

            store_filter = "AND ds.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT DISTINCT p.item_cd
                FROM products p
                INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE p.mid_cd IN ({placeholders})
                  AND ds.sales_date >= date('now', ? || ' days')
                  AND (pd.item_cd IS NULL OR pd.margin_rate IS NULL)
                {store_filter}
                ORDER BY p.item_cd
                """,
                (*mid_codes, str(-days)) + store_params
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def bulk_update_order_unit_qty(self, items: List[Dict[str, Any]]) -> int:
        """발주단위(order_unit_qty)만 일괄 갱신

        BGF 사이트 발주현황조회 "전체" 탭에서 수집한 ORD_UNIT_QTY를
        product_details에 반영. 기존 필드(expiration_days 등)는 보존.

        Args:
            items: [{"item_cd", "item_nm", "mid_cd", "order_unit_qty"}, ...]

        Returns:
            처리된 건수
        """
        if not items:
            return 0

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            data = [
                (
                    item["item_cd"],
                    item.get("item_nm", ""),
                    self._to_int(item.get("order_unit_qty", 1)),
                    now,
                    now,
                )
                for item in items
                if item.get("item_cd")
            ]

            cursor.executemany(
                """INSERT INTO product_details
                   (item_cd, item_nm, order_unit_qty, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(item_cd) DO UPDATE SET
                       order_unit_qty = excluded.order_unit_qty,
                       item_nm = COALESCE(
                           NULLIF(excluded.item_nm, ''),
                           product_details.item_nm
                       ),
                       updated_at = excluded.updated_at""",
                data,
            )
            conn.commit()
            count = len(data)
            logger.info(f"발주단위 일괄 갱신: {count}건")
            return count
        finally:
            conn.close()

    def get_all_active_items(self, days: int = 30,
                             store_id: Optional[str] = None) -> List[str]:
        """전체 활성 상품 코드 목록

        최근 N일 내 판매 실적이 있는 모든 상품 조회 (유통기한 유무 무관)

        Args:
            days: 최근 판매 기준 일수 (기본 30일)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            활성 상품코드 목록
        """
        # 방어적 프로그래밍: days는 최소 1
        days = max(1, days)

        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND ds.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT DISTINCT p.item_cd
                FROM products p
                INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd
                WHERE ds.sales_date >= date('now', ? || ' days')
                {store_filter}
                ORDER BY p.item_cd
                """,
                (str(-days),) + store_params
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()
