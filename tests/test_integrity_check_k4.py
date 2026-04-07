"""check_expiry_time_mismatch K4 식품 필터 회귀 테스트 (k4-non-food-sentinel-filter)"""

import sqlite3
from unittest.mock import patch

import pytest

from src.infrastructure.database.repos.integrity_check_repo import IntegrityCheckRepository


def _build_common(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE products (item_cd TEXT PRIMARY KEY, item_nm TEXT, mid_cd TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?)",
        [
            ("FOOD001", "도시락테스트", "001"),
            ("FOOD002", "주먹밥테스트", "002"),
            ("FOOD012", "빵테스트", "012"),
            ("NONFOOD072", "담배테스트", "072"),
            ("NONFOOD049", "맥주테스트", "049"),
        ],
    )
    conn.commit()
    conn.close()


def _build_store(path):
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, item_cd TEXT, item_nm TEXT,
            expiry_time TEXT, status TEXT, delivery_type TEXT,
            order_source TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, item_cd TEXT, item_nm TEXT,
            expiry_date TEXT, status TEXT, remaining_qty INTEGER
        )
    """)
    # Test data:
    # FOOD002: OT=2026-04-01, IB=2026-04-15 → diff=14d (식품 7일 초과 → 카운트)
    # FOOD001: OT=2026-04-08, IB=2026-04-13 → diff=5d (식품 7일 미만 → 스킵)
    # NONFOOD072: OT=2053-08-21, IB=2026-04-08 → diff=9997d (비식품 → 식품 필터로 스킵)
    rows_ot = [
        ("TEST", "FOOD002", "주먹밥", "2026-04-01 00:00:00", "active", "1차", "auto"),
        ("TEST", "FOOD001", "도시락", "2026-04-08 00:00:00", "active", "1차", "auto"),
        ("TEST", "NONFOOD072", "담배", "2053-08-21 00:00:00", "active", "1차", "auto"),
    ]
    conn.executemany("INSERT INTO order_tracking (store_id, item_cd, item_nm, expiry_time, status, delivery_type, order_source) VALUES (?,?,?,?,?,?,?)", rows_ot)
    rows_ib = [
        ("TEST", "FOOD002", "주먹밥", "2026-04-15", "active", 1),
        ("TEST", "FOOD001", "도시락", "2026-04-13", "active", 1),
        ("TEST", "NONFOOD072", "담배", "2026-04-08", "active", 1),
    ]
    conn.executemany("INSERT INTO inventory_batches (store_id, item_cd, item_nm, expiry_date, status, remaining_qty) VALUES (?,?,?,?,?,?)", rows_ib)
    conn.commit()
    conn.close()


@pytest.fixture
def fake_dbs(tmp_path):
    common_path = tmp_path / "common.db"
    store_path = tmp_path / "store.db"
    _build_common(common_path)
    _build_store(store_path)
    return common_path, store_path


def _patched_conn(common_path, store_path):
    def _conn(store_id):
        c = sqlite3.connect(str(store_path))
        c.row_factory = sqlite3.Row
        c.execute(f"ATTACH DATABASE '{common_path}' AS common")
        return c
    return _conn


class TestExpiryTimeMismatchK4Filter:
    """check_expiry_time_mismatch 식품 전용 + 7일 임계값 (k4-non-food-sentinel-filter)"""

    def test_food_diff_14days_counted(self, fake_dbs):
        """식품 002 주먹밥 14일 차이 → mismatch 카운트"""
        common, store = fake_dbs
        with patch(
            "src.infrastructure.database.connection.DBRouter.get_store_connection_with_common",
            side_effect=_patched_conn(common, store),
        ):
            repo = IntegrityCheckRepository(store_id="TEST")
            r = repo.check_expiry_time_mismatch("TEST")

        assert r["count"] >= 1
        item_cds = [d["item_cd"] for d in r["details"]]
        assert "FOOD002" in item_cds

    def test_food_diff_5days_skipped(self, fake_dbs):
        """식품 001 도시락 5일 차이 → 7일 임계 미만 → 카운트 안 함"""
        common, store = fake_dbs
        with patch(
            "src.infrastructure.database.connection.DBRouter.get_store_connection_with_common",
            side_effect=_patched_conn(common, store),
        ):
            repo = IntegrityCheckRepository(store_id="TEST")
            r = repo.check_expiry_time_mismatch("TEST")

        item_cds = [d["item_cd"] for d in r["details"]]
        assert "FOOD001" not in item_cds, f"7일 미만은 제외되어야 함: {item_cds}"

    def test_nonfood_huge_diff_skipped(self, fake_dbs):
        """비식품 072 담배 9997일 차이 → 식품 필터로 제외"""
        common, store = fake_dbs
        with patch(
            "src.infrastructure.database.connection.DBRouter.get_store_connection_with_common",
            side_effect=_patched_conn(common, store),
        ):
            repo = IntegrityCheckRepository(store_id="TEST")
            r = repo.check_expiry_time_mismatch("TEST")

        item_cds = [d["item_cd"] for d in r["details"]]
        assert "NONFOOD072" not in item_cds, f"비식품은 K4에서 제외되어야 함: {item_cds}"
