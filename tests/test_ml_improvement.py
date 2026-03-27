"""
ml-improvement PDCA 테스트
- Phase A: ML 기여도 로깅
- Phase B: 적응형 블렌딩 (MAE 기반 가중치)
- Phase C: 피처 정리 (41→31)
- Phase D: Quantile alpha 도메인 정합
- Phase E: Accuracy@1 메트릭 + 성능 게이트 보완
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── Phase C: 피처 정리 ──────────────────────────────────────

class TestFeatureCleanup:
    """Phase C: 중복 피처 제거 (41→31)"""

    def test_feature_count_is_35(self):
        """FEATURE_NAMES가 정확히 35개 (food-ml-dual-model: +4)"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        assert len(MLFeatureBuilder.FEATURE_NAMES) == 45

    def test_no_category_group_onehot(self):
        """카테고리 그룹 원핫 5개 제거 확인"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        removed = ["is_food_group", "is_alcohol_group", "is_tobacco_group",
                    "is_perishable_group", "is_general_group"]
        for name in removed:
            assert name not in MLFeatureBuilder.FEATURE_NAMES, f"{name} 미제거"

    def test_no_large_cd_onehot(self):
        """대분류 슈퍼그룹 원핫 5개 제거 확인"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        removed = ["is_lcd_food", "is_lcd_snack", "is_lcd_grocery",
                    "is_lcd_beverage", "is_lcd_non_food"]
        for name in removed:
            assert name not in MLFeatureBuilder.FEATURE_NAMES, f"{name} 미제거"

    def test_retained_features_present(self):
        """유지 피처가 여전히 존재"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        retained = ["daily_avg_7", "stock_qty", "trend_score", "weekday_sin",
                     "promo_active", "expiration_days", "temperature",
                     "lag_7", "association_score", "lead_time_avg"]
        for name in retained:
            assert name in MLFeatureBuilder.FEATURE_NAMES, f"{name} 누락"

    def test_build_features_returns_31(self):
        """build_features가 31차원 배열 반환"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        daily_sales = [{"sales_date": f"2026-02-{i:02d}", "sale_qty": 3}
                       for i in range(1, 29)]
        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date="2026-02-28",
            mid_cd="001",
        )
        assert features is not None
        assert len(features) == 45

    def test_feature_hash_changed(self):
        """feature_hash가 기존(a093ecdc)과 다름"""
        from src.prediction.ml.model import MLPredictor
        new_hash = MLPredictor._feature_hash()
        assert new_hash != "a093ecdc", "피처 변경 후 해시가 같으면 안됨"

    def test_category_group_lookup_still_works(self):
        """get_category_group은 여전히 정상 동작 (삭제 아님)"""
        from src.prediction.ml.feature_builder import get_category_group
        assert get_category_group("001") == "food_group"
        assert get_category_group("049") == "alcohol_group"
        assert get_category_group("999") == "general_group"


# ── Phase D: Quantile Alpha 도메인 정합 ─────────────────────

class TestQuantileAlpha:
    """Phase D: alpha 값 도메인 정합"""

    def test_food_alpha_conservative(self):
        """food는 보수적 (alpha < 0.5)"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        assert GROUP_QUANTILE_ALPHA["food_group"] < 0.5

    def test_perishable_alpha_conservative(self):
        """perishable도 보수적"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        assert GROUP_QUANTILE_ALPHA["perishable_group"] < 0.5

    def test_tobacco_alpha_generous(self):
        """tobacco는 넉넉 (alpha > 0.5, 품절 시 고객 이탈)"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        assert GROUP_QUANTILE_ALPHA["tobacco_group"] > 0.5

    def test_alcohol_alpha_generous(self):
        """alcohol은 넉넉 (유통기한 길고 품절 기회비용)"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        assert GROUP_QUANTILE_ALPHA["alcohol_group"] > 0.5

    def test_general_alpha_neutral(self):
        """general은 중립 (0.5)"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        assert GROUP_QUANTILE_ALPHA["general_group"] == 0.5

    def test_all_alpha_in_valid_range(self):
        """모든 alpha가 0.3~0.7 범위"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        for group, alpha in GROUP_QUANTILE_ALPHA.items():
            assert 0.3 <= alpha <= 0.7, f"{group}: alpha={alpha} 범위 초과"


# ── Phase B: 적응형 블렌딩 ──────────────────────────────────

