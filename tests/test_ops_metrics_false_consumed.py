"""ops_metrics._false_consumed_post_guard 회귀 테스트
   (ops-metrics-monitor-extension, 2026-04-08)

가드 위반 SQL 정의:
- status='consumed'
- expiration_days <= 7
- updated_at >= datetime('now','-24 hours')
- julianday(expiry_date) - julianday(updated_at) < 1.0
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.analysis.ops_metrics import OpsMetrics


def _build_store_db(path):
    """테스트용 매장 DB 생성 (inventory_batches 최소 스키마)"""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            receiving_date TEXT,
            receiving_id TEXT,
            expiration_days INTEGER,
            expiry_date TEXT,
            initial_qty INTEGER DEFAULT 0,
            remaining_qty INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    return conn


def _insert_batch(conn, **kwargs):
    """편의 INSERT — 누락 컬럼은 기본값 사용"""
    defaults = {
        "store_id": "TEST",
        "item_cd": "ITEM_A",
        "item_nm": "테스트 상품",
        "mid_cd": "001",
        "receiving_date": "2026-04-07",
        "expiration_days": 1,
        "initial_qty": 1,
        "remaining_qty": 0,
        "status": "consumed",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(
        f"INSERT INTO inventory_batches ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    conn.commit()


@pytest.fixture
def fake_store_db(tmp_path):
    db_path = tmp_path / "store.db"
    conn = _build_store_db(db_path)
    yield conn, db_path
    conn.close()


def _patched_metrics(db_path):
    """OpsMetrics 인스턴스가 fake DB를 보도록 patch"""
    def fake_conn(store_id):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c
    return patch(
        "src.analysis.ops_metrics.DBRouter.get_store_connection",
        side_effect=fake_conn,
    )


class TestFalseConsumedPostGuard:
    """가드 우회 감지 — _false_consumed_post_guard"""

    def test_no_data_returns_zero(self, fake_store_db):
        """배치 0건 → cnt=0"""
        _, db_path = fake_store_db
        with _patched_metrics(db_path):
            result = OpsMetrics("TEST")._false_consumed_post_guard()
        assert result["cnt"] == 0

    def test_normal_consumed_far_before_expiry_excluded(self, fake_store_db):
        """정상 케이스: 만료 3일 전에 consumed → 카운트 제외"""
        conn, db_path = fake_store_db
        # updated_at = 어제, expiry_date = 3일 후 (안전 마진)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        _insert_batch(
            conn,
            store_id="TEST",
            expiration_days=7,
            expiry_date=future,
            updated_at=yesterday,
        )
        with _patched_metrics(db_path):
            result = OpsMetrics("TEST")._false_consumed_post_guard()
        assert result["cnt"] == 0

    def test_false_consumed_within_24h_detected(self, fake_store_db):
        """위반 케이스: 만료 1시간 후가 consumed 시점 → 1건 감지"""
        conn, db_path = fake_store_db
        # 핵심: julianday(expiry) - julianday(updated_at) < 1.0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_batch(
            conn,
            store_id="TEST",
            item_cd="VIOLATE_1",
            expiration_days=1,
            expiry_date=today,  # 오늘 만료
            updated_at=now_str,  # 지금 consumed → 차이 < 1.0
        )
        with _patched_metrics(db_path):
            result = OpsMetrics("TEST")._false_consumed_post_guard()
        assert result["cnt"] == 1
        assert result["latest_at"] is not None
        assert "VIOLATE_1" in result.get("sample_items", "")

    def test_long_expiration_excluded(self, fake_store_db):
        """expiration_days > 7 (장기유통기한) → 카운트 제외"""
        conn, db_path = fake_store_db
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_batch(
            conn,
            store_id="TEST",
            item_cd="LONG_LIFE",
            expiration_days=30,  # 장기 → 노이즈로 간주
            expiry_date=today,
            updated_at=now_str,
        )
        with _patched_metrics(db_path):
            result = OpsMetrics("TEST")._false_consumed_post_guard()
        assert result["cnt"] == 0

    def test_old_consumed_outside_24h_excluded(self, fake_store_db):
        """24시간 이전에 처리된 consumed → 슬라이딩 윈도우 밖 제외"""
        conn, db_path = fake_store_db
        # 2일 전
        old = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        old_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        _insert_batch(
            conn,
            store_id="TEST",
            item_cd="OLD_VIOLATE",
            expiration_days=1,
            expiry_date=old_date,
            updated_at=old,
        )
        with _patched_metrics(db_path):
            result = OpsMetrics("TEST")._false_consumed_post_guard()
        assert result["cnt"] == 0

    def test_active_status_excluded(self, fake_store_db):
        """status=active는 가드 위반 아님"""
        conn, db_path = fake_store_db
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")
        _insert_batch(
            conn,
            store_id="TEST",
            item_cd="STILL_ACTIVE",
            status="active",
            remaining_qty=1,
            expiration_days=1,
            expiry_date=today,
            updated_at=now_str,
        )
        with _patched_metrics(db_path):
            result = OpsMetrics("TEST")._false_consumed_post_guard()
        assert result["cnt"] == 0
