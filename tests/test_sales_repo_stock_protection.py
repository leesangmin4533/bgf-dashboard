"""
sales_repo realtime_inventory 재고 보호 테스트

근본 원인: sales_repo._upsert_daily_sale()이 daily_sales 저장 시
realtime_inventory.stock_qty를 매출분석 화면의 과거 시점 재고로 덮어써서
prefetch(DirectAPI)가 저장한 정확한 BGF NOW_QTY를 오염시킴.

수정: ON CONFLICT에서 stock_qty 갱신 제거 → prefetch 값 보호

실측 근거 (2026-03-28):
- prefetch stock=13 → sales_repo가 0으로 덮어씀 → 20개 중 11개 불일치
"""

import sqlite3
import pytest
from unittest.mock import patch
from datetime import datetime

from src.infrastructure.database.repos.sales_repo import SalesRepository
from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository


@pytest.fixture
def store_db(tmp_path):
    """테스트용 store DB 생성"""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # daily_sales 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            collected_at TEXT,
            sales_date TEXT,
            item_cd TEXT,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(store_id, sales_date, item_cd)
        )
    """)

    # realtime_inventory 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            order_unit_qty INTEGER DEFAULT 1,
            is_available INTEGER DEFAULT 1,
            is_cut_item INTEGER DEFAULT 0,
            query_fail_count INTEGER DEFAULT 0,
            unavail_reason TEXT,
            queried_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            stop_plan_ymd TEXT,
            cut_reason TEXT,
            UNIQUE(store_id, item_cd)
        )
    """)

    # order_tracking (FIFO용)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            delivery_type TEXT,
            order_qty INTEGER,
            remaining_qty INTEGER,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(order_date, item_cd)
        )
    """)

    # inventory_batches (FIFO용)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            receiving_date TEXT NOT NULL,
            receiving_id INTEGER,
            expiration_days INTEGER NOT NULL,
            expiry_date TEXT NOT NULL,
            initial_qty INTEGER NOT NULL,
            remaining_qty INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            store_id TEXT,
            delivery_type TEXT DEFAULT NULL
        )
    """)

    conn.commit()
    conn.close()
    return str(db_path)


