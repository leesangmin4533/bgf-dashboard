"""
RI stale 카테고리별 분기 테스트

- 푸드/디저트: stale → stock=0 (기존 동작 유지)
- 비식품(라면/스낵/담배 등): stale → RI 제외 → daily_sales fallback
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.prediction.pre_order_evaluator import PreOrderEvaluator, STALE_ZERO_CATEGORIES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def stale_db():
    """RI + product_details + products + daily_sales 가 있는 in-memory DB"""
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
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            order_unit_qty INTEGER DEFAULT 1,
            expiration_days INTEGER,
            orderable_day TEXT DEFAULT '',
            demand_pattern TEXT DEFAULT NULL
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
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT,
            sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            created_at TEXT,
            promo_type TEXT DEFAULT '',
            store_id TEXT DEFAULT '46513',
            UNIQUE(sales_date, item_cd)
        )
    """)
    yield conn
    conn.close()


def _make_evaluator(store_id=None):
    """최소한의 PreOrderEvaluator (DB 연결 없이)
    store_id=None → prefix="" (in-memory DB에서 common. prefix 없이 직접 접근)
    """
    evaluator = PreOrderEvaluator.__new__(PreOrderEvaluator)
    evaluator.store_id = store_id
    evaluator.eval_config = None
    evaluator.conn = None
    evaluator._cost_optimizer = None
    return evaluator


def _insert_ri(conn, item_cd, stock_qty, pending_qty, queried_at, store_id="46513"):
    """realtime_inventory 행 삽입"""
    conn.execute(
        "INSERT INTO realtime_inventory (store_id, item_cd, stock_qty, pending_qty, queried_at, is_available, is_cut_item) "
        "VALUES (?, ?, ?, ?, ?, 1, 0)",
        (store_id, item_cd, stock_qty, pending_qty, queried_at)
    )


def _insert_product(conn, item_cd, mid_cd, expiry_days=None):
    """product_details + products 행 삽입"""
    conn.execute(
        "INSERT INTO product_details (item_cd, mid_cd, expiration_days) VALUES (?, ?, ?)",
        (item_cd, mid_cd, expiry_days)
    )
    conn.execute(
        "INSERT INTO products (item_cd, mid_cd) VALUES (?, ?)",
        (item_cd, mid_cd)
    )


def _insert_daily_sales(conn, item_cd, stock_qty, store_id="46513"):
    """daily_sales fallback용 행 삽입"""
    conn.execute(
        "INSERT INTO daily_sales (sales_date, item_cd, stock_qty, store_id) VALUES (?, ?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d"), item_cd, stock_qty, store_id)
    )


FRESH_TIME = (datetime.now() - timedelta(minutes=10)).isoformat()
STALE_TIME = (datetime.now() - timedelta(hours=72)).isoformat()


# ---------------------------------------------------------------------------
# 상수 검증
# ---------------------------------------------------------------------------

class TestStaleZeroCategories:
    """STALE_ZERO_CATEGORIES 상수 검증"""

    def test_contains_all_food_categories(self):
        """푸드 6종 포함"""
        for mid in ["001", "002", "003", "004", "005", "012"]:
            assert mid in STALE_ZERO_CATEGORIES

    def test_contains_dessert(self):
        """디저트(014) 포함"""
        assert "014" in STALE_ZERO_CATEGORIES

    def test_excludes_non_food(self):
        """비식품 미포함"""
        for mid in ["006", "015", "032", "049", "050", "072", "073", "900"]:
            assert mid not in STALE_ZERO_CATEGORIES


# ---------------------------------------------------------------------------
# 푸드/디저트: stale → stock=0 (기존 동작 유지)
# ---------------------------------------------------------------------------

