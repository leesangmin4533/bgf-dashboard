# 푸드 예측 파이프라인 데이터 흐름 및 지표 코드 분석

> 분석일: 2026-03-03 | 분석 대상: 예측 파이프라인 코드의 데이터 처리 로직
> 사전 분석 참조: `food-prediction-store-analysis.md` (매장 데이터 기반)

---

## 1. 예측 파이프라인에서 계수가 누적 감소되는 경로 추적

### 1-1. 곱셈 파이프라인 전체 흐름

푸드류(001~005, 012)는 `DEMAND_PATTERN_EXEMPT_MIDS`에 해당하여 기존 **곱셈(multiplicative) 파이프라인**을 사용한다. 비푸드류는 덧셈(additive) 방식을 사용하므로 이 문제는 푸드에만 해당한다.

**코드 경로**:

```
ImprovedPredictor.predict()                           # improved_predictor.py:725
  -> _compute_base_prediction()                       # base_predictor.py:54
       -> _compute_wma()                              # base_predictor.py:99
            WMA(7일) + Feature블렌딩 = base_prediction
  -> _apply_all_coefficients()                        # improved_predictor.py:920
       -> CoefficientAdjuster._apply_multiplicative() # coefficient_adjuster.py:524
            base *= holiday_coef                      # :532
            base *= weather_coef (precip 포함)        # :536
            base *= food_wx_coef                      # :543
            base *= food_precip_coef                  # :550
            adjusted = base * weekday_coef            # :556
            adjusted *= seasonal_coef                 # :565
            adjusted *= assoc_boost                   # :574
            adjusted *= trend_adjustment              # :582
            compound_floor = base * 0.15              # :591 (바닥값)
  -> _compute_safety_and_order()                      # improved_predictor.py:1031
       -> get_unified_waste_coefficient()             # food.py:836
            adjusted_prediction *= unified_waste_coef # improved_predictor.py:1178
```

### 1-2. 각 계수의 감소 범위

| 단계 | 계수 | 범위 | 감소 방향 조건 | 파일:라인 |
|------|------|------|---------------|----------|
| 1 | holiday_coef | 0.7 ~ 2.5 | post_holiday=0.90 | coefficient_adjuster.py:214 |
| 2 | weather_coef | 0.82 ~ 1.15 | 폭서 30도+: 0.90, 폭우 80%+: 0.85, 눈: 0.82 | coefficient_adjuster.py:35-113 |
| 3 | food_wx_coef | 0.85 ~ 1.10 | 기온x푸드 교차 (혹한기 김밥 0.90, 폭염 도시락 0.93) | food.py:987 |
| 4 | food_precip_coef | 0.82 ~ 1.10 | 강수x푸드 교차 | food.py (get_food_precipitation_cross_coefficient) |
| 5 | weekday_coef | 0.80 ~ 1.25 | DB 동적 계수 (일요일 등) | food.py:get_food_weekday_coefficient |
| 6 | seasonal_coef | 0.85 ~ 1.15 | 계절별 카테고리 보정 | prediction_config.py:get_seasonal_coefficient |
| 7 | trend_adjustment | 0.85 ~ 1.15 | 하락 트렌드 시 | base_predictor.py (FeatureCalculator) |
| 8 | unified_waste_coef | 0.70 ~ 1.00 | 폐기 실적 있으면 항상 감소 | food.py:836 |

### 1-3. 최악 케이스 복합 감소 계산

모든 감소 방향 계수가 동시 적용되는 시나리오:

```
base_prediction = 1.00 (WMA 결과)

x holiday       = 0.90 (연휴 후)
x weather       = 0.85 (폭우 80%+)
x food_wx       = 0.90 (혹한기 김밥)
x food_precip   = 0.85 (폭우x푸드 교차)
x weekday       = 0.80 (일요일)
x seasonal      = 0.90 (비수기)
x trend         = 0.85 (하락 트렌드)
x unified_waste = 0.70 (폐기율 30%+)

= 1.00 x 0.90 x 0.85 x 0.90 x 0.85 x 0.80 x 0.90 x 0.85 x 0.70
= 0.247 (조정 전 대비 75.3% 감소)
```

