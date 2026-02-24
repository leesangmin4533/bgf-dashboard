"""
food-underorder-fix 테스트

과소발주 알고리즘 수정 검증:
- 날짜 필터: inventory_batches에 receiving_date 기반 조회 기간 제한
- 상수/공식: 하한 0.65, 승수 1.2 적용
- 표본 임계값: 배치 수 14 이상일 때만 상품별 블렌딩
- 캘리브레이터 클램프: 극단값을 현재 안전 범위 하한으로 교정
"""

import sqlite3
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.settings.constants import (
    DISUSE_COEF_FLOOR,
    DISUSE_COEF_MULTIPLIER,
    DISUSE_MIN_BATCH_COUNT,
    DISUSE_IB_LOOKBACK_DAYS,
    FOOD_WASTE_CAL_SAFETY_DAYS_RANGE,
    FOOD_WASTE_CAL_GAP_COEF_RANGE,
)
from src.prediction.categories.food import (
    get_dynamic_disuse_coefficient,
    calculate_food_dynamic_safety,
)
from src.prediction.food_waste_calibrator import (
    CalibrationParams,
    FoodWasteRateCalibrator,
    get_calibrated_food_params,
)


# =============================================================================
# 헬퍼: 테스트용 in-memory DB 생성
# =============================================================================

def _create_test_db():
    """inventory_batches + daily_sales + food_waste_calibration이 있는 in-memory DB"""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
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
            store_id TEXT DEFAULT '46513'
        )
    """)
    conn.execute("""
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT,
            sales_date TEXT,
            item_cd TEXT,
            mid_cd TEXT,
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            created_at TEXT,
            promo_type TEXT DEFAULT '',
            store_id TEXT DEFAULT '46513'
        )
    """)
    conn.execute("""
        CREATE TABLE food_waste_calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            mid_cd TEXT NOT NULL,
            calibration_date TEXT NOT NULL,
            actual_waste_rate REAL NOT NULL,
            target_waste_rate REAL NOT NULL,
            error REAL NOT NULL,
            sample_days INTEGER NOT NULL,
            total_order_qty INTEGER,
            total_waste_qty INTEGER,
            total_sold_qty INTEGER,
            param_name TEXT,
            old_value REAL,
            new_value REAL,
            current_params TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(store_id, mid_cd, calibration_date)
        )
    """)
    conn.execute("""
        CREATE TABLE product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            expiration_days INTEGER,
            order_unit_qty INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            maega_amt REAL,
            margin_rate REAL,
            store_id TEXT
        )
    """)
    return conn


def _insert_batch(conn, item_cd, mid_cd, receiving_date, initial_qty,
                   remaining_qty, status, store_id="46513"):
    """inventory_batches에 배치 데이터 삽입"""
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO inventory_batches
        (item_cd, mid_cd, receiving_date, expiration_days, expiry_date,
         initial_qty, remaining_qty, status, created_at, updated_at, store_id)
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
    """, (item_cd, mid_cd, receiving_date, receiving_date,
          initial_qty, remaining_qty, status, now, now, store_id))


def _insert_daily_sale(conn, item_cd, mid_cd, sales_date, sale_qty,
                        disuse_qty, store_id="46513"):
    """daily_sales에 데이터 삽입"""
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO daily_sales
        (collected_at, sales_date, item_cd, mid_cd, sale_qty, disuse_qty,
         created_at, store_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (now, sales_date, item_cd, mid_cd, sale_qty, disuse_qty, now, store_id))


# =============================================================================
# 1. 상수 검증
# =============================================================================

class TestConstants:
    """새 상수 존재 및 값 검증"""

    def test_disuse_coef_floor_value(self):
        """하한이 0.65"""
        assert DISUSE_COEF_FLOOR == 0.65

    def test_disuse_coef_multiplier_value(self):
        """승수가 1.2"""
        assert DISUSE_COEF_MULTIPLIER == 1.2

    def test_disuse_min_batch_count_value(self):
        """최소 배치 수가 14"""
        assert DISUSE_MIN_BATCH_COUNT == 14

    def test_disuse_ib_lookback_days_value(self):
        """IB 조회 기간이 30일"""
        assert DISUSE_IB_LOOKBACK_DAYS == 30

    def test_floor_greater_than_old(self):
        """새 하한(0.65)이 구 하한(0.5)보다 높음"""
        assert DISUSE_COEF_FLOOR > 0.5

    def test_multiplier_less_than_old(self):
        """새 승수(1.2)가 구 승수(1.5)보다 낮음 (덜 공격적)"""
        assert DISUSE_COEF_MULTIPLIER < 1.5


