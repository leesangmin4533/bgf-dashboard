# Plan: ML 모델 매일 증분학습

## 1. 목적
- ML 모델 학습 주기를 주1회(일요일 03:00)에서 매일 야간(23:30)으로 변경
- 급변하는 수요 패턴에 빠르게 대응
- 매일 학습 시 30일 윈도우로 최신 데이터에 집중 (incremental 모드)
- 주간 학습은 90일 전체 데이터로 유지 (full 모드) → 안정적 기준선
- 성능 보호 게이트: 신규 모델 MAE가 기존 대비 20% 이상 나빠지면 롤백

## 2. 현재 상태
- `job_definitions.py`: `schedule="Sun 03:00"`
- `run_scheduler.py` line 996: `schedule.every().monday.at("03:00")`
  (job_definitions.py와 불일치: Sun vs monday)
- `ml_training_flow.py`: days=90 고정
- `trainer.py`: train_all_groups(days=90), 모델 백업(_prev) 존재
- 모델 메타데이터: model_meta.json (mae, rmse, mape 기록)

## 3. 수정 계획

### 3-1. 스케줄 변경
- job_definitions.py: `schedule="23:30"` (매일)
- run_scheduler.py: `schedule.every().day.at("23:30")` + 일요일 full 학습 유지
- wrapper 주석/로그 수정

### 3-2. MLTrainingFlow 확장
- `run(days=90)` → `run(days=90, incremental=False)`
- incremental=True: days=30 (최신 데이터)
- incremental=False: days=90 (전체 데이터, 주1회)

### 3-3. trainer.py 성능 보호 게이트
- 학습 후 테스트 MAE를 기존 모델 MAE와 비교
- 기존 대비 20% 이상 악화 → _prev 모델로 롤백
- 로깅: 개선율 표시

### 3-4. run_scheduler.py 이중 스케줄
- 매일 23:30: incremental=True (30일)
- 일요일 03:00: incremental=False (90일)

## 4. 영향 범위
- `src/application/scheduler/job_definitions.py`
- `src/application/use_cases/ml_training_flow.py`
- `src/prediction/ml/trainer.py`
- `run_scheduler.py`
- `tests/test_ml_daily_training.py` (신규)

## 5. 리스크
- 매일 학습 시 소요시간 증가 → 30일 윈도우로 완화
- 변동 심한 날 학습 시 모델 불안정 → 성능 게이트로 방어
