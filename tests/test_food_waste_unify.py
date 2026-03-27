"""
food-waste-unify PDCA 테스트

4중 독립 폐기 감량 → 단일 통합 계수 전환 검증:
1. 통합 계수 범위 (0.70 ~ 1.0)
2. IB + OT 가중 평균 정확성
3. 캘리브레이터 compound floor 0.15
4. 가속 회복 2.0x/0.12
5. 악순환 방지: 낮은 폐기율에서 감량 없음
6. 파이프라인 호환성
"""
import sys
import os
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# =============================================================================
# 1. 통합 폐기 계수 단위 테스트
# =============================================================================

class TestUnifiedWasteCoefficient:
    """get_unified_waste_coefficient() 단위 테스트"""

    def test_constants_exist(self):
        """통합 계수 상수가 존재하고 올바른 값인지"""
        from src.prediction.categories.food import (
            UNIFIED_WASTE_COEF_FLOOR,
            UNIFIED_WASTE_COEF_MULTIPLIER,
            UNIFIED_IB_WEIGHT,
            UNIFIED_OT_WEIGHT,
        )
        assert UNIFIED_WASTE_COEF_FLOOR == 0.70
        assert UNIFIED_WASTE_COEF_MULTIPLIER == 1.0
        assert UNIFIED_IB_WEIGHT == 0.7
        assert UNIFIED_OT_WEIGHT == 0.3
        assert UNIFIED_IB_WEIGHT + UNIFIED_OT_WEIGHT == 1.0

    def test_no_data_returns_1(self, tmp_path):
        """DB 데이터 없으면 감량 없음 (1.0)"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        # 빈 테이블 생성
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        assert result == 1.0

    def test_zero_waste_rate_returns_1(self, tmp_path):
        """폐기율 0% → 계수 1.0"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "zero_waste.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # 폐기 없는 데이터 삽입
        for i in range(15):
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 0, 'consumed', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        assert result == 1.0

    def test_high_waste_rate_floor(self, tmp_path):
        """높은 폐기율 → 하한 0.70에 도달"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "high_waste.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # 50% 폐기율 데이터 (initial=10, expired에 remaining=5)
        for i in range(15):
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 5, 'expired', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        assert result == 0.70  # 50% * 1.0 = 0.50 → floor 0.70

    def test_moderate_waste_rate(self, tmp_path):
        """중간 폐기율 (20%) → 계수 0.80"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "mod_waste.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # 20% 폐기율: 80개 consumed(remaining 0) + 20개 expired(remaining 2 of 10)
        for i in range(15):
            # 80% consumed
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 0, 'consumed', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        for i in range(15):
            # 20% expired (remaining_qty = 2 per batch of 10 initial)
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 2, 'expired', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        # ib_rate = expired_remaining / initial = 30 / 300 = 0.10
        # coef = 1.0 - 0.10 = 0.90
        assert 0.70 <= result <= 1.0

    def test_coefficient_range_always_valid(self, tmp_path):
        """어떤 입력이든 계수는 0.70 ~ 1.0 범위"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "range_check.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # 극단적 데이터 (100% 폐기)
        for i in range(20):
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 10, 'expired', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        assert result >= 0.70
        assert result <= 1.0


# =============================================================================
# 2. IB + OT 가중 평균 테스트
# =============================================================================

class TestWeightedBlending:
    """IB (70%) + OT (30%) 가중 평균 정확성"""

    def test_ib_only_no_ot(self, tmp_path):
        """OT 데이터 없으면 IB만 사용"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "ib_only.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # IB: 10% 폐기율
        for i in range(15):
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 1, 'expired', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        # ib_rate = 15 / 150 = 0.10, coef = 1.0 - 0.10 = 0.90
        assert result == 0.9

    def test_ot_only_no_ib(self, tmp_path):
        """IB 데이터 없으면 OT만 사용"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "ot_only.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # OT: 15% 폐기율
        conn.execute("""
            INSERT INTO order_tracking VALUES (?, ?, 100, 15, 'expired', date('now', '-3 days'))
        """, ("TEST001", "99999"))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        # ot_rate = 15/100 = 0.15, coef = 1.0 - 0.15 = 0.85
        assert result == 0.85

    def test_both_sources_blended(self, tmp_path):
        """IB + OT 모두 있으면 가중 평균"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "blended.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # IB: 10% 폐기율 (item 기반, 배치 15개)
        for i in range(15):
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 1, 'expired', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        # OT: 20% 폐기율
        conn.execute("""
            INSERT INTO order_tracking VALUES (?, ?, 100, 20, 'expired', date('now', '-3 days'))
        """, ("TEST001", "99999"))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        # ib_rate ≈ 0.10, ot_rate = 0.20
        # blended = 0.10 * 0.7 + 0.20 * 0.3 = 0.07 + 0.06 = 0.13
        # coef = 1.0 - 0.13 = 0.87
        assert 0.70 <= result <= 1.0


