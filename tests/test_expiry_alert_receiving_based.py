"""폐기 알림 입고 기반 전환 테스트.

Design Reference: docs/02-design/features/expiry-alert-receiving-based.design.md
Plan 시나리오 6개 커버:
1. AI 발주 + 2차 입고 + 14:00 폐기
2. 수동 발주 + 2차 입고 + 14:00 폐기 (핵심)
3. stock_qty=0 → 제외
4. 1차 입고 도시락 + 02:00 폐기
5. 빵(012) ambient → 별도 처리 (매핑에 없음)
6. 해당 시간 입고 건 없음 → 0건 스킵
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest


@pytest.fixture
def expiry_db(tmp_path):
    """테스트용 store DB (receiving_history + daily_sales + inventory_batches)"""
    db_path = tmp_path / "stores" / "46513.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # receiving_history
    conn.execute("""
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, receiving_date TEXT, receiving_time TEXT,
            chit_no TEXT, item_cd TEXT, item_nm TEXT, mid_cd TEXT,
            order_date TEXT, order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0, plan_qty INTEGER DEFAULT 0,
            delivery_type TEXT, center_nm TEXT, center_cd TEXT, created_at TEXT
        )
    """)

    # daily_sales
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, collected_at TEXT, sales_date TEXT,
            item_cd TEXT, mid_cd TEXT, sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0, buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0, stock_qty INTEGER DEFAULT 0,
            created_at TEXT, promo_type TEXT
        )
    """)

    # inventory_batches (보충용)
    conn.execute("""
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, item_cd TEXT, receiving_date TEXT,
            expiry_date TEXT, delivery_type TEXT,
            remaining_qty INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
            created_at TEXT
        )
    """)

    # order_tracking (기존 호환)
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT, order_date TEXT, item_cd TEXT, item_nm TEXT,
            mid_cd TEXT, delivery_type TEXT, order_qty INTEGER,
            remaining_qty INTEGER, arrival_time TEXT, expiry_time TEXT,
            status TEXT, alert_sent INTEGER DEFAULT 0, created_at TEXT,
            updated_at TEXT, actual_receiving_qty INTEGER,
            actual_arrival_time TEXT, order_source TEXT,
            pending_confirmed INTEGER, pending_confirmed_at TEXT,
            ord_input_id TEXT
        )
    """)

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def common_db(tmp_path):
    """테스트용 common DB"""
    db_path = tmp_path / "common.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY, item_nm TEXT NOT NULL,
            mid_cd TEXT NOT NULL, created_at TEXT, updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY, item_nm TEXT,
            expiration_days INTEGER, order_unit_qty INTEGER DEFAULT 1,
            created_at TEXT, updated_at TEXT, sell_price INTEGER,
            margin_rate REAL, store_id TEXT, small_cd TEXT, small_nm TEXT
        )
    """)
    now = datetime.now().isoformat()
    # 테스트 상품들
    products = [
        ("ITEM_2CHA_001", "삼)소고기고추장삼각2", "002"),
        ("ITEM_2CHA_004", "샌)햄치즈샌드2", "004"),
        ("ITEM_1CHA_001", "도)한돈불백정식1", "001"),
        ("ITEM_BREAD", "삼립)보름달쑥임자", "012"),
        ("ITEM_SOLD_OUT", "삼)참치마요삼각2", "002"),
    ]
    for item_cd, item_nm, mid_cd in products:
        conn.execute(
            "INSERT INTO products VALUES (?,?,?,?,?)",
            (item_cd, item_nm, mid_cd, now, now),
        )
    conn.commit()
    conn.close()
    return db_path


