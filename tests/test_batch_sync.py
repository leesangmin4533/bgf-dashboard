"""
InventoryBatchRepository.sync_remaining_with_stock() 테스트

stock 기반 FIFO 재동기화 로직 검증:
- batch_total > stock_qty 이면 FIFO 차감
- stock_qty == 0 이면 모든 active 배치 consumed
- 이미 정합이면 변경 없음
"""

import pytest
from datetime import datetime
from unittest.mock import patch

from src.infrastructure.database.repos.inventory_batch_repo import (
    InventoryBatchRepository,
)
from src.settings.constants import (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_CONSUMED,
    BATCH_STATUS_EXPIRED,
)


STORE_ID = "99999"


class _NoCloseConn:
    """close()를 무시하는 SQLite 연결 래퍼"""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._conn, name)

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        return self._conn.commit()

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)


@pytest.fixture
def batch_repo(in_memory_db):
    """in_memory_db 기반 InventoryBatchRepository"""
    # inventory_batches가 in_memory_db에 없으면 생성
    in_memory_db.execute("""
        CREATE TABLE IF NOT EXISTS inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            receiving_date TEXT,
            receiving_id INTEGER,
            expiration_days INTEGER,
            expiry_date TEXT,
            initial_qty INTEGER,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            store_id TEXT DEFAULT '99999'
        )
    """)
    in_memory_db.commit()

    repo = InventoryBatchRepository(store_id=STORE_ID)
    wrapped = _NoCloseConn(in_memory_db)
    repo._get_conn = lambda: wrapped
    return repo


def _insert_batch(conn, item_cd, remaining_qty, receiving_date="2026-02-17",
                  expiry_date="2026-02-18", initial_qty=None, status=BATCH_STATUS_ACTIVE):
    """헬퍼: inventory_batches 레코드 삽입"""
    if initial_qty is None:
        initial_qty = remaining_qty
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO inventory_batches
        (store_id, item_cd, item_nm, mid_cd, receiving_date, expiration_days,
         expiry_date, initial_qty, remaining_qty, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
        """,
        (STORE_ID, item_cd, f"상품_{item_cd}", "001", receiving_date,
         expiry_date, initial_qty, remaining_qty, status, now, now),
    )
    conn.commit()


def _insert_daily_sales(conn, item_cd, stock_qty, sales_date="2026-02-18"):
    """헬퍼: daily_sales 레코드 삽입"""
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_sales
        (store_id, collected_at, sales_date, item_cd, mid_cd,
         sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty, created_at)
        VALUES (?, ?, ?, ?, '001', 0, 0, 0, 0, ?, ?)
        """,
        (STORE_ID, now, sales_date, item_cd, stock_qty, now),
    )
    conn.commit()


