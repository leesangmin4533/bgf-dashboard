"""sync_remaining_with_stock 만료 임박 가드 회귀 테스트
(batch-sync-zero-sales-guard, 2026-04-07)
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.infrastructure.database.repos.inventory_batch_repo import InventoryBatchRepository


def _build_store_db(path):
    """테스트용 매장 DB (inventory_batches + daily_sales)"""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            receiving_date TEXT,
            receiving_id TEXT,
            expiration_days INTEGER,
            expiry_date TEXT,
            initial_qty INTEGER,
            remaining_qty INTEGER,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            delivery_type TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            collected_at TEXT,
            item_cd TEXT NOT NULL,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _add_batch(conn, item_cd, expiry_dt, qty, recv_date="2026-04-06"):
    conn.execute(
        """INSERT INTO inventory_batches
           (store_id, item_cd, item_nm, mid_cd, receiving_date, expiry_date,
            initial_qty, remaining_qty, status, created_at, delivery_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, '2차')""",
        ("TEST", item_cd, f"item_{item_cd}", "001", recv_date,
         expiry_dt, qty, qty, datetime.now().isoformat()),
    )


def _set_stock(conn, item_cd, stock_qty, sales_date="2026-04-07"):
    conn.execute(
        """INSERT INTO daily_sales (store_id, sales_date, item_cd, mid_cd, stock_qty)
           VALUES ('TEST', ?, ?, '001', ?)""",
        (sales_date, item_cd, stock_qty),
    )


@pytest.fixture
def store_db(tmp_path):
    path = tmp_path / "store.db"
    conn = _build_store_db(path)
    yield path, conn
    conn.close()


def _run_sync(store_path):
    """InventoryBatchRepository 인스턴스를 패치된 _get_conn으로 호출"""
    def _get_conn(self=None):
        c = sqlite3.connect(str(store_path))
        c.row_factory = sqlite3.Row
        return c

    with patch.object(InventoryBatchRepository, "_get_conn", _get_conn):
        repo = InventoryBatchRepository(store_id="TEST")
        return repo.sync_remaining_with_stock("TEST")


def _query_batch_status(store_path, item_cd):
    c = sqlite3.connect(str(store_path))
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT id, expiry_date, remaining_qty, status FROM inventory_batches WHERE item_cd=? ORDER BY id",
        (item_cd,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ── 시간 헬퍼 ──
def _hours_from_now(h):
    return (datetime.now() + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")


class TestBatchSyncZeroSalesGuard:
    """sync_remaining_with_stock 만료 임박 가드 (batch-sync-zero-sales-guard)"""

    def test_normal_sale_consumes_batch(self, store_db):
        """1. 정상 판매: batch=1 expiry=+3d, stock=0 → consumed"""
        path, conn = store_db
        _add_batch(conn, "ITEM1", _hours_from_now(72), 1)  # +3d
        _set_stock(conn, "ITEM1", 0)
        conn.commit()

        result = _run_sync(path)

        rows = _query_batch_status(path, "ITEM1")
        assert rows[0]["status"] == "consumed", f"정상 판매는 consumed: {rows}"
        assert rows[0]["remaining_qty"] == 0
        assert result["consumed"] >= 1

    def test_zero_sales_with_imminent_expiry_protected(self, store_db):
        """2. ★ 핵심 회귀: 0판매 + 만료 12h 내 → active 유지 (보호)"""
        path, conn = store_db
        _add_batch(conn, "ITEM2", _hours_from_now(12), 1)  # +12h
        _set_stock(conn, "ITEM2", 0)
        conn.commit()

        _run_sync(path)

        rows = _query_batch_status(path, "ITEM2")
        assert rows[0]["status"] == "active", \
            f"만료 임박 + stock=0 → active 유지되어야: {rows}"
        assert rows[0]["remaining_qty"] == 1

    def test_partial_sale_with_room(self, store_db):
        """3. 부분 판매 + 여유: batch=2 expiry=+3d, stock=1 → 1 consumed"""
        path, conn = store_db
        _add_batch(conn, "ITEM3", _hours_from_now(72), 2)
        _set_stock(conn, "ITEM3", 1)
        conn.commit()

        _run_sync(path)

        rows = _query_batch_status(path, "ITEM3")
        # batch_total(2) - stock(1) = 1 차감
        assert rows[0]["remaining_qty"] == 1

    def test_partial_sale_with_imminent_protected(self, store_db):
        """4. 부분 판매 + 일부 임박: 1개+12h, 1개+3d, stock=1
        → 임박 분 보호, 정상 분만 차감 (정상 1개 → consumed)"""
        path, conn = store_db
        _add_batch(conn, "ITEM4", _hours_from_now(12), 1, recv_date="2026-04-06")  # 임박
        _add_batch(conn, "ITEM4", _hours_from_now(72), 1, recv_date="2026-04-06")  # 정상
        _set_stock(conn, "ITEM4", 1)
        conn.commit()

        _run_sync(path)

        rows = _query_batch_status(path, "ITEM4")
        # 정상 배치(+3d)는 consumed, 임박 배치(+12h)는 active 유지
        imminent = [r for r in rows if "active" == r["status"]]
        consumed = [r for r in rows if "consumed" == r["status"]]
        assert len(imminent) == 1, f"임박 1개 보호: {rows}"
        assert len(consumed) == 1, f"정상 1개 consumed: {rows}"

    def test_zero_stock_with_mixed_batches(self, store_db):
        """5. stock=0 + 임박/정상 혼재: 1개+12h, 1개+3d, stock=0
        → 정상 분 consumed, 임박 분 보호"""
        path, conn = store_db
        _add_batch(conn, "ITEM5", _hours_from_now(12), 1)  # 임박
        _add_batch(conn, "ITEM5", _hours_from_now(72), 1)  # 정상
        _set_stock(conn, "ITEM5", 0)
        conn.commit()

        _run_sync(path)

        rows = _query_batch_status(path, "ITEM5")
        actives = [r for r in rows if r["status"] == "active"]
        assert len(actives) == 1, f"임박만 active 1개 남음: {rows}"
        # 임박이 active로 살아있어야 함
        for r in actives:
            assert r["remaining_qty"] == 1
