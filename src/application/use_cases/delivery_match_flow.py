"""
DeliveryMatchFlow — 발주 확정(confirmed_orders) vs 실제 입고(receiving_history) 매칭

10:30 pending_sync에서 저장한 스냅샷과 실제 입고 데이터를 비교하여
입고 확정된 상품만 inventory_batches(active) 배치를 생성한다.

호출 시점:
  - 20:30 receiving_collect 완료 후 → delivery_type='1차' (오늘 발주 → 오늘 입고)
  - 07:00 daily job Phase 1.1 완료 후 → delivery_type='2차' (어제 발주 → 오늘 입고)
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# FOOD_EXPIRY_CONFIG 공유 (receiving_collector와 동일 상수)
FOOD_EXPIRY_CONFIG = {
    '001': {'1차': (2, 2),  '2차': (1, 14)},  # 도시락
    '002': {'1차': (2, 2),  '2차': (1, 14)},  # 주먹밥
    '003': {'1차': (2, 2),  '2차': (1, 14)},  # 김밥
    '004': {'1차': (3, 22), '2차': (2, 10)},  # 샌드위치
    '005': {'1차': (3, 22), '2차': (2, 10)},  # 햄버거
}


def calc_expiry_datetime(
    mid_cd: str, delivery_type: str, receiving_date: str
) -> Optional[str]:
    """mid_cd + delivery_type 기반 폐기시간 계산

    Returns:
        'YYYY-MM-DD HH:MM:SS' 또는 None
    """
    config = FOOD_EXPIRY_CONFIG.get(mid_cd, {})
    dt_config = config.get(delivery_type)
    if dt_config:
        days_offset, expiry_hour = dt_config
        recv_dt = datetime.strptime(receiving_date, '%Y-%m-%d')
        expiry_dt = recv_dt + timedelta(days=days_offset)
        return expiry_dt.strftime('%Y-%m-%d') + f' {expiry_hour:02d}:00:00'
    return None


def match_confirmed_with_receiving(
    store_id: str,
    delivery_type: str,
    conn=None,
) -> Dict[str, Any]:
    """confirmed_orders와 receiving_history를 비교하여 확정 배치 생성.

    Args:
        store_id: 매장 코드
        delivery_type: '1차' (20:30 호출) 또는 '2차' (07:00 호출)
        conn: DB 연결 (테스트용, None이면 DBRouter에서 획득)

    Returns:
        {matched: N, unmatched: N, skipped: N, errors: N}
    """
    today = datetime.now().strftime("%Y-%m-%d")
    result: Dict[str, Any] = {
        "matched": 0, "unmatched": 0, "skipped": 0, "errors": 0,
        "store_id": store_id, "delivery_type": delivery_type,
    }

    if delivery_type == '1차':
        snapshot_date = today
        receiving_date = today
    else:
        snapshot_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        receiving_date = today

    own_conn = conn is None
    if own_conn:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_connection(store_id=store_id, table="confirmed_orders")
    try:
        cursor = conn.cursor()

        # 1. 매칭 대상 조회 (미매칭 + 해당 차수)
        cursor.execute("""
            SELECT id, item_cd, item_nm, mid_cd, ord_qty
            FROM confirmed_orders
            WHERE store_id = ? AND order_date = ? AND delivery_type = ? AND matched = 0
        """, (store_id, snapshot_date, delivery_type))
        confirmed_rows = cursor.fetchall()

        if not confirmed_rows:
            logger.info(
                f"[DeliveryMatch] {store_id} {delivery_type}: "
                f"스냅샷 없음 (order_date={snapshot_date}), 폴백 유지"
            )
            result["skipped"] = -1  # 스냅샷 없음 표시
            return result

        # 2. 실제 입고 조회
        item_cds = [r['item_cd'] for r in confirmed_rows]
        placeholders = ','.join('?' * len(item_cds))
        cursor.execute(f"""
            SELECT item_cd, SUM(receiving_qty) as total_qty
            FROM receiving_history
            WHERE store_id = ? AND receiving_date = ? AND item_cd IN ({placeholders})
            GROUP BY item_cd
        """, (store_id, receiving_date, *item_cds))
        received_map = {r['item_cd']: r['total_qty'] for r in cursor.fetchall()}

        # 3. 매칭 + 배치 생성
        from src.infrastructure.database.repos import InventoryBatchRepository
        batch_repo = InventoryBatchRepository(store_id=store_id)
        now = datetime.now().isoformat()

        for co in confirmed_rows:
            item_cd = co['item_cd']
            mid_cd = co['mid_cd'] or ''
            actual_qty = received_map.get(item_cd, 0)

            try:
                if actual_qty > 0:
                    # 입고 확인 → 확정 배치 생성
                    expiry_dt = calc_expiry_datetime(mid_cd, delivery_type, receiving_date)

                    # 중복 방지: 같은 item+date에 배치 있으면 스킵
                    existing = batch_repo.get_batch_by_item_and_date(
                        item_cd=item_cd,
                        receiving_date=receiving_date,
                        store_id=store_id,
                    )
                    if not existing:
                        # product_details에서 expiration_days 조회
                        from src.infrastructure.database.connection import DBRouter as DR
                        common_conn = DR.get_common_connection()
                        try:
                            cc = common_conn.cursor()
                            cc.execute(
                                "SELECT expiration_days FROM product_details WHERE item_cd = ?",
                                (item_cd,)
                            )
                            pd_row = cc.fetchone()
                            exp_days = pd_row[0] if pd_row and pd_row[0] else 2
                        finally:
                            common_conn.close()

                        batch_repo.create_batch(
                            item_cd=item_cd,
                            item_nm=co['item_nm'] or '',
                            mid_cd=mid_cd,
                            receiving_date=receiving_date,
                            expiration_days=exp_days,
                            initial_qty=actual_qty,
                            store_id=store_id,
                            delivery_type=delivery_type,
                            expiry_datetime=expiry_dt,
                        )
                        logger.info(
                            f"[DeliveryMatch] 매칭 OK: {co['item_nm']} "
                            f"발주={co['ord_qty']} 입고={actual_qty} "
                            f"expiry={expiry_dt or 'date-based'}"
                        )
                    else:
                        logger.debug(
                            f"[DeliveryMatch] 배치 이미 존재: {item_cd} {receiving_date}"
                        )

                    # 매칭 완료 표시
                    cursor.execute("""
                        UPDATE confirmed_orders
                        SET matched = 1, matched_qty = ?
                        WHERE id = ?
                    """, (actual_qty, co['id']))
                    result["matched"] += 1

                else:
                    # 미입고 → 배치 미생성
                    logger.warning(
                        f"[DeliveryMatch] 미입고: {co['item_nm']}({item_cd}) "
                        f"발주={co['ord_qty']}개 — 배치 미생성"
                    )
                    result["unmatched"] += 1

            except Exception as e:
                logger.warning(f"[DeliveryMatch] 매칭 오류 ({item_cd}): {e}")
                result["errors"] += 1

        conn.commit()

        logger.info(
            f"[DeliveryMatch] {store_id} {delivery_type} 완료: "
            f"매칭={result['matched']}, 미입고={result['unmatched']}, "
            f"오류={result['errors']}"
        )

    except Exception as e:
        logger.error(f"[DeliveryMatch] {store_id} {delivery_type} 실패: {e}")
        result["errors"] += 1
    finally:
        if own_conn:
            conn.close()

    return result


def rematch_unmatched(store_id: str, conn=None) -> Dict[str, Any]:
    """미매칭(matched=0) confirmed_orders 전체 재매칭 (delivery_type 무관)

    20:30 receiving_collect 후 호출. 07:00에 receiving_qty=0이었던 상품이
    이 시점에는 입고 완료되어 qty > 0이므로 매칭 가능.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    result: Dict[str, Any] = {
        "matched": 0, "still_unmatched": 0, "errors": 0, "store_id": store_id,
    }

    own_conn = conn is None
    if own_conn:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_connection(store_id=store_id, table="confirmed_orders")
    try:
        cursor = conn.cursor()

        # 1. 미매칭 전부 (오늘+어제)
        cursor.execute("""
            SELECT id, item_cd, item_nm, mid_cd, ord_qty, delivery_type, order_date
            FROM confirmed_orders
            WHERE store_id = ? AND matched = 0 AND order_date IN (?, ?)
        """, (store_id, today, yesterday))
        unmatched_rows = cursor.fetchall()

        if not unmatched_rows:
            logger.info(f"[Rematch] {store_id}: 미매칭 없음")
            return result

        # 2. receiving_qty 조회
        item_cds = list({r['item_cd'] for r in unmatched_rows})
        placeholders = ','.join('?' * len(item_cds))
        cursor.execute(f"""
            SELECT item_cd, receiving_date, SUM(receiving_qty) as total_qty
            FROM receiving_history
            WHERE store_id = ? AND receiving_date IN (?, ?)
              AND item_cd IN ({placeholders})
            GROUP BY item_cd, receiving_date
        """, (store_id, today, yesterday, *item_cds))
        received_map = {}
        for r in cursor.fetchall():
            received_map[(r['item_cd'], r['receiving_date'])] = r['total_qty']

        # 3. 매칭
        from src.infrastructure.database.repos import InventoryBatchRepository
        batch_repo = InventoryBatchRepository(store_id=store_id)

        for co in unmatched_rows:
            item_cd = co['item_cd']
            mid_cd = co['mid_cd'] or ''
            delivery_type = co['delivery_type']
            order_date = co['order_date']

            # receiving_date: 1차=당일, 2차=익일
            if delivery_type == '1차':
                recv_date = order_date
            else:
                recv_date = (
                    datetime.strptime(order_date, '%Y-%m-%d') + timedelta(days=1)
                ).strftime('%Y-%m-%d')

            actual_qty = received_map.get((item_cd, recv_date), 0)
            try:
                if actual_qty > 0:
                    expiry_dt = calc_expiry_datetime(mid_cd, delivery_type, recv_date)
                    existing = batch_repo.get_batch_by_item_and_date(
                        item_cd=item_cd, receiving_date=recv_date, store_id=store_id,
                    )
                    if not existing:
                        from src.infrastructure.database.connection import DBRouter as DR
                        common_conn = DR.get_common_connection()
                        try:
                            pd_row = common_conn.execute(
                                "SELECT expiration_days FROM product_details WHERE item_cd = ?",
                                (item_cd,),
                            ).fetchone()
                            exp_days = pd_row[0] if pd_row and pd_row[0] else 2
                        finally:
                            common_conn.close()

                        batch_repo.create_batch(
                            item_cd=item_cd, item_nm=co['item_nm'] or '',
                            mid_cd=mid_cd, receiving_date=recv_date,
                            expiration_days=exp_days, initial_qty=actual_qty,
                            store_id=store_id, delivery_type=delivery_type,
                            expiry_datetime=expiry_dt,
                        )

                    cursor.execute(
                        "UPDATE confirmed_orders SET matched = 1, matched_qty = ? WHERE id = ?",
                        (actual_qty, co['id']),
                    )
                    result["matched"] += 1
                else:
                    result["still_unmatched"] += 1
            except Exception as e:
                logger.warning(f"[Rematch] 오류 ({item_cd}): {e}")
                result["errors"] += 1

        conn.commit()
        logger.info(
            f"[Rematch] {store_id} 완료: "
            f"재매칭={result['matched']}, 미입고={result['still_unmatched']}, "
            f"오류={result['errors']}"
        )
    except Exception as e:
        logger.error(f"[Rematch] {store_id} 실패: {e}")
        result["errors"] += 1
    finally:
        if own_conn:
            conn.close()

    return result
