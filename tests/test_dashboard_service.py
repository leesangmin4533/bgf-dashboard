"""
DashboardService 테스트

db_path 직접 주입으로 DBRouter 우회.
빈 DB에서도 예외 없이 기본값 반환하는지 검증.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from src.application.services.dashboard_service import DashboardService


@pytest.fixture
def dashboard_db(tmp_path):
    """테스트용 DB 생성 (필수 테이블 포함)."""
    db_path = str(tmp_path / "test_dashboard.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE collection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_date TEXT,
            collected_at TEXT,
            total_items INTEGER DEFAULT 0,
            status TEXT DEFAULT '',
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE order_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_date TEXT,
            item_cd TEXT,
            item_nm TEXT,
            mid_cd TEXT DEFAULT '',
            order_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT '',
            remaining_qty INTEGER DEFAULT 0,
            expiry_time TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_cd TEXT DEFAULT '',
            item_nm TEXT DEFAULT '',
            mid_cd TEXT DEFAULT '',
            expiry_date TEXT DEFAULT '',
            remaining_qty INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sales_date TEXT,
            item_cd TEXT,
            item_nm TEXT DEFAULT '',
            mid_cd TEXT DEFAULT '',
            sale_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE prediction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT,
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE eval_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_date TEXT DEFAULT '',
            order_status TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE order_fail_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_date TEXT DEFAULT '',
            item_cd TEXT DEFAULT '',
            item_nm TEXT DEFAULT '',
            mid_cd TEXT DEFAULT '',
            stop_reason TEXT DEFAULT '',
            orderable_status TEXT DEFAULT '',
            checked_at TEXT DEFAULT '',
            store_id TEXT DEFAULT ''
        );

        CREATE TABLE products (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# =============================================================================
# get_expiry_risk 테스트
# =============================================================================
class TestGetExpiryRisk:
    """폐기 위험 상품 조회 검증."""

    def test_empty_db_returns_empty(self, dashboard_db):
        """빈 DB → count=0, items=[] 반환, 예외 없음."""
        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_expiry_risk()

        assert result["count"] == 0
        assert result["items"] == []

    def test_with_batch_data(self, dashboard_db):
        """inventory_batches에 만료 임박 데이터가 있으면 조회 가능 (예외 없음).

        datetime('now') 기준 2시간 이내 필터이므로, 테스트 시점에 따라
        count가 0 또는 1일 수 있다. 핵심: 예외 없이 dict 반환.
        """
        now = datetime.now()
        # 30분 후 만료 → datetime('now', '+2 hours') 이내이면서 >= datetime('now')
        expiry = (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(dashboard_db)
        conn.execute("""
            INSERT INTO inventory_batches
            (item_cd, item_nm, mid_cd, expiry_date, remaining_qty, status)
            VALUES ('FOOD001', '삼각김밥', '002', ?, 5, 'active')
        """, (expiry,))
        conn.commit()
        conn.close()

        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_expiry_risk()

        # 구조 검증 (시간 의존 데이터이므로 count >= 0)
        assert "count" in result
        assert "items" in result
        assert isinstance(result["items"], list)
        # 포함된 경우 필드 구조 검증
        if result["count"] > 0:
            item = result["items"][0]
            assert "item_nm" in item
            assert "remaining_qty" in item
            assert "mid_cd" in item


# =============================================================================
# get_order_trend_7d 테스트
# =============================================================================
class TestGetOrderTrend7d:
    """7일 발주 트렌드 검증."""

    def test_empty_db_returns_empty_list(self, dashboard_db):
        """빈 DB → [] 반환, 예외 없음."""
        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_order_trend_7d()
        assert result == []


# =============================================================================
# get_sales_trend_7d 테스트
# =============================================================================
class TestGetSalesTrend7d:
    """7일 매출 트렌드 검증."""

    def test_empty_db_returns_empty_list(self, dashboard_db):
        """빈 DB → [] 반환, 예외 없음."""
        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_sales_trend_7d()
        assert result == []


# =============================================================================
# get_waste_trend_7d 테스트
# =============================================================================
class TestGetWasteTrend7d:
    """7일 폐기 트렌드 검증."""

    def test_empty_db_returns_empty_list(self, dashboard_db):
        """빈 DB → [] 반환, 예외 없음."""
        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_waste_trend_7d()
        assert result == []


# =============================================================================
# get_last_order 테스트
# =============================================================================
class TestGetLastOrder:
    """최근 발주 정보 조회 검증."""

    def test_empty_db_returns_defaults(self, dashboard_db):
        """빈 DB → date=None, success/fail=0 반환, 예외 없음."""
        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_last_order()

        assert result["date"] is None
        assert result["total_items"] == 0
        assert result["success_count"] == 0
        assert result["fail_count"] == 0


# =============================================================================
# get_today_summary 테스트
# =============================================================================
class TestGetTodaySummary:
    """오늘 발주 요약 검증."""

    def test_empty_db_returns_defaults(self, dashboard_db):
        """빈 DB → 모든 값 0 반환, 예외 없음."""
        svc = DashboardService(db_path=dashboard_db)
        result = svc.get_today_summary()

        assert result["order_items"] == 0
        assert result["total_qty"] == 0
        assert result["ordered_items"] == 0
        assert result["ordered_qty"] == 0
        assert "categories" in result
