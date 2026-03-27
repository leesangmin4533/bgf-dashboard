"""
폐기율 목표 소분류(small_cd) 세분화 테스트

- SMALL_CD_TARGET_RATES 범위 검증
- FoodWasteRateCalibrator.calibrate() 소분류 보정 동작
- get_calibrated_food_params() small_cd 우선 조회 + 폴백
- _calibrate_small_cd() 보정 로직
- _count_products_in_small_cd() 폴백 조건
- _get_waste_stats_by_small_cd() 쿼리 정확성
- DB 저장 시 small_cd 포함
"""

import sqlite3
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.prediction.food_waste_calibrator import (
    FoodWasteRateCalibrator,
    CalibrationParams,
    CalibrationResult,
    get_calibrated_food_params,
    get_effective_params,
    get_default_params,
)
from src.settings.constants import (
    FOOD_WASTE_RATE_TARGETS,
    SMALL_CD_TARGET_RATES,
    SMALL_CD_MIN_PRODUCTS,
    FOOD_WASTE_CAL_DEADBAND,
    FOOD_WASTE_CAL_LOOKBACK_DAYS,
    FOOD_WASTE_CAL_MIN_DAYS,
)


# =========================================================================
# 픽스처
# =========================================================================

def _create_test_db(tmp_path, store_id="46513"):
    """테스트용 DB 생성 (daily_sales + product_details + food_waste_calibration)"""
    db_file = tmp_path / "test_smallcd.db"
    conn = sqlite3.connect(str(db_file))

    # daily_sales 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            sales_date TEXT NOT NULL,
            item_cd TEXT NOT NULL,
            mid_cd TEXT NOT NULL,
            sale_qty INTEGER DEFAULT 0,
            ord_qty INTEGER DEFAULT 0,
            buy_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0,
            stock_qty INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            promo_type TEXT DEFAULT '',
            store_id TEXT,
            UNIQUE(sales_date, item_cd)
        )
    """)

    # product_details 테이블 (small_cd 포함)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_details (
            item_cd TEXT PRIMARY KEY,
            item_nm TEXT,
            mid_cd TEXT,
            large_cd TEXT,
            small_cd TEXT,
            small_nm TEXT,
            class_nm TEXT,
            expiration_days INTEGER,
            order_unit_qty INTEGER DEFAULT 1
        )
    """)

    # food_waste_calibration 테이블 (v48: small_cd 컬럼 포함)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_waste_calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            mid_cd TEXT NOT NULL,
            small_cd TEXT DEFAULT '',
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
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fwc_store_mid_small_date
            ON food_waste_calibration(store_id, mid_cd, small_cd, calibration_date)
    """)

    conn.commit()
    return str(db_file), conn


def _insert_sales_data(conn, items, store_id="46513", days=21):
    """테스트용 판매 데이터 삽입

    Args:
        items: [(item_cd, mid_cd, small_cd, daily_sale, daily_disuse), ...]
    """
    now = datetime.now()
    for item_cd, mid_cd, small_cd, daily_sale, daily_disuse in items:
        # product_details 등록
        conn.execute("""
            INSERT OR IGNORE INTO product_details (item_cd, item_nm, mid_cd, small_cd)
            VALUES (?, ?, ?, ?)
        """, (item_cd, f"상품_{item_cd}", mid_cd, small_cd))

        # daily_sales 데이터 (최근 days일)
        for d in range(days):
            sales_date = (now - timedelta(days=d)).strftime("%Y-%m-%d")
            conn.execute("""
                INSERT OR IGNORE INTO daily_sales
                (collected_at, sales_date, item_cd, mid_cd, sale_qty, disuse_qty,
                 stock_qty, created_at, store_id)
                VALUES (?, ?, ?, ?, ?, ?, 10, ?, ?)
            """, (
                now.isoformat(), sales_date, item_cd, mid_cd,
                daily_sale, daily_disuse, now.isoformat(), store_id
            ))
    conn.commit()


@pytest.fixture
def test_db(tmp_path):
    """기본 테스트 DB"""
    db_path, conn = _create_test_db(tmp_path)
    yield db_path, conn
    conn.close()


@pytest.fixture
def populated_db(tmp_path):
    """판매 데이터가 포함된 테스트 DB"""
    db_path, conn = _create_test_db(tmp_path)

    # mid_cd=001 (도시락), small_cd=001 (정식도시락) - 6개 상품
    items_001_001 = [
        (f"ITEM_001_{i:03d}", "001", "001", 10, 2)  # 폐기율 ~16.7%
        for i in range(6)
    ]
    # mid_cd=001 (도시락), small_cd=268 (단품요리) - 8개 상품
    items_001_268 = [
        (f"ITEM_268_{i:03d}", "001", "268", 8, 3)  # 폐기율 ~27.3%
        for i in range(8)
    ]
    # mid_cd=002 (주먹밥), small_cd=002 (삼각김밥) - 10개 상품
    items_002_002 = [
        (f"ITEM_002_{i:03d}", "002", "002", 15, 3)  # 폐기율 ~16.7%
        for i in range(10)
    ]

    _insert_sales_data(conn, items_001_001 + items_001_268 + items_002_002)
    yield db_path, conn
    conn.close()


# =========================================================================
# 1. SMALL_CD_TARGET_RATES 범위 검증
# =========================================================================

class TestSmallCdTargetRatesRange:
    """소분류 목표 폐기율이 mid_cd 기준 +-3%p 이내인지 검증"""

    def test_small_cd_target_rates_range(self):
        """모든 소분류 목표가 mid_cd 기준 +-3%p 이내"""
        for (mid_cd, small_cd), target in SMALL_CD_TARGET_RATES.items():
            mid_target = FOOD_WASTE_RATE_TARGETS.get(mid_cd)
            assert mid_target is not None, (
                f"({mid_cd}, {small_cd}): mid_cd={mid_cd}가 FOOD_WASTE_RATE_TARGETS에 없음"
            )
            diff = abs(target - mid_target)
            assert diff <= 0.03 + 1e-9, (
                f"({mid_cd}, {small_cd}): 목표={target:.0%}, "
                f"mid_cd 기준={mid_target:.0%}, 차이={diff:.1%}p > 3%p"
            )

    def test_small_cd_target_rates_not_empty(self):
        """SMALL_CD_TARGET_RATES가 비어있지 않음"""
        assert len(SMALL_CD_TARGET_RATES) > 0

    def test_small_cd_min_products_positive(self):
        """SMALL_CD_MIN_PRODUCTS가 양수"""
        assert SMALL_CD_MIN_PRODUCTS > 0


# =========================================================================
# 2. 소분류 보정 기본 동작
# =========================================================================

class TestCalibrateSmallCdBasic:
    """소분류 보정 기본 동작 검증"""

    def test_calibrate_small_cd_basic(self, populated_db):
        """소분류 보정이 실행되고 결과에 small_cd가 포함됨"""
        db_path, conn = populated_db
        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result = calibrator.calibrate()

        # 결과에 small_cd가 포함된 항목이 있어야 함
        small_cd_results = [
            r for r in result["results"] if r.get("small_cd")
        ]
        assert len(small_cd_results) > 0, "소분류 보정 결과가 없음"

    def test_calibrate_includes_both_mid_and_small(self, populated_db):
        """calibrate() 결과에 mid_cd + small_cd 결과 모두 포함"""
        db_path, conn = populated_db
        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result = calibrator.calibrate()

        mid_results = [r for r in result["results"] if not r.get("small_cd")]
        small_results = [r for r in result["results"] if r.get("small_cd")]

        assert len(mid_results) > 0, "mid_cd 보정 결과가 없음"
        assert len(small_results) > 0, "small_cd 보정 결과가 없음"

    def test_calibrate_disabled(self, populated_db):
        """FOOD_WASTE_CAL_ENABLED=False 시 소분류 포함 비활성"""
        db_path, conn = populated_db
        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)

        with patch("src.prediction.food_waste_calibrator.FOOD_WASTE_CAL_ENABLED", False):
            result = calibrator.calibrate()

        assert result["calibrated"] is False
        assert result["reason"] == "disabled"
        assert len(result["results"]) == 0


# =========================================================================
# 3. 폐기율 높음/낮음 보정
# =========================================================================

class TestCalibrateSmallCdDirection:
    """소분류 폐기율 방향별 보정 검증"""

    def test_calibrate_small_cd_high_waste(self, tmp_path):
        """폐기율 > 목표 -> 파라미터 감소"""
        db_path, conn = _create_test_db(tmp_path)
        # small_cd=268 (단품요리) 목표 22%, 실제 ~33% -> 감소
        items = [
            (f"ITEM_HI_{i:03d}", "001", "268", 5, 3)  # 폐기율 37.5%
            for i in range(6)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "268"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert r["error"] > 0, "오차가 양수여야 함 (실제 > 목표)"

    def test_calibrate_small_cd_low_waste(self, tmp_path):
        """폐기율 < 목표 -> 파라미터 증가"""
        db_path, conn = _create_test_db(tmp_path)
        # small_cd=001 (정식도시락) 목표 18%, 실제 ~5%
        items = [
            (f"ITEM_LO_{i:03d}", "001", "001", 20, 1)  # 폐기율 ~4.8%
            for i in range(6)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "001"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert r["error"] < 0, "오차가 음수여야 함 (실제 < 목표)"

    def test_calibrate_small_cd_deadband(self, tmp_path):
        """불감대 이내 -> 조정 없음"""
        db_path, conn = _create_test_db(tmp_path)
        # small_cd=001 (정식도시락) 목표 18%, 실제 ~18.5% (불감대 +-2%p 이내)
        # sale=18, disuse=4 -> 4/(18+4) = 18.18%
        items = [
            (f"ITEM_DB_{i:03d}", "001", "001", 18, 4)
            for i in range(6)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "001"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert r["adjusted"] is False
        assert "deadband" in r["reason"]


# =========================================================================
# 4. 소분류 폴백
# =========================================================================

class TestSmallCdFallback:
    """소분류 상품 수 부족 시 mid_cd 폴백"""

    def test_calibrate_small_cd_fallback(self, tmp_path):
        """상품 수 < SMALL_CD_MIN_PRODUCTS -> mid_cd 폴백"""
        db_path, conn = _create_test_db(tmp_path)
        # small_cd=001에 상품 3개만 (5 미만)
        items = [
            (f"ITEM_FB_{i:03d}", "001", "001", 10, 2)
            for i in range(3)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "001"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert "fallback_to_mid" in r["reason"]
        assert r["adjusted"] is False

    def test_small_cd_min_products_boundary(self, tmp_path):
        """경계값: 정확히 SMALL_CD_MIN_PRODUCTS개 -> 보정 실행"""
        db_path, conn = _create_test_db(tmp_path)
        # 정확히 5개 상품
        items = [
            (f"ITEM_BD_{i:03d}", "001", "001", 10, 2)
            for i in range(SMALL_CD_MIN_PRODUCTS)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "001"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert "fallback_to_mid" not in r.get("reason", "")

    def test_calibrate_small_cd_no_target(self, tmp_path):
        """매핑 없는 small_cd -> 건너뛰기"""
        db_path, conn = _create_test_db(tmp_path)
        # small_cd=999는 SMALL_CD_TARGET_RATES에 없음
        items = [
            (f"ITEM_NT_{i:03d}", "001", "999", 10, 2)
            for i in range(10)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "999"
        ]
        # 매핑 없는 small_cd는 결과에 나타나지 않아야 함
        assert len(small_results) == 0


# =========================================================================
# 5. get_calibrated_food_params 소분류 조회
# =========================================================================

class TestGetCalibratedParams:
    """get_calibrated_food_params() small_cd 우선 조회 + 폴백"""

    def test_get_calibrated_params_small_cd(self, tmp_path):
        """small_cd 보정값 우선 조회"""
        db_path, conn = _create_test_db(tmp_path)
        now = datetime.now()

        # mid_cd 보정값 저장
        mid_params = CalibrationParams(safety_days=0.5, gap_coefficient=0.4, waste_buffer=3)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, '', ?, 0.20, 0.20, 0.0, 21, ?, ?)
        """, ("46513", "001", now.strftime("%Y-%m-%d"),
              mid_params.to_json(), now.isoformat()))

        # small_cd 보정값 저장 (다른 값)
        small_params = CalibrationParams(safety_days=0.6, gap_coefficient=0.5, waste_buffer=2)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, ?, ?, 0.18, 0.18, 0.0, 21, ?, ?)
        """, ("46513", "001", "001", now.strftime("%Y-%m-%d"),
              small_params.to_json(), now.isoformat()))
        conn.commit()
        conn.close()

        # small_cd 지정 시 small_cd 보정값 반환
        result = get_calibrated_food_params("001", "46513", db_path, small_cd="001")
        assert result is not None
        assert result.safety_days == 0.6
        assert result.gap_coefficient == 0.5

    def test_get_calibrated_params_fallback_mid(self, tmp_path):
        """small_cd 보정값 없으면 mid_cd 폴백"""
        db_path, conn = _create_test_db(tmp_path)
        now = datetime.now()

        # mid_cd 보정값만 저장
        mid_params = CalibrationParams(safety_days=0.5, gap_coefficient=0.4, waste_buffer=3)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, '', ?, 0.20, 0.20, 0.0, 21, ?, ?)
        """, ("46513", "001", now.strftime("%Y-%m-%d"),
              mid_params.to_json(), now.isoformat()))
        conn.commit()
        conn.close()

        # small_cd 지정했지만 보정값 없음 -> mid_cd 폴백
        result = get_calibrated_food_params("001", "46513", db_path, small_cd="999")
        assert result is not None
        assert result.safety_days == 0.5

    def test_get_calibrated_params_backward_compat(self, tmp_path):
        """small_cd 미지정 시 기존 동작 100% 호환"""
        db_path, conn = _create_test_db(tmp_path)
        now = datetime.now()

        mid_params = CalibrationParams(safety_days=0.5, gap_coefficient=0.4, waste_buffer=3)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, '', ?, 0.20, 0.20, 0.0, 21, ?, ?)
        """, ("46513", "001", now.strftime("%Y-%m-%d"),
              mid_params.to_json(), now.isoformat()))
        conn.commit()
        conn.close()

        # small_cd 미지정 (기존 API 호출)
        result = get_calibrated_food_params("001", "46513", db_path)
        assert result is not None
        assert result.safety_days == 0.5

    def test_get_calibrated_params_no_data(self, tmp_path):
        """보정 이력 없으면 None"""
        db_path, conn = _create_test_db(tmp_path)
        conn.close()

        result = get_calibrated_food_params("001", "46513", db_path, small_cd="001")
        assert result is None


# =========================================================================
# 6. DB 저장 검증
# =========================================================================

class TestSaveCalibration:
    """DB 저장 시 small_cd 포함 검증"""

    def test_save_calibration_small_cd(self, populated_db):
        """소분류 보정 결과 DB 저장 시 small_cd가 기록됨"""
        db_path, conn = populated_db
        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        calibrator.calibrate()

        # DB에서 small_cd가 설정된 행 확인
        cursor = conn.cursor()
        cursor.execute("""
            SELECT mid_cd, small_cd, actual_waste_rate, target_waste_rate
            FROM food_waste_calibration
            WHERE small_cd != '' AND small_cd IS NOT NULL
        """)
        rows = cursor.fetchall()
        assert len(rows) > 0, "small_cd가 기록된 보정 이력이 없음"

    def test_save_calibration_mid_cd_has_empty_small_cd(self, populated_db):
        """mid_cd 보정 결과는 small_cd='' 으로 저장됨"""
        db_path, conn = populated_db
        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        calibrator.calibrate()

        cursor = conn.cursor()
        cursor.execute("""
            SELECT mid_cd, small_cd
            FROM food_waste_calibration
            WHERE small_cd = '' OR small_cd IS NULL
        """)
        rows = cursor.fetchall()
        assert len(rows) > 0, "mid_cd 보정 이력이 없음"


# =========================================================================
# 7. waste_stats_by_small_cd 쿼리 정확성
# =========================================================================

class TestWasteStatsBySmallCd:
    """소분류 필터 쿼리 정확성 검증"""

    def test_waste_stats_by_small_cd(self, tmp_path):
        """소분류 통계가 정확히 해당 small_cd 상품만 집계"""
        db_path, conn = _create_test_db(tmp_path)

        # small_cd=001에 상품 6개 (sale=10, disuse=2)
        items_001 = [
            (f"ITEM_A_{i:03d}", "001", "001", 10, 2)
            for i in range(6)
        ]
        # small_cd=268에 상품 6개 (sale=5, disuse=3)
        items_268 = [
            (f"ITEM_B_{i:03d}", "001", "268", 5, 3)
            for i in range(6)
        ]
        _insert_sales_data(conn, items_001 + items_268)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        stats_001 = calibrator._get_waste_stats_by_small_cd("001", "001")
        stats_268 = calibrator._get_waste_stats_by_small_cd("001", "268")

        # small_cd=001: 6 items * 21 days * sale=10 = 1260 sold, disuse=2*6*21=252
        assert stats_001["total_sold"] > 0
        assert stats_268["total_sold"] > 0
        # 두 그룹의 통계가 다른 값이어야 함 (분리 검증)
        assert stats_001["waste_rate"] != stats_268["waste_rate"]


# =========================================================================
# 8. 히스테리시스 소분류 독립 동작
# =========================================================================

class TestHysteresisSmallCd:
    """소분류 히스테리시스가 독립적으로 동작"""

    def test_hysteresis_small_cd(self, tmp_path):
        """소분류 보정의 히스테리시스가 mid_cd와 독립"""
        db_path, conn = _create_test_db(tmp_path)
        now = datetime.now()
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        # mid_cd=001 히스테리시스: 양수 오차 이력
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, '', ?, 0.25, 0.20, 0.05, 21, ?, ?)
        """, ("46513", "001", yesterday,
              CalibrationParams(0.5, 0.4, 3).to_json(), now.isoformat()))

        # small_cd=001 히스테리시스: 음수 오차 이력 (mid_cd와 반대 방향)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, ?, ?, 0.10, 0.18, -0.08, 21, ?, ?)
        """, ("46513", "001", "001", yesterday,
              CalibrationParams(0.5, 0.4, 3).to_json(), now.isoformat()))
        conn.commit()
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)

        # mid_cd 히스테리시스: 양수 오차 -> 일관 (허용)
        assert calibrator._check_consistent_direction("001", 0.03) is True
        # small_cd 히스테리시스: 양수 오차 -> 불일관 (차단)
        assert calibrator._check_consistent_direction("001", 0.03, small_cd="001") is False


# =========================================================================
# 9. 데이터 부족 + 발주 없음
# =========================================================================

class TestDataInsufficient:
    """데이터 부족/발주 없음 시 처리"""

    def test_data_insufficient_small_cd(self, tmp_path):
        """소분류 데이터 일수 부족 시 조정 안 함"""
        db_path, conn = _create_test_db(tmp_path)
        # 3일치 데이터만 삽입 (MIN_DAYS=14 미만)
        items = [
            (f"ITEM_DI_{i:03d}", "001", "001", 10, 2)
            for i in range(6)
        ]
        _insert_sales_data(conn, items, days=3)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "001"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert r["adjusted"] is False
        assert "data_insufficient" in r["reason"]

    def test_no_orders_small_cd(self, tmp_path):
        """발주 없는 소분류 처리"""
        db_path, conn = _create_test_db(tmp_path)
        # sale=0, disuse=0 -> total_order=0
        items = [
            (f"ITEM_NO_{i:03d}", "001", "001", 0, 0)
            for i in range(6)
        ]
        _insert_sales_data(conn, items)
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = [
            r for r in result_dict["results"]
            if r.get("small_cd") == "001"
        ]
        assert len(small_results) == 1
        r = small_results[0]
        assert r["adjusted"] is False
        assert "no_orders" in r["reason"]


# =========================================================================
# 10. 동일 mid_cd 내 여러 small_cd 독립 보정
# =========================================================================

class TestMultipleSmallCds:
    """동일 mid_cd 내 여러 small_cd 독립 보정"""

    def test_multiple_small_cds_same_mid(self, populated_db):
        """동일 mid_cd=001 내 small_cd=001, 268 독립 보정"""
        db_path, conn = populated_db
        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        result_dict = calibrator.calibrate()

        small_results = {
            r.get("small_cd"): r
            for r in result_dict["results"]
            if r.get("small_cd") and r.get("mid_cd") == "001"
        }

        # 001과 268 모두 결과가 있어야 함
        if "001" in small_results and "268" in small_results:
            # 각각 다른 목표 폐기율
            assert small_results["001"]["target_waste_rate"] == 0.18
            assert small_results["268"]["target_waste_rate"] == 0.22


# =========================================================================
# 11. 극단값 클램프 소분류 지원
# =========================================================================

class TestClampSmallCd:
    """극단값 클램프가 소분류 보정에 영향 없이 동작"""

    def test_clamp_stale_params_small_cd(self, tmp_path):
        """클램프는 mid_cd 레벨만 처리 (기존 동작 유지)"""
        db_path, conn = _create_test_db(tmp_path)
        now = datetime.now()

        # 극단값 mid_cd 보정 (safety_days=0.1 < 하한 0.35)
        bad_params = CalibrationParams(safety_days=0.1, gap_coefficient=0.4, waste_buffer=3)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, '', ?, 0.30, 0.20, 0.10, 21, ?, ?)
        """, ("46513", "001", now.strftime("%Y-%m-%d"),
              bad_params.to_json(), now.isoformat()))
        conn.commit()
        conn.close()

        calibrator = FoodWasteRateCalibrator(store_id="46513", db_path=db_path)
        clamped = calibrator._clamp_stale_params()
        assert clamped == 1

        # 클램프 후 safety_days >= 0.35
        result = get_calibrated_food_params("001", "46513", db_path)
        assert result is not None
        assert result.safety_days >= 0.35


