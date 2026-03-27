"""post_order_pending_sync / clear_expired_pending 테스트"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _create_store_db(db_path):
    """테스트용 매장 DB 생성"""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '99999',
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER,
            remaining_qty INTEGER,
            arrival_time TEXT,
            expiry_time TEXT,
            status TEXT,
            alert_sent INTEGER,
            created_at TEXT,
            updated_at TEXT,
            actual_receiving_qty INTEGER,
            actual_arrival_time TEXT,
            order_source TEXT,
            pending_confirmed INTEGER DEFAULT 0,
            pending_confirmed_at TEXT,
            ord_input_id TEXT,
            UNIQUE(store_id, order_date, item_cd)
        );
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '99999',
            receiving_date TEXT NOT NULL,
            receiving_time TEXT,
            chit_no TEXT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0,
            delivery_type TEXT,
            center_nm TEXT,
            center_cd TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, receiving_date, item_cd, chit_no)
        );
        CREATE TABLE realtime_inventory (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            queried_at TEXT,
            collected_at TEXT,
            query_fail_count INTEGER DEFAULT 0,
            unavail_reason TEXT
        );
    """)
    conn.close()
    return db_path


@pytest.fixture
def store_db(tmp_path):
    """테스트용 매장 DB"""
    store_id = "99999"
    db_path = tmp_path / "stores" / f"{store_id}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _create_store_db(db_path)

    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))

    for item_cd, qty in [("ITEM_A", 10), ("ITEM_B", 5), ("ITEM_C", 3)]:
        conn.execute(
            """INSERT INTO order_tracking
               (store_id, order_date, item_cd, item_nm, mid_cd,
                order_qty, order_source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'auto', ?, ?)""",
            (store_id, today, item_cd, f"상품{item_cd}", "049",
             qty, today, today),
        )

    for item_cd in ["ITEM_A", "ITEM_B", "ITEM_C", "ITEM_D"]:
        conn.execute(
            """INSERT INTO realtime_inventory
               (item_cd, item_nm, stock_qty, pending_qty, is_available, queried_at)
               VALUES (?, ?, 5, 0, 1, ?)""",
            (item_cd, f"상품{item_cd}", today),
        )

    conn.commit()
    conn.close()

    return {"store_id": store_id, "db_path": db_path, "tmp_path": tmp_path}


@pytest.fixture
def collector(store_db):
    """OrderStatusCollector — Direct API 실패 → Selenium 폴백 경로 mock"""
    from src.collectors.order_status_collector import OrderStatusCollector

    info = store_db
    today_fmt = datetime.now().strftime("%Y%m%d")

    def mock_get_connection(store_id=None, table=None):
        return sqlite3.connect(str(info["db_path"]))

    with patch(
        "src.infrastructure.database.connection.DBRouter.get_connection",
        side_effect=mock_get_connection,
    ):
        mock_driver = MagicMock()
        c = OrderStatusCollector(driver=mock_driver, store_id=info["store_id"])

        # Direct API 캡처 실패하도록 mock (Selenium 폴백 경로 테스트)
        # _set_cal_day_and_search 성공
        c._set_cal_day_and_search = MagicMock(return_value=True)
        c.click_all_radio = MagicMock(return_value=True)

        # dsResult mock (ORD_YMD=오늘, ORD_INPUT_ID 포함, PYUN_QTY=배수)
        c.collect_order_status = MagicMock(return_value=[
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_A", "ITEM_NM": "상품A",
             "MID_CD": "049", "ORD_INPUT_ID": "자동발주",
             "PYUN_QTY": 10, "ORD_UNIT_QTY": 1, "ORD_CNT": 10},
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_B", "ITEM_NM": "상품B",
             "MID_CD": "049", "ORD_INPUT_ID": "자동발주",
             "PYUN_QTY": 5, "ORD_UNIT_QTY": 1, "ORD_CNT": 5},
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_D", "ITEM_NM": "상품D",
             "MID_CD": "050", "ORD_INPUT_ID": "단품별(재택)",
             "PYUN_QTY": 8, "ORD_UNIT_QTY": 1, "ORD_CNT": 8},
        ])

        # dsOrderSale mock (BUY_QTY 입고 정보)
        c.collect_order_sale_history = MagicMock(return_value=[
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_A", "ORD_QTY": "10", "BUY_QTY": "0"},
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_B", "ORD_QTY": "5", "BUY_QTY": "5"},
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_D", "ORD_QTY": "8", "BUY_QTY": "2"},
        ])

        yield c


