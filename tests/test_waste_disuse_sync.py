"""
폐기전표 -> daily_sales.disuse_qty 동기화 테스트

WasteDisuseSyncService + SalesRepository.update_disuse_qty_from_slip()
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.application.services.waste_disuse_sync_service import WasteDisuseSyncService
from src.infrastructure.database.repos import SalesRepository


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sync_db(tmp_path):
    """동기화 테스트용 in-memory DB (store DB)"""
    db_path = str(tmp_path / "test_store.db")
    conn = sqlite3.connect(db_path)

    # daily_sales 테이블
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT DEFAULT '',
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            collected_at TEXT,
            created_at TEXT,
            UNIQUE(store_id, sales_date, item_cd)
        )
    """)

    # waste_slip_items 테이블
    conn.execute("""
        CREATE TABLE waste_slip_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            chit_date TEXT NOT NULL,
            chit_no TEXT NOT NULL,
            chit_seq INTEGER DEFAULT 0,
            item_cd TEXT NOT NULL,
            item_nm TEXT DEFAULT '',
            large_cd TEXT DEFAULT '',
            large_nm TEXT DEFAULT '',
            qty INTEGER DEFAULT 0,
            wonga_price REAL DEFAULT 0,
            wonga_amt REAL DEFAULT 0,
            maega_price REAL DEFAULT 0,
            maega_amt REAL DEFAULT 0,
            cust_nm TEXT DEFAULT '',
            center_nm TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(store_id, chit_date, chit_no, item_cd)
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def _insert_daily_sale(db_path, store_id, date, item_cd, sale_qty=0, disuse_qty=0, mid_cd="002"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO daily_sales (store_id, sales_date, item_cd, mid_cd, sale_qty, disuse_qty, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (store_id, date, item_cd, mid_cd, sale_qty, disuse_qty, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def _insert_slip_item(db_path, store_id, date, chit_no, item_cd, qty, item_nm=""):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO waste_slip_items "
        "(store_id, chit_date, chit_no, item_cd, item_nm, qty, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (store_id, date, chit_no, item_cd, item_nm, qty, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def _get_disuse_qty(db_path, store_id, date, item_cd):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT disuse_qty FROM daily_sales WHERE store_id=? AND sales_date=? AND item_cd=?",
        (store_id, date, item_cd),
    ).fetchone()
    conn.close()
    return row[0] if row else None


# =============================================================================
# SalesRepository.update_disuse_qty_from_slip() 단위 테스트
# =============================================================================

class TestUpdateDisuseQtyFromSlip:
    """SalesRepository.update_disuse_qty_from_slip() 테스트"""

    def test_case_a_update_when_disuse_is_zero(self, sync_db):
        """Case A: disuse_qty=0이고 slip_qty=3 -> UPDATE"""
        store_id = "46513"
        date = "2026-02-20"
        item_cd = "ITEM001"

        _insert_daily_sale(sync_db, store_id, date, item_cd, sale_qty=5, disuse_qty=0)

        repo = SalesRepository(store_id=store_id)
        with patch.object(repo, '_get_conn', return_value=sqlite3.connect(sync_db)):
            result = repo.update_disuse_qty_from_slip(date, item_cd, 3, store_id=store_id)

        assert result == "updated"
        assert _get_disuse_qty(sync_db, store_id, date, item_cd) == 3

    def test_case_b_update_when_disuse_less_than_slip(self, sync_db):
        """Case B: disuse_qty=1이고 slip_qty=3 -> UPDATE (전표가 더 정확)"""
        store_id = "46513"
        date = "2026-02-20"
        item_cd = "ITEM002"

        _insert_daily_sale(sync_db, store_id, date, item_cd, sale_qty=5, disuse_qty=1)

        repo = SalesRepository(store_id=store_id)
        with patch.object(repo, '_get_conn', return_value=sqlite3.connect(sync_db)):
            result = repo.update_disuse_qty_from_slip(date, item_cd, 3, store_id=store_id)

        assert result == "updated"
        assert _get_disuse_qty(sync_db, store_id, date, item_cd) == 3

    def test_case_c_skip_when_disuse_already_correct(self, sync_db):
        """Case C: disuse_qty=5이고 slip_qty=3 -> SKIP"""
        store_id = "46513"
        date = "2026-02-20"
        item_cd = "ITEM003"

        _insert_daily_sale(sync_db, store_id, date, item_cd, sale_qty=5, disuse_qty=5)

        repo = SalesRepository(store_id=store_id)
        with patch.object(repo, '_get_conn', return_value=sqlite3.connect(sync_db)):
            result = repo.update_disuse_qty_from_slip(date, item_cd, 3, store_id=store_id)

        assert result == "skipped"
        # 기존 값 유지
        assert _get_disuse_qty(sync_db, store_id, date, item_cd) == 5

    def test_case_d_insert_when_no_row(self, sync_db):
        """Case D: daily_sales 행 없음 -> INSERT"""
        store_id = "46513"
        date = "2026-02-20"
        item_cd = "ITEM_NEW"

        repo = SalesRepository(store_id=store_id)
        with patch.object(repo, '_get_conn', return_value=sqlite3.connect(sync_db)):
            result = repo.update_disuse_qty_from_slip(
                date, item_cd, 2, mid_cd="002", store_id=store_id
            )

        assert result == "inserted"
        assert _get_disuse_qty(sync_db, store_id, date, item_cd) == 2

    def test_skip_when_disuse_equals_slip(self, sync_db):
        """disuse_qty == slip_qty -> SKIP (이미 정확)"""
        store_id = "46513"
        date = "2026-02-20"
        item_cd = "ITEM_EQUAL"

        _insert_daily_sale(sync_db, store_id, date, item_cd, sale_qty=3, disuse_qty=3)

        repo = SalesRepository(store_id=store_id)
        with patch.object(repo, '_get_conn', return_value=sqlite3.connect(sync_db)):
            result = repo.update_disuse_qty_from_slip(date, item_cd, 3, store_id=store_id)

        assert result == "skipped"


# =============================================================================
# WasteDisuseSyncService 통합 테스트
# =============================================================================

class TestWasteDisuseSyncService:
    """WasteDisuseSyncService 통합 테스트"""

    def test_sync_date_empty_slip(self, sync_db):
        """Case E: waste_slip_items가 없는 날 -> 변경 없음"""
        store_id = "46513"

        syncer = WasteDisuseSyncService(store_id=store_id)

        with patch.object(syncer.slip_repo, 'get_waste_slip_items_summary', return_value=[]):
            stats = syncer.sync_date("2026-02-20")

        assert stats["updated"] == 0
        assert stats["inserted"] == 0
        assert stats["skipped"] == 0
        assert stats["total"] == 0

    def test_sync_date_multiple_slips_same_item(self, sync_db):
        """Case F: 여러 전표에 같은 item_cd -> 합산 후 1회 업데이트"""
        store_id = "46513"
        date = "2026-02-20"
        item_cd = "ITEM_MULTI"

        _insert_daily_sale(sync_db, store_id, date, item_cd, sale_qty=10, disuse_qty=0)

        # slip_items_summary는 이미 item_cd별 합산된 결과 반환
        mock_summary = [
            {"item_cd": item_cd, "item_nm": "test", "total_qty": 5, "mid_cd": "002"},
        ]

        syncer = WasteDisuseSyncService(store_id=store_id)

        with patch.object(syncer.slip_repo, 'get_waste_slip_items_summary', return_value=mock_summary), \
             patch.object(syncer.sales_repo, '_get_conn', side_effect=lambda: sqlite3.connect(sync_db)):
            stats = syncer.sync_date(date)

        assert stats["updated"] == 1
        assert stats["total"] == 1
        assert _get_disuse_qty(sync_db, store_id, date, item_cd) == 5

    def test_sync_date_mixed_results(self, sync_db):
        """여러 상품이 섞인 경우: update + skip + insert"""
        store_id = "46513"
        date = "2026-02-20"

        # 기존 daily_sales 데이터
        _insert_daily_sale(sync_db, store_id, date, "ITEM_UPD", sale_qty=5, disuse_qty=0)
        _insert_daily_sale(sync_db, store_id, date, "ITEM_SKIP", sale_qty=5, disuse_qty=10)

        mock_summary = [
            {"item_cd": "ITEM_UPD", "item_nm": "update", "total_qty": 3, "mid_cd": "002"},
            {"item_cd": "ITEM_SKIP", "item_nm": "skip", "total_qty": 2, "mid_cd": "002"},
            {"item_cd": "ITEM_NEW", "item_nm": "insert", "total_qty": 1, "mid_cd": "002"},
        ]

        syncer = WasteDisuseSyncService(store_id=store_id)

        # side_effect로 매 호출마다 새 커넥션 생성 (각 호출의 commit 반영)
        with patch.object(syncer.slip_repo, 'get_waste_slip_items_summary', return_value=mock_summary), \
             patch.object(syncer.sales_repo, '_get_conn', side_effect=lambda: sqlite3.connect(sync_db)):
            stats = syncer.sync_date(date)

        assert stats["updated"] == 1
        assert stats["skipped"] == 1
        assert stats["inserted"] == 1
        assert stats["total"] == 3

    def test_backfill_multiple_days(self):
        """Case G: backfill 호출 시 날짜 범위 처리"""
        syncer = WasteDisuseSyncService(store_id="46513")

        call_dates = []

        def mock_sync_date(target_date):
            call_dates.append(target_date)
            return {"date": target_date, "updated": 1, "inserted": 0, "skipped": 0, "total": 1}

        with patch.object(syncer, 'sync_date', side_effect=mock_sync_date):
            result = syncer.backfill(days=3)

        assert result["days_processed"] == 3
        assert result["total_updated"] == 3
        assert len(call_dates) == 3

    def test_store_isolation(self, sync_db):
        """Case H: store_id별 독립 동기화"""
        date = "2026-02-20"
        item_cd = "ITEM_ISO"

        # 두 매장 데이터
        _insert_daily_sale(sync_db, "46513", date, item_cd, sale_qty=5, disuse_qty=0)
        _insert_daily_sale(sync_db, "46704", date, item_cd, sale_qty=3, disuse_qty=0)

        mock_summary = [
            {"item_cd": item_cd, "item_nm": "test", "total_qty": 2, "mid_cd": "002"},
        ]

        # 46513만 동기화
        syncer = WasteDisuseSyncService(store_id="46513")
        with patch.object(syncer.slip_repo, 'get_waste_slip_items_summary', return_value=mock_summary), \
             patch.object(syncer.sales_repo, '_get_conn', side_effect=lambda: sqlite3.connect(sync_db)):
            syncer.sync_date(date)

        # 46513은 업데이트됨
        assert _get_disuse_qty(sync_db, "46513", date, item_cd) == 2
        # 46704는 변경 없음
        assert _get_disuse_qty(sync_db, "46704", date, item_cd) == 0
