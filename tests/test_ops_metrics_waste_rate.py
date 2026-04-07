"""ops_metrics._waste_rate products JOIN 회귀 테스트 (ops-metrics-waste-query-fix)"""

import sqlite3
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.analysis.ops_metrics import OpsMetrics


def _build_common_db(path):
    """테스트용 common.db 생성 (products 테이블만)"""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT NOT NULL
        )
    """)
    conn.executemany(
        "INSERT INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
        [
            ("A001", "삼각김밥", "002"),
            ("A002", "김밥", "003"),
            ("A003", "도시락", "001"),
            # A999 (unmatched)는 일부러 넣지 않음
        ],
    )
    conn.commit()
    conn.close()


def _build_store_db(path):
    """테스트용 매장 DB 생성 (waste_slip_items + daily_sales 최소 스키마)"""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE waste_slip_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            chit_date TEXT NOT NULL,
            chit_no TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            large_cd TEXT,
            qty INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0
        )
    """)

    today = date.today()
    # 30일치 daily_sales (data_days 체크 통과)
    for i in range(30):
        d = (today - timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO daily_sales (store_id, sales_date, item_cd, mid_cd, sale_qty) VALUES (?, ?, ?, ?, ?)",
            ("TEST", d, "A001", "002", 10),
        )
        conn.execute(
            "INSERT INTO daily_sales (store_id, sales_date, item_cd, mid_cd, sale_qty) VALUES (?, ?, ?, ?, ?)",
            ("TEST", d, "A002", "003", 8),
        )

    # 폐기: A001(매칭), A002(매칭), A999(미매칭)
    for i in range(5):
        d = (today - timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO waste_slip_items (store_id, chit_date, chit_no, item_cd, qty) VALUES (?, ?, ?, ?, ?)",
            ("TEST", d, f"C{i}", "A001", 2),
        )
        conn.execute(
            "INSERT INTO waste_slip_items (store_id, chit_date, chit_no, item_cd, qty) VALUES (?, ?, ?, ?, ?)",
            ("TEST", d, f"C{i}b", "A002", 1),
        )
    # A999 미매칭 (products에 없음) — 10% > 5% 임계치 초과하도록
    conn.execute(
        "INSERT INTO waste_slip_items (store_id, chit_date, chit_no, item_cd, qty) VALUES (?, ?, ?, ?, ?)",
        ("TEST", today.isoformat(), "CX", "A999", 5),
    )

    conn.commit()
    conn.close()


@pytest.fixture
def fake_dbs(tmp_path):
    common_path = tmp_path / "common.db"
    store_path = tmp_path / "store.db"
    _build_common_db(common_path)
    _build_store_db(store_path)
    return common_path, store_path


class TestWasteRateProductsJoin:
    """_waste_rate products JOIN 회귀 (ops-metrics-waste-query-fix)"""

    def test_waste_rate_joins_products_for_mid_cd(self, fake_dbs):
        """정상: waste_slip_items + products JOIN → mid_cd별 집계"""
        common_path, store_path = fake_dbs

        def fake_conn(store_id):
            conn = sqlite3.connect(str(store_path))
            conn.row_factory = sqlite3.Row
            conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
            return conn

        with patch(
            "src.analysis.ops_metrics.DBRouter.get_store_connection_with_common",
            side_effect=fake_conn,
        ):
            result = OpsMetrics("TEST")._waste_rate()

        assert "categories" in result, f"insufficient_data 반환됨: {result}"
        mids = {c["mid_cd"] for c in result["categories"]}
        # A001(002), A002(003) 매칭 → 002, 003 존재
        assert "002" in mids
        assert "003" in mids

    def test_waste_rate_unmatched_items_trigger_warning(self, fake_dbs):
        """products 미매칭이 5% 초과하면 경고 로그 발생"""
        common_path, store_path = fake_dbs

        def fake_conn(store_id):
            conn = sqlite3.connect(str(store_path))
            conn.row_factory = sqlite3.Row
            conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
            return conn

        with patch(
            "src.analysis.ops_metrics.DBRouter.get_store_connection_with_common",
            side_effect=fake_conn,
        ), patch("src.analysis.ops_metrics.logger") as mock_logger:
            OpsMetrics("TEST")._waste_rate()

        # A999(qty=5) 미매칭 / 전체(A001*10 + A002*5 + A999*5 = 20) = 25% > 5%
        warning_calls = [
            c for c in mock_logger.warning.call_args_list
            if "products 미매칭" in str(c)
        ]
        assert warning_calls, f"경고 로그 호출 없음. calls: {mock_logger.warning.call_args_list}"

    def test_waste_rate_no_such_column_regression(self, fake_dbs):
        """회귀: 'no such column: mid_cd' OperationalError 재발 방지"""
        common_path, store_path = fake_dbs

        def fake_conn(store_id):
            conn = sqlite3.connect(str(store_path))
            conn.row_factory = sqlite3.Row
            conn.execute(f"ATTACH DATABASE '{common_path}' AS common")
            return conn

        with patch(
            "src.analysis.ops_metrics.DBRouter.get_store_connection_with_common",
            side_effect=fake_conn,
        ):
            result = OpsMetrics("TEST")._waste_rate()

        # insufficient_data로 떨어지지 않아야 함 (쿼리 성공)
        assert result != {"insufficient_data": True}
        assert "categories" in result
