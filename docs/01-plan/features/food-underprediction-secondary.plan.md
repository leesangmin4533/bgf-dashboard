# Plan: food-underprediction-secondary

## 문제 정의

food-systemic-underprediction 1차 수정(ab98bfc, base_predictor.py WMA 만성-품절 imputation) 이후에도, **재고가 있었던 그룹(has_stock)** 의 푸드 상품(001~005, 012)에서 평균 bias **-0.22 ~ -0.39** 의 약한 과소예측이 4매장 전반에 잔존한다.

### 피해 규모 (2026-04-08 측정)

| 매장 | has_stock n | 평균 bias | 평균 pred | 평균 avg7 |
|---|---|---|---|---|
| 46513 | 21 | -0.26 | 1.17 | 1.42 |
| 46704 | 31 | -0.39 | 0.92 | 1.32 |
| 47863 | 39 | -0.22 | 0.98 | 1.20 |
| 49965 | 67 | -0.22 | 1.02 | 1.24 |
| **합계** | **158** | **-0.27 평균** | — | — |

158건 × bias -0.27 ≈ 일 **42건 추가 결손**. 푸드 유통기한 1~2일 → 즉시 품절 + sell_qty 누적 0 → SLOW 오분류 악순환 위험.

### 1차 vs 2차 비교

| 그룹 | n (4매장) | bias | 원인 | 상태 |
|---|---|---|---|---|
| all_stockout | 162 | -0.49~-0.71 | imputation 미발동 | ✅ ab98bfc 수정 |
| **has_stock** | **158** | **-0.22~-0.39** | **미확인 (본 Plan 대상)** | ⚠️ |

---

## 사전 조사 데이터 (49965 샘플)

### 예시 1: 8800279678588 (도시락, mid=001)
- 7일 윈도우: 5일 stock>0(sale=1 each) + 2일 stock=0(sale=0)
- 이론 imputation 후 WMA = 1.0 (avg_avail × 7일)
- 실제 `predicted_qty=0.78`, `adjusted_qty=1.21`, `weekday_coef=1.18`, `assoc=1.08`
- → imputation 후에도 0.78 → **WMA 후속 단계 또는 가중치에서 추가 -22% 발생**

### 예시 2: 8800336392501 (도시락, mid=001)
- 7일 모든 day row 존재, **전부 `stock_qty=-1`** (음수 sentinel)
- imputation 코드는 `stk>0` (available), `stk==0` (stockout) 만 인식 → -1 은 누락 → 보정 없음
- 7일 sale 합 10/7 = 1.43, predicted=1.23 → -14% bias
- 매장별 `stock_qty<0` 14일치 row 수: 46513=2, 46704=24, 47863=26, 49965=45 → 영향 작지만 **명확한 사각지대**

### 예시 3: 샌드위치(004) 매장 평균 weekday_coef
- 49965 mid=004 weekday_coef = **0.84** → 식육가공 -16% 적용
- 다른 mid 들은 1.08~1.18 → 카테고리/요일 조합별 격차 존재

---

## 의심 원인 후보 (우선순위)

| 우선순위 | 후보 | 근거 | 검증 방법 |
|---|---|---|---|
| **P1** | WMA 가중치 — 최근일 가중이 추가 -bias 유발 | imputation 후에도 0.78 (이론 1.0 대비 -22%) | calculate_weighted_average 의 가중치 분포 출력 |
| **P1** | weekday_coef — 일부 mid/요일 조합 < 1.0 | 49965 mid=004 = 0.84 | 4매장 × 7요일 × food mid 매트릭스 추출 |
| **P2** | outlier_handler 가 정상 sale 상한 클립 | 04-08 푸드 평균 sale 값이 outlier 임계 근처 | clean_sales_data 호출 결과 before/after 비교 |
| **P2** | food/dessert 곱셈 체인의 음의 계수 | base × holiday × weather × weekday × season × assoc × trend 어딘가 | stage_trace 활성화 후 단계별 값 추출 |
| **P3** | stock_qty=-1 sentinel 미인식 | 4매장 합계 14일치 97 row | imputation 분기에 `stk<0 → 미수집 취급` 추가 |
| **P3** | holiday_wma_correction 비휴일 잔존 영향 | 비휴일에도 가중 감쇄 가능 | holiday_dates_set 디버그 출력 |
| **P0 (선결)** | **stage_trace / rule_order_qty / ml_order_qty / ml_weight_used 컬럼이 NULL** → 단계별 추적 자체가 불가능 | prediction_logs 직접 확인 | 로깅 활성화 후 재실행 |

> **P0 가 선결 과제**: stage_trace 가 비어 있어 다른 후보를 데이터로 검증할 수 없음. 먼저 단계별 로깅을 켜는 것이 본 Plan의 첫 작업.

---

## 수정 범위 (Plan 단계 — 가설)

### 변경 후보 파일
1. `src/prediction/improved_predictor.py` — stage_trace 컬럼 채우기 (각 단계 적용 후 값 기록)
2. `src/prediction/base_predictor.py` — calculate_weighted_average:
   - `stock_qty<0` sentinel → `(date, sale_qty, None)` 로 정규화 후 imputation 진입
   - 가중치 계산 디버그 로그 (item 단위 옵트인)
3. `src/prediction/feature_engineering.py` 또는 weekday_coef 산출 위치 — 음수 방향 너무 큰 계수에 floor (예: 0.9) 검토
4. `src/prediction/utils/outlier_handler.py` — 푸드 카테고리는 outlier clip 비활성화 검토

### 변경하지 않는 것
- ab98bfc 1차 수정 (이미 표적 정확)
- DemandClassifier (food/dessert 면제)
- DiffFeedback (별도 이슈)
- food_daily_cap (별도 이슈)

---

## Design 단계로 넘어가기 전 결정 필요

| 결정 항목 | 옵션 A | 옵션 B | 비고 |
|---|---|---|---|
| stage_trace 수집 범위 | 전 상품 + 전 단계 | food mid 만 + 핵심 5단계 | A는 DB 부담, B는 분석 효율 |
| 수정 깊이 | 가중치/계수 동시 패치 | 가장 큰 기여 1개만 우선 | 안전성 vs 효과 |
| stock_qty<0 처리 | 정상 데이터 (그대로) | 미수집 (None 취급) | 데이터 수집기 정책 확인 필요 |

→ Design 단계에서 토론(/discuss) 권장.

---

## 검증 계획 (Check 단계 사전 정의)

- 4매장 has_stock 그룹 평균 bias: -0.27 → **±0.10 이내**
- 158건 중 under_pred 비율: 현재 ~95% → **60% 이하**
- 8800279678588, 8800336392501 신규 predicted_qty 측정
- 1주 운영 후 푸드 mid 001~005 MAE 비교

목표 Match Rate: **90%**

---

## 기여 KPI

- K1 (서비스율): 푸드 품절률 직접 감소
- K3 (발주 실패율): 간접 (품절 → 결손 발주 감소)

## 관련 이슈

- 1차: order-execution#food-systemic-underprediction (커밋 ab98bfc, [WATCHING])
- 본 이슈: order-execution#food-underprediction-secondary

## Issue-Chain
order-execution#food-underprediction-secondary