class TestPostOrderPendingSync:
    """post_order_pending_sync() 테스트"""

    def test_syncs_successfully(self, collector, store_db):
        """정상 동기화 — dsResult 기반, Direct API 폴백"""
        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.post_order_pending_sync(today)

        # ITEM_A: pending=10 (10*1 - 0), ITEM_D: pending=6 (8*1 - 2)
        # ITEM_B: skipped (5*1 - 5 = 0)
        assert result["synced"] == 2
        assert result["skipped"] == 1
        assert result["date_mismatch"] is False

    def test_ord_input_id_saved(self, collector, store_db):
        """ord_input_id(발주방법) DB 저장 확인"""
        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.post_order_pending_sync(today)

        conn = sqlite3.connect(str(store_db["db_path"]))
        rows = conn.execute(
            "SELECT item_cd, ord_input_id FROM order_tracking WHERE pending_confirmed = 1"
        ).fetchall()
        conn.close()

        input_ids = {r[0]: r[1] for r in rows}
        assert input_ids.get("ITEM_A") == "자동발주"
        assert input_ids.get("ITEM_D") == "단품별(재택)"

    def test_by_method_counts(self, collector, store_db):
        """by_method 발주방법별 건수 반환"""
        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.post_order_pending_sync(today)

        assert "자동발주" in result["by_method"]
        assert result["by_method"]["자동발주"] == 1  # ITEM_A
        assert result["by_method"]["단품별(재택)"] == 1  # ITEM_D

    def test_pending_confirmed_in_db(self, collector, store_db):
        """DB에 pending_confirmed=1 마킹 확인"""
        collector.post_order_pending_sync(datetime.now().strftime("%Y-%m-%d"))

        conn = sqlite3.connect(str(store_db["db_path"]))
        rows = conn.execute(
            "SELECT item_cd, pending_confirmed, remaining_qty "
            "FROM order_tracking WHERE pending_confirmed = 1"
        ).fetchall()
        conn.close()

        confirmed_items = {r[0] for r in rows}
        assert "ITEM_A" in confirmed_items
        assert "ITEM_D" in confirmed_items

    def test_realtime_inventory_updated(self, collector, store_db):
        """realtime_inventory.pending_qty 업데이트 확인"""
        collector.post_order_pending_sync(datetime.now().strftime("%Y-%m-%d"))

        conn = sqlite3.connect(str(store_db["db_path"]))
        rows = conn.execute(
            "SELECT item_cd, pending_qty FROM realtime_inventory WHERE pending_qty > 0"
        ).fetchall()
        conn.close()

        pending_map = {r[0]: r[1] for r in rows}
        assert pending_map.get("ITEM_A") == 10
        assert pending_map.get("ITEM_D") == 6

    def test_no_today_orders_date_mismatch(self, collector):
        """dsResult에 오늘 발주 건 없으면 date_mismatch"""
        yesterday_fmt = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        collector.collect_order_status.return_value = [
            {"ORD_YMD": yesterday_fmt, "ITEM_CD": "ITEM_X",
             "PYUN_QTY": 5, "ORD_UNIT_QTY": 1, "ORD_INPUT_ID": "자동발주"},
        ]
        collector.collect_order_sale_history.return_value = []

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.post_order_pending_sync(today)
        assert result["date_mismatch"] is True

    def test_no_data(self, collector):
        """dsResult + dsOrderSale 모두 데이터 없을 때"""
        collector.collect_order_status.return_value = []
        collector.collect_order_sale_history.return_value = []

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.post_order_pending_sync(today)
        assert result["reason"] == "no_data"

    def test_bigdecimal_pyun_qty(self, collector, store_db):
        """PYUN_QTY가 BigDecimal dict일 때 처리"""
        today_fmt = datetime.now().strftime("%Y%m%d")
        collector.collect_order_status.return_value = [
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_A", "ITEM_NM": "상품A",
             "MID_CD": "049", "ORD_INPUT_ID": "자동발주",
             "PYUN_QTY": {"hi": 3, "lo": 0}, "ORD_UNIT_QTY": {"hi": 2, "lo": 0}},
        ]
        collector.collect_order_sale_history.return_value = [
            {"ORD_YMD": today_fmt, "ITEM_CD": "ITEM_A", "ORD_QTY": "6", "BUY_QTY": "0"},
        ]

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.post_order_pending_sync(today)
        assert result["synced"] == 1

        conn = sqlite3.connect(str(store_db["db_path"]))
        row = conn.execute(
            "SELECT remaining_qty FROM order_tracking WHERE item_cd='ITEM_A' AND pending_confirmed=1"
        ).fetchone()
        conn.close()
        # 3 * 2 = 6, buy=0, pending=6
        assert row[0] == 6


