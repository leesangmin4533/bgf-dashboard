# Design: bayesian-param-optimizer

> **Feature**: bayesian-param-optimizer
> **Plan Reference**: docs/01-plan/features/bayesian-param-optimizer.plan.md
> **Created**: 2026-02-24
> **Status**: Draft

---

## 1. 파일 구조

```
src/
  prediction/
    bayesian_optimizer.py                 # [NEW] 핵심 최적화 엔진
    eval_config.py                        # [MOD] ParamSpec.locked 필드 추가
  infrastructure/
    database/
      repos/
        bayesian_optimization_repo.py     # [NEW] DB 저장소
        __init__.py                       # [MOD] export 추가
  application/
    daily_job.py                          # [MOD] Phase 1.57 추가
  db/
    models.py                             # [MOD] v40 마이그레이션 추가
  settings/
    constants.py                          # [MOD] DB_SCHEMA_VERSION=40, 설정 상수
  web/
    routes/
      api_prediction.py                   # [MOD] 대시보드 API (Phase 6)
config/
  eval_params.default.json                # [MOD] locked 필드 추가
  bayesian_config.json                    # [NEW] 최적화 설정 (목적함수 가중치 등)
run_scheduler.py                          # [MOD] 주간 스케줄 추가
tests/
  test_bayesian_optimizer.py              # [NEW] 단위/통합 테스트
  conftest.py                             # [MOD] bayesian_optimization_log 테이블 추가
```

## 2. 모듈 상세 설계

### 2-1. `src/prediction/bayesian_optimizer.py` (핵심 엔진)