class TestSyncRemainingWithStock:
    """sync_remaining_with_stock() 테스트"""

    def test_stock_zero_consumes_all(self, batch_repo, in_memory_db):
        """stock=0이면 모든 active 배치를 consumed 처리"""
        _insert_batch(in_memory_db, "ITEM_A", remaining_qty=3)
        _insert_batch(in_memory_db, "ITEM_A", remaining_qty=2, receiving_date="2026-02-16")
        _insert_daily_sales(in_memory_db, "ITEM_A", stock_qty=0)

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["adjusted"] == 1
        assert result["consumed"] == 2  # 두 배치 모두 consumed

        # DB 확인
        cursor = in_memory_db.cursor()
        cursor.execute(
            "SELECT status FROM inventory_batches WHERE item_cd = ? AND store_id = ?",
            ("ITEM_A", STORE_ID),
        )
        statuses = [r[0] for r in cursor.fetchall()]
        assert all(s == BATCH_STATUS_CONSUMED for s in statuses)

    def test_batch_total_exceeds_stock(self, batch_repo, in_memory_db):
        """batch_total > stock이면 FIFO 차감 (오래된 배치부터)"""
        # 오래된 배치: 2/16 입고, 2개
        _insert_batch(in_memory_db, "ITEM_B", remaining_qty=2,
                      receiving_date="2026-02-16", expiry_date="2026-02-17")
        # 새 배치: 2/17 입고, 3개
        _insert_batch(in_memory_db, "ITEM_B", remaining_qty=3,
                      receiving_date="2026-02-17", expiry_date="2026-02-18")
        # stock = 1 → batch_total(5) > stock(1) → 4개 FIFO 차감
        _insert_daily_sales(in_memory_db, "ITEM_B", stock_qty=1)

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["adjusted"] == 1
        assert result["consumed"] >= 1  # 최소 오래된 배치 consumed

        # DB 확인: 오래된 배치(2개)는 consumed, 새 배치에서 2개 차감 → remain=1
        cursor = in_memory_db.cursor()
        cursor.execute(
            """SELECT remaining_qty, status FROM inventory_batches
               WHERE item_cd = ? AND store_id = ?
               ORDER BY receiving_date ASC""",
            ("ITEM_B", STORE_ID),
        )
        rows = cursor.fetchall()
        # 오래된 배치: 2 - 2 = 0 → consumed
        assert rows[0][0] == 0
        assert rows[0][1] == BATCH_STATUS_CONSUMED
        # 새 배치: 3 - 2 = 1 → active
        assert rows[1][0] == 1
        assert rows[1][1] == BATCH_STATUS_ACTIVE

    def test_no_change_when_aligned(self, batch_repo, in_memory_db):
        """이미 정합이면 변경 없음"""
        _insert_batch(in_memory_db, "ITEM_C", remaining_qty=3)
        _insert_daily_sales(in_memory_db, "ITEM_C", stock_qty=5)  # stock >= batch

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["checked"] == 1
        assert result["adjusted"] == 0
        assert result["consumed"] == 0

    def test_multiple_items(self, batch_repo, in_memory_db):
        """여러 상품 동시 보정"""
        # ITEM_D: batch=3, stock=0 → 전부 consumed
        _insert_batch(in_memory_db, "ITEM_D", remaining_qty=3)
        _insert_daily_sales(in_memory_db, "ITEM_D", stock_qty=0)

        # ITEM_E: batch=5, stock=5 → 변경 없음
        _insert_batch(in_memory_db, "ITEM_E", remaining_qty=5)
        _insert_daily_sales(in_memory_db, "ITEM_E", stock_qty=5)

        # ITEM_F: batch=4, stock=2 → 2개 차감
        _insert_batch(in_memory_db, "ITEM_F", remaining_qty=4)
        _insert_daily_sales(in_memory_db, "ITEM_F", stock_qty=2)

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["checked"] == 3
        assert result["adjusted"] == 2  # ITEM_D, ITEM_F

    def test_no_active_batches(self, batch_repo, in_memory_db):
        """active 배치가 없으면 아무것도 안 함"""
        _insert_batch(in_memory_db, "ITEM_G", remaining_qty=0,
                      status=BATCH_STATUS_CONSUMED)
        _insert_daily_sales(in_memory_db, "ITEM_G", stock_qty=0)

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["checked"] == 0
        assert result["adjusted"] == 0

    def test_no_daily_sales_record(self, batch_repo, in_memory_db):
        """daily_sales 레코드가 없으면 stock=0으로 취급하여 consumed"""
        _insert_batch(in_memory_db, "ITEM_H", remaining_qty=2)
        # daily_sales에 ITEM_H 없음

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["adjusted"] == 1
        assert result["consumed"] == 1

    def test_partial_consume(self, batch_repo, in_memory_db):
        """배치 부분 차감 (remaining > 0 유지)"""
        _insert_batch(in_memory_db, "ITEM_I", remaining_qty=5)
        _insert_daily_sales(in_memory_db, "ITEM_I", stock_qty=3)
        # batch=5, stock=3 → 2개 차감, remain=3

        result = batch_repo.sync_remaining_with_stock(STORE_ID)

        assert result["adjusted"] == 1
        assert result["consumed"] == 0  # 부분 차감이므로 consumed 아님

        cursor = in_memory_db.cursor()
        cursor.execute(
            "SELECT remaining_qty, status FROM inventory_batches WHERE item_cd = ?",
            ("ITEM_I",),
        )
        row = cursor.fetchone()
        assert row[0] == 3
        assert row[1] == BATCH_STATUS_ACTIVE


