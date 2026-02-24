"""ML 예측 모듈 테스트

- Feature 빌드 (MLFeatureBuilder)
- 모델 저장/로드 (MLPredictor)
- 앙상블 비율 검증
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _MockMLModel:
    """joblib 직렬화 가능한 mock 모델 (모듈 레벨)"""
    def __init__(self, value=5.0):
        self.value = value

    def predict(self, X):
        return np.ones(len(X)) * self.value


def _make_daily_sales(days: int = 14, avg_qty: int = 5):
    """테스트용 일별 판매 데이터 생성"""
    from datetime import datetime, timedelta

    today = datetime.now()
    sales = []
    for i in range(days):
        date = (today - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        weekday = (today - timedelta(days=days - 1 - i)).weekday()
        variation = 1.0 + (weekday - 3) * 0.1
        qty = max(1, int(avg_qty * variation))
        sales.append({
            "sales_date": date,
            "sale_qty": qty,
            "stock_qty": avg_qty * 2,
        })
    return sales


class TestMLFeatureBuilder:
    """Feature Builder 테스트"""

    def test_build_features_normal(self):
        """정상 데이터로 18개 feature 생성"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = _make_daily_sales(14, 5)
        target_date = daily_sales[-1]["sales_date"]

        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=target_date,
            mid_cd="049",
            stock_qty=10,
            pending_qty=2,
            promo_active=False,
        )

        assert features is not None
        assert isinstance(features, np.ndarray)
        assert features.shape == (36,)
        assert not np.any(np.isnan(features))

    def test_build_features_insufficient_data(self):
        """데이터 부족 시 None (최소 3일 필요)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = _make_daily_sales(2, 5)  # 2일 < 최소 3일
        target_date = daily_sales[-1]["sales_date"]

        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=target_date,
            mid_cd="049",
        )

        assert features is None

    def test_feature_names_count(self):
        """FEATURE_NAMES 목록 개수 확인 (31+입고패턴5=36)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        assert len(MLFeatureBuilder.FEATURE_NAMES) == 36

    def test_category_one_hot_encoding(self):
        """카테고리 그룹별 원핫 인코딩 검증"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        daily_sales = _make_daily_sales(14, 5)
        target_date = daily_sales[-1]["sales_date"]

        # 맥주 (alcohol_group)
        features_beer = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=target_date,
            mid_cd="049",
        )

        # 담배 (tobacco_group)
        features_tobacco = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date=target_date,
            mid_cd="072",
        )

        assert features_beer is not None
        assert features_tobacco is not None

        # 원핫 인코딩 위치: 인덱스 14~18 (is_food, is_alcohol, is_tobacco, is_perishable, is_general)
        # (is_holiday가 인덱스 12에 추가되어 1칸 shift)
        # 맥주: is_alcohol_group = 1
        assert features_beer[15] == 1.0   # is_alcohol_group
        assert features_beer[16] == 0.0   # is_tobacco_group

        # 담배: is_tobacco_group = 1
        assert features_tobacco[15] == 0.0  # is_alcohol_group
        assert features_tobacco[16] == 1.0  # is_tobacco_group

    def test_build_batch_features(self):
        """배치 feature 생성"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        items_data = []
        for i in range(5):
            daily_sales = _make_daily_sales(14, 3 + i)
            items_data.append({
                "item_cd": f"ITEM{i:03d}",
                "daily_sales": daily_sales,
                "target_date": daily_sales[-1]["sales_date"],
                "mid_cd": "049",
                "actual_sale_qty": 3 + i,
                "stock_qty": 10,
                "pending_qty": 0,
                "promo_active": False,
            })

        X, y, codes = MLFeatureBuilder.build_batch_features(items_data)

        assert X is not None
        assert y is not None
        assert len(codes) == 5
        assert X.shape == (5, 36)
        assert y.shape == (5,)
        assert all(yi >= 0 for yi in y)

    def test_build_batch_empty(self):
        """빈 데이터로 배치 feature"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder

        X, y, codes = MLFeatureBuilder.build_batch_features([])

        assert X is None
        assert y is None
        assert codes == []


class TestCategoryGroups:
    """카테고리 그룹 매핑 테스트"""

    def test_get_category_group(self):
        """중분류 코드 → 카테고리 그룹 매핑"""
        from src.prediction.ml.feature_builder import get_category_group

        assert get_category_group("001") == "food_group"
        assert get_category_group("005") == "food_group"
        assert get_category_group("012") == "food_group"
        assert get_category_group("049") == "alcohol_group"
        assert get_category_group("050") == "alcohol_group"
        assert get_category_group("072") == "tobacco_group"
        assert get_category_group("073") == "tobacco_group"
        assert get_category_group("013") == "perishable_group"
        assert get_category_group("099") == "general_group"  # 미분류

    def test_category_groups_keys(self):
        """CATEGORY_GROUPS 키 확인"""
        from src.prediction.ml.feature_builder import CATEGORY_GROUPS

        expected_groups = {"food_group", "alcohol_group", "tobacco_group", "perishable_group", "general_group"}
        assert set(CATEGORY_GROUPS.keys()) == expected_groups


class TestMLPredictor:
    """MLPredictor 모델 관리 테스트"""

    def test_init_no_models(self, tmp_path):
        """모델 없이 초기화 → is_available=False"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(model_dir=str(tmp_path / "models"))
        assert predictor.is_available() is False

    def test_save_and_load_model(self, tmp_path):
        """모델 저장 후 로드"""
        from src.prediction.ml.model import MLPredictor

        model_dir = tmp_path / "models"
        predictor = MLPredictor(model_dir=str(model_dir))

        # 저장 (모듈 레벨 클래스 → joblib 직렬화 가능)
        success = predictor.save_model("alcohol_group", _MockMLModel(5.0))
        assert success is True
        assert (model_dir / "model_alcohol_group.joblib").exists()

        # 새 인스턴스로 로드
        predictor2 = MLPredictor(model_dir=str(model_dir))
        loaded = predictor2.load_models()
        assert loaded is True
        assert predictor2.is_available() is True

    def test_predict_with_model(self, tmp_path):
        """모델이 있을 때 예측"""
        from src.prediction.ml.model import MLPredictor

        model_dir = tmp_path / "models"
        predictor = MLPredictor(model_dir=str(model_dir))

        predictor.save_model("alcohol_group", _MockMLModel(7.5))
        predictor.load_models()

        features = np.random.rand(31)
        result = predictor.predict(features, mid_cd="049")

        assert result is not None
        assert isinstance(result, float)
        assert result >= 0.0

    def test_predict_no_model(self, tmp_path):
        """모델 없을 때 None"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(model_dir=str(tmp_path / "models"))
        features = np.random.rand(31)
        result = predictor.predict(features, mid_cd="049")

        assert result is None

    def test_get_model_info(self, tmp_path):
        """모델 정보 조회"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(model_dir=str(tmp_path / "models"))
        info = predictor.get_model_info()

        assert isinstance(info, dict)
        assert "available" in info
        assert "model_dir" in info
        assert "groups" in info