```python
"""
베이지안 파라미터 자동 최적화 엔진

주 1회 실행 (Phase 1.57, 일요일 23:00):
1. 최근 7일 eval_outcomes + daily_sales 성과 지표 수집
2. 현재 파라미터 → 탐색 공간 구성 (ParamSpec 기반)
3. GP/TPE surrogate 기반 목적함수 최소화 (30 trials)
4. damping 적용 후 eval_params.json + food_waste_calibration 반영
5. DB에 최적화 이력 저장
6. 적용 후 3일 모니터링 → 성과 하락 시 자동 롤백

기존 보정기와의 관계:
- EvalCalibrator (daily): 미세조정 → Bayesian 결과를 기반값으로 사용
- FoodWasteRateCalibrator (daily): 목표폐기율 보정 → Bayesian이 safety_days 범위 설정
- Bayesian (weekly): 글로벌 최적화 → 전체 파라미터 탐색 공간의 최적점 탐색
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger
from src.prediction.eval_config import EvalConfig, ParamSpec
from src.settings.constants import DEFAULT_STORE_ID

logger = get_logger(__name__)

# ─── 설정 상수 ───────────────────────────────────────────────
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

    # ─── Repository Lazy Loading ──────────────────────────────

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

    # ─── 라이브러리 Import ────────────────────────────────────

    def _import_optimizer(self) -> str:
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

    # ═══════════════════════════════════════════════════════════
    # 메인 API
    # ═══════════════════════════════════════════════════════════

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
            logger.warning("[Bayesian] scikit-optimize/optuna 미설치 → 스킵")
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
        """적용 후 성과 모니터링 → 롤백 판단

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
                f"[Bayesian] 롤백 실행: loss {obj_before:.4f} → {obj_after:.4f} "
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

        # 성과 유지/개선 → 확정
        self.bayesian_repo.mark_confirmed(
            store_id=self.store_id,
            optimization_date=opt_date,
        )
        return {
            "rolled_back": False,
            "reason": f"Performance OK (delta: {degradation:+.1%})",
        }

    # ═══════════════════════════════════════════════════════════
    # 탐색 공간 구성
    # ═══════════════════════════════════════════════════════════

    def _build_search_space(self) -> Tuple[List, List[str]]:
        """EvalConfig ParamSpec → 탐색 차원 변환

        locked=True인 파라미터는 제외한다.
        가중치 파라미터(weight_*)는 3개 중 2개만 탐색 (나머지는 1.0 - sum)

        Returns:
            (dimensions_list, param_names_list)
        """
        dimensions = []
        param_names = []

        # A. eval_params (EvalConfig)
        weight_params = ["weight_daily_avg", "weight_sell_day_ratio"]
        # weight_trend는 1.0 - (w1 + w2)로 유도 → 탐색 안 함

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

        # B. FOOD safety_days (5그룹)
        food_params = self._get_food_search_params()
        for name, low, high in food_params:
            if self._skopt:
                from skopt.space import Real
                dimensions.append(Real(low, high, name=name))
            else:
                dimensions.append((name, low, high))
            param_names.append(f"food.{name}")

        # C. PREDICTION_PARAMS (선택적 — Phase 2)
        # 현재는 eval_params + food safety만 탐색

        return dimensions, param_names

    def _spec_to_dimension(self, name: str, spec: ParamSpec):
        """ParamSpec → skopt Dimension 변환"""
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

        FoodWasteRateCalibrator의 안전 범위를 참조한다.

        Returns:
            [(param_name, low, high), ...]
        """
        from src.settings.constants import (
            FOOD_WASTE_CAL_SAFETY_DAYS_RANGE,
        )

        params = []
        # ultra_short, short만 탐색 (medium/long/very_long은 Phase 2)
        for group in ["ultra_short", "short"]:
            if group in FOOD_WASTE_CAL_SAFETY_DAYS_RANGE:
                low, high = FOOD_WASTE_CAL_SAFETY_DAYS_RANGE[group]
                params.append((f"{group}_safety_days", low, high))

        return params

    # ═══════════════════════════════════════════════════════════
    # 성과 지표 수집
    # ═══════════════════════════════════════════════════════════

    def _collect_metrics(self, days: int = 7) -> Optional[Dict[str, float]]:
        """최근 N일 성과 지표 수집

        Sources:
        - eval_outcomes → accuracy_rate, over_prediction_rate
        - daily_sales → waste_rate (disuse_qty / sale_qty)
        - eval_outcomes.outcome → stockout detection (was_stockout=1)

        Returns:
            {
                "accuracy_error": float,     # 1.0 - accuracy@1
                "waste_rate_error": float,    # actual_waste - target_waste
                "stockout_rate": float,       # 품절 비율
                "over_order_ratio": float,    # 과잉발주 비율
                "sample_count": int,          # 데이터 건수
            }
            또는 None (데이터 부족)
        """
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 1) eval_outcomes에서 정확도/품절/과잉발주
        stats = self.outcome_repo.get_accuracy_stats(days=days)
        if not stats or stats.get("total", 0) < MIN_EVAL_DAYS * 5:
            logger.debug(f"[Bayesian] 데이터 부족: {stats.get('total', 0)}건")
            return None

        total = stats["total"]
        correct = stats.get("correct", 0)
        over = stats.get("over_order", 0)
        stockout_count = stats.get("miss", 0)  # was_stockout based

        accuracy_rate = correct / total if total > 0 else 0
        stockout_rate = stockout_count / total if total > 0 else 0
        over_order_ratio = over / total if total > 0 else 0

        # 2) 폐기율 계산 (FOOD 카테고리 전체)
        waste_rate = self._calculate_food_waste_rate(start_date, end_date)
        target_waste = 0.18  # 가중평균 목표 (FOOD_WASTE_RATE_TARGETS의 대략적 평균)

        return {
            "accuracy_error": 1.0 - accuracy_rate,
            "waste_rate_error": max(0, waste_rate - target_waste),
            "stockout_rate": stockout_rate,
            "over_order_ratio": over_order_ratio,
            "sample_count": total,
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
            cursor = conn.execute(
                """
                SELECT
                    COALESCE(SUM(disuse_qty), 0) as total_disuse,
                    COALESCE(SUM(sale_qty), 0) as total_sales
                FROM daily_sales
                WHERE sales_date BETWEEN ? AND ?
                  AND mid_cd IN ({})
                  AND store_id = ?
                """.format(",".join("?" * len(FOOD_MID_CODES))),
                (start_date, end_date, *FOOD_MID_CODES, self.store_id),
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

    # ═══════════════════════════════════════════════════════════
    # 목적함수
    # ═══════════════════════════════════════════════════════════

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
        - eval_outcomes 히스토리에서 유사 파라미터 구간의 성과를 참조

        Args:
            param_values: 후보 파라미터 값 리스트
            param_names: 파라미터 이름 리스트 (eval.XX / food.XX)
            baseline_metrics: 현재 성과 지표

        Returns:
            목적함수 값 (낮을수록 좋음)
        """
        # 파라미터 변화 → 지표 변화 추정
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
        """파라미터 변화에 따른 지표 변화 추정 (선형 근사 + 히스토리 보정)

        핵심 로직:
        1. 각 파라미터의 변화 방향/크기 파악
        2. calibration_history에서 유사 변화가 있었던 시점의 성과 변화율 참조
        3. 참조 없으면 경험적 감도(sensitivity) 행렬 사용

        Returns:
            추정된 성과 지표 dict
        """
        estimated = dict(baseline)

        for i, (name, value) in enumerate(zip(param_names, param_values)):
            current_value = self._get_current_param_value(name)
            if current_value is None:
                continue

            delta_ratio = (value - current_value) / max(abs(current_value), 1e-6)

            # 감도 행렬 기반 추정
            sensitivity = self._get_sensitivity(name)

            # accuracy_error: 가중치/노출 변경 → 정확도 영향
            estimated["accuracy_error"] += delta_ratio * sensitivity.get(
                "accuracy", 0
            )
            # waste_rate: safety_days 증가 → 폐기율 증가
            estimated["waste_rate_error"] += delta_ratio * sensitivity.get(
                "waste", 0
            )
            # stockout: safety_days 감소 → 품절 증가
            estimated["stockout_rate"] += delta_ratio * sensitivity.get(
                "stockout", 0
            )

        # 클램프: 0 이상
        for key in ["accuracy_error", "waste_rate_error", "stockout_rate", "over_order_ratio"]:
            estimated[key] = max(0, estimated[key])

        return estimated

    def _get_sensitivity(self, param_name: str) -> Dict[str, float]:
        """파라미터별 감도 행렬 (경험적 초기값)

        향후 calibration_history 분석으로 자동 업데이트 가능.
        양수 = 파라미터 증가 시 지표 증가, 음수 = 감소.

        Returns:
            {"accuracy": float, "waste": float, "stockout": float}
        """
        SENSITIVITY_MAP = {
            # eval_params
            "eval.weight_daily_avg":       {"accuracy": -0.05, "waste": 0.0,   "stockout": 0.0},
            "eval.weight_sell_day_ratio":   {"accuracy": -0.03, "waste": 0.0,   "stockout": 0.0},
            "eval.exposure_urgent":         {"accuracy": 0.02,  "waste": 0.01,  "stockout": -0.03},
            "eval.exposure_normal":         {"accuracy": 0.01,  "waste": 0.02,  "stockout": -0.02},
            "eval.exposure_sufficient":     {"accuracy": 0.0,   "waste": 0.03,  "stockout": -0.01},
            "eval.stockout_freq_threshold": {"accuracy": 0.02,  "waste": -0.01, "stockout": -0.05},
            "eval.daily_avg_days":          {"accuracy": -0.01, "waste": 0.0,   "stockout": 0.0},
            "eval.target_accuracy":         {"accuracy": -0.03, "waste": 0.0,   "stockout": 0.0},
            "eval.calibration_decay":       {"accuracy": -0.01, "waste": 0.0,   "stockout": 0.0},
            # food safety_days
            "food.ultra_short_safety_days": {"accuracy": 0.0, "waste": 0.08,  "stockout": -0.06},
            "food.short_safety_days":       {"accuracy": 0.0, "waste": 0.05,  "stockout": -0.04},
        }
        return SENSITIVITY_MAP.get(param_name, {"accuracy": 0, "waste": 0, "stockout": 0})

    # ═══════════════════════════════════════════════════════════
    # 최적화 실행 (scikit-optimize)
    # ═══════════════════════════════════════════════════════════

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

        return result.x, result.fun, int(result.x_iters.index(result.x)) + 1

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

    # ═══════════════════════════════════════════════════════════
    # 파라미터 적용
    # ═══════════════════════════════════════════════════════════

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

        food_waste_calibration DB에 직접 저장한다.

        Returns:
            (old_value, new_value)
        """
        from src.prediction.categories.food import (
            get_calibrated_food_params,
            FOOD_EXPIRY_SAFETY_CONFIG,
        )

        # param_name: "ultra_short_safety_days" → group="ultra_short"
        group = param_name.replace("_safety_days", "")
        groups = FOOD_EXPIRY_SAFETY_CONFIG.get("expiry_groups", {})

        if group not in groups:
            return None, value

        old_val = groups[group].get("safety_days", 0.5)
        new_val = max(0.1, min(2.0, value))  # 안전 범위

        # DB 업데이트 (모든 FOOD 카테고리에 적용)
        # 실제 적용은 FoodWasteRateCalibrator가 DB에서 읽어서 처리
        return old_val, new_val

    # ═══════════════════════════════════════════════════════════
    # 유틸리티
    # ═══════════════════════════════════════════════════════════

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
```