class TestReporterMetrics:
    """검증 보고서 정밀도/재현율 메트릭 테스트"""

    def test_precision_recall_in_comparison_data(self, in_memory_db):
        """get_comparison_data에 precision, recall 포함"""
        from src.report.waste_verification_reporter import (
            WasteVerificationReporter,
        )

        # waste_slip_items, order_tracking, inventory_batches 테이블 생성
        for tbl_sql in [
            """CREATE TABLE IF NOT EXISTS waste_slip_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT, chit_date TEXT, chit_no TEXT, chit_seq INTEGER,
                item_cd TEXT, item_nm TEXT, large_cd TEXT, large_nm TEXT,
                qty INTEGER DEFAULT 0, wonga_price REAL DEFAULT 0,
                wonga_amt REAL DEFAULT 0, maega_price REAL DEFAULT 0,
                maega_amt REAL DEFAULT 0, cust_nm TEXT, center_nm TEXT,
                created_at TEXT, updated_at TEXT,
                UNIQUE(store_id, chit_date, chit_no, item_cd))""",
            """CREATE TABLE IF NOT EXISTS order_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT, item_nm TEXT, mid_cd TEXT, order_date TEXT,
                delivery_type TEXT, order_qty INTEGER DEFAULT 0,
                arrival_time TEXT, expiry_time TEXT, remaining_qty INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ordered', created_at TEXT, updated_at TEXT,
                store_id TEXT DEFAULT '99999')""",
            """CREATE TABLE IF NOT EXISTS inventory_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_cd TEXT, item_nm TEXT, mid_cd TEXT, receiving_date TEXT,
                receiving_id INTEGER, expiration_days INTEGER, expiry_date TEXT,
                initial_qty INTEGER, remaining_qty INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active', created_at TEXT, updated_at TEXT,
                store_id TEXT DEFAULT '99999')""",
        ]:
            in_memory_db.execute(tbl_sql)
        in_memory_db.commit()

        reporter = WasteVerificationReporter(store_id=STORE_ID)
        wrapped = _NoCloseConn(in_memory_db)
        reporter._get_conn = lambda: wrapped
        reporter.slip_repo._get_conn = lambda: wrapped
        reporter.sales_repo._get_conn = lambda: wrapped

        # 전표 품목 2건
        now = datetime.now().isoformat()
        in_memory_db.execute(
            """INSERT INTO waste_slip_items
               (store_id, chit_date, chit_no, item_cd, item_nm, qty,
                wonga_amt, maega_amt, created_at)
               VALUES (?, '2026-02-18', 'C001', 'ITEM_X', 'X상품', 1, 100, 200, ?)""",
            (STORE_ID, now),
        )
        in_memory_db.execute(
            """INSERT INTO waste_slip_items
               (store_id, chit_date, chit_no, item_cd, item_nm, qty,
                wonga_amt, maega_amt, created_at)
               VALUES (?, '2026-02-18', 'C001', 'ITEM_Y', 'Y상품', 1, 100, 200, ?)""",
            (STORE_ID, now),
        )

        # 추적 4건 (daily_sales disuse): ITEM_X(매칭) + ITEM_Z1, Z2, Z3(전표에 없음)
        for item_cd, disuse in [("ITEM_X", 1), ("ITEM_Z1", 1), ("ITEM_Z2", 1), ("ITEM_Z3", 1)]:
            in_memory_db.execute(
                """INSERT INTO daily_sales
                   (store_id, collected_at, sales_date, item_cd, mid_cd,
                    sale_qty, ord_qty, buy_qty, disuse_qty, stock_qty, created_at)
                   VALUES (?, ?, '2026-02-18', ?, '001', 0, 0, 0, ?, 0, ?)""",
                (STORE_ID, now, item_cd, disuse, now),
            )
        in_memory_db.commit()

        data = reporter.get_comparison_data("2026-02-18")

        # 매칭 = 1 (ITEM_X), 전표만 = 1 (ITEM_Y), 추적만 = 3 (Z1,Z2,Z3)
        summary = data["summary"]
        assert summary["matched"] == 1
        assert summary["slip_only"] == 1
        assert summary["tracking_only"] == 3

        # precision = 1/4 = 25% (추적 4건 중 맞춘 게 1건)
        assert summary["precision"] == 25.0
        # recall = 1/2 = 50% (전표 2건 중 추적이 감지한 게 1건)
        assert summary["recall"] == 50.0