# =============================================================================
# 2. 공식 수학 검증
# =============================================================================

class TestFormulamath:
    """max(FLOOR, 1.0 - rate * MULTIPLIER) 공식 수학 검증"""

    def test_zero_waste_returns_1(self):
        """폐기율 0% → 계수 1.0"""
        coef = max(DISUSE_COEF_FLOOR, 1.0 - 0.0 * DISUSE_COEF_MULTIPLIER)
        assert coef == 1.0

    def test_10pct_waste(self):
        """폐기율 10% → 1.0 - 0.1*1.2 = 0.88"""
        coef = max(DISUSE_COEF_FLOOR, 1.0 - 0.1 * DISUSE_COEF_MULTIPLIER)
        assert abs(coef - 0.88) < 0.001

    def test_20pct_waste(self):
        """폐기율 20% → 1.0 - 0.2*1.2 = 0.76"""
        coef = max(DISUSE_COEF_FLOOR, 1.0 - 0.2 * DISUSE_COEF_MULTIPLIER)
        assert abs(coef - 0.76) < 0.001

    def test_25pct_waste(self):
        """폐기율 25% → 1.0 - 0.25*1.2 = 0.70"""
        coef = max(DISUSE_COEF_FLOOR, 1.0 - 0.25 * DISUSE_COEF_MULTIPLIER)
        assert abs(coef - 0.70) < 0.001

    def test_30pct_waste_hits_floor(self):
        """폐기율 30% → 1.0 - 0.3*1.2 = 0.64 < 0.65 → 하한 적용"""
        raw = 1.0 - 0.3 * DISUSE_COEF_MULTIPLIER
        assert raw < DISUSE_COEF_FLOOR
        coef = max(DISUSE_COEF_FLOOR, raw)
        assert coef == DISUSE_COEF_FLOOR

    def test_50pct_waste_floor(self):
        """폐기율 50% → 하한 0.65"""
        coef = max(DISUSE_COEF_FLOOR, 1.0 - 0.5 * DISUSE_COEF_MULTIPLIER)
        assert coef == DISUSE_COEF_FLOOR

    def test_floor_threshold_approx_29pct(self):
        """하한 도달 경계: (1.0-0.65)/1.2 ≈ 29.2%"""
        threshold = (1.0 - DISUSE_COEF_FLOOR) / DISUSE_COEF_MULTIPLIER
        assert abs(threshold - 0.2917) < 0.001


# =============================================================================
# 3. inventory_batches 날짜 필터
# =============================================================================

