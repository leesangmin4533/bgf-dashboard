"""입고 지연 영향 분석 API 테스트.

Design Reference: docs/02-design/features/receiving-delay-analysis.design.md
테스트 항목:
1. summary 응답 형식 (1)
2. summary pending_age 분포 (1)
3. trend 응답 형식 (1)
4. trend days 파라미터 (1)
5. slow-items 응답 형식 (1)
6. slow-items pending_age 정렬 (1)
7. slow-items limit 파라미터 (1)
8. 빈 데이터 처리 (1)
9. Blueprint 등록 (1)
10. receiving.js 존재 (1)
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def rcv_db(tmp_path):
    """테스트용 store DB (receiving_history + order_tracking)."""
    db_path = tmp_path / "data" / "stores" / "46513.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            order_date TEXT NOT NULL,
            receiving_date TEXT NOT NULL,
            order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT DEFAULT '46513',
            item_cd TEXT NOT NULL,
            order_date TEXT NOT NULL,
            order_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ordered'
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def common_db(tmp_path):
    """테스트용 common DB (products)."""
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
    conn.commit()
    conn.close()
    return tmp_path


def _seed_data(tmp_path):
    """receiving_history + order_tracking + products에 테스트 데이터 삽입."""
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    d3 = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    d5 = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    d10 = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    store_path = tmp_path / "data" / "stores" / "46513.db"
    conn = sqlite3.connect(str(store_path))

    # receiving_history: 리드타임 2일, 3일 (short delivery: 2번째)
    conn.execute("""
        INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty)
        VALUES ('ITEM_A', ?, ?, 10, 10)
    """, (d5, d3))  # lead_time = 2
    conn.execute("""
        INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty)
        VALUES ('ITEM_B', ?, ?, 10, 7)
    """, (d5, yesterday))  # lead_time = 4, short delivery
    conn.execute("""
        INSERT INTO receiving_history (item_cd, order_date, receiving_date, order_qty, receiving_qty)
        VALUES ('ITEM_C', ?, ?, 5, 5)
    """, (d3, yesterday))  # lead_time = 2

    # order_tracking: pending items
    conn.execute("""
        INSERT INTO order_tracking (item_cd, order_date, order_qty, remaining_qty, status)
        VALUES ('ITEM_A', ?, 5, 3, 'ordered')
    """, (yesterday,))  # pending_age = 1
    conn.execute("""
        INSERT INTO order_tracking (item_cd, order_date, order_qty, remaining_qty, status)
        VALUES ('ITEM_B', ?, 8, 5, 'ordered')
    """, (d5,))  # pending_age = 5
    conn.execute("""
        INSERT INTO order_tracking (item_cd, order_date, order_qty, remaining_qty, status)
        VALUES ('ITEM_D', ?, 3, 2, 'ordered')
    """, (d10,))  # pending_age = 10

    conn.commit()
    conn.close()

    # common DB: products
    common_path = tmp_path / "data" / "common.db"
    conn = sqlite3.connect(str(common_path))
    conn.execute("INSERT INTO products (item_cd, item_nm, mid_cd) VALUES ('ITEM_A', '상품A', '001')")
    conn.execute("INSERT INTO products (item_cd, item_nm, mid_cd) VALUES ('ITEM_B', '상품B', '002')")
    conn.execute("INSERT INTO products (item_cd, item_nm, mid_cd) VALUES ('ITEM_D', '상품D', '003')")
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────
# 1. summary 응답 형식
# ──────────────────────────────────────────────────────────────

class TestSummaryFormat:
    def test_summary_has_required_keys(self, rcv_db, common_db):
        """summary 응답에 필수 키 존재."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/summary?store_id=46513")
            assert resp.status_code == 200
            data = resp.get_json()

            assert "avg_lead_time" in data
            assert "max_lead_time" in data
            assert "short_delivery_rate" in data
            assert "total_items_tracked" in data
            assert "pending_items_count" in data
            assert "pending_age_distribution" in data
            assert isinstance(data["avg_lead_time"], float)
            assert isinstance(data["pending_age_distribution"], dict)


# ──────────────────────────────────────────────────────────────
# 2. summary pending_age 분포
# ──────────────────────────────────────────────────────────────

