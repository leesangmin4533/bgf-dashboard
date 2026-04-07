"""슬롯 기반 폐기 추적 검증 회귀 테스트
(waste-verification-slot-based, 2026-04-07)
"""

import sqlite3
from unittest.mock import patch

import pytest

from src.report.waste_verification_reporter import WasteVerificationReporter


def _build_store_db(path):
    """waste_slips + waste_slip_items + inventory_batches 최소 스키마"""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE waste_slips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            chit_date TEXT NOT NULL,
            chit_no TEXT NOT NULL,
            cre_ymdhms TEXT,
            created_at TEXT,
            UNIQUE(store_id, chit_date, chit_no)
        );
        CREATE TABLE waste_slip_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            chit_date TEXT NOT NULL,
            chit_no TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            qty INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            expiry_date TEXT,
            initial_qty INTEGER,
            remaining_qty INTEGER,
            status TEXT,
            created_at TEXT
        );
    """)
    return conn


def _add_slip(conn, chit_no, item_cd, qty, cre_ymdhms, chit_date="2026-04-07"):
    conn.execute(
        "INSERT INTO waste_slips (store_id, chit_date, chit_no, cre_ymdhms, created_at) VALUES ('TEST', ?, ?, ?, '2026-04-07T14:00:00')",
        (chit_date, chit_no, cre_ymdhms),
    )
    conn.execute(
        "INSERT INTO waste_slip_items (store_id, chit_date, chit_no, item_cd, item_nm, qty) VALUES ('TEST', ?, ?, ?, ?, ?)",
        (chit_date, chit_no, item_cd, f"name_{item_cd}", qty),
    )


def _add_batch(conn, item_cd, expiry_date, status):
    conn.execute(
        "INSERT INTO inventory_batches (store_id, item_cd, item_nm, expiry_date, initial_qty, remaining_qty, status) VALUES ('TEST', ?, ?, ?, 1, 0, ?)",
        (item_cd, f"name_{item_cd}", expiry_date, status),
    )


@pytest.fixture
def store_db(tmp_path):
    path = tmp_path / "store.db"
    conn = _build_store_db(path)
    yield path, conn
    conn.close()


def _run(store_path, target_date="2026-04-07"):
    from src.infrastructure.database.repos import SalesRepository

    def _get_conn(self):
        c = sqlite3.connect(str(store_path))
        c.row_factory = sqlite3.Row
        return c

    with patch.object(SalesRepository, "_get_conn", _get_conn):
        reporter = WasteVerificationReporter(store_id="TEST")
        return reporter.get_slot_comparison_data(target_date)


class TestSlotClassification:
    """_classify_slot 단위 테스트"""

    def test_slot_2am_window(self):
        # 02:00~13:59 → slot_2am
        assert WasteVerificationReporter._classify_slot("20260407020000") == "slot_2am"
        assert WasteVerificationReporter._classify_slot("20260407034306") == "slot_2am"
        assert WasteVerificationReporter._classify_slot("20260407135959") == "slot_2am"

    def test_slot_2pm_window(self):
        # 14:00~01:59 → slot_2pm
        assert WasteVerificationReporter._classify_slot("20260407140000") == "slot_2pm"
        assert WasteVerificationReporter._classify_slot("20260407230000") == "slot_2pm"
        assert WasteVerificationReporter._classify_slot("20260407000000") == "slot_2pm"
        assert WasteVerificationReporter._classify_slot("20260407015959") == "slot_2pm"

    def test_unclassified_invalid(self):
        assert WasteVerificationReporter._classify_slot(None) == "unclassified"
        assert WasteVerificationReporter._classify_slot("") == "unclassified"
        assert WasteVerificationReporter._classify_slot("invalid") == "unclassified"
        assert WasteVerificationReporter._classify_slot("2026") == "unclassified"


class TestSlotComparison:
    """get_slot_comparison_data 통합 테스트"""

    def test_slot_2am_matched(self, store_db):
        """1. 02:00 만료 추적 + 새벽 3시 BGF 입력 → matched"""
        path, conn = store_db
        _add_batch(conn, "ITEM_A", "2026-04-07 02:00:00", "expired")
        _add_slip(conn, "C001", "ITEM_A", 1, "20260407034306")
        conn.commit()

        r = _run(path)
        assert r["slot_2am"]["matched"] == 1
        assert r["slot_2am"]["slip_only"] == 0
        assert r["slot_2am"]["tracking_only"] == 0
        assert r["slot_2am"]["match_rate"] == 100.0

    def test_slot_2pm_matched(self, store_db):
        """2. 14:00 만료 추적 + 15시 BGF 입력 → matched"""
        path, conn = store_db
        _add_batch(conn, "ITEM_B", "2026-04-07 14:00:00", "expired")
        _add_slip(conn, "C002", "ITEM_B", 1, "20260407150000")
        conn.commit()

        r = _run(path)
        assert r["slot_2pm"]["matched"] == 1
        assert r["slot_2pm"]["match_rate"] == 100.0

    def test_slot_2am_tracking_only(self, store_db):
        """3. 추적은 있는데 BGF 폐기 입력 없음 → tracking_only"""
        path, conn = store_db
        _add_batch(conn, "ITEM_C", "2026-04-07 02:00:00", "expired")
        conn.commit()

        r = _run(path)
        assert r["slot_2am"]["tracking_only"] == 1
        assert r["slot_2am"]["matched"] == 0
        assert r["slot_2am"]["match_rate"] == 0.0
        assert r["summary"]["false_positive"] == 1

    def test_slot_2pm_late_night_window(self, store_db):
        """4. 14시 만료 + 다음날 새벽 1시 BGF 입력 → slot_2pm 매칭"""
        path, conn = store_db
        _add_batch(conn, "ITEM_D", "2026-04-07 14:00:00", "expired")
        # 04-08 01:30 입력 → 14:00 슬롯 윈도우 끝
        _add_slip(conn, "C003", "ITEM_D", 1, "20260408013000", chit_date="2026-04-07")
        conn.commit()

        r = _run(path)
        assert r["slot_2pm"]["matched"] == 1, f"새벽 윈도우 매칭 실패: {r}"

    def test_consumed_in_tracking_base(self, store_db):
        """5. 사각지대 해소: tracking에 consumed 포함되어 매칭"""
        path, conn = store_db
        # 04-07 사건 재현: consumed로 잘못 마킹된 만료 임박 배치
        _add_batch(conn, "ITEM_E", "2026-04-07 14:00:00", "consumed")
        _add_slip(conn, "C004", "ITEM_E", 1, "20260407150000")
        conn.commit()

        r = _run(path)
        # consumed도 검증 base에 포함되어야 매칭
        assert r["slot_2pm"]["tracking_base"] == 1, \
            f"consumed가 base에 포함되어야 함: {r}"
        assert r["slot_2pm"]["matched"] == 1, \
            f"consumed + slip 매칭되어야 함: {r}"
