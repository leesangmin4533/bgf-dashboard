# Plan: 예측 로깅 분리 (prediction-logging-separation)

> 작성일: 2026-02-04
> 상태: Plan

## 1. 배경 및 문제

### 현재 상태
- `prediction_logs` INSERT는 `auto_order.py:599`의 `log_predictions_batch()`에서만 발생
- 자동발주(Phase 2)를 실행하지 않으면 예측값이 기록되지 않음
- 예측 실적 소급(`update_actual_sales`)은 Phase 1.6에서 실행되지만, 소급할 대상(prediction_logs)이 없음

### 문제점
- **자동발주 없이는 예측 정확도 추적 불가**: 발주를 건너뛴 날은 예측 vs 실적 비교 데이터가 생성되지 않음
- **ML 학습 데이터 축적 지연**: 발주하지 않은 날의 데이터가 누락되어 학습 데이터셋에 공백 발생
- **예측 모델 평가 불가**: 규칙 기반 예측기의 성능을 지속적으로 모니터링할 수 없음

## 2. 목표

자동발주 실행 여부와 무관하게 **매일 예측을 수행하고 로그를 기록**하여:
1. 매일 예측 vs 실적 비교가 가능하도록 함
2. ML 학습 데이터를 빠짐없이 축적
3. 예측 정확도를 지속적으로 모니터링

## 3. 현재 플로우

```
07:00 run_optimized()
  ├─ [Phase 1]   데이터 수집           ← 항상 실행
  ├─ [Phase 1.5] 평가 보정             ← 항상 실행 (collection_success 조건)
  ├─ [Phase 1.6] 소급 backfill         ← 항상 실행 (collection_success 조건)
  │
  └─ [Phase 2]   자동 발주             ← run_auto_order=True일 때만
       ├─ get_recommendations()        ← 예측 수행
       │    ├─ pre_order_evaluator     ← 사전 평가
       │    ├─ predict_batch()         ← ImprovedPredictor 예측
       │    └─ log_predictions_batch() ← ★ prediction_logs INSERT (여기서만!)
       └─ execute_orders()             ← 실제 발주 실행
```

## 4. 변경 후 플로우

```
07:00 run_optimized()
  ├─ [Phase 1]   데이터 수집           ← 항상 실행
  ├─ [Phase 1.5] 평가 보정             ← 항상 실행
  ├─ [Phase 1.6] 소급 backfill         ← 항상 실행
  ├─ [Phase 1.7] 예측 로깅             ← ★ 신규 (항상 실행)
  │    ├─ ImprovedPredictor 예측
  │    └─ PredictionLogger 기록
  │
  └─ [Phase 2]   자동 발주             ← run_auto_order=True일 때만
       ├─ get_recommendations()        ← 예측 수행 (Phase 1.7 결과 재활용)
       └─ execute_orders()             ← 실제 발주 실행
```

## 5. 구현 전략

### 접근법: DailyCollectionJob에 독립 예측 로깅 단계 추가

`run_optimized()`에서 Phase 1.6(소급) 직후, Phase 2(자동발주) 이전에 **독립 예측 로깅 단계**를 삽입한다.

### 핵심 원칙
- **예측 로직 재사용**: `ImprovedPredictor.get_recommendations()`를 직접 호출하지 않고, 예측 전용 메서드를 호출
- **자동발주와 중복 방지**: Phase 2에서 자동발주가 실행되면 prediction_logs에 중복 INSERT 방지 필요
- **실패 시 발주 플로우 영향 없음**: try/except로 감싸서 예측 로깅 실패가 발주를 차단하지 않도록

### 변경 대상 파일

| 파일 | 변경 내용 |
|------|---------|
| `src/scheduler/daily_job.py` | Phase 1.7 예측 로깅 단계 추가 |
| `src/prediction/improved_predictor.py` | `predict_and_log()` 메서드 추가 (예측 + 로깅 통합) |
| `src/order/auto_order.py` | 중복 로깅 방지 플래그 추가 |

### 상세 변경

#### A. `improved_predictor.py` - `predict_and_log()` 메서드 추가

```python
def predict_and_log(self, target_date=None) -> int:
    """
    전체 활성 상품에 대해 예측 수행 + prediction_logs 저장

    자동발주와 독립적으로 실행 가능.
    이미 오늘 날짜로 prediction_logs가 있으면 스킵 (중복 방지).

    Returns:
        저장된 예측 건수
    """
```

**구현 포인트**:
- `SalesRepository`에서 최근 판매 이력이 있는 활성 상품 목록 조회
- `predict_batch()` 호출하여 예측 수행
- 오늘 날짜로 이미 prediction_logs에 기록이 있으면 스킵 (중복 방지)
- `PredictionLogger.log_predictions_batch()` 호출하여 저장

#### B. `daily_job.py` - Phase 1.7 추가

```python
# ★ 예측 로깅 (prediction_logs 기록 - 자동발주와 독립)
if collection_success:
    try:
        logger.info("[Phase 1.7] Prediction Logging")
        predictor = ImprovedPredictor()
        logged = predictor.predict_and_log()
        logger.info(f"예측 로깅 완료: {logged}건")
    except Exception as e:
        logger.warning(f"예측 로깅 실패 (발주 플로우 계속): {e}")
```

#### C. `auto_order.py` - 중복 로깅 방지

`get_recommendations()` 내 `log_predictions_batch()` 호출 시, 오늘 날짜로 이미 기록이 있으면 스킵:

```python
# 예측 결과 로그 저장 (Phase 1.7에서 이미 저장했으면 스킵)
try:
    saved_count = self.prediction_logger.log_predictions_batch(
        candidates, skip_if_exists=True
    )
    logger.info(f"예측 로그 저장: {saved_count}/{len(candidates)}건")
except Exception as e:
    logger.warning(f"예측 로그 저장 실패: {e}")
```

## 6. 중복 방지 전략

`PredictionLogger.log_predictions_batch()`에 중복 체크 추가:

```sql
-- 오늘 날짜로 해당 상품의 prediction_logs가 이미 있으면 INSERT 스킵
SELECT COUNT(*) FROM prediction_logs
WHERE prediction_date = ? AND item_cd = ?
```

또는 `INSERT OR IGNORE`와 UNIQUE 제약 조건 활용.

## 7. 영향 범위

### 변경 있음
- `src/scheduler/daily_job.py` - Phase 1.7 추가 (10줄)
- `src/prediction/improved_predictor.py` - `predict_and_log()` 메서드 (30줄)
- `src/order/auto_order.py` - 중복 방지 플래그 (5줄)

### 변경 없음
- DB 스키마 (prediction_logs 테이블 그대로)
- 기존 예측 로직 (ImprovedPredictor 내부 변경 없음)
- 기존 자동발주 로직 (동작 변경 없음)
- AccuracyTracker / EvalCalibrator (그대로 활용)

## 8. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 예측 로깅 시간 소요 | 자동발주 시작 지연 | try/except로 감싸서 실패 시 스킵 |
| prediction_logs 중복 INSERT | 데이터 오염 | 날짜+상품코드 중복 체크 |
| 예측 시 DB 부하 | 수집 직후 DB 경합 | 이미 수집 완료 후 실행이므로 문제 없음 |

## 9. 검증 기준

- [x] 자동발주 없이(`--no-order`) 실행해도 prediction_logs에 예측값 기록됨
- [x] 자동발주와 함께 실행해도 prediction_logs에 중복 없음
- [x] 다음날 update_actual_sales()로 actual_qty 소급 정상 작동
- [x] 기존 자동발주 플로우에 영향 없음 (발주 결과 동일)