class TestStaleFoodDesserStockZero:
    """푸드/디저트 stale 시 stock=0 처리 (기존 동작)"""

    def test_food_dosirak_stale_stock_zero(self, stale_db):
        """도시락(001) stale → stock=0"""
        _insert_ri(stale_db, "FOOD001", 15, 0, STALE_TIME)
        _insert_product(stale_db, "FOOD001", "001", expiry_days=1)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["FOOD001"], stale_db)

        assert "FOOD001" in result
        stock, pending = result["FOOD001"]
        assert stock == 0  # stale → 0

    def test_food_bread_stale_stock_zero(self, stale_db):
        """빵(012) stale → stock=0"""
        _insert_ri(stale_db, "BREAD01", 8, 0, STALE_TIME)
        _insert_product(stale_db, "BREAD01", "012", expiry_days=3)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["BREAD01"], stale_db)

        assert "BREAD01" in result
        stock, pending = result["BREAD01"]
        assert stock == 0

    def test_dessert_stale_stock_zero(self, stale_db):
        """디저트(014) stale → stock=0"""
        _insert_ri(stale_db, "DSRT001", 5, 0, STALE_TIME)
        _insert_product(stale_db, "DSRT001", "014", expiry_days=2)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["DSRT001"], stale_db)

        assert "DSRT001" in result
        stock, pending = result["DSRT001"]
        assert stock == 0


# ---------------------------------------------------------------------------
# 비식품: stale → RI 제외 → daily_sales fallback
# ---------------------------------------------------------------------------

class TestStaleNonFoodSkipped:
    """비식품 stale 시 RI 값 무시 → daily_sales fallback 사용"""

    def test_ramen_stale_uses_daily_sales_not_ri(self, stale_db):
        """라면(006) stale → RI(stock=50) 무시, daily_sales(stock=11) 사용"""
        _insert_ri(stale_db, "RAMEN01", 50, 0, STALE_TIME)
        _insert_product(stale_db, "RAMEN01", "006", expiry_days=117)
        _insert_daily_sales(stale_db, "RAMEN01", 11)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["RAMEN01"], stale_db)

        assert "RAMEN01" in result
        stock, pending = result["RAMEN01"]
        assert stock == 11  # RI(50)이 아닌 daily_sales(11) 값
        assert pending == 0  # daily_sales에서는 pending=0

    def test_ramen_stale_no_daily_sales_fallback_zero(self, stale_db):
        """라면(006) stale + daily_sales 없음 → (0,0) fallback"""
        _insert_ri(stale_db, "RAMEN01", 50, 0, STALE_TIME)
        _insert_product(stale_db, "RAMEN01", "006", expiry_days=117)
        # daily_sales 데이터 없음

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["RAMEN01"], stale_db)

        stock, pending = result["RAMEN01"]
        assert stock == 0  # fallback (0,0)
        assert pending == 0

    def test_snack_stale_uses_daily_sales(self, stale_db):
        """스낵(015) stale → daily_sales fallback"""
        _insert_ri(stale_db, "SNACK01", 20, 5, STALE_TIME)
        _insert_product(stale_db, "SNACK01", "015", expiry_days=90)
        _insert_daily_sales(stale_db, "SNACK01", 18)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["SNACK01"], stale_db)

        stock, pending = result["SNACK01"]
        assert stock == 18  # RI(20)이 아닌 daily_sales(18)
        assert pending == 0  # RI pending(5) 무시됨

    def test_tobacco_stale_ignores_ri_stock(self, stale_db):
        """담배(072) stale → RI stock(50) 무시"""
        _insert_ri(stale_db, "TBCCO01", 50, 0, STALE_TIME)
        _insert_product(stale_db, "TBCCO01", "072", expiry_days=365)
        _insert_daily_sales(stale_db, "TBCCO01", 45)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["TBCCO01"], stale_db)

        stock, pending = result["TBCCO01"]
        assert stock == 45  # daily_sales 값

    def test_mid_cd_none_stale_ignores_ri(self, stale_db):
        """mid_cd=None stale → RI 무시 (None은 STALE_ZERO에 없으므로)"""
        _insert_ri(stale_db, "UNKNWN01", 5, 0, STALE_TIME)
        stale_db.execute(
            "INSERT INTO product_details (item_cd, mid_cd, expiration_days) VALUES (?, NULL, NULL)",
            ("UNKNWN01",)
        )
        stale_db.execute(
            "INSERT INTO products (item_cd, mid_cd) VALUES (?, NULL)",
            ("UNKNWN01",)
        )
        _insert_daily_sales(stale_db, "UNKNWN01", 3)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["UNKNWN01"], stale_db)

        stock, pending = result["UNKNWN01"]
        assert stock == 3  # RI(5) 아닌 daily_sales(3)


