"""카테고리 3단계 드릴다운 API 테스트.

Design Reference: docs/02-design/features/category-drilldown.design.md
테스트 항목:
1. tree 빈 DB (1)
2. tree 응답 구조 (1)
3. tree 집계 정확성 (1)
4. summary large 레벨 (1)
5. summary mid 레벨 (1)
6. summary small 레벨 (1)
7. summary 잘못된 level (1)
8. summary 존재하지 않는 코드 (1)
9. products 기본 목록 (1)
10. products 페이지네이션 (1)
11. products 정렬 (1)
12. products 잘못된 level (1)
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def cat_common_db(tmp_path):
    """테스트용 common DB (products + product_details + mid_categories)."""
    db_path = tmp_path / "data" / "common.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            expiration_days INTEGER,
            large_cd TEXT,
            small_cd TEXT,
            small_nm TEXT,
            class_nm TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE mid_categories (
            mid_cd TEXT PRIMARY KEY,
            mid_nm TEXT,
            large_cd TEXT,
            large_nm TEXT,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def cat_store_db(tmp_path):
    """테스트용 store DB (daily_sales + realtime_inventory)."""
    db_path = tmp_path / "data" / "stores" / "46513.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            sale_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            UNIQUE(store_id, item_cd, sales_date)
        )
    """)
    conn.execute("""
        CREATE TABLE realtime_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            stock_qty INTEGER DEFAULT 0,
            pending_qty INTEGER DEFAULT 0,
            is_available INTEGER DEFAULT 1,
            queried_at TEXT,
            created_at TEXT DEFAULT '',
            UNIQUE(store_id, item_cd)
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def cat_app(cat_common_db, cat_store_db):
    """테스트용 Flask 앱."""
    assert cat_common_db == cat_store_db  # tmp_path 동일

    from flask import Flask
    from src.web.routes.api_category import category_bp

    app = Flask(__name__)
    app.register_blueprint(category_bp, url_prefix="/api/categories")
    app.config["TESTING"] = True
    return app


def _insert_test_data(tmp_path, mid_categories=None, products=None,
                      product_details=None, sales=None, inventory=None):
    """테스트 데이터 삽입 헬퍼."""
    common_db = tmp_path / "data" / "common.db"
    store_db = tmp_path / "data" / "stores" / "46513.db"
    now = datetime.now().isoformat()

    conn = sqlite3.connect(str(common_db))

    if mid_categories:
        for mc in mid_categories:
            conn.execute(
                "INSERT OR REPLACE INTO mid_categories "
                "(mid_cd, mid_nm, large_cd, large_nm, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mc["mid_cd"], mc["mid_nm"], mc.get("large_cd"),
                 mc.get("large_nm"), now, now)
            )

    if products:
        for p in products:
            conn.execute(
                "INSERT OR REPLACE INTO products "
                "(item_cd, item_nm, mid_cd, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (p["item_cd"], p.get("item_nm", ""), p.get("mid_cd", ""), now, now)
            )

    if product_details:
        for pd in product_details:
            conn.execute(
                "INSERT OR REPLACE INTO product_details "
                "(item_cd, item_nm, large_cd, small_cd, small_nm, class_nm) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pd["item_cd"], pd.get("item_nm", ""),
                 pd.get("large_cd"), pd.get("small_cd"),
                 pd.get("small_nm"), pd.get("class_nm"))
            )

    conn.commit()
    conn.close()

    if sales or inventory:
        conn = sqlite3.connect(str(store_db))

        if sales:
            for s in sales:
                conn.execute(
                    "INSERT OR REPLACE INTO daily_sales "
                    "(store_id, item_cd, sales_date, sale_qty, disuse_qty) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("46513", s["item_cd"], s["sales_date"],
                     s.get("sale_qty", 0),
                     s.get("disuse_qty", 0))
                )

        if inventory:
            for inv in inventory:
                conn.execute(
                    "INSERT OR REPLACE INTO realtime_inventory "
                    "(store_id, item_cd, item_nm, stock_qty, pending_qty, "
                    "is_available, queried_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("46513", inv["item_cd"], inv.get("item_nm", ""),
                     inv.get("stock_qty", 0), inv.get("pending_qty", 0),
                     inv.get("is_available", 1), now, now)
                )

        conn.commit()
        conn.close()


# =====================================================
# 1. tree 빈 DB (1 test)
# =====================================================

class TestTreeEmpty:
    """빈 DB에서 트리 반환."""

    def test_empty_tree(self, cat_app, cat_common_db):
        """데이터 없으면 빈 트리 반환."""
        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/tree")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["tree"] == []
        assert data["summary"]["large_count"] == 0
        assert data["summary"]["mid_count"] == 0


# =====================================================
# 2. tree 응답 구조 (1 test)
# =====================================================

class TestTreeStructure:
    """트리 응답 구조 검증."""

    def test_tree_has_hierarchy(self, cat_app, cat_common_db):
        """대분류 -> 중분류 -> 소분류 계층 구조 확인."""
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
            ],
            products=[
                {"item_cd": "A001", "item_nm": "참치도시락", "mid_cd": "001"},
            ],
            product_details=[
                {"item_cd": "A001", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/tree")

        data = resp.get_json()
        assert len(data["tree"]) >= 1

        large = data["tree"][0]
        assert "large_cd" in large
        assert "large_nm" in large
        assert "children" in large

        mid = large["children"][0]
        assert "mid_cd" in mid
        assert "mid_nm" in mid
        assert "children" in mid

        small = mid["children"][0]
        assert "small_cd" in small
        assert "small_nm" in small
        assert "product_count" in small


# =====================================================
# 3. tree 집계 정확성 (1 test)
# =====================================================

class TestTreeCounts:
    """트리 집계 수치 검증."""

    def test_tree_counts_accurate(self, cat_app, cat_common_db):
        """mid_count, small_count 정확히 집계."""
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
                {"mid_cd": "002", "mid_nm": "주먹밥", "large_cd": "01", "large_nm": "식품"},
                {"mid_cd": "049", "mid_nm": "맥주", "large_cd": "05", "large_nm": "주류"},
            ],
            products=[
                {"item_cd": "A001", "item_nm": "참치도시락", "mid_cd": "001"},
                {"item_cd": "A002", "item_nm": "불고기도시락", "mid_cd": "001"},
                {"item_cd": "B001", "item_nm": "참치주먹밥", "mid_cd": "002"},
                {"item_cd": "C001", "item_nm": "카스", "mid_cd": "049"},
            ],
            product_details=[
                {"item_cd": "A001", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
                {"item_cd": "A002", "large_cd": "01", "small_cd": "00102", "small_nm": "프리미엄도시락"},
                {"item_cd": "B001", "large_cd": "01", "small_cd": "00201", "small_nm": "삼각김밥"},
                {"item_cd": "C001", "large_cd": "05", "small_cd": "04901", "small_nm": "국산맥주"},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/tree")

        data = resp.get_json()
        summary = data["summary"]
        assert summary["large_count"] == 2  # 01, 05
        assert summary["mid_count"] == 3    # 001, 002, 049
        assert summary["product_count"] == 4


# =====================================================
# 4. summary large 레벨 (1 test)
# =====================================================

class TestSummaryLarge:
    """large 레벨 요약."""

    def test_large_summary_has_fields(self, cat_app, cat_common_db):
        """large 레벨 요약에 sales, waste, inventory 필드."""
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
            ],
            products=[
                {"item_cd": "A001", "item_nm": "참치도시락", "mid_cd": "001"},
            ],
            product_details=[
                {"item_cd": "A001", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
            ],
            sales=[
                {"item_cd": "A001", "sales_date": today, "sale_qty": 10, "disuse_qty": 2},
            ],
            inventory=[
                {"item_cd": "A001", "stock_qty": 5, "pending_qty": 3},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/large/01/summary?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["level"] == "large"
        assert data["code"] == "01"
        assert "sales" in data
        assert "waste" in data
        assert "inventory" in data
        assert data["sales"]["total_qty"] == 10
        assert data["waste"]["total_qty"] == 2
        assert data["inventory"]["total_stock"] == 5


# =====================================================
# 5. summary mid 레벨 (1 test)
# =====================================================

class TestSummaryMid:
    """mid 레벨 요약."""

    def test_mid_summary_daily_avg(self, cat_app, cat_common_db):
        """mid 레벨 일평균 계산 검증."""
        dates = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "049", "mid_nm": "맥주", "large_cd": "05", "large_nm": "주류"},
            ],
            products=[
                {"item_cd": "B001", "item_nm": "카스", "mid_cd": "049"},
            ],
            sales=[
                {"item_cd": "B001", "sales_date": dates[0], "sale_qty": 12},
                {"item_cd": "B001", "sales_date": dates[1], "sale_qty": 8},
                {"item_cd": "B001", "sales_date": dates[2], "sale_qty": 10},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/mid/049/summary?store_id=46513&days=7")

        data = resp.get_json()
        assert data["level"] == "mid"
        assert data["name"] == "맥주"
        assert data["sales"]["total_qty"] == 30
        # daily_avg = 30 / 7 = 4.3
        assert data["sales"]["daily_avg"] == pytest.approx(4.3, abs=0.1)


# =====================================================
# 6. summary small 레벨 (1 test)
# =====================================================

class TestSummarySmall:
    """small 레벨 요약."""

    def test_small_summary_item_count(self, cat_app, cat_common_db):
        """small 레벨 상품 수 정확."""
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
            ],
            products=[
                {"item_cd": "A001", "item_nm": "참치도시락", "mid_cd": "001"},
                {"item_cd": "A002", "item_nm": "불고기도시락", "mid_cd": "001"},
            ],
            product_details=[
                {"item_cd": "A001", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
                {"item_cd": "A002", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/small/00101/summary?store_id=46513")

        data = resp.get_json()
        assert data["level"] == "small"
        assert data["sales"]["item_count"] == 2


# =====================================================
# 7. summary 잘못된 level (1 test)
# =====================================================

class TestSummaryInvalidLevel:
    """잘못된 level 파라미터."""

    def test_invalid_level_returns_400(self, cat_app, cat_common_db):
        """유효하지 않은 level이면 400 반환."""
        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/invalid/001/summary?store_id=46513")

        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


# =====================================================
# 8. summary 존재하지 않는 코드 (1 test)
# =====================================================

class TestSummaryMissingCode:
    """존재하지 않는 카테고리 코드."""

    def test_missing_code_returns_zeros(self, cat_app, cat_common_db):
        """없는 코드면 모든 값이 0."""
        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/mid/999/summary?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["sales"]["total_qty"] == 0
        assert data["sales"]["item_count"] == 0
        assert data["waste"]["total_qty"] == 0
        assert data["inventory"]["total_stock"] == 0


# =====================================================
# 9. products 기본 목록 (1 test)
# =====================================================

class TestProductsList:
    """상품 목록 기본 응답."""

    def test_products_basic(self, cat_app, cat_common_db):
        """상품 목록에 products + pagination."""
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
            ],
            products=[
                {"item_cd": "A001", "item_nm": "참치도시락", "mid_cd": "001"},
                {"item_cd": "A002", "item_nm": "불고기도시락", "mid_cd": "001"},
            ],
            product_details=[
                {"item_cd": "A001", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
                {"item_cd": "A002", "large_cd": "01", "small_cd": "00101", "small_nm": "일반도시락"},
            ],
            sales=[
                {"item_cd": "A001", "sales_date": today, "sale_qty": 10},
                {"item_cd": "A002", "sales_date": today, "sale_qty": 5},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/mid/001/products?store_id=46513")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "products" in data
        assert "pagination" in data
        assert data["total_count"] == 2
        assert len(data["products"]) == 2

        # 각 상품에 필수 필드 포함
        item = data["products"][0]
        for field in ("item_cd", "item_nm", "sale_qty", "stock_qty", "pending_qty"):
            assert field in item, f"Missing field: {field}"


# =====================================================
# 10. products 페이지네이션 (1 test)
# =====================================================

class TestProductsPagination:
    """상품 목록 페이지네이션."""

    def test_pagination_limit_offset(self, cat_app, cat_common_db):
        """limit/offset으로 페이지네이션 동작."""
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
            ],
            products=[
                {"item_cd": f"P{i:03d}", "item_nm": f"상품{i}", "mid_cd": "001"}
                for i in range(5)
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get(
                    "/api/categories/mid/001/products?store_id=46513&limit=2&offset=0"
                )

        data = resp.get_json()
        assert len(data["products"]) == 2
        assert data["pagination"]["limit"] == 2
        assert data["pagination"]["offset"] == 0
        assert data["pagination"]["total"] == 5
        assert data["pagination"]["has_more"] is True

        # 2페이지
        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp2 = client.get(
                    "/api/categories/mid/001/products?store_id=46513&limit=2&offset=4"
                )

        data2 = resp2.get_json()
        assert len(data2["products"]) == 1
        assert data2["pagination"]["has_more"] is False


# =====================================================
# 11. products 정렬 (1 test)
# =====================================================

class TestProductsSort:
    """상품 목록 정렬."""

    def test_sort_by_sale_qty_desc(self, cat_app, cat_common_db):
        """sale_qty DESC 정렬."""
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_test_data(cat_common_db,
            mid_categories=[
                {"mid_cd": "001", "mid_nm": "도시락", "large_cd": "01", "large_nm": "식품"},
            ],
            products=[
                {"item_cd": "A001", "item_nm": "적게팔림", "mid_cd": "001"},
                {"item_cd": "A002", "item_nm": "많이팔림", "mid_cd": "001"},
            ],
            sales=[
                {"item_cd": "A001", "sales_date": today, "sale_qty": 3},
                {"item_cd": "A002", "sales_date": today, "sale_qty": 20},
            ],
        )

        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get(
                    "/api/categories/mid/001/products?store_id=46513&sort=sale_qty&order=desc"
                )

        data = resp.get_json()
        products = data["products"]
        assert len(products) == 2
        # 내림차순이므로 sale_qty 큰 순서
        assert products[0]["sale_qty"] >= products[1]["sale_qty"]


# =====================================================
# 12. products 잘못된 level (1 test)
# =====================================================

class TestProductsInvalidLevel:
    """잘못된 level 파라미터."""

    def test_invalid_level_returns_400(self, cat_app, cat_common_db):
        """유효하지 않은 level이면 400 반환."""
        with cat_app.test_client() as client:
            with patch("src.web.routes.api_category.PROJECT_ROOT", cat_common_db):
                resp = client.get("/api/categories/wrong/001/products?store_id=46513")

        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
