"""
SalesRepository -- 판매 데이터 저장소

daily_sales, products, mid_categories, collection_logs, realtime_inventory,
order_tracking, inventory_batches, product_details 테이블 관련 CRUD.

원본: src/db/repository.py SalesRepository (lines 85-813)
"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.infrastructure.database.connection import DBRouter
from src.utils.logger import get_logger
from src.settings.constants import (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_CONSUMED,
    BATCH_STATUS_EXPIRED,
    DEFAULT_STORE_ID,
)

logger = get_logger(__name__)


class SalesRepository(BaseRepository):
    """판매 데이터 저장소"""
    db_type = "store"  # daily_sales is store-scoped

    def save_daily_sales(
        self,
        sales_data: List[Dict[str, Any]],
        sales_date: str,
        store_id: str = DEFAULT_STORE_ID,
        collected_at: Optional[str] = None,
        enable_validation: bool = True
    ) -> Dict[str, int]:
        """
        일별 판매 데이터 저장

        Args:
            sales_data: 판매 데이터 리스트
            sales_date: 판매 일자 (YYYY-MM-DD)
            store_id: 매장 코드
            collected_at: 수집 시점 (기본: 현재 시간)
            enable_validation: 검증 활성화 여부 (기본값: True)

        Returns:
            {"total": 총 건수, "new": 신규, "updated": 업데이트}
        """
        if collected_at is None:
            collected_at = self._now()

        conn = self._get_conn()
        cursor = conn.cursor()

        # 공통 DB 연결 (mid_categories, products는 common.db에 있음)
        common_conn = DBRouter.get_common_connection() if not self._db_path else conn
        common_cursor = common_conn.cursor() if common_conn is not conn else cursor

        stats = {"total": 0, "new": 0, "updated": 0}
        now = self._now()

        try:
            for item in sales_data:
                item_cd = item.get("ITEM_CD", "")
                mid_cd = item.get("MID_CD", "")

                if not item_cd or not mid_cd:
                    continue

                # 중분류 마스터 업데이트 (common.db에 저장)
                self._upsert_mid_category(
                    common_cursor, mid_cd, item.get("MID_NM", ""), now
                )

                # 상품 마스터 업데이트 (common.db에 저장)
                self._upsert_product(
                    common_cursor, item_cd, item.get("ITEM_NM", ""), mid_cd, now
                )

                # 일별 판매 데이터 저장 (store DB에 저장)
                is_new = self._upsert_daily_sale(
                    cursor,
                    collected_at=collected_at,
                    sales_date=sales_date,
                    item_cd=item_cd,
                    mid_cd=mid_cd,
                    sale_qty=self._to_int(item.get("SALE_QTY")),
                    ord_qty=self._to_int(item.get("ORD_QTY")),
                    buy_qty=self._to_int(item.get("BUY_QTY")),
                    disuse_qty=self._to_int(item.get("DISUSE_QTY")),
                    stock_qty=self._to_int(item.get("STOCK_QTY")),
                    now=now,
                    store_id=store_id,
                    item_nm=item.get("ITEM_NM", ""),
                    common_cursor=common_cursor
                )

                stats["total"] += 1
                if is_new:
                    stats["new"] += 1
                else:
                    stats["updated"] += 1

            # 공통 DB 커밋 (mid_categories, products)
            if common_conn is not conn:
                common_conn.commit()
            conn.commit()

            # 데이터 검증 (저장 후)
            if enable_validation:
                self._validate_saved_data(sales_data, sales_date, store_id)

        except Exception as e:
            if common_conn is not conn:
                common_conn.rollback()
            conn.rollback()
            raise e
        finally:
            if common_conn is not conn:
                common_conn.close()
            conn.close()

        return stats

    def _upsert_mid_category(
        self, cursor: sqlite3.Cursor, mid_cd: str, mid_nm: str, now: str
    ):
        """중분류 마스터 upsert"""
        cursor.execute(
            """
            INSERT INTO mid_categories (mid_cd, mid_nm, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mid_cd) DO UPDATE SET
                mid_nm = excluded.mid_nm,
                updated_at = excluded.updated_at
            """,
            (mid_cd, mid_nm, now, now)
        )

    def _upsert_product(
        self, cursor: sqlite3.Cursor, item_cd: str, item_nm: str,
        mid_cd: str, now: str
    ):
        """상품 마스터 upsert"""
        cursor.execute(
            """
            INSERT INTO products (item_cd, item_nm, mid_cd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_cd) DO UPDATE SET
                item_nm = excluded.item_nm,
                mid_cd = excluded.mid_cd,
                updated_at = excluded.updated_at
            """,
            (item_cd, item_nm, mid_cd, now, now)
        )

    def _upsert_daily_sale(
        self, cursor: sqlite3.Cursor, collected_at: str, sales_date: str,
        item_cd: str, mid_cd: str, sale_qty: int, ord_qty: int,
        buy_qty: int, disuse_qty: int, stock_qty: int, now: str,
        store_id: str = DEFAULT_STORE_ID, item_nm: Optional[str] = None,
        common_cursor: Optional[sqlite3.Cursor] = None
    ) -> bool:
        """일별 판매 데이터 upsert, 신규 여부 반환

        Args:
            store_id: 매장 코드
            item_nm: 상품명 (호출자가 전달, products 테이블 조회 불필요)
            common_cursor: common.db 커서 (product_details 조회용)
        """
        # 기존 데이터 확인 (증분 계산용으로 sale_qty, buy_qty도 조회)
        cursor.execute(
            "SELECT id, sale_qty, buy_qty FROM daily_sales WHERE store_id = ? AND sales_date = ? AND item_cd = ?",
            (store_id, sales_date, item_cd)
        )
        existing = cursor.fetchone()
        prev_sale_qty = existing[1] if existing else 0
        prev_buy_qty = existing[2] if existing else 0

        if existing:
            cursor.execute(
                """
                UPDATE daily_sales SET
                    collected_at = ?,
                    mid_cd = ?,
                    sale_qty = ?,
                    ord_qty = ?,
                    buy_qty = ?,
                    disuse_qty = ?,
                    stock_qty = ?
                WHERE store_id = ? AND sales_date = ? AND item_cd = ?
                """,
                (collected_at, mid_cd, sale_qty, ord_qty, buy_qty,
                 disuse_qty, stock_qty, store_id, sales_date, item_cd)
            )
            is_new = False
        else:
            cursor.execute(
                """
                INSERT INTO daily_sales
                (store_id, collected_at, sales_date, item_cd, mid_cd, sale_qty,
                 ord_qty, buy_qty, disuse_qty, stock_qty, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (store_id, collected_at, sales_date, item_cd, mid_cd, sale_qty,
                 ord_qty, buy_qty, disuse_qty, stock_qty, now)
            )
            is_new = True

        # realtime_inventory 테이블도 함께 업데이트 (재고 동기화)
        # 주의: is_cut_item은 INSERT 기본값 0, ON CONFLICT에서 갱신하지 않음
        #       → 기존 CUT 상태가 판매수집으로 리셋되지 않음
        # item_nm은 호출자에서 전달받음 (products는 common.db에 있으므로 store cursor로 직접 조회 불가)

        cursor.execute(
            """
            INSERT INTO realtime_inventory
            (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty,
             is_available, is_cut_item, queried_at, created_at)
            VALUES (?, ?, ?, ?, 0, 1, 1, 0, ?, ?)
            ON CONFLICT(store_id, item_cd) DO UPDATE SET
                item_nm = COALESCE(excluded.item_nm, realtime_inventory.item_nm),
                stock_qty = excluded.stock_qty,
                queried_at = excluded.queried_at
            """,
            (store_id, item_cd, item_nm, stock_qty, now, now)
        )

        # === 폐기 추적 파이프라인 (FR-01, FR-02, FR-03) ===
        try:
            sale_qty_diff = sale_qty - prev_sale_qty
            buy_qty_diff = buy_qty - prev_buy_qty

            # FR-01: order_tracking.remaining_qty FIFO 차감 (판매 증분)
            if sale_qty_diff > 0:
                cursor.execute(
                    """
                    SELECT id, remaining_qty FROM order_tracking
                    WHERE item_cd = ? AND store_id = ?
                    AND status IN ('ordered','arrived','selling')
                    AND remaining_qty > 0
                    ORDER BY order_date ASC, id ASC
                    """,
                    (item_cd, store_id)
                )
                remaining_to_deduct = sale_qty_diff
                for ot_row in cursor.fetchall():
                    if remaining_to_deduct <= 0:
                        break
                    ot_id = ot_row[0]
                    ot_remaining = ot_row[1]
                    deduct = min(ot_remaining, remaining_to_deduct)
                    cursor.execute(
                        "UPDATE order_tracking SET remaining_qty = remaining_qty - ?, updated_at = ? WHERE id = ?",
                        (deduct, now, ot_id)
                    )
                    remaining_to_deduct -= deduct

            from src.alert.config import ALERT_CATEGORIES
            is_food = mid_cd in ALERT_CATEGORIES

            # FR-03: 입고 감지 시 배치 자동 생성 (전체 상품)
            # 중복 방지: 같은 상품+입고일에 active 배치가 이미 있으면 스킵
            # (receiving_collector에서 센터매입 기반으로 이미 생성했을 수 있음)
            if buy_qty_diff > 0:
                # product_details는 common.db에 있으므로 common_cursor 사용
                pd_cursor = common_cursor if common_cursor else cursor
                pd_cursor.execute(
                    "SELECT expiration_days FROM product_details WHERE item_cd = ?",
                    (item_cd,)
                )
                pd_row = pd_cursor.fetchone()
                if pd_row and pd_row[0] and pd_row[0] > 0:
                    # 중복 체크: 같은 상품+입고일+매장에 배치 존재 여부 (status 무관)
                    # consumed/expired 배치도 체크하여 재수집 시 중복 생성 방지
                    cursor.execute(
                        """
                        SELECT id FROM inventory_batches
                        WHERE item_cd = ? AND store_id = ? AND receiving_date = ?
                        LIMIT 1
                        """,
                        (item_cd, store_id, sales_date)
                    )
                    if cursor.fetchone():
                        logger.debug(f"배치 이미 존재 (스킵): {item_cd} 입고일={sales_date}")
                    else:
                        expiration_days = pd_row[0]
                        cursor.execute(
                            "SELECT date(?, '+' || ? || ' days')",
                            (sales_date, expiration_days)
                        )
                        expiry_date = cursor.fetchone()[0]
                        cursor.execute(
                            """
                            INSERT INTO inventory_batches
                            (store_id, item_cd, item_nm, mid_cd, receiving_date, receiving_id,
                             expiration_days, expiry_date, initial_qty, remaining_qty,
                             status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (store_id, item_cd, item_nm or '', mid_cd, sales_date,
                             expiration_days, expiry_date, buy_qty_diff, buy_qty_diff,
                             BATCH_STATUS_ACTIVE, now, now)
                        )
                        logger.info(f"배치 자동생성: {item_cd} 수량={buy_qty_diff} 폐기예정={expiry_date}")

            # FR-02: inventory_batches FIFO sync (활성 배치 있는 경우)
            cursor.execute(
                "SELECT COALESCE(SUM(remaining_qty), 0) FROM inventory_batches WHERE item_cd = ? AND store_id = ? AND status = ?",
                (item_cd, store_id, BATCH_STATUS_ACTIVE)
            )
            batch_total = cursor.fetchone()[0]
            if batch_total > stock_qty and batch_total > 0:
                to_consume = batch_total - stock_qty
                # 인라인 FIFO 차감 (InventoryBatchRepository 인스턴스 없이 cursor 직접 사용)
                cursor.execute(
                    """
                    SELECT id, remaining_qty FROM inventory_batches
                    WHERE item_cd = ? AND store_id = ? AND status = ? AND remaining_qty > 0
                    ORDER BY receiving_date ASC, id ASC
                    """,
                    (item_cd, store_id, BATCH_STATUS_ACTIVE)
                )
                remaining_consume = to_consume
                consumed_total = 0
                for batch_row in cursor.fetchall():
                    if remaining_consume <= 0:
                        break
                    b_id = batch_row[0]
                    b_remaining = batch_row[1]
                    b_deduct = min(b_remaining, remaining_consume)
                    new_remaining = b_remaining - b_deduct
                    new_status = BATCH_STATUS_CONSUMED if new_remaining == 0 else BATCH_STATUS_ACTIVE
                    cursor.execute(
                        "UPDATE inventory_batches SET remaining_qty = ?, status = ?, updated_at = ? WHERE id = ?",
                        (new_remaining, new_status, now, b_id)
                    )
                    remaining_consume -= b_deduct
                    consumed_total += b_deduct

                # FR-02 후속: FIFO 차감분만큼 realtime_inventory.stock_qty도 보정
                # (daily_sales.stock_qty가 이미 실제 재고이므로, 여기서는 배치와의 정합성만 확인)
                # stock_qty 값 자체는 위에서 이미 갱신했으므로 별도 차감 불필요

        except Exception as e:
            # 폐기 추적 실패해도 기존 판매 데이터 저장 플로우는 유지
            logger.warning(f"폐기 추적 파이프라인 오류 ({item_cd}): {e}")

        return is_new

    def log_collection(
        self, collected_at: str, sales_date: str, stats: Dict[str, int],
        status: str, error_message: Optional[str] = None, duration: Optional[float] = None
    ):
        """수집 로그 저장

        Args:
            collected_at: 수집 시점 (ISO 형식)
            sales_date: 판매 일자 (YYYY-MM-DD)
            stats: 수집 통계 {total, new, updated}
            status: 수집 상태 (success, failed, partial)
            error_message: 오류 메시지
            duration: 소요 시간 (초)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO collection_logs
                (collected_at, sales_date, total_items, new_items, updated_items,
                 status, error_message, duration_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (collected_at, sales_date, stats.get("total", 0),
                 stats.get("new", 0), stats.get("updated", 0),
                 status, error_message, duration, self._now())
            )

            conn.commit()
        finally:
            conn.close()

    # =========================================================================
    # 조회 메서드
    # =========================================================================

    def get_daily_sales(
        self, sales_date: str, mid_cd: Optional[str] = None,
        store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """특정 날짜의 판매 데이터 조회

        Args:
            sales_date: 판매 일자 (YYYY-MM-DD)
            mid_cd: 중분류 코드 (None이면 전체)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            판매 데이터 목록 (상품명, 중분류명 포함)
        """
        # products, mid_categories는 common.db → ATTACH 필요
        conn = self._get_conn_with_common()
        try:
            cursor = conn.cursor()

            store_filter = "AND ds.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # common. 접두사: products, mid_categories는 ATTACH된 common DB에 있음
            # 단, _db_path 지정 시(테스트)에는 같은 DB이므로 접두사 불필요
            p_prefix = "common." if (self.db_type == "store" and self.store_id and not self._db_path) else ""

            if mid_cd:
                cursor.execute(
                    f"""
                    SELECT ds.*, p.item_nm, mc.mid_nm
                    FROM daily_sales ds
                    JOIN {p_prefix}products p ON ds.item_cd = p.item_cd
                    JOIN {p_prefix}mid_categories mc ON ds.mid_cd = mc.mid_cd
                    WHERE ds.sales_date = ? AND ds.mid_cd = ?
                    {store_filter}
                    ORDER BY ds.mid_cd, ds.item_cd
                    """,
                    (sales_date, mid_cd) + store_params
                )
            else:
                cursor.execute(
                    f"""
                    SELECT ds.*, p.item_nm, mc.mid_nm
                    FROM daily_sales ds
                    JOIN {p_prefix}products p ON ds.item_cd = p.item_cd
                    JOIN {p_prefix}mid_categories mc ON ds.mid_cd = mc.mid_cd
                    WHERE ds.sales_date = ?
                    {store_filter}
                    ORDER BY ds.mid_cd, ds.item_cd
                    """,
                    (sales_date,) + store_params
                )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_sales_history(
        self, item_cd: str, days: int = 30,
        store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """특정 상품의 판매 이력 조회

        Args:
            item_cd: 상품 코드
            days: 조회할 최근 일수
            store_id: 매장 코드 (None이면 전체)

        Returns:
            일별 판매/발주/입고/폐기/재고 데이터 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT sales_date, sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty
                FROM daily_sales
                WHERE item_cd = ?
                {store_filter}
                ORDER BY sales_date DESC
                LIMIT ?
                """,
                (item_cd,) + store_params + (days,)
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_all_mid_categories(self) -> List[Dict[str, Any]]:
        """모든 중분류 조회

        Returns:
            중분류 코드 및 이름 목록
        """
        # mid_categories는 common.db에 있음
        if self._db_path:
            conn = self._get_conn()
        else:
            conn = DBRouter.get_common_connection()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT mid_cd, mid_nm FROM mid_categories ORDER BY mid_cd"
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_collection_logs(self, limit: int = 10, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """최근 수집 로그 조회

        Args:
            limit: 조회할 최대 건수
            store_id: 매장 코드

        Returns:
            수집 로그 목록 (최신순)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""
                SELECT * FROM collection_logs
                WHERE 1=1 {sf}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                sp + (limit,)
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_stats_summary(self, store_id: Optional[str] = None) -> Dict[str, Any]:
        """통계 요약 조회

        Args:
            store_id: 매장 코드 (None이면 전체)

        Returns:
            총 상품 수, 중분류 수, 수집 일수, 최초/최근 수집일 포함 요약 dict
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # 총 상품 수, 중분류 수 → common.db에서 조회
            if self._db_path:
                common_conn = conn
            else:
                common_conn = DBRouter.get_common_connection()
            try:
                cc = common_conn.cursor()
                cc.execute("SELECT COUNT(*) FROM products")
                total_products = cc.fetchone()[0]
                cc.execute("SELECT COUNT(*) FROM mid_categories")
                total_categories = cc.fetchone()[0]
            finally:
                if common_conn is not conn:
                    common_conn.close()

            # 수집된 날짜 수
            cursor.execute(
                f"SELECT COUNT(DISTINCT sales_date) FROM daily_sales WHERE 1=1 {store_filter}",
                store_params
            )
            total_days = cursor.fetchone()[0]

            # 최초/최근 수집일
            cursor.execute(
                f"SELECT MIN(sales_date), MAX(sales_date) FROM daily_sales WHERE 1=1 {store_filter}",
                store_params
            )
            row = cursor.fetchone()
            first_date = row[0]
            last_date = row[1]

            return {
                "total_products": total_products,
                "total_categories": total_categories,
                "total_days": total_days,
                "first_date": first_date,
                "last_date": last_date
            }
        finally:
            conn.close()

    def get_collected_dates(self, days: int = 30, store_id: Optional[str] = None) -> List[str]:
        """과거 N일 중 수집된 날짜 목록 조회

        Args:
            days: 조회할 과거 일수
            store_id: 점포 ID (지정 시 해당 점포만 조회)

        Returns:
            수집된 날짜 문자열 목록 (YYYY-MM-DD, 오래된 순)
        """
        from datetime import timedelta

        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 과거 N일 범위
            today = datetime.now().date()
            start_date = today - timedelta(days=days - 1)

            # store_id 필터 추가
            if store_id:
                cursor.execute(
                    """
                    SELECT DISTINCT sales_date FROM daily_sales
                    WHERE sales_date >= ? AND sales_date <= ?
                    AND store_id = ?
                    ORDER BY sales_date
                    """,
                    (start_date.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), store_id)
                )
            else:
                cursor.execute(
                    """
                    SELECT DISTINCT sales_date FROM daily_sales
                    WHERE sales_date >= ? AND sales_date <= ?
                    ORDER BY sales_date
                    """,
                    (start_date.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
                )

            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_missing_dates(self, days: int = 30) -> List[str]:
        """과거 N일 중 데이터가 없는 날짜 목록 조회 (날짜 순)

        Args:
            days: 조회할 과거 일수

        Returns:
            누락된 날짜 문자열 목록 (YYYY-MM-DD, 오래된 순)
        """
        from datetime import timedelta

        today = datetime.now().date()

        # 과거 N일의 모든 날짜 생성
        all_dates = set()
        for i in range(days):
            date = today - timedelta(days=i)
            all_dates.add(date.strftime("%Y-%m-%d"))

        # DB에 있는 날짜
        collected_dates = set(self.get_collected_dates(days))

        # 누락된 날짜 = 전체 - 수집된 날짜
        missing_dates = all_dates - collected_dates

        # 날짜 순으로 정렬 (오래된 날짜 먼저)
        return sorted(list(missing_dates))

    def update_buy_qty(self, sales_date: str, item_cd: str, buy_qty: int,
                       store_id: Optional[str] = None) -> bool:
        """daily_sales의 buy_qty를 dsOrderSale 데이터로 보정

        기존 buy_qty가 0이고 dsOrderSale에서 buy_qty > 0인 경우만 업데이트.
        이미 buy_qty > 0이면 기존 값을 유지 (수집 데이터 우선).

        Args:
            sales_date: 판매일자 (YYYY-MM-DD)
            item_cd: 상품코드
            buy_qty: dsOrderSale에서 가져온 입고 수량
            store_id: 매장 코드 (None이면 전체)

        Returns:
            업데이트 여부
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(f"""
                UPDATE daily_sales
                SET buy_qty = ?
                WHERE sales_date = ? AND item_cd = ? AND buy_qty = 0
                {store_filter}
            """, (buy_qty, sales_date, item_cd) + store_params)
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


    def update_promo_type(self, item_cd: str, promo_type: str, start_date: str, end_date: str,
                          store_id: Optional[str] = None) -> int:
        """daily_sales의 promo_type을 행사 기간에 맞게 업데이트

        1일/15일 주기로 행사가 변경되므로 해당 기간의 모든 daily_sales 행 업데이트.

        Args:
            item_cd: 상품코드
            promo_type: 행사 타입 ('1+1', '2+1', '')
            start_date: 행사 시작일 (YYYY-MM-DD)
            end_date: 행사 종료일 (YYYY-MM-DD)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            업데이트된 행 수
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(f"""
                UPDATE daily_sales SET promo_type = ?
                WHERE item_cd = ? AND sales_date BETWEEN ? AND ? AND promo_type != ?
                {store_filter}
            """, (promo_type, item_cd, start_date, end_date, promo_type) + store_params)
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def update_disuse_qty_from_slip(
        self, sales_date: str, item_cd: str, slip_qty: int,
        mid_cd: str = "", store_id: Optional[str] = None
    ) -> str:
        """waste_slip_items 기반으로 daily_sales.disuse_qty 동기화

        3가지 케이스:
        A) daily_sales 행 있고 disuse_qty < slip_qty -> UPDATE (전표가 더 정확)
        B) daily_sales 행 있고 disuse_qty >= slip_qty -> SKIP (이미 정확하거나 초과)
        C) daily_sales 행 없음 -> INSERT (폐기만 있고 판매 없는 상품)

        Args:
            sales_date: 판매일자 (YYYY-MM-DD)
            item_cd: 상품코드
            slip_qty: 폐기전표 합산 수량
            mid_cd: 중분류 코드 (INSERT 시 필요)
            store_id: 매장 코드

        Returns:
            "updated" | "inserted" | "skipped"
        """
        sid = store_id or self.store_id
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 기존 행 확인
            cursor.execute(
                "SELECT disuse_qty FROM daily_sales "
                "WHERE store_id = ? AND sales_date = ? AND item_cd = ?",
                (sid, sales_date, item_cd),
            )
            row = cursor.fetchone()

            if row is not None:
                current_disuse = row[0] or 0
                if current_disuse >= slip_qty:
                    return "skipped"

                # Case A: 전표 값이 더 크면 덮어쓰기
                cursor.execute(
                    "UPDATE daily_sales SET disuse_qty = ? "
                    "WHERE store_id = ? AND sales_date = ? AND item_cd = ?",
                    (slip_qty, sid, sales_date, item_cd),
                )
                conn.commit()
                return "updated"
            else:
                # Case C: daily_sales에 행이 없음 -> INSERT
                # mid_cd가 비어있으면 products(common.db)에서 조회
                if not mid_cd:
                    try:
                        from src.infrastructure.database.connection import DBRouter

                        pconn = DBRouter.get_common_connection()
                        try:
                            pcur = pconn.cursor()
                            pcur.execute(
                                "SELECT mid_cd FROM products WHERE item_cd = ?",
                                (item_cd,),
                            )
                            prow = pcur.fetchone()
                            if prow and prow[0]:
                                mid_cd = prow[0]
                        finally:
                            pconn.close()
                    except Exception:
                        pass
                now = self._now()
                cursor.execute(
                    """
                    INSERT INTO daily_sales
                    (store_id, sales_date, item_cd, mid_cd,
                     sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty,
                     collected_at, created_at)
                    VALUES (?, ?, ?, ?, 0, 0, 0, ?, 0, ?, ?)
                    """,
                    (sid, sales_date, item_cd, mid_cd,
                     slip_qty, now, now),
                )
                conn.commit()
                return "inserted"
        finally:
            conn.close()

    def _validate_saved_data(
        self,
        sales_data: List[Dict[str, Any]],
        sales_date: str,
        store_id: str
    ):
        """저장된 데이터 검증 후크

        Args:
            sales_data: 저장된 판매 데이터
            sales_date: 판매 일자
            store_id: 점포 ID
        """
        try:
            from src.validation.data_validator import DataValidator
            from src.infrastructure.database.repos import ValidationRepository

            # 검증 실행
            validator = DataValidator(store_id=store_id)
            result = validator.validate_sales_data(sales_data, sales_date, store_id)

            # 검증 결과 로깅
            validation_repo = ValidationRepository()
            validation_repo.log_validation_result(result, validation_type='post_save')

            # 에러 발생 시 경고 로그 + 알림 (선택적)
            if not result.is_valid:
                logger.warning(
                    f"데이터 검증 실패: {sales_date} / {store_id} - "
                    f"{len(result.errors)}건 오류, {len(result.warnings)}건 경고"
                )
                # Kakao 알림은 선택적으로 활성화 가능
                # self._send_validation_alert(result, sales_date, store_id)

        except Exception as e:
            logger.error(f"검증 프로세스 오류: {e}")

    def _send_validation_alert(
        self,
        result,
        sales_date: str,
        store_id: str
    ):
        """검증 실패 시 카카오 알림 발송 (선택적 활성화)

        Args:
            result: ValidationResult
            sales_date: 판매 일자
            store_id: 점포 ID
        """
        try:
            from src.notification.kakao_notifier import KakaoNotifier

            notifier = KakaoNotifier()
            message = f"""
[데이터 검증 실패 알림]
일자: {sales_date}
점포: {store_id}

🔴 오류: {len(result.errors)}건
⚠️ 경고: {len(result.warnings)}건

주요 오류:
"""
            # 상위 3개 오류만 표시
            for error in result.errors[:3]:
                message += f"\n- {error.error_code}: {error.error_message}"

            if len(result.errors) > 3:
                message += f"\n... 외 {len(result.errors) - 3}건"

            notifier.send_message(message)
        except Exception as e:
            logger.warning(f"검증 알림 발송 실패: {e}")
