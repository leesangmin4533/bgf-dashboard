# Design: 과자류 예측 개선

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | snack-prediction-improvement |
| Plan 참조 | `docs/01-plan/features/snack-prediction-improvement.plan.md` |
| 작성일 | 2026-02-01 |
| 변경 파일 | 2개 (`snack_confection.py`, `improved_predictor.py`) |

## 2. 변경 파일 상세

### 변경 파일 (2개)

| # | 파일 | 개선 항목 |
|---|------|----------|
| 1 | `src/prediction/categories/snack_confection.py` | A. 카테고리 확장, B. 요일계수 |
| 2 | `src/prediction/improved_predictor.py` | C. 발주단위 보정 |

### 변경하지 않는 파일

| 파일 | 이유 |
|------|------|
| `src/prediction/categories/default.py` | 015/016/019/020이 snack_confection으로 이동하면 자동 미사용 |
| `src/prediction/utils/outlier_handler.py` | 이미 IQR 기반 이상치 처리 내장 (개선 D 불필요) |
| `src/order/auto_order.py` | 발주 실행 로직 변경 없음 |
| `src/prediction/pre_order_evaluator.py` | 사전 평가 로직 변경 없음 |
| `src/db/models.py` | 스키마 변경 없음 |

> **개선 D (스파이크 필터링) 제외 사유**: `outlier_handler.py`가 이미 존재하며,
> `calculate_weighted_average()`에서 `clean_outliers=True`로 IQR 기반 이상치 캡핑이
> 적용되고 있음 (improved_predictor.py:554-560). 별도 구현 불필요.

---

## 3. 개선 A: 카테고리 핸들러 확장

### 3.1 변경: `snack_confection.py`

#### SNACK_CONFECTION_TARGET_CATEGORIES (line 23)

```python
# 현재
SNACK_CONFECTION_TARGET_CATEGORIES = ["014", "017", "018", "029", "030"]

# 변경
SNACK_CONFECTION_TARGET_CATEGORIES = ["014", "015", "016", "017", "018", "019", "020", "029", "030"]
```

#### SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG (line 26)

```python
# 현재
"target_categories": ["014", "017", "018", "029", "030"],

# 변경
"target_categories": ["014", "015", "016", "017", "018", "019", "020", "029", "030"],
```

#### 모듈 docstring (line 4)

```python
# 현재
과자류(014, 017, 018, 029, 030)의 동적 안전재고 계산:

# 변경
과자류(014~020, 029, 030)의 동적 안전재고 계산:
```

#### is_snack_confection_category() docstring (line 87)

```python
# 현재
과자/간식 카테고리(014, 017, 018, 029, 030)이면 True

# 변경
과자/간식 카테고리(014~020, 029, 030)이면 True
```

### 3.2 영향 분석

**변경 전 (default.py 경로):**
```
mid_cd=015 → is_snack_confection_category() = False
           → else 분기 (line 896)
           → get_safety_stock_days("015", 2.75, 243)
           → shelf_group="ultra_long" → base=2.0 × multiplier=1.2 = 2.4
           → safety_stock = 2.75 × 2.4 = 6.6
```

**변경 후 (snack_confection.py 경로):**
```
mid_cd=015 → is_snack_confection_category() = True
           → get_safety_stock_with_snack_confection_pattern()
           → _get_turnover_level(2.75) → "medium" → safety_days=1.5
           → safety_stock = 2.75 × 1.5 × weekday_coef = 4.1~4.3
           → max_stock = 2.75 × 7.0 = 19.3 (상한 체크)
```

**안전재고 변화 예시:**

| 상품 | 일평균 | 변경 전 (default) | 변경 후 (snack) | 변화 |
|------|:------:|:-----------------:|:---------------:|:----:|
| 일평균 < 2 | 1.0 | 1.0 × 1.6 = 1.6 | 1.0 × 1.2 = 1.2 | -25% |
| 일평균 2~5 | 2.75 | 2.75 × 2.4 = 6.6 | 2.75 × 1.5 = 4.1 | -38% |
| 일평균 5+ | 6.0 | 6.0 × 3.0 = 18.0 | 6.0 × 2.0 = 12.0 | -33% |

안전재고가 25~38% 감소하므로 발주량도 줄어듦. 유통기한 243일 상품에 이 수준의 감소는 적절함.

---

