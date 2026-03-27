"""
food-ml-dual-model PDCA 테스트
- 그룹 컨텍스트 피처 4개 (data_days_ratio, smallcd_peer_avg, relative_position, lifecycle_stage)
- 그룹 모델 (predict_group, predict_dual)
- 그룹 모델 학습 (train_group_models)
- 데이터 파이프라인 (get_smallcd_peer_avg_batch, get_item_smallcd_map, get_lifecycle_stages_batch)
- improved_predictor 통합
"""

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _MockMLModel:
    """mock 모델 (모듈 레벨 → joblib 직렬화 가능)"""
    def __init__(self, value=5.0):
        self.value = value

    def predict(self, X):
        return np.ones(len(X)) * self.value


def _make_daily_sales(days=14, avg_qty=5):
    today = datetime.now()
    return [
        {
            "sales_date": (today - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d"),
            "sale_qty": max(1, int(avg_qty * (1.0 + (i % 3 - 1) * 0.1))),
        }
        for i in range(days)
    ]


# ═══════════════════════════════════════════
# 1. 그룹 컨텍스트 피처 테스트
# ═══════════════════════════════════════════

class TestGroupContextFeatures:
    """FEATURE_NAMES 35개 + 새 피처 4개 검증"""

    def test_feature_names_count_35(self):
        """FEATURE_NAMES가 35개"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        assert len(MLFeatureBuilder.FEATURE_NAMES) == 45

    def test_new_features_in_names(self):
        """새 4개 피처 이름 존재"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        names = MLFeatureBuilder.FEATURE_NAMES
        assert "data_days_ratio" in names
        assert "smallcd_peer_avg" in names
        assert "relative_position" in names
        assert "lifecycle_stage" in names

    def test_data_days_ratio_computation(self):
        """data_days_ratio = min(data_days/60, 1.0)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        features = MLFeatureBuilder.build_features(
            daily_sales=_make_daily_sales(14),
            target_date=datetime.now().strftime("%Y-%m-%d"),
            mid_cd="001",
            data_days=30,
        )
        assert features is not None
        # index 31: data_days_ratio = 30/60 = 0.5
        assert abs(features[31] - 0.5) < 0.01

    def test_data_days_ratio_cap(self):
        """data_days=120 → ratio = 1.0 (cap)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        features = MLFeatureBuilder.build_features(
            daily_sales=_make_daily_sales(14),
            target_date=datetime.now().strftime("%Y-%m-%d"),
            mid_cd="001",
            data_days=120,
        )
        assert features is not None
        assert features[31] == pytest.approx(1.0)

    def test_smallcd_peer_avg_normalization(self):
        """smallcd_peer_avg 정규화: /10, cap 1.0"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        features = MLFeatureBuilder.build_features(
            daily_sales=_make_daily_sales(14),
            target_date=datetime.now().strftime("%Y-%m-%d"),
            mid_cd="001",
            smallcd_peer_avg=7.5,
        )
        assert features is not None
        # index 32: 7.5/10 = 0.75
        assert abs(features[32] - 0.75) < 0.01

    def test_relative_position_computation(self):
        """relative_position = daily_avg_7 / peer_avg (cap 3.0)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        features = MLFeatureBuilder.build_features(
            daily_sales=_make_daily_sales(14, avg_qty=10),
            target_date=datetime.now().strftime("%Y-%m-%d"),
            mid_cd="001",
            smallcd_peer_avg=5.0,
        )
        assert features is not None
        # index 33: ~10/5 = ~2.0 (avg_qty 변동이 있으므로 approx)
        assert 1.0 < features[33] <= 3.0

    def test_relative_position_zero_peer(self):
        """peer_avg=0이면 relative_position=0"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        features = MLFeatureBuilder.build_features(
            daily_sales=_make_daily_sales(14),
            target_date=datetime.now().strftime("%Y-%m-%d"),
            mid_cd="001",
            smallcd_peer_avg=0.0,
        )
        assert features is not None
        assert features[33] == 0.0

    def test_lifecycle_stage_values(self):
        """lifecycle_stage 값 범위"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        for stage_val in [0.0, 0.25, 0.5, 0.75, 1.0]:
            features = MLFeatureBuilder.build_features(
                daily_sales=_make_daily_sales(14),
                target_date=datetime.now().strftime("%Y-%m-%d"),
                mid_cd="001",
                lifecycle_stage=stage_val,
            )
            assert features is not None
            assert features[34] == pytest.approx(stage_val)

    def test_batch_features_with_new_params(self):
        """build_batch_features에서 새 파라미터 전달"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        items_data = [{
            "daily_sales": _make_daily_sales(14),
            "target_date": datetime.now().strftime("%Y-%m-%d"),
            "mid_cd": "001",
            "actual_sale_qty": 5,
            "data_days": 45,
            "smallcd_peer_avg": 6.0,
            "lifecycle_stage": 0.75,
        }]
        X, y, codes = MLFeatureBuilder.build_batch_features(items_data)
        assert X is not None
        assert X.shape == (1, 45)
        # data_days_ratio = 45/60 = 0.75
        assert abs(X[0, 31] - 0.75) < 0.01


# ═══════════════════════════════════════════
# 2. 그룹 모델 테스트 (model.py)
# ═══════════════════════════════════════════

class TestGroupModel:
    """predict_group, predict_dual, has_group_model 테스트"""

    def test_predict_group_small_cd(self, tmp_path):
        """small_cd 그룹 모델 예측"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.group_models["small_001A"] = _MockMLModel(3.0)
        features = np.random.rand(39).astype(np.float32)
        result = predictor.predict_group(features, "001A")
        assert result == pytest.approx(3.0)

    def test_predict_group_mid_fallback(self, tmp_path):
        """small_cd 모델 없으면 mid_cd 폴백"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.group_models["mid_001"] = _MockMLModel(4.0)
        features = np.random.rand(39).astype(np.float32)
        result = predictor.predict_group(features, "001Z")  # small 없음
        assert result == pytest.approx(4.0)

    def test_predict_group_no_model(self, tmp_path):
        """그룹 모델 없으면 None"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        features = np.random.rand(39).astype(np.float32)
        result = predictor.predict_group(features, "999X")
        assert result is None

    def test_predict_dual_blending(self, tmp_path):
        """predict_dual: data_confidence 기반 블렌딩"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.models["food_group"] = _MockMLModel(10.0)
        predictor.group_models["small_001A"] = _MockMLModel(6.0)
        predictor._loaded = True
        features = np.random.rand(39).astype(np.float32)

        # data_days=30 → confidence=0.5
        result = predictor.predict_dual(features, "001", "001A", data_days=30)
        assert result is not None
        expected = 0.5 * 10.0 + 0.5 * 6.0  # 8.0
        assert result == pytest.approx(expected)

    def test_predict_dual_zero_data_days(self, tmp_path):
        """data_days=0 → 100% 그룹 모델"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.models["food_group"] = _MockMLModel(10.0)
        predictor.group_models["small_001A"] = _MockMLModel(6.0)
        predictor._loaded = True
        features = np.random.rand(39).astype(np.float32)

        result = predictor.predict_dual(features, "001", "001A", data_days=0)
        assert result == pytest.approx(6.0)

    def test_predict_dual_full_confidence(self, tmp_path):
        """data_days>=60 → 100% 개별 모델"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.models["food_group"] = _MockMLModel(10.0)
        predictor.group_models["small_001A"] = _MockMLModel(6.0)
        predictor._loaded = True
        features = np.random.rand(39).astype(np.float32)

        result = predictor.predict_dual(features, "001", "001A", data_days=90)
        assert result == pytest.approx(10.0)

    def test_predict_dual_no_group(self, tmp_path):
        """그룹 모델 없으면 개별 모델만"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.models["food_group"] = _MockMLModel(8.0)
        predictor._loaded = True
        features = np.random.rand(39).astype(np.float32)

        result = predictor.predict_dual(features, "001", "999X", data_days=0)
        assert result == pytest.approx(8.0)

    def test_predict_dual_no_individual(self, tmp_path):
        """개별 모델 없으면 그룹만"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor.group_models["small_999A"] = _MockMLModel(5.0)
        predictor._loaded = True
        features = np.random.rand(39).astype(np.float32)

        result = predictor.predict_dual(features, "999", "999A", data_days=10)
        assert result == pytest.approx(5.0)

    def test_has_group_model(self, tmp_path):
        """has_group_model 정상 동작"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        predictor._group_loaded = True
        predictor.group_models["small_001A"] = _MockMLModel()
        assert predictor.has_group_model("001A") is True
        assert predictor.has_group_model("999Z") is False

    def test_save_and_load_group_model(self, tmp_path):
        """그룹 모델 저장/로드"""
        from src.prediction.ml.model import MLPredictor
        predictor = MLPredictor(model_dir=str(tmp_path))
        success = predictor.save_group_model("small_001A", _MockMLModel(7.0))
        assert success is True
        assert (tmp_path / "group_small_001A.joblib").exists()

        # 새 인스턴스에서 로드
        predictor2 = MLPredictor(model_dir=str(tmp_path))
        loaded = predictor2.load_group_models()
        assert loaded >= 1
        assert "small_001A" in predictor2.group_models


# ═══════════════════════════════════════════
# 3. 데이터 파이프라인 테스트
# ═══════════════════════════════════════════

class TestDataPipelineDualModel:
    """get_smallcd_peer_avg_batch, get_item_smallcd_map, get_lifecycle_stages_batch"""

    def _create_test_db(self, tmp_path):
        """테스트 DB 생성 (단일 DB 모드)"""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS daily_sales (item_cd TEXT, mid_cd TEXT, sales_date TEXT, sale_qty INTEGER, disuse_qty INTEGER DEFAULT 0, stock_qty INTEGER DEFAULT 0, store_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS products (item_cd TEXT PRIMARY KEY, item_nm TEXT, mid_cd TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS product_details (item_cd TEXT PRIMARY KEY, mid_cd TEXT, small_cd TEXT, large_cd TEXT, expiration_days INTEGER, margin_rate REAL, order_unit_qty INTEGER DEFAULT 1)")
        conn.execute("CREATE TABLE IF NOT EXISTS detected_new_products (item_cd TEXT PRIMARY KEY, lifecycle_status TEXT)")

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # 상품 등록
        conn.execute("INSERT INTO products VALUES ('ITEM1', '테스트1', '001')")
        conn.execute("INSERT INTO products VALUES ('ITEM2', '테스트2', '001')")
        conn.execute("INSERT INTO product_details (item_cd, mid_cd, small_cd, large_cd) VALUES ('ITEM1', '001', '001A', '01')")
        conn.execute("INSERT INTO product_details (item_cd, mid_cd, small_cd, large_cd) VALUES ('ITEM2', '001', '001A', '01')")

        # 매출 데이터
        conn.execute("INSERT INTO daily_sales VALUES ('ITEM1', '001', ?, 5, 0, 10, NULL)", (today,))
        conn.execute("INSERT INTO daily_sales VALUES ('ITEM1', '001', ?, 3, 0, 10, NULL)", (yesterday,))
        conn.execute("INSERT INTO daily_sales VALUES ('ITEM2', '001', ?, 8, 0, 15, NULL)", (today,))

        # 라이프사이클
        conn.execute("INSERT INTO detected_new_products VALUES ('ITEM1', 'monitoring')")
        conn.execute("INSERT INTO detected_new_products VALUES ('ITEM2', 'stable')")

        conn.commit()
        conn.close()
        return db_path

    def test_get_item_smallcd_map(self, tmp_path):
        """item_cd → small_cd 매핑"""
        from src.prediction.ml.data_pipeline import MLDataPipeline
        db_path = self._create_test_db(tmp_path)
        pipeline = MLDataPipeline(db_path=db_path)
        result = pipeline.get_item_smallcd_map()
        assert result["ITEM1"] == "001A"
        assert result["ITEM2"] == "001A"

    def test_get_smallcd_peer_avg_batch(self, tmp_path):
        """small_cd별 7일 평균"""
        from src.prediction.ml.data_pipeline import MLDataPipeline
        db_path = self._create_test_db(tmp_path)
        pipeline = MLDataPipeline(db_path=db_path)
        result = pipeline.get_smallcd_peer_avg_batch()
        assert "001A" in result
        assert result["001A"] > 0

    def test_get_lifecycle_stages_batch(self, tmp_path):
        """라이프사이클 단계 매핑"""
        from src.prediction.ml.data_pipeline import MLDataPipeline
        db_path = self._create_test_db(tmp_path)
        pipeline = MLDataPipeline(db_path=db_path)
        result = pipeline.get_lifecycle_stages_batch()
        assert result["ITEM1"] == 0.25  # monitoring
        assert result["ITEM2"] == 0.75  # stable


# ═══════════════════════════════════════════
# 4. Trainer 그룹 학습 테스트
# ═══════════════════════════════════════════

class TestTrainerGroupModels:
    """train_group_models 테스트"""

    def test_group_min_samples_constant(self):
        """GROUP_MIN_SAMPLES=30"""
        from src.prediction.ml.trainer import GROUP_MIN_SAMPLES
        assert GROUP_MIN_SAMPLES == 30

    def test_group_training_days_food(self):
        """food_group 학습 기간 30일"""
        from src.prediction.ml.trainer import GROUP_TRAINING_DAYS
        assert GROUP_TRAINING_DAYS.get("food_group") == 30

    def test_train_group_models_structure(self):
        """train_group_models 메서드 존재 + 반환 타입"""
        from src.prediction.ml.trainer import MLTrainer
        assert hasattr(MLTrainer, "train_group_models")


# ═══════════════════════════════════════════
# 5. improved_predictor 통합 테스트
# ═══════════════════════════════════════════

class TestImprovedPredictorDualModel:
    """improved_predictor에서 dual model 호출 통합"""

    def _make_predictor(self):
        from src.prediction.improved_predictor import ImprovedPredictor
        with patch.object(ImprovedPredictor, '__init__', lambda self, *a, **kw: None):
            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._ml_predictor = MagicMock()
            p._association_adjuster = None
            p._promo_adjuster = None
            p._receiving_stats_cache = {}
            p._smallcd_peer_cache = {}
            p._item_smallcd_map = {}
            p._lifecycle_cache = {}
            p.store_id = "46704"
            p.db_path = ":memory:"
            return p

    def test_food_group_model_fallback_weight(self):
        """food + group model 있으면 data_days<30에도 weight=0.05 (ml-weight-adjust)"""
        p = self._make_predictor()
        p._item_smallcd_map = {"ITEM1": "001A"}
        p._ml_predictor.has_group_model.return_value = True
        p._ml_predictor._load_meta.return_value = {}
        w = p._get_ml_weight("001", data_days=15, item_cd="ITEM1")
        assert w == 0.05

    def test_non_food_no_group_fallback(self):
        """비푸드 + data_days<30 → weight=0.0"""
        p = self._make_predictor()
        p._ml_predictor._load_meta.return_value = {}
        w = p._get_ml_weight("049", data_days=15)
        assert w == 0.0
