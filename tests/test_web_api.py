"""웹 API 엔드포인트 테스트"""

import json
import pytest


class TestHomeAPI:
    """홈 대시보드 API 테스트"""

    def test_home_status(self, client):
        """GET /api/home/status 200 응답"""
        resp = client.get("/api/home/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)


class TestOrderAPI:
    """발주 관련 API 테스트"""

    def test_get_categories(self, client):
        """GET /api/order/categories - 카테고리 목록"""
        resp = client.get("/api/order/categories")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "categories" in data

    def test_get_exclusions(self, client):
        """GET /api/order/exclusions - 제외 목록 (상세 포함)"""
        resp = client.get("/api/order/exclusions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "auto_order" in data
        assert "smart_order" in data
        assert "items" in data["auto_order"]
        assert "items" in data["smart_order"]
        assert isinstance(data["auto_order"]["items"], list)

    def test_exclusions_items_detail(self, flask_app, client):
        """GET /api/order/exclusions - 자동/스마트 발주 상품 목록 상세 검증"""
        import sqlite3
        from pathlib import Path
        from unittest.mock import patch
        db_path = flask_app.config["DB_PATH"]

        # 테스트 데이터 삽입
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO auto_order_items (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("TEST_AUTO_001", "테스트자동상품A", "049"),
        )
        conn.execute(
            "INSERT INTO auto_order_items (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("TEST_AUTO_002", "테스트자동상품B", "050"),
        )
        conn.execute(
            "INSERT INTO smart_order_items (item_cd, item_nm, mid_cd) VALUES (?, ?, ?)",
            ("TEST_SMART_001", "테스트스마트상품A", "072"),
        )
        conn.commit()
        conn.close()

        # Repository가 테스트 DB를 사용하도록 패치
        # Repository(store_id=X) → _get_conn() → DBRouter.get_store_db_path(X)
        with patch(
            "src.infrastructure.database.connection.get_db_path",
            return_value=Path(db_path),
        ), patch(
            "src.infrastructure.database.connection.DBRouter.get_store_db_path",
            return_value=Path(db_path),
        ):
            resp = client.get("/api/order/exclusions")

        assert resp.status_code == 200
        data = resp.get_json()

        # 자동발주 검증
        auto = data["auto_order"]
        assert auto["count"] == 2
        assert len(auto["items"]) == 2
        codes = [item["item_cd"] for item in auto["items"]]
        assert "TEST_AUTO_001" in codes
        assert "TEST_AUTO_002" in codes
        # 상세 필드 존재 확인
        item = next(i for i in auto["items"] if i["item_cd"] == "TEST_AUTO_001")
        assert item["item_nm"] == "테스트자동상품A"
        assert item["mid_cd"] == "049"

        # 스마트발주 검증
        smart = data["smart_order"]
        assert smart["count"] == 1
        assert len(smart["items"]) == 1
        assert smart["items"][0]["item_cd"] == "TEST_SMART_001"
        assert smart["items"][0]["item_nm"] == "테스트스마트상품A"
        assert smart["items"][0]["mid_cd"] == "072"

    def test_exclusions_empty_items(self, flask_app, client):
        """GET /api/order/exclusions - 빈 목록일 때 items=[] 반환"""
        from pathlib import Path
        from unittest.mock import patch
        db_path = flask_app.config["DB_PATH"]

        with patch(
            "src.infrastructure.database.connection.get_db_path",
            return_value=Path(db_path),
        ), patch(
            "src.infrastructure.database.connection.DBRouter.get_store_db_path",
            return_value=Path(db_path),
        ):
            resp = client.get("/api/order/exclusions")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_order"]["items"] == []
        assert data["auto_order"]["count"] == 0
        assert data["smart_order"]["items"] == []
        assert data["smart_order"]["count"] == 0

    def test_toggle_exclusion(self, client):
        """POST /api/order/exclusions/toggle"""
        resp = client.post(
            "/api/order/exclusions/toggle",
            data=json.dumps({"kind": "auto", "enabled": False}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is False

    @pytest.mark.slow
    def test_predict_returns_error_or_data(self, client):
        """POST /api/order/predict - 에러 또는 데이터 반환"""
        resp = client.post(
            "/api/order/predict",
            data=json.dumps({"max_items": 3}),
            content_type="application/json",
        )
        # DB가 비어있으면 에러 또는 빈 결과 반환
        assert resp.status_code in (200, 500)


class TestReportAPI:
    """리포트 API 테스트"""

    @pytest.mark.slow
    def test_daily_report(self, client):
        """GET /api/report/daily"""
        resp = client.get("/api/report/daily")
        assert resp.status_code == 200

    def test_weekly_report(self, client):
        """GET /api/report/weekly"""
        resp = client.get("/api/report/weekly")
        assert resp.status_code == 200

    @pytest.mark.slow
    def test_impact_no_baseline(self, client):
        """GET /api/report/impact - baseline 없을 때 404"""
        resp = client.get("/api/report/impact")
        assert resp.status_code in (200, 404)
