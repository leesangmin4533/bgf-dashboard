"""
발주 추적 저장소 (OrderTrackingRepository)

order_tracking 테이블 CRUD (폐기 관리용)
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.settings.constants import (
    BATCH_STATUS_EXPIRED,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderTrackingRepository(BaseRepository):
    """발주 추적 저장소 (폐기 관리용)"""

    db_type = "store"

    def save_order(
        self,
        order_date: str,
        item_cd: str,
        item_nm: str,
        mid_cd: str,
        delivery_type: str,
        order_qty: int,
        arrival_time: str,
        expiry_time: str,
        store_id: Optional[str] = None,
        order_source: str = 'site'
    ) -> int:
        """
        발주 정보 저장

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            item_cd: 상품 코드
            item_nm: 상품명
            mid_cd: 중분류 코드
            delivery_type: 배송 차수 (1차/2차)
            order_qty: 발주 수량
            arrival_time: 도착 예정 시간 (YYYY-MM-DD HH:MM)
            expiry_time: 폐기 예정 시간 (YYYY-MM-DD HH:MM)
            store_id: 매장 코드 (None이면 기본값 사용)
            order_source: 발주 출처 ('auto'=자동발주, 'site'=BGF사이트수집, 'manual'=수동)

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            if store_id:
                cursor.execute(
                    """
                    INSERT INTO order_tracking
                    (store_id, order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, remaining_qty, arrival_time, expiry_time,
                     status, alert_sent, order_source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered', 0, ?, ?, ?)
                    """,
                    (store_id, order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, order_qty, arrival_time, expiry_time,
                     order_source, now, now)
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO order_tracking
                    (order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, remaining_qty, arrival_time, expiry_time,
                     status, alert_sent, order_source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered', 0, ?, ?, ?)
                    """,
                    (order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, order_qty, arrival_time, expiry_time,
                     order_source, now, now)
                )

            order_id = cursor.lastrowid
            conn.commit()
            return order_id
        finally:
            conn.close()

    def get_items_expiring_at(self, expiry_hour: int, target_date: Optional[str] = None,
                              store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        특정 폐기 시간에 해당하는 발주 상품 조회

        Args:
            expiry_hour: 폐기 시간 (예: 14 = 14:00)
            target_date: 대상 날짜 (기본: 오늘)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            폐기 대상 상품 목록
        """
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")

        # 폐기 시간 패턴 (예: "2026-01-28 14:00")
        expiry_pattern = f"{target_date} {expiry_hour:02d}:%"

        store_filter = "AND store_id = ?" if store_id else ""
        store_params = (store_id,) if store_id else ()

        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                f"""
                SELECT *
                FROM order_tracking
                WHERE expiry_time LIKE ?
                AND status IN ('ordered', 'arrived')
                AND remaining_qty > 0
                AND alert_sent = 0
                {store_filter}
                ORDER BY item_nm
                """,
                (expiry_pattern,) + store_params
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pending_expiry_items(self, hours_ahead: int = 1,
                                store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        N시간 내 폐기 예정 상품 조회

        Args:
            hours_ahead: 몇 시간 이내 폐기 예정
            store_id: 매장 코드 (None이면 전체)

        Returns:
            폐기 예정 상품 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT *
                FROM order_tracking
                WHERE datetime(expiry_time) <= datetime('now', '+' || ? || ' hours')
                AND datetime(expiry_time) > datetime('now')
                AND status IN ('ordered', 'arrived')
                AND remaining_qty > 0
                {store_filter}
                ORDER BY expiry_time
                """,
                (hours_ahead,) + store_params
            )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_status(self, order_id: int, status: str) -> None:
        """발주 추적 상태 업데이트

        Args:
            order_id: order_tracking 레코드 ID
            status: 변경할 상태 (ordered, arrived, expired, disposed)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE order_tracking
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, self._now(), order_id)
            )

            conn.commit()
        finally:
            conn.close()

    def get_by_item_and_order_date(self, item_cd: str, order_date: str,
                                   store_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        상품코드와 발주일로 발주 추적 조회

        Args:
            item_cd: 상품 코드
            order_date: 발주일 (YYYY-MM-DD)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            발주 추적 레코드 (없으면 None)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT *
                FROM order_tracking
                WHERE item_cd = ? AND order_date = ?
                {store_filter}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (item_cd, order_date) + store_params
            )

            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_receiving(self, tracking_id: int, receiving_qty: int, arrival_time: str) -> None:
        """
        입고 정보 업데이트

        Args:
            tracking_id: order_tracking 레코드 ID
            receiving_qty: 입고 수량
            arrival_time: 입고 시각 (YYYY-MM-DD HH:MM)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE order_tracking
                SET arrival_time = ?, remaining_qty = ?, updated_at = ?
                WHERE id = ?
                """,
                (arrival_time, receiving_qty, self._now(), tracking_id)
            )

            conn.commit()
        finally:
            conn.close()

    def mark_alert_sent(self, order_id: int) -> None:
        """알림 발송 완료 표시

        Args:
            order_id: order_tracking 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE order_tracking
                SET alert_sent = 1, updated_at = ?
                WHERE id = ?
                """,
                (self._now(), order_id)
            )

            conn.commit()
        finally:
            conn.close()

    def update_remaining_qty(self, order_id: int, remaining_qty: int) -> None:
        """남은 수량 업데이트

        Args:
            order_id: order_tracking 레코드 ID
            remaining_qty: 남은 수량
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                UPDATE order_tracking
                SET remaining_qty = ?, updated_at = ?
                WHERE id = ?
                """,
                (remaining_qty, self._now(), order_id)
            )

            conn.commit()
        finally:
            conn.close()

    def get_active_orders(self, item_cd: Optional[str] = None,
                          store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        활성 발주 목록 조회 (폐기 전 상품)

        Args:
            item_cd: 상품 코드 (None이면 전체)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            활성 발주 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            if item_cd:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM order_tracking
                    WHERE item_cd = ?
                    AND status IN ('ordered', 'arrived')
                    AND remaining_qty > 0
                    {store_filter}
                    ORDER BY expiry_time
                    """,
                    (item_cd,) + store_params
                )
            else:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM order_tracking
                    WHERE status IN ('ordered', 'arrived')
                    AND remaining_qty > 0
                    {store_filter}
                    ORDER BY expiry_time
                    """,
                    store_params
                )

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def mark_as_disposed(self, order_id: int, disposed_qty: Optional[int] = None) -> None:
        """폐기 완료 처리

        Args:
            order_id: order_tracking 레코드 ID
            disposed_qty: 폐기 수량 (지정 시 remaining_qty를 0으로 설정)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            if disposed_qty is not None:
                cursor.execute(
                    """
                    UPDATE order_tracking
                    SET status = 'disposed', remaining_qty = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (self._now(), order_id)
                )
            else:
                cursor.execute(
                    """
                    UPDATE order_tracking
                    SET status = 'disposed', updated_at = ?
                    WHERE id = ?
                    """,
                    (self._now(), order_id)
                )

            conn.commit()
        finally:
            conn.close()

    def auto_update_statuses(self, store_id: Optional[str] = None) -> Dict[str, int]:
        """
        상태 자동 업데이트
        - 도착 시간 지나면 ordered → arrived
        - 폐기 시간 지나면 arrived → expired

        Args:
            store_id: 매장 코드 (None이면 전체)

        Returns:
            {"arrived": N, "expired": N}
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # 도착 시간 지난 상품: ordered → arrived
            cursor.execute(
                f"""
                UPDATE order_tracking
                SET status = 'arrived', updated_at = ?
                WHERE status = 'ordered'
                AND datetime(arrival_time) <= datetime('now','localtime')
                {store_filter}
                """,
                (now,) + store_params
            )
            arrived_count = cursor.rowcount

            # 폐기 시간 지난 상품: arrived → expired
            cursor.execute(
                f"""
                UPDATE order_tracking
                SET status = ?, updated_at = ?
                WHERE status = 'arrived'
                AND datetime(expiry_time) <= datetime('now','localtime')
                AND remaining_qty > 0
                {store_filter}
                """,
                (BATCH_STATUS_EXPIRED, now) + store_params
            )
            expired_count = cursor.rowcount

            conn.commit()
            return {"arrived": arrived_count, "expired": expired_count}
        finally:
            conn.close()

    def save_order_manual(
        self,
        order_date: str,
        item_cd: str,
        item_nm: str,
        mid_cd: str,
        delivery_type: str,
        order_qty: int,
        arrival_time: str,
        expiry_time: str,
        order_source: str = 'manual',
        store_id: Optional[str] = None
    ) -> int:
        """
        수동 발주 정보 저장

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            item_cd: 상품 코드
            item_nm: 상품명
            mid_cd: 중분류 코드
            delivery_type: 배송 차수
            order_qty: 발주 수량
            arrival_time: 도착 예정 시간
            expiry_time: 폐기 예정 시간
            order_source: 발주 소스 ('manual')
            store_id: 매장 코드 (None이면 기본값 사용)

        Returns:
            저장된 레코드 ID
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            now = self._now()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            # 중복 체크
            cursor.execute(
                f"""
                SELECT id FROM order_tracking
                WHERE order_date = ? AND item_cd = ? AND order_source = ?
                {store_filter}
                """,
                (order_date, item_cd, order_source) + store_params
            )
            if cursor.fetchone():
                raise ValueError(f"이미 등록된 수동발주: {item_cd}")

            if store_id:
                cursor.execute(
                    """
                    INSERT INTO order_tracking
                    (store_id, order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, remaining_qty, arrival_time, expiry_time,
                     status, alert_sent, order_source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered', 0, ?, ?, ?)
                    """,
                    (store_id, order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, order_qty, arrival_time, expiry_time,
                     order_source, now, now)
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO order_tracking
                    (order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, remaining_qty, arrival_time, expiry_time,
                     status, alert_sent, order_source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered', 0, ?, ?, ?)
                    """,
                    (order_date, item_cd, item_nm, mid_cd, delivery_type,
                     order_qty, order_qty, arrival_time, expiry_time,
                     order_source, now, now)
                )

            order_id = cursor.lastrowid
            conn.commit()
            return order_id
        finally:
            conn.close()

    def get_existing_order(
        self,
        order_date: str,
        item_cd: str,
        order_source: Optional[str] = None,
        store_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """특정 발주 조회 (중복 체크용)

        Args:
            order_date: 발주일 (YYYY-MM-DD)
            item_cd: 상품코드
            order_source: 'auto'|'site'|'manual' (None이면 전체)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            발주 정보 dict 또는 None
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            sql = """
                SELECT id, order_date, item_cd, item_nm, mid_cd,
                       order_qty, remaining_qty, order_source, status
                FROM order_tracking
                WHERE order_date = ? AND item_cd = ?
            """
            params: list = [order_date, item_cd]

            if order_source:
                sql += " AND order_source = ?"
                params.append(order_source)

            if store_id:
                sql += " AND store_id = ?"
                params.append(store_id)

            sql += " ORDER BY id DESC LIMIT 1"

            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_site_ordered_items(self, order_date: str) -> Dict[str, int]:
        """특정 날짜에 site 발주된 상품 목록 조회 (item_cd -> order_qty)

        Phase 2 auto 발주에서 site 기발주 상품을 스킵하기 위해 사용.
        BGF saveOrd가 UPSERT이므로, auto가 제출하면 site 수량을 덮어쓰게 됨.

        Args:
            order_date: 발주일 (YYYY-MM-DD)

        Returns:
            {item_cd: order_qty} dict
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT item_cd, order_qty
                FROM order_tracking
                WHERE order_date = ? AND order_source = 'site'
            """, (order_date,))
            return {row["item_cd"]: row["order_qty"] for row in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"site 기발주 조회 실패: {e}")
            return {}
        finally:
            conn.close()

    def get_by_source(self, order_source: str, limit: int = 100,
                      store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        발주 소스별 조회

        Args:
            order_source: 'auto' 또는 'manual'
            limit: 최대 조회 수
            store_id: 매장 코드 (None이면 전체)

        Returns:
            발주 목록
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT *
                FROM order_tracking
                WHERE order_source = ?
                {store_filter}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (order_source,) + store_params + (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_existing_tracking_items(self, arrival_date: str,
                                    store_id: Optional[str] = None) -> set:
        """이미 등록된 tracking 항목 조회 (중복 방지용)

        Args:
            arrival_date: 도착일 (YYYY-MM-DD)
            store_id: 매장 코드 (None이면 전체)

        Returns:
            등록된 상품코드 set
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT DISTINCT item_cd
                FROM order_tracking
                WHERE date(arrival_time) = ?
                {store_filter}
                """,
                (arrival_date,) + store_params
            )
            return {row['item_cd'] for row in cursor.fetchall()}
        finally:
            conn.close()

    def get_pending_age_batch(
        self,
        store_id: Optional[str] = None
    ) -> Dict[str, int]:
        """
        매장 전체 미입고 상품의 pending 경과일 일괄 조회 (ML 피처용)

        현재 ordered/arrived 상태이고 remaining_qty > 0인 상품에 대해
        가장 오래된 발주일 기준 경과일수를 계산한다.

        Args:
            store_id: 매장 코드

        Returns:
            {item_cd: pending_age_days(int)}
            pending 없는 상품은 dict에 포함하지 않음
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT
                    item_cd,
                    MIN(order_date) as oldest_order_date
                FROM order_tracking
                WHERE status IN ('ordered', 'arrived')
                    AND remaining_qty > 0
                    {store_filter}
                GROUP BY item_cd
                """,
                store_params
            )

            result = {}
            today = datetime.now().strftime("%Y-%m-%d")
            for row in cursor.fetchall():
                item_cd = row[0]
                oldest_date = row[1]
                if oldest_date:
                    try:
                        from datetime import timedelta
                        delta = (datetime.strptime(today, "%Y-%m-%d")
                                 - datetime.strptime(oldest_date, "%Y-%m-%d"))
                        result[item_cd] = max(0, delta.days)
                    except (ValueError, TypeError):
                        result[item_cd] = 0

            return result
        except Exception as e:
            logger.warning(f"[입고패턴] pending 경과일 조회 실패: {e}")
            return {}
        finally:
            conn.close()

    def get_pending_qty_sum_batch(
        self,
        store_id: Optional[str] = None
    ) -> Dict[str, int]:
        """
        매장 전체 미입고 상품의 remaining_qty 합계 일괄 조회 (교차검증용)

        realtime_inventory.pending_qty와 교차검증하여 phantom pending을 방지한다.
        ordered/arrived 상태이고 remaining_qty > 0인 상품의 잔여 수량을 합산.

        Args:
            store_id: 매장 코드

        Returns:
            {item_cd: total_remaining_qty(int)}
            pending 없는 상품은 dict에 포함하지 않음 (= pending 0)
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            store_filter = "AND store_id = ?" if store_id else ""
            store_params = (store_id,) if store_id else ()

            cursor.execute(
                f"""
                SELECT
                    item_cd,
                    SUM(remaining_qty) as total_pending
                FROM order_tracking
                WHERE status IN ('ordered', 'arrived')
                    AND remaining_qty > 0
                    {store_filter}
                GROUP BY item_cd
                """,
                store_params
            )

            result = {}
            for row in cursor.fetchall():
                result[row[0]] = row[1] or 0

            return result
        except Exception as e:
            logger.warning(f"[미입고교차검증] pending 합계 조회 실패: {e}")
            return {}
        finally:
            conn.close()

    def reconcile_with_bgf_orders(
        self,
        order_date: str,
        bgf_confirmed_items: set,
        store_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """BGF 발주현황과 order_tracking을 대조하여 정합성 보정

        1. BGF에 있는 건 → pending_confirmed=1 (미입고 보정의 근거)
        2. BGF에 없는 건 → remaining_qty=0, status='invalidated' (false positive 제거)

        Args:
            order_date: 대조 대상 날짜 ('YYYY-MM-DD')
            bgf_confirmed_items: BGF 발주현황에서 확인된 item_cd set
            store_id: 매장 코드

        Returns:
            {confirmed: N, invalidated: N, confirmed_items: [{item_cd, order_qty}, ...]}
        """
        sid = store_id or self.store_id
        conn = self._get_conn(sid)
        try:
            cursor = conn.cursor()

            # auto 발주 중 remaining_qty > 0 (미입고 상태)인 건만 대상
            cursor.execute("""
                SELECT id, item_cd, item_nm, order_qty, remaining_qty
                FROM order_tracking
                WHERE order_date = ?
                  AND order_source = 'auto'
                  AND remaining_qty > 0
            """, (order_date,))
            rows = cursor.fetchall()

            confirmed_count = 0
            invalidated_count = 0
            confirmed_items = []

            for row in rows:
                track_id, item_cd, item_nm, order_qty, remaining_qty = row
                if item_cd in bgf_confirmed_items:
                    # BGF에 있음 → 발주 확인됨, pending_confirmed 마킹
                    cursor.execute("""
                        UPDATE order_tracking
                        SET pending_confirmed = 1,
                            pending_confirmed_at = datetime('now', 'localtime'),
                            updated_at = datetime('now', 'localtime')
                        WHERE id = ?
                    """, (track_id,))
                    confirmed_count += 1
                    confirmed_items.append({
                        'item_cd': item_cd,
                        'item_nm': item_nm,
                        'order_qty': order_qty,
                    })
                else:
                    # BGF에 없음 → false positive, 무효화
                    cursor.execute("""
                        UPDATE order_tracking
                        SET remaining_qty = 0, status = 'invalidated',
                            updated_at = datetime('now', 'localtime')
                        WHERE id = ?
                    """, (track_id,))
                    invalidated_count += 1
                    logger.warning(
                        f"[발주정합성] 무효화: {item_nm}({item_cd}) "
                        f"qty={order_qty} (order_tracking에 있으나 BGF에 없음)"
                    )

            if confirmed_count > 0 or invalidated_count > 0:
                conn.commit()
                logger.info(
                    f"[발주정합성] {order_date}: "
                    f"BGF확인={confirmed_count}건, 무효화={invalidated_count}건 "
                    f"(전체 {len(rows)}건 대조)"
                )

            return {
                'confirmed': confirmed_count,
                'invalidated': invalidated_count,
                'confirmed_items': confirmed_items,
            }
        except Exception as e:
            logger.warning(f"[발주정합성] 대조 처리 실패: {e}")
            return {'confirmed': 0, 'invalidated': 0, 'confirmed_items': []}
        finally:
            conn.close()

    def get_confirmed_pending(
        self,
        order_date: str,
        store_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """BGF 확인된 미입고 수량 조회 (adjuster pending 보정용)

        pending_confirmed=1이고 remaining_qty>0인 항목의 수량을 반환.
        order_prep_collector가 pending=0으로 잘못 반환했을 때
        이 데이터로 보정하여 중복 발주를 방지한다.

        Args:
            order_date: 발주 날짜 ('YYYY-MM-DD')
            store_id: 매장 코드

        Returns:
            {item_cd: remaining_qty, ...}
        """
        sid = store_id or self.store_id
        conn = self._get_conn(sid)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT item_cd, SUM(remaining_qty) as total_pending
                FROM order_tracking
                WHERE order_date = ?
                  AND pending_confirmed = 1
                  AND remaining_qty > 0
                GROUP BY item_cd
            """, (order_date,))
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"[발주정합성] 확인 pending 조회 실패: {e}")
            return {}
        finally:
            conn.close()