## 4. 개선 B: 요일계수 실측 반영

### 4.1 변경: `snack_confection.py`

#### DEFAULT_WEEKDAY_COEF (lines 53-61)

```python
# 현재 (전 카테고리 동일)
DEFAULT_WEEKDAY_COEF = {
    0: 1.00,  # 월
    1: 1.00,  # 화
    2: 1.00,  # 수
    3: 1.00,  # 목
    4: 1.00,  # 금
    5: 1.05,  # 토
    6: 1.05,  # 일
}

# 변경 (실측 데이터 기반)
DEFAULT_WEEKDAY_COEF = {
    0: 1.06,  # 월
    1: 0.99,  # 화
    2: 1.04,  # 수
    3: 0.84,  # 목
    4: 1.01,  # 금
    5: 1.34,  # 토
    6: 0.74,  # 일
}
```

### 4.2 실측 근거

DB 분석 결과 (최근 28일, mid_cd IN ('015','016','019','020')):

| 요일 | 일총판매 | 실측 계수 | 현재 계수 | 차이 |
|------|:-------:|:---------:|:---------:|:----:|
| 일 | 85.6 | **0.74** | 1.05 | -0.31 |
| 월 | 123.5 | **1.06** | 1.00 | +0.06 |
| 화 | 115.0 | **0.99** | 1.00 | -0.01 |
| 수 | 120.8 | **1.04** | 1.00 | +0.04 |
| 목 | 97.2 | **0.84** | 1.00 | -0.16 |
| 금 | 117.0 | **1.01** | 1.00 | +0.01 |
| 토 | 155.8 | **1.34** | 1.05 | +0.29 |

주요 차이: 토요일 +29%, 일요일 -31%

### 4.3 DB 학습과의 관계

`_learn_weekday_pattern()` (line 100)이 DB에서 자동 학습하지만:
- `min_data_days=14` 미만이면 DEFAULT_WEEKDAY_COEF를 사용
- 현재 과자류 평균 데이터 5일 → 대부분 기본값 사용

따라서 DEFAULT_WEEKDAY_COEF를 실측에 맞게 수정하는 것이 중요.
데이터가 14일 이상 축적되면 자동으로 DB 학습값이 우선됨.

---

## 5. 개선 C: 발주단위 과잉발주 보정

### 5.1 변경: `improved_predictor.py`

#### 위치: line 941~944 (12. 발주 단위 맞춤)

```python
# 현재 코드
# 12. 발주 단위 맞춤 (올림)
order_unit = product["order_unit_qty"]
if order_qty > 0 and order_unit > 1:
    order_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit
```

```python
# 변경 코드
# 12. 발주 단위 맞춤 (올림) + 과잉발주 보정
order_unit = product["order_unit_qty"]
if order_qty > 0 and order_unit > 1:
    unit_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit
    surplus = unit_qty - order_qty  # 올림으로 인한 잉여

    # 잉여가 안전재고 이상이면 발주 불필요 (이미 충분)
    if surplus >= safety_stock and current_stock + surplus >= adjusted_prediction + safety_stock:
        order_qty = 0
    else:
        order_qty = unit_qty
```

#### 위치: line 1215~1217 (두 번째 발주단위 올림 - predict_with_details용)

동일한 보정 로직 적용:

```python
# 현재 코드
order_unit = product["order_unit_qty"]
if order_qty > 0 and order_unit > 1:
    order_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit
```

```python
# 변경 코드
order_unit = product["order_unit_qty"]
if order_qty > 0 and order_unit > 1:
    unit_qty = ((order_qty + order_unit - 1) // order_unit) * order_unit
    surplus = unit_qty - order_qty

    if surplus >= safety_stock and current_stock + surplus >= adjusted_prediction + safety_stock:
        order_qty = 0
    else:
        order_qty = unit_qty
```

### 5.2 보정 로직 상세

**조건**: `surplus >= safety_stock AND current_stock + surplus >= adjusted_prediction + safety_stock`

| 조건 | 의미 |
|------|------|
| `surplus >= safety_stock` | 올림 잉여만으로 안전재고를 확보 |
| `current_stock + surplus >= adj_pred + safety` | 현재고 + 잉여로 예측 + 안전재고 충당 가능 |

**프레첼 사례 적용:**