### 2-2. `src/infrastructure/database/repos/bayesian_optimization_repo.py`

```python
"""
BayesianOptimizationRepository — 베이지안 최적화 이력 저장소

DB: 매장별 store DB (db_type="store")
테이블: bayesian_optimization_log
"""

from typing import Any, Dict, List, Optional

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BayesianOptimizationRepository(BaseRepository):
    """베이지안 최적화 이력 저장소"""

    db_type = "store"

    def save_optimization_log(
        self,
        store_id: str,
        optimization_date: str,
        objective_value: float,
        accuracy_error: float,
        waste_rate_error: float,
        stockout_rate: float,
        over_order_ratio: float,
        params_before: str,         # JSON
        params_after: str,          # JSON
        params_delta: str,          # JSON
        algorithm: str,
        n_trials: int,
        best_trial: int,
        eval_period_start: str,
        eval_period_end: str,
    ) -> int:
        """최적화 결과 저장

        Returns:
            저장된 row ID
        """
        ...

    def get_latest_applied(self, store_id: str) -> Optional[Dict[str, Any]]:
        """가장 최근 적용된(applied=1, rolled_back=0) 최적화 기록 조회"""
        ...

    def mark_applied(self, store_id: str, optimization_date: str) -> None:
        """applied = 1로 업데이트"""
        ...

    def mark_rolled_back(
        self, store_id: str, optimization_date: str, reason: str
    ) -> None:
        """rolled_back = 1, rollback_reason 업데이트"""
        ...

    def mark_confirmed(self, store_id: str, optimization_date: str) -> None:
        """롤백 모니터링 통과 확인 (추가 컬럼 또는 로그)"""
        ...

    def get_optimization_history(
        self, store_id: str, days: int = 90
    ) -> List[Dict[str, Any]]:
        """최근 N일 최적화 이력 조회 (대시보드용)"""
        ...

    def get_param_evolution(
        self, store_id: str, param_name: str, days: int = 90
    ) -> List[Dict[str, Any]]:
        """특정 파라미터의 최적화 변화 추이"""
        ...
```

