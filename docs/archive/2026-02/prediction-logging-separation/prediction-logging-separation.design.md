# Design: 예측 로깅 분리 (prediction-logging-separation)

> 작성일: 2026-02-04
> 상태: Design
> Plan 참조: `prediction-logging-separation.plan.md`

## 1. 설계 개요

자동발주 실행 여부와 무관하게 매일 예측을 수행하고 `prediction_logs`에 기록한다.
기존 `get_order_candidates()` 메서드를 재활용하되, 로깅 전용 진입점을 추가한다.

## 2. 변경 파일 및 구현 명세

### 2.1 `src/prediction/improved_predictor.py`

#### 추가: `predict_and_log()` 메서드 (ImprovedPredictor 클래스)

`get_order_candidates()`의 1630행 로직을 재활용한다. 이미 상품 조회 → 예측 → 필터링 파이프라인이 구현되어 있으므로, 이를 호출하고 결과를 `PredictionLogger`로 저장한다.

```python
def predict_and_log(self, target_date: Optional[datetime] = None) -> int:
    """전체 활성 상품 예측 수행 + prediction_logs 저장 (자동발주와 독립)

    이미 오늘 날짜로 prediction_logs가 있으면 스킵하여 중복 방지.

    Args:
        target_date: 예측 대상 날짜 (기본: 내일)

    Returns:
        저장된 예측 건수
    """
```

**구현 순서**:

1. 중복 체크: 오늘 날짜로 `prediction_logs` 레코드가 이미 있으면 return 0
2. `get_order_candidates(target_date, min_order_qty=0)` 호출 (min_order_qty=0으로 발주량 0인 상품도 포함)
3. `PredictionLogger().log_predictions_batch(results)` 호출
4. 저장 건수 return

**중복 체크 SQL**:
```sql
SELECT COUNT(*) FROM prediction_logs
WHERE prediction_date = ?
```
- 1건이라도 있으면 이미 로깅 완료로 판단하고 스킵
- 이유: 상품 단위 중복 체크는 성능 부담 (2,000+ 상품 × INSERT마다 SELECT), 날짜 단위면 충분

**min_order_qty=0인 이유**:
- 현재 `get_order_candidates()`는 `min_order_qty` 이상인 것만 반환 (1671행)
- ML 학습에는 **발주량 0인 상품도** 필요 (재고 충분한 상품의 예측값도 기록해야 모델이 학습 가능)
- 로깅 전용이므로 `min_order_qty=0`으로 모든 예측 결과를 기록

#### 메서드 위치

`get_order_candidates()` 바로 아래 (1672행 이후), `PredictionLogger` 클래스 직전.

---

### 2.2 `src/scheduler/daily_job.py`

#### 추가: Phase 1.7 (Phase 1.6 소급 직후, Phase 2 자동발주 직전)

```python
# ★ 예측 로깅 (prediction_logs 기록 - 자동발주와 독립)
if collection_success:
    try:
        logger.info("[Phase 1.7] Prediction Logging")
        logger.info(LOG_SEPARATOR_THIN)
        from prediction.improved_predictor import ImprovedPredictor
        predictor = ImprovedPredictor()
        logged = predictor.predict_and_log()
        logger.info(f"예측 로깅 완료: {logged}건")
    except Exception as e:
        logger.warning(f"예측 로깅 실패 (발주 플로우 계속): {e}")
```

**위치**: `run_optimized()` 내, Phase 1.6 블록 직후 ~ 카카오톡 발송 직전.

**설계 판단**:
- `ImprovedPredictor`는 Phase 1.7에서 새로 생성 (Phase 2의 `AutoOrderSystem`과 독립)
- `AutoOrderSystem`은 내부에서 자체 `ImprovedPredictor` 인스턴스를 생성하므로 충돌 없음
- lazy import (`from prediction.improved_predictor import ImprovedPredictor`) 사용하여 Phase 1.7 스킵 시 불필요한 import 방지

---

### 2.3 `src/order/auto_order.py`

#### 변경: 597~602행 중복 로깅 방지

기존:
```python
# 예측 결과 로그 저장 (정확도 추적용) - 배치 저장으로 성능 개선
try:
    saved_count = self.prediction_logger.log_predictions_batch(candidates)
    logger.info(f"예측 로그 저장: {saved_count}/{len(candidates)}건")
except Exception as e:
    logger.warning(f"예측 로그 저장 실패: {e}")
```