# ---------------------------------------------------------------------------
# Fresh RI → 카테고리 무관하게 정상 사용
# ---------------------------------------------------------------------------

class TestFreshRIUnaffected:
    """Fresh RI 데이터는 카테고리 무관하게 정상 반환"""

    def test_food_fresh_returns_actual_stock(self, stale_db):
        """푸드 fresh → 실제 재고"""
        _insert_ri(stale_db, "FOOD001", 15, 3, FRESH_TIME)
        _insert_product(stale_db, "FOOD001", "001", expiry_days=1)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["FOOD001"], stale_db)

        stock, pending = result["FOOD001"]
        assert stock == 15
        assert pending == 3

    def test_ramen_fresh_returns_actual_stock(self, stale_db):
        """라면 fresh → 실제 재고"""
        _insert_ri(stale_db, "RAMEN01", 11, 0, FRESH_TIME)
        _insert_product(stale_db, "RAMEN01", "006", expiry_days=117)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["RAMEN01"], stale_db)

        stock, pending = result["RAMEN01"]
        assert stock == 11
        assert pending == 0

    def test_snack_fresh_returns_actual_stock(self, stale_db):
        """스낵 fresh → 실제 재고"""
        _insert_ri(stale_db, "SNACK01", 20, 5, FRESH_TIME)
        _insert_product(stale_db, "SNACK01", "015", expiry_days=90)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["SNACK01"], stale_db)

        stock, pending = result["SNACK01"]
        assert stock == 20
        assert pending == 5


# ---------------------------------------------------------------------------
# 혼합 시나리오
# ---------------------------------------------------------------------------

class TestMixedScenarios:
    """푸드+비식품 혼합 배치 테스트"""

    def test_mixed_batch_food_stale_zero_ramen_stale_fallback(self, stale_db):
        """같은 배치: 도시락(stale)→0, 라면(stale)→DS fallback"""
        _insert_ri(stale_db, "FOOD001", 15, 0, STALE_TIME)
        _insert_product(stale_db, "FOOD001", "001", expiry_days=1)

        _insert_ri(stale_db, "RAMEN01", 11, 0, STALE_TIME)
        _insert_product(stale_db, "RAMEN01", "006", expiry_days=117)
        _insert_daily_sales(stale_db, "RAMEN01", 11)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["FOOD001", "RAMEN01"], stale_db)

        # 푸드: stock=0
        food_stock, _ = result["FOOD001"]
        assert food_stock == 0

        # 라면: daily_sales fallback = 11
        ramen_stock, _ = result["RAMEN01"]
        assert ramen_stock == 11

    def test_mixed_fresh_and_stale(self, stale_db):
        """fresh 비식품(RI) + stale 비식품(DS fallback)"""
        _insert_ri(stale_db, "SNACK_F", 20, 0, FRESH_TIME)
        _insert_product(stale_db, "SNACK_F", "015", expiry_days=90)

        _insert_ri(stale_db, "SNACK_S", 30, 0, STALE_TIME)
        _insert_product(stale_db, "SNACK_S", "015", expiry_days=90)
        _insert_daily_sales(stale_db, "SNACK_S", 25)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["SNACK_F", "SNACK_S"], stale_db)

        # fresh → RI 값 사용
        assert result["SNACK_F"][0] == 20

        # stale → daily_sales fallback (RI의 30이 아닌 DS의 25)
        assert result["SNACK_S"][0] == 25

    def test_stale_pending_preserved_for_food(self, stale_db):
        """푸드 stale: stock=0이지만 pending은 유지"""
        _insert_ri(stale_db, "FOOD001", 15, 8, STALE_TIME)
        _insert_product(stale_db, "FOOD001", "001", expiry_days=1)

        evaluator = _make_evaluator()
        result = evaluator._batch_load_inventory(["FOOD001"], stale_db)

        stock, pending = result["FOOD001"]
        assert stock == 0
        assert pending == 8  # pending은 유지
