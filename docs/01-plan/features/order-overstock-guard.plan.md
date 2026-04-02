# Plan: 안전재고 기반 과잉발주 방지 (friday_boost 가드 + daily_avg 이상치 처리)

## 1. 개요

### 문제
산토리나마비어캔500ml(8801021235240) @ 46513: **재고 19개, WMA=0.0(15일 무판매)인데 6개 추가 발주**

```
[원인 체인]
1. daily_avg = 37개/30일 = 1.23 (3/7에 18개 대량판매로 부풀림)
2. beer.py: safety_stock = 1.23 × 3일(금요일) × safety_days → 인자 전달 과정에서
   실제: analyze_beer_pattern()이 DB 직접 조회 → daily_avg=8.23 산출 → safe=24.7
3. need = 0.0(adj) + 0.0(lead) + 24.7(safe) - 19(stk) - 0(pnd) = 5.7
4. friday_boost: 5.7 × 1.15 = 6.6 → 규칙 적용 후 7
5. ml_ensemble: 7 → 8
6. round_floor: 8 → 6 (배수=6)
```

### 근본 원인 3가지

| # | 원인 | 위치 | 영향 |
|---|------|------|------|
| 1 | **beer.py daily_avg가 `analysis_days=30일` 전체 SUM/COUNT** — 3/7의 18개 이상치 미제거 | `beer.py:198` | safety_stock 과대 산정 |
| 2 | **friday_boost에 WMA=0 가드 없음** — 무판매 상품도 금요일+맥주면 무조건 부스트 | `improved_predictor.py:3185` | 0수요 상품에 발주 트리거 |
| 3 | **overstock_prevention이 daily_avg=0이면 스킵** — stock_days 계산 불가로 과잉재고 미감지 | `improved_predictor.py:3200` | 재고 19개인데 가드 통과 |

### 목표
1. WMA=0 + 충분한 재고 상태에서 안전재고만으로 발주 트리거되지 않도록 방지
2. daily_avg 이상치(대량 일괄 판매 등)가 안전재고를 왜곡하지 않도록 처리
3. 기존 정상 발주 흐름에 영향 없음 보장

### 대상
- 맥주(049), 소주(050), 전자담배(073) — friday_boost 대상 3개 카테고리
- beer.py의 daily_avg 계산 — 맥주 카테고리 전체

---

## 2. 현행 코드 분석

### 2.1 friday_boost 규칙 (`improved_predictor.py:3184-3189`)

```python
if rules["friday_boost"]["enabled"]:
    if weekday == 4 and product["mid_cd"] in rules["friday_boost"]["categories"]:
        order_qty *= rules["friday_boost"]["boost_rate"]  # 무조건 1.15배
```

**문제**: `base_prediction(WMA) > 0` 또는 `daily_avg > 0` 체크 없음

### 2.2 beer.py daily_avg 계산 (`beer.py:170-198`)

```python
# 30일간 전체 SUM / 판매일수
cursor.execute("""
    SELECT COUNT(DISTINCT sales_date), COALESCE(SUM(sale_qty), 0)
    FROM daily_sales WHERE item_cd=? AND sales_date >= date('now', '-30 days')
""")
daily_avg = total_sales / data_days  # 이상치 미제거
```

**문제**: 3/7에 18개 → daily_avg가 실제 패턴 대비 과대 추정

### 2.3 need 계산 (`improved_predictor.py:1700-1720`)

```
need = adjusted_prediction + lead_time + safety_stock - stock - pending
     = 0.0 + 0.0 + 24.7 - 19 - 0 = 5.7
```

**문제**: WMA=0인데 safety_stock만으로 양수 need 발생 → 발주 트리거

### 2.4 overstock_prevention (`improved_predictor.py:3198-3209`)

```python
if daily_avg > 0:  # daily_avg=0이면 전체 스킵!
    stock_days = effective_stock / daily_avg
    if stock_days >= 5: return 0
```

**문제**: WMA=0 → daily_avg=0(adjusted) → 과잉재고 방지 로직 전체 우회

---

## 3. 수정 방안

### 방안 A: friday_boost WMA 가드 (핵심, 즉시)

**파일**: `src/prediction/improved_predictor.py:3184-3189`

```python
# Before
if weekday == 4 and product["mid_cd"] in categories:
    order_qty *= boost_rate

# After
if weekday == 4 and product["mid_cd"] in categories:
    if base_prediction > 0:  # WMA=0이면 부스트 불필요
        order_qty *= boost_rate
```

**영향**: WMA > 0인 정상 상품은 기존과 동일, WMA=0인 무판매 상품만 부스트 제거

### 방안 B: need 계산에 safety-only 가드 추가

**파일**: `src/prediction/improved_predictor.py` (need 계산 직후)

```python
# need가 safety_stock에서만 발생한 경우 (실수요=0) → 발주 불필요
if adjusted_prediction <= 0 and lead_time_demand <= 0 and need_qty > 0:
    # safety_stock만으로 양수 need → 재고가 안전재고 이하일 뿐, 실수요 없음
    if current_stock > 0:  # 재고가 0이 아니면
        need_qty = 0
        ctx["_safety_only_skip"] = True
```

**단, 주의**: 재고 0인 상품은 안전재고 기반 발주가 필요할 수 있으므로 `current_stock > 0` 조건 추가

**검토 필요**: 이 가드가 너무 공격적일 수 있음. WMA=0이지만 간헐적 수요가 있는 상품(INTERMITTENT)은 Croston/TSB 모델이 따로 처리하므로 WMA 기반 파이프라인에서는 안전.

