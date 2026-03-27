# Design-Implementation Gap Analysis Report: 46704 도시락(001) 과잉발주

> **Summary**: 46704 매장 도시락 14개 발주 vs 일요일 평균 4.2개 판매, 설계 의도와 구현 간 갭 분석
>
> **Author**: gap-detector
> **Created**: 2026-03-22
> **Status**: Draft

---

## Analysis Overview
- **Analysis Target**: 46704 매장 도시락(mid_cd=001) 일요일 과잉발주
- **현상**: 14개 발주, 일요일 평균 판매 4.2개, 폐기율 ~32%
- **Design Documents**: CLAUDE.md, prediction_config.py, food_daily_cap skill
- **Implementation Path**: `src/prediction/`, `src/order/auto_order.py`
- **Analysis Date**: 2026-03-22

---

## Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| CategoryFloor 설계 정합성 | 60% | !!! |
| Promotion 부스트 적정성 | 75% | !! |
| 폐기 감량 시스템 실효성 | 85% | ! |
| Cap 시스템 정합성 | 50% | !!! |
| **Overall** | **67.5%** | !!! |

---

## Gap 1: CategoryDemandForecaster Floor 보충 -- 매장 규모 무시

### 설계 의도
- `category_floor`: 카테고리 총량 WMA 대비 개별 예측 합이 70% 미만이면 부족분 보충
- 목적: "품목 로테이션이 심한 신선식품에서 개별 예측 합이 실제 카테고리 총 수요에 크게 미달하는 문제"

### 실제 구현 (`category_demand_forecaster.py`)
```
threshold = 0.7
max_add_per_item = 1
min_candidate_sell_days = 1  (최근 7일 중 1일만 판매해도 후보)
```

### GAP: 매장 규모/판매량에 따른 차등 없음

**핵심 문제**: Floor 보충은 `카테고리 총량 WMA * 0.7`을 하한으로 설정하지만, 이 WMA 자체가 "기존에 과잉발주해서 판매가 늘어난 값"이 아닌 "실제 판매된 총량"을 기반으로 한다. 그러나:

1. **avg=0 품목에도 1개씩 보충**: `_get_supplement_candidates`는 최근 7일 중 1일이라도 판매가 있으면 후보에 포함. 일평균=0.1인 상품도 `sell_days >= 1`이면 후보가 된다.

2. **기존 발주 목록에 없는 품목만 보충**: 예측값이 0이어서 order_list에 안 들어간 품목이 Floor에 의해 추가됨. 46704처럼 도시락 일 판매 4.2개인 매장에서 category WMA가 5.0이라면, floor = 5.0 * 0.7 = 3.5개. 기존 예측 합이 2개라면 1.5개 부족 -> 후보 1~2개 추가.

3. **문제의 증폭**: Floor로 추가된 품목은 이후 Cap에서 `len(items)` (품목수)로 비교된다. Floor가 추가한 1개 품목 = 1개 발주이므로, 총 품목수가 증가해도 Cap의 `weekday_avg`가 적절하다면 걸러져야 하지만...

**설계 문서에 없는 것**: 매장 판매량 기반 Floor 차등 로직. 일 판매 4.2개 매장과 일 판매 40개 매장에 동일한 `threshold=0.7`, `max_add_per_item=1` 적용.

**영향**: LOW-MEDIUM. Floor는 max_add_per_item=1이고 카테고리 WMA에 비례하므로, 4.2개 판매 매장이면 floor 자체가 ~3개 수준이라 이 단독으로는 14개 과잉을 유발하지 않음. 다만 다른 시스템과의 합산 효과에 기여.

---

## Gap 2: Promotion 부스트 -- 고정 배율의 저판매 매장 부적합성

### 설계 의도
- 행사 중(Branch C): `daily_avg < promo_avg * 0.8` 이면 promo_need 기반 보정
- 행사 시작 임박(Branch B): `START_ADJUSTMENT = {3: 1.20, 2: 1.50, 1: 2.00}`

### 실제 구현 (`improved_predictor.py:2031-2230`, `promotion_adjuster.py:44-57`)

**Branch C (행사 안정기) 분석**:
```python
promo_daily_demand = promo_status.promo_avg * weekday_coef
promo_need = promo_daily_demand + safety_stock - current_stock - pending_qty
promo_order = int(max(0, promo_need))
if promo_order > order_qty:
    order_qty = promo_order
```

