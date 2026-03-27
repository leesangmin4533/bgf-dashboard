"""폐기 원인 시각화 - /api/waste/waterfall 엔드포인트 테스트"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.infrastructure.database.repos.waste_cause_repo import WasteCauseRepository


@pytest.fixture
def waste_db(tmp_path):
    """테스트용 waste_cause_analysis DB"""
    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE waste_cause_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            analysis_date TEXT,
            waste_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT,
            waste_qty INTEGER DEFAULT 0,
            waste_source TEXT DEFAULT 'daily_sales',
            primary_cause TEXT,
            secondary_cause TEXT,
            confidence REAL DEFAULT 0.0,
            order_qty INTEGER,
            daily_avg REAL,
            predicted_qty REAL,
            actual_sold_qty INTEGER,
            expiration_days INTEGER,
            trend_ratio REAL,
            sell_day_ratio REAL,
            weather_factor TEXT,
            promo_factor TEXT,
            holiday_factor TEXT,
            feedback_action TEXT DEFAULT 'DEFAULT',
            feedback_multiplier REAL DEFAULT 1.0,
            feedback_expiry_date TEXT,
            is_applied INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(store_id, waste_date, item_cd)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def waste_app(waste_db):
    from flask import Flask
    from src.web.routes.api_waste import waste_bp

    app = Flask(__name__)
    app.register_blueprint(waste_bp, url_prefix="/api/waste")
    app.config["TESTING"] = True
    return app


def _insert_cause(repo, item_cd, item_nm, cause, waste_qty, order_qty, sold_qty, days_ago=0):
    """헬퍼: 폐기 원인 레코드 삽입"""
    waste_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    repo.upsert_cause({
        "store_id": "46513",
        "waste_date": waste_date,
        "item_cd": item_cd,
        "item_nm": item_nm,
        "primary_cause": cause,
        "waste_qty": waste_qty,
        "order_qty": order_qty,
        "actual_sold_qty": sold_qty,
        "waste_source": "daily_sales",
        "confidence": 0.8,
    })


class TestWaterfallEndpoint:
    """GET /api/waste/waterfall 테스트"""

    def test_waterfall_basic(self, waste_app, waste_db):
        """워터폴 기본 응답 형식"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        _insert_cause(repo, "A001", "삼각김밥 참치", "OVER_ORDER", 10, 50, 35, 1)
        _insert_cause(repo, "A002", "도시락 불고기", "DEMAND_DROP", 5, 30, 20, 2)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=7")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert len(data["items"]) == 2
        # 폐기량 내림차순
        assert data["items"][0]["waste_qty"] >= data["items"][1]["waste_qty"]

    def test_waterfall_item_fields(self, waste_app, waste_db):
        """워터폴 아이템 필드 확인"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        _insert_cause(repo, "B001", "샌드위치 에그", "OVER_ORDER", 8, 40, 30, 1)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=7")

        data = resp.get_json()
        item = data["items"][0]
        assert item["item_cd"] == "B001"
        assert item["item_nm"] == "샌드위치 에그"
        assert item["order_qty"] == 40
        assert item["sold_qty"] == 30
        assert item["waste_qty"] == 8
        assert item["primary_cause"] == "OVER_ORDER"

    def test_waterfall_aggregates_same_item(self, waste_app, waste_db):
        """같은 상품의 다른 날짜 데이터가 합산"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        _insert_cause(repo, "C001", "우유 1L", "OVER_ORDER", 3, 20, 15, 1)
        _insert_cause(repo, "C001", "우유 1L", "OVER_ORDER", 5, 25, 18, 2)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=7")

        data = resp.get_json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["waste_qty"] == 8  # 3+5
        assert item["order_qty"] == 45  # 20+25
        assert item["sold_qty"] == 33  # 15+18

    def test_waterfall_limit(self, waste_app, waste_db):
        """limit 파라미터로 반환 수 제한"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        for i in range(5):
            _insert_cause(repo, f"D{i:03d}", f"상품{i}", "MIXED", i + 1, 10, 5, i)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=14&limit=3")

        data = resp.get_json()
        assert len(data["items"]) == 3
        # 폐기 내림차순: 5, 4, 3
        assert data["items"][0]["waste_qty"] == 5
        assert data["items"][1]["waste_qty"] == 4
        assert data["items"][2]["waste_qty"] == 3

    def test_waterfall_empty(self, waste_app, waste_db):
        """데이터 없으면 빈 배열"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=7")

        data = resp.get_json()
        assert data["items"] == []

    def test_waterfall_days_filter(self, waste_app, waste_db):
        """days 파라미터로 기간 필터링"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        _insert_cause(repo, "E001", "최근 상품", "OVER_ORDER", 5, 20, 10, 1)
        _insert_cause(repo, "E002", "오래된 상품", "DEMAND_DROP", 3, 15, 8, 10)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=7")

        data = resp.get_json()
        # days=7이면 10일 전 데이터는 제외
        assert len(data["items"]) == 1
        assert data["items"][0]["item_cd"] == "E001"

    def test_waterfall_sort_descending(self, waste_app, waste_db):
        """폐기량 내림차순 정렬 확인"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        _insert_cause(repo, "F001", "소량 폐기", "MIXED", 2, 10, 5, 1)
        _insert_cause(repo, "F002", "대량 폐기", "OVER_ORDER", 20, 50, 25, 1)
        _insert_cause(repo, "F003", "중량 폐기", "DEMAND_DROP", 8, 30, 18, 1)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/waterfall?store_id=46513&days=7")

        data = resp.get_json()
        qtys = [item["waste_qty"] for item in data["items"]]
        assert qtys == sorted(qtys, reverse=True)
        assert qtys == [20, 8, 2]


class TestSummaryForChart:
    """GET /api/waste/summary 파이 차트용 데이터 검증"""

    def test_summary_by_cause_structure(self, waste_app, waste_db):
        """by_cause 구조 확인 (차트 데이터용)"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")
        _insert_cause(repo, "G001", "상품A", "OVER_ORDER", 10, 50, 35, 1)
        _insert_cause(repo, "G002", "상품B", "DEMAND_DROP", 5, 30, 20, 2)
        _insert_cause(repo, "G003", "상품C", "OVER_ORDER", 7, 40, 28, 3)

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/summary?store_id=46513&days=7")

        data = resp.get_json()
        assert data["total_count"] == 3
        assert data["total_qty"] == 22  # 10+5+7
        assert "by_cause" in data
        assert data["by_cause"]["OVER_ORDER"]["count"] == 2
        assert data["by_cause"]["OVER_ORDER"]["total_qty"] == 17  # 10+7
        assert data["by_cause"]["DEMAND_DROP"]["count"] == 1

    def test_summary_empty(self, waste_app, waste_db):
        """빈 데이터 시 총합 0"""
        repo = WasteCauseRepository(db_path=waste_db, store_id="46513")

        with waste_app.test_client() as client:
            with patch("src.web.routes.api_waste.WasteCauseRepository", return_value=repo):
                resp = client.get("/api/waste/summary?store_id=46513&days=7")

        data = resp.get_json()
        assert data["total_count"] == 0
        assert data["total_qty"] == 0
