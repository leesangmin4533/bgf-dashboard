# PDCA Report: ML 모델 매일 증분학습

## 1. 개요
| 항목 | 내용 |
|------|------|
| Feature | ml-daily-training |
| 목적 | ML 학습 주기를 주1회→매일로 변경, 급변 수요 대응력 향상 |
| Match Rate | 99% |
| 신규 테스트 | 11개 |
| 전체 테스트 | 2,196개 (전부 통과) |

## 2. 변경 요약

### 스케줄 (Before → After)
| | Before | After |
|---|--------|-------|
| 증분학습 | - | **매일 23:45** (30일 윈도우) |
| 전체학습 | 일요일 03:00 (90일) | 일요일 03:00 (90일, 유지) |

### 성능 보호 게이트
- 증분학습 시 신규 모델 MAE를 기존 모델 MAE와 비교
- **20% 이상 악화** → 자동 롤백 (_prev 모델 복원)
- 전체학습(일요일)에는 게이트 미적용 (기준선 갱신 역할)

### 증분학습 흐름
```
매일 23:45 → MLTrainingFlow.run(incremental=True)
  → days=30 자동 축소
  → trainer.train_all_groups(days=30, incremental=True)
    → 각 그룹별 학습
    → _check_performance_gate(group, mae)
      → PASS: 모델 저장
      → FAIL: _rollback_model(group) + gated=True
  → gated_count 보고
```

## 3. 수정 파일

| 파일 | 변경 | 상세 |
|------|------|------|
| `src/application/scheduler/job_definitions.py` | 수정 | ml_training 23:45 + ml_training_full Sun 03:00 |
| `src/application/use_cases/ml_training_flow.py` | 수정 | incremental 파라미터, gated_count 집계 |
| `src/prediction/ml/trainer.py` | 수정 | _check_performance_gate, _rollback_model, incremental 분기 |
| `run_scheduler.py` | 수정 | 이중 스케줄, wrapper incremental 파라미터 |
| `tests/test_ml_daily_training.py` | 신규 | 11개 테스트 (6 클래스) |

## 4. 테스트 결과

| 클래스 | 테스트 수 | 내용 |
|--------|:--------:|------|
| TestScheduleRegistration | 2 | 매일+주간 스케줄 |
| TestIncrementalMode | 2 | 30일/90일 윈도우 |
| TestPerformanceGate | 3 | PASS/FAIL/no-prev |
| TestRollback | 2 | 롤백 성공/실패 |
| TestGatedFlag | 1 | gated_count 집계 |
| TestWrapper | 1 | wrapper 파라미터 |

## 5. 완료 일자
- 2026-02-26