class TestClearExpiredPending:
    """clear_expired_pending() 테스트"""

    def test_clear_arrived_by_receiving_history(self, collector, store_db):
        """1차 검증: receiving_history에 입고 기록 → 클리어"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_iso = (datetime.now() - timedelta(days=1)).isoformat()

        conn = sqlite3.connect(str(store_db["db_path"]))
        conn.execute(
            """UPDATE order_tracking
               SET pending_confirmed = 1, pending_confirmed_at = ?,
                   remaining_qty = 5, order_qty = 10, order_date = ?
               WHERE item_cd = 'ITEM_A'""",
            (yesterday_iso, yesterday),
        )
        conn.execute(
            "UPDATE realtime_inventory SET pending_qty = 5 WHERE item_cd = 'ITEM_A'"
        )
        # 입고 기록 추가 (receiving_qty >= order_qty)
        conn.execute(
            """INSERT INTO receiving_history
               (store_id, receiving_date, item_cd, item_nm, order_date,
                receiving_qty, created_at)
               VALUES ('99999', ?, 'ITEM_A', '상품A', ?, 10, ?)""",
            (datetime.now().strftime("%Y-%m-%d"), yesterday, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.clear_expired_pending(today)

        assert result["cleared"] >= 1

        conn = sqlite3.connect(str(store_db["db_path"]))
        row = conn.execute(
            "SELECT pending_confirmed, remaining_qty FROM order_tracking WHERE item_cd = 'ITEM_A'"
        ).fetchone()
        ri = conn.execute(
            "SELECT pending_qty FROM realtime_inventory WHERE item_cd = 'ITEM_A'"
        ).fetchone()
        conn.close()

        assert row[0] == 0  # pending_confirmed 클리어
        assert row[1] == 0  # remaining_qty 클리어
        assert ri[0] == 0   # pending_qty 클리어

    def test_clear_arrived_by_ot_status(self, collector, store_db):
        """2차 폴백: receiving_history 없지만 OT status='arrived' → 클리어"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_iso = (datetime.now() - timedelta(days=1)).isoformat()

        conn = sqlite3.connect(str(store_db["db_path"]))
        conn.execute(
            """UPDATE order_tracking
               SET pending_confirmed = 1, pending_confirmed_at = ?,
                   remaining_qty = 5, order_qty = 10, order_date = ?,
                   status = 'arrived'
               WHERE item_cd = 'ITEM_A'""",
            (yesterday_iso, yesterday),
        )
        # receiving_history 없음! 하지만 status='arrived'
        conn.commit()
        conn.close()

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.clear_expired_pending(today)

        assert result["cleared"] >= 1

        conn = sqlite3.connect(str(store_db["db_path"]))
        row = conn.execute(
            "SELECT pending_confirmed FROM order_tracking WHERE item_cd = 'ITEM_A'"
        ).fetchone()
        conn.close()

        assert row[0] == 0

    def test_extend_not_arrived_day1(self, collector, store_db):
        """미입고 + 1일 경과 → pending 연장 (재발주 방지)"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_iso = (datetime.now() - timedelta(days=1)).isoformat()

        conn = sqlite3.connect(str(store_db["db_path"]))
        conn.execute(
            """UPDATE order_tracking
               SET pending_confirmed = 1, pending_confirmed_at = ?,
                   remaining_qty = 5, order_qty = 10, order_date = ?,
                   status = 'ordered'
               WHERE item_cd = 'ITEM_A'""",
            (yesterday_iso, yesterday),
        )
        conn.execute(
            "UPDATE realtime_inventory SET pending_qty = 5 WHERE item_cd = 'ITEM_A'"
        )
        # receiving_history 없음! status='ordered'
        conn.commit()
        conn.close()

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.clear_expired_pending(today)

        assert result["extended"] >= 1
        assert result["cleared"] == 0

        conn = sqlite3.connect(str(store_db["db_path"]))
        row = conn.execute(
            "SELECT pending_confirmed FROM order_tracking WHERE item_cd = 'ITEM_A'"
        ).fetchone()
        ri = conn.execute(
            "SELECT pending_qty FROM realtime_inventory WHERE item_cd = 'ITEM_A'"
        ).fetchone()
        conn.close()

        assert row[0] == 1  # pending 유지
        assert ri[0] == 5   # pending_qty 유지

    def test_force_clear_day2(self, collector, store_db):
        """미입고 + 2일 이상 경과 → 강제 클리어"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        two_days_ago_iso = (datetime.now() - timedelta(days=2)).isoformat()

        conn = sqlite3.connect(str(store_db["db_path"]))
        conn.execute(
            """UPDATE order_tracking
               SET pending_confirmed = 1, pending_confirmed_at = ?,
                   remaining_qty = 5, order_qty = 10, order_date = ?,
                   status = 'ordered'
               WHERE item_cd = 'ITEM_A'""",
            (two_days_ago_iso, two_days_ago),
        )
        conn.commit()
        conn.close()

        today = datetime.now().strftime("%Y-%m-%d")
        result = collector.clear_expired_pending(today)

        assert result["force_cleared"] >= 1

    def test_keeps_today_pending(self, collector, store_db):
        """오늘 pending은 유지"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_iso = datetime.now().isoformat()

        conn = sqlite3.connect(str(store_db["db_path"]))
        conn.execute(
            """UPDATE order_tracking
               SET pending_confirmed = 1, pending_confirmed_at = ?,
                   remaining_qty = 7
               WHERE item_cd = 'ITEM_B'""",
            (today_iso,),
        )
        conn.execute(
            "UPDATE realtime_inventory SET pending_qty = 7 WHERE item_cd = 'ITEM_B'"
        )
        conn.commit()
        conn.close()

        result = collector.clear_expired_pending(today)

        assert result["cleared_ot"] == 0

        conn = sqlite3.connect(str(store_db["db_path"]))
        row = conn.execute(
            "SELECT pending_confirmed FROM order_tracking WHERE item_cd = 'ITEM_B'"
        ).fetchone()
        ri = conn.execute(
            "SELECT pending_qty FROM realtime_inventory WHERE item_cd = 'ITEM_B'"
        ).fetchone()
        conn.close()

        assert row[0] == 1
        assert ri[0] == 7


class TestGetConfirmedPendingFromDB:
    """_get_confirmed_pending_from_db() 테스트"""

    def test_returns_today_confirmed(self, store_db):
        """오늘 confirmed pending 반환"""
        from src.order.auto_order import AutoOrderSystem

        info = store_db
        today_iso = datetime.now().isoformat()

        conn = sqlite3.connect(str(info["db_path"]))
        conn.execute(
            """UPDATE order_tracking
               SET pending_confirmed = 1, pending_confirmed_at = ?,
                   remaining_qty = 8
               WHERE item_cd = 'ITEM_A'""",
            (today_iso,),
        )
        conn.commit()
        conn.close()

        def mock_get_connection(store_id=None, table=None):
            return sqlite3.connect(str(info["db_path"]))

        with patch(
            "src.infrastructure.database.connection.DBRouter.get_connection",
            side_effect=mock_get_connection,
        ):
            system = AutoOrderSystem.__new__(AutoOrderSystem)
            system.store_id = info["store_id"]
            result = system._get_confirmed_pending_from_db()

        assert result.get("ITEM_A") == 8
        assert "ITEM_B" not in result

    def test_empty_when_no_confirmed(self, store_db):
        """confirmed 없으면 빈 dict"""
        from src.order.auto_order import AutoOrderSystem

        info = store_db

        def mock_get_connection(store_id=None, table=None):
            return sqlite3.connect(str(info["db_path"]))

        with patch(
            "src.infrastructure.database.connection.DBRouter.get_connection",
            side_effect=mock_get_connection,
        ):
            system = AutoOrderSystem.__new__(AutoOrderSystem)
            system.store_id = info["store_id"]
            result = system._get_confirmed_pending_from_db()

        assert result == {}