### 2-3. `src/prediction/eval_config.py` 변경사항

```python
@dataclass
class ParamSpec:
    """파라미터 정의 (현재값, 기본값, 허용 범위, 1회 최대 변경폭)"""
    value: float
    default: float
    min_val: float
    max_val: float
    max_delta: float
    description: str = ""
    locked: bool = False  # [NEW] True면 Bayesian 최적화에서 제외
```

**JSON 직렬화/역직렬화에 locked 필드 추가:**
- `to_dict()`: `"locked": spec.locked` 추가
- `_apply_params()`: `spec.locked = data[name].get("locked", False)` 추가

### 2-4. DB 스키마 변경 (`src/db/models.py`)

```python
# constants.py
DB_SCHEMA_VERSION = 40  # v40: bayesian_optimization_log

# models.py SCHEMA_MIGRATIONS
40: """
CREATE TABLE IF NOT EXISTS bayesian_optimization_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    optimization_date TEXT NOT NULL,
    iteration INTEGER DEFAULT 0,
    objective_value REAL,
    accuracy_error REAL,
    waste_rate_error REAL,
    stockout_rate REAL,
    over_order_ratio REAL,
    params_before TEXT,
    params_after TEXT,
    params_delta TEXT,
    algorithm TEXT DEFAULT 'gp',
    n_trials INTEGER,
    best_trial INTEGER,
    eval_period_start TEXT,
    eval_period_end TEXT,
    applied INTEGER DEFAULT 0,
    rolled_back INTEGER DEFAULT 0,
    rollback_reason TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(store_id, optimization_date)
);

CREATE INDEX IF NOT EXISTS idx_bayesian_log_store_date
    ON bayesian_optimization_log(store_id, optimization_date);
""",
```

