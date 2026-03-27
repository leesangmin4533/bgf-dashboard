"""디저트 대시보드 테스트

Repository (batch_update, weekly_trend, pending_count) + API (batch, summary history) 테스트.
기존 test_dessert_decision.py의 repo_with_db 패턴 재사용.
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# Fixtures
# ============================================================================

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS dessert_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT DEFAULT '014',
        dessert_category TEXT NOT NULL,
        expiration_days INTEGER,
        small_nm TEXT,
        lifecycle_phase TEXT NOT NULL,
        first_receiving_date TEXT,
        first_receiving_source TEXT,
        weeks_since_intro INTEGER DEFAULT 0,
        judgment_period_start TEXT NOT NULL,
        judgment_period_end TEXT NOT NULL,
        total_order_qty INTEGER DEFAULT 0,
        total_sale_qty INTEGER DEFAULT 0,
        total_disuse_qty INTEGER DEFAULT 0,
        sale_amount INTEGER DEFAULT 0,
        disuse_amount INTEGER DEFAULT 0,
        sell_price INTEGER DEFAULT 0,
        sale_rate REAL DEFAULT 0.0,
        category_avg_sale_qty REAL DEFAULT 0.0,
        prev_period_sale_qty INTEGER DEFAULT 0,
        sale_trend_pct REAL DEFAULT 0.0,
        consecutive_low_weeks INTEGER DEFAULT 0,
        consecutive_zero_months INTEGER DEFAULT 0,
        decision TEXT NOT NULL,
        decision_reason TEXT,
        is_rapid_decline_warning INTEGER DEFAULT 0,
        operator_action TEXT,
        operator_note TEXT,
        action_taken_at TEXT,
        judgment_cycle TEXT NOT NULL,
        category_type TEXT DEFAULT 'dessert',
        created_at TEXT NOT NULL,
        UNIQUE(store_id, item_cd, judgment_period_end)
    )
"""


def _sample_decision(item_cd="ITEM001", decision="KEEP", category="A",
                     period_end="2026-03-04", **overrides):
    d = {
        "store_id": "99999",
        "item_cd": item_cd,
        "item_nm": "테스트상품_" + item_cd,
        "mid_cd": "014",
        "dessert_category": category,
        "expiration_days": 3,
        "small_nm": "냉장디저트",
        "lifecycle_phase": "established",
        "first_receiving_date": "2025-12-01",
        "first_receiving_source": "daily_sales",
        "weeks_since_intro": 13,
        "judgment_period_start": "2026-02-25",
        "judgment_period_end": period_end,
        "total_order_qty": 20,
        "total_sale_qty": 15,
        "total_disuse_qty": 5,
        "sale_amount": 15000,
        "disuse_amount": 5000,
        "sell_price": 1000,
        "sale_rate": 0.75,
        "category_avg_sale_qty": 20.0,
        "prev_period_sale_qty": 12,
        "sale_trend_pct": 25.0,
        "consecutive_low_weeks": 0,
        "consecutive_zero_months": 0,
        "decision": decision,
        "decision_reason": "정상",
        "is_rapid_decline_warning": 0,
        "judgment_cycle": "weekly",
    }
    d.update(overrides)
    return d


@pytest.fixture
def repo_with_db(tmp_path):
    """테스트용 SQLite DB + DessertDecisionRepository"""
    from src.infrastructure.database.repos.dessert_decision_repo import (
        DessertDecisionRepository,
    )

    db_path = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()
    conn.close()

    repo = DessertDecisionRepository(store_id="99999")
    repo._get_conn = lambda: sqlite3.connect(str(db_path))
    repo._get_conn_rr = repo._get_conn
    return repo