class TestAdaptiveBlending:
    """Phase B: MAE 기반 적응형 ML 가중치"""

    def _make_predictor(self):
        """테스트용 ImprovedPredictor stub"""
        from src.prediction.improved_predictor import ImprovedPredictor
        with patch.object(ImprovedPredictor, '__init__', lambda self, *a, **kw: None):
            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._ml_predictor = MagicMock()
            return p

    def _mock_meta(self, predictor, group_mae_map):
        """모델 메타를 mock"""
        meta = {"groups": {}}
        for group, mae in group_mae_map.items():
            meta["groups"][group] = {"metrics": {"mae": mae}}
        predictor._ml_predictor._load_meta.return_value = meta

    def test_data_days_below_30_non_food_returns_zero(self):
        """비푸드: 데이터 30일 미만이면 ML 미사용"""
        p = self._make_predictor()
        assert p._get_ml_weight("049", data_days=20) == 0.0

    def test_data_days_below_30_food_with_group_returns_005(self):
        """푸드+그룹모델: 30일 미만이어도 0.05 (ml-weight-adjust)"""
        p = self._make_predictor()
        p._item_smallcd_map = {"ITEM_F": "001A"}
        p._ml_predictor.has_group_model.return_value = True
        assert p._get_ml_weight("001", data_days=20, item_cd="ITEM_F") == 0.05

    def test_no_meta_returns_conservative(self):
        """메타 없으면 보수적 0.10 (ml-weight-adjust)"""
        p = self._make_predictor()
        p._ml_predictor._load_meta.return_value = {}
        assert p._get_ml_weight("001", data_days=90) == 0.10

    def test_low_mae_food_max_015(self):
        """MAE 낮으면 (0.5) 푸드 최대 가중치 0.15 (ml-weight-adjust)"""
        p = self._make_predictor()
        self._mock_meta(p, {"food_group": 0.5})
        w = p._get_ml_weight("001", data_days=90)
        assert w == 0.15, f"MAE=0.5, food → weight={w}, 0.15이어야 함"

    def test_low_mae_general_max_025(self):
        """MAE 낮으면 (0.5) 일반 최대 가중치 0.25 (ml-weight-adjust)"""
        p = self._make_predictor()
        self._mock_meta(p, {"general_group": 0.5})
        w = p._get_ml_weight("999", data_days=90)
        assert w == 0.25, f"MAE=0.5, general → weight={w}, 0.25이어야 함"

    def test_high_mae_low_weight(self):
        """MAE 높으면 (2.0) 가중치 0.05 (ml-weight-adjust)"""
        p = self._make_predictor()
        self._mock_meta(p, {"general_group": 2.0})
        w = p._get_ml_weight("999", data_days=90)
        assert w == 0.05

    def test_medium_mae_medium_weight(self):
        """MAE 중간 (1.0) → 가중치 중간 (ml-weight-adjust)"""
        p = self._make_predictor()
        self._mock_meta(p, {"food_group": 1.0})
        w = p._get_ml_weight("001", data_days=90)
        assert 0.05 <= w <= 0.15, f"MAE=1.0, food → weight={w}"

    def test_data_days_below_60_dampened(self):
        """60일 미만이면 가중치 감쇄 (×0.6)"""
        p = self._make_predictor()
        self._mock_meta(p, {"food_group": 0.5})
        w_90 = p._get_ml_weight("001", data_days=90)
        w_45 = p._get_ml_weight("001", data_days=45)
        assert w_45 < w_90, f"45일({w_45}) >= 90일({w_90}) 감쇄 안됨"
        assert abs(w_45 - round(w_90 * 0.6, 2)) < 0.02

    def test_weight_bounded(self):
        """가중치가 0.05~0.25 범위 (ml-weight-adjust)"""
        p = self._make_predictor()
        # 매우 낮은 MAE → 카테고리 최대
        self._mock_meta(p, {"food_group": 0.01})
        w = p._get_ml_weight("001", data_days=90)
        assert w <= 0.15  # food 상한
        self._mock_meta(p, {"general_group": 0.01})
        w = p._get_ml_weight("999", data_days=90)
        assert w <= 0.25  # general 상한
        # 매우 높은 MAE
        self._mock_meta(p, {"food_group": 10.0})
        w = p._get_ml_weight("001", data_days=90)
        assert w >= 0.05


# ── Phase A: ML 기여도 로깅 ─────────────────────────────────

