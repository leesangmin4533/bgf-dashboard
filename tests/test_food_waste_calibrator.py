"""FoodWasteRateCalibrator 테스트"""

import json
import sqlite3
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.prediction.food_waste_calibrator import (
    FoodWasteRateCalibrator,
    CalibrationParams,
    CalibrationResult,
    get_calibrated_food_params,
    get_default_params,
    get_effective_params,
)
from src.settings.constants import (
    FOOD_WASTE_RATE_TARGETS,
    FOOD_WASTE_CAL_DEADBAND,
    FOOD_WASTE_CAL_STEP_SMALL,
    FOOD_WASTE_CAL_STEP_LARGE,
    FOOD_WASTE_CAL_ERROR_LARGE,
    FOOD_WASTE_CAL_MIN_DAYS,
)


# =============================================================================
# 픽스처
# =============================================================================

@pytest.fixture
def cal_db(tmp_path):
    """테스트용 DB (food_waste_calibration + daily_sales 테이블)"""
    db_path = tmp_path / "test_cal.db"
    conn = sqlite3.connect(str(db_path))

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
        CREATE TABLE daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            item_cd TEXT,
            mid_cd TEXT,
            sales_date TEXT,
            sale_qty INTEGER DEFAULT 0,
            disuse_qty INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    return str(db_path)


def _insert_sales(db_path, mid_cd, days, avg_sale, avg_disuse, store_id=""):
    """테스트 판매 데이터 삽입"""
    conn = sqlite3.connect(db_path)
    today = datetime.now()
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO daily_sales (store_id, item_cd, mid_cd, sales_date, sale_qty, disuse_qty) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (store_id, f"item_{mid_cd}_{i}", mid_cd, d, avg_sale, avg_disuse),
        )
    conn.commit()
    conn.close()


# =============================================================================
# CalibrationParams 테스트
# =============================================================================

class TestCalibrationParams:
    def test_to_json_and_back(self):
        params = CalibrationParams(safety_days=0.5, gap_coefficient=0.4, waste_buffer=3)
        js = params.to_json()
        restored = CalibrationParams.from_json(js)
        assert restored.safety_days == 0.5
        assert restored.gap_coefficient == 0.4
        assert restored.waste_buffer == 3

    def test_get_default_params_dosirak(self):
        """도시락(001) 기본 파라미터: ultra_short"""
        params = get_default_params("001")
        assert params.safety_days == 0.5
        assert params.gap_coefficient == 0.4
        assert params.waste_buffer == 3

    def test_get_default_params_sandwich(self):
        """샌드위치(004) 기본 파라미터: short"""
        params = get_default_params("004")
        assert params.safety_days == 0.7
        assert params.gap_coefficient == 0.6
        assert params.waste_buffer == 3

    def test_get_default_params_bread(self):
        """빵(012) 기본 파라미터: short"""
        params = get_default_params("012")
        assert params.safety_days == 0.7
        assert params.gap_coefficient == 0.6
        assert params.waste_buffer == 3


# =============================================================================
# DB 조회 테스트
# =============================================================================