```
order_qty (반올림 후) = 3
order_unit = 10
unit_qty = 10
surplus = 10 - 3 = 7

safety_stock = 6.86
surplus(7) >= safety_stock(6.86)? → YES

current_stock(7) + surplus(7) = 14
adjusted_prediction(2.86) + safety_stock(6.86) = 9.72
14 >= 9.72? → YES

→ order_qty = 0 (발주 스킵)
```

현재고 7개 + 발주 안 해도 2.5일분 확보 → 발주 불필요.

**반례 (발주 필요한 경우):**

```
일평균 5개 상품, 재고 2개, order_unit 10
order_qty = 5 + 10 - 2 - 0 = 13 → unit_qty = 20
surplus = 20 - 13 = 7
safety_stock = 10.0

surplus(7) >= safety_stock(10.0)? → NO
→ order_qty = 20 (정상 발주)
```

### 5.3 영향 범위

이 보정은 **모든 카테고리**에 적용되지만, 실질적으로 영향받는 상품:
- `order_unit_qty > 1`인 상품
- `need_qty`가 작고 `order_unit_qty`가 큰 상품 (잉여 > 안전재고)
- 과자류 외에도 음료, 생활용품 등에도 적용됨

---

## 6. 수정하지 않는 파일 확인

| 파일 | 확인 항목 |
|------|----------|
| `default.py` | 015/016/019/020은 snack_confection 분기를 먼저 타므로 default에 도달하지 않음. `get_weekday_coefficient()`의 기존 과자류 계수는 그대로 두되 사용되지 않음 |
| `outlier_handler.py` | `get_outlier_config()`에 015/016/019/020 전용 설정이 없으나, "default" IQR×1.5 캡핑이 적용되어 충분함 |
| `auto_order.py` | 발주단위 보정이 predictor에서 처리되므로 auto_order 변경 불필요 |
| `pre_order_evaluator.py` | 사전 평가 판정(FORCE/URGENT/NORMAL/PASS)은 안전재고 변경과 독립적 |

---

## 7. 구현 순서

```
1. snack_confection.py: SNACK_CONFECTION_TARGET_CATEGORIES 확장 (개선 A)
      ↓
2. snack_confection.py: DEFAULT_WEEKDAY_COEF 업데이트 (개선 B)
      ↓
3. improved_predictor.py: 발주단위 과잉발주 보정 (개선 C) — line 941~944
      ↓
4. improved_predictor.py: 동일 보정 적용 — line 1215~1217
      ↓
5. 모의발주 비교 검증
```

---

## 8. 검증 계획

### 8.1 모의발주 전/후 비교

```bash
# 개선 전 (이미 저장됨)
# test report/dry_order_20260201_101419.xlsx → 과자류 총 발주량 303개

# 개선 후
cd bgf_auto && python scripts/dry_order.py --no-login --no-pending
```

### 8.2 검증 지표

| 지표 | 개선 전 | 기대 범위 | 이상 징후 |
|------|:------:|:---------:|----------|
| 015 발주량 | 84개 | 50~70개 | < 30 (과소) 또는 > 84 (증가) |
| 016 발주량 | 116개 | 80~100개 | < 50 (과소) |
| 019 발주량 | 115개 | 80~100개 | < 50 (과소) |
| 020 발주량 | 203개 | 140~180개 | < 100 (과소) |
| 과자류 합계 | 518개 | 350~450개 | < 250 (롤백 필요) |
| 발주 상품 수 | 105개 | 90~110개 | < 70 (품절 위험) |

### 8.3 개별 상품 검증

프레첼(8809304881023) 추적:
```
개선 전: order_qty=10, safety_stock=6.86 (default.py)
개선 후 (A): safety_stock=4.13 (snack_confection, medium 1.5일)
개선 후 (A+C): need_qty=2.86+4.13-7-0 ≈ 0 → order_qty=0 (재고 충분)
```

---

## 9. 롤백 계획

| 단계 | 롤백 방법 | 소요 |
|------|----------|------|
| 개선 A 롤백 | `SNACK_CONFECTION_TARGET_CATEGORIES`에서 015/016/019/020 제거 | 1줄 |
| 개선 B 롤백 | `DEFAULT_WEEKDAY_COEF`를 이전값(1.00/1.05)으로 복원 | 7줄 |
| 개선 C 롤백 | 발주단위 보정 if문 제거, 원래 단순 올림으로 복원 | 2곳 |