### 방안 C: beer.py daily_avg 이상치 제거

**파일**: `src/prediction/categories/beer.py:170-198`

```python
# Before: 단순 SUM / COUNT
daily_avg = total_sales / data_days

# After: IQR 기반 이상치 제거
# 1. 일별 판매량 조회
cursor.execute("""
    SELECT sale_qty FROM daily_sales
    WHERE item_cd = ? AND sales_date >= date('now', '-30 days') AND sale_qty > 0
""")
daily_sales = [r[0] for r in cursor.fetchall()]

if len(daily_sales) >= 3:
    q1 = np.percentile(daily_sales, 25)
    q3 = np.percentile(daily_sales, 75)
    iqr = q3 - q1
    upper_bound = q3 + 1.5 * iqr
    filtered = [s for s in daily_sales if s <= upper_bound]
    daily_avg = sum(filtered) / data_days  # 전체 일수로 나눠 일평균 유지
else:
    daily_avg = total_sales / data_days if data_days > 0 else 0.0
```

**산토리나마비어 적용 시**:
- 원본: [1, 2, 2, 4, 4, 4, 18] → avg=37/8=4.6 → safe=4.6×3=13.8 (data_days 기준)
- IQR 제거: Q1=2, Q3=4, IQR=2, upper=7 → 18 제거 → [1,2,2,4,4,4] → 17/8=2.1 → safe=6.4
- 실제 beer.py는 `total_sales/data_days`이므로: 37/8=4.6 → 필터 후 19/8=2.4

### 방안 D: overstock_prevention WMA=0 처리

**파일**: `src/prediction/improved_predictor.py:3198-3209`

```python
# Before: daily_avg=0이면 전체 스킵
if daily_avg > 0:
    stock_days = effective_stock / daily_avg
    ...

# After: daily_avg=0이고 재고 충분하면 발주 스킵
if daily_avg > 0:
    stock_days = effective_stock / daily_avg
    if stock_days >= threshold:
        return RuleResult(qty=0, ...)
elif daily_avg == 0 and effective_stock > 0 and need_qty > 0:
    # WMA=0 + 재고 있음 → 발주 불필요
    return RuleResult(qty=0, stage="rules_overstock",
                      reason=f"zero_demand, stock={effective_stock}")
```

---

## 4. 우선순위 및 구현 순서

| 순서 | 방안 | 난이도 | 영향 범위 | 위험도 |
|------|------|--------|----------|--------|
| 1 | **A: friday_boost WMA 가드** | 낮음 (1줄) | 049/050/073 금요일만 | 극히 낮음 |
| 2 | **D: overstock WMA=0 가드** | 낮음 (5줄) | 전 카테고리 | 낮음 |
| 3 | **C: beer daily_avg 이상치 제거** | 중간 (20줄) | 맥주 전체 | 중간 |
| 4 | B: safety-only 가드 | 중간 | 전 카테고리 | **높음** (간헐적 수요 상품 주의) |

### 구현 전략
- **즉시 적용**: A + D (간단하고 안전, 당장의 과잉발주 방지)
- **검증 후 적용**: C (이상치 제거는 다른 맥주 상품에도 영향, 테스트 필요)
- **보류**: B (safety-only 가드는 부작용 분석 필요 — INTERMITTENT 패턴과 충돌 가능)

---

## 5. 테스트 계획

### 5.1 단위 테스트
- `test_friday_boost_skip_when_wma_zero`: WMA=0 상품에 friday_boost 미적용 확인
- `test_friday_boost_normal_when_wma_positive`: WMA>0 상품은 기존대로 부스트 확인
- `test_overstock_zero_demand_skip`: daily_avg=0 + stock>0 → 발주 스킵
- `test_beer_daily_avg_outlier_removal`: 이상치 제거 전후 daily_avg 비교

### 5.2 통합 검증
- `run_scheduler.py --now --store 46513` (드라이런)
- 산토리나마비어캔500ml 발주량: 6 → 0 예상
- 다른 맥주 상품(정상 판매 중)의 발주량 변화 없음 확인

### 5.3 운영 검증 (4/3 07시 발주)
- order.log에서 8801021235240 발주 여부 확인
- 전체 맥주 카테고리(049) 발주 건수/수량 비교 (전일 대비)

---

## 6. 영향 분석

### 영향받는 파일
| 파일 | 변경 | 영향 |
|------|------|------|
| `src/prediction/improved_predictor.py` | friday_boost 가드 + overstock 가드 | 049/050/073 + 전카테고리 |
| `src/prediction/categories/beer.py` | daily_avg 이상치 제거 | 맥주 전체 |

### 후행 덮어쓰기 체크
- friday_boost는 `_apply_order_rules()` 내 첫 번째 규칙 → 이후 단계에 의해 0으로 될 수 있음 (안전)
- overstock 가드는 `_apply_order_rules()` 내 → `return 0`으로 이후 단계 진입 불가 (안전)
- beer daily_avg 변경 → safety_stock 변경 → need 변경 → 파이프라인 전체 영향 (테스트 필수)

### 위험 시나리오
- **간헐적 수요 맥주**: WMA=0이지만 3~4일 후 갑자기 판매 발생하는 상품 → safety_stock이 0이면 품절 가능
  - **완화**: beer.py의 `has_enough_data` 플래그 + min_data_days(7일) 조건으로 극단 케이스 방지
  - 방안 A/D는 "WMA=0 + 재고>0"인 경우만 스킵이므로 재고 0이면 여전히 발주