# ============================================================================
# 1. batch_update_operator_action 테스트 (~5개)
# ============================================================================
class TestBatchUpdateOperatorAction:

    def test_batch_confirm_stop(self, repo_with_db):
        """STOP_RECOMMEND 미처리 상품 일괄 정지확정"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="A001", decision="STOP_RECOMMEND"),
            _sample_decision(item_cd="A002", decision="STOP_RECOMMEND"),
            _sample_decision(item_cd="A003", decision="KEEP"),
        ])

        results = repo_with_db.batch_update_operator_action(
            item_cds=["A001", "A002", "A003"],
            action="CONFIRMED_STOP",
        )

        # A003은 KEEP이므로 제외, 2건만 업데이트
        assert len(results) == 2
        assert all(r["action"] == "CONFIRMED_STOP" for r in results)
        item_cds = {r["item_cd"] for r in results}
        assert "A001" in item_cds
        assert "A002" in item_cds
        assert "A003" not in item_cds

    def test_batch_override_keep(self, repo_with_db):
        """일괄 유지(재정)"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="B001", decision="STOP_RECOMMEND"),
        ])

        results = repo_with_db.batch_update_operator_action(
            item_cds=["B001"],
            action="OVERRIDE_KEEP",
        )
        assert len(results) == 1
        assert results[0]["action"] == "OVERRIDE_KEEP"

    def test_batch_ignores_already_processed(self, repo_with_db):
        """이미 처리된 상품은 무시"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="C001", decision="STOP_RECOMMEND"),
        ])
        # 먼저 개별 처리
        repo_with_db.update_operator_action(1, "CONFIRMED_STOP", "수동")

        # 일괄 처리 시도 → 이미 처리됨
        results = repo_with_db.batch_update_operator_action(
            item_cds=["C001"],
            action="OVERRIDE_KEEP",
        )
        assert len(results) == 0

    def test_batch_empty_list(self, repo_with_db):
        """빈 리스트 → 빈 결과"""
        results = repo_with_db.batch_update_operator_action(
            item_cds=[],
            action="CONFIRMED_STOP",
        )
        assert results == []

    def test_batch_nonexistent_items(self, repo_with_db):
        """DB에 없는 상품코드 → 빈 결과"""
        results = repo_with_db.batch_update_operator_action(
            item_cds=["NONEXIST001", "NONEXIST002"],
            action="CONFIRMED_STOP",
        )
        assert results == []


# ============================================================================
# 2. get_weekly_trend 테스트 (~2개)
# ============================================================================
class TestGetWeeklyTrend:

    def test_weekly_trend_returns_data(self, repo_with_db):
        """주간 추이 데이터 반환 확인"""
        # 여러 주차에 걸쳐 데이터 삽입
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="W001", decision="KEEP", period_end="2026-02-17"),
            _sample_decision(item_cd="W002", decision="WATCH", period_end="2026-02-17"),
            _sample_decision(item_cd="W003", decision="KEEP", period_end="2026-02-24"),
            _sample_decision(item_cd="W004", decision="STOP_RECOMMEND", period_end="2026-02-24"),
            _sample_decision(item_cd="W005", decision="KEEP", period_end="2026-03-03"),
        ])

        trend = repo_with_db.get_weekly_trend(weeks=8)
        assert isinstance(trend, list)
        assert len(trend) > 0
        # 각 항목에 week, KEEP, WATCH, STOP_RECOMMEND 키 확인
        for entry in trend:
            assert "week" in entry
            assert "KEEP" in entry
            assert "WATCH" in entry
            assert "STOP_RECOMMEND" in entry

    def test_weekly_trend_empty(self, repo_with_db):
        """데이터 없으면 빈 리스트"""
        trend = repo_with_db.get_weekly_trend(weeks=8)
        assert trend == []


# ============================================================================
# 3. get_pending_stop_count 테스트 (~2개)
# ============================================================================
class TestGetPendingStopCount:

    def test_pending_count(self, repo_with_db):
        """미처리 STOP_RECOMMEND 건수"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="P001", decision="STOP_RECOMMEND"),
            _sample_decision(item_cd="P002", decision="STOP_RECOMMEND"),
            _sample_decision(item_cd="P003", decision="KEEP"),
        ])
        count = repo_with_db.get_pending_stop_count()
        assert count == 2

    def test_pending_count_after_action(self, repo_with_db):
        """처리 후 미처리 건수 감소"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="P001", decision="STOP_RECOMMEND"),
            _sample_decision(item_cd="P002", decision="STOP_RECOMMEND"),
        ])
        assert repo_with_db.get_pending_stop_count() == 2

        # 1건 처리
        repo_with_db.update_operator_action(1, "CONFIRMED_STOP", "테스트")
        assert repo_with_db.get_pending_stop_count() == 1


# ============================================================================
# 4. get_decision_summary 확장 테스트 (~2개)
# ============================================================================
class TestDecisionSummaryExtended:

    def test_summary_includes_pending_count(self, repo_with_db):
        """summary에 pending_count 포함 확인"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="S001", decision="STOP_RECOMMEND", category="A"),
            _sample_decision(item_cd="S002", decision="KEEP", category="A"),
        ])
        summary = repo_with_db.get_decision_summary()
        assert summary["current"]["STOP_RECOMMEND"] == 1

    def test_summary_by_category(self, repo_with_db):
        """카테고리별 집계 정확성"""
        repo_with_db.save_decisions_batch([
            _sample_decision(item_cd="X001", decision="KEEP", category="A"),
            _sample_decision(item_cd="X002", decision="KEEP", category="A"),
            _sample_decision(item_cd="X003", decision="WATCH", category="B"),
            _sample_decision(item_cd="X004", decision="STOP_RECOMMEND", category="C"),
        ])
        summary = repo_with_db.get_decision_summary()
        total = summary["current"]["KEEP"] + summary["current"]["WATCH"] + summary["current"]["STOP_RECOMMEND"]
        assert total == 4
        assert summary["current"]["STOP_RECOMMEND"] == 1
        assert summary["by_category"]["A"]["KEEP"] == 2
        assert summary["by_category"]["B"]["WATCH"] == 1


