"""통합 카테고리 대시보드 테스트

설계서: docs/02-design/features/unified-category-dashboard.design.md
"""

import json
import sqlite3
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock


# ============================================================================
# BeverageDecisionRepository 추가 메서드 테스트
# ============================================================================


class TestBeverageRepoHistory:
    """get_item_decision_history() 테스트"""

    def _make_repo(self, conn):
        from src.infrastructure.database.repos.beverage_decision_repo import BeverageDecisionRepository
        repo = BeverageDecisionRepository.__new__(BeverageDecisionRepository)
        repo.store_id = "TEST"
        repo._conn = conn
        repo._get_conn = lambda: conn
        return repo

    def _create_table(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dessert_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT, item_cd TEXT, item_nm TEXT, mid_cd TEXT,
                dessert_category TEXT, expiration_days INTEGER, small_nm TEXT,
                lifecycle_phase TEXT, first_receiving_date TEXT, first_receiving_source TEXT,
                weeks_since_intro INTEGER,
                judgment_period_start TEXT, judgment_period_end TEXT,
                total_order_qty INTEGER, total_sale_qty INTEGER, total_disuse_qty INTEGER,
                sale_amount INTEGER, disuse_amount INTEGER, sell_price INTEGER, sale_rate REAL,
                category_avg_sale_qty REAL,
                prev_period_sale_qty INTEGER, sale_trend_pct REAL,
                consecutive_low_weeks INTEGER, consecutive_zero_months INTEGER,
                decision TEXT, decision_reason TEXT, is_rapid_decline_warning INTEGER,
                judgment_cycle TEXT, category_type TEXT DEFAULT 'dessert',
                operator_action TEXT, operator_note TEXT, action_taken_at TEXT,
                created_at TEXT,
                UNIQUE(store_id, item_cd, judgment_period_end)
            )
        """)

    def _insert(self, conn, item_cd, period_end, category_type="beverage", decision="KEEP"):
        conn.execute("""
            INSERT INTO dessert_decisions (
                store_id, item_cd, item_nm, mid_cd, dessert_category,
                lifecycle_phase, judgment_period_start, judgment_period_end,
                decision, decision_reason, judgment_cycle, category_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("TEST", item_cd, f"Test {item_cd}", "042", "C",
              "established", "2026-01-01", period_end,
              decision, "test", "monthly", category_type, datetime.now().isoformat()))
        conn.commit()

    def test_returns_beverage_only(self):
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        self._insert(conn, "ITEM1", "2026-01-31", "beverage")
        self._insert(conn, "ITEM1", "2026-02-28", "beverage")
        self._insert(conn, "ITEM1", "2026-03-31", "dessert")  # 다른 period_end 디저트 → 제외

        repo = self._make_repo(conn)
        result = repo.get_item_decision_history("ITEM1")

        assert len(result) == 2
        for r in result:
            assert r["category_type"] == "beverage"

    def test_limit(self):
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        for i in range(5):
            self._insert(conn, "ITEM1", f"2026-0{i+1}-28", "beverage")

        repo = self._make_repo(conn)
        result = repo.get_item_decision_history("ITEM1", limit=3)
        assert len(result) == 3

    def test_order_desc(self):
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        self._insert(conn, "ITEM1", "2026-01-31", "beverage")
        self._insert(conn, "ITEM1", "2026-03-31", "beverage")
        self._insert(conn, "ITEM1", "2026-02-28", "beverage")

        repo = self._make_repo(conn)
        result = repo.get_item_decision_history("ITEM1")
        dates = [r["judgment_period_end"] for r in result]
        assert dates == sorted(dates, reverse=True)

    def test_empty(self):
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        repo = self._make_repo(conn)
        result = repo.get_item_decision_history("NONEXIST")
        assert result == []