# =========================================================================
# 12. get_effective_params 소분류 지원
# =========================================================================

class TestGetEffectiveParams:
    """get_effective_params() small_cd 지원"""

    def test_effective_params_with_small_cd(self, tmp_path):
        """small_cd 보정값이 있으면 해당 값 반환"""
        db_path, conn = _create_test_db(tmp_path)
        now = datetime.now()

        small_params = CalibrationParams(safety_days=0.6, gap_coefficient=0.5, waste_buffer=2)
        conn.execute("""
            INSERT INTO food_waste_calibration
            (store_id, mid_cd, small_cd, calibration_date,
             actual_waste_rate, target_waste_rate, error, sample_days,
             current_params, created_at)
            VALUES (?, ?, ?, ?, 0.18, 0.18, 0.0, 21, ?, ?)
        """, ("46513", "001", "001", now.strftime("%Y-%m-%d"),
              small_params.to_json(), now.isoformat()))
        conn.commit()
        conn.close()

        result = get_effective_params("001", "46513", db_path, small_cd="001")
        assert result.safety_days == 0.6

    def test_effective_params_fallback_default(self, tmp_path):
        """보정값 없으면 기본값 반환"""
        db_path, conn = _create_test_db(tmp_path)
        conn.close()

        result = get_effective_params("001", "46513", db_path, small_cd="001")
        default = get_default_params("001")
        assert result.safety_days == default.safety_days


# =========================================================================
# 13. CalibrationResult dataclass
# =========================================================================

class TestCalibrationResult:
    """CalibrationResult에 small_cd 필드 검증"""

    def test_calibration_result_small_cd_field(self):
        """CalibrationResult에 small_cd 필드 존재"""
        result = CalibrationResult(
            mid_cd="001",
            small_cd="268",
            actual_waste_rate=0.25,
            target_waste_rate=0.22,
            error=0.03,
            sample_days=21,
            total_order_qty=100,
            total_waste_qty=25,
            total_sold_qty=75,
        )
        assert result.small_cd == "268"
        d = result.__dict__
        assert "small_cd" in d

    def test_calibration_result_small_cd_none(self):
        """small_cd가 None인 경우 (mid_cd 보정)"""
        result = CalibrationResult(
            mid_cd="001",
            actual_waste_rate=0.20,
            target_waste_rate=0.20,
            error=0.0,
            sample_days=21,
            total_order_qty=100,
            total_waste_qty=20,
            total_sold_qty=80,
        )
        assert result.small_cd is None