변경 후:
```python
# 예측 결과 로그 저장 (Phase 1.7에서 이미 저장했으면 스킵)
try:
    saved_count = self.prediction_logger.log_predictions_batch_if_needed(candidates)
    if saved_count > 0:
        logger.info(f"예측 로그 저장: {saved_count}/{len(candidates)}건")
    else:
        logger.info(f"예측 로그: 이미 기록됨 (Phase 1.7), 스킵")
except Exception as e:
    logger.warning(f"예측 로그 저장 실패: {e}")
```

---

### 2.4 `src/prediction/improved_predictor.py` - PredictionLogger 클래스

#### 추가: `log_predictions_batch_if_needed()` 메서드

```python
def log_predictions_batch_if_needed(self, results: List[PredictionResult]) -> int:
    """오늘 날짜로 이미 기록이 있으면 스킵, 없으면 저장

    Args:
        results: PredictionResult 리스트

    Returns:
        저장된 건수 (이미 있으면 0)
    """
```

**구현**:
```python
def log_predictions_batch_if_needed(self, results: List[PredictionResult]) -> int:
    if not results:
        return 0

    conn = self._get_connection(timeout=30)
    try:
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT COUNT(*) FROM prediction_logs WHERE prediction_date = ?",
            (today,)
        )
        if cursor.fetchone()[0] > 0:
            return 0
    finally:
        conn.close()

    return self.log_predictions_batch(results)
```

## 3. 변경 없는 파일

| 파일 | 이유 |
|------|------|
| `src/db/models.py` | DB 스키마 변경 없음 |
| `src/db/repository.py` | 새 Repository 불필요 |
| `src/prediction/accuracy/tracker.py` | 그대로 활용 (update_actual_sales) |
| `src/prediction/eval_calibrator.py` | 그대로 활용 |
| `run_scheduler.py` | daily_job.py 내부 변경이므로 스케줄러 수정 불필요 |

## 4. 실행 흐름 (시나리오별)

### 시나리오 A: 정상 실행 (자동발주 O)

```
07:00 run_optimized(run_auto_order=True)
  [Phase 1]   수집 성공
  [Phase 1.5] 평가 보정
  [Phase 1.6] 소급 backfill (어제 actual_qty 채움)
  [Phase 1.7] predict_and_log() → prediction_logs에 600건 INSERT
  [Phase 2]   get_recommendations()
              → log_predictions_batch_if_needed()
              → 이미 기록 있음 → 스킵 (0건)
              → execute_orders() 실행
```

### 시나리오 B: 자동발주 없이 실행

```
07:00 run_optimized(run_auto_order=False)  또는 pre_alert_collection
  [Phase 1]   수집 성공
  [Phase 1.5] 평가 보정
  [Phase 1.6] 소급 backfill
  [Phase 1.7] predict_and_log() → prediction_logs에 600건 INSERT
  [Phase 2]   스킵 (run_auto_order=False)
```

→ 발주는 안 했지만 예측 로그는 기록됨. 다음날 소급으로 actual_qty 채워짐.

### 시나리오 C: Phase 1.7 실패

```
07:00 run_optimized(run_auto_order=True)
  [Phase 1]   수집 성공
  [Phase 1.5] 평가 보정
  [Phase 1.6] 소급 backfill
  [Phase 1.7] predict_and_log() → 예외 발생 → warning 로그, 계속 진행
  [Phase 2]   get_recommendations()
              → log_predictions_batch_if_needed()
              → 기록 없음 → 정상 INSERT
              → execute_orders() 실행
```

→ Phase 1.7 실패해도 Phase 2에서 기존대로 로깅됨. **기존 동작 100% 보존**.

### 시나리오 D: 같은 날 재실행

```
07:00 첫 실행 → Phase 1.7에서 600건 INSERT
09:00 재실행 → predict_and_log() 중복 체크 → 이미 있음 → return 0
             → Phase 2 log_predictions_batch_if_needed() → 이미 있음 → return 0
```

→ 중복 INSERT 없음.

## 5. 구현 순서

```
1. PredictionLogger.log_predictions_batch_if_needed() 추가
2. ImprovedPredictor.predict_and_log() 추가
3. auto_order.py 기존 로깅 호출을 log_predictions_batch_if_needed()로 교체
4. daily_job.py Phase 1.7 추가
5. 검증: --no-order로 실행하여 prediction_logs 기록 확인
```

## 6. 검증 체크리스트

- [x] `predict_and_log()` 실행 시 prediction_logs에 예측값 기록됨
- [x] 같은 날 재실행 시 중복 INSERT 없음
- [x] Phase 1.7 실패 시 Phase 2 자동발주 정상 작동
- [x] 자동발주 실행 시 `log_predictions_batch_if_needed()`가 중복 스킵
- [x] `--no-order` 모드에서도 prediction_logs 기록됨
- [x] 다음날 `update_actual_sales()`로 actual_qty 소급 정상 작동