class TestInventoryBatchesDateFilter:
    """inventory_batches 쿼리에 receiving_date 날짜 필터 적용 확인"""

    def test_old_batches_excluded(self):
        """60일 전 배치는 제외되어야 함"""
        db = _create_test_db()
        today = datetime.now().strftime("%Y-%m-%d")
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

        # 60일 전 배치: 폐기율 높음 (expired)
        for i in range(20):
            _insert_batch(db, "ITEM001", "001", old_date, 10, 8, "expired")
        # 최근 배치: 폐기율 낮음 (consumed)
        for i in range(15):
            _insert_batch(db, "ITEM001", "001", today, 10, 0, "consumed")

        db_path = ":memory:"
        # in-memory DB이므로 직접 패치
        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM001", "001", days=30, db_path=db_path, store_id="46513"
            )
        # 60일 전 배치가 제외되므로 최근 15개(consumed, waste=0) 기반
        # coef는 1.0에 가까워야 함 (expired 배치가 없으므로)
        assert coef >= 0.9

    def test_recent_batches_included(self):
        """10일 전 배치는 포함되어야 함"""
        db = _create_test_db()
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

        # 최근 배치 20개: 일부 expired
        for i in range(14):
            _insert_batch(db, "ITEM002", "001", recent, 10, 0, "consumed")
        for i in range(6):
            _insert_batch(db, "ITEM002", "001", recent, 10, 5, "expired")
        # mid-level: 30% expired 비율
        for i in range(10):
            _insert_batch(db, "OTHER01", "001", recent, 10, 0, "consumed")
        for i in range(4):
            _insert_batch(db, "OTHER01", "001", recent, 10, 3, "expired")

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM002", "001", days=30, db_path=":memory:", store_id="46513"
            )
        # item_rate = 5*6 / (14*10 + 6*10) = 30/200 = 15%
        # 배치 20개 >= 14 → 블렌딩 적용
        # mid_rate = 12/280 ≈ 4.3%
        # blended = 15%*0.8 + 4.3%*0.2 = 12.9%
        # coef = max(0.65, 1.0 - 0.129 * 1.2) = max(0.65, 0.845) = 0.845
        assert 0.7 < coef < 1.0

    def test_no_recent_batches_falls_to_daily_sales(self):
        """최근 배치가 없으면 daily_sales fallback"""
        db = _create_test_db()
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        # 오래된 배치만 존재
        for i in range(20):
            _insert_batch(db, "ITEM003", "002", old_date, 10, 8, "expired")

        # daily_sales에 최근 데이터
        for i in range(10):
            _insert_daily_sale(db, "ITEM003", "002", recent, 8, 2)

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM003", "002", days=30, db_path=":memory:", store_id="46513"
            )
        # daily_sales: disuse_rate = 20/(80+20) = 20%
        # item_data_days = 10 >= 7 → 상품 데이터 사용
        # coef = max(0.65, 1.0 - 0.2*1.2) = 0.76
        assert 0.7 < coef < 0.85

    def test_date_filter_with_store_id(self):
        """store_id 있는 쿼리에도 날짜 필터 적용"""
        today = datetime.now().strftime("%Y-%m-%d")

        # 사전에 DB 2개 생성 (conn.close() 때문에 각각 필요)
        dbs = []
        for _ in range(2):
            db = _create_test_db()
            for i in range(15):
                _insert_batch(db, "ITEM004", "003", today, 10, 0, "consumed", "46513")
            for i in range(15):
                _insert_batch(db, "ITEM004", "003", today, 10, 8, "expired", "46704")
            dbs.append(db)

        db_iter = iter(dbs)
        with patch("src.prediction.categories.food.sqlite3.connect", side_effect=lambda *a, **kw: next(db_iter)):
            coef_513 = get_dynamic_disuse_coefficient(
                "ITEM004", "003", days=30, db_path=":memory:", store_id="46513"
            )
            coef_704 = get_dynamic_disuse_coefficient(
                "ITEM004", "003", days=30, db_path=":memory:", store_id="46704"
            )
        # 46513: consumed만 → coef ≈ 1.0
        # 46704: expired 많음 → coef < 1.0
        assert coef_513 > coef_704

    def test_mid_level_query_also_filtered(self):
        """중분류 쿼리도 날짜 필터 적용"""
        db = _create_test_db()
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        # 오래된 mid-level 배치 (폐기 높음)
        for i in range(30):
            _insert_batch(db, "OLD_ITEM", "001", old_date, 10, 9, "expired")
        # 최근 mid-level 배치 (폐기 낮음)
        for i in range(20):
            _insert_batch(db, "NEW_ITEM", "001", today, 10, 1, "consumed")

        # 아이템은 데이터 없음 → mid_rate만 사용
        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "NO_DATA_ITEM", "001", days=30, db_path=":memory:", store_id="46513"
            )
        # 최근 30일만 포함: NEW_ITEM 20개 consumed만 → mid_rate ≈ 0 → coef ≈ 1.0
        # 오래된 OLD_ITEM 30개 excluded
        assert coef >= 0.9


# =============================================================================
# 4. 표본 임계값 (배치 수 vs 캘린더 일수)
# =============================================================================