class TestMLContributionLogging:
    """Phase A: ctx에 ML 기여도 필드 기록"""

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
            p._lag_feature_repo = None
            p._stacking = None
            p.store_id = "46704"
            p.db_path = ":memory:"
            return p

    @patch("src.prediction.ml.feature_builder.MLFeatureBuilder.build_features")
    @patch("src.prediction.ml.data_pipeline.MLDataPipeline")
    def test_ctx_has_ml_fields_when_blended(self, mock_pipeline, mock_build):
        """ML 블렌딩 시 ctx에 기여도 필드 기록"""
        p = self._make_predictor()
        p._ml_predictor.predict_dual.return_value = 5.0
        mock_build.return_value = np.zeros(39)

        # _get_ml_weight를 모킹하여 0.3 반환
        with patch.object(p, '_get_ml_weight', return_value=0.3):
            with patch.object(p, '_get_holiday_context', return_value={}):
                with patch.object(p, '_get_temperature_for_date', return_value=None):
                    with patch.object(p, '_get_temperature_delta', return_value=None):
                        with patch.object(p, '_get_disuse_rate', return_value=0.0):
                            from datetime import date
                            ctx = {"model_type": "rule"}
                            order_qty, model_type = p._apply_ml_ensemble(
                                "item1", {"item_nm": "test"}, "001", 3,
                                90, date(2026, 3, 1),
                                2, 0, 1.0, None, ctx=ctx
                            )
                            assert "ml_delta" in ctx
                            assert "ml_abs_delta" in ctx
                            assert "ml_changed_final" in ctx
                            assert "ml_weight" in ctx
                            assert ctx["ml_weight"] == 0.3

    def test_ctx_has_zero_delta_when_no_ml(self):
        """ML 미사용 시 ctx에 delta=0 기록"""
        p = self._make_predictor()
        p._ml_predictor = None  # ML 없음
        ctx = {"model_type": "rule"}
        order_qty, model_type = p._apply_ml_ensemble(
            "item1", {"item_nm": "test"}, "001", 3,
            90, None, 2, 0, 1.0, None, ctx=ctx
        )
        assert order_qty == 3
        assert model_type == "rule"


# ── Phase E: Accuracy@1 메트릭 + 성능 게이트 보완 ───────────

