# Design: ML 모델 매일 증분학습

## 1. 수정 파일 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `src/application/scheduler/job_definitions.py` | 수정 | 스케줄 변경 + incremental 파라미터 |
| `src/application/use_cases/ml_training_flow.py` | 수정 | incremental 파라미터 추가 |
| `src/prediction/ml/trainer.py` | 수정 | 성능 보호 게이트 추가 |
| `run_scheduler.py` | 수정 | 이중 스케줄 (매일+일요일) |
| `tests/test_ml_daily_training.py` | 신규 | 12개 테스트 |

## 2. job_definitions.py 변경

```python
JobDefinition(
    name="ml_training",
    flow_class="src.application.use_cases.ml_training_flow.MLTrainingFlow",
    schedule="23:30",         # 매일 야간
    multi_store=True,
),
JobDefinition(
    name="ml_training_full",
    flow_class="src.application.use_cases.ml_training_flow.MLTrainingFlow",
    schedule="Sun 03:00",     # 주간 전체 학습
    multi_store=True,
),
```

## 3. MLTrainingFlow 변경

```python
def run(self, days: int = 90, incremental: bool = False) -> Dict[str, Any]:
    if incremental:
        days = 30  # 최신 데이터에 집중
    trainer = MLTrainer(store_id=self.store_id)
    results = trainer.train_all_groups(days=days, incremental=incremental)
    ...
```

## 4. trainer.py 성능 보호 게이트

### 4-1. _check_performance_gate()
```python
def _check_performance_gate(self, group_name: str, new_mae: float) -> bool:
    """기존 모델 대비 성능 악화 여부 확인.

    Returns:
        True: 신규 모델 수용 (개선 또는 허용 범위 내)
        False: 롤백 필요 (20% 이상 악화)
    """
    meta = self.predictor._load_meta()
    group_meta = meta.get(group_name, {})
    prev_mae = group_meta.get("mae")

    if prev_mae is None or prev_mae <= 0:
        return True  # 기존 기록 없음 → 수용

    degradation = (new_mae - prev_mae) / prev_mae
    if degradation > 0.2:  # 20% 이상 악화
        logger.warning(
            f"[{group_name}] 성능 게이트 FAIL: "
            f"기존 MAE={prev_mae:.2f} → 신규 MAE={new_mae:.2f} "
            f"(악화 {degradation:.1%}). 이전 모델 유지."
        )
        return False

    logger.info(
        f"[{group_name}] 성능 게이트 PASS: "
        f"기존 MAE={prev_mae:.2f} → 신규 MAE={new_mae:.2f} "
        f"({'개선' if degradation < 0 else '허용'} {degradation:+.1%})"
    )
    return True
```

### 4-2. _rollback_model()
```python
def _rollback_model(self, group_name: str) -> bool:
    """_prev 모델로 롤백."""
    prev_path = self.predictor.model_dir / f"model_{group_name}_prev.joblib"
    model_path = self.predictor.model_dir / f"model_{group_name}.joblib"

    if not prev_path.exists():
        return False

    import shutil
    shutil.copy2(prev_path, model_path)
    logger.info(f"[{group_name}] 이전 모델로 롤백 완료")
    return True
```

### 4-3. train_all_groups() 수정
```python
def train_all_groups(self, days: int = 90, incremental: bool = False):
    ...
    # 모델 저장 전 성능 게이트 체크
    if incremental and not self._check_performance_gate(group_name, mae):
        self._rollback_model(group_name)
        results[group_name]["gated"] = True
        results[group_name]["success"] = False
        results[group_name]["reason"] = "performance_gate_failed"
        continue

    # 기존 저장 로직
    self.predictor.save_model(group_name, ensemble, metrics=save_metrics)
```

## 5. run_scheduler.py 변경

```python
def ml_train_wrapper(incremental: bool = False) -> None:
    """ML 모델 학습 -- 매장별 병렬"""
    mode = "증분(30일)" if incremental else "전체(90일)"
    logger.info(f"ML model training ({mode}) at {datetime.now().isoformat()}")

    from src.application.use_cases.ml_training_flow import MLTrainingFlow
    _run_task(
        task_fn=lambda ctx: MLTrainingFlow(store_ctx=ctx).run(incremental=incremental),
        task_name="MLTrain",
    )

# 스케줄 등록
schedule.every().day.at("23:30").do(ml_train_wrapper, incremental=True)
schedule.every().sunday.at("03:00").do(ml_train_wrapper, incremental=False)
```

## 6. 테스트 설계 (12개)

| 테스트 | 검증 내용 |
|--------|----------|
| test_daily_schedule_registered | 매일 23:30 스케줄 등록 |
| test_weekly_schedule_registered | 일요일 03:00 스케줄 등록 |
| test_incremental_uses_30_days | incremental=True → days=30 |
| test_full_uses_90_days | incremental=False → days=90 |
| test_performance_gate_pass | MAE 개선 → 수용 |
| test_performance_gate_fail | MAE 20% 악화 → 거부 |
| test_performance_gate_no_prev | 기존 기록 없음 → 수용 |
| test_rollback_model | _prev 모델로 롤백 |
| test_rollback_no_prev | _prev 없으면 False |
| test_gated_result_flagged | 게이트 실패 시 gated=True |
| test_job_definitions_schedule | job_definitions에 매일 스케줄 |
| test_wrapper_passes_incremental | wrapper가 incremental 전달 |