class TestBeverageRepoUpdateOperatorAction:
    """update_operator_action() 테스트"""

    def _make_repo(self, conn):
        from src.infrastructure.database.repos.beverage_decision_repo import BeverageDecisionRepository
        repo = BeverageDecisionRepository.__new__(BeverageDecisionRepository)
        repo.store_id = "TEST"
        repo._conn = conn
        repo._get_conn = lambda: conn
        return repo

    def _create_table(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dessert_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT, item_cd TEXT, item_nm TEXT, mid_cd TEXT,
                dessert_category TEXT, lifecycle_phase TEXT,
                judgment_period_start TEXT, judgment_period_end TEXT,
                decision TEXT, decision_reason TEXT, judgment_cycle TEXT,
                category_type TEXT DEFAULT 'dessert',
                operator_action TEXT, operator_note TEXT, action_taken_at TEXT,
                created_at TEXT,
                UNIQUE(store_id, item_cd, judgment_period_end)
            )
        """)

    def test_update_success(self):
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        conn.execute("""
            INSERT INTO dessert_decisions (store_id, item_cd, item_nm, mid_cd,
                dessert_category, lifecycle_phase, judgment_period_start, judgment_period_end,
                decision, decision_reason, judgment_cycle, category_type, created_at)
            VALUES ('TEST', 'ITEM1', 'Test', '042', 'C', 'established',
                '2026-01-01', '2026-01-31', 'STOP_RECOMMEND', 'test', 'monthly',
                'beverage', '2026-01-31')
        """)
        conn.commit()

        repo = self._make_repo(conn)
        result = repo.update_operator_action(1, "CONFIRMED_STOP", "테스트 정지")
        assert result is True

        row = conn.execute("SELECT operator_action, operator_note FROM dessert_decisions WHERE id = 1").fetchone()
        assert row[0] == "CONFIRMED_STOP"
        assert row[1] == "테스트 정지"

    def test_update_nonexistent(self):
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        repo = self._make_repo(conn)
        result = repo.update_operator_action(999, "CONFIRMED_STOP")
        assert result is False

    def test_update_dessert_rejected(self):
        """category_type='dessert'인 레코드는 업데이트되지 않음"""
        conn = sqlite3.connect(":memory:")
        self._create_table(conn)
        conn.execute("""
            INSERT INTO dessert_decisions (store_id, item_cd, item_nm, mid_cd,
                dessert_category, lifecycle_phase, judgment_period_start, judgment_period_end,
                decision, decision_reason, judgment_cycle, category_type, created_at)
            VALUES ('TEST', 'ITEM1', 'Test', '014', 'A', 'established',
                '2026-01-01', '2026-01-31', 'STOP_RECOMMEND', 'test', 'weekly',
                'dessert', '2026-01-31')
        """)
        conn.commit()

        repo = self._make_repo(conn)
        result = repo.update_operator_action(1, "CONFIRMED_STOP")
        assert result is False  # category_type='beverage' 필터로 업데이트 안 됨


# ============================================================================
# API Blueprint 테스트
# ============================================================================


class TestBeverageDecisionAPI:
    """api_beverage_decision.py 엔드포인트 테스트"""

    @pytest.fixture
    def client(self):
        """Flask test client"""
        import sys
        import os
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)

        from src.web.routes.api_beverage_decision import beverage_decision_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(beverage_decision_bp, url_prefix="/api/beverage-decision")
        return app.test_client()

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_latest_success(self, mock_logger, client):
        mock_repo = MagicMock()
        mock_repo.get_latest_decisions.return_value = [
            {"item_cd": "001", "decision": "KEEP", "dessert_category": "C"}
        ]

        with patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_repo):
            resp = client.get("/api/beverage-decision/latest")
            data = json.loads(resp.data)
            assert data["success"] is True
            assert data["total"] == 1
            assert data["data"][0]["item_cd"] == "001"

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_latest_category_filter(self, mock_logger, client):
        mock_repo = MagicMock()
        mock_repo.get_latest_decisions.return_value = [
            {"item_cd": "001", "decision": "KEEP", "dessert_category": "A"},
            {"item_cd": "002", "decision": "KEEP", "dessert_category": "C"},
        ]

        with patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_repo):
            resp = client.get("/api/beverage-decision/latest?category=A")
            data = json.loads(resp.data)
            assert data["total"] == 1
            assert data["data"][0]["dessert_category"] == "A"

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_history(self, mock_logger, client):
        mock_repo = MagicMock()
        mock_repo.get_item_decision_history.return_value = [
            {"item_cd": "001", "decision": "KEEP", "judgment_period_end": "2026-01-31"}
        ]

        with patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_repo):
            resp = client.get("/api/beverage-decision/history/001")
            data = json.loads(resp.data)
            assert data["success"] is True
            assert data["item_cd"] == "001"

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_summary_with_trend(self, mock_logger, client):
        mock_repo = MagicMock()
        mock_repo.get_decision_summary.return_value = {"current": {"KEEP": 10}, "by_category": {}}
        mock_repo.get_weekly_trend.return_value = [{"week": "W1", "KEEP": 10}]

        with patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_repo):
            resp = client.get("/api/beverage-decision/summary?history=8w")
            data = json.loads(resp.data)
            assert data["success"] is True
            assert "weekly_trend" in data["data"]

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_action_invalid(self, mock_logger, client):
        resp = client.post("/api/beverage-decision/action/1",
                           data=json.dumps({"action": "INVALID"}),
                           content_type="application/json")
        data = json.loads(resp.data)
        assert resp.status_code == 400
        assert data["success"] is False

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_batch_action_empty(self, mock_logger, client):
        resp = client.post("/api/beverage-decision/action/batch",
                           data=json.dumps({"item_cds": [], "action": "CONFIRMED_STOP"}),
                           content_type="application/json")
        data = json.loads(resp.data)
        assert resp.status_code == 400

    @patch("src.web.routes.api_beverage_decision.logger")
    def test_batch_action_success(self, mock_logger, client):
        mock_repo = MagicMock()
        mock_repo.batch_update_operator_action.return_value = [
            {"item_cd": "001", "action": "CONFIRMED_STOP", "action_taken_at": "2026-01-01"}
        ]

        with patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_repo):
            resp = client.post("/api/beverage-decision/action/batch",
                               data=json.dumps({"item_cds": ["001"], "action": "CONFIRMED_STOP"}),
                               content_type="application/json")
            data = json.loads(resp.data)
            assert data["success"] is True
            assert data["updated_count"] == 1


class TestCategoryDecisionAPI:
    """api_category_decision.py pending-count 테스트"""

    @pytest.fixture
    def client(self):
        import sys
        import os
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)

        from src.web.routes.api_category_decision import category_decision_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(category_decision_bp, url_prefix="/api/category-decision")
        return app.test_client()

    def test_pending_count(self, client):
        mock_dessert = MagicMock()
        mock_dessert.get_pending_stop_count.return_value = 5
        mock_beverage = MagicMock()
        mock_beverage.get_pending_stop_count.return_value = 12

        with patch("src.infrastructure.database.repos.DessertDecisionRepository", return_value=mock_dessert), \
             patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_beverage):
            resp = client.get("/api/category-decision/pending-count")
            data = json.loads(resp.data)
            assert data["success"] is True
            assert data["data"]["dessert"] == 5
            assert data["data"]["beverage"] == 12
            assert data["data"]["total"] == 17

    def test_pending_count_partial_failure(self, client):
        """한쪽 실패해도 다른 쪽은 정상 반환"""
        mock_beverage = MagicMock()
        mock_beverage.get_pending_stop_count.return_value = 3

        with patch("src.infrastructure.database.repos.DessertDecisionRepository", side_effect=Exception("DB error")), \
             patch("src.infrastructure.database.repos.BeverageDecisionRepository", return_value=mock_beverage):
            resp = client.get("/api/category-decision/pending-count")
            data = json.loads(resp.data)
            assert data["success"] is True
            assert data["data"]["dessert"] == 0
            assert data["data"]["beverage"] == 3


# ============================================================================
# Blueprint 등록 테스트
# ============================================================================


class TestBlueprintRegistration:
    """routes/__init__.py에 beverage/category blueprint가 등록되었는지 확인"""

    def test_beverage_blueprint_registered(self):
        import sys
        import os
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)

        from src.web.routes import register_blueprints
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"

        register_blueprints(app)

        # Blueprint 이름 확인
        bp_names = [bp for bp in app.blueprints.keys()]
        assert "beverage_decision" in bp_names
        assert "category_decision" in bp_names
        assert "dessert_decision" in bp_names

    def test_url_rules(self):
        from src.web.routes import register_blueprints
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"

        register_blueprints(app)

        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/api/beverage-decision/latest" in rules
        assert "/api/beverage-decision/summary" in rules
        assert "/api/category-decision/pending-count" in rules
