"""재고 수명(TTL) 대시보드 API 테스트.

Design Reference: docs/02-design/features/inventory-ttl-dashboard.design.md
테스트 항목:
1. ttl-summary 응답 형식 (2)
2. ttl-summary 스테일 분류 (3)
3. batch-expiry 응답 형식 (2)
4. batch-expiry 빈 테이블 (1)
5. TTL 계산 검증 (2)
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch
from pathlib import Path

import pytest


@pytest.fixture
def inv_db(tmp_path):
    """테스트용 store DB (realtime_inventory + inventory_batches)."""
    db_path = tmp_path / "data" / "stores" / "46513.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
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
            queried_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, item_cd)
        )
    """)
    conn.execute("""
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
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
            UNIQUE(store_id, item_cd, receiving_date)
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def common_db(tmp_path):
    """테스트용 common DB (product_details + products + mid_categories)."""
    db_path = tmp_path / "data" / "common.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
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
    conn.execute("""
        CREATE TABLE mid_categories (
            mid_cd TEXT PRIMARY KEY,
            mid_nm TEXT
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def inv_app(inv_db, common_db):
    """테스트용 Flask 앱."""
    assert inv_db == common_db  # tmp_path가 같아야 함

    from flask import Flask
    from src.web.routes.api_inventory import inventory_bp

    app = Flask(__name__)
    app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
    app.config["TESTING"] = True
    return app


def _setup_test_data(tmp_path, items, batches=None, products=None):
    """테스트 데이터 삽입 헬퍼."""
    store_db = tmp_path / "data" / "stores" / "46513.db"
    common_db = tmp_path / "data" / "common.db"
    now = datetime.now().isoformat()

    conn = sqlite3.connect(str(store_db))
    for item in items:
        conn.execute("""
            INSERT OR REPLACE INTO realtime_inventory
            (store_id, item_cd, item_nm, stock_qty, pending_qty, is_available, queried_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "46513", item["item_cd"], item.get("item_nm", ""),
            item.get("stock_qty", 0), item.get("pending_qty", 0),
            item.get("is_available", 1), item["queried_at"], now
        ))

    if batches:
        for b in batches:
            conn.execute("""
                INSERT INTO inventory_batches
                (store_id, item_cd, item_nm, mid_cd, receiving_date, expiration_days,
                 expiry_date, initial_qty, remaining_qty, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """, (
                "46513", b["item_cd"], b.get("item_nm", ""), b.get("mid_cd", ""),
                b["receiving_date"], b["expiration_days"], b["expiry_date"],
                b["initial_qty"], b["remaining_qty"], now, now
            ))

    conn.commit()
    conn.close()

    if products:
        conn = sqlite3.connect(str(common_db))
        for p in products:
            conn.execute(
                "INSERT OR REPLACE INTO products (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
                (p["item_cd"], p.get("item_nm", ""), p.get("mid_cd", ""))
            )
            if "expiration_days" in p:
                conn.execute(
                    "INSERT OR REPLACE INTO product_details (item_cd, item_nm, expiration_days) VALUES (?, ?, ?)",
                    (p["item_cd"], p.get("item_nm", ""), p["expiration_days"])
                )
            if "mid_cd" in p and "mid_nm" in p:
                conn.execute(
                    "INSERT OR REPLACE INTO mid_categories (mid_cd, mid_nm) VALUES (?, ?)",
                    (p["mid_cd"], p["mid_nm"])
                )
        conn.commit()
        conn.close()


# =====================================================
# 1. ttl-summary 응답 형식 (2 tests)
# =====================================================

class TestTtlSummaryFormat:
    """GET /api/inventory/ttl-summary 응답 형식."""

    def test_summary_empty_store(self, inv_app, inv_db):
        """데이터 없으면 기본값 반환."""
        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/ttl-summary?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_items"] == 0
        assert data["stale_items"] == 0
        assert "freshness_distribution" in data
        assert "ttl_distribution" in data

    def test_summary_has_required_fields(self, inv_app, inv_db):
        """응답에 필수 필드 포함."""
        now = datetime.now().isoformat()
        _setup_test_data(inv_db, [
            {"item_cd": "A001", "item_nm": "테스트", "stock_qty": 5, "queried_at": now},
        ])

        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/ttl-summary?store_id=46513")

        data = resp.get_json()
        required = ["store_id", "total_items", "stale_items", "stale_stock_qty",
                     "freshness_distribution", "ttl_distribution",
                     "category_breakdown", "stale_items_list"]
        for field in required:
            assert field in data, f"Missing field: {field}"


# =====================================================
# 2. ttl-summary 스테일 분류 (3 tests)
# =====================================================

class TestTtlSummaryStaleClassification:
    """스테일/워닝/프레시 분류 로직."""

    def test_fresh_item_classified(self, inv_app, inv_db):
        """최근 조회 상품은 fresh."""
        now = datetime.now().isoformat()
        _setup_test_data(inv_db, [
            {"item_cd": "B001", "stock_qty": 10, "queried_at": now},
        ])

        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/ttl-summary?store_id=46513")

        data = resp.get_json()
        assert data["freshness_distribution"]["fresh"] == 1
        assert data["freshness_distribution"]["stale"] == 0

    def test_stale_item_classified(self, inv_app, inv_db):
        """TTL 초과 상품은 stale."""
        old_time = (datetime.now() - timedelta(hours=48)).isoformat()
        _setup_test_data(inv_db, [
            {"item_cd": "C001", "stock_qty": 5, "queried_at": old_time},
        ])

        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/ttl-summary?store_id=46513")

        data = resp.get_json()
        assert data["freshness_distribution"]["stale"] >= 1
        assert data["stale_items"] >= 1
        assert data["stale_stock_qty"] >= 5

    def test_stale_items_list_populated(self, inv_app, inv_db):
        """stale_items_list에 상세 정보 포함."""
        old_time = (datetime.now() - timedelta(hours=50)).isoformat()
        _setup_test_data(inv_db, [
            {"item_cd": "D001", "item_nm": "스테일 상품", "stock_qty": 8, "queried_at": old_time},
        ])

        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/ttl-summary?store_id=46513")

        data = resp.get_json()
        stale_list = data["stale_items_list"]
        assert len(stale_list) >= 1
        item = stale_list[0]
        assert "item_cd" in item
        assert "stock_qty" in item
        assert "hours_since_query" in item
        assert "ttl_hours" in item


# =====================================================
# 3. batch-expiry 응답 형식 (2 tests)
# =====================================================

class TestBatchExpiryFormat:
    """GET /api/inventory/batch-expiry 응답 형식."""

    def test_batch_expiry_basic(self, inv_app, inv_db):
        """배치 만료 기본 응답."""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        _setup_test_data(inv_db, [], batches=[
            {
                "item_cd": "E001", "item_nm": "도시락",
                "mid_cd": "001", "receiving_date": yesterday,
                "expiration_days": 1, "expiry_date": today,
                "initial_qty": 10, "remaining_qty": 3,
            },
        ])

        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/batch-expiry?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "batches" in data
        assert "summary" in data
        assert data["summary"]["total_expiring_qty"] >= 3

    def test_batch_expiry_has_labels(self, inv_app, inv_db):
        """배치에 날짜 라벨(오늘/내일/모레) 포함."""
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        _setup_test_data(inv_db, [], batches=[
            {
                "item_cd": "F001", "receiving_date": yesterday,
                "expiration_days": 1, "expiry_date": today,
                "initial_qty": 5, "remaining_qty": 2,
            },
            {
                "item_cd": "F002", "receiving_date": today,
                "expiration_days": 1, "expiry_date": tomorrow,
                "initial_qty": 8, "remaining_qty": 8,
            },
        ])

        with inv_app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", inv_db):
                resp = client.get("/api/inventory/batch-expiry?store_id=46513")

        data = resp.get_json()
        labels = [b["label"] for b in data["batches"]]
        assert "오늘" in labels or "내일" in labels


# =====================================================
# 4. batch-expiry 빈 테이블 (1 test)
# =====================================================

class TestBatchExpiryEmpty:
    """inventory_batches 없는 환경."""

    def test_no_batches_table_returns_empty(self, tmp_path):
        """inventory_batches 테이블 없으면 빈 배열."""
        from flask import Flask
        from src.web.routes.api_inventory import inventory_bp

        app = Flask(__name__)
        app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
        app.config["TESTING"] = True

        # inventory_batches 없는 DB 생성
        store_dir = tmp_path / "data" / "stores"
        store_dir.mkdir(parents=True, exist_ok=True)
        db_path = store_dir / "46513.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE realtime_inventory (
                id INTEGER PRIMARY KEY,
                store_id TEXT, item_cd TEXT, item_nm TEXT,
                stock_qty INTEGER, pending_qty INTEGER,
                is_available INTEGER DEFAULT 1, queried_at TEXT, created_at TEXT,
                UNIQUE(store_id, item_cd)
            )
        """)
        conn.commit()
        conn.close()

        with app.test_client() as client:
            with patch("src.web.routes.api_inventory.PROJECT_ROOT", tmp_path):
                resp = client.get("/api/inventory/batch-expiry?store_id=46513")

        data = resp.get_json()
        assert data["batches"] == []
        assert data["summary"]["total_expiring_qty"] == 0


# =====================================================
# 5. TTL 계산 검증 (2 tests)
# =====================================================

class TestTtlCalculation:
    """_get_stale_hours() TTL 변환 검증."""

    def test_food_ttl_18h(self):
        """유통기한 1일 → 18시간."""
        from src.web.routes.api_inventory import _get_stale_hours

        assert _get_stale_hours(1) == 18
        assert _get_stale_hours(2) == 36
        assert _get_stale_hours(3) == 54

    def test_default_ttl_36h(self):
        """유통기한 4일+ 또는 None → 36시간."""
        from src.web.routes.api_inventory import _get_stale_hours

        assert _get_stale_hours(7) == 36
        assert _get_stale_hours(None) == 36
        assert _get_stale_hours(30) == 36