### 2-5. `src/application/daily_job.py` Phase 1.57 추가

```python
# Phase 1.56 뒤에 추가:

# ── Phase 1.57: Bayesian Parameter Optimization (주 1회) ──
if datetime.now().weekday() == 6:  # 일요일만
    try:
        logger.info("[Phase 1.57] Bayesian Parameter Optimization")
        from src.prediction.bayesian_optimizer import (
            BayesianParameterOptimizer,
            BAYESIAN_ENABLED,
        )
        if BAYESIAN_ENABLED:
            bayesian_opt = BayesianParameterOptimizer(store_id=self.store_id)

            # 롤백 체크 (이전 최적화 결과 모니터링)
            rollback_result = bayesian_opt.check_rollback()
            if rollback_result.get("rolled_back"):
                logger.warning(
                    f"[Phase 1.57] 이전 최적화 롤백: {rollback_result['reason']}"
                )
            else:
                # 새 최적화 실행
                opt_result = bayesian_opt.optimize()
                if opt_result.success:
                    logger.info(
                        f"[Phase 1.57] 최적화 완료: "
                        f"loss={opt_result.best_objective:.4f}, "
                        f"변경={len(opt_result.params_delta)}개"
                    )
                else:
                    logger.info(
                        f"[Phase 1.57] 최적화 스킵: {opt_result.error_message}"
                    )
        else:
            logger.debug("[Phase 1.57] Bayesian optimization disabled")
    except Exception as e:
        logger.warning(f"[Phase 1.57] Bayesian optimization 실패: {e}")
```

### 2-6. `run_scheduler.py` 주간 스케줄 추가

```python
# 기존 스케줄에 추가 (주간):
# schedule.every().sunday.at("23:00").do(bayesian_optimize_wrapper)

def bayesian_optimize_wrapper() -> None:
    """주간 베이지안 최적화 (일요일 23:00)"""
    logger.info("=" * 60)
    logger.info(f"Bayesian Optimization at {datetime.now().isoformat()}")

    def task(ctx):
        from src.prediction.bayesian_optimizer import (
            BayesianParameterOptimizer,
            BAYESIAN_ENABLED,
        )
        if not BAYESIAN_ENABLED:
            return {"skipped": True, "reason": "disabled"}

        optimizer = BayesianParameterOptimizer(store_id=ctx.store_id)
        result = optimizer.optimize()
        return result.to_dict()

    _run_task(task, "BayesianOptimize")

# CLI: --bayesian-optimize
parser.add_argument(
    "--bayesian-optimize",
    action="store_true",
    help="Run Bayesian parameter optimization",
)
```

### 2-7. `config/bayesian_config.json` (최적화 설정)

```json
{
  "enabled": true,
  "n_trials": 30,
  "damping_factor": 0.5,
  "rollback_monitor_days": 3,
  "rollback_threshold": 0.10,
  "eval_lookback_days": 7,
  "min_eval_days": 7,
  "objective_weights": {
    "accuracy_error": 0.35,
    "waste_rate_error": 0.30,
    "stockout_rate": 0.25,
    "over_order_ratio": 0.10
  },
  "locked_params": [],
  "preferred_algorithm": "skopt"
}
```

## 3. 데이터 흐름도

