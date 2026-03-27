"""
데이터 무결성 검증 저장소 (IntegrityCheckRepository)

integrity_checks 테이블 CRUD + 5개 무결성 검증 쿼리.
매일 Phase 1.67에서 실행되어 데이터 이상을 감지한다.
"""

import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 푸드 카테고리 중분류 코드
FOOD_MID_CDS = ("001", "002", "003", "004", "005", "012", "014")


class IntegrityCheckRepository(BaseRepository):
    """데이터 무결성 검증 저장소

    5개 검증 쿼리와 결과 저장/조회 CRUD를 제공한다.
    db_type="store"이므로 _get_conn()이 자동으로 common.db를 ATTACH하며,
    products 테이블을 접두사 없이 사용할 수 있다.
    """

    db_type = "store"

    # ── 테이블 보장 ──

    def ensure_table(self) -> None:
        """CREATE TABLE IF NOT EXISTS (마이그레이션 전 안전 보장)"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS integrity_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    check_date TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    anomaly_count INTEGER DEFAULT 0,
                    details TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(store_id, check_date, check_name)
                );
                CREATE INDEX IF NOT EXISTS idx_integrity_date
                    ON integrity_checks(check_date, store_id);
                CREATE INDEX IF NOT EXISTS idx_integrity_status
                    ON integrity_checks(status);
            """)
        finally:
            conn.close()

    # ── CRUD ──

    def save_check_result(
        self,
        store_id: str,
        session_id: str,
        check_date: str,
        check_name: str,
        status: str,
        anomaly_count: int,
        details: List[Dict[str, Any]],
    ) -> None:
        """검증 결과 저장 (UPSERT — 동일 날짜+체크명 재실행 시 덮어쓰기)"""
        conn = self._get_conn()
        try:
            now = self._now()
            conn.execute(
                """
                INSERT INTO integrity_checks
                (store_id, session_id, check_date, check_name, status,
                 anomaly_count, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, check_date, check_name) DO UPDATE SET
                    session_id = excluded.session_id,
                    status = excluded.status,
                    anomaly_count = excluded.anomaly_count,
                    details = excluded.details,
                    created_at = excluded.created_at
                """,
                (store_id, session_id, check_date, check_name,
                 status, anomaly_count,
                 json.dumps(details, ensure_ascii=False), now),
            )
            conn.commit()
        finally:
            conn.close()

    def get_latest_results(
        self, store_id: str, days: int = 7
    ) -> List[Dict[str, Any]]:
        """최근 N일 검증 결과 조회"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM integrity_checks
                WHERE store_id = ?
                  AND check_date >= date('now', '-' || ? || ' days')
                ORDER BY check_date DESC, check_name
                """,
                (store_id, days),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                if row.get("details"):
                    try:
                        row["details"] = json.loads(row["details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return rows
        finally:
            conn.close()

    def get_anomaly_trend(
        self, store_id: str, check_name: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """특정 검증 항목 이상치 추이 (차트용)"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT check_date, status, anomaly_count
                FROM integrity_checks
                WHERE store_id = ? AND check_name = ?
                  AND check_date >= date('now', '-' || ? || ' days')
                ORDER BY check_date
                """,
                (store_id, check_name, days),
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    # ── 5개 무결성 검증 쿼리 ──

    def check_expired_batch_remaining(self, store_id: str) -> Dict[str, Any]:
        """Check 1: expired 배치의 remaining_qty > 0 (유령 배치)

        inventory_batches status='expired'인데 remaining_qty가 초기화되지 않은 레코드.
        발주 시 재고를 과다 추정하여 과소발주 유발.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM inventory_batches
                WHERE store_id = ? AND status = 'expired' AND remaining_qty > 0
                """,
                (store_id,),
            )
            count = cursor.fetchone()["cnt"]
            details = []
            if count > 0:
                cursor.execute(
                    """
                    SELECT id, item_cd, item_nm, mid_cd, expiry_date, remaining_qty
                    FROM inventory_batches
                    WHERE store_id = ? AND status = 'expired' AND remaining_qty > 0
                    ORDER BY expiry_date DESC LIMIT 20
                    """,
                    (store_id,),
                )
                details = [dict(r) for r in cursor.fetchall()]
            return {
                "check_name": "expired_batch_remaining",
                "status": "FAIL" if count > 0 else "OK",
                "count": count,
                "details": details,
            }
        finally:
            conn.close()

    def check_food_ghost_stock(self, store_id: str) -> Dict[str, Any]:
        """Check 2: 활성 배치 없는 푸드 상품의 RI stock_qty > 0 (유령 재고)

        active 배치가 하나도 없는데 realtime_inventory에 재고가 남아있는 푸드류.
        발주 계산에서 존재하지 않는 재고로 인해 발주량 감소.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            food_ph = ",".join("?" * len(FOOD_MID_CDS))
            cursor.execute(
                f"""
                SELECT COUNT(*) as cnt
                FROM realtime_inventory ri
                JOIN products p ON ri.item_cd = p.item_cd
                WHERE ri.store_id = ? AND ri.stock_qty > 0
                  AND p.mid_cd IN ({food_ph})
                  AND ri.item_cd NOT IN (
                      SELECT DISTINCT item_cd FROM inventory_batches
                      WHERE store_id = ? AND status = 'active' AND remaining_qty > 0
                  )
                """,
                (store_id, *FOOD_MID_CDS, store_id),
            )
            count = cursor.fetchone()["cnt"]
            details = []
            if count > 0:
                cursor.execute(
                    f"""
                    SELECT ri.item_cd, ri.item_nm, ri.stock_qty, p.mid_cd
                    FROM realtime_inventory ri
                    JOIN products p ON ri.item_cd = p.item_cd
                    WHERE ri.store_id = ? AND ri.stock_qty > 0
                      AND p.mid_cd IN ({food_ph})
                      AND ri.item_cd NOT IN (
                          SELECT DISTINCT item_cd FROM inventory_batches
                          WHERE store_id = ? AND status = 'active' AND remaining_qty > 0
                      )
                    ORDER BY ri.stock_qty DESC LIMIT 20
                    """,
                    (store_id, *FOOD_MID_CDS, store_id),
                )
                details = [dict(r) for r in cursor.fetchall()]
            if count > 5:
                status = "FAIL"
            elif count > 0:
                status = "WARN"
            else:
                status = "OK"
            return {
                "check_name": "food_ghost_stock",
                "status": status,
                "count": count,
                "details": details,
            }
        finally:
            conn.close()

    def check_expiry_time_mismatch(self, store_id: str) -> Dict[str, Any]:
        """Check 3: OT expiry_time vs IB expiry_date 1일 초과 차이

        order_tracking과 inventory_batches의 유통기한이 1일 넘게 불일치하면
        알림 시스템이 잘못된 시점에 경보를 발생시킬 수 있다.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            _mismatch_sql = """
                FROM order_tracking ot
                JOIN inventory_batches ib
                    ON ot.item_cd = ib.item_cd AND ot.store_id = ib.store_id
                WHERE ot.store_id = ?
                  AND ot.status NOT IN ('expired', 'disposed', 'cancelled')
                  AND ib.status = 'active' AND ib.remaining_qty > 0
                  AND ABS(julianday(date(ot.expiry_time)) - julianday(ib.expiry_date)) > 1
            """
            cursor.execute(f"SELECT COUNT(*) as cnt {_mismatch_sql}", (store_id,))
            count = cursor.fetchone()["cnt"]
            details = []
            if count > 0:
                cursor.execute(
                    f"""
                    SELECT ot.item_cd, ot.item_nm, ot.expiry_time, ib.expiry_date,
                           ABS(julianday(date(ot.expiry_time)) - julianday(ib.expiry_date)) as diff_days
                    {_mismatch_sql}
                    ORDER BY diff_days DESC LIMIT 20
                    """,
                    (store_id,),
                )
                details = [dict(r) for r in cursor.fetchall()]
            return {
                "check_name": "expiry_time_mismatch",
                "status": "WARN" if count > 1 else "OK",
                "count": count,
                "details": details,
            }
        finally:
            conn.close()

    def check_missing_delivery_type(self, store_id: str) -> Dict[str, Any]:
        """Check 4: 활성 OT의 delivery_type NULL

        배송차수(1차/2차) 정보가 없으면 유통기한 계산이 부정확해진다.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            _where = """
                FROM order_tracking
                WHERE store_id = ?
                  AND status NOT IN ('expired', 'disposed', 'cancelled')
                  AND (delivery_type IS NULL OR delivery_type = '')
            """
            cursor.execute(f"SELECT COUNT(*) as cnt {_where}", (store_id,))
            count = cursor.fetchone()["cnt"]
            details = []
            if count > 0:
                cursor.execute(
                    f"""
                    SELECT id, item_cd, item_nm, order_date, status
                    {_where}
                    ORDER BY order_date DESC LIMIT 20
                    """,
                    (store_id,),
                )
                details = [dict(r) for r in cursor.fetchall()]
            return {
                "check_name": "missing_delivery_type",
                "status": "WARN" if count > 10 else "OK",
                "count": count,
                "details": details,
            }
        finally:
            conn.close()

    def check_unavailable_with_sales(self, store_id: str) -> Dict[str, Any]:
        """Check 6: is_available=0인데 최근 7일 판매 있는 상품 감지 + 자동 복원

        preserve_stock_if_zero 등으로 is_available=0이 된 뒤
        복구되지 않아 발주가 차단되는 상품을 감지한다.

        자동 복원 조건 (안전):
        - unavail_reason IS NULL (수동 마킹/query_fail은 건드리지 않음)
        - is_cut_item = 0 (CUT 상품은 제외)
        - 최근 7일 내 판매 기록 있음
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 감지: 최근 7일 판매 있는 is_available=0 상품 전체
            cursor.execute(
                """
                SELECT COUNT(*) as cnt
                FROM realtime_inventory ri
                WHERE ri.store_id = ? AND ri.is_available = 0
                  AND ri.item_cd IN (
                      SELECT DISTINCT item_cd FROM daily_sales
                      WHERE store_id = ? AND sales_date >= date('now', '-7 days')
                        AND sale_qty > 0
                  )
                """,
                (store_id, store_id),
            )
            count = cursor.fetchone()["cnt"]

            details = []
            if count > 0:
                cursor.execute(
                    """
                    SELECT ri.item_cd, ri.item_nm, ri.stock_qty, ri.is_cut_item,
                           ri.query_fail_count, ri.unavail_reason,
                           s.sale_7d
                    FROM realtime_inventory ri
                    JOIN (
                        SELECT item_cd, SUM(sale_qty) as sale_7d
                        FROM daily_sales
                        WHERE store_id = ? AND sales_date >= date('now', '-7 days')
                          AND sale_qty > 0
                        GROUP BY item_cd
                    ) s ON ri.item_cd = s.item_cd
                    WHERE ri.store_id = ? AND ri.is_available = 0
                    ORDER BY s.sale_7d DESC LIMIT 20
                    """,
                    (store_id, store_id),
                )
                details = [dict(r) for r in cursor.fetchall()]

            # 자동 복원: unavail_reason이 NULL이고 CUT 아닌 상품만 안전하게 복원
            now = datetime.now().isoformat()
            cursor.execute(
                """
                UPDATE realtime_inventory
                SET is_available = 1,
                    query_fail_count = 0,
                    queried_at = ?
                WHERE store_id = ? AND is_available = 0
                  AND (unavail_reason IS NULL OR unavail_reason = '')
                  AND is_cut_item = 0
                  AND item_cd IN (
                      SELECT DISTINCT item_cd FROM daily_sales
                      WHERE store_id = ? AND sales_date >= date('now', '-7 days')
                        AND sale_qty > 0
                  )
                """,
                (now, store_id, store_id),
            )
            restored_count = cursor.rowcount
            conn.commit()

            if restored_count > 0:
                logger.info(
                    f"[Integrity] {store_id}/unavailable_with_sales: "
                    f"{restored_count}건 자동 복원 (감지 {count}건 중)"
                )

            # 복원 후에도 남아있는 건 (수동마킹/CUT/query_fail로 인한 것)
            remaining = count - restored_count

            return {
                "check_name": "unavailable_with_sales",
                "status": "WARN" if remaining > 0 else ("RESTORED" if restored_count > 0 else "OK"),
                "count": count,
                "restored_count": restored_count,
                "remaining_count": remaining,
                "details": details,
            }
        finally:
            conn.close()

    def check_past_expiry_active(self, store_id: str) -> Dict[str, Any]:
        """Check 5: 만료 시간 경과인데 arrived 상태 유지 (좀비 OT)

        expiry_time이 현재보다 과거인데 status='arrived', remaining_qty > 0인 레코드.
        _get_actual_expiry_time()에서 만료된 expiry_time을 반환하여
        알림 시스템이 "아직 유통기한 남음"으로 오판정.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            _where = """
                FROM order_tracking
                WHERE store_id = ?
                  AND remaining_qty > 0 AND status = 'arrived'
                  AND expiry_time < datetime('now')
            """
            cursor.execute(f"SELECT COUNT(*) as cnt {_where}", (store_id,))
            count = cursor.fetchone()["cnt"]
            details = []
            if count > 0:
                cursor.execute(
                    f"""
                    SELECT id, item_cd, item_nm, expiry_time, remaining_qty, order_date
                    {_where}
                    ORDER BY expiry_time ASC LIMIT 20
                    """,
                    (store_id,),
                )
                details = [dict(r) for r in cursor.fetchall()]
            return {
                "check_name": "past_expiry_active",
                "status": "FAIL" if count > 0 else "OK",
                "count": count,
                "details": details,
            }
        finally:
            conn.close()
