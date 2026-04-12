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

    def bulk_update_demand_pattern(self, patterns: Dict[str, str]) -> int:
        """수요 패턴(demand_pattern) 일괄 갱신

        DemandClassifier에서 분류한 패턴을 product_details에 반영.

        Args:
            patterns: {item_cd: "daily"|"frequent"|"intermittent"|"slow"}

        Returns:
            처리된 건수
        """
        if not patterns:
            return 0

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            data = [
                (pattern, now, item_cd)
                for item_cd, pattern in patterns.items()
                if item_cd and pattern
            ]

            cursor.executemany(
                """UPDATE product_details
                   SET demand_pattern = ?, updated_at = ?
                   WHERE item_cd = ?""",
                data,
            )
            conn.commit()
            count = cursor.rowcount
            logger.info(f"수요 패턴 일괄 갱신: {count}건")
            return count
        finally:
            conn.close()

    def update_orderable_day(self, item_cd: str, orderable_day: str) -> bool:
        """발주가능요일만 단독 업데이트 (검증 교정용)

        Args:
            item_cd: 상품 코드
            orderable_day: 새 발주가능요일 (예: "월화수목금토")

        Returns:
            업데이트 성공 여부
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            cursor.execute(
                "UPDATE product_details SET orderable_day = ?, updated_at = ? WHERE item_cd = ?",
                (orderable_day, now, item_cd)
            )
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"[DB교정] {item_cd}: orderable_day → {orderable_day}")
            return updated
        finally:
            conn.close()

    def get_items_needing_detail_fetch(self, limit: int = 200) -> List[str]:
        """상세 정보 수집이 필요한 상품 코드 목록

        대상 조건 (OR):
        1. product_details.fetched_at IS NULL (BGF 사이트 미조회)
        2. product_details.expiration_days IS NULL (유통기한 누락)
        3. product_details.orderable_day = '일월화수목금토' (기본값)
        4. products.mid_cd IN ('999', '') (카테고리 미분류)
        5. product_details.large_cd IS NULL (대분류 미수집)

        product_details에 아예 없는 products도 포함.

        Args:
            limit: 최대 반환 개수

        Returns:
            item_cd 리스트
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT p.item_cd
                FROM products p
                LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE pd.fetched_at IS NULL
                   OR pd.expiration_days IS NULL
                   OR pd.orderable_day = '일월화수목금토'
                   OR p.mid_cd IN ('999', '')
                   OR pd.large_cd IS NULL
                ORDER BY p.updated_at DESC
                LIMIT ?
            """, (limit,))
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def bulk_update_from_popup(self, item_cd: str, data: Dict[str, Any]) -> bool:
        """팝업에서 수집한 데이터로 product_details 부분 업데이트

        기존에 정확한 값이 있는 필드는 덮어쓰지 않음:
        - expiration_days: 기존 NULL인 경우만 갱신
        - orderable_day: 기존 '일월화수목금토'(기본값)인 경우만 갱신
        - orderable_status: 기존 NULL인 경우만 갱신
        - sell_price: 기존 NULL인 경우만 갱신
        - fetched_at: 항상 갱신

        Args:
            item_cd: 상품코드
            data: 팝업에서 추출된 데이터

        Returns:
            성공 여부
        """
        now = self._now()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM product_details WHERE item_cd = ?",
                (item_cd,)
            )
            exists = cursor.fetchone()[0] > 0

            if not exists:
                cursor.execute("""
                    INSERT INTO product_details
                    (item_cd, item_nm, expiration_days, orderable_day,
                     orderable_status, order_unit_name, order_unit_qty,
                     case_unit_qty, sell_price, fetched_at,
                     large_cd, small_cd, small_nm, class_nm,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item_cd,
                    data.get("item_nm"),
                    self._to_int(data.get("expiration_days")) if data.get("expiration_days") else None,
                    data.get("orderable_day", "일월화수목금토"),
                    data.get("orderable_status"),
                    data.get("order_unit_name"),
                    self._to_int(data.get("order_unit_qty", 1)),
                    self._to_int(data.get("case_unit_qty", 1)),
                    self._to_price(data.get("sell_price")),
                    data.get("fetched_at", now),
                    data.get("large_cd"),
                    data.get("small_cd"),
                    data.get("small_nm"),
                    data.get("class_nm"),
                    now, now,
                ))
            else:
                exp_days = self._to_int(data.get("expiration_days")) if data.get("expiration_days") else None
                ord_day = data.get("orderable_day")
                ord_status = data.get("orderable_status")
                ord_qty = self._to_int(data.get("order_unit_qty")) if data.get("order_unit_qty") else None
                ord_name = data.get("order_unit_name")
                case_qty = self._to_int(data.get("case_unit_qty")) if data.get("case_unit_qty") else None
                sell_prc = self._to_price(data.get("sell_price"))
                fetched = data.get("fetched_at", now)

                large_cd_val = data.get("large_cd")
                small_cd_val = data.get("small_cd")
                small_nm_val = data.get("small_nm")
                class_nm_val = data.get("class_nm")

                cursor.execute("""
                    UPDATE product_details
                    SET
                        expiration_days = COALESCE(expiration_days, ?),
                        orderable_day = CASE
                            WHEN orderable_day IS NULL OR orderable_day = '일월화수목금토'
                            THEN COALESCE(?, orderable_day)
                            ELSE orderable_day END,
                        orderable_status = COALESCE(orderable_status, ?),
                        order_unit_qty = CASE
                            WHEN order_unit_qty IS NULL OR order_unit_qty <= 1
                            THEN COALESCE(?, order_unit_qty)
                            ELSE order_unit_qty END,
                        order_unit_name = COALESCE(order_unit_name, ?),
                        case_unit_qty = COALESCE(case_unit_qty, ?),
                        sell_price = COALESCE(sell_price, ?),
                        large_cd = COALESCE(large_cd, ?),
                        small_cd = COALESCE(small_cd, ?),
                        small_nm = COALESCE(small_nm, ?),
                        class_nm = COALESCE(class_nm, ?),
                        fetched_at = ?,
                        updated_at = ?
                    WHERE item_cd = ?
                """, (
                    exp_days,
                    ord_day,
                    ord_status,
                    ord_qty,
                    ord_name,
                    case_qty,
                    sell_prc,
                    large_cd_val,
                    small_cd_val,
                    small_nm_val,
                    class_nm_val,
                    fetched,
                    now,
                    item_cd,
                ))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"[BatchDetail] DB 저장 실패 {item_cd}: {e}")
            return False
        finally:
            conn.close()

    def update_product_mid_cd(self, item_cd: str, mid_cd: str) -> bool:
        """products 테이블의 mid_cd 업데이트 (추정값만 덮어쓰기)

        기존 mid_cd가 '999' 또는 '' 인 경우만 갱신.
        이미 정확한 카테고리가 있으면 건드리지 않음.

        Args:
            item_cd: 상품코드
            mid_cd: BGF 팝업에서 가져온 정확한 중분류코드

        Returns:
            성공 여부
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE products
                SET mid_cd = ?, updated_at = ?
                WHERE item_cd = ?
                AND (mid_cd IN ('999', '') OR mid_cd IS NULL)
            """, (mid_cd, self._now(), item_cd))
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"[BatchDetail] products.mid_cd 갱신: {item_cd} -> {mid_cd}")
            return updated
        except Exception as e:
            logger.error(f"[BatchDetail] mid_cd 갱신 실패 {item_cd}: {e}")
            return False
        finally:
            conn.close()

    def upsert_mid_category_detail(
        self,
        mid_cd: str,
        mid_nm: Optional[str] = None,
        large_cd: Optional[str] = None,
        large_nm: Optional[str] = None,
    ) -> bool:
        """mid_categories에 대분류 정보 업데이트

        기존 mid_nm이 비어 있으면 갱신, large_cd/large_nm은 NULL인 경우만 채움.

        Args:
            mid_cd: 중분류 코드
            mid_nm: 중분류 명칭
            large_cd: 대분류 코드
            large_nm: 대분류 명칭

        Returns:
            성공 여부
        """
        if not mid_cd or not mid_cd.strip():
            return False

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            cursor.execute("""
                INSERT INTO mid_categories
                (mid_cd, mid_nm, large_cd, large_nm, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mid_cd) DO UPDATE SET
                    mid_nm = COALESCE(
                        NULLIF(excluded.mid_nm, ''), mid_categories.mid_nm
                    ),
                    large_cd = COALESCE(mid_categories.large_cd, excluded.large_cd),
                    large_nm = COALESCE(mid_categories.large_nm, excluded.large_nm),
                    updated_at = excluded.updated_at
            """, (mid_cd, mid_nm or "", large_cd, large_nm, now, now))
            conn.commit()
            return True
        except Exception as e:
            logger.error(
                f"[BatchDetail] mid_categories 업데이트 실패 {mid_cd}: {e}"
            )
            return False
        finally:
            conn.close()

    def bulk_update_category_force(self, items: List[Dict[str, Any]]) -> int:
        """카테고리 정보 강제 일괄 갱신 (일회성 벌크 업데이트용)

        기존 값과 무관하게 large_cd, small_cd, small_nm, class_nm을 덮어씀.
        동시에 products.mid_cd, mid_categories도 갱신.

        Args:
            items: [{item_cd, mid_cd, mid_nm, large_cd, large_nm,
                     small_cd, small_nm, class_nm}, ...]

        Returns:
            처리된 건수
        """
        if not items:
            return 0

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            count = 0

            for item in items:
                item_cd = item.get("item_cd")
                if not item_cd:
                    continue

                mid_cd = item.get("mid_cd")
                large_cd = item.get("large_cd")
                small_cd = item.get("small_cd")
                small_nm = item.get("small_nm")
                class_nm = item.get("class_nm")

                # product_details 카테고리 강제 갱신
                cursor.execute("""
                    UPDATE product_details
                    SET large_cd = ?, small_cd = ?, small_nm = ?,
                        class_nm = ?, fetched_at = ?, updated_at = ?
                    WHERE item_cd = ?
                """, (large_cd, small_cd, small_nm, class_nm, now, now, item_cd))

                if cursor.rowcount == 0:
                    # product_details에 없으면 INSERT
                    cursor.execute("""
                        INSERT OR IGNORE INTO product_details
                        (item_cd, item_nm, large_cd, small_cd, small_nm,
                         class_nm, fetched_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (item_cd, item.get("item_nm", ""),
                          large_cd, small_cd, small_nm, class_nm, now, now, now))

                # products.mid_cd 갱신 (API 응답값으로 강제)
                if mid_cd and mid_cd.strip():
                    cursor.execute("""
                        UPDATE products SET mid_cd = ?, updated_at = ?
                        WHERE item_cd = ? AND mid_cd != ?
                    """, (mid_cd, now, item_cd, mid_cd))

                # mid_categories UPSERT
                mid_nm = item.get("mid_nm")
                large_nm = item.get("large_nm")
                if mid_cd and mid_cd.strip():
                    cursor.execute("""
                        INSERT INTO mid_categories
                        (mid_cd, mid_nm, large_cd, large_nm, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(mid_cd) DO UPDATE SET
                            mid_nm = COALESCE(NULLIF(excluded.mid_nm, ''), mid_categories.mid_nm),
                            large_cd = COALESCE(excluded.large_cd, mid_categories.large_cd),
                            large_nm = COALESCE(excluded.large_nm, mid_categories.large_nm),
                            updated_at = excluded.updated_at
                    """, (mid_cd, mid_nm or "", large_cd, large_nm, now, now))

                count += 1

            conn.commit()
            logger.info(f"[BulkCategory] 카테고리 강제 갱신: {count}건")
            return count
        except Exception as e:
            logger.error(f"[BulkCategory] 갱신 실패: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    def get_all_product_codes(self) -> List[str]:
        """전체 상품 코드 목록 (products 테이블)

        Returns:
            모든 item_cd 리스트
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT item_cd FROM products ORDER BY item_cd")
            return [row[0] for row in cursor.fetchall()]
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

        if store_id:
            # daily_sales는 매장 DB, products는 common DB → 매장 DB + common ATTACH 필요
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_store_connection_with_common(store_id)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT p.item_cd
                    FROM common.products p
                    INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd
                    WHERE ds.sales_date >= date('now', ? || ' days')
                    ORDER BY p.item_cd
                    """,
                    (str(-days),)
                )
                rows = cursor.fetchall()
                return [row[0] for row in rows]
            finally:
                conn.close()
        else:
            # store_id 없음: 레거시 단일 DB 사용
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT p.item_cd
                    FROM products p
                    INNER JOIN daily_sales ds ON p.item_cd = ds.item_cd
                    WHERE ds.sales_date >= date('now', ? || ' days')
                    ORDER BY p.item_cd
                    """,
                    (str(-days),)
                )
                rows = cursor.fetchall()
                return [row[0] for row in rows]
            finally:
                conn.close()
