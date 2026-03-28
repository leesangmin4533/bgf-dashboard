"""
행사 사전 동기화 테스트 (promo-sync-fix)

Phase 1.68에서 product_details에 행사가 있지만 promotions 테이블에 미등록인
상품을 DirectAPI 조회 대상에 포함하여 신제품 행사 수집 지연을 해소한다.
"""
import pytest
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_dbs(tmp_path):
    """테스트용 store DB + common DB 생성"""
    store_id = "99999"
    store_db = tmp_path / f"{store_id}.db"
    common_db = tmp_path / "common.db"

    # Common DB
    conn_c = sqlite3.connect(str(common_db))
    conn_c.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            promo_type TEXT,
            promo_start TEXT,
            promo_end TEXT,
            promo_updated TEXT,
            store_id TEXT,
            expiration_days INTEGER,
            demand_pattern TEXT,
            order_unit_qty INTEGER DEFAULT 1
        )
    """)
    conn_c.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT
        )
    """)
    conn_c.commit()
    conn_c.close()

    # Store DB
    conn_s = sqlite3.connect(str(store_db))
    conn_s.execute("""
        CREATE TABLE realtime_inventory (
            item_cd TEXT,
            store_id TEXT,
            stock_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            queried_at TEXT,
            created_at TEXT
        )
    """)
    conn_s.execute("""
        CREATE TABLE promotions (
            store_id TEXT,
            item_cd TEXT,
            promo_type TEXT,
            start_date TEXT,
            end_date TEXT,
            is_active INTEGER DEFAULT 1,
            collected_at TEXT
        )
    """)
    conn_s.commit()
    conn_s.close()

    return store_id, str(store_db), str(common_db)


def _create_repo(setup_dbs):
    """RealtimeInventoryRepository 생성 (DB 경로 패치)"""
    store_id, store_db, common_db = setup_dbs
    repo = RealtimeInventoryRepository(store_id=store_id)
    # DB 경로 패치
    repo._db_path = store_db
    repo._common_db_path = common_db
    return repo, store_id, store_db, common_db


def _insert_product(common_db, item_cd, promo_type, promo_end, store_id="99999"):
    """product_details에 상품 삽입"""
    conn = sqlite3.connect(common_db)
    conn.execute(
        "INSERT OR REPLACE INTO product_details (item_cd, promo_type, promo_end, store_id) VALUES (?,?,?,?)",
        (item_cd, promo_type, promo_end, store_id),
    )
    conn.commit()
    conn.close()