class TestEnsembleModel:
    """앙상블 모델 래퍼 테스트"""

    def test_ensemble_weighted_average(self):
        """RF/GB 가중평균 검증"""
        from src.prediction.ml.trainer import _EnsembleModel

        class MockRF:
            def predict(self, X):
                return np.array([10.0, 20.0])

        class MockGB:
            def predict(self, X):
                return np.array([8.0, 16.0])

        # 기본 비율: 50/50
        ensemble = _EnsembleModel(MockRF(), MockGB(), rf_weight=0.5)
        X = np.zeros((2, 5))
        pred = ensemble.predict(X)

        np.testing.assert_array_almost_equal(pred, [9.0, 18.0])

    def test_ensemble_custom_weights(self):
        """커스텀 가중치 검증"""
        from src.prediction.ml.trainer import _EnsembleModel

        class MockRF:
            def predict(self, X):
                return np.array([10.0])

        class MockGB:
            def predict(self, X):
                return np.array([0.0])

        # RF 70%, GB 30%
        ensemble = _EnsembleModel(MockRF(), MockGB(), rf_weight=0.7)
        pred = ensemble.predict(np.zeros((1, 5)))

        np.testing.assert_array_almost_equal(pred, [7.0])

    def test_ensemble_repr(self):
        """__repr__ 테스트"""
        from src.prediction.ml.trainer import _EnsembleModel

        ensemble = _EnsembleModel(None, None, rf_weight=0.6)
        assert "0.6" in repr(ensemble)
        assert "0.4" in repr(ensemble)


