# Plan: prediction-accuracy-fix

## 1. 개요

대시보드 예측정확도 섹션의 MAPE 계산 불일치 및 지표 개선.

### 배경
- 요약 카드 MAPE(149%)와 일별 차트 MAPE(1.6%)가 **93배 차이**
- prediction_logs의 97.9%가 actual=0 (매출 없는 상품)이라 MAPE가 구조적으로 왜곡
- NORMAL_ORDER 적중률 6.4%는 판정 기준이 편의점 특성에 맞지 않음

### 목표
1. MAPE 계산을 Python/SQL 간 통일
2. 소량 판매 상품에 적합한 보조 지표(SMAPE, wMAPE) 추가
3. NORMAL_ORDER 판정 기준에 리드타임 반영
4. 대시보드 UI에 지표 의미 명확하게 표시

## 2. 현재 문제점 (3건)

### 문제 A: MAPE 계산 불일치 [심각도: 높음]

| 위치 | 파일 | 방식 | 결과 |
|------|------|------|------|
| 요약 카드 | `tracker.py:139` | actual=0 **제외** | 149.2% |
| 일별 차트 | `tracker.py:630` SQL | actual=0 → **MAPE=0 포함** | 1.6% |
| Worst/Best | `tracker.py:409` SQL | actual=0 → **MAPE=0 포함** | 혼합 |

**근본원인**: actual=0이 97.9%인 상황에서 두 방식의 결과가 극단적으로 차이남.

### 문제 B: MAPE 지표 자체의 부적합성 [심각도: 중간]

- 편의점 상품은 일 판매 0~3개가 대부분
- actual=1, predicted=0 → MAPE=100% (1개 차이인데 100%)
- actual=1, predicted=2 → MAPE=100% (역시 1개 차이인데 100%)
- MAE=0.36은 양호하나 MAPE=149%로 보이면 오해 유발

### 문제 C: NORMAL_ORDER 판정 기준 [심각도: 낮음]

- 현재: 발주 다음날 actual_sold=0이면 무조건 OVER_ORDER
- 문제: 유통기한 긴 상품은 발주 후 2~3일 뒤 판매될 수 있음
- NORMAL_ORDER 적중률 6.4%는 구조적 과소평가

## 3. 수정 계획

### 수정 1: MAPE 계산 통일 + SMAPE 도입

**파일**: `src/prediction/accuracy/tracker.py`

변경 내용:
- `calculate_metrics()`: 기존 MAPE 유지 (actual>0 기준) + **SMAPE 추가**
- `get_daily_mape_trend()`: SQL을 Python 방식과 동일하게 actual=0 **제외**로 변경
- `get_worst_items()` / `get_best_items()`: 동일 통일
- AccuracyMetrics에 `smape` 필드 추가

SMAPE 공식: `|pred - actual| / ((|pred| + |actual|) / 2) × 100`
- actual=0, pred=0 → SMAPE=0% (둘 다 0이면 정확)
- actual=0, pred=1 → SMAPE=200% (명확한 과대)
- actual=1, pred=0 → SMAPE=200% (명확한 과소)
- actual=1, pred=2 → SMAPE=66.7% (MAPE 100%보다 합리적)

### 수정 2: wMAPE(가중 MAPE) 추가

**파일**: `src/prediction/accuracy/tracker.py`

변경 내용:
- AccuracyMetrics에 `wmape` 필드 추가
- wMAPE = SUM(|pred-actual|) / SUM(actual) × 100
- 대량 판매 상품이 가중치를 더 받아 소량 상품의 MAPE 왜곡 해소

### 수정 3: API 응답에 새 지표 포함

**파일**: `src/web/routes/api_prediction.py`

변경 내용:
- `_get_qty_accuracy()`: smape, wmape 추가 반환
- `accuracy_detail()`: daily_mape_trend에 smape 추가

### 수정 4: 프론트엔드 표시 개선

**파일**: `src/web/static/js/prediction.js`

변경 내용:
- 요약 카드: MAPE 대신 **MAE** 메인 표시, SMAPE 보조 표시
- 서브텍스트: "MAE 0.36개 | ±2이내 97% | SMAPE 42%"
- 일별 차트: Y축 MAPE → SMAPE로 변경 (또는 둘 다 표시)
- Worst/Best: MAPE 대신 MAE + bias 강조

### 수정 5: NORMAL_ORDER 판정 개선 (선택)

**파일**: `src/prediction/eval_calibrator.py`

변경 내용:
- `_determine_outcome()` NORMAL_ORDER: 리드타임 기반 유예 기간 추가
- actual_sold=0이어도 리드타임 내이면 PENDING으로 판정 보류
- 리드타임 경과 후 최종 판정

> 이 수정은 eval_outcomes 테이블 스키마에 영향 없음 (outcome 텍스트만 변경)
> 단, 과거 데이터 소급 변경 불가 → 신규 데이터부터 적용

## 4. 수정 대상 파일

| # | 파일 | 변경 유형 | 우선순위 |
|---|------|---------|---------|
| 1 | `src/prediction/accuracy/tracker.py` | MAPE 통일 + SMAPE/wMAPE 추가 | 높음 |
| 2 | `src/web/routes/api_prediction.py` | API 응답에 새 지표 포함 | 높음 |
| 3 | `src/web/static/js/prediction.js` | UI 표시 개선 | 높음 |
| 4 | `src/prediction/eval_calibrator.py` | NORMAL_ORDER 판정 개선 | 낮음 |
| 5 | `tests/test_accuracy_tracker.py` | 테스트 추가/수정 | 높음 |

## 5. 테스트 계획

- SMAPE/wMAPE 계산 정확성 테스트
- actual=0, actual=1 경계 케이스
- daily_mape_trend SQL vs Python 일치성 검증
- API 응답 스키마 검증
- 프론트엔드 렌더링 (수동 확인)

## 6. 예상 영향

- 기존 MAPE 값은 유지 (하위 호환)
- 새 지표(smape, wmape)는 추가 필드로 제공
- 대시보드 UI만 표시 우선순위 변경 (MAPE → MAE/SMAPE)
- eval_outcomes 과거 데이터에는 영향 없음

## 7. 제외 범위

- prediction_logs에 actual=0이 97.9%인 근본 문제 (예측 대상 상품 필터링은 별도 이슈)
- ML 모델 자체 성능 개선
- eval_outcomes 과거 데이터 소급 재판정
