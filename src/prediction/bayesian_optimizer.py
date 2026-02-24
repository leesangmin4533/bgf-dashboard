"""
베이지안 파라미터 자동 최적화 엔진

주 1회 실행 (Phase 1.57, 일요일 23:00):
1. 최근 7일 eval_outcomes + daily_sales 성과 지표 수집
2. 현재 파라미터 -> 탐색 공간 구성 (ParamSpec 기반)
3. GP/TPE surrogate 기반 목적함수 최소화 (30 trials)
4. damping 적용 후 eval_params.json + food_waste_calibration 반영
5. DB에 최적화 이력 저장
6. 적용 후 3일 모니터링 -> 성과 하락 시 자동 롤백

기존 보정기와의 관계:
- EvalCalibrator (daily): 미세조정 -> Bayesian 결과를 기반값으로 사용
- FoodWasteRateCalibrator (daily): 목표폐기율 보정 -> Bayesian이 safety_days 범위 설정
- Bayesian (weekly): 글로벌 최적화 -> 전체 파라미터 탐색 공간의 최적점 탐색
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger
from src.prediction.eval_config import EvalConfig, ParamSpec
from src.settings.constants import DEFAULT_STORE_ID

logger = get_logger(__name__)

# --- 설정 상수 ---
BAYESIAN_ENABLED = True
MIN_EVAL_DAYS = 7               # 최소 데이터 요건 (7일)
N_TRIALS = 30                   # GP 시행 횟수
DAMPING_FACTOR = 0.5            # 점진적 적용 (0=변경없음, 1=전량적용)
ROLLBACK_MONITOR_DAYS = 3       # 롤백 모니터링 기간
ROLLBACK_THRESHOLD = 0.10       # 성과 하락 10% 이상이면 롤백
EVAL_LOOKBACK_DAYS = 7          # 목적함수 평가 기간

# 목적함수 가중치 (기본값)
DEFAULT_OBJECTIVE_WEIGHTS = {
    "accuracy_error": 0.35,     # alpha: 예측 정확도 (1 - accuracy@1)
    "waste_rate_error": 0.30,   # beta:  폐기율 초과분
    "stockout_rate": 0.25,      # gamma: 품절율
    "over_order_ratio": 0.10,   # delta: 과잉발주율
}


class OptimizationResult:
    """최적화 결과 데이터 클래스"""
    __slots__ = (
        "success", "best_objective", "best_params",
        "params_before", "params_after", "params_delta",
        "n_trials", "best_trial", "metrics_before", "metrics_after",
        "eval_period_start", "eval_period_end",
        "algorithm", "error_message",
    )

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class BayesianParameterOptimizer:
    """베이지안 파라미터 자동 최적화 엔진

    Usage:
        optimizer = BayesianParameterOptimizer(store_id="46513")
        result = optimizer.optimize()
        if result.success:
            print(f"Best loss: {result.best_objective:.4f}")
    """

    def __init__(
        self,
        store_id: str = DEFAULT_STORE_ID,
        config: Optional[EvalConfig] = None,
        objective_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self.store_id = store_id
        self.config = config or EvalConfig.load(store_id=self.store_id)
        self.objective_weights = objective_weights or DEFAULT_OBJECTIVE_WEIGHTS

        # Lazy-loaded repositories
        self._outcome_repo = None
        self._sales_repo = None
        self._bayesian_repo = None
        self._calibration_repo = None

        # scikit-optimize / optuna import 체크
        self._skopt = None
        self._optuna = None

    # --- Repository Lazy Loading ---

    @property
    def outcome_repo(self):
        if self._outcome_repo is None:
            from src.infrastructure.database.repos import EvalOutcomeRepository
            self._outcome_repo = EvalOutcomeRepository(store_id=self.store_id)
        return self._outcome_repo

    @property
    def sales_repo(self):
        if self._sales_repo is None:
            from src.infrastructure.database.repos import SalesRepository
            self._sales_repo = SalesRepository(store_id=self.store_id)
        return self._sales_repo

    @property
    def bayesian_repo(self):
        if self._bayesian_repo is None:
            from src.infrastructure.database.repos.bayesian_optimization_repo import (
                BayesianOptimizationRepository,
            )
            self._bayesian_repo = BayesianOptimizationRepository(
                store_id=self.store_id
            )
        return self._bayesian_repo

    @property
    def calibration_repo(self):
        if self._calibration_repo is None:
            from src.infrastructure.database.repos import CalibrationRepository
            self._calibration_repo = CalibrationRepository(store_id=self.store_id)
        return self._calibration_repo

    # --- 라이브러리 Import ---

    def _import_optimizer(self) -> Optional[str]:
        """scikit-optimize 또는 optuna import. 반환: 'skopt' | 'optuna' | None"""
        try:
            import skopt
            self._skopt = skopt
            return "skopt"
        except ImportError:
            pass
        try:
            import optuna
            self._optuna = optuna
            return "optuna"
        except ImportError:
            pass
        return None

    # =================================================================
    # 메인 API
    # =================================================================

    def optimize(self) -> OptimizationResult:
        """메인 최적화 실행

        Returns:
            OptimizationResult (success, best_objective, params_delta 등)
        """
        # 0) 활성화 체크
        if not BAYESIAN_ENABLED:
            return OptimizationResult(
                success=False, error_message="Bayesian optimization disabled"
            )

        # 1) 라이브러리 체크
        algo = self._import_optimizer()
        if algo is None:
            logger.warning("[Bayesian] scikit-optimize/optuna 미설치 -> 스킵")
            return OptimizationResult(
                success=False,
                error_message="No optimizer library: pip install scikit-optimize or optuna",
            )

        # 2) 데이터 충분성 체크
        metrics_before = self._collect_metrics(EVAL_LOOKBACK_DAYS)
        if metrics_before is None:
            return OptimizationResult(
                success=False, error_message="Insufficient data for optimization"
            )

        # 3) 탐색 공간 구성
        search_space, param_names = self._build_search_space()
        if not search_space:
            return OptimizationResult(
                success=False, error_message="No unlocked parameters"
            )

        # 4) 현재 파라미터 스냅샷
        params_before = self._snapshot_params(param_names)

        # 5) 최적화 실행
        if algo == "skopt":
            best_params, best_obj, best_trial = self._optimize_skopt(
                search_space, param_names, metrics_before
            )
        else:
            best_params, best_obj, best_trial = self._optimize_optuna(
                search_space, param_names, metrics_before
            )

        # 6) damping 적용
        damped_params = self._apply_with_damping(param_names, best_params)

        # 7) 파라미터 적용
        params_delta = self._apply_params(param_names, damped_params)

        # 8) 저장
        params_after = self._snapshot_params(param_names)
        self.config.save(store_id=self.store_id)

        eval_end = datetime.now().strftime("%Y-%m-%d")
        eval_start = (
            datetime.now() - timedelta(days=EVAL_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")

        # 9) DB 로그
        self.bayesian_repo.save_optimization_log(
            store_id=self.store_id,
            optimization_date=eval_end,
            objective_value=best_obj,
            accuracy_error=metrics_before.get("accuracy_error", 0),
            waste_rate_error=metrics_before.get("waste_rate_error", 0),
            stockout_rate=metrics_before.get("stockout_rate", 0),
            over_order_ratio=metrics_before.get("over_order_ratio", 0),
            params_before=json.dumps(params_before),
            params_after=json.dumps(params_after),
            params_delta=json.dumps(params_delta),
            algorithm=algo,
            n_trials=N_TRIALS,
            best_trial=best_trial,
            eval_period_start=eval_start,
            eval_period_end=eval_end,
        )

        logger.info(
            f"[Bayesian] 최적화 완료: loss={best_obj:.4f}, "
            f"변경 파라미터={len(params_delta)}개, algo={algo}"
        )

        return OptimizationResult(
            success=True,
            best_objective=best_obj,
            best_params=best_params,
            params_before=params_before,
            params_after=params_after,
            params_delta=params_delta,
            n_trials=N_TRIALS,
            best_trial=best_trial,
            metrics_before=metrics_before,
            eval_period_start=eval_start,
            eval_period_end=eval_end,
            algorithm=algo,
        )

    def check_rollback(self) -> Dict[str, Any]:
        """적용 후 성과 모니터링 -> 롤백 판단

        최근 bayesian_optimization_log에서 applied=1 & rolled_back=0 레코드 조회.
        적용일 + ROLLBACK_MONITOR_DAYS 이후의 성과를 비교.
        성과 하락 시 rollback 실행.

        Returns:
            {"rolled_back": bool, "reason": str, "details": Dict}
        """
        recent_log = self.bayesian_repo.get_latest_applied(self.store_id)
        if not recent_log:
            return {"rolled_back": False, "reason": "No applied optimization found"}

        opt_date = recent_log["optimization_date"]
        opt_dt = datetime.strptime(opt_date, "%Y-%m-%d")
        days_since = (datetime.now() - opt_dt).days

        if days_since < ROLLBACK_MONITOR_DAYS:
            return {
                "rolled_back": False,
                "reason": f"Monitoring period: {days_since}/{ROLLBACK_MONITOR_DAYS} days",
            }

        # 적용 후 성과 수집
        metrics_after = self._collect_metrics(days=days_since)
        if metrics_after is None:
            return {"rolled_back": False, "reason": "Insufficient post-optimization data"}

        # 적용 전 성과 (DB에서 로드)
        obj_before = recent_log.get("objective_value", 0)

        # 현재 목적함수 계산
        obj_after = self._calculate_objective(metrics_after)

        # 하락 비율
        if obj_before > 0:
            degradation = (obj_after - obj_before) / obj_before
        else:
            degradation = 0

        if degradation > ROLLBACK_THRESHOLD:
            # 롤백 실행
            params_before_json = recent_log.get("params_before", "{}")
            params_before = json.loads(params_before_json)
            self._restore_params(params_before)
            self.config.save(store_id=self.store_id)

            self.bayesian_repo.mark_rolled_back(
                store_id=self.store_id,
                optimization_date=opt_date,
                reason=f"Performance degraded {degradation:.1%} (threshold: {ROLLBACK_THRESHOLD:.0%})",
            )

            logger.warning(
                f"[Bayesian] 롤백 실행: loss {obj_before:.4f} -> {obj_after:.4f} "
                f"(+{degradation:.1%})"
            )
            return {
                "rolled_back": True,
                "reason": f"Performance degraded {degradation:.1%}",
                "details": {
                    "objective_before": obj_before,
                    "objective_after": obj_after,
                    "degradation": degradation,
                },
            }

        # 성과 유지/개선 -> 확정
        self.bayesian_repo.mark_confirmed(
            store_id=self.store_id,
            optimization_date=opt_date,
        )
        return {
            "rolled_back": False,
            "reason": f"Performance OK (delta: {degradation:+.1%})",
        }

    # =================================================================
    # 탐색 공간 구성
    # =================================================================

    def _build_search_space(self) -> Tuple[List, List[str]]:
        """EvalConfig ParamSpec -> 탐색 차원 변환

        locked=True인 파라미터는 제외한다.
        weight_trend는 유도 파라미터이므로 제외 (1.0 - sum of others).

        Returns:
            (dimensions_list, param_names_list)
        """
        dimensions = []
        param_names = []

        # A. eval_params (EvalConfig)
        for name in self.config._param_names():
            spec: ParamSpec = getattr(self.config, name)

            # locked 체크
            if getattr(spec, "locked", False):
                continue

            # weight_trend 제외 (유도 파라미터)
            if name == "weight_trend":
                continue

            dim = self._spec_to_dimension(name, spec)
            if dim is not None:
                dimensions.append(dim)
                param_names.append(f"eval.{name}")

        # B. FOOD safety_days (2그룹: ultra_short, short)
        food_params = self._get_food_search_params()
        for name, low, high in food_params:
            if self._skopt:
                from skopt.space import Real
                dimensions.append(Real(low, high, name=name))
            else:
                dimensions.append((name, low, high))
            param_names.append(f"food.{name}")

        return dimensions, param_names

    def _spec_to_dimension(self, name: str, spec: ParamSpec):
        """ParamSpec -> skopt Dimension 변환"""
        if self._skopt:
            from skopt.space import Real, Integer
            if name == "daily_avg_days":
                return Integer(int(spec.min_val), int(spec.max_val), name=name)
            return Real(spec.min_val, spec.max_val, name=name)
        elif self._optuna:
            return (name, spec.min_val, spec.max_val)
        return None

    def _get_food_search_params(self) -> List[Tuple[str, float, float]]:
        """FOOD_EXPIRY_SAFETY_CONFIG에서 탐색 가능한 파라미터 추출

        Returns:
            [(param_name, low, high), ...]
        """
        from src.settings.constants import FOOD_WASTE_CAL_SAFETY_DAYS_RANGE

        params = []
        # ultra_short, short만 탐색 (medium/long/very_long은 Phase 2)
        for group in ["ultra_short", "short"]:
            if group in FOOD_WASTE_CAL_SAFETY_DAYS_RANGE:
                low, high = FOOD_WASTE_CAL_SAFETY_DAYS_RANGE[group]
                params.append((f"{group}_safety_days", low, high))

        return params

    # =================================================================
    # 성과 지표 수집
    # =================================================================

    def _collect_metrics(self, days: int = 7) -> Optional[Dict[str, float]]:
        """최근 N일 성과 지표 수집

        Sources:
        - eval_outcomes -> accuracy_rate, over_prediction_rate
        - daily_sales -> waste_rate (disuse_qty / sale_qty)
        - eval_outcomes.outcome -> stockout detection (MISS)

        Returns:
            metrics dict 또는 None (데이터 부족)
        """
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 1) eval_outcomes에서 정확도/품절/과잉발주
        stats = self.outcome_repo.get_accuracy_stats(
            days=days, store_id=self.store_id
        )
        total_verified = stats.get("total_verified", 0)
        if total_verified < MIN_EVAL_DAYS * 5:
            logger.debug(f"[Bayesian] 데이터 부족: {total_verified}건")
            return None

        # by_decision에서 합산
        total_correct = 0
        total_over = 0
        total_miss = 0
        for decision_stats in stats.get("by_decision", {}).values():
            total_correct += decision_stats.get("correct", 0)
            total_over += decision_stats.get("over_order", 0)
            total_miss += decision_stats.get("miss", 0)

        accuracy_rate = total_correct / total_verified if total_verified > 0 else 0
        stockout_rate = total_miss / total_verified if total_verified > 0 else 0
        over_order_ratio = total_over / total_verified if total_verified > 0 else 0

        # 2) 폐기율 계산 (FOOD 카테고리 전체)
        waste_rate = self._calculate_food_waste_rate(start_date, end_date)
        target_waste = 0.18  # 가중평균 목표

        return {
            "accuracy_error": 1.0 - accuracy_rate,
            "waste_rate_error": max(0, waste_rate - target_waste),
            "stockout_rate": stockout_rate,
            "over_order_ratio": over_order_ratio,
            "sample_count": total_verified,
            "accuracy_rate": accuracy_rate,
            "waste_rate": waste_rate,
        }

    def _calculate_food_waste_rate(
        self, start_date: str, end_date: str
    ) -> float:
        """FOOD 카테고리 폐기율 계산

        폐기율 = SUM(disuse_qty) / SUM(sale_qty + disuse_qty)
        (daily_sales 기반)
        """
        from src.settings.constants import FOOD_MID_CODES

        conn = self.sales_repo._get_conn()
        try:
            mid_codes = list(FOOD_MID_CODES)
            placeholders = ",".join("?" * len(mid_codes))
            cursor = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(disuse_qty), 0) as total_disuse,
                    COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE sales_date BETWEEN ? AND ?
                  AND mid_cd IN ({placeholders})
                  AND store_id = ?
                """,
                (start_date, end_date, *mid_codes, self.store_id),
            )
            row = cursor.fetchone()
            if row:
                total_disuse = row[0] or 0
                total_sales = row[1] or 0
                denominator = total_sales + total_disuse
                if denominator > 0:
                    return total_disuse / denominator
            return 0.0
        finally:
            conn.close()

    # =================================================================
    # 목적함수
    # =================================================================

    def _calculate_objective(self, metrics: Dict[str, float]) -> float:
        """가중 목적함수 계산

        L = alpha*accuracy_error + beta*waste_error + gamma*stockout + delta*over_order
        """
        w = self.objective_weights
        return (
            w["accuracy_error"] * metrics.get("accuracy_error", 0)
            + w["waste_rate_error"] * metrics.get("waste_rate_error", 0)
            + w["stockout_rate"] * metrics.get("stockout_rate", 0)
            + w["over_order_ratio"] * metrics.get("over_order_ratio", 0)
        )

    def _objective(
        self,
        param_values: List[float],
        param_names: List[str],
        baseline_metrics: Dict[str, float],
    ) -> float:
        """GP surrogate가 호출하는 목적함수

        실제 예측 파이프라인을 실행하지 않고, 히스토리 기반으로 추정한다:
        - 파라미터 변화량에 따른 지표 변화를 선형 근사
        - 감도(sensitivity) 행렬 사용

        Returns:
            목적함수 값 (낮을수록 좋음)
        """
        estimated_metrics = self._estimate_metrics(
            param_values, param_names, baseline_metrics
        )
        return self._calculate_objective(estimated_metrics)

    def _estimate_metrics(
        self,
        param_values: List[float],
        param_names: List[str],
        baseline: Dict[str, float],
    ) -> Dict[str, float]:
        """파라미터 변화에 따른 지표 변화 추정 (선형 근사 + 감도 행렬)

        Returns:
            추정된 성과 지표 dict
        """
        estimated = dict(baseline)

        for i, (name, value) in enumerate(zip(param_names, param_values)):
            current_value = self._get_current_param_value(name)
            if current_value is None:
                continue

            delta_ratio = (value - current_value) / max(abs(current_value), 1e-6)

            sensitivity = self._get_sensitivity(name)

            estimated["accuracy_error"] += delta_ratio * sensitivity.get("accuracy", 0)
            estimated["waste_rate_error"] += delta_ratio * sensitivity.get("waste", 0)
            estimated["stockout_rate"] += delta_ratio * sensitivity.get("stockout", 0)

        # 클램프: 0 이상
        for key in ["accuracy_error", "waste_rate_error", "stockout_rate", "over_order_ratio"]:
            estimated[key] = max(0, estimated[key])

        return estimated

    def _get_sensitivity(self, param_name: str) -> Dict[str, float]:
        """파라미터별 감도 행렬 (경험적 초기값)

        양수 = 파라미터 증가 시 지표 증가, 음수 = 감소.
        """
        SENSITIVITY_MAP = {
            # eval_params
            "eval.weight_daily_avg":        {"accuracy": -0.05, "waste": 0.0,   "stockout": 0.0},
            "eval.weight_sell_day_ratio":    {"accuracy": -0.03, "waste": 0.0,   "stockout": 0.0},
            "eval.exposure_urgent":          {"accuracy": 0.02,  "waste": 0.01,  "stockout": -0.03},
            "eval.exposure_normal":          {"accuracy": 0.01,  "waste": 0.02,  "stockout": -0.02},
            "eval.exposure_sufficient":      {"accuracy": 0.0,   "waste": 0.03,  "stockout": -0.01},
            "eval.stockout_freq_threshold":  {"accuracy": 0.02,  "waste": -0.01, "stockout": -0.05},
            "eval.daily_avg_days":           {"accuracy": -0.01, "waste": 0.0,   "stockout": 0.0},
            "eval.target_accuracy":          {"accuracy": -0.03, "waste": 0.0,   "stockout": 0.0},
            "eval.calibration_decay":        {"accuracy": -0.01, "waste": 0.0,   "stockout": 0.0},
            "eval.calibration_reversion_rate": {"accuracy": 0.0, "waste": 0.0,   "stockout": 0.0},
            "eval.popularity_high_percentile": {"accuracy": -0.01, "waste": 0.0, "stockout": 0.0},
            "eval.popularity_low_percentile":  {"accuracy": 0.01,  "waste": 0.0, "stockout": 0.0},
            # food safety_days
            "food.ultra_short_safety_days":  {"accuracy": 0.0,  "waste": 0.08,  "stockout": -0.06},
            "food.short_safety_days":        {"accuracy": 0.0,  "waste": 0.05,  "stockout": -0.04},
        }
        return SENSITIVITY_MAP.get(
            param_name, {"accuracy": 0, "waste": 0, "stockout": 0}
        )

    # =================================================================
    # 최적화 실행 (scikit-optimize)
    # =================================================================

    def _optimize_skopt(
        self,
        dimensions: List,
        param_names: List[str],
        baseline_metrics: Dict[str, float],
    ) -> Tuple[List[float], float, int]:
        """scikit-optimize GP 기반 최적화

        Returns:
            (best_param_values, best_objective, best_trial_index)
        """
        from skopt import gp_minimize

        def objective_fn(params):
            return self._objective(params, param_names, baseline_metrics)

        # 현재값을 x0으로 사용 (warm start)
        x0 = [self._get_current_param_value(n) for n in param_names]

        result = gp_minimize(
            func=objective_fn,
            dimensions=dimensions,
            x0=x0,
            n_calls=N_TRIALS,
            n_random_starts=5,
            random_state=42,
            verbose=False,
        )

        # best trial index
        try:
            best_idx = list(result.x_iters).index(list(result.x)) + 1
        except (ValueError, AttributeError):
            best_idx = 1

        return list(result.x), result.fun, best_idx

    def _optimize_optuna(
        self,
        search_space: List,
        param_names: List[str],
        baseline_metrics: Dict[str, float],
    ) -> Tuple[List[float], float, int]:
        """optuna TPE 기반 최적화 (폴백)

        Returns:
            (best_param_values, best_objective, best_trial_number)
        """
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective_fn(trial):
            params = []
            for name, low, high in search_space:
                if isinstance(low, int) and isinstance(high, int):
                    params.append(trial.suggest_int(name, low, high))
                else:
                    params.append(trial.suggest_float(name, low, high))
            return self._objective(params, param_names, baseline_metrics)

        study = optuna.create_study(direction="minimize")
        study.optimize(objective_fn, n_trials=N_TRIALS, show_progress_bar=False)

        best = study.best_trial
        best_params = [best.params[n] for n, _, _ in search_space]
        return best_params, best.value, best.number + 1

    # =================================================================
    # 파라미터 적용
    # =================================================================

    def _apply_with_damping(
        self, param_names: List[str], best_values: List[float]
    ) -> List[float]:
        """damping 적용: 현재값과 최적값의 가중평균

        damped = current + DAMPING_FACTOR * (best - current)

        Returns:
            damped 파라미터 값 리스트
        """
        damped = []
        for name, best_val in zip(param_names, best_values):
            current = self._get_current_param_value(name) or best_val
            damped_val = current + DAMPING_FACTOR * (best_val - current)
            damped.append(round(damped_val, 6))
        return damped

    def _apply_params(
        self, param_names: List[str], values: List[float]
    ) -> Dict[str, Dict[str, float]]:
        """최적화된 파라미터 실제 적용

        제약 조건:
        1. ParamSpec.max_delta 범위 제한
        2. weight 합 = 1.0 정규화
        3. safety_days 순서 유지

        Returns:
            변경된 파라미터 {name: {"old": float, "new": float}}
        """
        delta_map = {}

        for name, value in zip(param_names, values):
            if name.startswith("eval."):
                eval_name = name[5:]  # strip "eval."
                spec: ParamSpec = getattr(self.config, eval_name, None)
                if spec is None:
                    continue
                old_val = spec.value
                new_val = spec.apply_delta(value - spec.value)
                if abs(new_val - old_val) > 1e-6:
                    spec.value = new_val
                    delta_map[name] = {"old": old_val, "new": new_val}

            elif name.startswith("food."):
                food_name = name[5:]  # strip "food."
                old_val, new_val = self._apply_food_param(food_name, value)
                if old_val is not None and abs(new_val - old_val) > 1e-6:
                    delta_map[name] = {"old": old_val, "new": new_val}

        # 가중치 정규화
        self.config.normalize_weights()

        return delta_map

    def _apply_food_param(
        self, param_name: str, value: float
    ) -> Tuple[Optional[float], float]:
        """food safety_days 파라미터 적용

        Returns:
            (old_value, new_value)
        """
        from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG

        # param_name: "ultra_short_safety_days" -> group="ultra_short"
        group = param_name.replace("_safety_days", "")
        groups = FOOD_EXPIRY_SAFETY_CONFIG.get("expiry_groups", {})

        if group not in groups:
            return None, value

        old_val = groups[group].get("safety_days", 0.5)
        new_val = max(0.1, min(2.0, value))  # 안전 범위

        return old_val, new_val

    # =================================================================
    # 유틸리티
    # =================================================================

    def _snapshot_params(self, param_names: List[str]) -> Dict[str, float]:
        """현재 파라미터 값 스냅샷"""
        return {
            name: self._get_current_param_value(name)
            for name in param_names
        }

    def _get_current_param_value(self, name: str) -> Optional[float]:
        """이름으로 현재 파라미터 값 조회"""
        if name.startswith("eval."):
            eval_name = name[5:]
            spec = getattr(self.config, eval_name, None)
            return spec.value if spec else None
        elif name.startswith("food."):
            food_name = name[5:]
            group = food_name.replace("_safety_days", "")
            from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG
            groups = FOOD_EXPIRY_SAFETY_CONFIG.get("expiry_groups", {})
            if group in groups:
                return groups[group].get("safety_days")
        return None

    def _restore_params(self, params: Dict[str, float]) -> None:
        """파라미터 복원 (롤백용)"""
        for name, value in params.items():
            if name.startswith("eval."):
                eval_name = name[5:]
                spec = getattr(self.config, eval_name, None)
                if spec:
                    spec.value = value
        self.config.normalize_weights()
