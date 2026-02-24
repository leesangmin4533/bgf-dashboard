"""
eval_config.py 단위 테스트

ParamSpec, EvalConfig 클래스의 핵심 기능을 검증한다.
- 값 클램핑 / 델타 적용
- 직렬화 / 역직렬화 (JSON round-trip)
- 가중치 정규화
- 파라미터 업데이트 / 변경 감지
- 타입 일관성 (float 유지)
"""

import json
import pytest

from src.prediction.eval_config import ParamSpec, EvalConfig


# ============================================================
# ParamSpec 테스트
# ============================================================

class TestParamSpecClamp:
    """ParamSpec.clamp() 메서드 테스트"""

    @pytest.fixture
    def spec(self):
        """테스트용 ParamSpec (value=5.0, range=[1.0, 10.0])"""
        return ParamSpec(
            value=5.0, default=5.0,
            min_val=1.0, max_val=10.0, max_delta=2.0,
            description="테스트 파라미터",
        )

    @pytest.mark.unit
    def test_clamp_within_range(self, spec):
        """범위 내 값은 그대로 반환한다"""
        assert spec.clamp(5.0) == 5.0
        assert spec.clamp(3.0) == 3.0
        assert spec.clamp(8.5) == 8.5

    @pytest.mark.unit
    def test_clamp_below_min(self, spec):
        """최솟값보다 작은 값은 min_val로 클램핑한다"""
        assert spec.clamp(0.0) == 1.0
        assert spec.clamp(-100.0) == 1.0

    @pytest.mark.unit
    def test_clamp_above_max(self, spec):
        """최댓값보다 큰 값은 max_val로 클램핑한다"""
        assert spec.clamp(11.0) == 10.0
        assert spec.clamp(999.0) == 10.0

    @pytest.mark.unit
    @pytest.mark.parametrize("boundary", ["min", "max"])
    def test_clamp_exact_boundary(self, spec, boundary):
        """경계값(min_val, max_val)은 그대로 반환한다"""
        if boundary == "min":
            assert spec.clamp(1.0) == 1.0
        else:
            assert spec.clamp(10.0) == 10.0


class TestParamSpecApplyDelta:
    """ParamSpec.apply_delta() 메서드 테스트"""

    @pytest.fixture
    def spec(self):
        """테스트용 ParamSpec (value=5.0, range=[1.0, 10.0], max_delta=2.0)"""
        return ParamSpec(
            value=5.0, default=5.0,
            min_val=1.0, max_val=10.0, max_delta=2.0,
            description="테스트 파라미터",
        )

    @pytest.mark.unit
    def test_apply_delta_normal(self, spec):
        """max_delta 이내의 일반 델타는 그대로 적용된다"""
        assert spec.apply_delta(1.0) == 6.0
        assert spec.apply_delta(-1.5) == 3.5

    @pytest.mark.unit
    def test_apply_delta_exceeding_max_delta(self, spec):
        """max_delta를 초과하는 델타는 max_delta로 제한된다"""
        # delta=5.0 > max_delta=2.0 → clamped to 2.0 → value=5.0+2.0=7.0
        assert spec.apply_delta(5.0) == 7.0

    @pytest.mark.unit
    def test_apply_delta_negative_exceeding(self, spec):
        """음수 방향으로 max_delta를 초과하는 델타도 제한된다"""
        # delta=-5.0, |delta| > max_delta=2.0 → clamped to -2.0 → value=5.0-2.0=3.0
        assert spec.apply_delta(-5.0) == 3.0

    @pytest.mark.unit
    def test_apply_delta_result_clamped_to_range(self):
        """델타 적용 후 범위를 벗어나면 clamp가 적용된다"""
        spec = ParamSpec(
            value=9.5, default=9.5,
            min_val=1.0, max_val=10.0, max_delta=2.0,
            description="경계 근처 파라미터",
        )
        # delta=2.0 (max_delta 이내) → 9.5+2.0=11.5 → clamp(11.5)=10.0
        assert spec.apply_delta(2.0) == 10.0