class TestStoreSpecificML:
    """매장별 ML 모델 분리 테스트"""

    def test_store_specific_model_dir(self, tmp_path):
        """store_id 전달 시 매장별 디렉토리 사용"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(store_id="46513")
        assert "46513" in str(predictor.model_dir)
        assert predictor.store_id == "46513"

    def test_global_model_dir_without_store(self, tmp_path):
        """store_id 없으면 글로벌 디렉토리"""
        from src.prediction.ml.model import MLPredictor, MODEL_BASE_DIR

        predictor = MLPredictor()
        assert predictor.model_dir == MODEL_BASE_DIR
        assert predictor.store_id is None

    def test_explicit_model_dir_overrides_store(self, tmp_path):
        """model_dir 명시 시 store_id 무시"""
        from src.prediction.ml.model import MLPredictor

        model_dir = str(tmp_path / "custom_models")
        predictor = MLPredictor(model_dir=model_dir, store_id="46513")
        assert str(predictor.model_dir) == model_dir

    def test_global_fallback(self, tmp_path):
        """매장별 모델 없으면 글로벌 폴백"""
        from src.prediction.ml.model import MLPredictor

        # 글로벌 디렉토리에만 모델 저장
        global_dir = tmp_path / "models"
        global_dir.mkdir()
        store_dir = tmp_path / "models" / "99999"
        store_dir.mkdir()

        # 글로벌에 모델 저장
        global_predictor = MLPredictor(model_dir=str(global_dir))
        global_predictor.save_model("alcohol_group", _MockMLModel(5.0))

        # 매장별 디렉토리에서 로드 시도 → 글로벌 폴백
        # 직접 _load_from_dir 호출로 폴백 메커니즘 검증
        store_predictor = MLPredictor(model_dir=str(store_dir))
        loaded = store_predictor._load_from_dir(store_dir)
        assert loaded == 0  # 매장별 모델 없음

        loaded_global = store_predictor._load_from_dir(global_dir)
        assert loaded_global == 1  # 글로벌에서 1개 로드
        assert "alcohol_group" in store_predictor.models

    def test_store_model_saves_in_store_dir(self, tmp_path):
        """매장별 모델 저장이 매장 디렉토리에 생성"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(model_dir=str(tmp_path / "46513"))
        predictor.save_model("food_group", _MockMLModel(3.0))

        assert (tmp_path / "46513" / "model_food_group.joblib").exists()
        assert predictor.models["food_group"] is not None

    def test_trainer_with_store_id(self):
        """MLTrainer에 store_id 전달"""
        from src.prediction.ml.trainer import MLTrainer

        trainer = MLTrainer(store_id="46513")
        assert trainer.store_id == "46513"
        assert trainer.pipeline.store_id == "46513"
        assert trainer.predictor.store_id == "46513"
        assert "46513" in str(trainer.predictor.model_dir)

    def test_trainer_without_store_id(self):
        """MLTrainer store_id 없으면 기존 동작"""
        from src.prediction.ml.trainer import MLTrainer

        trainer = MLTrainer()
        assert trainer.store_id is None
        assert trainer.pipeline.store_id is None
        assert trainer.predictor.store_id is None

    def test_data_pipeline_store_db_path(self):
        """MLDataPipeline에 store_id 전달 시 매장 DB 경로"""
        from src.prediction.ml.data_pipeline import MLDataPipeline

        pipeline = MLDataPipeline(store_id="46513")
        assert "46513" in pipeline.db_path
        assert pipeline._use_split_db is True

    def test_data_pipeline_legacy_fallback(self):
        """MLDataPipeline store_id 없으면 레거시 DB"""
        from src.prediction.ml.data_pipeline import MLDataPipeline

        pipeline = MLDataPipeline()
        assert "bgf_sales.db" in pipeline.db_path
        assert pipeline._use_split_db is False

    def test_data_pipeline_explicit_db_path(self):
        """MLDataPipeline db_path 명시 시 그대로 사용"""
        from src.prediction.ml.data_pipeline import MLDataPipeline

        pipeline = MLDataPipeline(db_path="/tmp/test.db", store_id="46513")
        assert pipeline.db_path == "/tmp/test.db"
        assert pipeline._use_split_db is False  # db_path 명시 → split 안 함

    def test_get_model_info_includes_store_id(self, tmp_path):
        """get_model_info에 store_id 포함"""
        from src.prediction.ml.model import MLPredictor

        predictor = MLPredictor(model_dir=str(tmp_path / "models"), store_id="46513")
        info = predictor.get_model_info()

        assert info["store_id"] == "46513"
        assert "model_dir" in info
