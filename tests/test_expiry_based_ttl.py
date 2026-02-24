"""
유통기한 기반 재고 TTL 시스템 테스트

- _get_stale_hours_for_expiry(): 유통기한 -> stale_hours 변환
- _is_stale(): 상품별 TTL 판정
- cleanup_stale_stock(): 카테고리별 TTL 적용 정리
- decrement_stock(): 폐기/판매 재고 차감
- WasteDisuseSyncService: 폐기 시 realtime_inventory 차감
"""

import sqlite3
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository


class NonClosingConnection:
    """sqlite3.Connection 래퍼 — close()를 noop으로 만들어 테스트 후 검증 가능"""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass  # noop

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ===========================================================================
# _get_stale_hours_for_expiry 단위 테스트
# ===========================================================================
class TestGetStaleHoursForExpiry:
    """유통기한 -> stale_hours 변환 테스트"""

    def test_expiry_1day_returns_18h(self):
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(1) == 18

    def test_expiry_2day_returns_36h(self):
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(2) == 36

    def test_expiry_3day_returns_54h(self):
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(3) == 54

    def test_expiry_4day_returns_default_36h(self):
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(4) == 36

    def test_expiry_7day_returns_default_36h(self):
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(7) == 36

    def test_expiry_none_with_mid_cd_001(self):
        """product_details에 없을 때 카테고리 폴백 (도시락=1일->18h)"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '001') == 18

    def test_expiry_none_with_mid_cd_002(self):
        """주먹밥=1일->18h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '002') == 18

    def test_expiry_none_with_mid_cd_003(self):
        """김밥=1일->18h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '003') == 18

    def test_expiry_none_with_mid_cd_004(self):
        """샌드위치=2일->36h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '004') == 36

    def test_expiry_none_with_mid_cd_005(self):
        """햄버거=3일->54h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '005') == 54

    def test_expiry_none_with_mid_cd_012(self):
        """빵=3일->54h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '012') == 54

    def test_expiry_none_no_mid_cd(self):
        """유통기한 없고 mid_cd도 없으면 기본 36h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None) == 36

    def test_expiry_none_unknown_mid_cd(self):
        """알 수 없는 mid_cd -> 기본 36h"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(None, '999') == 36

    def test_expiry_zero_falls_to_mid_cd(self):
        """expiry_days=0은 유효하지 않으므로 mid_cd 폴백"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(0, '001') == 18

    def test_expiry_negative_falls_to_mid_cd(self):
        """음수 expiry_days는 mid_cd 폴백"""
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(-1, '004') == 36

    def test_db_value_overrides_mid_cd(self):
        """DB 값이 있으면 mid_cd 무시"""
        # 도시락(001)이지만 DB에서 3일 유통으로 나오면 54h
        assert RealtimeInventoryRepository._get_stale_hours_for_expiry(3, '001') == 54


# ===========================================================================
# _is_stale 테스트
# ===========================================================================
class TestIsStale:
    """유통기한 기반 staleness 판정 테스트"""

    def setup_method(self):
        self.repo = RealtimeInventoryRepository.__new__(RealtimeInventoryRepository)

    def test_none_queried_at_is_stale(self):
        assert self.repo._is_stale(None) is True

    def test_empty_queried_at_is_stale(self):
        assert self.repo._is_stale("") is True

    def test_recent_query_not_stale(self):
        recent = (datetime.now() - timedelta(hours=1)).isoformat()
        assert self.repo._is_stale(recent) is False

    def test_old_query_default_stale(self):
        old = (datetime.now() - timedelta(hours=37)).isoformat()
        assert self.repo._is_stale(old) is True

    def test_1day_expiry_stale_after_18h(self):
        """유통기한 1일: 18h 초과면 stale"""
        at_19h = (datetime.now() - timedelta(hours=19)).isoformat()
        assert self.repo._is_stale(at_19h, expiry_days=1) is True

    def test_1day_expiry_fresh_within_18h(self):
        """유통기한 1일: 17h면 fresh"""
        at_17h = (datetime.now() - timedelta(hours=17)).isoformat()
        assert self.repo._is_stale(at_17h, expiry_days=1) is False

    def test_2day_expiry_stale_after_36h(self):
        at_37h = (datetime.now() - timedelta(hours=37)).isoformat()
        assert self.repo._is_stale(at_37h, expiry_days=2) is True

    def test_2day_expiry_fresh_within_36h(self):
        at_35h = (datetime.now() - timedelta(hours=35)).isoformat()
        assert self.repo._is_stale(at_35h, expiry_days=2) is False

    def test_mid_cd_fallback_for_dosirak(self):
        """expiry_days 없이 mid_cd='001' -> 18h TTL"""
        at_19h = (datetime.now() - timedelta(hours=19)).isoformat()
        assert self.repo._is_stale(at_19h, mid_cd='001') is True
        at_17h = (datetime.now() - timedelta(hours=17)).isoformat()
        assert self.repo._is_stale(at_17h, mid_cd='001') is False


# ===========================================================================
# cleanup_stale_stock 통합 테스트
# ===========================================================================
class TestCleanupStaleStock:
    """카테고리별 TTL 적용 유령 재고 정리 테스트"""

    def setup_method(self):
        self.repo = RealtimeInventoryRepository.__new__(RealtimeInventoryRepository)
        self.repo.store_id = None
        self.repo._db_path = ":memory:"

    def _setup_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE realtime_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT,
                item_cd TEXT NOT NULL,
                item_nm TEXT,
                stock_qty INTEGER DEFAULT 0,
                pending_qty INTEGER DEFAULT 0,
                order_unit_qty INTEGER DEFAULT 1,
                is_available INTEGER DEFAULT 1,
                is_cut_item INTEGER DEFAULT 0,
                queried_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(store_id, item_cd)
            )
        """)
        conn.execute("""
            CREATE TABLE products (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                mid_cd TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE product_details (
                item_cd TEXT PRIMARY KEY,
                item_nm TEXT,
                expiration_days INTEGER
            )
        """)
        return conn

    def _run_cleanup(self, conn):
        """cleanup_stale_stock 실행 (conn.close 방지)"""
        wrapped = NonClosingConnection(conn)
        with patch.object(RealtimeInventoryRepository, '_get_conn_with_common', return_value=wrapped):
            with patch.object(RealtimeInventoryRepository, '_get_conn', return_value=wrapped):
                return self.repo.cleanup_stale_stock()

    def test_1day_item_cleaned_after_18h(self):
        """유통기한 1일 상품: 19h 경과 시 클린업"""
        conn = self._setup_db()
        at_19h = (datetime.now() - timedelta(hours=19)).isoformat()
        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'ITEM1','도시락',5,0,1,1,0,?,?)",
            (at_19h, at_19h)
        )
        conn.execute("INSERT INTO products VALUES ('ITEM1','도시락','001')")
        conn.execute("INSERT INTO product_details VALUES ('ITEM1','도시락',1)")
        conn.commit()

        result = self._run_cleanup(conn)
        assert result["cleaned"] == 1
        assert result["total_ghost_stock"] == 5

    def test_2day_item_not_cleaned_at_19h(self):
        """유통기한 2일 상품: 19h 경과 시 아직 클린업 안 됨"""
        conn = self._setup_db()
        at_19h = (datetime.now() - timedelta(hours=19)).isoformat()
        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'ITEM2','샌드위치',3,0,1,1,0,?,?)",
            (at_19h, at_19h)
        )
        conn.execute("INSERT INTO products VALUES ('ITEM2','샌드위치','004')")
        conn.execute("INSERT INTO product_details VALUES ('ITEM2','샌드위치',2)")
        conn.commit()

        result = self._run_cleanup(conn)
        assert result["cleaned"] == 0

    def test_mixed_categories_selective_cleanup(self):
        """서로 다른 유통기한의 상품: 카테고리별로 선택 정리"""
        conn = self._setup_db()
        at_20h = (datetime.now() - timedelta(hours=20)).isoformat()

        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'ITEM_1D','도시락',5,0,1,1,0,?,?)",
            (at_20h, at_20h)
        )
        conn.execute("INSERT INTO products VALUES ('ITEM_1D','도시락','001')")
        conn.execute("INSERT INTO product_details VALUES ('ITEM_1D','도시락',1)")

        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'ITEM_2D','샌드위치',3,0,1,1,0,?,?)",
            (at_20h, at_20h)
        )
        conn.execute("INSERT INTO products VALUES ('ITEM_2D','샌드위치','004')")
        conn.execute("INSERT INTO product_details VALUES ('ITEM_2D','샌드위치',2)")

        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'ITEM_7D','음료',10,0,1,1,0,?,?)",
            (at_20h, at_20h)
        )
        conn.execute("INSERT INTO products VALUES ('ITEM_7D','음료','100')")
        conn.execute("INSERT INTO product_details VALUES ('ITEM_7D','음료',7)")
        conn.commit()

        result = self._run_cleanup(conn)

        assert result["cleaned"] == 1
        assert result["total_ghost_stock"] == 5

        row = conn.execute("SELECT stock_qty FROM realtime_inventory WHERE item_cd='ITEM_1D'").fetchone()
        assert row[0] == 0
        row = conn.execute("SELECT stock_qty FROM realtime_inventory WHERE item_cd='ITEM_2D'").fetchone()
        assert row[0] == 3
        row = conn.execute("SELECT stock_qty FROM realtime_inventory WHERE item_cd='ITEM_7D'").fetchone()
        assert row[0] == 10

    def test_no_product_info_uses_default_ttl(self):
        """products/product_details에 없는 상품은 기본 36h TTL"""
        conn = self._setup_db()
        at_20h = (datetime.now() - timedelta(hours=20)).isoformat()

        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'UNKNOWN','미지상품',5,0,1,1,0,?,?)",
            (at_20h, at_20h)
        )
        conn.commit()

        result = self._run_cleanup(conn)
        assert result["cleaned"] == 0

    def test_mid_cd_fallback_when_no_expiration_days(self):
        """product_details.expiration_days 없을 때 mid_cd 폴백"""
        conn = self._setup_db()
        at_20h = (datetime.now() - timedelta(hours=20)).isoformat()

        conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,NULL,'ITEM_FB','김밥',4,0,1,1,0,?,?)",
            (at_20h, at_20h)
        )
        conn.execute("INSERT INTO products VALUES ('ITEM_FB','김밥','003')")
        conn.commit()

        result = self._run_cleanup(conn)
        assert result["cleaned"] == 1