**Branch B (행사 시작 D-1)**: `adjusted_qty = int(base_qty * 2.00)` (2배)

### GAP: promo_avg가 다른 매장 또는 전체 매장 기반일 수 있음

avg=0.9인 상품에 행사가 걸리면:
- **Branch C**: promo_avg가 2.0이고 weekday_coef=1.0이면, promo_need = 2.0 + safety - stock - pending. stock=0이면 order=2~3개로 부풀릴 수 있다.
- **Branch B (D-1 시작)**: base_qty=1이면 2배=2개. base_qty=2이면 4개.

**핵심**: 행사 부스트에 **재고 체크(Fix B)** 가 있어서 `stock + pending >= promo_daily_demand`이면 스킵한다. 이것은 설계 대비 양호한 구현. 그러나 재고=0 + pending=0인 상태에서는 항상 부스트가 적용됨.

**avg=0.9 -> 3개가 되는 경로**:
- base_prediction=0.9, 폐기계수 적용 후 ~0.7, safety_stock ~ 0.3~0.5
- need_qty = 0.7 + 0.5 - 0(stock) - 0(pending) = 1.2 -> rule에서 ceil -> order_qty = 1~2
- 행사 Branch C: promo_need = 2.0 + 0.5 - 0 - 0 = 2.5 -> 3개
- **결론**: avg=0.9 -> 3개는 **행사가 있는 경우** 설계 의도에 부합. 행사가 없는데 3개라면 다른 경로가 관여.

**설계 문서에 없는 것**: 저판매 매장에서 promo_avg의 출처 (해당 매장 자체 행사 기간 판매 vs 전체 매장 평균). `PromotionManager.calculate_promotion_stats`가 해당 매장 DB에서 산출하므로, 매장 판매량에 비례하긴 함. 다만 행사 자체가 판매를 끌어올리는 self-fulfilling 효과 존재.

**영향**: MEDIUM. 행사 상품이 여러 개이고 각각 2~3개씩 부스트되면 합산이 크다.

---

## Gap 3: 폐기 감량 시스템 (get_unified_waste_coefficient)

### 설계 의도
- 폐기율 32%면: `max(0.70, 1.0 - 0.32 * 1.0)` = `max(0.70, 0.68)` = **0.70** (하한)
- 최대 30% 감량, 예측값에 곱하기 -> 과잉 발주 억제

### 실제 구현 분석

**경로**: `improved_predictor.py:1328-1374`
```python
unified_waste_coef = get_unified_waste_coefficient(item_cd, mid_cd, ...)
# 조건부 적용:
if stockout_freq > 0.50:
    effective_waste_coef = 1.0      # 면제!
elif stockout_freq > 0.30:
    effective_waste_coef = max(unified_waste_coef, 0.90)  # 완화
else:
    effective_waste_coef = unified_waste_coef  # 정상 적용
```

### GAP: 품절 빈도에 의한 폐기 감량 면제

**핵심 문제**: 저판매 매장에서 품절 빈도가 높은 경우(50%+), 폐기 감량 계수가 **완전 면제**됨. `stockout_freq = 1.0 - sell_day_ratio`. sell_day_ratio=0.4이면 stockout_freq=0.6 -> 면제.

46704 매장 도시락은:
- 일 평균 4.2개 판매 -> 품목별로는 0~2개 수준
- 많은 품목의 sell_day_ratio가 낮음 (7일 중 2~3일만 판매)
- stockout_freq > 0.50에 해당하는 품목이 많을 수 있음
- **결과**: 폐기율 32%인데도 폐기 감량 계수가 1.0(미적용)인 품목 다수 존재 가능

**설계 의도와의 일치 여부**: `food-stockout-balance-fix` 설계에서는 "품절이 잦은 상품은 폐기보다 품절 기회손실이 더 크므로 감량 면제"가 목적. 이 설계는 합리적이지만, **카테고리 전체가 과잉**인 상황에서는 개별 품목의 품절 면제가 전체 과잉을 심화시킴.

**영향**: HIGH. 폐기율 32%에서 감량 계수가 미작동이면, 해당 품목의 예측값이 30% 더 높게 유지됨.