class TestSampleThreshold:
    """배치 수 기반 표본 충분성 판정"""

    def test_below_threshold_uses_mid_rate(self):
        """배치 10개 (< 14) → mid_rate만 사용"""
        db = _create_test_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # item: 10개 배치 (높은 폐기율)
        for i in range(5):
            _insert_batch(db, "ITEM_FEW", "001", today, 10, 0, "consumed")
        for i in range(5):
            _insert_batch(db, "ITEM_FEW", "001", today, 10, 8, "expired")
        # mid: 20개 배치 (낮은 폐기율)
        for i in range(18):
            _insert_batch(db, "OTHER_X", "001", today, 10, 0, "consumed")
        for i in range(2):
            _insert_batch(db, "OTHER_X", "001", today, 10, 2, "expired")

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM_FEW", "001", days=30, db_path=":memory:", store_id="46513"
            )
        # batch_count=10 < 14 → mid_only 사용
        # mid_rate = (8*5 + 2*2) / (10*10 + 20*10) = 44/300 ≈ 14.7%
        # coef = max(0.65, 1.0 - 0.147*1.2) ≈ 0.824
        assert coef > 0.75  # mid_rate 기반이므로 높게 나옴

    def test_above_threshold_blends(self):
        """배치 20개 (>= 14) → item 80% + mid 20% 블렌딩"""
        db = _create_test_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # item: 20개 배치 (30% 폐기율)
        for i in range(14):
            _insert_batch(db, "ITEM_MANY", "001", today, 10, 0, "consumed")
        for i in range(6):
            _insert_batch(db, "ITEM_MANY", "001", today, 10, 5, "expired")
        # mid: 포함됨
        for i in range(10):
            _insert_batch(db, "OTHER_Y", "001", today, 10, 0, "consumed")

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM_MANY", "001", days=30, db_path=":memory:", store_id="46513"
            )
        # batch_count=20 >= 14 → 블렌딩 적용
        # item_rate = 30/200 = 15%, mid_rate = 30/300 = 10%
        # blended = 15%*0.8 + 10%*0.2 = 14%
        # coef = max(0.65, 1.0 - 0.14*1.2) = max(0.65, 0.832) = 0.832
        assert 0.7 < coef < 0.95

    def test_exactly_14_batches_triggers_blending(self):
        """정확히 14개 배치 → 블렌딩 트리거"""
        db = _create_test_db()
        today = datetime.now().strftime("%Y-%m-%d")

        for i in range(14):
            _insert_batch(db, "ITEM_14", "002", today, 10, 0, "consumed")
        # mid-level에 다른 데이터 추가
        for i in range(10):
            _insert_batch(db, "MID_ITEM", "002", today, 10, 3, "expired")

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM_14", "002", days=30, db_path=":memory:", store_id="46513"
            )
        # 14 >= 14 → 블렌딩 적용
        # item: consumed만 → item_rate = 0%
        # mid: 30/240 = 12.5%
        # blended = 0*0.8 + 12.5%*0.2 = 2.5%
        # coef = max(0.65, 1.0 - 0.025*1.2) = max(0.65, 0.97) = 0.97
        assert coef > 0.9

    def test_13_batches_not_sufficient(self):
        """13개 배치 → 상품별 블렌딩 안 됨"""
        db = _create_test_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # item: 13개 배치 (높은 폐기율)
        for i in range(6):
            _insert_batch(db, "ITEM_13", "002", today, 10, 0, "consumed")
        for i in range(7):
            _insert_batch(db, "ITEM_13", "002", today, 10, 7, "expired")
        # mid: 낮은 폐기율
        for i in range(20):
            _insert_batch(db, "MID_LOW", "002", today, 10, 0, "consumed")

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "ITEM_13", "002", days=30, db_path=":memory:", store_id="46513"
            )
        # 13 < 14 → mid_only 사용
        # mid_rate = 49/330 ≈ 14.8% (ITEM_13의 expired도 mid에 포함)
        # coef = max(0.65, 1.0 - 0.148*1.2) ≈ 0.822
        assert coef > 0.75

    def test_daily_sales_fallback_uses_calendar_days(self):
        """daily_sales 폴백 경로에서는 캘린더 일수 7일 기준 유지"""
        db = _create_test_db()

        # inventory_batches 테이블 드롭 (폴백 강제)
        db.execute("DROP TABLE inventory_batches")
        db.execute("""
            CREATE TABLE inventory_batches (
                id INTEGER PRIMARY KEY, item_cd TEXT, mid_cd TEXT,
                receiving_date TEXT, expiration_days INTEGER,
                expiry_date TEXT, initial_qty INTEGER,
                remaining_qty INTEGER, status TEXT,
                created_at TEXT, updated_at TEXT, store_id TEXT
            )
        """)

        # daily_sales: 10일치 데이터 (>= 7 → 상품별 충분)
        for i in range(10):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_daily_sale(db, "DS_ITEM", "001", d, 8, 2)
        # mid-level daily_sales
        for i in range(10):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            _insert_daily_sale(db, "DS_OTHER", "001", d, 10, 0)

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "DS_ITEM", "001", days=30, db_path=":memory:", store_id="46513"
            )
        # daily_sales: item_data_days=10 >= 7 → 블렌딩
        # item_rate = 20/100 = 20%, mid_rate = 20/120 ≈ 16.7%
        # blended = 20%*0.8 + 16.7%*0.2 = 19.3%
        # coef = max(0.65, 1.0 - 0.193*1.2) ≈ 0.768
        assert 0.65 < coef < 0.85


