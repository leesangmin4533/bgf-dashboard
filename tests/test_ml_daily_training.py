"""ML 모델 매일 증분학습 테스트.

Design Reference: docs/02-design/features/ml-daily-training.design.md
테스트 항목:
1. 스케줄 등록 (2)
2. incremental 모드 (2)
3. 성능 보호 게이트 (3)
4. 모델 롤백 (2)
5. 게이트 결과 플래그 (1)
6. job_definitions 스케줄 (1)
7. wrapper incremental 파라미터 (1)
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest


# ──────────────────────────────────────────────────────────────
# 1. 스케줄 등록
# ──────────────────────────────────────────────────────────────

class TestScheduleRegistration:
    def test_daily_schedule_in_job_definitions(self):
        """job_definitions에 매일 증분학습 스케줄 존재."""
        from src.application.scheduler.job_definitions import SCHEDULED_JOBS
        ml_jobs = [j for j in SCHEDULED_JOBS if j.name == "ml_training"]
        assert len(ml_jobs) == 1
        assert "23:" in ml_jobs[0].schedule  # 23:xx

    def test_weekly_full_schedule_in_job_definitions(self):
        """job_definitions에 주간 전체학습 스케줄 존재."""
        from src.application.scheduler.job_definitions import SCHEDULED_JOBS
        ml_full = [j for j in SCHEDULED_JOBS if j.name == "ml_training_full"]
        assert len(ml_full) == 1
        assert "Sun" in ml_full[0].schedule


# ──────────────────────────────────────────────────────────────
# 2. incremental 모드
# ──────────────────────────────────────────────────────────────

class TestIncrementalMode:
    def test_incremental_uses_30_days(self):
        """incremental=True이면 days=30으로 학습."""
        from src.application.use_cases.ml_training_flow import MLTrainingFlow

        mock_trainer = MagicMock()
        mock_trainer.train_all_groups.return_value = {
            "food_group": {"success": True}
        }

        with patch.dict("sys.modules", {}):
            flow = MLTrainingFlow(store_ctx=MagicMock(store_id="46513"))
            # MLTrainer 생성을 mock
            with patch("src.prediction.ml.trainer.MLTrainer", return_value=mock_trainer):
                result = flow.run(incremental=True)

        # 30일로 호출되었는지 확인
        mock_trainer.train_all_groups.assert_called_once_with(days=30, incremental=True)
        assert result["incremental"] is True
        assert result["days"] == 30

    def test_full_uses_90_days(self):
        """incremental=False이면 days=90으로 학습."""
        from src.application.use_cases.ml_training_flow import MLTrainingFlow

        mock_trainer = MagicMock()
        mock_trainer.train_all_groups.return_value = {
            "food_group": {"success": True}
        }

        with patch("src.prediction.ml.trainer.MLTrainer", return_value=mock_trainer):
            flow = MLTrainingFlow(store_ctx=MagicMock(store_id="46513"))
            result = flow.run(incremental=False)

        mock_trainer.train_all_groups.assert_called_once_with(days=90, incremental=False)
        assert result["incremental"] is False
        assert result["days"] == 90


# ──────────────────────────────────────────────────────────────
# 3. 성능 보호 게이트
# ──────────────────────────────────────────────────────────────

class TestPerformanceGate:
    def _make_trainer(self, prev_mae=None):
        """MLTrainer mock 생성."""
        from src.prediction.ml.trainer import MLTrainer

        trainer = MLTrainer.__new__(MLTrainer)
        trainer.store_id = "46513"
        trainer.pipeline = MagicMock()
        trainer.predictor = MagicMock()

        # model_meta mock (groups → group_name → metrics 구조)
        meta = {}
        if prev_mae is not None:
            meta["groups"] = {"food_group": {"metrics": {"mae": prev_mae}}}
        trainer.predictor._load_meta.return_value = meta
        trainer.predictor.model_dir = Path("/tmp/models")

        return trainer

    def test_gate_pass_when_improved(self):
        """MAE 개선 → True (수용)."""
        trainer = self._make_trainer(prev_mae=2.0)
        assert trainer._check_performance_gate("food_group", 1.8) is True

    def test_gate_fail_when_degraded(self):
        """MAE 20% 초과 악화 → False (거부)."""
        trainer = self._make_trainer(prev_mae=2.0)
        # 2.0 → 2.5 = 25% 악화 > 20% threshold
        assert trainer._check_performance_gate("food_group", 2.5) is False

    def test_gate_pass_when_no_previous(self):
        """기존 기록 없음 → True (수용)."""
        trainer = self._make_trainer(prev_mae=None)
        assert trainer._check_performance_gate("food_group", 3.0) is True


# ──────────────────────────────────────────────────────────────
# 4. 모델 롤백
# ──────────────────────────────────────────────────────────────

class TestRollback:
    def test_rollback_with_prev_model(self, tmp_path):
        """_prev 모델 존재 시 롤백 성공."""
        from src.prediction.ml.trainer import MLTrainer

        trainer = MLTrainer.__new__(MLTrainer)
        trainer.store_id = "46513"
        trainer.pipeline = MagicMock()
        trainer.predictor = MagicMock()
        trainer.predictor.model_dir = tmp_path

        # _prev 파일 생성
        prev_file = tmp_path / "model_food_group_prev.joblib"
        prev_file.write_text("prev_model_data")
        current_file = tmp_path / "model_food_group.joblib"
        current_file.write_text("bad_model_data")

        result = trainer._rollback_model("food_group")
        assert result is True
        assert current_file.read_text() == "prev_model_data"

    def test_rollback_no_prev_returns_false(self, tmp_path):
        """_prev 모델 없음 → False."""
        from src.prediction.ml.trainer import MLTrainer

        trainer = MLTrainer.__new__(MLTrainer)
        trainer.store_id = "46513"
        trainer.pipeline = MagicMock()
        trainer.predictor = MagicMock()
        trainer.predictor.model_dir = tmp_path

        result = trainer._rollback_model("food_group")
        assert result is False


# ──────────────────────────────────────────────────────────────
# 5. 게이트 결과 플래그
# ──────────────────────────────────────────────────────────────

class TestGatedFlag:
    def test_gated_count_in_flow_result(self):
        """게이트 실패 시 gated_count 반영."""
        from src.application.use_cases.ml_training_flow import MLTrainingFlow

        mock_trainer = MagicMock()
        mock_trainer.train_all_groups.return_value = {
            "food_group": {"success": False, "gated": True, "reason": "performance_gate_failed"},
            "alcohol_group": {"success": True},
        }

        with patch("src.prediction.ml.trainer.MLTrainer", return_value=mock_trainer):
            flow = MLTrainingFlow(store_ctx=MagicMock(store_id="46513"))
            result = flow.run(incremental=True)

        assert result["gated_count"] == 1
        assert result["models_trained"] == 1


# ──────────────────────────────────────────────────────────────
# 6. wrapper incremental 파라미터
# ──────────────────────────────────────────────────────────────

class TestWrapper:
    def test_wrapper_accepts_incremental_param(self):
        """ml_train_wrapper가 incremental 파라미터를 수용."""
        import inspect
        import run_scheduler
        sig = inspect.signature(run_scheduler.ml_train_wrapper)
        assert "incremental" in sig.parameters
        assert sig.parameters["incremental"].default is False