---

## Gap 4: Cap 시스템 -- 수량 vs 품목수 불일치 (핵심 갭)

### 설계 의도
- `total_cap = round(weekday_avg) + effective_buffer`
- `effective_buffer = int(category_total * 0.20 + 0.5)`
- weekday_avg=4.2이면: `category_total = 4.2`, `buffer = int(4.2*0.2+0.5) = 1`, `total_cap = 4 + 1 = 5`
- **설계**: 도시락 총 5개 이하로 발주 제한

### 실제 구현 (`food_daily_cap.py:455-490`)

```python
# NOTE: total_cap은 수량 기반(weekday_avg)이지만, 비교 대상은 품목수(len(items)).
# 푸드류는 대부분 final_order_qty=1 이므로 품목수 ~ 수량이 성립하여 오차 허용범위.
current_count = len(non_cancel)
if current_count <= adjusted_cap:
    result.extend(non_cancel + cancel_items)  # 그대로 통과!
```

### GAP: 품목당 order_qty > 1인 경우 Cap 무력화

**핵심 문제**: Cap은 `len(items)` (품목수)으로 비교한다. 그런데:

1. 행사 부스트(Branch C)에서 개별 품목의 `final_order_qty`가 2~3개로 증가
2. Floor 보충도 `final_order_qty=1`이지만, 배수 정렬(order_unit>1)에서 올림 가능
3. 14개 발주가 5~6개 품목 x 2~3개/품목이면, `len(items)=5~6 <= cap=5` -> **Cap을 통과**

**수치 시뮬레이션 (weekday_avg=4.2, 일요일)**:
```
total_cap = round(4.2) + int(4.2*0.2+0.5) = 4 + 1 = 5
adjusted_cap = 5 (site_count=0 가정)

시나리오: 6개 품목이 order_list에 존재
- 품목A: final_order_qty=3 (행사 부스트)
- 품목B: final_order_qty=2
- 품목C: final_order_qty=2
- 품목D: final_order_qty=3 (행사 부스트)
- 품목E: final_order_qty=2
- 품목F: final_order_qty=2
총 수량: 14개
len(non_cancel) = 6 > adjusted_cap(5) -> Cap 발동, 5개로 절삭

BUT: 절삭 후 5개 품목의 수량 합 = 3+2+2+3+2 = 12개
-> 여전히 weekday_avg(4.2)의 약 3배
```

**더 심각한 시나리오**: 품목수가 cap 이하일 때
```
4개 품목:
- 품목A: qty=4 (행사)
- 품목B: qty=3 (행사)
- 품목C: qty=4 (행사)
- 품목D: qty=3 (행사)
len(items) = 4 <= cap(5) -> Cap 미발동!
총 수량: 14개 -- 과잉 그대로 통과
```

**설계 문서(food-daily-cap skill)**:
> "cap=요일평균+3" (원래 설계, 현재는 20% 버퍼로 변경됨)
> "각 상품 final_order_qty=1 유지" -- 이 전제가 깨지면 Cap이 무력화됨

**코드 주석도 인정하고 있음** (`food_daily_cap.py:456-458`):
> "total_cap은 수량 기반이지만 비교 대상은 품목수. 푸드류는 대부분 final_order_qty=1이므로 품목수 ~ 수량이 성립"

**결론**: 행사 부스트가 개별 품목 qty를 2~3배로 올린 후, Cap이 품목 **수**로만 체크하므로 실질적 수량 상한이 작동하지 않음. **이것이 14개 과잉발주의 주원인**.

**영향**: CRITICAL. Cap의 근본 메커니즘이 무력화되는 구조적 갭.

---

## Gap Summary

### Missing Features (설계 O, 구현 X)
| Item | Design Location | Description |
|------|-----------------|-------------|
| 매장 규모 기반 Floor 차등 | category_floor config | 저판매 매장에서 Floor 임계값/max_add 축소 로직 없음 |
| **수량 기반 Cap 체크** | food_daily_cap.py:456 주석 | `len(items)` 대신 `sum(final_order_qty)` 비교 미구현 |