def _setup_store_db(db_path, common_path, items, sales=None):
    """receiving_history + daily_sales 데이터 설정"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"ATTACH DATABASE '{str(common_path).replace(chr(92), '/')}' AS common")

    for item in items:
        conn.execute(
            """INSERT INTO receiving_history
            (store_id, receiving_date, item_cd, item_nm, mid_cd,
             receiving_qty, delivery_type, center_nm)
            VALUES (?,?,?,?,?,?,?,?)""",
            ("46513", item["recv_date"], item["item_cd"], item.get("item_nm", ""),
             item.get("mid_cd", ""), item.get("qty", 1),
             item["delivery_type"], "테스트센터"),
        )

    for sale in (sales or []):
        conn.execute(
            """INSERT INTO daily_sales
            (store_id, sales_date, item_cd, mid_cd, stock_qty, sale_qty, created_at)
            VALUES (?,?,?,?,?,?,?)""",
            ("46513", sale["date"], sale["item_cd"], sale.get("mid_cd", ""),
             sale["stock_qty"], sale.get("sale_qty", 0), datetime.now().isoformat()),
        )

    conn.commit()
    conn.close()


class TestExpiryAlertReceivingBased:
    """Plan 시나리오 6개 테스트"""

    def _make_checker(self, db_path, common_path):
        """ExpiryChecker를 테스트 DB로 연결"""
        from src.alert.expiry_checker import ExpiryChecker

        checker = ExpiryChecker(store_id="46513", store_name="테스트점")

        # DB 연결을 테스트 DB로 교체
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"ATTACH DATABASE '{str(common_path).replace(chr(92), '/')}' AS common")
        conn.row_factory = sqlite3.Row
        checker.conn = conn
        return checker

    def test_s1_ai_order_2cha_14h(self, expiry_db, common_db):
        """S1: AI 발주 + 2차 입고 + 14:00 폐기 → 알림 대상"""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        _setup_store_db(expiry_db, common_db, [
            {"item_cd": "ITEM_2CHA_001", "recv_date": yesterday,
             "delivery_type": "2차", "mid_cd": "002", "qty": 1},
        ], sales=[
            {"item_cd": "ITEM_2CHA_001", "date": today, "stock_qty": 1, "mid_cd": "002"},
        ])

        checker = self._make_checker(expiry_db, common_db)
        try:
            items = checker._get_receiving_items_expiring_at(14, today)
            assert len(items) >= 1
            assert items[0]["item_cd"] == "ITEM_2CHA_001"
            assert items[0]["delivery_type"] == "2차"
        finally:
            checker.close()

    def test_s2_manual_order_2cha_14h(self, expiry_db, common_db):
        """S2 (핵심): 수동 발주 + 2차 입고 + 14:00 폐기 → 알림 대상
        order_tracking에 없어도 receiving_history만으로 감지"""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        _setup_store_db(expiry_db, common_db, [
            {"item_cd": "ITEM_2CHA_001", "recv_date": yesterday,
             "delivery_type": "2차", "mid_cd": "002", "qty": 1},
        ], sales=[
            {"item_cd": "ITEM_2CHA_001", "date": today, "stock_qty": 1, "mid_cd": "002"},
        ])

        # order_tracking에는 아무 데이터도 없음 (수동 발주)
        checker = self._make_checker(expiry_db, common_db)
        try:
            items = checker.get_items_expiring_at(14)
            assert len(items) >= 1
            found = [i for i in items if i["item_cd"] == "ITEM_2CHA_001"]
            assert len(found) == 1
        finally:
            checker.close()

    def test_s3_stock_zero_excluded(self, expiry_db, common_db):
        """S3: stock_qty=0 (이미 판매) → 알림 대상에서 제외"""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        _setup_store_db(expiry_db, common_db, [
            {"item_cd": "ITEM_SOLD_OUT", "recv_date": yesterday,
             "delivery_type": "2차", "mid_cd": "002", "qty": 1},
        ], sales=[
            {"item_cd": "ITEM_SOLD_OUT", "date": today, "stock_qty": 0, "mid_cd": "002"},
        ])

        checker = self._make_checker(expiry_db, common_db)
        try:
            items = checker._get_receiving_items_expiring_at(14, today)
            found = [i for i in items if i["item_cd"] == "ITEM_SOLD_OUT"]
            assert len(found) == 0  # stock=0이므로 제외
        finally:
            checker.close()

    def test_s4_1cha_dosirak_02h(self, expiry_db, common_db):
        """S4: 1차 입고 도시락 + 02:00 폐기"""
        today = datetime.now().strftime("%Y-%m-%d")
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

        _setup_store_db(expiry_db, common_db, [
            {"item_cd": "ITEM_1CHA_001", "recv_date": two_days_ago,
             "delivery_type": "1차", "mid_cd": "001", "qty": 1},
        ], sales=[
            {"item_cd": "ITEM_1CHA_001", "date": today, "stock_qty": 1, "mid_cd": "001"},
        ])

        checker = self._make_checker(expiry_db, common_db)
        try:
            items = checker._get_receiving_items_expiring_at(2, today)
            found = [i for i in items if i["item_cd"] == "ITEM_1CHA_001"]
            assert len(found) == 1
            assert found[0]["delivery_type"] == "1차"
        finally:
            checker.close()

    def test_s5_bread_not_in_mapping(self, expiry_db, common_db):
        """S5: 빵(012) ambient → EXPIRY_HOUR_TO_RECEIVING에 없으므로 빈 리스트"""
        today = datetime.now().strftime("%Y-%m-%d")

        checker = self._make_checker(expiry_db, common_db)
        try:
            # expiry_hour=0은 매핑에 없음 → 빈 리스트
            items = checker._get_receiving_items_expiring_at(0, today)
            assert items == []
        finally:
            checker.close()

    def test_s6_no_receiving_empty_result(self, expiry_db, common_db):
        """S6: 해당 시간 입고 건 없음 → 0건"""
        today = datetime.now().strftime("%Y-%m-%d")

        # receiving_history 비어있음
        checker = self._make_checker(expiry_db, common_db)
        try:
            items = checker.get_items_expiring_at(14)
            assert items == []
        finally:
            checker.close()

    def test_mapping_offsets_correct(self):
        """EXPIRY_HOUR_TO_RECEIVING offset 값 검증"""
        from src.alert.expiry_checker import ExpiryChecker

        m = ExpiryChecker.EXPIRY_HOUR_TO_RECEIVING
        assert m[2][0] == ("1차", ["001", "002", "003"], -2)
        assert m[10][0] == ("2차", ["004", "005"], -3)
        assert m[14][0] == ("2차", ["001", "002", "003"], -1)
        assert m[22][0] == ("1차", ["004", "005"], -3)
        assert 0 not in m  # 빵은 별도 처리
