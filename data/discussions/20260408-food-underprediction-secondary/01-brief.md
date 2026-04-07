# Brief: food-underprediction-secondary 설계 결정 토론

## 토론 모드
**트레이드오프** — Design 단계 진입 전 3개 핵심 결정 항목 조율

## 1. 현재 상황

BGF 리테일 4매장 푸드 카테고리(001~005,012)에서 1차 수정(ab98bfc, base_predictor.py WMA 만성-품절 imputation) 이후에도 has_stock 그룹 158건이 평균 bias **-0.22 ~ -0.39** 의 약한 과소예측 잔존.

| 그룹 | n (4매장) | bias | 상태 |
|---|---|---|---|
| all_stockout | 162 | -0.49~-0.71 | ✅ 1차 수정됨 |
| **has_stock** | **158** | **-0.22~-0.39** | ⚠️ 본 토론 대상 |

## 2. 이미 구현된 기능 (인벤토리)

| 모듈 | 기능 |
|---|---|
| `src/prediction/base_predictor.py:calculate_weighted_average` | WMA + stockout imputation (available/stockout 분기) |
| `src/prediction/base_predictor.py` (방금 추가) | not-available + stockout 분기 (만성 품절 imputation, 1차 수정) |
| `src/prediction/utils/outlier_handler.py` | clean_sales_data — sales 이상치 처리 |
| `src/prediction/prediction_config.py` | PREDICTION_PARAMS (stockout_filter, holiday_wma_correction, category_floor 등) |
| `src/db/models.py` | prediction_logs 컬럼: stage_trace, rule_order_qty, ml_order_qty, ml_weight_used 등 (현재 NULL) |
| FoodStrategy | food/dessert 곱셈 체인: base × holiday × weather × weekday × season × assoc × trend |

## 3. 사전 조사 (49965 04-08 샘플)

### 예시 1: 8800279678588 (도시락)
- 7일: 5일 stock>0(sale=1) + 2일 stock=0(sale=0)
- 이론 imputation: avg_avail=1.0 → 2 stockout 일을 1.0으로 대체 → WMA 1.0
- 실제 `predicted_qty=0.78`, `weekday_coef=1.18`, `assoc=1.08`
- → **imputation 후에도 -22%** 발생. 단일 상품 한정이 아닌 158건 전반.

### 예시 2: 8800336392501 (도시락)
- 7일 모든 row 존재, 전부 `stock_qty=-1` (음수 sentinel)
- imputation 분기는 `stk>0`, `stk==0` 만 인식 → `-1`은 어느 그룹에도 안 들어가 보정 미적용
- sum sales 10/7=1.43, predicted=1.23 → -14%

### 예시 3: 49965 mid=004(샌드위치) `weekday_coef=0.84`
- 식육가공 -16% 적용. 다른 mid는 1.08~1.18.

### prediction_logs 컬럼 상태
- `stage_trace`, `rule_order_qty`, `ml_order_qty`, `ml_weight_used` **모두 NULL** (130건 전부)
- → **단계별 추적이 데이터 자체로 불가능** → 가설 검증을 막는 가장 큰 장벽

## 4. 토론 포인트 (3개 결정 항목)

### 결정 1: stage_trace 수집 범위
- **A안**: 전 상품 × 전 단계 (base→holiday→weather→weekday→season→assoc→trend→ML→DiffFeedback→cap)
- **B안**: 푸드 mid(001~005,012)만 + 핵심 5단계
- **C안**: 디버그 플래그 ON 한 상품/카테고리만 (옵트인)

기준: DB 부담 vs 분석 효율 vs 운영 안정성. prediction_logs 일일 row 수 약 4매장 × 1000상품 = 4000건. JSON blob 평균 500바이트 가정 시 일 2MB.

### 결정 2: 수정 깊이/순서
- **A안**: 가설 7개를 한 번에 패치 (가중치+계수+sentinel+outlier 동시)
- **B안**: stage_trace 먼저 켜고 → 1주 데이터 수집 → 가장 큰 기여 1개 식별 → 표적 패치
- **C안**: B안 + stock_qty<0 sentinel처럼 명백한 사각지대는 즉시 같이 패치

기준: 수정 안전성(회귀 위험) vs 효과 도달 속도 vs 검증 가능성. 1차 수정(ab98bfc)이 아직 [WATCHING] 상태로 04-09 검증 대기 중이라는 점 고려 필수.

### 결정 3: stock_qty < 0 sentinel 처리 정책
- **A안**: 정상 데이터로 취급 (현 상태) — 데이터 수집기가 의미 있는 음수를 보낼 수 있음
- **B안**: 미수집(`None`)으로 정규화하여 imputation 진입 — sentinel을 무의미로 간주
- **C안**: 데이터 수집기 측에서 -1을 0 또는 NULL로 정규화 — 근원 수정

기준: 데이터 수집기의 실제 -1 발생 의도 파악 필요. 4매장 14일치 합계 97건 (영향 작음, 그러나 명확).

## 5. 제약 조건

- **회귀 위험**: 1차 수정(ab98bfc) WATCHING 중 → 동시 다발 패치 시 효과 분리 불가
- **운영 시간**: 매일 07:00 daily_job 단일 윈도우 → 패치 후 다음날 07:30까지 검증 사이클 24h
- **DB schema 변경**: stage_trace 활성화는 DB_SCHEMA_VERSION 증가 + 마이그레이션 필요 (현 v74)
- **테스트**: pytest 20개 pre-existing fail 잔존 → 신규 회귀 식별 어려움

## 6. 기존 결정 / 관련 이슈

- 1차 이슈: order-execution#food-systemic-underprediction (커밋 ab98bfc, [WATCHING], 04-09 07:30 검증 예약)
- 묶음 가드 이슈: order-execution#bundle-guard-bypass-49965 (커밋 190b24f, [WATCHING])
- food-stockout-misclassify (이전): "일부 품절"만 처리, "전체 품절" 사각지대 잔존이 본 이슈를 낳음
- prediction-quick-wins(03-30): Rolling Bias + Stacking 100 도입 — 푸드에 부작용 가능성 (가설 후보 중 하나)
- food-cap-qty-fix(03-22): count→sum(qty) 변경 — food_daily_cap 조기 발동 가능성

## 7. 토론 출력 요구

각 결정에 대해:
1. A/B/C안 비교 테이블 (장단점)
2. 본 프로젝트 제약 하에서 권고안
3. 권고 근거 (특히 1차 수정 WATCHING 상태와 어떻게 충돌/조화되는지)
4. 결정 간 의존 관계 (예: 결정1이 결정2의 전제인가)
5. 통합 구현 순서 체크리스트 (Design 단계 작성용)
