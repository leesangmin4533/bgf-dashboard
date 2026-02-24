# -*- coding: utf-8 -*-
"""
bayesian-param-optimizer PDCA 테스트

검증 대상:
  1. OptimizationResult 데이터 클래스
  2. BayesianParameterOptimizer 핵심 메서드
  3. BayesianOptimizationRepository CRUD
  4. ParamSpec.locked 필드 동작
  5. Phase 1.57 조건 분기
"""

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.prediction.bayesian_optimizer import (
    BayesianParameterOptimizer,
    OptimizationResult,
    BAYESIAN_ENABLED,
    DEFAULT_OBJECTIVE_WEIGHTS,
    DAMPING_FACTOR,
    ROLLBACK_MONITOR_DAYS,
    ROLLBACK_THRESHOLD,
    N_TRIALS,
)
from src.prediction.eval_config import EvalConfig, ParamSpec


# =========================================================================
# 1. OptimizationResult 테스트
# =========================================================================


class TestOptimizationResult:
    """OptimizationResult 데이터 클래스 테스트"""

    def test_create_success_result(self):
        """성공 결과 생성"""
        result = OptimizationResult(
            success=True,
            best_objective=0.15,
            algorithm="skopt",
        )
        assert result.success is True
        assert result.best_objective == 0.15
        assert result.algorithm == "skopt"

    def test_create_failure_result(self):
        """실패 결과 생성"""
        result = OptimizationResult(
            success=False,
            error_message="No optimizer library",
        )
        assert result.success is False
        assert result.error_message == "No optimizer library"

    def test_to_dict(self):
        """to_dict 직렬화 검증"""
        result = OptimizationResult(
            success=True,
            best_objective=0.2,
            n_trials=30,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["best_objective"] == 0.2
        assert d["n_trials"] == 30
        # 미설정 필드는 None
        assert d["error_message"] is None


# =========================================================================
# 2. ParamSpec.locked 테스트
# =========================================================================


class TestParamSpecLocked:
    """ParamSpec.locked 필드 동작 검증"""

    def test_default_not_locked(self):
        """기본값: locked=False"""
        spec = ParamSpec(value=1.0, default=1.0, min_val=0.0, max_val=2.0, max_delta=0.5)
        assert spec.locked is False

    def test_set_locked(self):
        """locked=True 설정"""
        spec = ParamSpec(
            value=1.0, default=1.0, min_val=0.0, max_val=2.0,
            max_delta=0.5, locked=True
        )
        assert spec.locked is True

    def test_locked_serialization(self):
        """EvalConfig.to_dict()에 locked 포함 확인"""
        config = EvalConfig()
        config.daily_avg_days.locked = True
        d = config.to_dict()
        assert d["daily_avg_days"]["locked"] is True
        assert d["weight_daily_avg"]["locked"] is False

    def test_locked_deserialization(self):
        """_apply_params에서 locked 복원 확인"""
        config = EvalConfig()
        data = {
            "daily_avg_days": {
                "value": 14.0,
                "locked": True,
            },
            "weight_daily_avg": {
                "value": 0.4,
                "locked": False,
            },
        }
        EvalConfig._apply_params(config, data)
        assert config.daily_avg_days.locked is True
        assert config.weight_daily_avg.locked is False

    def test_locked_missing_defaults_false(self):
        """locked 필드 없는 JSON에서 기본값 False"""
        config = EvalConfig()
        data = {
            "daily_avg_days": {"value": 14.0},
        }
        EvalConfig._apply_params(config, data)
        assert config.daily_avg_days.locked is False


# =========================================================================
# 3. BayesianParameterOptimizer 핵심 메서드 테스트
# =========================================================================


class TestBayesianOptimizer:
    """BayesianParameterOptimizer 핵심 로직 테스트"""

    def _make_optimizer(self, config=None):
        """테스트용 옵티마이저 생성"""
        config = config or EvalConfig()
        return BayesianParameterOptimizer(
            store_id="TEST01",
            config=config,
        )

    def test_build_search_space_excludes_locked(self):
        """locked=True 파라미터는 탐색 공간에서 제외"""
        config = EvalConfig()
        config.daily_avg_days.locked = True
        config.weight_daily_avg.locked = True

        optimizer = self._make_optimizer(config)
        # skopt/optuna 없이 테스트하기 위해 _skopt, _optuna 모두 None
        optimizer._skopt = None
        optimizer._optuna = None

        dims, names = optimizer._build_search_space()
        # locked된 파라미터는 없어야 함
        assert "eval.daily_avg_days" not in names
        assert "eval.weight_daily_avg" not in names

    def test_build_search_space_excludes_weight_trend(self):
        """weight_trend는 유도 파라미터이므로 제외"""
        optimizer = self._make_optimizer()
        optimizer._skopt = None
        optimizer._optuna = None

        dims, names = optimizer._build_search_space()
        assert "eval.weight_trend" not in names

    def test_calculate_objective(self):
        """가중 목적함수 계산 정확성"""
        optimizer = self._make_optimizer()
        metrics = {
            "accuracy_error": 0.4,
            "waste_rate_error": 0.1,
            "stockout_rate": 0.2,
            "over_order_ratio": 0.05,
        }
        obj = optimizer._calculate_objective(metrics)
        expected = (
            0.35 * 0.4  # accuracy
            + 0.30 * 0.1  # waste
            + 0.25 * 0.2  # stockout
            + 0.10 * 0.05  # over_order
        )
        assert abs(obj - expected) < 1e-6

    def test_apply_with_damping_zero(self):
        """damping_factor=0이면 변경 없음"""
        optimizer = self._make_optimizer()

        import src.prediction.bayesian_optimizer as bo
        original_damping = bo.DAMPING_FACTOR
        try:
            bo.DAMPING_FACTOR = 0.0
            current_val = optimizer.config.weight_daily_avg.value
            result = optimizer._apply_with_damping(
                ["eval.weight_daily_avg"], [0.55]
            )
            assert abs(result[0] - current_val) < 1e-6
        finally:
            bo.DAMPING_FACTOR = original_damping

    def test_apply_with_damping_full(self):
        """damping_factor=1이면 전량 적용"""
        optimizer = self._make_optimizer()

        import src.prediction.bayesian_optimizer as bo
        original_damping = bo.DAMPING_FACTOR
        try:
            bo.DAMPING_FACTOR = 1.0
            result = optimizer._apply_with_damping(
                ["eval.weight_daily_avg"], [0.55]
            )
            assert abs(result[0] - 0.55) < 1e-6
        finally:
            bo.DAMPING_FACTOR = original_damping

    def test_apply_params_respects_max_delta(self):
        """_apply_params에서 max_delta 클램핑 동작"""
        config = EvalConfig()
        # weight_daily_avg: max_delta=0.05, value=0.40
        optimizer = self._make_optimizer(config)

        # 0.60은 현재(0.40)에서 delta=0.20 -> max_delta=0.05로 클램핑
        delta_map = optimizer._apply_params(
            ["eval.weight_daily_avg"], [0.60]
        )

        if "eval.weight_daily_avg" in delta_map:
            new_val = delta_map["eval.weight_daily_avg"]["new"]
            # 0.40 + 0.05 = 0.45 (max_delta 제한)
            assert new_val <= 0.45 + 1e-6

    def test_apply_params_normalizes_weights(self):
        """_apply_params 후 가중치 합 = 1.0"""
        config = EvalConfig()
        optimizer = self._make_optimizer(config)

        optimizer._apply_params(
            ["eval.weight_daily_avg"], [0.45]
        )

        total = (
            config.weight_daily_avg.value
            + config.weight_sell_day_ratio.value
            + config.weight_trend.value
        )
        assert abs(total - 1.0) < 1e-4

    def test_estimate_metrics_no_change(self):
        """변화량 0이면 baseline 그대로"""
        config = EvalConfig()
        optimizer = self._make_optimizer(config)
        baseline = {
            "accuracy_error": 0.3,
            "waste_rate_error": 0.1,
            "stockout_rate": 0.1,
            "over_order_ratio": 0.05,
        }

        # 현재 값 그대로 전달
        current_val = config.weight_daily_avg.value
        estimated = optimizer._estimate_metrics(
            [current_val], ["eval.weight_daily_avg"], baseline
        )
        assert abs(estimated["accuracy_error"] - 0.3) < 1e-6

    def test_get_sensitivity_known(self):
        """알려진 파라미터 감도 반환"""
        optimizer = self._make_optimizer()
        s = optimizer._get_sensitivity("eval.exposure_urgent")
        assert s["accuracy"] == 0.02
        assert s["waste"] == 0.01
        assert s["stockout"] == -0.03

    def test_get_sensitivity_unknown(self):
        """알 수 없는 파라미터 -> 0 감도"""
        optimizer = self._make_optimizer()
        s = optimizer._get_sensitivity("eval.unknown_param")
        assert s["accuracy"] == 0
        assert s["waste"] == 0
        assert s["stockout"] == 0

    def test_snapshot_params(self):
        """현재 파라미터 스냅샷"""
        config = EvalConfig()
        optimizer = self._make_optimizer(config)

        snapshot = optimizer._snapshot_params(["eval.weight_daily_avg", "eval.daily_avg_days"])
        assert "eval.weight_daily_avg" in snapshot
        assert "eval.daily_avg_days" in snapshot
        assert snapshot["eval.weight_daily_avg"] == config.weight_daily_avg.value
        assert snapshot["eval.daily_avg_days"] == config.daily_avg_days.value

    def test_optimize_no_library(self):
        """scikit-optimize/optuna 미설치 -> graceful 스킵"""
        optimizer = self._make_optimizer()

        with patch.object(optimizer, "_import_optimizer", return_value=None):
            result = optimizer.optimize()
            assert result.success is False
            assert "No optimizer library" in result.error_message

    def test_optimize_disabled(self):
        """BAYESIAN_ENABLED=False -> 스킵"""
        import src.prediction.bayesian_optimizer as bo
        original = bo.BAYESIAN_ENABLED
        try:
            bo.BAYESIAN_ENABLED = False
            optimizer = self._make_optimizer()
            result = optimizer.optimize()
            assert result.success is False
            assert "disabled" in result.error_message
        finally:
            bo.BAYESIAN_ENABLED = original

    def test_optimize_insufficient_data(self):
        """데이터 부족 -> 스킵"""
        optimizer = self._make_optimizer()

        with patch.object(optimizer, "_import_optimizer", return_value="skopt"):
            with patch.object(optimizer, "_collect_metrics", return_value=None):
                result = optimizer.optimize()
                assert result.success is False
                assert "Insufficient" in result.error_message

    def test_restore_params(self):
        """롤백 시 파라미터 복원"""
        config = EvalConfig()
        optimizer = self._make_optimizer(config)

        old_val = config.weight_daily_avg.value
        config.weight_daily_avg.value = 0.55

        optimizer._restore_params({"eval.weight_daily_avg": old_val})
        assert config.weight_daily_avg.value == old_val


# =========================================================================
# 4. check_rollback 테스트
# =========================================================================


class TestCheckRollback:
    """check_rollback 메서드 테스트"""

    def _make_optimizer(self):
        config = EvalConfig()
        return BayesianParameterOptimizer(
            store_id="TEST01", config=config
        )

    def test_no_applied_optimization(self):
        """적용된 최적화 없음 -> 스킵"""
        optimizer = self._make_optimizer()
        mock_repo = MagicMock()
        mock_repo.get_latest_applied.return_value = None
        optimizer._bayesian_repo = mock_repo

        result = optimizer.check_rollback()
        assert result["rolled_back"] is False
        assert "No applied" in result["reason"]

    def test_within_monitoring_period(self):
        """모니터링 기간 내 -> 스킵"""
        optimizer = self._make_optimizer()
        mock_repo = MagicMock()
        # 1일 전 최적화
        mock_repo.get_latest_applied.return_value = {
            "optimization_date": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            "objective_value": 0.15,
        }
        optimizer._bayesian_repo = mock_repo

        result = optimizer.check_rollback()
        assert result["rolled_back"] is False
        assert "Monitoring period" in result["reason"]

    def test_performance_ok_confirmed(self):
        """성과 유지 -> 확정"""
        optimizer = self._make_optimizer()
        mock_repo = MagicMock()
        # 5일 전 최적화
        opt_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        mock_repo.get_latest_applied.return_value = {
            "optimization_date": opt_date,
            "objective_value": 0.20,
            "params_before": "{}",
        }
        optimizer._bayesian_repo = mock_repo

        # 성과 수집: 약간 개선됨
        with patch.object(optimizer, "_collect_metrics", return_value={
            "accuracy_error": 0.3,
            "waste_rate_error": 0.05,
            "stockout_rate": 0.1,
            "over_order_ratio": 0.03,
        }):
            result = optimizer.check_rollback()
            assert result["rolled_back"] is False
            mock_repo.mark_confirmed.assert_called_once()


# =========================================================================
# 5. BayesianOptimizationRepository CRUD 테스트
# =========================================================================


class TestBayesianOptimizationRepository:
    """BayesianOptimizationRepository CRUD 테스트"""

    @pytest.fixture
    def repo_with_db(self, tmp_path):
        """테스트용 SQLite DB + Repository"""
        from src.infrastructure.database.repos.bayesian_optimization_repo import (
            BayesianOptimizationRepository,
        )

        db_path = tmp_path / "test_store.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE bayesian_optimization_log (
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
            )
        """)
        conn.commit()
        conn.close()

        repo = BayesianOptimizationRepository(store_id="TEST01")
        repo._db_path = str(db_path)
        return repo

    def test_save_and_get(self, repo_with_db):
        """저장 후 조회"""
        repo = repo_with_db
        row_id = repo.save_optimization_log(
            store_id="TEST01",
            optimization_date="2026-02-24",
            objective_value=0.15,
            accuracy_error=0.3,
            waste_rate_error=0.05,
            stockout_rate=0.1,
            over_order_ratio=0.02,
            params_before='{"eval.weight_daily_avg": 0.4}',
            params_after='{"eval.weight_daily_avg": 0.45}',
            params_delta='{"eval.weight_daily_avg": {"old": 0.4, "new": 0.45}}',
            algorithm="skopt",
            n_trials=30,
            best_trial=15,
            eval_period_start="2026-02-17",
            eval_period_end="2026-02-24",
        )
        assert row_id is not None

        latest = repo.get_latest_applied("TEST01")
        assert latest is not None
        assert latest["objective_value"] == 0.15
        assert latest["algorithm"] == "skopt"

    def test_mark_rolled_back(self, repo_with_db):
        """롤백 마킹"""
        repo = repo_with_db
        repo.save_optimization_log(
            store_id="TEST01",
            optimization_date="2026-02-24",
            objective_value=0.15,
            accuracy_error=0.3,
            waste_rate_error=0.05,
            stockout_rate=0.1,
            over_order_ratio=0.02,
            params_before="{}",
            params_after="{}",
            params_delta="{}",
            algorithm="skopt",
            n_trials=30,
            best_trial=15,
            eval_period_start="2026-02-17",
            eval_period_end="2026-02-24",
        )

        repo.mark_rolled_back(
            store_id="TEST01",
            optimization_date="2026-02-24",
            reason="Performance degraded 15%",
        )

        # rolled_back=1 이므로 get_latest_applied에서 안 나와야 함
        latest = repo.get_latest_applied("TEST01")
        assert latest is None

    def test_mark_confirmed(self, repo_with_db):
        """확정 마킹 (iteration +1)"""
        repo = repo_with_db
        repo.save_optimization_log(
            store_id="TEST01",
            optimization_date="2026-02-24",
            objective_value=0.15,
            accuracy_error=0.3,
            waste_rate_error=0.05,
            stockout_rate=0.1,
            over_order_ratio=0.02,
            params_before="{}",
            params_after="{}",
            params_delta="{}",
            algorithm="skopt",
            n_trials=30,
            best_trial=15,
            eval_period_start="2026-02-17",
            eval_period_end="2026-02-24",
        )

        repo.mark_confirmed(store_id="TEST01", optimization_date="2026-02-24")

        latest = repo.get_latest_applied("TEST01")
        assert latest is not None
        assert latest["iteration"] == 1

    def test_get_optimization_history(self, repo_with_db):
        """이력 조회"""
        repo = repo_with_db
        for i in range(3):
            date = f"2026-02-{20 + i:02d}"
            repo.save_optimization_log(
                store_id="TEST01",
                optimization_date=date,
                objective_value=0.15 + i * 0.01,
                accuracy_error=0.3,
                waste_rate_error=0.05,
                stockout_rate=0.1,
                over_order_ratio=0.02,
                params_before="{}",
                params_after="{}",
                params_delta="{}",
                algorithm="skopt",
                n_trials=30,
                best_trial=15,
                eval_period_start="2026-02-13",
                eval_period_end=date,
            )

        history = repo.get_optimization_history("TEST01", days=90)
        assert len(history) == 3
        # 최신순 정렬
        assert history[0]["optimization_date"] == "2026-02-22"

    def test_get_param_evolution(self, repo_with_db):
        """파라미터 변화 추이"""
        repo = repo_with_db
        repo.save_optimization_log(
            store_id="TEST01",
            optimization_date="2026-02-24",
            objective_value=0.15,
            accuracy_error=0.3,
            waste_rate_error=0.05,
            stockout_rate=0.1,
            over_order_ratio=0.02,
            params_before="{}",
            params_after="{}",
            params_delta=json.dumps({
                "eval.weight_daily_avg": {"old": 0.4, "new": 0.45}
            }),
            algorithm="skopt",
            n_trials=30,
            best_trial=15,
            eval_period_start="2026-02-17",
            eval_period_end="2026-02-24",
        )

        evolution = repo.get_param_evolution("TEST01", "eval.weight_daily_avg")
        assert len(evolution) == 1
        assert evolution[0]["old"] == 0.4
        assert evolution[0]["new"] == 0.45

    def test_upsert_same_date(self, repo_with_db):
        """동일 날짜 저장 시 UPSERT"""
        repo = repo_with_db
        repo.save_optimization_log(
            store_id="TEST01",
            optimization_date="2026-02-24",
            objective_value=0.15,
            accuracy_error=0.3,
            waste_rate_error=0.05,
            stockout_rate=0.1,
            over_order_ratio=0.02,
            params_before="{}",
            params_after="{}",
            params_delta="{}",
            algorithm="skopt",
            n_trials=30,
            best_trial=15,
            eval_period_start="2026-02-17",
            eval_period_end="2026-02-24",
        )

        # 같은 날짜로 다시 저장 (업데이트)
        repo.save_optimization_log(
            store_id="TEST01",
            optimization_date="2026-02-24",
            objective_value=0.12,
            accuracy_error=0.25,
            waste_rate_error=0.04,
            stockout_rate=0.08,
            over_order_ratio=0.01,
            params_before="{}",
            params_after="{}",
            params_delta="{}",
            algorithm="optuna",
            n_trials=30,
            best_trial=20,
            eval_period_start="2026-02-17",
            eval_period_end="2026-02-24",
        )

        history = repo.get_optimization_history("TEST01")
        assert len(history) == 1
        assert history[0]["objective_value"] == 0.12
        assert history[0]["algorithm"] == "optuna"