그러나 compound_floor(`base_prediction * 0.15`)가 `coefficient_adjuster.py:591`에서 적용되므로 실제 바닥은 **base_prediction의 15%**이다. 다만 이 floor는 unified_waste_coef 적용 **이전**의 adjusted_prediction에만 적용된다.

### 1-4. 핵심 문제: 2단계 곱셈 구조

**compound_floor(15%)는 _apply_multiplicative()에서 적용**되지만, **unified_waste_coef는 _compute_safety_and_order()에서 별도로 곱해진다**. 즉:

```python
# Step 1: coefficient_adjuster.py:591
compound_floor = base_prediction * 0.15  # 여기서 바닥 적용

# Step 2: improved_predictor.py:1178
adjusted_prediction *= unified_waste_coef  # 바닥 이후 추가 감소!
```

이로 인해 compound_floor 15%를 통과한 값이 다시 unified_waste_coef(최대 0.70)에 의해 추가 감소된다. **최종 바닥은 base_prediction의 0.15 x 0.70 = 10.5%**가 된다.

**누락된 보호 장치**: `_compute_safety_and_order()`에서 unified_waste_coef를 적용한 후 별도의 compound floor가 없다.

---

## 2. WMA가 0-sale 일을 처리하는 방식

### 2-1. 데이터 조회: 달력일 기준 LEFT JOIN

`data_provider.py:83-104`에서 WMA 입력 데이터를 조회한다:

```sql
WITH RECURSIVE dates(d) AS (
    SELECT date('now', '-1 day')
    UNION ALL
    SELECT date(d, '-1 day')
    FROM dates
    WHERE d > date('now', '-' || ? || ' days')
)
SELECT
    dates.d AS sales_date,
    COALESCE(ds.sale_qty, 0) AS sale_qty,   -- 레코드 없는 날 = 0
    ds.stock_qty AS stock_qty               -- 레코드 없는 날 = NULL
FROM dates
LEFT JOIN daily_sales ds ON ...
ORDER BY dates.d DESC
```

**핵심**: `COALESCE(ds.sale_qty, 0)` -- 판매 기록이 없는 날은 **sale_qty=0**으로 채워진다. 이 0이 WMA에 직접 포함된다.

### 2-2. 품절일 Imputation

`base_predictor.py:258-289`에서 품절일(stock_qty=0)의 sale_qty를 비품절일 평균으로 대체한다:

```python
if stockout_cfg.get("enabled", False) and has_stock_info:
    available = [(d, qty, stk) for d, qty, stk in sales_history
                 if stk is not None and stk > 0]
    stockout = [(d, qty, stk) for d, qty, stk in sales_history
                if stk is not None and stk == 0]
    # 신선식품은 stock_qty=None도 품절 취급
    if include_none_as_stockout:
        none_days = [(d, qty, stk) for d, qty, stk in sales_history
                     if stk is None]
        stockout = stockout + none_days

    if available and stockout:
        avg_available_sales = sum(row[1] for row in available) / len(available)
        # 품절일의 판매량을 비품절일 평균으로 대체
        for row in sales_history:
            if row[2] == 0 or (row[2] is None and include_none_as_stockout):
                imputed_history.append((row[0], avg_available_sales, row[2]))
```

### 2-3. 0-sale 일의 3가지 유형과 처리

| 유형 | stock_qty | sale_qty | imputation 대상? | WMA 입력값 |
|------|-----------|----------|-----------------|-----------|
| (A) 확인된 품절 | 0 | 0 | **Yes** | avg_available_sales |
| (B) 미수집일 | NULL | 0 | 신선식품만 Yes | 0 또는 avg (신선) |
| (C) 재고 있지만 안 팔림 | >0 | 0 | **No** | **0** (그대로) |

### 2-4. 구조적 문제: "재고 있지만 안 팔림" 0-sale 일

**유형 (C)가 WMA를 0으로 수렴시키는 핵심 원인이다.**

일평균 판매 1개 미만인 상품(대부분의 푸드 개별 상품)에서:
- 7일 중 5일은 stock_qty>0, sale_qty=0 (진열 중이지만 판매 없음)
- 7일 중 2일은 sale_qty=1~2 (가끔 판매)