### Changed Features (설계 != 구현)
| Item | Design | Implementation | Impact |
|------|--------|----------------|--------|
| Cap 비교 단위 | 수량(qty) 기반 상한 | 품목수(count) 기반 비교 | **CRITICAL** |
| 폐기감량+품절 면제 | 폐기율 32% -> 0.70 계수 | stockout>50% -> 1.0(면제) | HIGH |
| 행사 부스트 | 재고 체크 후 적용 | 재고=0+pending=0이면 항상 적용 | MEDIUM |

### Added Features (설계 X, 구현 O)
| Item | Implementation Location | Description |
|------|------------------------|-------------|
| stockout 기반 폐기계수 면제 | improved_predictor.py:1339-1362 | food-stockout-balance-fix |
| site_order_counts 차감 | food_daily_cap.py:459-470 | category-site-budget |
| cancel_smart Cap 제외 | food_daily_cap.py:477-480 | smart-cancel-qty0 |

---

## Root Cause Analysis: 14개 발주 경로 재구성

46704 매장 도시락(001) 일요일 발주 14개의 예상 발생 경로:

```
Step 1: 개별 품목 예측 (improved_predictor)
  - 품목별 WMA + 폐기계수 + 안전재고 -> order_qty
  - avg=0.9 품목: need_qty = 0.9 + 0.3(safety) - 0(stock) = 1.2 -> qty=1~2
  - avg=0 품목: need_qty = 0 -> qty=0

Step 2: ROP (재주문점)
  - 재고=0, sell_day_ratio<0.3, data_days>=7 -> qty=1
  - 추가 +2~3개

Step 3: 행사 부스트 (Branch C)
  - 행사 중 품목: promo_avg > daily_avg*0.8 -> promo_need 기반 보정
  - 개별 qty: 1->2~3개로 증가
  - 행사 4~5개 품목 x 3개 = 12~15개

Step 4: Floor 보충 (CategoryDemandForecaster)
  - category WMA = 5.0, floor = 5.0*0.7 = 3.5
  - 기존 합산이 이미 12~15개 > 3.5 -> Floor 미발동
  (Floor는 이 케이스에서는 문제 아님)

Step 5: Cap 체크 (food_daily_cap)
  - weekday_avg=4.2, total_cap = 4+1 = 5
  - len(items) = 5~6개 품목
  - 만약 5개 이하면: Cap 미발동 -> 14개 그대로 통과
  - 만약 6개 이상이면: 5개로 절삭 BUT 수량합=10~12개 (여전히 과잉)
```

**과잉의 주원인 순위**:
1. **Cap이 수량이 아닌 품목수 비교** (CRITICAL)
2. **행사 부스트가 개별 품목 qty를 2~3배로 증가** (HIGH)
3. **폐기 감량이 품절 면제로 미작동** (HIGH)
4. Floor 보충 (이 케이스에서는 영향 낮음)

---

## Recommended Actions

### [Immediate] A-1: Cap을 수량 합산 기반으로 변경

**파일**: `src/prediction/categories/food_daily_cap.py:482-490`

현재:
```python
current_count = len(non_cancel)
if current_count <= adjusted_cap:
    result.extend(non_cancel + cancel_items)
```

변경 제안:
```python
current_qty_sum = sum(i.get("final_order_qty", 1) for i in non_cancel)
if current_qty_sum <= adjusted_cap:
    result.extend(non_cancel + cancel_items)
else:
    # 수량 합 기준으로 select_items_with_cap 호출
    selected = select_items_with_cap_by_qty(non_cancel, adjusted_cap, ...)
    result.extend(selected + cancel_items)
```

**주의**: `select_items_with_cap`도 품목수 기반이므로, 수량 합산 기반 절삭 함수를 별도 구현해야 함. 품목 우선순위(proven/new) 유지하면서 누적 수량이 cap 초과하면 절삭.

**영향**: Cap이 실질적 상한으로 작동. weekday_avg=4.2 -> 총 5개 이하 보장.

### [Immediate] A-2: 행사 부스트에 카테고리 총량 가드 추가

**파일**: `src/prediction/improved_predictor.py:2174-2212` (Branch C)

현재는 개별 품목 단위로만 promo_need를 산출. 카테고리 총량 대비 개별 부스트 상한을 추가:
```python
# Branch C 내부에 추가:
if is_food_category(mid_cd):
    # 개별 행사 부스트가 카테고리 평균의 50%를 넘지 않도록 제한
    max_promo_order = max(1, int(weekday_avg * 0.5))
    promo_order = min(promo_order, max_promo_order)
```