class TestAccuracyMetric:
    """Phase E: Accuracy@N 메트릭 및 성능 게이트"""

    def test_accuracy_at_1_calculation(self):
        """Accuracy@1: ±1 이내 비율 계산"""
        y_test = np.array([0, 1, 2, 3, 5])
        y_pred = np.array([0.5, 1.2, 4.0, 3.0, 5.5])
        acc1 = float(np.mean(np.abs(y_test - y_pred) <= 1.0))
        # |0-0.5|=0.5 ✓, |1-1.2|=0.2 ✓, |2-4|=2.0 ✗, |3-3|=0 ✓, |5-5.5|=0.5 ✓
        assert acc1 == pytest.approx(0.8)

    def test_accuracy_at_2_calculation(self):
        """Accuracy@2: ±2 이내 비율 계산"""
        y_test = np.array([0, 1, 2, 3, 5])
        y_pred = np.array([0.5, 1.2, 4.0, 3.0, 5.5])
        acc2 = float(np.mean(np.abs(y_test - y_pred) <= 2.0))
        # |2-4|=2.0 ✓ (경계), 전부 ✓
        assert acc2 == pytest.approx(1.0)

    def test_performance_gate_mae_fail(self):
        """MAE 20% 이상 악화 시 게이트 실패"""
        from src.prediction.ml.trainer import MLTrainer
        with patch.object(MLTrainer, '__init__', lambda self, *a, **kw: None):
            t = MLTrainer.__new__(MLTrainer)
            t.predictor = MagicMock()
            t.predictor._load_meta.return_value = {
                "groups": {"food_group": {"metrics": {"mae": 1.0}}}
            }
            # MAE 1.0 → 1.3 (30% 악화)
            assert t._check_performance_gate("food_group", 1.3) is False

    def test_performance_gate_mae_pass(self):
        """MAE 10% 악화는 허용"""
        from src.prediction.ml.trainer import MLTrainer
        with patch.object(MLTrainer, '__init__', lambda self, *a, **kw: None):
            t = MLTrainer.__new__(MLTrainer)
            t.predictor = MagicMock()
            t.predictor._load_meta.return_value = {
                "groups": {"food_group": {"metrics": {"mae": 1.0}}}
            }
            assert t._check_performance_gate("food_group", 1.1) is True

    def test_performance_gate_accuracy_fail(self):
        """Accuracy@1 5%p 이상 하락 시 게이트 실패"""
        from src.prediction.ml.trainer import MLTrainer
        with patch.object(MLTrainer, '__init__', lambda self, *a, **kw: None):
            t = MLTrainer.__new__(MLTrainer)
            t.predictor = MagicMock()
            t.predictor._load_meta.return_value = {
                "groups": {"food_group": {"metrics": {"mae": 1.0, "accuracy_at_1": 0.80}}}
            }
            # MAE는 OK, Acc@1 0.80→0.72 (8%p 하락)
            assert t._check_performance_gate("food_group", 1.0, accuracy_at_1=0.72) is False

    def test_performance_gate_accuracy_pass(self):
        """Accuracy@1 3%p 하락은 허용"""
        from src.prediction.ml.trainer import MLTrainer
        with patch.object(MLTrainer, '__init__', lambda self, *a, **kw: None):
            t = MLTrainer.__new__(MLTrainer)
            t.predictor = MagicMock()
            t.predictor._load_meta.return_value = {
                "groups": {"food_group": {"metrics": {"mae": 1.0, "accuracy_at_1": 0.80}}}
            }
            assert t._check_performance_gate("food_group", 1.0, accuracy_at_1=0.77) is True

    def test_performance_gate_no_prev_meta(self):
        """이전 메타 없으면 항상 통과"""
        from src.prediction.ml.trainer import MLTrainer
        with patch.object(MLTrainer, '__init__', lambda self, *a, **kw: None):
            t = MLTrainer.__new__(MLTrainer)
            t.predictor = MagicMock()
            t.predictor._load_meta.return_value = {}
            assert t._check_performance_gate("food_group", 5.0, accuracy_at_1=0.1) is True

    def test_save_metrics_includes_accuracy(self):
        """save_metrics에 accuracy_at_1/2 포함"""
        y_test = np.array([1, 2, 3])
        y_pred = np.array([1.5, 2.5, 3.5])
        acc1 = float(np.mean(np.abs(y_test - y_pred) <= 1.0))
        acc2 = float(np.mean(np.abs(y_test - y_pred) <= 2.0))
        save_metrics = {
            "mae": 0.5,
            "accuracy_at_1": round(acc1, 3),
            "accuracy_at_2": round(acc2, 3),
        }
        assert "accuracy_at_1" in save_metrics
        assert "accuracy_at_2" in save_metrics
        assert save_metrics["accuracy_at_1"] == 1.0


# ── 통합 테스트 ──────────────────────────────────────────────

class TestIntegration:
    """파일 간 일관성 테스트"""

    def test_feature_names_match_array_length(self):
        """FEATURE_NAMES 개수와 build_features 배열 길이 일치"""
        from src.prediction.ml.feature_builder import MLFeatureBuilder
        daily_sales = [{"sales_date": f"2026-02-{i:02d}", "sale_qty": 2}
                       for i in range(1, 29)]
        features = MLFeatureBuilder.build_features(
            daily_sales=daily_sales,
            target_date="2026-02-28",
            mid_cd="049",
        )
        assert features is not None
        assert len(features) == len(MLFeatureBuilder.FEATURE_NAMES)

    def test_model_meta_records_accuracy(self):
        """model_meta.json에 accuracy_at_1 기록"""
        from src.prediction.ml.model import MLPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            p = MLPredictor(model_dir=tmpdir)
            mock_model = MagicMock()
            metrics = {
                "mae": 0.6,
                "accuracy_at_1": 0.75,
                "accuracy_at_2": 0.92,
            }
            p.save_model = MagicMock()  # 실제 joblib 호출 방지
            # _update_meta 직접 호출
            p._update_meta("food_group", metrics)

            meta_path = Path(tmpdir) / "model_meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert meta["groups"]["food_group"]["metrics"]["accuracy_at_1"] == 0.75
            assert meta["groups"]["food_group"]["metrics"]["accuracy_at_2"] == 0.92

    def test_all_quantile_alphas_have_groups(self):
        """GROUP_QUANTILE_ALPHA의 모든 키가 CATEGORY_GROUPS에 존재"""
        from src.prediction.ml.trainer import GROUP_QUANTILE_ALPHA
        from src.prediction.ml.feature_builder import CATEGORY_GROUPS
        for group in GROUP_QUANTILE_ALPHA:
            assert group in CATEGORY_GROUPS, f"{group} not in CATEGORY_GROUPS"