이 경우 imputation이 적용되지 않고, 5개의 0이 WMA에 직접 포함되어:

```
WMA = (1*0.25 + 0*0.20 + 0*0.15 + 0+0+1+0 * 0.40/4) / (0.25+0.20+0.15+0.10*4)
    = (0.25 + 0.10) / 1.0
    = 0.35
```

실제 판매일만 평균하면 1.0인데, WMA는 0.35로 계산된다. 이것이 기존 분석에서 보인 "raw_pred가 actual의 60~85%" 현상의 직접적 원인이다.

### 2-5. sell_day_ratio 69~81% 상품의 구조적 과소예측

`food-prediction-store-analysis.md` 7-5절의 데이터:

| 매장 | mid_cd | sell_ratio | 의미 |
|------|--------|-----------|------|
| 46513 | 003 샌드위치 | 69.6% | 7일 중 ~5일 판매, ~2일 미판매 |
| 46513 | 001 도시락 | 73.2% | 7일 중 ~5일 판매, ~2일 미판매 |

sell_ratio 70%인 상품에서 WMA가 0-sale 일을 포함하면 실제 수요의 약 70%만 예측한다. 그 후 곱셈 계수(40~54% 추가 감소)가 적용되면:

```
실제 일평균: 1.0개
WMA 결과:   0.70개 (0-sale 일 포함)
계수 적용:  0.70 x 0.60(계수 누적) = 0.42개
최종 편향: -58%
```

이것이 기존 분석에서 보인 adjusted_prediction 편향 -58~-86%와 정확히 일치한다.

### 2-6. 누락된 보정: 판매일 기준 평균 폴백

현재 코드에는 `sell_day_ratio`가 낮은 상품에 대한 WMA 보정이 있지만(`base_predictor.py:150-172`), 이것은 WMA를 추가로 **감소**시키는 방향으로만 작동한다:

```python
if sell_day_ratio < very_intermittent_threshold:  # 0.3 미만
    base_prediction = max(base_prediction * 0.5, min_prediction_floor)  # 감소!
elif sell_day_ratio < intermittent_threshold:  # 0.6 미만
    base_prediction *= sell_day_ratio  # 감소!
```

sell_ratio 0.6~1.0 범위(대부분의 푸드 상품)에서는 아무 보정도 없이 0-sale 포함 WMA가 그대로 사용된다.

---

## 3. 입고율(fulfillment rate)이 예측/발주에 반영되는지

### 3-1. 코드 검색 결과

`fulfillment`, `fill_rate`, `입고율`, `accept_rate`, `delivery_rate` 키워드로 예측/발주 코드를 검색한 결과:

- `src/prediction/` 디렉토리: **입고율 관련 로직 없음**
- `src/order/order_adjuster.py`: **입고율 반영 없음**
- `src/prediction/ml/feature_builder.py:105`: `short_delivery_rate` (숏배송율)가 ML 피처에 포함되어 있으나, 이는 입고율과 다른 개념 (배송 시간 관련)

### 3-2. 데이터 수집에서의 buy_qty

`buy_qty`(실제 입고량)는 daily_sales에 수집되며 ord_qty(발주량)와 함께 저장된다:

- `direct_sales_fetcher.py:53`: `ORD_QTY, BUY_QTY` 수집
- `sales_repo.py:197`: `ord_qty, buy_qty` DB 저장
- `order_status_collector.py:474`: `ord_qty > buy_qty` 비교로 미입고 감지

그러나 이 데이터는 **order_tracking의 미입고(pending) 계산**에만 사용되고, **발주량 보정에는 사용되지 않는다**.

### 3-3. 현재 미입고 처리 방식

발주량 계산 시 미입고(pending_qty)는 **차감** 요소로만 사용된다:

```python
# improved_predictor.py:1365
need_qty = adjusted_prediction + lead_time_demand + safety_stock
           - effective_stock - pending_qty
```

이는 "아직 안 온 물건이 올 것"이라는 가정이다. 그러나 실제로 46704의 입고율이 44~78%인 상황에서, pending_qty가 모두 입고된다는 가정은 과대 차감을 초래한다.