# =============================================================================
# 5. calculate_food_dynamic_safety() 공식 동기화
# =============================================================================

class TestDynamicSafetyFormula:
    """calculate_food_dynamic_safety()가 새 공식 사용 확인"""

    @patch("src.prediction.categories.food.analyze_food_expiry_pattern")
    def test_disuse_rate_uses_new_formula(self, mock_pattern):
        """disuse_rate 전달 시 새 공식 적용"""
        from src.prediction.categories.food import FoodExpiryResult

        mock_pattern.return_value = FoodExpiryResult(
            item_cd="TEST", mid_cd="001",
            expiration_days=1, expiry_group="ultra_short",
            safety_days=0.5, data_source="test",
            daily_avg=5.0, actual_data_days=30,
            safety_stock=2.5, description="test"
        )

        with patch("src.prediction.food_waste_calibrator.get_calibrated_food_params", return_value=None):
            safety, result = calculate_food_dynamic_safety(
                "TEST", "001", daily_avg=5.0, disuse_rate=0.3, store_id="46513"
            )
        # disuse_coef = max(0.65, 1.0 - 0.3*1.2) = 0.65
        # safety_stock = 5.0 * 0.5 * 0.65 = 1.625
        assert result is not None
        # 새 하한 0.65 적용 확인 (구 하한 0.5이면 5.0*0.5*0.5=1.25)
        assert safety > 1.5


# =============================================================================
# 6. 캘리브레이터 극단값 클램프
# =============================================================================