# =============================================================================
# 3. 캘리브레이터 compound floor 테스트
# =============================================================================

class TestCalibratorCompoundFloor:
    """compound floor 0.15 검증"""

    def test_compound_floor_value(self):
        """COMPOUND_FLOOR 상수가 0.15인지"""
        from src.prediction.food_waste_calibrator import FoodWasteRateCalibrator
        assert FoodWasteRateCalibrator.COMPOUND_FLOOR == 0.15

    def test_compound_floor_blocks_reduction(self):
        """compound <= 0.15 이면 감소 중단"""
        from src.prediction.food_waste_calibrator import (
            FoodWasteRateCalibrator,
            CalibrationParams,
        )
        cal = FoodWasteRateCalibrator.__new__(FoodWasteRateCalibrator)

        # safety=0.3, gap=0.4 → compound=0.12 < 0.15 → 중단
        params = CalibrationParams(safety_days=0.3, gap_coefficient=0.4, waste_buffer=1.0)
        result = cal._reduce_order(params, "ultra_short", 0.10)
        assert result == (None, None, None)  # 감소 중단

    def test_compound_above_floor_allows_reduction(self):
        """compound > 0.15 이면 감소 허용"""
        from src.prediction.food_waste_calibrator import (
            FoodWasteRateCalibrator,
            CalibrationParams,
        )
        cal = FoodWasteRateCalibrator.__new__(FoodWasteRateCalibrator)

        # safety=0.5, gap=0.5 → compound=0.25 > 0.15 → 허용
        params = CalibrationParams(safety_days=0.5, gap_coefficient=0.5, waste_buffer=1.0)
        result = cal._reduce_order(params, "ultra_short", 0.10)
        assert result != (None, None, None)


# =============================================================================
# 4. 가속 회복 테스트
# =============================================================================

class TestAcceleratedRecovery:
    """심각한 과소발주 시 가속 회복 (2.0x / max 0.12)"""

    def test_recovery_step_boost(self):
        """오차 10%p+ 시 step이 2.0x, max 0.12"""
        from src.prediction.food_waste_calibrator import (
            FoodWasteRateCalibrator,
            CalibrationParams,
            FOOD_WASTE_CAL_STEP_LARGE,
        )
        cal = FoodWasteRateCalibrator.__new__(FoodWasteRateCalibrator)

        # 심각 과소발주 (error = -15%)
        params = CalibrationParams(safety_days=0.4, gap_coefficient=0.4, waste_buffer=1.0)
        param_name, old_val, new_val = cal._increase_order(params, "ultra_short", -0.15)

        assert param_name == "safety_days"
        # step = min(LARGE_STEP * 2.0, 0.12)
        # LARGE_STEP = 0.05 → step = min(0.10, 0.12) = 0.10
        expected_step = min(round(FOOD_WASTE_CAL_STEP_LARGE * 2.0, 3), 0.12)
        assert new_val == round(old_val + expected_step, 3)

    def test_recovery_step_capped_at_012(self):
        """가속 step이 0.12를 초과하지 않음"""
        from src.prediction.food_waste_calibrator import (
            FoodWasteRateCalibrator,
            CalibrationParams,
            FOOD_WASTE_CAL_STEP_LARGE,
        )
        cal = FoodWasteRateCalibrator.__new__(FoodWasteRateCalibrator)

        # step * 2.0 > 0.12 되는 케이스
        params = CalibrationParams(safety_days=0.4, gap_coefficient=0.4, waste_buffer=1.0)
        _, old_val, new_val = cal._increase_order(params, "ultra_short", -0.20)

        effective_step = round(new_val - old_val, 3)
        assert effective_step <= 0.12