```
                    ┌──────────────────┐
                    │ eval_outcomes    │ (7일)
                    │ daily_sales      │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ _collect_metrics │
                    │ accuracy, waste  │
                    │ stockout, over   │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ _build_search_   │
                    │    space         │
                    │ EvalConfig →     │
                    │ skopt.Real/Int   │
                    └────────┬─────────┘
                             │
              ┌──────────────▼──────────────┐
              │    GP/TPE Surrogate Model    │
              │    gp_minimize() (30 trials) │
              │                              │
              │ Trial 1 → _objective() → L₁  │
              │ Trial 2 → _objective() → L₂  │
              │   ...                        │
              │ Trial 30 → _objective() → L₃₀│
              │                              │
              │ → best_params (min loss)     │
              └──────────────┬──────────────┘
                             │
                    ┌────────▼─────────┐
                    │ _apply_with_     │
                    │   damping        │
                    │ x_new = x_old +  │
                    │ 0.5*(best-x_old) │
                    └────────┬─────────┘
                             │
              ┌──────────────▼──────────────┐
              │ _apply_params               │
              │ ├─ max_delta 클램핑          │
              │ ├─ normalize_weights()      │
              │ └─ safety_days 순서 검증    │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │ EvalConfig.save()           │
              │ ├─ eval_params.json         │
              │ └─ DB (store_eval_params)   │
              │                             │
              │ bayesian_repo.save_log()    │
              │ └─ bayesian_optimization_log│
              └──────────────┬──────────────┘
                             │
                    (3일 후 check_rollback)
                             │
              ┌──────────────▼──────────────┐
              │ 성과 비교                    │
              │ loss_after vs loss_before    │
              │ > 10% 하락 → 롤백           │
              │ ≤ 10% → 확정                │
              └─────────────────────────────┘
```

## 4. 기존 보정기 계층 관계

```
┌─────────────────────────────────────────────────────┐
│              Weekly: Bayesian Optimizer              │
│  글로벌 탐색 → eval_params.json 업데이트            │
│  Phase 1.57 (일요일)                                │
├─────────────────────────────────────────────────────┤
│              Daily: EvalCalibrator                   │
│  Pearson 상관 기반 미세조정 (가중치 3개)            │
│  Phase 1.5 (매일)                                   │
│  ★ Bayesian 출력을 기반값(base)으로 사용            │
├─────────────────────────────────────────────────────┤
│              Daily: FoodWasteRateCalibrator          │
│  목표 폐기율 기반 safety_days/gap_coef 조정         │
│  Phase 1.56 (매일)                                  │
│  ★ Bayesian이 safety_days 범위를 설정               │
├─────────────────────────────────────────────────────┤
│              Per-Item: DiffFeedback                  │
│  제거 패턴 기반 감소 (고정 계수)                    │
│  예측 파이프라인 12-1단계                           │
│  ★ Bayesian Phase 2에서 계수 최적화 예정            │
└─────────────────────────────────────────────────────┘
```

**충돌 방지 메커니즘:**
1. Bayesian은 주 1회만 실행 → EvalCalibrator는 6일간 독립 미세조정
2. Bayesian의 max_delta가 EvalCalibrator의 max_delta보다 크지 않음 (동일한 ParamSpec 공유)
3. FoodWasteCalibrator는 DB(food_waste_calibration) 기반이므로 Bayesian 결과와 충돌 없음
4. Bayesian 실행 순서가 기존 보정기 뒤 (1.57 > 1.56) → 기존 조정 결과 반영 후 최적화

## 5. 구현 순서 체크리스트