class TestParamSpecDefaultTypes:
    """ParamSpec 기본값 타입 테스트"""

    @pytest.mark.unit
    def test_default_values_are_float(self):
        """ParamSpec 필드가 float 타입으로 저장된다"""
        spec = ParamSpec(
            value=5.0, default=5.0,
            min_val=1.0, max_val=10.0, max_delta=2.0,
        )
        assert isinstance(spec.value, float), "value는 float이어야 한다"
        assert isinstance(spec.default, float), "default는 float이어야 한다"
        assert isinstance(spec.min_val, float), "min_val은 float이어야 한다"
        assert isinstance(spec.max_val, float), "max_val은 float이어야 한다"
        assert isinstance(spec.max_delta, float), "max_delta는 float이어야 한다"


# ============================================================
# EvalConfig 테스트
# ============================================================

class TestEvalConfigDefaults:
    """EvalConfig 기본값 검증"""

    @pytest.mark.unit
    def test_default_daily_avg_days(self):
        """daily_avg_days 기본값은 14.0이다"""
        config = EvalConfig()
        assert config.daily_avg_days.value == 14.0
        assert config.daily_avg_days.default == 14.0

    @pytest.mark.unit
    def test_default_popularity_weights(self):
        """인기도 가중치 기본값이 올바르다 (0.40, 0.35, 0.25 = 1.0)"""
        config = EvalConfig()
        assert config.weight_daily_avg.value == 0.40
        assert config.weight_sell_day_ratio.value == 0.35
        assert config.weight_trend.value == 0.25

    @pytest.mark.unit
    def test_default_exposure_thresholds(self):
        """노출시간 임계값 기본값이 올바르다 (1.0, 2.0, 3.0)"""
        config = EvalConfig()
        assert config.exposure_urgent.value == 1.0
        assert config.exposure_normal.value == 2.0
        assert config.exposure_sufficient.value == 3.0

    @pytest.mark.unit
    def test_default_target_accuracy(self):
        """목표 적중률 기본값은 0.60이다"""
        config = EvalConfig()
        assert config.target_accuracy.value == 0.60

    @pytest.mark.unit
    def test_default_stockout_freq_threshold(self):
        """품절 빈도 임계값 기본값은 0.15이다"""
        config = EvalConfig()
        assert config.stockout_freq_threshold.value == 0.15

    @pytest.mark.unit
    def test_default_popularity_percentiles(self):
        """인기도 백분위 기본값이 올바르다 (70.0, 35.0)"""
        config = EvalConfig()
        assert config.popularity_high_percentile.value == 70.0
        assert config.popularity_low_percentile.value == 35.0


class TestGetPopularityWeights:
    """EvalConfig.get_popularity_weights() 테스트"""

    @pytest.mark.unit
    def test_returns_dict_with_expected_keys(self):
        """반환 딕셔너리에 daily_avg, sell_day_ratio, trend 키가 있다"""
        config = EvalConfig()
        weights = config.get_popularity_weights()
        assert set(weights.keys()) == {"daily_avg", "sell_day_ratio", "trend"}

    @pytest.mark.unit
    def test_weights_sum_to_one(self):
        """가중치 합계가 1.0이다"""
        config = EvalConfig()
        weights = config.get_popularity_weights()
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    @pytest.mark.unit
    def test_weights_after_modification_still_sum_to_one(self):
        """가중치를 변경해도 정규화되어 합계 1.0을 유지한다"""
        config = EvalConfig()
        config.weight_daily_avg.value = 0.50
        config.weight_sell_day_ratio.value = 0.30
        config.weight_trend.value = 0.20
        weights = config.get_popularity_weights()
        assert abs(sum(weights.values()) - 1.0) < 1e-9


class TestNormalizeWeights:
    """EvalConfig.normalize_weights() 테스트"""

    @pytest.mark.unit
    def test_normalize_sums_to_one(self):
        """정규화 후 3개 가중치 합계가 1.0이다"""
        config = EvalConfig()
        config.weight_daily_avg.value = 0.50
        config.weight_sell_day_ratio.value = 0.30
        config.weight_trend.value = 0.20
        config.normalize_weights()

        total = (
            config.weight_daily_avg.value
            + config.weight_sell_day_ratio.value
            + config.weight_trend.value
        )
        assert abs(total - 1.0) < 1e-9

    @pytest.mark.unit
    def test_normalize_with_default_values(self):
        """기본값(합계 1.0)에 정규화를 적용해도 값이 유지된다"""
        config = EvalConfig()
        original_daily = config.weight_daily_avg.value
        original_ratio = config.weight_sell_day_ratio.value
        original_trend = config.weight_trend.value

        config.normalize_weights()

        # 기본값 합계가 1.0이므로 정규화 후에도 동일
        assert abs(config.weight_daily_avg.value - original_daily) < 1e-3
        assert abs(config.weight_sell_day_ratio.value - original_ratio) < 1e-3
        assert abs(config.weight_trend.value - original_trend) < 1e-3

    @pytest.mark.unit
    def test_normalize_with_unbalanced_weights(self):
        """비대칭 가중치도 정규화 후 합계 1.0이 된다"""
        config = EvalConfig()
        config.weight_daily_avg.value = 1.0
        config.weight_sell_day_ratio.value = 2.0
        config.weight_trend.value = 3.0

        config.normalize_weights()

        total = (
            config.weight_daily_avg.value
            + config.weight_sell_day_ratio.value
            + config.weight_trend.value
        )
        assert abs(total - 1.0) < 1e-9