**단점**: 카테고리 총량 정보(`weekday_avg`)가 개별 예측 함수 내에서 직접 접근 불가. `ctx`를 통해 전달하거나, Cap 단계에서 수량 기반 절삭(A-1)으로 대체 가능.

### [Medium-term] A-3: 폐기 감량 면제 조건 재검토

현재 `stockout_freq > 0.50`이면 폐기 감량 완전 면제. 저판매 매장에서는 대부분의 품목이 이 조건에 해당하여 감량 시스템이 전면 비활성화됨.

**제안**: 카테고리 전체 폐기율이 목표(20%)를 크게 초과하는 경우, 개별 품목 면제를 부분적으로 회수
```python
# mid_cd 전체 폐기율 > 25% 이면 면제를 완화:
if stockout_freq > 0.50 and category_waste_rate > 0.25:
    effective_waste_coef = max(unified_waste_coef, 0.85)  # 완전 면제 대신 최소 15% 감량
```

### [Documentation] A-4: Cap 설계 주석 업데이트

`food_daily_cap.py:456-458`의 주석이 "품목수 ~ 수량 성립"을 전제하지만, 행사 부스트와의 상호작용에서 이 전제가 깨지는 것을 문서화해야 함.

---

## Synchronization Options

| # | Option | Description |
|---|--------|-------------|
| 1 | **구현 수정 (A-1 우선)** | Cap을 수량 합산 기반으로 변경 -> 설계 의도에 부합 |
| 2 | 설계 업데이트 | 현재 구현(품목수 기반 Cap)을 의도적 설계로 문서화 -> 비권장 |
| 3 | 통합 수정 | A-1(Cap) + A-3(폐기 면제 완화) 동시 적용 -> 최적 |
| 4 | 차이를 의도적으로 기록 | "품목수 기반 Cap은 행사 부스트 환경에서 한계가 있음" 기록 |

**권장**: Option 3 (A-1 + A-3 통합)

---

## Match Rate Calculation

| Category | Items Checked | Matched | Score |
|----------|:------------:|:-------:|:-----:|
| CategoryFloor 설계 정합성 | 5 | 3 | 60% |
| Promotion 부스트 적정성 | 4 | 3 | 75% |
| 폐기 감량 시스템 실효성 | 4 | 3.5 | 85% |
| Cap 시스템 정합성 | 4 | 2 | 50% |
| **Total** | **17** | **11.5** | **67.5%** |

### Category Details

**CategoryFloor (60%)**:
- [O] threshold 0.7 구현됨
- [O] max_add_per_item=1 구현됨
- [O] WMA 기반 카테고리 총량 산출
- [X] 매장 규모 차등 없음
- [X] avg=0 + sell_days=1이면 후보 포함 (과잉 후보 선정)

**Promotion (75%)**:
- [O] Fix B 재고 체크 구현됨
- [O] Branch A/B/C/D 분기 구현됨
- [O] on-demand 통계 산출 구현됨
- [X] 저판매 매장에서 행사 배율의 절대 상한 없음

**폐기 감량 (85%)**:
- [O] 통합 계수 공식 정상 (max(0.70, 1.0 - rate))
- [O] IB 70% + OT 30% 가중
- [O] compound floor 0.20 보장
- [~] stockout 면제가 설계 의도대로이나 저판매 매장에서 과도한 면제 효과

**Cap (50%)**:
- [O] weekday_avg 기반 total_cap 산출
- [O] 20% effective_buffer 적용
- [X] 비교가 수량이 아닌 품목수 (설계-구현 불일치)
- [X] 행사 부스트 후 개별 qty>1 상황에서 Cap 무력화

---

## Post-Analysis Action

Match Rate **67.5% < 70%**: 설계와 구현 간 유의미한 갭 존재. Cap 시스템의 구조적 결함이 과잉발주의 주원인.

**권장 다음 단계**:
1. A-1 (Cap 수량 합산 기반 변경) 설계서 작성
2. A-3 (폐기 면제 조건 완화) 설계서 작성
3. `/pdca plan food-cap-qty-fix` 실행
