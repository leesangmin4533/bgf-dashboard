"""
컷상품 발주 방지 테스트

1. order_tracking_repo.save_order() order_source 파라미터 검증
2. _warn_stale_cut_items() stale 경고 검증
3. _exclude_filtered_items() CUT 상품 제외 검증
4. CUT_STALE_PRIORITY_CHECK 상수 검증
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────

@pytest.fixture
def order_tracking_db():
    """order_tracking 테이블이 있는 in-memory DB"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            arrival_time TEXT,
            expiry_time TEXT,
            status TEXT DEFAULT 'pending',
            alert_sent INTEGER,
            created_at TEXT,
            updated_at TEXT,
            actual_receiving_qty INTEGER,
            actual_arrival_time TEXT,
            order_source TEXT,
            UNIQUE(order_date, item_cd)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def inventory_db():
    """realtime_inventory 테이블이 있는 in-memory DB"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            queried_at TEXT,
            created_at TEXT,
            UNIQUE(store_id, item_cd)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


# ─────────────────────────────────────────────────
# 1. save_order() order_source 파라미터 테스트
# ─────────────────────────────────────────────────

class TestSaveOrderSource:
    """order_tracking_repo.save_order()의 order_source 파라미터 검증"""

    def _make_repo(self, conn):
        """OrderTrackingRepository를 mock conn으로 생성

        save_order()는 finally에서 conn.close()를 호출하므로,
        _get_conn이 매번 동일한 in-memory conn의 '비닫힘' 래퍼를 반환하도록 구성.
        """
        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository

        class NonClosingConn:
            """conn.close()를 무시하는 래퍼"""
            def __init__(self, real_conn):
                self._conn = real_conn
            def cursor(self):
                return self._conn.cursor()
            def commit(self):
                return self._conn.commit()
            def close(self):
                pass  # 테스트에서는 닫지 않음
            def execute(self, *args, **kwargs):
                return self._conn.execute(*args, **kwargs)

        repo = OrderTrackingRepository.__new__(OrderTrackingRepository)
        repo._get_conn = MagicMock(return_value=NonClosingConn(conn))
        repo._now = MagicMock(return_value="2026-02-23 12:00:00")
        repo.store_id = "46513"
        return repo

    def test_save_order_default_source_is_site(self, order_tracking_db):
        """save_order() 기본값 → order_source='site'"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="8801013777260",
            item_nm="테스트상품",
            mid_cd="040",
            delivery_type="1차",
            order_qty=10,
            arrival_time="2026-02-24 06:00",
            expiry_time="2027-04-13 23:59",
        )
        assert order_id > 0

        row = order_tracking_db.execute(
            "SELECT order_source FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "site"

    def test_save_order_with_explicit_site(self, order_tracking_db):
        """save_order(order_source='site') → DB에 'site' 저장"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="TEST001",
            item_nm="테스트",
            mid_cd="001",
            delivery_type="1차",
            order_qty=5,
            arrival_time="2026-02-24 06:00",
            expiry_time="2026-02-25 06:00",
            order_source="site",
        )
        row = order_tracking_db.execute(
            "SELECT order_source FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "site"

    def test_save_order_with_auto_source(self, order_tracking_db):
        """save_order(order_source='auto') → DB에 'auto' 저장"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="TEST002",
            item_nm="자동발주상품",
            mid_cd="049",
            delivery_type="일반",
            order_qty=6,
            arrival_time="2026-02-24 12:00",
            expiry_time="2026-03-24 12:00",
            order_source="auto",
        )
        row = order_tracking_db.execute(
            "SELECT order_source FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "auto"

    def test_save_order_with_manual_source(self, order_tracking_db):
        """save_order(order_source='manual') → DB에 'manual' 저장"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="TEST003",
            item_nm="수동발주상품",
            mid_cd="050",
            delivery_type="일반",
            order_qty=3,
            arrival_time="2026-02-24 12:00",
            expiry_time="2026-04-24 12:00",
            order_source="manual",
        )
        row = order_tracking_db.execute(
            "SELECT order_source FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "manual"

    def test_save_order_with_receiving_source(self, order_tracking_db):
        """save_order(order_source='receiving') → DB에 'receiving' 저장"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="TEST004",
            item_nm="입고역추적상품",
            mid_cd="001",
            delivery_type="1차",
            order_qty=2,
            arrival_time="2026-02-24 06:00",
            expiry_time="2026-02-25 06:00",
            order_source="receiving",
        )
        row = order_tracking_db.execute(
            "SELECT order_source FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "receiving"

    def test_save_order_with_store_id_includes_source(self, order_tracking_db):
        """store_id 지정 시에도 order_source가 INSERT에 포함되는지"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="TEST005",
            item_nm="매장지정상품",
            mid_cd="072",
            delivery_type="일반",
            order_qty=1,
            arrival_time="2026-02-24 12:00",
            expiry_time="2026-06-24 12:00",
            store_id="46513",
            order_source="auto",
        )
        row = order_tracking_db.execute(
            "SELECT order_source, store_id FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "auto"
        assert row["store_id"] == "46513"

    def test_save_order_without_store_id_includes_source(self, order_tracking_db):
        """store_id 미지정 시에도 order_source가 INSERT에 포함되는지"""
        repo = self._make_repo(order_tracking_db)
        order_id = repo.save_order(
            order_date="2026-02-23",
            item_cd="TEST006",
            item_nm="매장미지정상품",
            mid_cd="006",
            delivery_type="1차",
            order_qty=3,
            arrival_time="2026-02-24 06:00",
            expiry_time="2026-02-26 06:00",
            store_id=None,
            order_source="site",
        )
        row = order_tracking_db.execute(
            "SELECT order_source FROM order_tracking WHERE id = ?", (order_id,)
        ).fetchone()
        assert row["order_source"] == "site"


# ─────────────────────────────────────────────────
# 2. _warn_stale_cut_items() 테스트
# ─────────────────────────────────────────────────

class TestWarnStaleCutItems:
    """auto_order.py _warn_stale_cut_items() 검증"""

    def _make_auto_order(self, inventory_data=None):
        """AutoOrder 인스턴스를 최소 mock으로 생성"""
        from src.order.auto_order import AutoOrderSystem

        ao = AutoOrderSystem.__new__(AutoOrderSystem)
        ao.store_id = "46513"

        # inventory_repo mock
        inv_repo = MagicMock()
        if inventory_data:
            inv_repo.get_by_item = MagicMock(side_effect=lambda item_cd: inventory_data.get(item_cd))
        else:
            inv_repo.get_by_item = MagicMock(return_value=None)
        ao._inventory_repo = inv_repo

        return ao

    @patch("src.order.auto_order.logger")
    def test_warn_stale_items_logs_warning(self, mock_logger):
        """stale 상품이 있으면 경고 로그 출력"""
        stale_date = (datetime.now() - timedelta(days=7)).isoformat()
        inv_data = {
            "ITEM001": {"queried_at": stale_date},
        }
        ao = self._make_auto_order(inv_data)

        order_list = [{"item_cd": "ITEM001", "final_order_qty": 5}]
        ao._warn_stale_cut_items(order_list)

        mock_logger.warning.assert_called_once()
        call_msg = mock_logger.warning.call_args[0][0]
        assert "CUT" in call_msg
        assert "1" in call_msg

    @patch("src.order.auto_order.logger")
    def test_warn_no_stale_items_no_warning(self, mock_logger):
        """모든 상품이 최신이면 경고 없음"""
        fresh_date = datetime.now().isoformat()
        inv_data = {
            "ITEM001": {"queried_at": fresh_date},
            "ITEM002": {"queried_at": fresh_date},
        }
        ao = self._make_auto_order(inv_data)

        order_list = [
            {"item_cd": "ITEM001", "final_order_qty": 3},
            {"item_cd": "ITEM002", "final_order_qty": 2},
        ]
        ao._warn_stale_cut_items(order_list)

        mock_logger.warning.assert_not_called()

    @patch("src.order.auto_order.logger")
    def test_warn_never_queried_item(self, mock_logger):
        """queried_at이 없는 상품 → 'never'로 경고"""
        inv_data = {
            "ITEM001": {"queried_at": None},
        }
        ao = self._make_auto_order(inv_data)

        order_list = [{"item_cd": "ITEM001"}]
        ao._warn_stale_cut_items(order_list)

        mock_logger.warning.assert_called_once()
        call_msg = mock_logger.warning.call_args[0][0]
        assert "CUT" in call_msg
        assert "never" in call_msg

    @patch("src.order.auto_order.logger")
    def test_warn_inventory_not_found(self, mock_logger):
        """인벤토리에 없는 상품 → 'never'로 경고"""
        ao = self._make_auto_order(inventory_data={})  # empty
        order_list = [{"item_cd": "UNKNOWN_ITEM"}]
        ao._warn_stale_cut_items(order_list)

        mock_logger.warning.assert_called_once()
        call_msg = mock_logger.warning.call_args[0][0]
        assert "CUT" in call_msg

    def test_warn_empty_order_list_no_crash(self):
        """빈 발주 목록에서도 에러 없이 정상 동작"""
        ao = self._make_auto_order()
        ao._warn_stale_cut_items([])  # should not raise

    @patch("src.order.auto_order.logger")
    def test_warn_mixed_stale_and_fresh(self, mock_logger):
        """stale + fresh 혼합 → stale 상품만 경고에 포함"""
        stale_date = (datetime.now() - timedelta(days=10)).isoformat()
        fresh_date = datetime.now().isoformat()
        inv_data = {
            "STALE001": {"queried_at": stale_date},
            "FRESH001": {"queried_at": fresh_date},
            "STALE002": {"queried_at": stale_date},
        }
        ao = self._make_auto_order(inv_data)

        order_list = [
            {"item_cd": "STALE001"},
            {"item_cd": "FRESH001"},
            {"item_cd": "STALE002"},
        ]
        ao._warn_stale_cut_items(order_list)

        mock_logger.warning.assert_called_once()
        call_msg = mock_logger.warning.call_args[0][0]
        assert "2" in call_msg


# ─────────────────────────────────────────────────
# 3. _exclude_filtered_items() CUT 제외 테스트
# ─────────────────────────────────────────────────

class TestExcludeFilteredItemsCut:
    """auto_order._exclude_filtered_items()에서 CUT 상품 제외 검증"""

    def _make_auto_order_with_cut(self, cut_items_set):
        """CUT 상품 세트를 가진 AutoOrder mock"""
        from src.order.auto_order import AutoOrderSystem

        ao = AutoOrderSystem.__new__(AutoOrderSystem)
        ao.store_id = "46513"
        ao._cut_items = cut_items_set
        ao._unavailable_items = set()
        ao._auto_order_items = set()
        ao._smart_order_items = set()
        return ao

    def test_cut_item_excluded(self):
        """CUT 상품이 발주 목록에서 제외"""
        ao = self._make_auto_order_with_cut({"CUT001", "CUT002"})

        order_list = [
            {"item_cd": "CUT001", "final_order_qty": 10},
            {"item_cd": "NORMAL001", "final_order_qty": 5},
            {"item_cd": "CUT002", "final_order_qty": 3},
        ]

        result = ao._exclude_filtered_items(order_list)
        result_items = {item["item_cd"] for item in result}

        assert "CUT001" not in result_items
        assert "CUT002" not in result_items
        assert "NORMAL001" in result_items
        assert len(result) == 1

    def test_no_cut_items_nothing_excluded(self):
        """CUT 목록이 비어있으면 모든 상품 유지"""
        ao = self._make_auto_order_with_cut(set())

        order_list = [
            {"item_cd": "ITEM001", "final_order_qty": 5},
            {"item_cd": "ITEM002", "final_order_qty": 3},
        ]

        result = ao._exclude_filtered_items(order_list)
        assert len(result) == 2

    def test_all_cut_items_empty_result(self):
        """모든 상품이 CUT이면 빈 목록 반환"""
        ao = self._make_auto_order_with_cut({"A", "B", "C"})

        order_list = [
            {"item_cd": "A"},
            {"item_cd": "B"},
            {"item_cd": "C"},
        ]

        result = ao._exclude_filtered_items(order_list)
        assert len(result) == 0


# ─────────────────────────────────────────────────
# 4. CUT_STALE_PRIORITY_CHECK 상수 검증
# ─────────────────────────────────────────────────

class TestCutConstants:
    """CUT 관련 상수 검증"""

    def test_cut_stale_priority_check_increased(self):
        """CUT_STALE_PRIORITY_CHECK >= 100 (20에서 증가)"""
        from src.settings.constants import CUT_STALE_PRIORITY_CHECK
        assert CUT_STALE_PRIORITY_CHECK >= 100

    def test_cut_status_stale_days_exists(self):
        """CUT_STATUS_STALE_DAYS가 존재하고 양수"""
        from src.settings.constants import CUT_STATUS_STALE_DAYS
        assert CUT_STATUS_STALE_DAYS > 0

    def test_cut_status_stale_days_reasonable(self):
        """CUT_STATUS_STALE_DAYS <= 7 (합리적 범위)"""
        from src.settings.constants import CUT_STATUS_STALE_DAYS
        assert CUT_STATUS_STALE_DAYS <= 7