class TestToDictFromDict:
    """to_dict() / from_dict() JSON round-trip 테스트

    참고: EvalConfig에는 from_dict()가 없으므로
    to_dict() → save() → load() 경로로 round-trip을 검증한다.
    """

    @pytest.mark.unit
    def test_to_dict_contains_all_params(self):
        """to_dict()가 모든 파라미터를 포함한다"""
        config = EvalConfig()
        d = config.to_dict()
        expected_keys = config._param_names()
        assert set(d.keys()) == set(expected_keys)

    @pytest.mark.unit
    def test_to_dict_param_structure(self):
        """to_dict()의 각 파라미터가 올바른 키를 가진다"""
        config = EvalConfig()
        d = config.to_dict()
        for name, param_dict in d.items():
            assert "value" in param_dict, f"{name}에 value 키가 없다"
            assert "default" in param_dict, f"{name}에 default 키가 없다"
            assert "min" in param_dict, f"{name}에 min 키가 없다"
            assert "max" in param_dict, f"{name}에 max 키가 없다"
            assert "max_delta" in param_dict, f"{name}에 max_delta 키가 없다"
            assert "description" in param_dict, f"{name}에 description 키가 없다"

    @pytest.mark.unit
    def test_round_trip_via_json(self, tmp_path):
        """save() → load() round-trip에서 값이 보존된다"""
        config = EvalConfig()
        config.weight_daily_avg.value = 0.50
        config.exposure_urgent.value = 1.5
        config.target_accuracy.value = 0.70

        filepath = tmp_path / "eval_params.json"
        config.save(path=filepath)

        loaded = EvalConfig.load(path=filepath)
        assert loaded.weight_daily_avg.value == 0.50
        assert loaded.exposure_urgent.value == 1.5
        assert loaded.target_accuracy.value == 0.70

    @pytest.mark.unit
    def test_round_trip_preserves_all_defaults(self, tmp_path):
        """기본값 설정을 저장/로드하면 모든 값이 동일하다"""
        config = EvalConfig()
        filepath = tmp_path / "eval_params.json"
        config.save(path=filepath)

        loaded = EvalConfig.load(path=filepath)
        original_dict = config.to_dict()
        loaded_dict = loaded.to_dict()

        for name in config._param_names():
            assert abs(original_dict[name]["value"] - loaded_dict[name]["value"]) < 1e-9, \
                f"{name} 값이 round-trip에서 변경되었다"


