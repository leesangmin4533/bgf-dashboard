# Report: mape-fix

## Summary
ML 학습 지표(`ml_training_logs`) 테이블에 numpy float이 BLOB(bytes)로 저장되는 버그 수정.

## Root Cause
`trainer.py` `train_all_groups()`에서 `results` dict 구성 시 `round(mape, 1)` 등이 numpy.float64를 반환하여, `_save_training_metrics()`가 SQLite INSERT 시 바이너리로 직렬화됨.

## Changes

### `src/prediction/ml/trainer.py`
- **Line 488-489**: gated 실패 시 `round(mae, 2)` → `round(float(mae), 2)` (2개 값)
- **Line 512-517**: 성공 시 results dict `round(float(...))` 래핑 (6개 값)

## Data Cleanup
- 46513: 80건 오염 레코드 삭제
- 46704: 55건 오염 레코드 삭제

## Verification
- ML 학습 재실행 → `typeof(mape) = "real"` 확인 (BLOB 0건)
- 양쪽 매장 5/5 그룹 + 7~8개 그룹모델 정상 저장
- ML 관련 테스트 88개 전부 통과 (9.72s)

## Match Rate
100% (Plan의 수정 범위와 실제 구현 일치)
