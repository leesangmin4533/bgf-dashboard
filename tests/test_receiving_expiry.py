"""
배송차수별 폐기시간 계산 + delivery_type 판별 테스트

수정 대상:
- receiving_collector._determine_delivery_type()
- receiving_collector._calc_expiry_datetime()
- inventory_batch_repo.create_batch() delivery_type/expiry_datetime 파라미터
"""

import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from src.collectors.receiving_collector import (
    FOOD_EXPIRY_CONFIG,
    ReceivingCollector,
)
from src.infrastructure.database.repos.inventory_batch_repo import (
    InventoryBatchRepository,
)


# ─────────────────────────────────────────────
# _determine_delivery_type 테스트
# ─────────────────────────────────────────────

class TestDetermineDeliveryType:
    """_determine_delivery_type() 함수 테스트"""

    def setup_method(self):
        """테스트용 ReceivingCollector 인스턴스 (driver=None)"""
        self.collector = ReceivingCollector.__new__(ReceivingCollector)
        self.collector.store_id = '46513'

    def test_dosirak_1cha(self):
        """도시락 1차: item_nm '도)한돈불백정식1' → '1차'"""
        result = self.collector._determine_delivery_type(
            '', '도)한돈불백정식1', '001'
        )
        assert result == '1차'

    def test_dosirak_2cha(self):
        """도시락 2차: item_nm '도)한돈불백정식2' → '2차'"""
        result = self.collector._determine_delivery_type(
            '', '도)한돈불백정식2', '001'
        )
        assert result == '2차'

    def test_sandwich_1cha(self):
        """샌드위치 1차: item_nm '샌)딸기잼햄치즈샌드1' → '1차'"""
        result = self.collector._determine_delivery_type(
            '', '샌)딸기잼햄치즈샌드1', '004'
        )
        assert result == '1차'

    def test_sandwich_2cha(self):
        """샌드위치 2차: item_nm '샌)딸기잼햄치즈샌드2' → '2차'"""
        result = self.collector._determine_delivery_type(
            '', '샌)딸기잼햄치즈샌드2', '004'
        )
        assert result == '2차'

    def test_non_food_ambient(self):
        """비푸드 상품: 코카콜라 → 'ambient'"""
        result = self.collector._determine_delivery_type(
            '', '코카콜라500ml', '039'
        )
        assert result == 'ambient'

    def test_bread_no_suffix(self):
        """빵(012) 접미사 없음: → 'ambient' (1/2로 끝나지 않으므로)"""
        result = self.collector._determine_delivery_type(
            '', '연세)우유식빵', '012'
        )
        assert result == 'ambient'

    def test_bread_with_suffix_1(self):
        """빵(012) 접미사 1: → '1차'"""
        result = self.collector._determine_delivery_type(
            '', '연세)우유식빵1', '012'
        )
        assert result == '1차'

    def test_kimbap_1cha(self):
        """김밥 1차: mid_cd='003' → '1차'"""
        result = self.collector._determine_delivery_type(
            '', '김)참치김밥1', '003'
        )
        assert result == '1차'

    def test_hamburger_2cha(self):
        """햄버거 2차: mid_cd='005' → '2차'"""
        result = self.collector._determine_delivery_type(
            '', '햄)불고기버거2', '005'
        )
        assert result == '2차'

    def test_empty_item_nm(self):
        """item_nm 비어있으면 → 'ambient'"""
        result = self.collector._determine_delivery_type(
            '', '', '001'
        )
        assert result == 'ambient'


# ─────────────────────────────────────────────
# _calc_expiry_datetime 테스트
# ─────────────────────────────────────────────

