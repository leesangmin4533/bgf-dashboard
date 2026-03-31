"""
InventoryBatchRepository -- 재고 배치 추적 (FIFO 폐기 관리)

기존 src/db/repository.py의 InventoryBatchRepository를 1:1 추출.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger
from src.settings.constants import (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_CONSUMED,
    BATCH_STATUS_EXPIRED,
)

logger = get_logger(__name__)


class InventoryBatchRepository(BaseRepository):
    """재고 배치 추적 저장소 (FIFO 폐기 관리)

    비-푸드 상품의 입고 배치별 유통기한 추적.
    판매 시 FIFO(선입선출)로 차감, 유통기한 도래 시 잔여수량 = 폐기.
    """

    db_type = "store"  # inventory_batches is store-scoped

    def create_batch(
        self,
        item_cd: str,
        item_nm: str,
        mid_cd: str,
        receiving_date: str,
        expiration_days: int,
        initial_qty: int,
        receiving_id: Optional[int] = None,
        store_id: Optional[str] = None,
        delivery_type: Optional[str] = None,
        expiry_datetime: Optional[str] = None,
    ) -> int:
        """입고 시 새 배치 생성

        Args:
            item_cd: 상품코드
            item_nm: 상품명
            mid_cd: 중분류 코드
            receiving_date: 입고일 (YYYY-MM-DD)
            expiration_days: 유통기한 (일)
            initial_qty: 입고 수량
            receiving_id: receiving_history FK
            store_id: 매장 코드 (None이면 기본값 사용)
            delivery_type: 배송차수 ('1차'/'2차'/None)
            expiry_datetime: 정밀 폐기시간 ('YYYY-MM-DD HH:MM:SS', None이면 date 기반)

        Returns:
            생성된 배치 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            # 폐기 예정일 계산: expiry_datetime 우선, 없으면 기존 date 기반
            if expiry_datetime:
                expiry_date = expiry_datetime
            else:
                cursor.execute(
                    "SELECT date(?, '+' || ? || ' days')",
                    (receiving_date, expiration_days)
                )
                expiry_date = cursor.fetchone()[0]

            if store_id:
                cursor.execute(
                    """
                    INSERT INTO inventory_batches
                    (store_id, item_cd, item_nm, mid_cd, receiving_date, receiving_id,
                     expiration_days, expiry_date, initial_qty, remaining_qty,
                     status, created_at, updated_at, delivery_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (store_id, item_cd, item_nm, mid_cd, receiving_date, receiving_id,
                     expiration_days, expiry_date, initial_qty, initial_qty,
                     BATCH_STATUS_ACTIVE, now, now, delivery_type)
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO inventory_batches
                    (item_cd, item_nm, mid_cd, receiving_date, receiving_id,
                     expiration_days, expiry_date, initial_qty, remaining_qty,
                     status, created_at, updated_at, delivery_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item_cd, item_nm, mid_cd, receiving_date, receiving_id,
                     expiration_days, expiry_date, initial_qty, initial_qty,
                     BATCH_STATUS_ACTIVE, now, now, delivery_type)
                )

            batch_id = cursor.lastrowid
            conn.commit()
            logger.info(f"배치 생성: {item_nm}({item_cd}) "
                        f"입고={receiving_date} 수량={initial_qty} "
                        f"폐기예정={expiry_date} 차수={delivery_type}")
            return batch_id
        finally:
            conn.close()

    def get_batch_by_item_and_date(
        self, item_cd: str, receiving_date: str, store_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """동일 상품+입고일 배치 존재 여부 확인 (중복 방지용)

        모든 status(active/consumed/expired)를 검사하여
        이미 소비/만료된 배치가 있어도 중복 생성을 방지한다.

        Args:
            item_cd: 상품코드
            receiving_date: 입고일 (YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            배치 딕셔너리 (없으면 None)
        """
        conn = self._get_conn()
        try:
            sf, sp = self._store_filter(None, store_id)
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM inventory_batches
                WHERE item_cd = ? AND receiving_date = ?
                {sf}
                LIMIT 1
                """,
                (item_cd, receiving_date) + sp
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def sync_with_stock(self, item_cd: str, current_stock: int, store_id: Optional[str] = None) -> int:
        """현재 재고와 배치 잔량 동기화 (FIFO 차감) + realtime_inventory 갱신

        배치 잔량 합계 > 현재 재고이면, 차이만큼 FIFO로 차감.
        멱등성 보장: 여러 번 호출해도 같은 결과.
        동기화 시 realtime_inventory.stock_qty도 함께 갱신.

        Args:
            item_cd: 상품코드
            current_stock: 현재 실제 재고
            store_id: 매장 코드

        Returns:
            차감된 수량 (0이면 차감 불필요)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sf, sp = self._store_filter(None, store_id)

            # 활성 배치 잔량 합계
            cursor.execute(
                f"""
                SELECT COALESCE(SUM(remaining_qty), 0) as batch_total
                FROM inventory_batches
                WHERE item_cd = ? AND status = ? {sf}
                """,
                (item_cd, BATCH_STATUS_ACTIVE) + sp
            )
            batch_total = cursor.fetchone()['batch_total']

            if batch_total <= current_stock:
                if batch_total > 0 and current_stock > batch_total:
                    logger.warning(
                        f"미추적 재고 감지: {item_cd} "
                        f"(실재고={current_stock}, 배치합계={batch_total}, "
                        f"차이={current_stock - batch_total})"
                    )
                return 0

            to_consume = batch_total - current_stock
            consumed = self._consume_fifo_internal(cursor, item_cd, to_consume, store_id=store_id)

            # realtime_inventory.stock_qty도 current_stock으로 동기화
            if store_id and consumed > 0:
                now = self._now()
                cursor.execute(
                    """
                    UPDATE realtime_inventory
                    SET stock_qty = ?, queried_at = ?
                    WHERE store_id = ? AND item_cd = ?
                    """,
                    (max(0, current_stock), now, store_id, item_cd)
                )

            conn.commit()
            return consumed
        finally:
            conn.close()

    def _consume_fifo_internal(self, cursor, item_cd: str, qty: int, store_id: Optional[str] = None) -> int:
        """FIFO로 배치 차감 (내부용, 커밋하지 않음)

        Args:
            cursor: DB 커서
            item_cd: 상품코드
            qty: 차감할 수량
            store_id: 매장 코드

        Returns:
            실제 차감된 수량
        """
        now = self._now()
        remaining_to_consume = qty
        sf, sp = self._store_filter(None, store_id)

        # 오래된 배치부터 차감 (FIFO)
        cursor.execute(
            f"""
            SELECT id, remaining_qty
            FROM inventory_batches
            WHERE item_cd = ? AND status = ? AND remaining_qty > 0 {sf}
            ORDER BY receiving_date ASC, id ASC
            """,
            (item_cd, BATCH_STATUS_ACTIVE) + sp
        )

        for row in cursor.fetchall():
            if remaining_to_consume <= 0:
                break

            batch_id = row['id']
            batch_remaining = row['remaining_qty']
            deduct = min(batch_remaining, remaining_to_consume)
            new_remaining = batch_remaining - deduct

            # 전량 소진 시 상태 변경
            new_status = BATCH_STATUS_CONSUMED if new_remaining == 0 else BATCH_STATUS_ACTIVE

            cursor.execute(
                """
                UPDATE inventory_batches
                SET remaining_qty = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_remaining, new_status, now, batch_id)
            )

            remaining_to_consume -= deduct

        return qty - remaining_to_consume

    def check_and_expire_batches(self, target_date: Optional[str] = None, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """만료된 배치 상태 업데이트 + realtime_inventory 재고 차감

        expiry_date 도달 + remaining_qty > 0 -> status = BATCH_STATUS_EXPIRED
        만료 전 FIFO 소비 차감을 수행하여 remaining_qty가 실제 폐기량만 반영하도록 보정.
        만료된 수량만큼 realtime_inventory.stock_qty도 차감하여 실제 재고 반영.

        Args:
            target_date: 기준 날짜 (기본: 오늘)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            폐기 확정된 배치 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            if target_date is None:
                target_date = datetime.now().strftime("%Y-%m-%d")

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # 만료 대상 조회
            cursor.execute(
                f"""
                SELECT id, store_id, item_cd, item_nm, mid_cd, receiving_date,
                       expiry_date, initial_qty, remaining_qty
                FROM inventory_batches
                WHERE status = ?
                AND expiry_date <= ?
                AND remaining_qty > 0
                {store_filter}
                ORDER BY expiry_date ASC
                """,
                (BATCH_STATUS_ACTIVE, target_date) + store_params
            )
            expired_batches = [dict(row) for row in cursor.fetchall()]

            if expired_batches:
                # ── FIFO 소비 차감: 만료 전 remaining_qty를 실제 재고 기준으로 보정 ──
                # 만료되는 배치의 remaining_qty가 initial_qty 그대로인 경우가 많음 (FIFO sync 누락).
                # daily_sales stock_qty 기준으로 해당 상품의 전체 active 배치 합계와 비교,
                # 초과분을 FIFO(오래된 배치=만료 배치부터) 차감하여 실제 폐기량만 남김.
                self._deduct_consumption_before_expiry(
                    cursor, expired_batches, store_filter, store_params, now
                )

                # remaining_qty=0이 된 배치는 consumed 처리 (폐기 아닌 소비 완료)
                consumed_batches = [b for b in expired_batches if b['remaining_qty'] <= 0]
                actual_expired = [b for b in expired_batches if b['remaining_qty'] > 0]

                # consumed 배치: status=consumed
                if consumed_batches:
                    consumed_ids = [b['id'] for b in consumed_batches]
                    placeholders = ','.join('?' * len(consumed_ids))
                    cursor.execute(
                        f"""
                        UPDATE inventory_batches
                        SET status = ?, remaining_qty = 0, updated_at = ?
                        WHERE id IN ({placeholders})
                        """,
                        [BATCH_STATUS_CONSUMED, now] + consumed_ids
                    )

                # expired 배치: status=expired (remaining_qty > 0 = 실제 폐기량)
                if actual_expired:
                    for b in actual_expired:
                        cursor.execute(
                            """
                            UPDATE inventory_batches
                            SET status = ?, remaining_qty = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (BATCH_STATUS_EXPIRED, b['remaining_qty'], now, b['id'])
                        )

                # 상품별 만료 수량 합산 → realtime_inventory.stock_qty 차감
                # (consumed 배치는 이미 판매된 것이므로 RI 차감 불필요, expired만 차감)
                expired_by_item = {}  # {(store_id, item_cd): total_expired_qty}
                for b in actual_expired:
                    key = (b.get('store_id', ''), b['item_cd'])
                    expired_by_item[key] = expired_by_item.get(key, 0) + b['remaining_qty']

                for (batch_store_id, item_cd), expired_qty in expired_by_item.items():
                    cursor.execute(
                        """
                        UPDATE realtime_inventory
                        SET stock_qty = MAX(0, stock_qty - ?),
                            queried_at = ?
                        WHERE store_id = ? AND item_cd = ?
                        """,
                        (expired_qty, now, batch_store_id, item_cd)
                    )
                    if cursor.rowcount > 0:
                        logger.debug(
                            f"realtime_inventory 재고 차감: {item_cd} "
                            f"-{expired_qty}개 (만료, store={batch_store_id})"
                        )

                conn.commit()

                total_waste = sum(b['remaining_qty'] for b in actual_expired)
                total_consumed = len(consumed_batches)
                logger.info(
                    f"폐기 확정: {len(actual_expired)}건 ({total_waste}개), "
                    f"소비 완료: {total_consumed}건 ({target_date})"
                )

            return expired_batches
        finally:
            conn.close()

    def _deduct_consumption_before_expiry(
        self,
        cursor: sqlite3.Cursor,
        expiring_batches: List[Dict[str, Any]],
        store_filter: str,
        store_params: tuple,
        now: str,
    ) -> None:
        """만료 직전 FIFO 소비 차감 -- remaining_qty를 실제 폐기량으로 보정

        상품별로:
        1. 전체 active 배치(만료 대상 포함) remaining_qty 합계
        2. daily_sales 최신 stock_qty 조회
        3. batch_total > stock_qty → 초과분을 FIFO 차감 (oldest first = 만료 배치 우선)
        4. expiring_batches dict를 in-place 수정 (remaining_qty 갱신)
        """
        # 만료 대상 상품별 그룹화
        items_by_store = {}  # {(store_id, item_cd): [batch_dicts]}
        for b in expiring_batches:
            key = (b.get('store_id', ''), b['item_cd'])
            items_by_store.setdefault(key, []).append(b)

        for (batch_store_id, item_cd), batches in items_by_store.items():
            # 해당 상품의 전체 active 배치 합계 (만료 대상 포함)
            cursor.execute(
                """
                SELECT COALESCE(SUM(remaining_qty), 0)
                FROM inventory_batches
                WHERE item_cd = ? AND store_id = ? AND status = ?
                  AND remaining_qty > 0
                """,
                (item_cd, batch_store_id, BATCH_STATUS_ACTIVE),
            )
            batch_total = int(cursor.fetchone()[0] or 0)

            # daily_sales 최신 stock_qty
            cursor.execute(
                """
                SELECT ds.stock_qty
                FROM daily_sales ds
                INNER JOIN (
                    SELECT MAX(sales_date) as max_date
                    FROM daily_sales
                    WHERE store_id = ? AND item_cd = ?
                ) latest ON ds.sales_date = latest.max_date
                WHERE ds.store_id = ? AND ds.item_cd = ?
                """,
                (batch_store_id, item_cd, batch_store_id, item_cd),
            )
            row = cursor.fetchone()
            stock_qty = int(row[0] or 0) if row else 0

            if batch_total <= stock_qty:
                continue  # 배치 합계 <= 재고 → 소비 차감 불필요

            # 초과분 = 소비된 수량 → FIFO 차감 (oldest first)
            to_consume = batch_total - stock_qty

            # 해당 상품의 전체 active 배치를 oldest-first로 조회
            cursor.execute(
                """
                SELECT id, remaining_qty
                FROM inventory_batches
                WHERE item_cd = ? AND store_id = ? AND status = ?
                  AND remaining_qty > 0
                ORDER BY receiving_date ASC, id ASC
                """,
                (item_cd, batch_store_id, BATCH_STATUS_ACTIVE),
            )
            all_active = [dict(r) for r in cursor.fetchall()]

            # 만료 배치 id → dict 매핑 (in-place 수정용)
            expiring_id_map = {b['id']: b for b in batches}

            remain = to_consume
            for ab in all_active:
                if remain <= 0:
                    break
                b_remaining = int(ab['remaining_qty'] or 0)
                deduct = min(b_remaining, remain)
                new_remaining = b_remaining - deduct
                remain -= deduct

                # 만료 대상 배치면 in-place 수정
                if ab['id'] in expiring_id_map:
                    expiring_id_map[ab['id']]['remaining_qty'] = new_remaining
                else:
                    # 비만료 active 배치도 DB 업데이트 (FIFO 정합성)
                    new_status = (
                        BATCH_STATUS_CONSUMED if new_remaining == 0
                        else BATCH_STATUS_ACTIVE
                    )
                    cursor.execute(
                        """
                        UPDATE inventory_batches
                        SET remaining_qty = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_remaining, new_status, now, ab['id']),
                    )

    def fix_expired_batch_remaining_qty(self, store_id: str, days: int = 28) -> Dict[str, Any]:
        """기존 expired 배치의 remaining_qty를 소비량 기준으로 보정 (일회성 데이터 수정)

        버그로 인해 remaining_qty=initial_qty인 expired 배치가 존재.
        daily_sales 판매 데이터와 대조하여 실제 폐기량으로 remaining_qty를 보정.

        로직: 만료일 전후 3일간 해당 상품 판매량(sale_qty)을 구해,
        같은 기간 입고된 배치의 initial_qty에서 판매량을 FIFO 차감.

        Args:
            store_id: 매장 코드
            days: 보정 대상 기간 (최근 N일, 기본 28일)

        Returns:
            {"total": 대상 배치수, "fixed": 보정된 배치수, "consumed": 소비완료 전환수}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            # remaining_qty = initial_qty인 expired 배치 (= 소비 차감 누락된 배치)
            cursor.execute(
                """
                SELECT id, item_cd, item_nm, mid_cd, receiving_date,
                       expiry_date, initial_qty, remaining_qty
                FROM inventory_batches
                WHERE store_id = ? AND status = ?
                  AND expiry_date >= ?
                  AND remaining_qty = initial_qty
                  AND initial_qty > 0
                ORDER BY item_cd, receiving_date ASC
                """,
                (store_id, BATCH_STATUS_EXPIRED, cutoff),
            )
            corrupted = [dict(row) for row in cursor.fetchall()]

            if not corrupted:
                logger.info(f"[{store_id}] expired 배치 remaining_qty 보정 대상 없음")
                return {"total": 0, "fixed": 0, "consumed": 0}

            # 상품별 그룹화
            by_item = {}
            for b in corrupted:
                by_item.setdefault(b['item_cd'], []).append(b)

            fixed = 0
            consumed = 0

            for item_cd, batches in by_item.items():
                # 해당 기간 전체 판매량 조회
                min_recv = min(b['receiving_date'] for b in batches)
                max_expiry = max(b['expiry_date'] for b in batches)

                cursor.execute(
                    """
                    SELECT COALESCE(SUM(sale_qty), 0) as total_sales
                    FROM daily_sales
                    WHERE store_id = ? AND item_cd = ?
                      AND sales_date BETWEEN ? AND ?
                    """,
                    (store_id, item_cd, min_recv, max_expiry),
                )
                total_sales = int(cursor.fetchone()[0] or 0)

                if total_sales <= 0:
                    continue  # 판매 없으면 remaining=initial이 맞음

                # FIFO 차감 (oldest batch부터)
                remain_sales = total_sales
                for b in sorted(batches, key=lambda x: (x['receiving_date'], x['id'])):
                    if remain_sales <= 0:
                        break
                    deduct = min(b['initial_qty'], remain_sales)
                    new_remaining = b['initial_qty'] - deduct
                    remain_sales -= deduct

                    if new_remaining != b['remaining_qty']:
                        new_status = BATCH_STATUS_CONSUMED if new_remaining == 0 else BATCH_STATUS_EXPIRED
                        cursor.execute(
                            """
                            UPDATE inventory_batches
                            SET remaining_qty = ?, status = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (new_remaining, new_status, now, b['id']),
                        )
                        fixed += 1
                        if new_status == BATCH_STATUS_CONSUMED:
                            consumed += 1

            conn.commit()
            logger.info(
                f"[{store_id}] expired 배치 보정 완료: "
                f"대상 {len(corrupted)}건, 보정 {fixed}건, consumed 전환 {consumed}건"
            )
            return {"total": len(corrupted), "fixed": fixed, "consumed": consumed}
        except Exception as e:
            logger.error(f"[{store_id}] expired 배치 보정 실패: {e}")
            return {"total": 0, "fixed": 0, "consumed": 0}
        finally:
            conn.close()

    # ── 정밀 폐기 확정 (3단계: 수집→판정→수집+확정) ──

    def judge_expiry_batches(
        self, target_date: Optional[str] = None, store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """[폐기 판정] 만료 배치 조회 + 현재 stock 스냅샷 저장

        폐기 시간 정시에 실행. 만료 대상 배치를 조회하고
        각 상품의 현재 realtime_inventory.stock_qty를 스냅샷으로 기록.
        아직 status 변경하지 않음 (확정 단계에서 처리).

        Args:
            target_date: 기준 날짜 (기본: 오늘)
            store_id: 매장 코드

        Returns:
            판정 대상 배치 목록 (stock_snapshot 포함)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            if target_date is None:
                target_date = datetime.now().strftime("%Y-%m-%d")

            store_filter = "AND ib.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # 만료 대상 배치 + 현재 재고 스냅샷
            cursor.execute(
                f"""
                SELECT ib.id, ib.store_id, ib.item_cd, ib.item_nm, ib.mid_cd,
                       ib.receiving_date, ib.expiry_date,
                       ib.initial_qty, ib.remaining_qty,
                       COALESCE(ri.stock_qty, 0) as stock_at_judge
                FROM inventory_batches ib
                LEFT JOIN realtime_inventory ri
                    ON ib.item_cd = ri.item_cd AND ib.store_id = ri.store_id
                WHERE ib.status = ?
                AND ib.expiry_date <= ?
                AND ib.remaining_qty > 0
                {store_filter}
                ORDER BY ib.expiry_date ASC
                """,
                (BATCH_STATUS_ACTIVE, target_date) + store_params
            )
            judged = [dict(row) for row in cursor.fetchall()]

            if judged:
                logger.info(
                    f"[폐기판정] {len(judged)}건 대상, "
                    f"총 {sum(b['remaining_qty'] for b in judged)}개 "
                    f"(store={store_id}, date={target_date})"
                )
                for b in judged:
                    logger.debug(
                        f"  판정: {b['item_cd']} {b['item_nm']} "
                        f"잔여={b['remaining_qty']} 재고={b['stock_at_judge']}"
                    )

            return judged
        finally:
            conn.close()

    def confirm_expiry_batches(
        self,
        judged_batches: List[Dict[str, Any]],
        store_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """[폐기 확정] 판정 목록 vs 현재 stock 비교 → 실제 폐기량 결정

        10분 후 수집 완료 후 실행. 판정 시점 stock과 현재 stock을 비교하여
        그 사이 판매된 수량만큼 remaining_qty에서 추가 차감 후 폐기 확정.

        로직:
        1. 상품별로 판정 시 stock - 현재 stock = 10분간 판매량
        2. 판매량만큼 remaining_qty에서 FIFO 차감
        3. 최종 remaining_qty > 0인 배치만 expired 처리
        4. remaining_qty == 0이면 consumed 처리 (전량 판매됨)

        Args:
            judged_batches: judge_expiry_batches()의 반환값
            store_id: 매장 코드

        Returns:
            폐기 확정된 배치 목록 (adjusted_qty 포함)
        """
        if not judged_batches:
            return []

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            # 상품별 판정 시 stock 합산
            item_judge_stock = {}  # {item_cd: stock_at_judge}
            item_batches = {}     # {item_cd: [batch_list]}
            for b in judged_batches:
                item_cd = b['item_cd']
                item_judge_stock[item_cd] = b['stock_at_judge']
                item_batches.setdefault(item_cd, []).append(b)

            confirmed = []
            total_saved = 0  # 판매 보정으로 구출된 총 수량

            for item_cd, batches in item_batches.items():
                batch_store_id = batches[0].get('store_id', store_id or '')

                # 현재 stock 조회 (10분 후 수집 반영된 값)
                cursor.execute(
                    """
                    SELECT COALESCE(stock_qty, 0) as current_stock
                    FROM realtime_inventory
                    WHERE store_id = ? AND item_cd = ?
                    """,
                    (batch_store_id, item_cd)
                )
                row = cursor.fetchone()
                current_stock = row['current_stock'] if row else 0
                judge_stock = item_judge_stock[item_cd]

                # 10분간 판매량 = 판정 시 재고 - 현재 재고
                sold_in_gap = max(0, judge_stock - current_stock)

                if sold_in_gap > 0:
                    total_saved += sold_in_gap
                    logger.info(
                        f"[폐기보정] {item_cd}: 판정stock={judge_stock} → "
                        f"현재stock={current_stock}, 10분간 판매={sold_in_gap}개"
                    )

                # 판매분만큼 FIFO 차감 (오래된 배치부터)
                remaining_sold = sold_in_gap
                for b in sorted(batches, key=lambda x: (x['receiving_date'], x['id'])):
                    if remaining_sold <= 0:
                        break
                    deduct = min(b['remaining_qty'], remaining_sold)
                    b['remaining_qty'] -= deduct
                    remaining_sold -= deduct

                # 배치별 최종 처리
                for b in batches:
                    batch_id = b['id']
                    final_remaining = b['remaining_qty']

                    if final_remaining > 0:
                        # 잔여 있음 → expired
                        cursor.execute(
                            """
                            UPDATE inventory_batches
                            SET remaining_qty = ?, status = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (final_remaining, BATCH_STATUS_EXPIRED, now, batch_id)
                        )
                        b['status'] = BATCH_STATUS_EXPIRED
                        b['adjusted_qty'] = final_remaining
                        confirmed.append(b)
                    else:
                        # 전량 판매됨 → consumed
                        cursor.execute(
                            """
                            UPDATE inventory_batches
                            SET remaining_qty = 0, status = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (BATCH_STATUS_CONSUMED, now, batch_id)
                        )
                        logger.info(
                            f"[폐기→판매전환] {b['item_cd']} {b['item_nm']} "
                            f"배치#{batch_id}: 폐기 대상이었으나 전량 판매 확인"
                        )

                # realtime_inventory 재고 차감 (실제 폐기분만)
                total_expired_qty = sum(
                    b['remaining_qty'] for b in batches if b.get('status') == BATCH_STATUS_EXPIRED
                )
                if total_expired_qty > 0:
                    cursor.execute(
                        """
                        UPDATE realtime_inventory
                        SET stock_qty = MAX(0, stock_qty - ?),
                            queried_at = ?
                        WHERE store_id = ? AND item_cd = ?
                        """,
                        (total_expired_qty, now, batch_store_id, item_cd)
                    )

            conn.commit()

            if confirmed:
                total_waste = sum(b['adjusted_qty'] for b in confirmed)
                logger.info(
                    f"[폐기확정] {len(confirmed)}건, 총 {total_waste}개 폐기 "
                    f"(판매보정으로 {total_saved}개 구출, store={store_id})"
                )
            else:
                logger.info(
                    f"[폐기확정] 폐기 0건 (전량 판매 확인, "
                    f"판매보정 {total_saved}개, store={store_id})"
                )

            return confirmed
        finally:
            conn.close()

    def get_expiring_soon(self, days_ahead: int = 1, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """N일 내 만료 예정 배치 조회 (알림용)

        Args:
            days_ahead: 몇 일 이내 만료 예정
            store_id: 매장 코드 (None이면 전체)

        Returns:
            만료 예정 배치 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND ib.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # 비식품/담배/발주불가/9999일/999일 제외
            cursor.execute(
                f"""
                SELECT ib.*, pd.expiration_days as product_expiry,
                       pd.orderable_status
                FROM inventory_batches ib
                LEFT JOIN product_details pd ON ib.item_cd = pd.item_cd
                WHERE ib.status = ?
                AND ib.remaining_qty > 0
                AND ib.expiry_date <= date('now', '+' || ? || ' days')
                AND ib.expiry_date > date('now')
                AND ib.mid_cd NOT IN (
                    '054','055','056','057','058','059','060','061','062','063',
                    '064','065','066','067','068','069','070','071',
                    '072','073','086','900','100','101','300','605'
                )
                AND COALESCE(ib.expiration_days, 0) NOT IN (9999, 999)
                AND COALESCE(pd.orderable_status, '가능') != '불가'
                {store_filter}
                ORDER BY ib.expiry_date ASC
                """,
                (BATCH_STATUS_ACTIVE, days_ahead) + store_params
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_active_batches(self, item_cd: Optional[str] = None,
                           store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """활성 배치 조회

        Args:
            item_cd: 상품코드 (None이면 전체)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            활성 배치 목록 (입고일 오래된 순)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            if item_cd:
                cursor.execute(
                    f"""
                    SELECT * FROM inventory_batches
                    WHERE item_cd = ? AND status = ?
                    {store_filter}
                    ORDER BY receiving_date ASC, id ASC
                    """,
                    (item_cd, BATCH_STATUS_ACTIVE) + store_params
                )
            else:
                cursor.execute(
                    f"""
                    SELECT * FROM inventory_batches
                    WHERE status = ?
                    {store_filter}
                    ORDER BY item_cd, receiving_date ASC, id ASC
                    """,
                    (BATCH_STATUS_ACTIVE,) + store_params
                )

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_expired_batches(
        self,
        target_date: Optional[str] = None,
        days_back: int = 7,
        store_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """폐기 확정 배치 조회 (리포트용)

        Args:
            target_date: 기준 날짜 (기본: 오늘)
            days_back: 최근 N일간 조회
            store_id: 매장 코드 (None이면 전체)

        Returns:
            폐기 확정 배치 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            if target_date is None:
                target_date = datetime.now().strftime("%Y-%m-%d")

            store_filter = "AND ib.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT ib.*, p.item_nm as product_name
                FROM inventory_batches ib
                LEFT JOIN products p ON ib.item_cd = p.item_cd
                WHERE ib.status = ?
                AND ib.expiry_date >= date(?, '-' || ? || ' days')
                AND ib.expiry_date <= ?
                {store_filter}
                ORDER BY ib.expiry_date DESC, ib.item_cd
                """,
                (BATCH_STATUS_EXPIRED, target_date, days_back, target_date) + store_params
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_today_expiring_item_qty(
        self,
        target_date: Optional[str] = None,
        store_id: Optional[str] = None
    ) -> Dict[str, int]:
        """오늘 만료되는 active 배치의 item_cd별 잔여수량 합계

        Args:
            target_date: 기준 날짜 (기본: 오늘, YYYY-MM-DD)
            store_id: 매장 코드

        Returns:
            {item_cd: remaining_qty_sum} — 만료 배치가 있는 상품만 포함
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if target_date is None:
                target_date = datetime.now().strftime("%Y-%m-%d")

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT item_cd, SUM(remaining_qty) as expiring_qty
                FROM inventory_batches
                WHERE status = ?
                AND remaining_qty > 0
                AND expiry_date <= ?
                {store_filter}
                GROUP BY item_cd
                """,
                (BATCH_STATUS_ACTIVE, target_date) + store_params
            )
            return {row["item_cd"]: row["expiring_qty"] for row in cursor.fetchall()}
        finally:
            conn.close()

    def get_waste_summary(self, days_back: int = 30,
                          store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """폐기 통계 요약 (카테고리별)

        Args:
            days_back: 최근 N일간 집계
            store_id: 매장 코드 (None이면 전체)

        Returns:
            카테고리별 폐기 통계 [{mid_cd, total_items, total_waste_qty, ...}]
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND ib.store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT
                    ib.mid_cd,
                    COUNT(*) as total_items,
                    SUM(ib.remaining_qty) as total_waste_qty,
                    SUM(ib.initial_qty) as total_initial_qty,
                    ROUND(
                        CAST(SUM(ib.remaining_qty) AS REAL) /
                        NULLIF(SUM(ib.initial_qty), 0) * 100, 1
                    ) as waste_rate_pct
                FROM inventory_batches ib
                WHERE ib.status = ?
                AND ib.expiry_date >= date('now', '-' || ? || ' days')
                {store_filter}
                GROUP BY ib.mid_cd
                ORDER BY total_waste_qty DESC
                """,
                (BATCH_STATUS_EXPIRED, days_back) + store_params
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_daily_waste_trend(self, days_back: int = 30,
                              store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """일별 폐기 추이 (트렌드 차트용)

        Args:
            days_back: 최근 N일간 조회
            store_id: 매장 코드 (None이면 전체)

        Returns:
            일별 폐기 수량 [{expiry_date, waste_items, waste_qty}]
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT
                    expiry_date,
                    COUNT(*) as waste_items,
                    SUM(remaining_qty) as waste_qty
                FROM inventory_batches
                WHERE status = ?
                AND expiry_date >= date('now', '-' || ? || ' days')
                {store_filter}
                GROUP BY expiry_date
                ORDER BY expiry_date
                """,
                (BATCH_STATUS_EXPIRED, days_back) + store_params
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ── stock 기반 FIFO 재동기화 ──

    def sync_remaining_with_stock(
        self, store_id: str
    ) -> Dict[str, int]:
        """active 배치의 remaining_qty를 현재 stock_qty 기준으로 재동기화

        판매 데이터 수집 후 호출하여 배치 잔여수량과 실제 재고의 정합성을 맞춤.
        FR-02(save_daily_sales 내 인라인 FIFO)가 누락한 차감분을 일괄 보정.

        로직:
        1. 상품별 active 배치의 remaining_qty 합계 vs daily_sales 최신 stock_qty
        2. batch_total > stock_qty -> FIFO 차감 (오래된 배치부터)
        3. stock_qty == 0 -> 모든 active 배치를 consumed 처리

        Args:
            store_id: 매장 코드

        Returns:
            {"checked": 점검 상품수, "adjusted": 보정 상품수, "consumed": consumed 배치수}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            # 1) 상품별 active 배치 합계 조회
            cursor.execute(
                """
                SELECT item_cd, SUM(remaining_qty) as batch_total
                FROM inventory_batches
                WHERE store_id = ? AND status = ? AND remaining_qty > 0
                GROUP BY item_cd
                """,
                (store_id, BATCH_STATUS_ACTIVE),
            )
            batch_totals = {
                row["item_cd"]: int(row["batch_total"] or 0)
                for row in cursor.fetchall()
            }

            if not batch_totals:
                return {"checked": 0, "adjusted": 0, "consumed": 0}

            # 2) 해당 상품들의 최신 stock_qty 조회 (daily_sales 기준)
            placeholders = ",".join("?" * len(batch_totals))
            cursor.execute(
                f"""
                SELECT ds.item_cd, ds.stock_qty
                FROM daily_sales ds
                INNER JOIN (
                    SELECT item_cd, MAX(sales_date) as max_date
                    FROM daily_sales
                    WHERE store_id = ? AND item_cd IN ({placeholders})
                    GROUP BY item_cd
                ) latest
                  ON ds.item_cd = latest.item_cd
                 AND ds.sales_date = latest.max_date
                WHERE ds.store_id = ?
                """,
                (store_id, *batch_totals.keys(), store_id),
            )
            stock_map = {
                row["item_cd"]: int(row["stock_qty"] or 0)
                for row in cursor.fetchall()
            }

            checked = len(batch_totals)
            adjusted = 0
            consumed_count = 0

            # 3) 상품별 FIFO 보정
            for item_cd, batch_total in batch_totals.items():
                stock_qty = stock_map.get(item_cd, 0)

                if batch_total <= stock_qty:
                    continue  # 정합 -- 보정 불필요

                # batch_total > stock_qty -> 초과분 FIFO 차감
                to_consume = batch_total - stock_qty
                adjusted += 1

                cursor.execute(
                    """
                    SELECT id, remaining_qty
                    FROM inventory_batches
                    WHERE item_cd = ? AND store_id = ? AND status = ?
                      AND remaining_qty > 0
                    ORDER BY receiving_date ASC, id ASC
                    """,
                    (item_cd, store_id, BATCH_STATUS_ACTIVE),
                )
                remain = to_consume
                for batch_row in cursor.fetchall():
                    if remain <= 0:
                        break
                    b_id = batch_row["id"]
                    b_remaining = int(batch_row["remaining_qty"] or 0)
                    deduct = min(b_remaining, remain)
                    new_remaining = b_remaining - deduct
                    new_status = (
                        BATCH_STATUS_CONSUMED
                        if new_remaining == 0
                        else BATCH_STATUS_ACTIVE
                    )
                    cursor.execute(
                        """
                        UPDATE inventory_batches
                        SET remaining_qty = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_remaining, new_status, now, b_id),
                    )
                    remain -= deduct
                    if new_status == BATCH_STATUS_CONSUMED:
                        consumed_count += 1

            conn.commit()

            if adjusted > 0:
                logger.info(
                    f"[BatchSync] {store_id}: "
                    f"점검 {checked}건, 보정 {adjusted}건, "
                    f"consumed {consumed_count}건"
                )

            # 4) 푸드 유령재고 정리: active 배치 없는데 RI > 0인 푸드 상품 → stock_qty = 0
            # [원본 보존] 기존에는 active 배치 없는 상품을 의도적으로 skip했음 (비푸드 보호)
            # 푸드 카테고리(001~005, 012)만 정리, 비푸드는 기존대로 건드리지 않음
            # FIFO 보정 후 batch_totals 재계산 (보정 중 consumed된 배치 반영)
            cursor.execute(
                """
                SELECT item_cd, SUM(remaining_qty) as batch_total
                FROM inventory_batches
                WHERE store_id = ? AND status = ? AND remaining_qty > 0
                GROUP BY item_cd
                """,
                (store_id, BATCH_STATUS_ACTIVE),
            )
            batch_totals_after = {
                row["item_cd"]: int(row["batch_total"] or 0)
                for row in cursor.fetchall()
            }
            ghost_cleared = self._clear_ghost_stock(
                cursor, store_id, batch_totals_after, now
            )

            # ── order_tracking 만료 레코드 remaining_qty 정리 ──
            # [원본 보존] 기존: order_tracking의 expired/disposed 레코드는 remaining_qty를
            # 그대로 유지 → _get_actual_expiry_time() 등에서 만료된 OT를 활성으로 오판
            # 수정: 푸드 카테고리의 expired/disposed OT 레코드 remaining_qty → 0
            FOOD_MID_CDS = ('001', '002', '003', '004', '005', '012', '014')
            ot_cleared = self._clear_expired_order_tracking(
                cursor, store_id, FOOD_MID_CDS, now
            )
            if ot_cleared > 0:
                logger.info(
                    f"[BatchSync] {store_id}: OT 만료 잔량 {ot_cleared}건 정리 "
                    f"(expired/disposed remaining_qty → 0)"
                )

            if ghost_cleared > 0 or ot_cleared > 0:
                conn.commit()

            return {
                "checked": checked,
                "adjusted": adjusted,
                "consumed": consumed_count,
                "ghost_cleared": ghost_cleared,
                "ot_cleared": ot_cleared,
            }
        except Exception as e:
            logger.error(f"[BatchSync] FIFO 재동기화 오류: {e}")
            return {"checked": 0, "adjusted": 0, "consumed": 0, "ghost_cleared": 0}
        finally:
            conn.close()

    def _clear_ghost_stock(
        self, cursor: sqlite3.Cursor, store_id: str,
        batch_totals: Dict[str, int], now: str
    ) -> int:
        """유령재고 정리 (전 카테고리)

        active 배치가 없는데 realtime_inventory.stock_qty > 0인
        상품의 stock_qty를 0으로 리셋.

        # [원본 보존] 기존: 푸드 카테고리(001~005,012,014)만 대상
        # FOOD_MID_CDS = ('001','002','003','004','005','012','014')
        # → 비푸드 expired 배치 잔량이 RI에 유령으로 누적되는 문제로 전 카테고리 확장

        Args:
            cursor: DB 커서 (이미 열린 연결)
            store_id: 매장 코드
            batch_totals: 현재 active 배치가 있는 상품 목록 (제외용)
            now: 현재 시각 문자열

        Returns:
            정리된 유령재고 상품 수
        """
        try:
            # RI에서 stock > 0인 전체 상품
            ri_rows = cursor.execute(
                """
                SELECT item_cd, stock_qty FROM realtime_inventory
                WHERE store_id = ? AND stock_qty > 0
                """,
                (store_id,),
            ).fetchall()

            # active 배치가 있는 상품 제외
            ghost_items = [
                row["item_cd"] for row in ri_rows
                if row["item_cd"] not in batch_totals
            ]

            if not ghost_items:
                return 0

            # 유령재고 stock_qty → 0
            cleared = 0
            for item_cd in ghost_items:
                cursor.execute(
                    """
                    UPDATE realtime_inventory
                    SET stock_qty = 0, queried_at = ?
                    WHERE item_cd = ? AND store_id = ? AND stock_qty > 0
                    """,
                    (now, item_cd, store_id),
                )
                if cursor.rowcount > 0:
                    cleared += 1

            if cleared > 0:
                logger.info(
                    f"[BatchSync] {store_id}: 유령재고 {cleared}건 정리 "
                    f"(active 배치 없는 상품 stock_qty → 0)"
                )

            return cleared

        except Exception as e:
            logger.warning(f"[BatchSync] 유령재고 정리 오류: {e}")
            return 0

    def _clear_expired_order_tracking(
        self, cursor: sqlite3.Cursor, store_id: str,
        food_mid_cds: tuple, now: str
    ) -> int:
        """만료/폐기된 order_tracking 레코드의 remaining_qty를 0으로 정리

        두 가지 케이스를 처리:
        A) status='expired' 또는 'disposed'인데 remaining_qty > 0 — 상태는 맞지만 잔량 미초기화
        B) status='arrived'인데 expiry_time < now AND remaining_qty > 0 — 좀비 OT
           (만료 시각 경과했는데 arrived 유지 → status도 expired로 전환)

        푸드 카테고리만 대상.

        Args:
            cursor: DB 커서 (이미 열린 연결)
            store_id: 매장 코드
            food_mid_cds: 대상 중분류 코드 튜플
            now: 현재 시각 문자열

        Returns:
            정리된 레코드 수
        """
        try:
            from src.infrastructure.database.connection import DBRouter
            common_conn = DBRouter.get_common_connection()
            if not common_conn:
                return 0

            try:
                # 1-A) 만료/폐기 상태인데 remaining_qty > 0인 OT 레코드
                expired_rows = cursor.execute(
                    """
                    SELECT id, item_cd, remaining_qty, status
                    FROM order_tracking
                    WHERE store_id = ?
                      AND status IN ('expired', 'disposed')
                      AND remaining_qty > 0
                    """,
                    (store_id,),
                ).fetchall()

                # 1-B) 좀비 OT: arrived인데 expiry_time 경과 + remaining_qty > 0
                zombie_rows = cursor.execute(
                    """
                    SELECT id, item_cd, remaining_qty, status
                    FROM order_tracking
                    WHERE store_id = ?
                      AND status = 'arrived'
                      AND remaining_qty > 0
                      AND expiry_time < datetime('now')
                    """,
                    (store_id,),
                ).fetchall()

                all_rows = list(expired_rows) + list(zombie_rows)
                zombie_ids = {row["id"] for row in zombie_rows}

                if not all_rows:
                    return 0

                # 2) common.db에서 푸드 카테고리 필터링
                item_cds = list({row["item_cd"] for row in all_rows})
                placeholders = ",".join("?" * len(item_cds))
                food_cds_ph = ",".join("?" * len(food_mid_cds))
                common_cursor = common_conn.cursor()
                food_rows = common_cursor.execute(
                    f"""
                    SELECT item_cd FROM products
                    WHERE item_cd IN ({placeholders})
                      AND mid_cd IN ({food_cds_ph})
                    """,
                    (*item_cds, *food_mid_cds),
                ).fetchall()
                food_item_set = {row[0] for row in food_rows}
            finally:
                common_conn.close()

            if not food_item_set:
                return 0

            target_ids = [
                row["id"] for row in all_rows
                if row["item_cd"] in food_item_set
            ]

            if not target_ids:
                return 0

            # 3-A) 전체 대상 remaining_qty → 0
            placeholders = ",".join("?" * len(target_ids))
            cursor.execute(
                f"""
                UPDATE order_tracking
                SET remaining_qty = 0
                WHERE id IN ({placeholders})
                """,
                target_ids,
            )
            cleared = cursor.rowcount

            # 3-B) 좀비 OT는 status도 'expired'로 전환
            zombie_target_ids = [
                tid for tid in target_ids if tid in zombie_ids
            ]
            if zombie_target_ids:
                z_ph = ",".join("?" * len(zombie_target_ids))
                cursor.execute(
                    f"""
                    UPDATE order_tracking
                    SET status = 'expired'
                    WHERE id IN ({z_ph})
                    """,
                    zombie_target_ids,
                )
                logger.info(
                    f"[BatchSync] {store_id}: 좀비 OT {len(zombie_target_ids)}건 "
                    f"arrived→expired 전환"
                )

            return cleared

        except Exception as e:
            logger.warning(f"[BatchSync] OT 만료 잔량 정리 오류: {e}")
            return 0