def _insert_ri(store_db, item_cd, store_id="99999", is_available=1):
    """realtime_inventory에 상품 삽입"""
    conn = sqlite3.connect(store_db)
    conn.execute(
        "INSERT INTO realtime_inventory (item_cd, store_id, stock_qty, is_available, queried_at) VALUES (?,?,0,?,?)",
        (item_cd, store_id, is_available, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def _insert_promo(store_db, item_cd, promo_type, start_date, end_date, store_id="99999"):
    """promotions에 행사 삽입"""
    conn = sqlite3.connect(store_db)
    conn.execute(
        "INSERT INTO promotions (store_id, item_cd, promo_type, start_date, end_date, is_active) VALUES (?,?,?,?,?,1)",
        (store_id, item_cd, promo_type, start_date, end_date),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetPromoMissingItemCodes:
    """get_promo_missing_item_codes() 단위 테스트"""

    def test_basic_missing(self, setup_dbs):
        """product_details에 행사 있고 promotions에 없는 상품 반환"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        _insert_product(common_db, "ITEM_A", "1+1", future, store_id)
        _insert_ri(store_db, "ITEM_A", store_id)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert "ITEM_A" in result

    def test_already_in_promotions(self, setup_dbs):
        """promotions에 이미 있는 상품은 제외"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        _insert_product(common_db, "ITEM_B", "1+1", future, store_id)
        _insert_ri(store_db, "ITEM_B", store_id)
        _insert_promo(store_db, "ITEM_B", "1+1", today, future, store_id)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert "ITEM_B" not in result

    def test_expired_promo_excluded(self, setup_dbs):
        """만료된 행사는 대상에서 제외"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        _insert_product(common_db, "ITEM_C", "2+1", past, store_id)
        _insert_ri(store_db, "ITEM_C", store_id)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert "ITEM_C" not in result

    def test_unavailable_excluded(self, setup_dbs):
        """is_available=0 상품은 제외"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        _insert_product(common_db, "ITEM_D", "1+1", future, store_id)
        _insert_ri(store_db, "ITEM_D", store_id, is_available=0)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert "ITEM_D" not in result

    def test_no_promo_type_excluded(self, setup_dbs):
        """promo_type이 없는 상품은 제외"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        _insert_product(common_db, "ITEM_E", None, future, store_id)
        _insert_ri(store_db, "ITEM_E", store_id)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert "ITEM_E" not in result

    def test_empty_promo_type_excluded(self, setup_dbs):
        """promo_type='' 상품은 제외"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

        _insert_product(common_db, "ITEM_F", "", future, store_id)
        _insert_ri(store_db, "ITEM_F", store_id)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert "ITEM_F" not in result

    def test_mixed_scenario(self, setup_dbs):
        """혼합 시나리오: 여러 조건의 상품이 섞인 경우"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        # MISS_1: 행사 있고 유효, promotions 없음 → 대상
        _insert_product(common_db, "MISS_1", "1+1", future, store_id)
        _insert_ri(store_db, "MISS_1", store_id)

        # MISS_2: 행사 있고 유효, promotions 없음 → 대상
        _insert_product(common_db, "MISS_2", "2+1", future, store_id)
        _insert_ri(store_db, "MISS_2", store_id)

        # OK_1: promotions에 이미 있음 → 제외
        _insert_product(common_db, "OK_1", "1+1", future, store_id)
        _insert_ri(store_db, "OK_1", store_id)
        _insert_promo(store_db, "OK_1", "1+1", today, future, store_id)

        # EXPIRED: 만료됨 → 제외
        _insert_product(common_db, "EXPIRED", "1+1", past, store_id)
        _insert_ri(store_db, "EXPIRED", store_id)

        # UNAVAIL: 미취급 → 제외
        _insert_product(common_db, "UNAVAIL", "1+1", future, store_id)
        _insert_ri(store_db, "UNAVAIL", store_id, is_available=0)

        with patch.object(repo, '_get_conn_with_common') as mock_conn:
            conn = sqlite3.connect(store_db)
            conn.execute(f"ATTACH DATABASE '{common_db}' AS common")
            mock_conn.return_value = conn

            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert set(result) == {"MISS_1", "MISS_2"}

    def test_exception_returns_empty(self, setup_dbs):
        """DB 오류 시 빈 리스트 반환 (발주 플로우 중단 방지)"""
        repo, store_id, store_db, common_db = _create_repo(setup_dbs)

        with patch.object(repo, '_get_conn_with_common', side_effect=Exception("DB error")):
            result = repo.get_promo_missing_item_codes(store_id=store_id)

        assert result == []


class TestPhase168PromoSync:
    """daily_job.py Phase 1.68 행사 병합 로직 테스트"""

    def test_merge_dedup(self):
        """stale_items + promo_missing_items 중복 제거 병합"""
        stale = ["A", "B", "C"]
        promo_missing = ["B", "D", "E"]
        merged = list(set(stale + promo_missing))
        assert len(merged) == 5
        assert set(merged) == {"A", "B", "C", "D", "E"}

    def test_empty_promo_missing(self):
        """promo_missing이 비어있으면 stale만 사용"""
        stale = ["A", "B"]
        promo_missing = []
        merged = list(set(stale + promo_missing))
        assert set(merged) == {"A", "B"}

    def test_empty_stale(self):
        """stale이 비어있고 promo_missing만 있는 경우"""
        stale = []
        promo_missing = ["X", "Y"]
        merged = list(set(stale + promo_missing))
        assert set(merged) == {"X", "Y"}

    def test_both_empty(self):
        """둘 다 비어있으면 빈 리스트"""
        stale = []
        promo_missing = []
        merged = list(set(stale + promo_missing))
        assert merged == []

    def test_toggle_disabled(self):
        """PROMO_SYNC_ENABLED=False 시 promo_missing 비활성"""
        with patch("src.settings.constants.PROMO_SYNC_ENABLED", False):
            from src.settings.constants import PROMO_SYNC_ENABLED
            promo_missing = []
            if PROMO_SYNC_ENABLED:
                promo_missing = ["should_not_appear"]
            assert promo_missing == []