class TestSummaryPending:
    def test_pending_age_distribution(self, rcv_db, common_db):
        """pending_age 분포가 올바르게 계산."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/summary?store_id=46513")
            data = resp.get_json()
            dist = data["pending_age_distribution"]

            # ITEM_A: 1일 → 0-1, ITEM_B: 5일 → 4-7, ITEM_D: 10일 → 8+
            assert dist["0-1"] >= 1
            assert dist["4-7"] >= 1
            assert dist["8+"] >= 1
            assert data["pending_items_count"] == 3


# ──────────────────────────────────────────────────────────────
# 3. trend 응답 형식
# ──────────────────────────────────────────────────────────────

class TestTrendFormat:
    def test_trend_has_required_keys(self, rcv_db, common_db):
        """trend 응답에 dates, avg_lead_times, delivery_counts 존재."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/trend?store_id=46513&days=30")
            assert resp.status_code == 200
            data = resp.get_json()

            assert "dates" in data
            assert "avg_lead_times" in data
            assert "delivery_counts" in data
            assert isinstance(data["dates"], list)
            assert len(data["dates"]) == len(data["avg_lead_times"])


# ──────────────────────────────────────────────────────────────
# 4. trend days 파라미터
# ──────────────────────────────────────────────────────────────

class TestTrendDays:
    def test_trend_respects_days_param(self, rcv_db, common_db):
        """days=7이면 최근 7일 데이터만 반환."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp30 = client.get("/api/receiving/trend?store_id=46513&days=30")
            resp7 = client.get("/api/receiving/trend?store_id=46513&days=7")
            data30 = resp30.get_json()
            data7 = resp7.get_json()

            # days=7은 days=30보다 적거나 같은 데이터
            assert len(data7["dates"]) <= len(data30["dates"])


# ──────────────────────────────────────────────────────────────
# 5. slow-items 응답 형식
# ──────────────────────────────────────────────────────────────

class TestSlowItemsFormat:
    def test_slow_items_has_required_fields(self, rcv_db, common_db):
        """slow-items 각 항목에 필수 필드 존재."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/slow-items?store_id=46513&limit=20")
            assert resp.status_code == 200
            data = resp.get_json()

            assert "items" in data
            assert len(data["items"]) > 0
            item = data["items"][0]
            assert "item_cd" in item
            assert "item_nm" in item
            assert "pending_age" in item
            assert "lead_time_avg" in item
            assert "short_delivery_rate" in item


# ──────────────────────────────────────────────────────────────
# 6. slow-items pending_age 내림차순
# ──────────────────────────────────────────────────────────────

class TestSlowItemsSorted:
    def test_slow_items_sorted_by_pending_age_desc(self, rcv_db, common_db):
        """slow-items는 pending_age 내림차순 정렬."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/slow-items?store_id=46513&limit=20")
            items = resp.get_json()["items"]

            ages = [i["pending_age"] for i in items]
            assert ages == sorted(ages, reverse=True)


# ──────────────────────────────────────────────────────────────
# 7. slow-items limit 파라미터
# ──────────────────────────────────────────────────────────────

class TestSlowItemsLimit:
    def test_slow_items_respects_limit(self, rcv_db, common_db):
        """limit=1이면 최대 1개만 반환."""
        _seed_data(rcv_db)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/slow-items?store_id=46513&limit=1")
            items = resp.get_json()["items"]
            assert len(items) <= 1


# ──────────────────────────────────────────────────────────────
# 8. 빈 데이터
# ──────────────────────────────────────────────────────────────

class TestEmptyData:
    def test_empty_receiving_returns_defaults(self, rcv_db, common_db):
        """데이터 없을 때 기본값 반환."""
        # 데이터 삽입 안 함 (빈 테이블)
        with patch(
            "src.web.routes.api_receiving.PROJECT_ROOT", rcv_db
        ):
            from src.web.routes.api_receiving import receiving_bp
            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
            client = app.test_client()

            resp = client.get("/api/receiving/summary?store_id=46513")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["avg_lead_time"] == 0.0
            assert data["pending_items_count"] == 0

            resp2 = client.get("/api/receiving/slow-items?store_id=46513")
            assert resp2.status_code == 200
            assert resp2.get_json()["items"] == []


# ──────────────────────────────────────────────────────────────
# 9. Blueprint 등록
# ──────────────────────────────────────────────────────────────

class TestBlueprintRegistered:
    def test_receiving_blueprint_in_register(self):
        """register_blueprints에 receiving_bp 등록."""
        import importlib
        mod = importlib.import_module("src.web.routes")
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "receiving_bp" in source
        assert "/api/receiving" in source


# ──────────────────────────────────────────────────────────────
# 10. receiving.js 존재
# ──────────────────────────────────────────────────────────────

class TestJsFile:
    def test_receiving_js_exists(self):
        """receiving.js 파일 존재 확인."""
        js_path = Path(__file__).parent.parent / "src" / "web" / "static" / "js" / "receiving.js"
        assert js_path.exists(), f"receiving.js not found at {js_path}"
        content = js_path.read_text(encoding="utf-8")
        assert "loadReceivingDashboard" in content
