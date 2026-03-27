# Plan: mape-fix

## Overview
ML 학습 결과를 `ml_training_logs` 테이블에 INSERT할 때, numpy float 타입이 Python float으로 변환되지 않아 BLOB(bytes)으로 저장되는 버그 수정.

## Problem
- `trainer.py` `train_all_groups()`에서 `results` dict 구성 시 `round(mape, 1)` 등이 numpy.float64 반환
- `_save_training_metrics()`가 이 값을 SQLite INSERT → numpy float은 bytes로 직렬화
- 결과: `mape`, `mae`, `accuracy_at_1` 등 컬럼에 `b'\x33\xd6B'` 같은 바이너리 저장
- `save_metrics` dict(line 494-500)에는 `float()` 래핑이 있어 정상 저장됨 → 불일치

## Root Cause
`results[group_name]` dict (line 507-519)와 gated 실패 dict (line 483-490)에서 `round()` 결과를 `float()`으로 래핑하지 않음.

## Fix Scope

### 1. `src/prediction/ml/trainer.py`
- **Line 488-489**: gated 실패 시 `round(mae, 2)` → `round(float(mae), 2)`, `round(accuracy_at_1, 3)` → `round(float(accuracy_at_1), 3)`
- **Line 512-517**: 성공 시 results dict의 6개 값에 `float()` 래핑 추가
  - `round(mae, 2)` → `round(float(mae), 2)`
  - `round(rmse, 2)` → `round(float(rmse), 2)`
  - `round(mape, 1)` → `round(float(mape), 1)`
  - `round(pinball, 3)` → `round(float(pinball), 3)`
  - `round(accuracy_at_1, 3)` → `round(float(accuracy_at_1), 3)`
  - `round(accuracy_at_2, 3)` → `round(float(accuracy_at_2), 3)`

### 2. 기존 오염 데이터 정리
- 양쪽 매장 DB에서 bytes로 저장된 기존 레코드 삭제 (또는 UPDATE)

## Impact
- 수정 파일: 1개 (`trainer.py`)
- 수정 라인: 8개
- 위험도: 매우 낮음 (값 타입 캐스팅만, 로직 변경 없음)

## Verification
- ML 학습 재실행 후 `ml_training_logs` 테이블에서 모든 컬럼이 REAL 타입으로 정상 조회되는지 확인