# ============================================================================
# 5. API 엔드포인트 테스트 (~4개)
# ============================================================================
class TestDessertDashboardAPI:
    """Flask test client 기반 API 테스트."""

    @pytest.fixture
    def client(self, tmp_path):
        """Flask test client with mock session."""
        from flask import Flask
        from src.web.routes.api_dessert_decision import dessert_decision_bp

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"
        app.config["TESTING"] = True
        app.register_blueprint(dessert_decision_bp, url_prefix="/api/dessert-decision")

        return app.test_client()

    def test_batch_action_requires_login(self, client):
        """로그인 없이 batch 요청 → 401"""
        resp = client.post("/api/dessert-decision/action/batch",
                           json={"item_cds": ["A001"], "action": "CONFIRMED_STOP"})
        assert resp.status_code == 401

    def test_batch_action_requires_admin(self, client):
        """viewer 권한으로 batch 요청 → 403"""
        with client.session_transaction() as sess:
            sess["user_id"] = "viewer1"
            sess["role"] = "viewer"
            sess["store_id"] = "99999"

        resp = client.post("/api/dessert-decision/action/batch",
                           json={"item_cds": ["A001"], "action": "CONFIRMED_STOP"})
        assert resp.status_code == 403

    def test_batch_action_invalid_action(self, client):
        """유효하지 않은 action → 400"""
        with client.session_transaction() as sess:
            sess["user_id"] = "admin1"
            sess["role"] = "admin"
            sess["store_id"] = "99999"

        resp = client.post("/api/dessert-decision/action/batch",
                           json={"item_cds": ["A001"], "action": "INVALID"})
        assert resp.status_code == 400

    def test_batch_action_too_many_items(self, client):
        """51개 초과 → 400"""
        with client.session_transaction() as sess:
            sess["user_id"] = "admin1"
            sess["role"] = "admin"
            sess["store_id"] = "99999"

        item_cds = [f"ITEM{i:03d}" for i in range(51)]
        resp = client.post("/api/dessert-decision/action/batch",
                           json={"item_cds": item_cds, "action": "CONFIRMED_STOP"})
        assert resp.status_code == 400

    def test_summary_with_history_param(self, client):
        """summary?history=8w 요청 정상 처리 (lazy import 패치)"""
        mock_repo = MagicMock()
        mock_repo.get_decision_summary.return_value = {
            "by_category": {"A": {"KEEP": 5, "WATCH": 2, "STOP_RECOMMEND": 1}},
            "total_items": 8,
            "stop_recommended": 1,
            "pending_count": 1,
        }
        mock_repo.get_weekly_trend.return_value = [
            {"week": "W8", "KEEP": 10, "WATCH": 3, "STOP_RECOMMEND": 1}
        ]

        with patch("src.infrastructure.database.repos.DessertDecisionRepository",
                    return_value=mock_repo):
            resp = client.get("/api/dessert-decision/summary?store_id=99999&history=8w")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "weekly_trend" in data["data"]
        assert len(data["data"]["weekly_trend"]) == 1
        assert data["data"]["weekly_trend"][0]["KEEP"] == 10