class TestSaveLoad:
    """EvalConfig.save() / load() 파일 I/O 테스트"""

    @pytest.mark.unit
    def test_save_creates_file(self, tmp_path):
        """save()가 JSON 파일을 생성한다"""
        config = EvalConfig()
        filepath = tmp_path / "eval_params.json"
        result = config.save(path=filepath)
        assert filepath.exists()
        assert result == str(filepath)

    @pytest.mark.unit
    def test_save_creates_parent_directory(self, tmp_path):
        """save()가 부모 디렉토리를 자동 생성한다"""
        config = EvalConfig()
        filepath = tmp_path / "nested" / "dir" / "eval_params.json"
        config.save(path=filepath)
        assert filepath.exists()

    @pytest.mark.unit
    def test_saved_file_is_valid_json(self, tmp_path):
        """저장된 파일이 올바른 JSON 형식이다"""
        config = EvalConfig()
        filepath = tmp_path / "eval_params.json"
        config.save(path=filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) == len(config._param_names())

    @pytest.mark.unit
    def test_load_missing_file_returns_defaults(self, tmp_path):
        """존재하지 않는 파일을 로드하면 기본값을 반환한다"""
        filepath = tmp_path / "nonexistent.json"
        loaded = EvalConfig.load(path=filepath)

        default = EvalConfig()
        for name in default._param_names():
            loaded_spec = getattr(loaded, name)
            default_spec = getattr(default, name)
            assert loaded_spec.value == default_spec.value, \
                f"{name}: 기본값과 다르다 ({loaded_spec.value} != {default_spec.value})"

    @pytest.mark.unit
    def test_load_corrupted_json_returns_defaults(self, tmp_path):
        """손상된 JSON 파일을 로드하면 기본값을 반환한다"""
        filepath = tmp_path / "corrupted.json"
        filepath.write_text("{{{{not valid json!!", encoding="utf-8")

        loaded = EvalConfig.load(path=filepath)

        default = EvalConfig()
        for name in default._param_names():
            loaded_spec = getattr(loaded, name)
            default_spec = getattr(default, name)
            assert loaded_spec.value == default_spec.value, \
                f"{name}: 손상 파일 로드 후 기본값과 다르다"

    @pytest.mark.unit
    def test_load_preserves_clamping(self, tmp_path):
        """로드 시 범위 밖 값이 클램핑된다"""
        filepath = tmp_path / "out_of_range.json"
        config = EvalConfig()
        data = config.to_dict()

        # daily_avg_days의 value를 범위 밖으로 설정 (max_val=30.0)
        data["daily_avg_days"]["value"] = 999.0
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)

        loaded = EvalConfig.load(path=filepath)
        assert loaded.daily_avg_days.value == 30.0, \
            "범위 밖 값은 max_val로 클램핑되어야 한다"


class TestUpdateParam:
    """EvalConfig.update_param() 테스트"""

    @pytest.mark.unit
    def test_update_valid_param(self):
        """유효한 파라미터를 업데이트하면 값이 변경된다"""
        config = EvalConfig()
        result = config.update_param("exposure_urgent", 1.2)
        assert result is not None
        assert result == 1.2
        assert config.exposure_urgent.value == 1.2

    @pytest.mark.unit
    def test_update_with_delta_limiting(self):
        """max_delta를 초과하는 변경은 제한된다"""
        config = EvalConfig()
        # daily_avg_days: value=14.0, max_delta=7.0
        # 목표: 30.0 → delta=16.0 > max_delta=7.0 → 적용: 14.0+7.0=21.0
        result = config.update_param("daily_avg_days", 30.0)
        assert result == 21.0
        assert config.daily_avg_days.value == 21.0

    @pytest.mark.unit
    def test_update_invalid_param_name(self):
        """잘못된 파라미터 이름은 None을 반환한다"""
        config = EvalConfig()
        result = config.update_param("nonexistent_param", 5.0)
        assert result is None

    @pytest.mark.unit
    def test_update_param_respects_range_clamp(self):
        """업데이트 결과가 범위를 벗어나면 클램핑된다"""
        config = EvalConfig()
        # stockout_freq_threshold: value=0.15, max_delta=0.03, max_val=0.30
        # 연속으로 max_delta씩 올려서 결국 max_val에 도달하는 시나리오
        for _ in range(20):
            config.update_param("stockout_freq_threshold", 1.0)
        assert config.stockout_freq_threshold.value <= 0.30


class TestDiffFromDefault:
    """EvalConfig.diff_from_default() 테스트"""

    @pytest.mark.unit
    def test_no_changes_returns_empty(self):
        """변경이 없으면 빈 딕셔너리를 반환한다"""
        config = EvalConfig()
        diff = config.diff_from_default()
        assert diff == {}

    @pytest.mark.unit
    def test_changes_are_detected(self):
        """값이 변경되면 해당 파라미터가 diff에 포함된다"""
        config = EvalConfig()
        config.exposure_urgent.value = 1.5
        config.target_accuracy.value = 0.75

        diff = config.diff_from_default()
        assert "exposure_urgent" in diff
        assert "target_accuracy" in diff
        assert len(diff) == 2

    @pytest.mark.unit
    def test_diff_contains_correct_values(self):
        """diff에 current, default, delta 값이 올바르게 포함된다"""
        config = EvalConfig()
        config.exposure_urgent.value = 1.5

        diff = config.diff_from_default()
        entry = diff["exposure_urgent"]
        assert entry["current"] == 1.5
        assert entry["default"] == 1.0
        assert abs(entry["delta"] - 0.5) < 1e-6

    @pytest.mark.unit
    def test_tiny_change_below_threshold_not_detected(self):
        """1e-6 이하의 미세한 변경은 감지되지 않는다"""
        config = EvalConfig()
        config.exposure_urgent.value = config.exposure_urgent.default + 1e-8
        diff = config.diff_from_default()
        assert "exposure_urgent" not in diff


