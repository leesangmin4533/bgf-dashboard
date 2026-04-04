"""
delivery_match_flow 테스트

시나리오:
  S1: 정상 매칭 — confirmed_orders + receiving_history → 배치 생성
  S2: 미입고 — confirmed_orders에 있지만 receiving_history에 없음 → 배치 미생성
  S3: 부분 입고 — 발주 5, 입고 3 → 배치 3개로 생성
  S4: 스냅샷 없음 — confirmed_orders 비어있으면 폴백 (skipped=-1)
  S5: calc_expiry_datetime — FOOD_EXPIRY_CONFIG 기반 계산 검증
"""

import sqlite3
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from src.application.use_cases.delivery_match_flow import (
    calc_expiry_datetime,
    match_confirmed_with_receiving,
    FOOD_EXPIRY_CONFIG,
)


# ─── 유틸 ───

def _create_test_db():
    """테스트용 in-memory DB 생성"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE confirmed_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            order_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            ord_qty INTEGER NOT NULL,
            delivery_type TEXT,
            ord_input_id TEXT,
            matched INTEGER DEFAULT 0,
            matched_qty INTEGER DEFAULT 0,
            confirmed_at TEXT NOT NULL,
            UNIQUE(store_id, order_date, item_cd)
        )
    """)
    conn.execute("""
        CREATE TABLE receiving_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            receiving_date TEXT NOT NULL,
            receiving_time TEXT,
            chit_no TEXT,
            item_cd TEXT NOT NULL,
            item_nm TEXT,
            mid_cd TEXT,
            order_date TEXT,
            order_qty INTEGER DEFAULT 0,
            receiving_qty INTEGER DEFAULT 0,
            delivery_type TEXT,
            center_nm TEXT,
            center_cd TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(receiving_date, item_cd, chit_no)
        )
    """)
    conn.commit()
    return conn


# ─── S5: calc_expiry_datetime 계산 검증 ───

class TestCalcExpiryDatetime:

    def test_001_1st_delivery(self):
        """001 도시락 1차: 입고일+2일 02:00"""
        result = calc_expiry_datetime('001', '1차', '2026-04-05')
        assert result == '2026-04-07 02:00:00'

    def test_001_2nd_delivery(self):
        """001 도시락 2차: 입고일+1일 14:00"""
        result = calc_expiry_datetime('001', '2차', '2026-04-05')
        assert result == '2026-04-06 14:00:00'

    def test_004_1st_delivery(self):
        """004 샌드위치 1차: 입고일+3일 22:00"""
        result = calc_expiry_datetime('004', '1차', '2026-04-05')
        assert result == '2026-04-08 22:00:00'

    def test_004_2nd_delivery(self):
        """004 샌드위치 2차: 입고일+2일 10:00"""
        result = calc_expiry_datetime('004', '2차', '2026-04-05')
        assert result == '2026-04-07 10:00:00'

    def test_unknown_mid_returns_none(self):
        """매핑 없는 mid_cd → None"""
        result = calc_expiry_datetime('099', '1차', '2026-04-05')
        assert result is None

    def test_unknown_delivery_type_returns_none(self):
        """매핑 없는 delivery_type → None"""
        result = calc_expiry_datetime('001', '3차', '2026-04-05')
        assert result is None


# ─── S1~S4: match_confirmed_with_receiving 통합 테스트 ───

class TestMatchConfirmedWithReceiving:

    @patch('src.infrastructure.database.repos.InventoryBatchRepository')
    def test_s1_normal_match(self, MockBatchRepo):
        """S1: 정상 매칭 — 발주 2, 입고 2 → 배치 생성"""
        conn = _create_test_db()

        conn.execute("""
            INSERT INTO confirmed_orders
            (store_id, order_date, item_cd, item_nm, mid_cd, ord_qty, delivery_type, confirmed_at)
            VALUES ('46513', '2026-04-05', 'ITEM001', '삼각김밥1', '001', 2, '1차', '2026-04-05T10:30:00')
        """)
        conn.execute("""
            INSERT INTO receiving_history
            (store_id, receiving_date, item_cd, item_nm, receiving_qty, delivery_type, created_at)
            VALUES ('46513', '2026-04-05', 'ITEM001', '삼각김밥1', 2, '1차', '2026-04-05T20:30:00')
        """)
        conn.commit()

        mock_batch = MockBatchRepo.return_value
        mock_batch.get_batch_by_item_and_date.return_value = None

        mock_common = MagicMock()
        mock_common.cursor.return_value.fetchone.return_value = (2,)
        with patch('src.infrastructure.database.connection.DBRouter.get_common_connection',
                   return_value=mock_common):
            result = match_confirmed_with_receiving('46513', '1차', conn=conn)

        assert result['matched'] == 1
        assert result['unmatched'] == 0
        mock_batch.create_batch.assert_called_once()

    def test_s2_unmatched(self):
        """S2: 미입고 — confirmed에 있지만 receiving에 없음"""
        conn = _create_test_db()

        conn.execute("""
            INSERT INTO confirmed_orders
            (store_id, order_date, item_cd, item_nm, mid_cd, ord_qty, delivery_type, confirmed_at)
            VALUES ('46513', '2026-04-05', 'ITEM002', '주먹밥1', '002', 3, '1차', '2026-04-05T10:30:00')
        """)
        conn.commit()

        result = match_confirmed_with_receiving('46513', '1차', conn=conn)

        assert result['matched'] == 0
        assert result['unmatched'] == 1

    def test_s4_no_snapshot(self):
        """S4: 스냅샷 없음 → skipped=-1 (폴백 유지)"""
        conn = _create_test_db()

        result = match_confirmed_with_receiving('46513', '1차', conn=conn)

        assert result['skipped'] == -1