# ===========================================================================
# decrement_stock 테스트
# ===========================================================================
class TestDecrementStock:
    """폐기/판매 재고 차감 테스트"""

    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE realtime_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT,
                item_cd TEXT NOT NULL,
                item_nm TEXT,
                stock_qty INTEGER DEFAULT 0,
                pending_qty INTEGER DEFAULT 0,
                order_unit_qty INTEGER DEFAULT 1,
                is_available INTEGER DEFAULT 1,
                is_cut_item INTEGER DEFAULT 0,
                queried_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(store_id, item_cd)
            )
        """)
        now = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO realtime_inventory VALUES (NULL,'46513','ITEM1','테스트',10,5,1,1,0,?,?)",
            (now, now)
        )
        self.conn.commit()

        self.repo = RealtimeInventoryRepository.__new__(RealtimeInventoryRepository)
        self.repo.store_id = "46513"
        self.repo._db_path = ":memory:"

    def _run_decrement(self, item_cd, qty, store_id):
        wrapped = NonClosingConnection(self.conn)
        with patch.object(RealtimeInventoryRepository, '_get_conn', return_value=wrapped):
            return self.repo.decrement_stock(item_cd, qty, store_id)

    def test_decrement_reduces_stock(self):
        result = self._run_decrement("ITEM1", 3, "46513")
        assert result is True
        row = self.conn.execute("SELECT stock_qty FROM realtime_inventory WHERE item_cd='ITEM1'").fetchone()
        assert row[0] == 7

    def test_decrement_cannot_go_below_zero(self):
        result = self._run_decrement("ITEM1", 15, "46513")
        assert result is True
        row = self.conn.execute("SELECT stock_qty FROM realtime_inventory WHERE item_cd='ITEM1'").fetchone()
        assert row[0] == 0

    def test_decrement_nonexistent_item(self):
        result = self._run_decrement("NOITEM", 5, "46513")
        assert result is False

    def test_decrement_zero_qty_returns_false(self):
        result = self._run_decrement("ITEM1", 0, "46513")
        assert result is False

    def test_decrement_negative_qty_returns_false(self):
        result = self._run_decrement("ITEM1", -5, "46513")
        assert result is False


# ===========================================================================
# WasteDisuseSyncService 재고 차감 통합 테스트
# ===========================================================================
class TestWasteDisuseSyncStockDecrement:
    """폐기 동기화 시 realtime_inventory 재고 차감 테스트"""

    def test_sync_date_decrements_stock(self):
        from src.application.services.waste_disuse_sync_service import WasteDisuseSyncService

        service = WasteDisuseSyncService(store_id="46513")

        # mock slip_repo
        service.slip_repo = MagicMock()
        service.slip_repo.get_waste_slip_items_summary.return_value = [
            {"item_cd": "ITEM1", "total_qty": 3, "mid_cd": "001"},
        ]

        # mock sales_repo
        service.sales_repo = MagicMock()
        service.sales_repo.update_disuse_qty_from_slip.return_value = "updated"

        # mock inventory_repo
        service.inventory_repo = MagicMock()
        service.inventory_repo.decrement_stock.return_value = True

        stats = service.sync_date("2026-02-21")

        assert stats["total"] == 1
        assert stats["updated"] == 1
        assert stats["stock_decremented"] == 1

        # decrement_stock이 올바른 인자로 호출되었는지
        service.inventory_repo.decrement_stock.assert_called_once_with(
            item_cd="ITEM1",
            qty=3,
            store_id="46513",
        )

    def test_sync_date_decrement_failure_does_not_block(self):
        """재고 차감 실패해도 동기화는 계속"""
        from src.application.services.waste_disuse_sync_service import WasteDisuseSyncService

        service = WasteDisuseSyncService(store_id="46513")

        service.slip_repo = MagicMock()
        service.slip_repo.get_waste_slip_items_summary.return_value = [
            {"item_cd": "ITEM1", "total_qty": 3, "mid_cd": "001"},
        ]

        service.sales_repo = MagicMock()
        service.sales_repo.update_disuse_qty_from_slip.return_value = "updated"

        service.inventory_repo = MagicMock()
        service.inventory_repo.decrement_stock.side_effect = Exception("DB error")

        stats = service.sync_date("2026-02-21")

        # 동기화 자체는 성공
        assert stats["updated"] == 1
        assert stats["stock_decremented"] == 0