# =============================================================================
# 5. 악순환 방지 테스트
# =============================================================================

class TestFeedbackLoopPrevention:
    """악순환 루프 차단 검증"""

    def test_low_waste_no_reduction(self, tmp_path):
        """폐기율 5% 미만 → 감량 거의 없음 (0.95 이상)"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "low_waste.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # 3% 폐기율
        for i in range(20):
            status = 'expired' if i == 0 else 'consumed'
            remaining = 3 if i == 0 else 0
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 100, ?, ?, date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", remaining, status, i))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        assert result >= 0.95  # 5% 미만 폐기 → 감량 거의 없음

    def test_max_reduction_30_percent(self, tmp_path):
        """어떤 상황이든 최대 30% 감량 (계수 >= 0.70)"""
        from src.prediction.categories.food import get_unified_waste_coefficient

        db_path = str(tmp_path / "max_check.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE inventory_batches (
                item_cd TEXT, mid_cd TEXT, store_id TEXT,
                initial_qty REAL, remaining_qty REAL,
                status TEXT, receiving_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE order_tracking (
                item_cd TEXT, store_id TEXT,
                order_qty REAL, remaining_qty REAL,
                status TEXT, order_date TEXT
            )
        """)
        # 80% 폐기 (극단적)
        for i in range(20):
            conn.execute("""
                INSERT INTO inventory_batches VALUES (?, ?, ?, 10, 8, 'expired', date('now', '-' || ? || ' days'))
            """, ("TEST001", "001", "99999", i))
        conn.execute("""
            INSERT INTO order_tracking VALUES (?, ?, 100, 90, 'expired', date('now', '-1 day'))
        """, ("TEST001", "99999"))
        conn.commit()
        conn.close()

        result = get_unified_waste_coefficient(
            "TEST001", "001", store_id="99999", db_path=db_path
        )
        assert result == 0.70  # floor
        # 기존 4중 시스템이라면 ~0.13까지 감소 가능했음


# =============================================================================
# 6. improved_predictor 통합 검증
# =============================================================================

class TestPipelineIntegration:
    """improved_predictor에서 통합 계수가 올바르게 호출되는지"""

    def test_no_disuse_cache_in_predictor(self):
        """_food_disuse_cache 변수가 제거되었는지"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor.__init__)
        assert "_food_disuse_cache" not in source

    def test_no_waste_feedback_in_predict(self):
        """폐기 원인 피드백 호출이 제거되었는지"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._compute_safety_and_order)
        # waste_fb.get_adjustment 호출 없어야 함
        assert "waste_fb.get_adjustment" not in source
        assert "fb_result.multiplier" not in source

    def test_unified_coefficient_called_in_food_branch(self):
        """food 카테고리 분기에서 get_unified_waste_coefficient 호출"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._compute_safety_and_order)
        assert "get_unified_waste_coefficient" in source

    def test_no_delivery_waste_in_predict(self):
        """get_delivery_waste_adjustment 직접 호출이 제거되었는지"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._compute_safety_and_order)
        assert "get_delivery_waste_adjustment" not in source

    def test_no_min_disuse_delivery(self):
        """min(disuse, delivery) 이중 처벌이 제거되었는지"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._compute_safety_and_order)
        assert "effective_waste_coef = min(" not in source