### 3-4. 누락된 로직: 입고율 보정

| 항목 | 현재 상태 | 필요한 로직 |
|------|----------|-----------|
| pending 차감 | 100% 입고 가정 | `pending_qty * fulfillment_rate` |
| 발주량 보정 | 없음 | `order_qty / fulfillment_rate` (46704: /0.70) |
| 입고율 계산 | 데이터 존재하나 미사용 | `SUM(buy_qty) / SUM(ord_qty)` per item/mid_cd |
| 입고율 저장 | 없음 | item/mid_cd별 최근 14일 입고율 캐시 |

**영향 추정**:
- 46704 mid_cd=004 (햄버거): 입고율 44% -> 발주 10개 중 4~5개만 입고
- 현재: need=5 -> 발주=5 -> 입고=2 -> 부족
- 보정 후: need=5 -> 발주=5/0.44=12 -> 입고=5 -> 충족

---

## 4. 기회손실(stockout) 추적 로직 존재 여부

### 4-1. 기존 stockout 관련 코드

| 모듈 | 기능 | 파일 | 예측 피드백 연결 |
|------|------|------|----------------|
| pre_order_evaluator | stockout_freq 계산 (30일 stock=0 비율) | pre_order_evaluator.py:206 | eval_outcomes DB에 저장 |
| eval_calibrator | was_stockout 판정 (next_day_stock <= 0) | eval_calibrator.py:259 | 평가 결과에 기록 |
| eval_config | stockout_freq_threshold 파라미터 | eval_config.py:111 | 발주 판정 임계값 조정 |
| DB schema | was_stockout, stockout_freq 컬럼 | models.py:360,447 | 저장만 |

### 4-2. stockout -> 예측 피드백 경로 분석

**결론: stockout 데이터가 예측값을 증가시키는 피드백 루프는 존재하지 않는다.**

현재 흐름:

```
stock=0 + sale>0 (기회손실)
  -> pre_order_evaluator._get_stockout_frequency() -> stockout_freq 계산
  -> eval_outcomes 테이블에 저장
  -> eval_calibrator: 판정 결과에 반영 (SHORTAGE/ACCEPT/OVER_ORDER)
  -> eval_calibrator._update_params(): stockout_freq_threshold 파라미터 조정
  -> (여기서 끝 -- 예측 파이프라인에 되먹임 없음)
```

`stockout_freq_threshold`는 **사전 평가(pre-order evaluation)**에서 "이 상품을 발주할지 말지"를 결정하는 데 사용되며, **예측 수량 자체를 늘리지는 않는다**.

### 4-3. WMA의 구조적 한계: stockout은 수요를 숨긴다

stockout 상황에서:
- 재고=0이므로 판매 기회가 제한됨
- 실제 수요가 3개일 수 있지만 재고 1개만 있으면 sale_qty=1
- WMA는 이 1을 "실제 수요"로 학습
- 결과적으로 수요가 과소추정되어 다음 발주도 적음 -> 또 stockout -> 악순환

**품절일 imputation**이 이 문제를 부분적으로 해결하지만, stock_qty=0인 날에만 적용되고, stock_qty>0이지만 부족(수요 > 재고)인 날에는 적용되지 않는다.

### 4-4. 누락된 지표: 잠재 수요(censored demand)

| 지표 | 설명 | 현재 상태 |
|------|------|----------|
| stock_qty=0, sale_qty>0 | 확인된 품절 + 판매 | stockout_freq로 계산됨, 피드백 없음 |
| stock_qty=0, sale_qty=0 | 확인된 품절 + 미판매 | imputation 대상 |
| stock_qty>0 but < demand | 부분 충족 (잠재수요 > 판매) | **감지 불가** |
| 기회손실 금액 | (잠재수요 - 판매) x 매가 | **계산 없음** |

---

## 5. 캘리브레이터가 "증가 방향"으로 조정할 수 있는지

### 5-1. 양방향 조정 코드 확인

**결론: 증가 방향 조정은 구현되어 있다.** (기존 분석 문서의 "단방향 설계" 판정은 부정확)

`food_waste_calibrator.py:567-635`:

```python
if error > 0:
    # 폐기율이 목표보다 높음 -> 발주 줄이기
    param_name, old_val, new_val = self._reduce_order(new_params, expiry_group, error)
else:
    # 폐기율이 목표보다 낮음 -> 품절 위험, 발주 늘리기
    param_name, old_val, new_val = self._increase_order(new_params, expiry_group, error)
```

`_increase_order()` 메서드 (`food_waste_calibrator.py:741-782`):

```python
def _increase_order(self, params, expiry_group, error):
    # 1순위: safety_days 증가
    sd_range = FOOD_WASTE_CAL_SAFETY_DAYS_RANGE.get(expiry_group, (0.35, 0.8))
    new_sd = min(sd_range[1], round(old_sd + step, 3))

    # 2순위: gap_coefficient 증가
    gc_range = FOOD_WASTE_CAL_GAP_COEF_RANGE.get(expiry_group, (0.2, 0.7))
    new_gc = min(gc_range[1], round(old_gc + step, 3))

    # 심각한 과소발주(오차 10%p+) 시 step 2.0배 가속 (최대 0.12)
    if abs_error > 0.10:
        step = min(round(step * 2.0, 3), 0.12)
```

### 5-2. 히스테리시스 비대칭

**증가 방향에는 히스테리시스가 면제된다** (`food_waste_calibrator.py:598-603`):

```python
# 4. 히스테리시스 체크 -- 연속 2일 같은 방향이어야 조정
#    단, 폐기율 < 목표 (error < 0 = 품절 위험) 시에는 면제하여 빠른 회복 허용
if error > 0:  # 폐기율 초과일 때만 히스테리시스 적용
    if not self._check_consistent_direction(mid_cd, error):
        return  # 감소 방향: 2일 연속 같은 방향 필요
# error < 0 (과소발주): 히스테리시스 면제 -> 즉시 증가
```

이것은 과소발주 상황에서 빠른 복구를 위한 올바른 설계이다.

### 5-3. compound floor 도달 후의 행동

compound floor(0.15)에 도달한 상태에서의 동작:

| 시나리오 | error | _reduce_order | _increase_order | 결과 |
|----------|-------|--------------|----------------|------|
| 폐기율 > 목표 | > 0 | compound floor 체크 -> 감소 중단 | - | **조정 불가** (at_limit) |
| 폐기율 < 목표 | < 0 | - | compound floor 체크 없음 -> 증가 가능 | **증가 실행** |

**compound floor는 _reduce_order()에서만 체크되고, _increase_order()에서는 체크하지 않는다.** 따라서 floor에 도달한 후에도 폐기율이 목표보다 낮으면(과소발주) safety_days와 gap_coefficient를 다시 올릴 수 있다.

### 5-4. 증가 방향의 실질적 한계

| 파라미터 | ultra_short 하한 | ultra_short 상한 | 현재값(46513 001) | 증가 여지 |
|----------|-----------------|-----------------|-------------------|----------|
| safety_days | 0.35 | 0.80 | 0.36 | +0.44 |
| gap_coefficient | 0.20 | 0.70 | 0.40 | +0.30 |

46513 001은 safety_days=0.36으로 하한(0.35) 근접이지만, 상한(0.80)까지 증가 가능하다. **문제는 폐기율이 목표(20%) 초과이므로 _increase_order()가 호출되지 않는다는 것이다.**

### 5-5. 근본 문제: 캘리브레이터의 입력 데이터

캘리브레이터는 `폐기율 = waste / (sold + waste)`를 기준으로 판단한다. 46513 001의 폐기율이 21.1%로 목표 20%를 초과하므로, 캘리브레이터는 "발주를 줄여야 한다"고 판단한다.

그러나 기존 분석에서 46513 001의 stockout rate는 **70.5%**이다. 즉:
- 70%의 경우에서 재고가 없어 판매를 놓치고
- 30%의 경우에서만 제대로 팔리거나 폐기됨
- 그 30% 중 21%가 폐기

**캘리브레이터는 기회손실(70%)을 전혀 고려하지 않고, 가시적인 폐기(21%)만 본다.**