class TestCalibratorClamp:
    """_clamp_stale_params() 테스트"""

    def test_clamp_safety_days_below_range(self):
        """safety_days가 범위 하한 미만이면 클램프"""
        db = _create_test_db()
        # 극단값 삽입: safety_days=0.2 (ultra_short 하한 0.35)
        params = CalibrationParams(safety_days=0.2, gap_coefficient=0.4, waste_buffer=3)
        db.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, calibration_date, actual_waste_rate,
             target_waste_rate, error, sample_days, current_params, created_at)
            VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?)
        """, ("46513", "001", "2026-02-23", params.to_json(), datetime.now().isoformat()))
        db.commit()

        cal = FoodWasteRateCalibrator(store_id="46513", db_path=":memory:")
        with patch.object(cal, "_get_conn", return_value=db):
            with patch("src.prediction.food_waste_calibrator.get_calibrated_food_params") as mock_get:
                mock_get.return_value = params
                clamped = cal._clamp_stale_params()

        assert clamped >= 1

    def test_clamp_gap_coef_below_range(self):
        """gap_coefficient가 범위 하한 미만이면 클램프"""
        db = _create_test_db()
        params = CalibrationParams(safety_days=0.5, gap_coefficient=0.1, waste_buffer=3)
        db.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, calibration_date, actual_waste_rate,
             target_waste_rate, error, sample_days, current_params, created_at)
            VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?)
        """, ("46513", "003", "2026-02-23", params.to_json(), datetime.now().isoformat()))
        db.commit()

        cal = FoodWasteRateCalibrator(store_id="46513", db_path=":memory:")
        with patch.object(cal, "_get_conn", return_value=db):
            with patch("src.prediction.food_waste_calibrator.get_calibrated_food_params") as mock_get:
                mock_get.return_value = params
                clamped = cal._clamp_stale_params()

        assert clamped >= 1

    def test_no_clamp_when_in_range(self):
        """범위 내 값이면 클램프 안 함"""
        db = _create_test_db()
        params = CalibrationParams(safety_days=0.5, gap_coefficient=0.4, waste_buffer=3)

        cal = FoodWasteRateCalibrator(store_id="46513", db_path=":memory:")
        with patch.object(cal, "_get_conn", return_value=db):
            with patch("src.prediction.food_waste_calibrator.get_calibrated_food_params") as mock_get:
                mock_get.return_value = params
                clamped = cal._clamp_stale_params()

        assert clamped == 0

    def test_clamp_called_at_calibrate_start(self):
        """calibrate() 호출 시 _clamp_stale_params() 먼저 실행"""
        from src.prediction.food_waste_calibrator import CalibrationResult
        cal = FoodWasteRateCalibrator(store_id="46513", db_path=":memory:")
        mock_result = CalibrationResult(
            mid_cd="001", actual_waste_rate=0.15, target_waste_rate=0.20,
            error=-0.05, sample_days=14, total_order_qty=100,
            total_waste_qty=15, total_sold_qty=85, adjusted=False, reason="deadband"
        )
        with patch.object(cal, "_clamp_stale_params", return_value=0) as mock_clamp:
            with patch.object(cal, "_calibrate_mid_cd", return_value=mock_result):
                with patch.object(cal, "_save_calibration"):
                    cal.calibrate()

        mock_clamp.assert_called_once()


# =============================================================================
# 7. 통합 / 시그니처 호환
# =============================================================================

class TestIntegration:
    """통합 테스트 및 하위 호환성"""

    def test_function_signature_compatible(self):
        """get_dynamic_disuse_coefficient() 시그니처 변경 없음"""
        import inspect
        sig = inspect.signature(get_dynamic_disuse_coefficient)
        params = list(sig.parameters.keys())
        assert "item_cd" in params
        assert "mid_cd" in params
        assert "days" in params
        assert "db_path" in params
        assert "store_id" in params

    def test_return_type_is_float(self):
        """반환값이 float"""
        db = _create_test_db()
        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "NONE_ITEM", "999", days=30, db_path=":memory:", store_id="46513"
            )
        assert isinstance(coef, float)

    def test_no_data_returns_1(self):
        """데이터 없으면 1.0 반환 (감량 없음)"""
        db = _create_test_db()
        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "EMPTY_ITEM", "001", days=30, db_path=":memory:", store_id="46513"
            )
        assert coef == 1.0

    def test_store_46513_scenario(self):
        """46513 시나리오: 최근 30일 데이터로 과소발주 해소"""
        db = _create_test_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # 도시락(001): 최근 배치 20개, 일부 폐기
        for i in range(15):
            _insert_batch(db, "DOSIRAK_A", "001", today, 10, 0, "consumed")
        for i in range(5):
            _insert_batch(db, "DOSIRAK_A", "001", today, 10, 4, "expired")
        # mid-level 추가
        for i in range(20):
            _insert_batch(db, "DOSIRAK_B", "001", today, 10, 1, "consumed")

        with patch("src.prediction.categories.food.sqlite3.connect", return_value=db):
            coef = get_dynamic_disuse_coefficient(
                "DOSIRAK_A", "001", days=30, db_path=":memory:", store_id="46513"
            )

        # item_rate = 20/200 = 10%, batch_count=20 >= 14 → 블렌딩
        # mid_rate = 20/400 = 5%
        # blended = 10%*0.8 + 5%*0.2 = 9%
        # coef = max(0.65, 1.0 - 0.09*1.2) = max(0.65, 0.892) = 0.892
        assert coef > 0.8  # 구 공식이면 0.865, 새 공식이면 0.892
        assert coef > DISUSE_COEF_FLOOR  # 하한 미도달