| # | 파일 | 설명 | 의존성 |
|---|------|------|--------|
| 1 | `src/settings/constants.py` | `DB_SCHEMA_VERSION=40`, Bayesian 상수 추가 | 없음 |
| 2 | `src/db/models.py` | v40 마이그레이션 (bayesian_optimization_log) | #1 |
| 3 | `tests/conftest.py` | in_memory_db에 bayesian_optimization_log 추가 | #2 |
| 4 | `src/prediction/eval_config.py` | ParamSpec.locked 필드, to_dict/apply 수정 | 없음 |
| 5 | `config/eval_params.default.json` | locked 필드 추가 | #4 |
| 6 | `src/infrastructure/database/repos/bayesian_optimization_repo.py` | Repository 구현 | #2 |
| 7 | `src/infrastructure/database/repos/__init__.py` | export 추가 | #6 |
| 8 | `src/prediction/bayesian_optimizer.py` | 핵심 엔진 구현 | #4, #6 |
| 9 | `config/bayesian_config.json` | 설정 파일 생성 | 없음 |
| 10 | `src/application/daily_job.py` | Phase 1.57 추가 | #8 |
| 11 | `run_scheduler.py` | 주간 스케줄 + CLI | #8 |
| 12 | `tests/test_bayesian_optimizer.py` | 단위 테스트 | #8 |
| 13 | `src/web/routes/api_prediction.py` | 대시보드 API (선택) | #6 |

## 6. 검증 기준

### 단위 테스트
- [ ] `_build_search_space()`: locked 파라미터 제외 확인
- [ ] `_build_search_space()`: weight_trend 제외 (유도 파라미터)
- [ ] `_collect_metrics()`: 데이터 부족 시 None 반환
- [ ] `_collect_metrics()`: 정상 데이터에서 지표 계산 정확성
- [ ] `_calculate_objective()`: 가중합 계산 정확성
- [ ] `_apply_with_damping()`: damping_factor=0 → 변경없음, =1 → 전량적용
- [ ] `_apply_params()`: max_delta 클램핑 동작
- [ ] `_apply_params()`: normalize_weights 호출 확인
- [ ] `check_rollback()`: 모니터링 기간 내 → 스킵
- [ ] `check_rollback()`: 성과 하락 > 10% → 롤백 실행
- [ ] `check_rollback()`: 성과 유지 → 확정
- [ ] `_get_sensitivity()`: 알려진 파라미터 감도 반환
- [ ] `_estimate_metrics()`: 변화량 0 → baseline 그대로
- [ ] `BayesianOptimizationRepository.save/get/mark` CRUD
- [ ] `ParamSpec.locked` 직렬화/역직렬화
- [ ] `optimize()`: scikit-optimize 미설치 → graceful 스킵

### 통합 테스트
- [ ] `optimize()` → `save_optimization_log()` → `get_latest_applied()` 전체 흐름
- [ ] Phase 1.57 일요일 조건 분기 동작
- [ ] 다중 매장 환경에서 매장별 독립 최적화

### A/B 검증 (수동)
- [ ] 최적화 전 1주일 성과 기록
- [ ] 최적화 적용 후 1주일 성과 비교
- [ ] accuracy@1, waste_rate, stockout_rate 각각 비교

## 7. 의존성 관리

```python
# requirements.txt에 추가 (선택적)
# scikit-optimize>=0.9.0  # GP 기반 최적화

# 설치 확인 패턴 (bayesian_optimizer.py 내부):
try:
    import skopt
    SKOPT_AVAILABLE = True
except ImportError:
    SKOPT_AVAILABLE = False

try:
    import optuna
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False
```

**미설치 시 동작:**
- `optimize()` → `OptimizationResult(success=False, error_message="No optimizer library")`
- 기존 보정기(EvalCalibrator, FoodWasteRateCalibrator) 정상 동작에 영향 없음
- Phase 1.57 로그에 "스킵" 메시지만 출력

## 8. 보안 및 안정성

### 안전 장치 요약
| 장치 | 구현 위치 | 동작 |
|------|----------|------|
| max_delta | ParamSpec.apply_delta() | 1회 변경 폭 제한 (기존) |
| locked | ParamSpec.locked | 특정 파라미터 최적화 제외 |
| damping | _apply_with_damping() | 최적값의 50%만 적용 |
| min_data | _collect_metrics() | 7일*5건 미만이면 스킵 |
| rollback | check_rollback() | 3일 후 성과 하락 > 10% → 복원 |
| weekly | daily_job.py | 주 1회만 실행 (일요일) |
| graceful | _import_optimizer() | 라이브러리 미설치 → 스킵 |
| backup | EvalConfig.save() | 저장 전 .json.bak 자동 생성 (기존) |