class TestStockProtection:
    """prefetch가 저장한 stock_qty를 SalesCollector가 덮어쓰지 않는지 검증"""

    def test_prefetch_stock_not_overwritten_by_sales(self, store_db):
        """핵심 테스트: prefetch stock=13 → sales 저장 → stock=13 유지"""
        store_id = "46513"
        conn = sqlite3.connect(store_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 1. prefetch가 정확한 재고를 저장 (BGF DirectAPI → stock=13)
        cursor.execute(
            """INSERT INTO realtime_inventory
            (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty,
             is_available, is_cut_item, queried_at, created_at)
            VALUES (?, ?, ?, 13, 0, 10, 1, 0, '2026-03-28T07:07:40', '2026-03-28T07:07:40')""",
            (store_id, "8801043036177", "농심)꿀꽈배기90g")
        )
        conn.commit()

        # 2. SalesCollector가 daily_sales 저장 (stock_qty=1은 매출분석 화면의 과거값)
        repo = SalesRepository.__new__(SalesRepository)
        repo.store_id = store_id

        repo._upsert_daily_sale(
            cursor=cursor,
            collected_at="2026-03-28T10:13:24",
            sales_date="2026-03-27",
            item_cd="8801043036177",
            mid_cd="015",
            sale_qty=2, ord_qty=0, buy_qty=0, disuse_qty=0,
            stock_qty=1,  # ← 매출분석 화면의 과거 시점 재고
            now="2026-03-28T10:13:24",
            store_id=store_id,
            item_nm="농심)꿀꽈배기90g"
        )
        conn.commit()

        # 3. 검증
        cursor.execute(
            "SELECT stock_qty, queried_at FROM realtime_inventory WHERE item_cd = ?",
            ("8801043036177",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["stock_qty"] == 13, (
            f"prefetch stock=13이 보호되어야 하지만 {row['stock_qty']}로 변경됨"
        )
        assert row["queried_at"] == "2026-03-28T07:07:40", (
            f"queried_at이 변경되면 안 됨: {row['queried_at']}"
        )

    def test_new_item_gets_stock_from_sales(self, store_db):
        """신규 상품(realtime_inventory에 없음)은 daily_sales.stock_qty로 INSERT"""
        store_id = "46513"

        conn = sqlite3.connect(store_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        repo = SalesRepository.__new__(SalesRepository)
        repo.store_id = store_id
        repo._db_path = store_db

        repo._upsert_daily_sale(
            cursor=cursor,
            collected_at="2026-03-28T10:13:24",
            sales_date="2026-03-27",
            item_cd="NEW_ITEM_001",
            mid_cd="042",
            sale_qty=3,
            ord_qty=0,
            buy_qty=0,
            disuse_qty=0,
            stock_qty=5,
            now="2026-03-28T10:13:24",
            store_id=store_id,
            item_nm="테스트신규상품"
        )
        conn.commit()

        cursor.execute(
            "SELECT stock_qty FROM realtime_inventory WHERE item_cd = ?",
            ("NEW_ITEM_001",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None, "신규 상품은 realtime_inventory에 INSERT되어야 함"
        assert row["stock_qty"] == 5, (
            f"신규 상품은 daily_sales.stock_qty=5로 저장되어야 하지만 {row['stock_qty']}"
        )

    def test_item_nm_still_updated(self, store_db):
        """기존 상품의 item_nm은 갱신되어야 함 (stock_qty만 보호)"""
        store_id = "46513"
        conn = sqlite3.connect(store_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO realtime_inventory
            (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty,
             is_available, is_cut_item, queried_at, created_at)
            VALUES (?, ?, NULL, 5, 0, 1, 1, 0, '2026-03-28T07:07:40', '2026-03-28T07:07:40')""",
            (store_id, "ITEM_NO_NAME")
        )
        conn.commit()

        repo = SalesRepository.__new__(SalesRepository)
        repo.store_id = store_id

        repo._upsert_daily_sale(
            cursor=cursor,
            collected_at="2026-03-28T10:13:24",
            sales_date="2026-03-27",
            item_cd="ITEM_NO_NAME",
            mid_cd="015",
            sale_qty=1, ord_qty=0, buy_qty=0, disuse_qty=0,
            stock_qty=99,
            now="2026-03-28T10:13:24",
            store_id=store_id,
            item_nm="이름갱신됨"
        )
        conn.commit()

        cursor.execute(
            "SELECT item_nm, stock_qty FROM realtime_inventory WHERE item_cd = ?",
            ("ITEM_NO_NAME",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row["item_nm"] == "이름갱신됨", "item_nm은 갱신되어야 함"
        assert row["stock_qty"] == 5, (
            f"stock_qty=5(prefetch값)가 보호되어야 하지만 {row['stock_qty']}로 변경됨"
        )

    def test_daily_sales_stock_not_affected(self, store_db):
        """daily_sales 테이블의 stock_qty는 정상 저장되어야 함"""
        store_id = "46513"

        conn = sqlite3.connect(store_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        repo = SalesRepository.__new__(SalesRepository)
        repo.store_id = store_id
        repo._db_path = store_db

        repo._upsert_daily_sale(
            cursor=cursor,
            collected_at="2026-03-28T10:13:24",
            sales_date="2026-03-27",
            item_cd="DAILY_TEST_001",
            mid_cd="042",
            sale_qty=3,
            ord_qty=1,
            buy_qty=5,
            disuse_qty=0,
            stock_qty=7,
            now="2026-03-28T10:13:24",
            store_id=store_id,
            item_nm="일별판매테스트"
        )
        conn.commit()

        cursor.execute(
            "SELECT stock_qty, sale_qty, buy_qty FROM daily_sales WHERE item_cd = ?",
            ("DAILY_TEST_001",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row["stock_qty"] == 7, "daily_sales.stock_qty는 정상 저장"
        assert row["sale_qty"] == 3, "daily_sales.sale_qty는 정상 저장"
        assert row["buy_qty"] == 5, "daily_sales.buy_qty는 정상 저장"

    def test_stock_zero_from_sales_does_not_overwrite(self, store_db):
        """매출분석 stock=0이어도 prefetch stock=13을 덮어쓰지 않음"""
        store_id = "46513"
        conn = sqlite3.connect(store_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO realtime_inventory
            (store_id, item_cd, item_nm, stock_qty, pending_qty, order_unit_qty,
             is_available, is_cut_item, queried_at, created_at)
            VALUES (?, ?, ?, 13, 2, 10, 1, 0, '2026-03-28T07:07:40', '2026-03-28T07:07:40')""",
            (store_id, "ZERO_TEST_001", "제로테스트상품")
        )
        conn.commit()

        repo = SalesRepository.__new__(SalesRepository)
        repo.store_id = store_id

        repo._upsert_daily_sale(
            cursor=cursor,
            collected_at="2026-03-28T10:13:24",
            sales_date="2026-03-28",
            item_cd="ZERO_TEST_001",
            mid_cd="015",
            sale_qty=0, ord_qty=0, buy_qty=0, disuse_qty=0,
            stock_qty=0,  # ← 매출분석에서 오늘 판매 없어서 stock=0
            now="2026-03-28T10:13:24",
            store_id=store_id,
            item_nm="제로테스트상품"
        )
        conn.commit()

        cursor.execute(
            "SELECT stock_qty, pending_qty FROM realtime_inventory WHERE item_cd = ?",
            ("ZERO_TEST_001",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row["stock_qty"] == 13, f"prefetch stock=13 보호 실패: {row['stock_qty']}"
        assert row["pending_qty"] == 2, f"pending_qty=2도 보호되어야 함: {row['pending_qty']}"