class TestCalcExpiryDatetime:
    """_calc_expiry_datetime() 함수 테스트"""

    def setup_method(self):
        self.collector = ReceivingCollector.__new__(ReceivingCollector)
        self.collector.store_id = '46513'

    def test_dosirak_1cha_expiry(self):
        """도시락 1차: 2026-03-15 입고 → 2026-03-17 02:00:00"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '20:00', '001', '1차'
        )
        assert result == '2026-03-17 02:00:00'

    def test_dosirak_2cha_expiry(self):
        """도시락 2차: 2026-03-15 입고 → 2026-03-16 14:00:00"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '07:00', '001', '2차'
        )
        assert result == '2026-03-16 14:00:00'

    def test_sandwich_1cha_expiry(self):
        """샌드위치 1차: 2026-03-15 입고 → 2026-03-18 22:00:00"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '20:00', '004', '1차'
        )
        assert result == '2026-03-18 22:00:00'

    def test_sandwich_2cha_expiry(self):
        """샌드위치 2차: 2026-03-15 입고 → 2026-03-17 10:00:00"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '07:00', '004', '2차'
        )
        assert result == '2026-03-17 10:00:00'

    def test_kimbap_1cha_expiry(self):
        """김밥 1차: 2026-03-15 입고 → 2026-03-17 02:00:00"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '20:00', '003', '1차'
        )
        assert result == '2026-03-17 02:00:00'

    def test_hamburger_2cha_expiry(self):
        """햄버거 2차: 2026-03-15 입고 → 2026-03-17 10:00:00"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '07:00', '005', '2차'
        )
        assert result == '2026-03-17 10:00:00'

    def test_non_food_returns_none(self):
        """비푸드 → None (폴백 사용)"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '', '039', 'ambient'
        )
        assert result is None

    def test_bread_ambient_returns_none(self):
        """빵(012) ambient → None (FOOD_EXPIRY_CONFIG에 012 없음)"""
        result = self.collector._calc_expiry_datetime(
            '2026-03-15', '', '012', 'ambient'
        )
        assert result is None


# ─────────────────────────────────────────────
# create_batch delivery_type 저장 테스트
# ─────────────────────────────────────────────

class TestCreateBatchDeliveryType:
    """create_batch()에 delivery_type/expiry_datetime 전달 테스트"""

    def setup_method(self):
        """인메모리 DB로 테스트"""
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE inventory_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                store_id TEXT,
                delivery_type TEXT DEFAULT NULL
            )
        """)

    def teardown_method(self):
        self.conn.close()

    def _create_repo(self):
        """mock _get_conn으로 인메모리 DB 연결 (close를 no-op 래퍼로)"""
        repo = InventoryBatchRepository.__new__(InventoryBatchRepository)
        repo.store_id = '46513'

        # sqlite3.Connection.close는 read-only이므로 래퍼 객체 사용
        class _NoCloseConn:
            """close()를 no-op으로 만드는 래퍼"""
            def __init__(self, conn):
                self._conn = conn
            def close(self):
                pass  # 인메모리 DB 유지를 위해 close 무시
            def __getattr__(self, name):
                return getattr(self._conn, name)

        wrapped = _NoCloseConn(self.conn)
        repo._get_conn = MagicMock(return_value=wrapped)
        return repo

    def test_create_batch_with_delivery_type(self):
        """delivery_type='1차' 저장 확인"""
        repo = self._create_repo()
        batch_id = repo.create_batch(
            item_cd='TEST001',
            item_nm='도)테스트도시락1',
            mid_cd='001',
            receiving_date='2026-03-15',
            expiration_days=1,
            initial_qty=3,
            store_id='46513',
            delivery_type='1차',
            expiry_datetime='2026-03-17 02:00:00',
        )
        assert batch_id > 0

        row = self.conn.execute(
            "SELECT delivery_type, expiry_date FROM inventory_batches WHERE id = ?",
            (batch_id,)
        ).fetchone()
        assert row['delivery_type'] == '1차'
        assert row['expiry_date'] == '2026-03-17 02:00:00'

    def test_create_batch_with_2cha(self):
        """delivery_type='2차' + expiry_datetime 저장 확인"""
        repo = self._create_repo()
        batch_id = repo.create_batch(
            item_cd='TEST002',
            item_nm='도)테스트도시락2',
            mid_cd='001',
            receiving_date='2026-03-15',
            expiration_days=1,
            initial_qty=2,
            store_id='46513',
            delivery_type='2차',
            expiry_datetime='2026-03-16 14:00:00',
        )
        row = self.conn.execute(
            "SELECT delivery_type, expiry_date FROM inventory_batches WHERE id = ?",
            (batch_id,)
        ).fetchone()
        assert row['delivery_type'] == '2차'
        assert row['expiry_date'] == '2026-03-16 14:00:00'

    def test_create_batch_without_delivery_type(self):
        """delivery_type=None → date 기반 폴백"""
        repo = self._create_repo()
        batch_id = repo.create_batch(
            item_cd='TEST003',
            item_nm='코카콜라500ml',
            mid_cd='039',
            receiving_date='2026-03-15',
            expiration_days=365,
            initial_qty=10,
            store_id='46513',
        )
        row = self.conn.execute(
            "SELECT delivery_type, expiry_date FROM inventory_batches WHERE id = ?",
            (batch_id,)
        ).fetchone()
        assert row['delivery_type'] is None
        assert row['expiry_date'] == '2027-03-15'  # date 기반


# ─────────────────────────────────────────────
# FOOD_EXPIRY_CONFIG 정합성 테스트
# ─────────────────────────────────────────────

class TestFoodExpiryConfig:
    """FOOD_EXPIRY_CONFIG 상수 정합성 검증"""

    def test_all_food_mid_cds_covered(self):
        """001~005 모두 CONFIG에 존재"""
        for mid_cd in ['001', '002', '003', '004', '005']:
            assert mid_cd in FOOD_EXPIRY_CONFIG, f"{mid_cd} 누락"

    def test_each_has_1cha_2cha(self):
        """각 mid_cd에 1차/2차 설정 존재"""
        for mid_cd, config in FOOD_EXPIRY_CONFIG.items():
            assert '1차' in config, f"{mid_cd}에 1차 설정 누락"
            assert '2차' in config, f"{mid_cd}에 2차 설정 누락"

    def test_2cha_expiry_before_1cha(self):
        """2차 폐기시간 < 1차 폐기시간 (2차가 먼저 만료)"""
        for mid_cd, config in FOOD_EXPIRY_CONFIG.items():
            days_1, hour_1 = config['1차']
            days_2, hour_2 = config['2차']
            # 2차 폐기일이 1차 폐기일보다 이르거나 같아야 함
            assert (days_2, hour_2) <= (days_1, hour_1), \
                f"{mid_cd}: 2차({days_2}d {hour_2}h) > 1차({days_1}d {hour_1}h)"