# ============================================================
# 타입 일관성 테스트
# ============================================================

class TestTypeConsistency:
    """모든 파라미터 값이 float 타입을 유지하는지 검증"""

    @pytest.mark.unit
    def test_all_param_values_are_float(self):
        """EvalConfig의 모든 ParamSpec.value가 float 타입이다"""
        config = EvalConfig()
        for name in config._param_names():
            spec = getattr(config, name)
            assert isinstance(spec.value, float), \
                f"{name}.value ({spec.value})는 float이어야 한다 (실제: {type(spec.value).__name__})"

    @pytest.mark.unit
    def test_values_remain_float_after_json_round_trip(self, tmp_path):
        """JSON save/load 후에도 모든 값이 float 타입을 유지한다"""
        config = EvalConfig()
        filepath = tmp_path / "type_test.json"
        config.save(path=filepath)

        loaded = EvalConfig.load(path=filepath)
        for name in loaded._param_names():
            spec = getattr(loaded, name)
            assert isinstance(spec.value, float), \
                f"JSON 로드 후 {name}.value ({spec.value})가 float이 아니다 (실제: {type(spec.value).__name__})"

    @pytest.mark.unit
    def test_integer_like_values_stay_float_after_load(self, tmp_path):
        """정수처럼 보이는 값(14.0 등)도 JSON 로드 후 float을 유지한다"""
        config = EvalConfig()
        filepath = tmp_path / "int_like.json"

        # JSON에서 14.0은 14로 저장될 수 있으므로 로드 시 float 변환 확인
        data = config.to_dict()
        # 정수 값으로 강제 변환하여 저장
        data["daily_avg_days"]["value"] = 14  # int로 저장
        data["exposure_urgent"]["value"] = 1  # int로 저장
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)

        loaded = EvalConfig.load(path=filepath)
        assert isinstance(loaded.daily_avg_days.value, float), \
            "정수로 저장된 daily_avg_days가 float으로 복원되어야 한다"
        assert isinstance(loaded.exposure_urgent.value, float), \
            "정수로 저장된 exposure_urgent가 float으로 복원되어야 한다"


# ============================================================
# 3-가중치 인기도 시스템 테스트
# ============================================================

class TestThreeWeightPopularity:
    """3개 가중치 인기도 시스템 테스트 (순수 수요 지표)"""

    @pytest.mark.unit
    def test_three_weights_sum_to_one(self):
        """get_popularity_weights() 반환값 3개의 합이 1.0이다"""
        config = EvalConfig()
        weights = config.get_popularity_weights()
        assert len(weights) == 3
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    @pytest.mark.unit
    def test_three_weights_after_modification_sum_to_one(self):
        """3개 가중치를 변경해도 정규화되어 합계 1.0을 유지한다"""
        config = EvalConfig()
        config.weight_daily_avg.value = 0.50
        config.weight_sell_day_ratio.value = 0.30
        config.weight_trend.value = 0.10
        weights = config.get_popularity_weights()
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    @pytest.mark.unit
    def test_normalize_three_weights(self):
        """normalize_weights()가 3개 가중치를 1.0으로 정규화한다"""
        config = EvalConfig()
        config.weight_daily_avg.value = 2.0
        config.weight_sell_day_ratio.value = 1.0
        config.weight_trend.value = 1.0

        config.normalize_weights()

        total = (
            config.weight_daily_avg.value
            + config.weight_sell_day_ratio.value
            + config.weight_trend.value
        )
        assert abs(total - 1.0) < 1e-9
        # daily_avg가 가장 크게 유지
        assert config.weight_daily_avg.value > config.weight_sell_day_ratio.value

    @pytest.mark.unit
    def test_param_names_exclude_removed_params(self):
        """_param_names()에 weight_exposure_urgency, urgency_max_days가 없다"""
        config = EvalConfig()
        names = config._param_names()
        assert "weight_exposure_urgency" not in names
        assert "urgency_max_days" not in names