이것은 데이터 관점에서 **관측 편향(observation bias)**이다. stockout 상품은 판매/폐기 레코드가 없으므로 캘리브레이터의 분모(sold+waste)에 포함되지 않는다. 발주를 늘리면 판매와 폐기가 동시에 증가하지만, 판매 증가분이 더 크다면(기회손실 해소) 폐기율은 오히려 낮아질 수 있다.

---

## 6. 예측값이 0이 되는 경로

### 6-1. base_prediction = 0 경로

| # | 조건 | 파일:라인 | 빈도 추정 |
|---|------|----------|----------|
| 1 | sales_history가 비어있음 | base_predictor.py:252 | 신규 상품 |
| 2 | 7일간 모든 날 sale_qty=0 (0-sale 전부) | base_predictor.py:353 | 저회전 상품 |
| 3 | DemandClassifier pattern=slow | base_predictor.py:79-86 | sell_day_ratio<15% |
| 4 | Feature 블렌딩 결과 0 | base_predictor.py:119-134 | 드물음 |

### 6-2. adjusted_prediction -> 0 경로

| # | 조건 | 파일:라인 |
|---|------|----------|
| 1 | base_prediction = 0이면 모든 곱셈 결과 0 | coefficient_adjuster.py:524+ |
| 2 | weekday_coef = 0 (이론적으로 불가능, 최소 0.80) | - |
| 3 | compound_floor = base * 0.15 에서 base=0이면 floor=0 | coefficient_adjuster.py:591 |

### 6-3. order_qty = 0 경로 (need_qty <= 0)

| # | 조건 | 파일:라인 | 빈도 추정 |
|---|------|----------|----------|
| 1 | adjusted_prediction + safety_stock < stock + pending | improved_predictor.py:1365 | **가장 빈번** |
| 2 | food_skip_order = True (재고 >= 유통기한+1일 x daily_avg) | improved_predictor.py:1225 | 중간 |
| 3 | 가용재고가 다음 발주일까지 충분 | improved_predictor.py:1381-1391 | 중간 |
| 4 | ramen/tobacco/beer/soju_skip_order | improved_predictor.py:1405-1419 | 비푸드 |
| 5 | need < min_order_threshold (0.1) | prediction_config.py:473 | 낮음 |
| 6 | adjusted_prediction = 0 (6-2에서) | - | 저회전 |
| 7 | new_cat_skip_order | improved_predictor.py:1418 | 비푸드 |

### 6-4. 발주 스킵 비율과 근본 원인

기존 분석 데이터 (food-prediction-store-analysis.md 2-3):

| 매장 | mid_cd | pred=0 비율 | pred>0 but ord=0 | 실제 발주율 |
|------|--------|------------|-----------------|-----------|
| 46513 | 001 | 49% | 17% | 34% |
| 46513 | 012 | 46% | 36% | 18% |
| 46704 | 012 | 54% | 24% | 22% |

**pred=0 비율 46~54%의 근본 원인 추적**:

```
pred=0 (49%)
  ├── WMA=0: 7일간 sale_qty 전부 0 (경로 6-1 #2)
  │     └── 상품이 7일간 미판매 (저회전)
  │           └── 해당 상품은 간헐적으로만 팔림 (3~5일에 1번)
  │                 └── 7일 WMA 윈도우에 판매일이 없을 확률:
  │                     sell_ratio 70% → (1-0.70)^7 = 0.022 (2.2%)
  │                     sell_ratio 50% → (1-0.50)^7 = 0.008 (0.8%)
  │                     → 단순 확률로는 설명 안 됨
  │
  ├── WMA>0이지만 계수 적용 후 0: compound_floor로 인해 거의 불가능
  │
  └── DemandClassifier pattern=slow: sell_day_ratio<15%
        └── 이 상품은 60일 중 9일 미만 판매 → 예측 스킵
```

**추가 분석 필요**: pred=0 49%가 WMA=0 때문인지, pattern=slow 때문인지 로그 분석 필요. WMA 윈도우 7일에 sale=0이 7일 연속일 확률이 2%인데 pred=0이 49%라면, 이 상품들 중 대부분이 DemandClassifier에 의해 slow로 분류된 것으로 추정된다.

### 6-5. need_qty가 음수가 되는 주요 조건