class TestGetCalibratedParams:
    def test_no_data_returns_none(self, cal_db):
        result = get_calibrated_food_params("001", "", cal_db)
        assert result is None

    def test_returns_saved_params(self, cal_db):
        conn = sqlite3.connect(cal_db)
        params = CalibrationParams(safety_days=0.35, gap_coefficient=0.3, waste_buffer=2)
        conn.execute(
            "INSERT INTO food_waste_calibration "
            "(store_id, mid_cd, calibration_date, actual_waste_rate, target_waste_rate, "
            "error, sample_days, current_params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("", "001", "2026-02-19", 0.25, 0.20, 0.05, 21, params.to_json(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        result = get_calibrated_food_params("001", "", cal_db)
        assert result is not None
        assert result.safety_days == 0.35
        assert result.gap_coefficient == 0.3
        assert result.waste_buffer == 2

    def test_effective_params_with_calibration(self, cal_db):
        """보정값이 있으면 보정값 반환"""
        conn = sqlite3.connect(cal_db)
        params = CalibrationParams(safety_days=0.4, gap_coefficient=0.35, waste_buffer=2)
        conn.execute(
            "INSERT INTO food_waste_calibration "
            "(store_id, mid_cd, calibration_date, actual_waste_rate, target_waste_rate, "
            "error, sample_days, current_params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("", "001", "2026-02-19", 0.22, 0.20, 0.02, 21, params.to_json(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        result = get_effective_params("001", "", cal_db)
        assert result.safety_days == 0.4

    def test_effective_params_without_calibration(self, cal_db):
        """보정값이 없으면 기본값 반환"""
        result = get_effective_params("001", "", cal_db)
        assert result.safety_days == 0.5  # 기본값


# =============================================================================
# 캘리브레이터 로직 테스트
# =============================================================================

class TestFoodWasteRateCalibrator:
    def test_calibrate_data_insufficient(self, cal_db):
        """데이터 부족 시 보정 안 함"""
        _insert_sales(cal_db, "001", 5, 10, 3)  # 5일 < 14일
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()
        assert result["calibrated"] is False

    def test_calibrate_within_deadband(self, cal_db):
        """불감대 내면 보정 안 함 (±2%p)"""
        # 목표 20%, 실제 ~21% → error = +1%p < deadband(2%p)
        _insert_sales(cal_db, "001", 21, 79, 21)  # 21/(79+21) = 21%
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert len(r001) == 1
        assert r001[0]["adjusted"] is False

    def test_calibrate_reduce_when_waste_high(self, cal_db):
        """폐기율이 높으면 safety_days 감소"""
        # 목표 20%, 실제 ~30% → error = +10%p > deadband
        _insert_sales(cal_db, "001", 21, 70, 30)  # 30/(70+30) = 30%
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert len(r001) == 1
        assert r001[0]["adjusted"] is True
        assert r001[0]["param_name"] == "safety_days"
        assert r001[0]["new_value"] < r001[0]["old_value"]

    def test_calibrate_increase_when_waste_low(self, cal_db):
        """폐기율이 낮으면 safety_days 증가"""
        # 목표 20%, 실제 ~5% → error = -15%p
        _insert_sales(cal_db, "001", 21, 95, 5)  # 5/(95+5) = 5%
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert len(r001) == 1
        assert r001[0]["adjusted"] is True
        assert r001[0]["param_name"] == "safety_days"
        assert r001[0]["new_value"] > r001[0]["old_value"]

    def test_calibrate_step_size_small(self, cal_db):
        """오차 작으면(2~5%p) 작은 step 사용"""
        # 목표 20%, 실제 ~24% → error = +4%p (small step)
        _insert_sales(cal_db, "001", 21, 76, 24)
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert r001[0]["adjusted"] is True
        old = r001[0]["old_value"]
        new = r001[0]["new_value"]
        assert abs(old - new) == pytest.approx(FOOD_WASTE_CAL_STEP_SMALL, abs=0.001)

    def test_calibrate_step_size_large(self, cal_db):
        """오차 크면(5%p+) 큰 step 사용"""
        # 목표 20%, 실제 ~35% → error = +15%p (large step)
        _insert_sales(cal_db, "001", 21, 65, 35)
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert r001[0]["adjusted"] is True
        old = r001[0]["old_value"]
        new = r001[0]["new_value"]
        assert abs(old - new) == pytest.approx(FOOD_WASTE_CAL_STEP_LARGE, abs=0.001)

    def test_calibrate_respects_range_floor(self, cal_db):
        """하한을 넘지 않음"""
        # safety_days를 현재 하한(ultra_short: 0.35)에, gap_coef도 하한(0.2)에 설정
        conn = sqlite3.connect(cal_db)
        params = CalibrationParams(safety_days=0.35, gap_coefficient=0.2, waste_buffer=1)
        conn.execute(
            "INSERT INTO food_waste_calibration "
            "(store_id, mid_cd, calibration_date, actual_waste_rate, target_waste_rate, "
            "error, sample_days, current_params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("", "001", "2026-02-18", 0.40, 0.20, 0.20, 21, params.to_json(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        # 여전히 폐기율 높지만 모든 파라미터가 하한에 도달
        _insert_sales(cal_db, "001", 21, 60, 40)
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert r001[0]["adjusted"] is False
        assert "at_limit" in r001[0].get("reason", "")

    def test_calibrate_saves_to_db(self, cal_db):
        """보정 결과가 DB에 저장됨"""
        _insert_sales(cal_db, "001", 21, 65, 35)
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        cal.calibrate()

        conn = sqlite3.connect(cal_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM food_waste_calibration WHERE mid_cd = '001'")
        count = cursor.fetchone()[0]
        conn.close()

        assert count >= 1

    def test_calibrate_multiple_categories(self, cal_db):
        """여러 카테고리가 독립적으로 보정됨"""
        _insert_sales(cal_db, "001", 21, 65, 35)  # 35% > 20% target
        _insert_sales(cal_db, "004", 21, 90, 10)  # 10% < 15% target
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        r004 = [r for r in result["results"] if r["mid_cd"] == "004"]

        if r001:
            # 001: 폐기율 높음 → 감소
            assert r001[0]["adjusted"] is True
            assert r001[0]["new_value"] < r001[0]["old_value"]

        if r004:
            # 004: 폐기율 낮음 → 증가
            assert r004[0]["adjusted"] is True
            assert r004[0]["new_value"] > r004[0]["old_value"]

    @patch("src.prediction.food_waste_calibrator.FOOD_WASTE_CAL_ENABLED", False)
    def test_calibrate_disabled(self, cal_db):
        """비활성화 시 보정 안 함"""
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()
        assert result["calibrated"] is False
        assert result["reason"] == "disabled"

    def test_no_orders_no_adjustment(self, cal_db):
        """발주 없으면 보정 안 함"""
        # daily_sales에 데이터 없음
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()
        assert result["calibrated"] is False

    def test_second_calibration_uses_previous_params(self, cal_db):
        """이전 보정값이 다음 보정의 시작점이 됨"""
        # 1회차: 높은 폐기율 → safety_days 감소
        _insert_sales(cal_db, "001", 21, 65, 35)
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result1 = cal.calibrate()

        r001 = [r for r in result1["results"] if r["mid_cd"] == "001"]
        first_new = r001[0]["new_value"]

        # 2회차: 다음 날짜로 다시 보정
        cal2 = FoodWasteRateCalibrator(db_path=cal_db)
        result2 = cal2.calibrate()

        r001_2 = [r for r in result2["results"] if r["mid_cd"] == "001"]
        if r001_2 and r001_2[0]["adjusted"]:
            # 이전 보정값(first_new)에서 시작해서 추가 감소
            assert r001_2[0]["old_value"] == pytest.approx(first_new, abs=0.001)


# =============================================================================
# 엣지 케이스
# =============================================================================

class TestEdgeCases:
    def test_calibrate_with_zero_waste(self, cal_db):
        """폐기 0%인 경우 → 발주 늘리기"""
        _insert_sales(cal_db, "001", 21, 100, 0)  # 0% waste
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert r001[0]["adjusted"] is True
        assert r001[0]["new_value"] > r001[0]["old_value"]

    def test_calibrate_with_100_percent_waste(self, cal_db):
        """폐기 100%인 경우 → 발주 줄이기"""
        _insert_sales(cal_db, "001", 21, 0, 100)  # 100% waste
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()

        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        assert r001[0]["adjusted"] is True
        assert r001[0]["new_value"] < r001[0]["old_value"]

    def test_nonexistent_db(self):
        """DB 파일 없으면 에러 없이 반환"""
        cal = FoodWasteRateCalibrator(db_path="/nonexistent/path.db")
        result = cal.calibrate()
        assert result["calibrated"] is False

    def test_different_targets_per_category(self, cal_db):
        """카테고리별 다른 목표 폐기율 적용 확인"""
        target_001 = FOOD_WASTE_RATE_TARGETS.get("001", 0.20)
        target_012 = FOOD_WASTE_RATE_TARGETS.get("012", 0.12)
        assert target_001 != target_012
        assert target_001 == 0.20
        assert target_012 == 0.12


# =============================================================================
# 히스테리시스 테스트
# =============================================================================

class TestHysteresis:
    def test_first_calibration_allowed(self, cal_db):
        """첫 보정은 이력 없으므로 항상 허용"""
        _insert_sales(cal_db, "001", 21, 80, 20)  # 20% waste (= target)
        # 폐기율이 정확히 target이면 deadband → 조정 안됨
        # 폐기율을 target보다 높게 설정: 30%
        _insert_sales(cal_db, "003", 21, 70, 30)  # ~30% waste
        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()
        r003 = [r for r in result["results"] if r["mid_cd"] == "003"]
        # 첫 보정 → 히스테리시스 통과, 조정됨
        if r003:
            assert r003[0]["reason"] != "hysteresis"

    def test_consistent_direction_allowed(self, cal_db):
        """같은 방향 연속이면 조정 허용"""
        _insert_sales(cal_db, "001", 21, 70, 30)  # ~30% waste > target 20%

        # 이전 보정 이력에 양수 error 삽입 (같은 방향)
        conn = sqlite3.connect(cal_db)
        conn.execute(
            "INSERT INTO food_waste_calibration "
            "(store_id, mid_cd, calibration_date, actual_waste_rate, target_waste_rate, "
            "error, sample_days, current_params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("", "001", "2026-02-18", 0.28, 0.20, 0.08, 21,
             CalibrationParams(0.5, 0.4, 3).to_json(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()
        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        if r001:
            assert "hysteresis" not in r001[0].get("reason", "")

    def test_opposite_direction_blocked(self, cal_db):
        """반대 방향이면 조정 차단"""
        _insert_sales(cal_db, "001", 21, 70, 30)  # ~30% waste > target 20% → error > 0

        # 이전 보정 이력에 음수 error 삽입 (반대 방향)
        conn = sqlite3.connect(cal_db)
        conn.execute(
            "INSERT INTO food_waste_calibration "
            "(store_id, mid_cd, calibration_date, actual_waste_rate, target_waste_rate, "
            "error, sample_days, current_params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("", "001", "2026-02-18", 0.15, 0.20, -0.05, 21,
             CalibrationParams(0.5, 0.4, 3).to_json(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        cal = FoodWasteRateCalibrator(db_path=cal_db)
        result = cal.calibrate()
        r001 = [r for r in result["results"] if r["mid_cd"] == "001"]
        if r001:
            assert "hysteresis" in r001[0].get("reason", "")


# =============================================================================
# 푸드 요일 계수 / 날씨 교차 효과 / 시간대별 배송 비율 테스트
# =============================================================================

class TestFoodUpgrades:
    def test_food_weekday_coefficient_no_data(self):
        """데이터 없으면 1.0 반환"""
        from src.prediction.categories.food import get_food_weekday_coefficient
        result = get_food_weekday_coefficient("001", 1, db_path="/nonexistent/path.db")
        assert result == 1.0

    def test_food_weekday_coefficient_non_food(self):
        """비푸드 카테고리는 1.0 반환"""
        from src.prediction.categories.food import get_food_weekday_coefficient
        result = get_food_weekday_coefficient("049", 1)  # 맥주
        assert result == 1.0

    def test_food_weekday_coefficient_with_data(self, cal_db):
        """충분한 데이터가 있으면 실제 계수 계산"""
        from src.prediction.categories.food import get_food_weekday_coefficient
        conn = sqlite3.connect(cal_db)
        # 4주간 요일별 데이터 삽입 (월~일 각각 다른 판매량)
        base_date = datetime.now()
        for week in range(4):
            for wd in range(7):
                d = base_date - timedelta(days=week * 7 + (base_date.weekday() - wd) % 7)
                sale_qty = 10 + wd * 2  # 일=10, 월=12, ..., 토=22
                conn.execute(
                    "INSERT INTO daily_sales (store_id, item_cd, mid_cd, sales_date, sale_qty, disuse_qty) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("", f"item_{wd}_{week}", "002", d.strftime("%Y-%m-%d"), sale_qty, 0),
                )
        conn.commit()
        conn.close()

        # 토요일 (wd=6, SQLite strftime %w: 6=토) 판매 높음 → 계수 > 1.0
        coef = get_food_weekday_coefficient("002", 6, db_path=cal_db)
        assert coef >= 1.0

    def test_food_weather_cross_coefficient(self):
        """기온별 교차 효과 확인"""
        from src.prediction.categories.food import get_food_weather_cross_coefficient

        # 혹한기: 도시락 +10%
        assert get_food_weather_cross_coefficient("001", -5) == 1.10
        # 혹한기: 김밥 -10%
        assert get_food_weather_cross_coefficient("003", -5) == 0.90
        # 적온기: 모두 1.0
        assert get_food_weather_cross_coefficient("001", 20) == 1.0
        assert get_food_weather_cross_coefficient("003", 20) == 1.0
        # 폭염기: 도시락 -12%
        assert get_food_weather_cross_coefficient("001", 35) == 0.88
        # None → 1.0
        assert get_food_weather_cross_coefficient("001", None) == 1.0
        # 비푸드 → 1.0
        assert get_food_weather_cross_coefficient("049", -5) == 1.0

    def test_delivery_time_demand_ratio(self):
        """배송 차수별 시간대 수요 비율 상수 확인"""
        from src.prediction.categories.food import DELIVERY_TIME_DEMAND_RATIO
        assert DELIVERY_TIME_DEMAND_RATIO["1차"] == 0.70
        assert DELIVERY_TIME_DEMAND_RATIO["2차"] == 1.00

    def test_disuse_coef_continuous_formula(self):
        """calculate_food_dynamic_safety에서 연속 공식 사용 확인"""
        from src.prediction.categories.food import calculate_food_dynamic_safety
        with patch("src.prediction.categories.food.analyze_food_expiry_pattern") as mock_pattern:
            from src.prediction.categories.food import FoodExpiryResult, FOOD_EXPIRY_SAFETY_CONFIG
            mock_pattern.return_value = FoodExpiryResult(
                item_cd="test",
                mid_cd="001",
                expiration_days=1,
                expiry_group="ultra_short",
                safety_days=0.5,
                data_source="fallback",
                daily_avg=10.0,
                actual_data_days=30,
                safety_stock=5.0,
                description="test",
            )
            # disuse_rate=0.2 → max(0.65, 1.0 - 0.2*1.2) = 0.76
            safety, _ = calculate_food_dynamic_safety("test", "001", daily_avg=10.0, disuse_rate=0.2)
            assert safety == round(5.0 * 0.76, 2)  # 3.8

    def test_new_item_daily_avg_calculation(self, cal_db):
        """신규 상품(데이터 7일 미만) 일평균은 actual_data_days로 나눔"""
        from src.prediction.categories.food import analyze_food_expiry_pattern
        conn = sqlite3.connect(cal_db)

        # product_details 테이블 (유통기한 조회용)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_details (
                item_cd TEXT PRIMARY KEY,
                expiration_days INTEGER
            )
        """)
        conn.execute("INSERT INTO product_details VALUES ('NEW001', 1)")

        # 3일 데이터만 삽입 (총 판매 30개 = 일평균 10개)
        today = datetime.now()
        for i in range(3):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_sales (store_id, item_cd, mid_cd, sales_date, sale_qty, disuse_qty) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("", "NEW001", "001", d, 10, 0),
            )
        conn.commit()
        conn.close()

        result = analyze_food_expiry_pattern("NEW001", "001", db_path=cal_db)
        # 신규 상품: 30 / 3 = 10.0 (7일 미만이므로 actual_data_days로 나눔)
        assert result.daily_avg == 10.0
        assert result.actual_data_days == 3
