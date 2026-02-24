"""
RealtimeInventoryRepository — 실시간 재고/미입고 저장소

원본: src/db/repository.py RealtimeInventoryRepository 클래스에서 1:1 추출
"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RealtimeInventoryRepository(BaseRepository):
    """실시간 재고/미입고 저장소 (발주 전 조회 데이터)"""

    db_type = "store"

    def save(
        self,
        item_cd: str,
        stock_qty: int = 0,
        pending_qty: int = 0,
        order_unit_qty: int = 1,
        is_available: bool = True,
        item_nm: Optional[str] = None,
        is_cut_item: bool = False,
        store_id: str = DEFAULT_STORE_ID
    ) -> None:
        """
        실시간 재고/미입고 정보 저장 (upsert)

        Args:
            item_cd: 상품코드
            stock_qty: 현재 재고 (NOW_QTY)
            pending_qty: 미입고 수량
            order_unit_qty: 발주 단위 수량 (입수)
            is_available: 점포 취급 여부
            item_nm: 상품명 (편의용)
            is_cut_item: 발주중지(CUT) 상품 여부
            store_id: 점포 코드
        """
        # 음수 재고/미입고 방어 (저장 시점에서 0으로 변환)
        stock_qty = self._to_positive_int(stock_qty)
        pending_qty = self._to_positive_int(pending_qty)
        order_unit_qty = max(1, self._to_int(order_unit_qty))  # 최소 1

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            cursor.execute(
                """
                INSERT INTO realtime_inventory
                (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty, is_available, is_cut_item, queried_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, item_cd) DO UPDATE SET
                    item_nm = excluded.item_nm,
                    stock_qty = excluded.stock_qty,
                    pending_qty = excluded.pending_qty,
                    order_unit_qty = excluded.order_unit_qty,
                    is_available = excluded.is_available,
                    is_cut_item = excluded.is_cut_item,
                    queried_at = excluded.queried_at
                """,
                (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty,
                 1 if is_available else 0, 1 if is_cut_item else 0, now, now)
            )

            conn.commit()
        finally:
            conn.close()

    def save_bulk(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        일괄 저장

        Args:
            items: 저장할 항목 리스트
                [{"item_cd": "xxx", "stock_qty": 10, "pending_qty": 5, ...}, ...]

        Returns:
            {"total": N, "new": N, "updated": N}
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = self._now()

        stats = {"total": 0, "new": 0, "updated": 0}

        try:
            for item in items:
                item_cd = item.get("item_cd")
                if not item_cd:
                    continue

                store_id = item.get("store_id", DEFAULT_STORE_ID)

                # 기존 데이터 확인
                cursor.execute(
                    "SELECT id FROM realtime_inventory WHERE store_id = ? AND item_cd = ?",
                    (store_id, item_cd)
                )
                existing = cursor.fetchone()

                # 음수 재고/미입고 방어 (저장 시점에서 0으로 변환)
                stock_qty = self._to_positive_int(item.get("stock_qty", 0))
                pending_qty = self._to_positive_int(item.get("pending_qty", 0))
                order_unit_qty = max(1, self._to_int(item.get("order_unit_qty", 1)))

                cursor.execute(
                    """
                    INSERT INTO realtime_inventory
                    (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty, is_available, is_cut_item, queried_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, item_cd) DO UPDATE SET
                        item_nm = excluded.item_nm,
                        stock_qty = excluded.stock_qty,
                        pending_qty = excluded.pending_qty,
                        order_unit_qty = excluded.order_unit_qty,
                        is_available = excluded.is_available,
                        is_cut_item = excluded.is_cut_item,
                        queried_at = excluded.queried_at
                    """,
                    (
                        store_id,
                        item_cd,
                        item.get("item_nm"),
                        stock_qty,
                        pending_qty,
                        order_unit_qty,
                        1 if item.get("is_available", True) else 0,
                        1 if item.get("is_cut_item", False) else 0,
                        now,
                        now
                    )
                )

                stats["total"] += 1
                if existing:
                    stats["updated"] += 1
                else:
                    stats["new"] += 1

            conn.commit()
            return stats

        finally:
            conn.close()

    # 재고 데이터 유효기간 (이보다 오래된 queried_at은 신뢰하지 않음)
    STOCK_FRESHNESS_HOURS = 36

    @staticmethod
    def _get_stale_hours_for_expiry(expiry_days: Optional[int], mid_cd: Optional[str] = None) -> int:
        """유통기한 → stale_hours 변환 (2단계 폴백)

        우선순위:
        1. product_details.expiration_days (상품별)
        2. FOOD_EXPIRY_FALLBACK[mid_cd] (카테고리별 기본값)
        3. 기본값 36h

        변환: 유통기한 1일→18h, 2일→36h, 3일→54h, 4일+→36h
        """
        from src.prediction.categories.food import FOOD_EXPIRY_FALLBACK

        eff_days = None

        # 1) 상품별 DB 값
        if expiry_days is not None and expiry_days > 0:
            eff_days = expiry_days

        # 2) 카테고리 기본값 폴백
        if eff_days is None and mid_cd:
            fallback = FOOD_EXPIRY_FALLBACK.get(mid_cd)
            if fallback is not None:
                eff_days = fallback

        # 3) 변환
        if eff_days is not None and eff_days <= 3:
            return eff_days * 18  # 1일→18h, 2일→36h, 3일→54h

        return RealtimeInventoryRepository.STOCK_FRESHNESS_HOURS

    def get(self, item_cd: str, store_id: str = DEFAULT_STORE_ID) -> Optional[Dict[str, Any]]:
        """
        상품별 실시간 재고/미입고 조회

        Args:
            item_cd: 상품코드
            store_id: 점포 코드

        Returns:
            {"item_cd": "xxx", "stock_qty": 10, "pending_qty": 5, ...} 또는 None
            queried_at이 유통기한 기반 TTL보다 오래되면 _stale=True 플래그 추가
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM realtime_inventory WHERE store_id = ? AND item_cd = ?",
                (store_id, item_cd)
            )
            row = cursor.fetchone()

            if row:
                result = dict(row)
                result["is_available"] = bool(result.get("is_available", 1))

                # 음수 재고/미입고 방어 (조회 시점에서도 0으로 변환)
                result["stock_qty"] = self._to_positive_int(result.get("stock_qty", 0))
                result["pending_qty"] = self._to_positive_int(result.get("pending_qty", 0))

                # 유통기한 기반 staleness: product_details → 카테고리 폴백
                expiry_days, mid_cd = self._lookup_expiry_info(item_cd, conn)
                result["_stale"] = self._is_stale(
                    result.get("queried_at"), expiry_days, mid_cd
                )

                return result
            return None
        finally:
            conn.close()

    def _lookup_expiry_info(
        self, item_cd: str, conn: sqlite3.Connection
    ) -> tuple:
        """상품의 유통기한/중분류 조회 (common DB JOIN)

        Returns:
            (expiry_days, mid_cd) — 못 찾으면 (None, None)
        """
        cursor = conn.cursor()
        try:
            # store DB라면 common. 접두사로 접근
            prefix = "common." if self.store_id else ""
            cursor.execute(
                f"""
                SELECT pd.expiration_days, p.mid_cd
                FROM {prefix}product_details pd
                LEFT JOIN {prefix}products p ON pd.item_cd = p.item_cd
                WHERE pd.item_cd = ?
                """,
                (item_cd,)
            )
            row = cursor.fetchone()
            if row:
                return (row[0], row[1])

            # product_details에 없으면 products에서 mid_cd만
            cursor.execute(
                f"SELECT mid_cd FROM {prefix}products WHERE item_cd = ?",
                (item_cd,)
            )
            row = cursor.fetchone()
            return (None, row[0] if row else None)
        except Exception:
            return (None, None)

    def _is_stale(
        self,
        queried_at: Optional[str],
        expiry_days: Optional[int] = None,
        mid_cd: Optional[str] = None,
    ) -> bool:
        """queried_at이 유통기한 기반 TTL보다 오래되었는지 판별

        Args:
            queried_at: 조회 시각 (ISO format)
            expiry_days: product_details.expiration_days (상품별)
            mid_cd: 중분류 코드 (카테고리 폴백용)
        """
        if not queried_at:
            return True
        try:
            queried_dt = datetime.fromisoformat(queried_at)
            stale_hours = self._get_stale_hours_for_expiry(expiry_days, mid_cd)
            cutoff = datetime.now() - timedelta(hours=stale_hours)
            return queried_dt < cutoff
        except (ValueError, TypeError):
            return True

    def get_all(self, available_only: bool = False, exclude_cut: bool = False, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        모든 실시간 재고/미입고 조회

        Args:
            available_only: True면 점포 취급 상품만 조회
            exclude_cut: True면 발주중지(CUT) 상품 제외
            store_id: 매장 코드

        Returns:
            상품 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cut_filter = " AND is_cut_item = 0" if exclude_cut else ""

            if available_only:
                cursor.execute(
                    f"SELECT * FROM realtime_inventory WHERE is_available = 1{cut_filter} {sf} ORDER BY item_cd",
                    sp
                )
            else:
                cursor.execute(
                    f"SELECT * FROM realtime_inventory WHERE 1=1{cut_filter} {sf} ORDER BY item_cd",
                    sp
                )

            rows = cursor.fetchall()

            result = []
            for row in rows:
                item = dict(row)
                item["is_available"] = bool(item.get("is_available", 1))

                # 음수 재고/미입고 방어 (조회 시점에서도 0으로 변환)
                item["stock_qty"] = self._to_positive_int(item.get("stock_qty", 0))
                item["pending_qty"] = self._to_positive_int(item.get("pending_qty", 0))

                result.append(item)
            return result
        finally:
            conn.close()

    def get_unavailable_items(self, store_id: Optional[str] = None) -> List[str]:
        """
        점포 미취급 상품 코드 목록

        Args:
            store_id: 매장 코드

        Returns:
            미취급 상품코드 리스트
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"SELECT item_cd FROM realtime_inventory WHERE is_available = 0 {sf}",
                sp
            )

            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_cut_items(self, store_id: Optional[str] = None) -> List[str]:
        """
        발주중지(CUT) 상품 코드 목록

        Args:
            store_id: 매장 코드

        Returns:
            발주중지 상품코드 리스트
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"SELECT item_cd FROM realtime_inventory WHERE is_cut_item = 1 {sf}",
                sp
            )

            rows = cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def get_cut_items_detail(self, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        발주중지(CUT) 상품 상세 목록 (코드 + 이름)

        Args:
            store_id: 매장 코드

        Returns:
            [{'item_cd': ..., 'item_nm': ...}, ...]
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)
            cursor.execute(
                f"SELECT item_cd, item_nm FROM realtime_inventory WHERE is_cut_item = 1 {sf}",
                sp
            )
            rows = cursor.fetchall()
            return [{"item_cd": row[0], "item_nm": row[1] or ""} for row in rows]
        finally:
            conn.close()

    def get_stale_suspicious_items(
        self,
        stale_days: int = 3,
        max_items: int = 20,
        store_id: Optional[str] = None
    ) -> List[str]:
        """
        CUT 미확인 의심 상품 코드 목록 (재고0 + 조회 오래됨)

        is_cut_item=0이지만 queried_at이 stale_days 이상 경과하고
        stock_qty=0인 상품. CUT 전환 가능성이 높아 우선 재조회 대상.

        Args:
            stale_days: 조회 이후 경과일 기준
            max_items: 최대 반환 수
            store_id: 매장 코드

        Returns:
            우선 재조회 대상 상품코드 리스트 (오래된 순)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""SELECT item_cd FROM realtime_inventory
                WHERE is_cut_item = 0 AND is_available = 1
                AND stock_qty = 0
                AND queried_at < datetime('now', '-' || ? || ' days')
                {sf}
                ORDER BY queried_at ASC
                LIMIT ?""",
                (stale_days,) + sp + (max_items,)
            )

            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_stale_item_codes(
        self,
        stale_days: int = 3,
        store_id: Optional[str] = None
    ) -> set:
        """
        queried_at이 stale_days 이상 경과한 상품코드 set.
        CUT 상태를 신뢰할 수 없는 상품 목록.

        Args:
            stale_days: 조회 이후 경과일 기준
            store_id: 매장 코드

        Returns:
            stale 상품코드 set
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(
                f"""SELECT item_cd FROM realtime_inventory
                WHERE is_cut_item = 0 AND is_available = 1
                AND queried_at < datetime('now', '-' || ? || ' days')
                {sf}""",
                (stale_days,) + sp
            )

            return {row[0] for row in cursor.fetchall()}
        finally:
            conn.close()

    def cleanup_stale_stock(
        self,
        stale_hours: int = 36,
        store_id: Optional[str] = None
    ) -> Dict[str, int]:
        """
        유통기한 기반 TTL로 유령 재고를 0으로 초기화.

        상품별 유통기한(product_details.expiration_days)에 따라
        서로 다른 TTL을 적용하여 정리:
        - 1일(도시락/김밥/주먹밥): 18시간
        - 2일(샌드위치): 36시간
        - 3일+(햄버거/빵 등): 54시간 또는 기본 36시간

        Args:
            stale_hours: 기본 유효기간 (폴백용, 기본 36시간)
            store_id: 매장 코드

        Returns:
            {"cleaned": N, "total_ghost_stock": N}
        """
        conn = self._get_conn_with_common() if self.store_id else self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter("ri", store_id)
            prefix = "common." if self.store_id else ""

            # 재고 또는 미입고가 있는 모든 상품을 유통기한 정보와 함께 조회
            cursor.execute(
                f"""SELECT ri.item_cd, ri.stock_qty, ri.queried_at,
                       pd.expiration_days, p.mid_cd, ri.pending_qty
                FROM realtime_inventory ri
                LEFT JOIN {prefix}product_details pd ON ri.item_cd = pd.item_cd
                LEFT JOIN {prefix}products p ON ri.item_cd = p.item_cd
                WHERE (ri.stock_qty > 0 OR ri.pending_qty > 0)
                {sf}""",
                sp
            )
            candidates = cursor.fetchall()

            if not candidates:
                return {"cleaned": 0, "total_ghost_stock": 0, "total_ghost_pending": 0}

            # 상품별 TTL 판정
            now = datetime.now()
            stale_item_cds = []
            total_ghost = 0
            total_ghost_pending = 0

            for row in candidates:
                item_cd = row[0]
                stock_qty = row[1]
                queried_at = row[2]
                expiry_days = row[3]
                mid_cd = row[4]
                pending_qty = row[5] or 0

                item_stale_hours = self._get_stale_hours_for_expiry(expiry_days, mid_cd)

                try:
                    queried_dt = datetime.fromisoformat(queried_at)
                    cutoff = now - timedelta(hours=item_stale_hours)
                    if queried_dt < cutoff:
                        stale_item_cds.append(item_cd)
                        total_ghost += stock_qty
                        total_ghost_pending += pending_qty
                except (ValueError, TypeError):
                    stale_item_cds.append(item_cd)
                    total_ghost += stock_qty
                    total_ghost_pending += pending_qty

            if not stale_item_cds:
                return {"cleaned": 0, "total_ghost_stock": 0, "total_ghost_pending": 0}

            # 일괄 UPDATE (청크 처리) — stock_qty와 pending_qty 모두 초기화
            for i in range(0, len(stale_item_cds), 500):
                chunk = stale_item_cds[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                store_params = [store_id] if store_id else []
                store_cond = "AND store_id = ?" if store_id else ""
                cursor.execute(
                    f"""UPDATE realtime_inventory
                    SET stock_qty = 0, pending_qty = 0
                    WHERE item_cd IN ({placeholders})
                    {store_cond}""",
                    chunk + store_params
                )

            # cross-store 오염 정리: 해당 매장 DB에 다른 store_id 레코드 제거
            cross_store_cleaned = 0
            if store_id:
                cursor.execute(
                    "DELETE FROM realtime_inventory WHERE store_id != ?",
                    (store_id,)
                )
                cross_store_cleaned = cursor.rowcount

            conn.commit()

            log_parts = [
                f"유령 재고 정리: {len(stale_item_cds)}개 상품, "
                f"stock {total_ghost}개 + pending {total_ghost_pending}개 -> 0 초기화 "
                f"(상품별 유통기한 TTL 적용)"
            ]
            if cross_store_cleaned > 0:
                log_parts.append(
                    f", cross-store 오염 {cross_store_cleaned}건 삭제"
                )
            logger.info("".join(log_parts))

            return {
                "cleaned": len(stale_item_cds),
                "total_ghost_stock": total_ghost,
                "total_ghost_pending": total_ghost_pending,
                "cross_store_cleaned": cross_store_cleaned,
            }

        finally:
            conn.close()

    def decrement_stock(
        self,
        item_cd: str,
        qty: int,
        store_id: str = DEFAULT_STORE_ID,
    ) -> bool:
        """폐기/판매에 의한 재고 차감

        stock_qty = MAX(0, stock_qty - qty)

        Args:
            item_cd: 상품코드
            qty: 차감 수량
            store_id: 점포 코드

        Returns:
            True if 차감 성공 (레코드 존재), False if 레코드 없음
        """
        if qty <= 0:
            return False

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE realtime_inventory
                SET stock_qty = MAX(0, stock_qty - ?)
                WHERE store_id = ? AND item_cd = ? AND stock_qty > 0""",
                (qty, store_id, item_cd)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def mark_cut_item(self, item_cd: str, item_nm: str = '', store_id: str = DEFAULT_STORE_ID) -> None:
        """
        상품을 발주중지(CUT)로 표시 (수동 등록용)

        BGF 시스템의 CUT_ITEM_YN 자동 감지가 안 되는 경우,
        수동으로 CUT 상품을 등록할 때 사용.

        Args:
            item_cd: 상품코드
            item_nm: 상품명 (선택)
            store_id: 점포 코드
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            cursor.execute(
                """
                INSERT INTO realtime_inventory
                (store_id, item_cd, item_nm, is_cut_item, is_available, queried_at, created_at)
                VALUES (?, ?, ?, 1, 1, ?, ?)
                ON CONFLICT(store_id, item_cd) DO UPDATE SET
                    is_cut_item = 1,
                    item_nm = COALESCE(NULLIF(excluded.item_nm, ''), realtime_inventory.item_nm),
                    queried_at = excluded.queried_at
                """,
                (store_id, item_cd, item_nm, now, now)
            )

            conn.commit()
        finally:
            conn.close()

    def mark_unavailable(self, item_cd: str, store_id: str = DEFAULT_STORE_ID) -> None:
        """
        상품을 미취급으로 표시

        Args:
            item_cd: 상품코드
            store_id: 점포 코드
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            cursor.execute(
                """
                INSERT INTO realtime_inventory
                (store_id, item_cd, is_available, queried_at, created_at)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(store_id, item_cd) DO UPDATE SET
                    is_available = 0,
                    queried_at = excluded.queried_at
                """,
                (store_id, item_cd, now, now)
            )

            conn.commit()
        finally:
            conn.close()

    def get_stock_qty(self, item_cd: str) -> Optional[int]:
        """
        상품의 현재 재고 조회

        Args:
            item_cd: 상품코드

        Returns:
            현재 재고 또는 None
        """
        data = self.get(item_cd)
        return data.get("stock_qty") if data else None

    def get_pending_qty(self, item_cd: str) -> Optional[int]:
        """
        상품의 미입고 수량 조회

        Args:
            item_cd: 상품코드

        Returns:
            미입고 수량 또는 None
        """
        data = self.get(item_cd)
        return data.get("pending_qty") if data else None

    def get_summary(self, store_id: Optional[str] = None) -> Dict[str, Any]:
        """
        실시간 재고/미입고 요약

        Args:
            store_id: 매장 코드

        Returns:
            {"total": N, "available": N, "unavailable": N, "with_pending": N}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            cursor.execute(f"SELECT COUNT(*) FROM realtime_inventory WHERE 1=1 {sf}", sp)
            total = cursor.fetchone()[0]

            cursor.execute(f"SELECT COUNT(*) FROM realtime_inventory WHERE is_available = 1 {sf}", sp)
            available = cursor.fetchone()[0]

            cursor.execute(f"SELECT COUNT(*) FROM realtime_inventory WHERE is_available = 0 {sf}", sp)
            unavailable = cursor.fetchone()[0]

            cursor.execute(f"SELECT COUNT(*) FROM realtime_inventory WHERE pending_qty > 0 {sf}", sp)
            with_pending = cursor.fetchone()[0]

            return {
                "total": total,
                "available": available,
                "unavailable": unavailable,
                "with_pending": with_pending
            }
        finally:
            conn.close()

    def sync_stock_from_batches(self, store_id: Optional[str] = None) -> Dict[str, int]:
        """inventory_batches 활성 잔량 기반으로 realtime_inventory.stock_qty 보정

        daily_sales가 수집되지 않는 날에도 만료된 배치를 반영하여
        realtime_inventory의 재고를 실제에 가깝게 보정한다.

        로직:
        - 활성 배치(active)의 remaining_qty 합 < 현재 stock_qty이면
          stock_qty를 배치 잔량 합으로 하향 보정
        - 배치가 전혀 없는 상품은 건드리지 않음 (비추적 상품)

        Args:
            store_id: 매장 코드 (None이면 전체)

        Returns:
            {"checked": 확인 건수, "corrected": 보정 건수, "total_reduced": 총 차감량}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            sf, sp = self._store_filter("ri", store_id)

            # 배치가 있는 상품의 활성 배치 잔량 합계 vs realtime_inventory 재고 비교
            cursor.execute(
                f"""
                SELECT
                    ri.store_id,
                    ri.item_cd,
                    ri.item_nm,
                    ri.stock_qty as ri_stock,
                    COALESCE(batch_sum.active_total, 0) as batch_active_total
                FROM realtime_inventory ri
                LEFT JOIN (
                    SELECT store_id, item_cd, SUM(remaining_qty) as active_total
                    FROM inventory_batches
                    WHERE status = 'active'
                    GROUP BY store_id, item_cd
                ) batch_sum ON batch_sum.store_id = ri.store_id AND batch_sum.item_cd = ri.item_cd
                WHERE ri.stock_qty > 0
                {sf}
                AND batch_sum.active_total IS NOT NULL
                AND ri.stock_qty > COALESCE(batch_sum.active_total, 0)
                """,
                sp
            )
            rows = cursor.fetchall()

            stats = {"checked": len(rows), "corrected": 0, "total_reduced": 0}

            for row in rows:
                s_id = row['store_id']
                item_cd = row['item_cd']
                ri_stock = row['ri_stock']
                batch_total = row['batch_active_total']
                reduction = ri_stock - batch_total

                cursor.execute(
                    """
                    UPDATE realtime_inventory
                    SET stock_qty = ?, queried_at = ?
                    WHERE store_id = ? AND item_cd = ?
                    """,
                    (max(0, batch_total), now, s_id, item_cd)
                )
                stats["corrected"] += 1
                stats["total_reduced"] += reduction
                logger.debug(
                    f"재고 보정: {row['item_nm'] or item_cd} "
                    f"stock {ri_stock} -> {batch_total} (-{reduction})"
                )

            if stats["corrected"] > 0:
                conn.commit()
                logger.info(
                    f"realtime_inventory 배치 기반 보정: "
                    f"{stats['corrected']}건, 총 -{stats['total_reduced']}개"
                )

            return stats
        finally:
            conn.close()