```python
need_qty = adjusted_prediction + lead_time_demand + safety_stock
           - effective_stock - pending_qty
```

need_qty < 0이 되려면:

```
effective_stock + pending_qty > adjusted_prediction + safety_stock
```

유통기한 1일 이하 푸드: `effective_stock = 0`이므로 `pending_qty > adjusted_prediction + safety_stock`일 때만 음수. 대체로 need_qty > 0이 되어야 하지만, adjusted_prediction이 0.3이고 safety_stock이 0.2이면, pending_qty=1만 있어도 need_qty < 0이 된다.

---

## 종합 데이터 흐름 진단

### 누적 감소 파이프라인 시각화

```
실제 일평균 수요: 1.0개/일

[Stage 1: WMA 과소추정]
  7일 중 5일 판매, 2일 미판매 (sell_ratio=71%)
  WMA = (판매일 평균 1.2) x (7일 기준 희석) ≈ 0.60
  → 이미 40% 과소

[Stage 2: 곱셈 계수 적용]
  holiday(1.0) x weather(0.95) x weekday(0.90) x seasonal(1.0)
  x food_wx(0.95) x food_precip(1.0) x trend(0.95) x assoc(1.0)
  = 0.60 x 0.81 = 0.49
  → 추가 19% 감소, 누적 51% 감소

[Stage 3: 통합 폐기 계수]
  unified_waste_coef = 0.85 (폐기율 15%)
  = 0.49 x 0.85 = 0.42
  → 추가 15% 감소, 누적 58% 감소

[Stage 4: 안전재고 계산]
  safety_stock = daily_avg(0.42) x safety_days(0.70) = 0.29
  → 안전재고도 과소추정된 daily_avg에 기반

[Stage 5: need_qty 계산]
  need = 0.42 + 0.29 - stock(0) - pending(0) = 0.71
  → 반올림: order_qty = 1

[실제 필요량]
  실제 수요 1.0 + 안전재고 0.7 = 1.7
  → 적정 발주: 2개
  → 실제 발주: 1개 (47% 부족)
```

### 누락된 피드백 루프 요약

| # | 피드백 경로 | 현재 상태 | 영향 |
|---|-----------|----------|------|
| 1 | stockout -> 예측 증가 | **없음** | 과소예측 악순환 지속 |
| 2 | 입고율 -> 발주량 보정 | **없음** | 46704 실입고 44~78% |
| 3 | 잠재수요(censored demand) | **감지 불가** | WMA가 관측 판매만 학습 |
| 4 | 기회손실 금액 | **계산 없음** | 폐기 vs 품절 트레이드오프 불가 |
| 5 | 폐기계수 -> 과소예측 시 면제 | **없음** | stockout 70%인데 폐기계수 적용 |
| 6 | compound floor 후 adjusted_prediction 추가 감소 | **보호 없음** | 최종 바닥 10.5% |

### 데이터 관점에서의 근본 원인

```
1. 관측 편향 (Observation Bias)
   - 재고가 없으면 판매가 없음 -> 수요가 없다고 기록
   - WMA는 "관측된 판매"만 학습, "잠재 수요"는 보이지 않음
   - imputation이 stock=0인 날만 보정, stock>0 but insufficient는 미보정

2. 단방향 최적화 (One-sided Optimization)
   - 폐기율만 KPI로 추적 → 계수는 감소 방향으로만 누적
   - 기회손실은 폐기율 공식에 포함되지 않음
   - 캘리브레이터가 기회손실을 인지하려면 error < 0 (폐기율 < 목표)이어야 하는데,
     과소발주로 인해 판매도 줄고 폐기도 줄어 폐기율은 오히려 안정적으로 보임

3. 곱셈 계수의 비대칭 효과
   - 10개 계수가 모두 독립적으로 감소 가능 (worst case 복합 0.25x)
   - compound floor(15%)는 1차 계수에만 적용, 2차 waste_coef는 별도
   - 증가 방향 계수(holiday 1.4, hot_boost 1.15)는 단일 이벤트에 종속
     감소 방향 계수(waste 0.70, weather 0.85, weekday 0.80)는 상시 적용
```
