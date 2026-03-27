# Design: promo-unit-guard (행사 보정 + 발주단위 과잉발주 방지)

> 버전 1.0 | 2026-03-06 | BGF리테일 (CU) 자동발주 시스템

## 1. 프로젝트 개요

### 1.1 목적

높은 발주입수(order_unit_qty) 상품에서 행사 보정과 발주단위 올림이 결합되어 발생하는 **과잉발주 버그 2건**을 수정합니다.

### 1.2 대상 상품 사례

| 항목 | 값 |
|------|-----|
| 상품코드 | 8801043022262 (컵라면) |
| 중분류 | 032 (라면) |
| 발주입수 | 16 (1박스=16개) |
| 일평균 판매 | 1.17개 |
| 현재 재고 | 14개 |
| 안전재고 | 9.2개 |
| 행사 | 2+1 진행중 |
| 행사 일평균 | 5.0개 |
| 버그 발주 결과 | **16개 (=1박스)** 발주 |
| 정상 발주 결과 | **0개** (재고 충분) |

### 1.3 영향 범위

- 46513 점포 032(라면) 카테고리: order_unit_qty=16 상품 **14개**
- cat_max_stock 분기를 사용하는 모든 카테고리: 라면/맥주/소주/푸드
- 행사 Case C + 높은 발주입수 + 충분한 재고 조합일 때 발생

## 2. 버그 상세 분석

### 2.1 Bug 1: `_round_to_order_unit()` cat_max_stock 분기 surplus 취소 누락

**파일**: `src/prediction/improved_predictor.py`
**메서드**: `_round_to_order_unit()` (line 1893~1983)
**위치**: cat_max_stock 분기 line 1932~1953

#### 현재 코드 흐름

```
order_qty=3, order_unit=16 일 때:
  ceil_qty = ((3+16-1)//16)*16 = 16
  floor_qty = (3//16)*16 = 0

  cat_max_stock 분기 진입 (라면):
    ① ceil(16) > max_stock? → No (max_stock이 더 큼)
    ② needs_ceil(결품위험)? → No (days_cover=14/1.17=12일)
    ③ floor_qty > 0? → No (floor=0)
    ④ return ceil_qty → 16 ← ★ 여기서 무조건 올림 반환
```

#### 문제점

default 카테고리 분기(line 1956~1959)에는 "올림 잉여가 안전재고보다 크고, 재고가 충분하면 발주 취소(return 0)" 로직이 있음:

```python
# default 카테고리 (line 1956-1959) — 취소 로직 있음
surplus = ceil_qty - order_qty  # 16-3=13
if surplus >= safety_stock and current_stock + surplus >= adjusted_prediction + safety_stock:
    return 0  # 발주 취소!
```

그러나 cat_max_stock 분기(라면/맥주/소주/푸드)에는 이 체크가 **빠져있어서**, floor=0일 때 무조건 `ceil_qty`를 반환합니다.

#### 수치 검증

```
surplus = ceil_qty - order_qty = 16 - 3 = 13
safety_stock = 9.2
→ surplus(13) >= safety_stock(9.2) ✓

current_stock + surplus = 14 + 13 = 27
adjusted_prediction + safety_stock = 1.69 + 9.2 = 10.89
→ 27 >= 10.89 ✓

→ 두 조건 모두 만족 → return 0 (취소) 해야 함
→ 그러나 cat_max_stock 분기라서 체크 없이 return 16 됨 ← BUG
```

### 2.2 Bug 2: `_apply_promotion_adjustment()` Case C 재고 충분 여부 무시

**파일**: `src/prediction/improved_predictor.py`
**메서드**: `_apply_promotion_adjustment()` (line 1574~1702)
**위치**: Case C (행사 안정기 보정) line 1639~1654

#### 현재 코드 흐름

```python
# Case C 조건: 행사 진행중 + 행사 일평균 > 0 + 예측 < 행사 * 0.8
promo_need = promo_avg * weekday_coef + safety_stock - current_stock - pending_qty
           = 5.0 * 1.67 + 9.2 - 14 - 0
           = 8.35 + 9.2 - 14
           = 3.55
promo_order = int(max(0, 3.55)) = 3

→ promo_order(3) > order_qty(0) → order_qty = 3
```

#### 문제점

`promo_need` 공식에 `safety_stock`(9.2)를 그대로 더하는 것이 문제입니다:
- 재고(14)가 행사 일수요(8.35 = 5.0 * 1.67)를 **이미 커버**하고 있음
- 그런데도 safety_stock을 더해서 부족분을 만들어냄
- 재고가 행사 일수요 이상이면 추가 발주가 불필요

#### 근본 원인

이 공식은 "행사 중 재고가 부족할 수 있으니 미리 채우자"는 의도이지만, **이미 재고가 충분한 경우**를 고려하지 않습니다.

## 3. 수정 설계

### 3.1 Fix A: `_round_to_order_unit()` surplus 취소 로직 추가

#### 수정 위치

`src/prediction/improved_predictor.py` → `_round_to_order_unit()` 메서드
line 1952~1953 (cat_max_stock 분기, `else: return ceil_qty` 부분)

#### 수정 내용

```python
# 수정 전 (line 1951-1953):
                else:
                    return ceil_qty  # floor=0이면 최소 1단위

# 수정 후:
                else:
                    # ★ Fix A: floor=0일 때 surplus 취소 체크
                    # default 카테고리와 동일한 안전 체크 적용
                    surplus = ceil_qty - order_qty
                    if (surplus >= safety_stock
                            and current_stock + surplus >= adjusted_prediction + safety_stock):
                        logger.info(
                            f"[발주단위] {product['item_nm']}: "
                            f"올림 {ceil_qty}개 잉여({surplus}) >= "
                            f"안전재고({safety_stock:.0f}), "
                            f"재고 충분 → 발주 취소"
                        )
                        return 0
                    return ceil_qty
```

#### 코드 흐름 (수정 후)

```
cat_max_stock 분기, floor_qty=0 일 때:
  ① max_stock 초과 체크 → 기존 유지
  ② needs_ceil(결품위험) 체크 → 기존 유지 (우선 적용)
  ③ floor_qty > 0 체크 → 기존 유지
  ④ floor_qty == 0일 때:
     ④-a. surplus >= safety_stock AND 재고+잉여 >= 예측+안전재고?
          → Yes: return 0 (취소) ← ★ 새로 추가
          → No: return ceil_qty (기존대로 올림)
```

#### 안전장치

- `needs_ceil` (days_cover < 0.5)가 **먼저** 체크되므로, 결품 위험 시에는 올림이 우선 적용됩니다.
- surplus 취소는 `needs_ceil=False`이고 `floor_qty=0`인 경우에만 실행됩니다.
- 담배 카테고리는 별도 분기(`elif is_tobacco_category`)라 영향 없습니다.

### 3.2 Fix B: `_apply_promotion_adjustment()` Case C 재고 충분 체크

#### 수정 위치

`src/prediction/improved_predictor.py` → `_apply_promotion_adjustment()` 메서드
line 1639~1654 (Case C 분기 내부)

#### 수정 내용

```python
# 수정 전 (line 1639-1654):
            # (C) 행사 안정기 -> 예측 부족 시 행사 일평균 보정
            elif (promo_status.current_promo
                  and promo_status.promo_avg > 0
                  and daily_avg < promo_status.promo_avg * 0.8):
                promo_need = (promo_status.promo_avg * weekday_coef
                              + safety_stock - current_stock - pending_qty)
                promo_order = int(max(0, promo_need))
                if promo_order > order_qty:
                    old_qty = order_qty
                    order_qty = promo_order
                    logger.info(
                        f"[행사중보정] {item_cd}: {promo_status.current_promo} "
                        f"행사avg {promo_status.promo_avg:.1f} 적용 "
                        f"(예측avg {daily_avg:.1f} < 행사avg×0.8), "
                        f"발주 {old_qty}→{order_qty}"
                    )

# 수정 후:
            # (C) 행사 안정기 -> 예측 부족 시 행사 일평균 보정
            elif (promo_status.current_promo
                  and promo_status.promo_avg > 0
                  and daily_avg < promo_status.promo_avg * 0.8):
                # ★ Fix B: 재고가 행사 일수요를 이미 커버하면 보정 스킵
                promo_daily_demand = promo_status.promo_avg * weekday_coef
                if current_stock + pending_qty >= promo_daily_demand:
                    logger.info(
                        f"[행사중보정] {item_cd}: "
                        f"재고({current_stock}+{pending_qty}) >= "
                        f"행사일수요({promo_daily_demand:.1f}), 보정 스킵"
                    )
                else:
                    promo_need = (promo_daily_demand
                                  + safety_stock - current_stock - pending_qty)
                    promo_order = int(max(0, promo_need))
                    if promo_order > order_qty:
                        old_qty = order_qty
                        order_qty = promo_order
                        logger.info(
                            f"[행사중보정] {item_cd}: {promo_status.current_promo} "
                            f"행사avg {promo_status.promo_avg:.1f} 적용 "
                            f"(예측avg {daily_avg:.1f} < 행사avg×0.8), "
                            f"발주 {old_qty}→{order_qty}"
                        )
```

#### 코드 흐름 (수정 후)

```
Case C 진입 시:
  promo_daily_demand = promo_avg * weekday_coef = 5.0 * 1.67 = 8.35

  ★ 재고 체크: current_stock + pending = 14 + 0 = 14
     14 >= 8.35? → Yes → 보정 스킵, order_qty 변경 없음

  재고 부족 시 (예: stock=5):
     5 >= 8.35? → No → 기존 공식 적용
     promo_need = 8.35 + 9.2 - 5 - 0 = 12.55 → 12
```

#### 설계 근거

- 재고가 행사 일수요(= 행사 일평균 × 요일계수)를 커버하면 추가 발주 불필요
- safety_stock은 "언제든 보유해야 할 최소 재고"이지, "오늘 발주에 반드시 추가할 양"이 아님
- 재고 충분 시 보정을 스킵하면 order_qty=0 유지 → _round_to_order_unit에서도 0 반환

## 4. 실행 순서와 상호작용

### 4.1 예측 파이프라인 내 위치

```
predict_single_product()
  ├── base_prediction (WMA) → 0.94
  ├── coefficient_adjustment → 1.69
  ├── _apply_order_rules (ROP) → order_qty = 0 (재고 충분)
  ├── ★ _apply_promotion_adjustment → Case C → 0→3 (Fix B 적용 시: 0 유지)
  ├── _apply_ml_ensemble → ...
  ├── diff_feedback → ...
  ├── waste_feedback → ...
  ├── substitution → ...
  ├── category_forecaster → ...
  ├── ★ _round_to_order_unit → 3→16 (Fix A 적용 시: 3→0)
  └── final order_qty
```

### 4.2 Fix 우선순위

| Fix | 위치 | 효과 | 독립성 |
|-----|------|------|--------|
| Fix B | 파이프라인 상류 | 원인 차단 (보정 자체를 스킵) | 독립적 |
| Fix A | 파이프라인 하류 | 결과 방지 (올림 과잉 취소) | 독립적 |

- **두 Fix는 독립적**: Fix B만 적용해도 이 케이스는 해결되지만, Fix A 없이는 다른 원인으로 order_qty=3이 되었을 때 여전히 16으로 올림될 수 있음
- **두 Fix 모두 필요**: Fix B는 행사 보정 문제, Fix A는 발주단위 올림 문제를 각각 해결

### 4.3 Fix A + Fix B 동시 적용 시 흐름

```
8801043022262 (재고=14, unit=16, 행사 2+1):

1. base_prediction: 0.94
2. adjustment: 1.69
3. _apply_order_rules: need=0 (재고 충분)
4. _apply_promotion_adjustment:
   → promo_daily_demand = 5.0 * 1.67 = 8.35
   → stock(14) >= 8.35 → ★ Fix B: 보정 스킵 → order_qty = 0
5. _round_to_order_unit:
   → order_qty=0 → ceil=0, floor=0 → return 0
6. 최종 발주: 0개 ✓
```

## 5. 영향 받지 않는 기존 로직

### 5.1 Fix A가 영향 주지 않는 부분

| 분기 | 이유 |
|------|------|
| max_stock 초과 (line 1934) | surplus 체크보다 먼저 실행, floor>0 반환 |
| needs_ceil=True (line 1942) | 결품 위험 시 올림 우선, surplus 체크 도달 안 함 |
| floor_qty > 0 (line 1946) | floor 반환으로 종료, surplus 체크 도달 안 함 |
| 담배 (line 1968) | 별도 분기, cat_max_stock과 무관 |
| default 카테고리 (line 1956) | 이미 surplus 체크 있음 |

### 5.2 Fix B가 영향 주지 않는 부분

| 케이스 | 이유 |
|--------|------|
| Case A (행사 종료 임박) | 별도 elif 분기, Case C와 독립 |
| Case B (행사 시작 임박) | 별도 elif 분기, Case C와 독립 |
| Case D (비행사 안정기) | 별도 elif 분기, Case C와 독립 |
| 행사 최소발주 보정 (line 1674) | Case C 이후 실행, order_qty>0일 때만 동작 |

## 6. 테스트 설계

### 6.1 Fix A 테스트 (발주단위 surplus 취소)

**테스트 파일**: `tests/test_promo_unit_guard.py`

#### TC-A1: 높은 unit + 재고 충분 → 취소

```python
# order_qty=3, unit=16, stock=14, safety=9.2, prediction=1.69
# surplus = 16 - 3 = 13 >= 9.2 ✓
# stock + surplus = 14 + 13 = 27 >= 1.69 + 9.2 = 10.89 ✓
# → return 0
```

#### TC-A2: 높은 unit + 재고 부족 → 발주 유지

```python
# order_qty=3, unit=16, stock=2, safety=3.0, prediction=5.0
# surplus = 16 - 3 = 13 >= 3.0 ✓
# stock + surplus = 2 + 13 = 15 >= 5.0 + 3.0 = 8.0 ✓
# → return 0? 아니, needs_ceil 체크 먼저
# days_cover = 2 / 5.0 = 0.4 < 0.5 → needs_ceil=True → return 16
```

#### TC-A3: 소량 unit + 재고 적당 → 발주 유지

```python
# order_qty=3, unit=4, stock=5, safety=2.0, prediction=3.0
# ceil=4, floor=0
# surplus = 4 - 3 = 1 >= 2.0? No → return ceil=4
```

#### TC-A4: 높은 need → 발주 유지

```python
# order_qty=15, unit=16, stock=2, safety=9.0, prediction=12.0
# ceil=16, floor=0
# surplus = 16 - 15 = 1 >= 9.0? No → return ceil=16
```

#### TC-A5: 대량 unit + 대량 재고 → 취소

```python
# order_qty=5, unit=24, stock=20, safety=8.0, prediction=4.0
# ceil=24, floor=0
# surplus = 24 - 5 = 19 >= 8.0 ✓
# stock + surplus = 20 + 19 = 39 >= 4.0 + 8.0 = 12.0 ✓
# → return 0
```

#### TC-A6: max_stock 초과 시 기존 로직 유지

```python
# order_qty=10, unit=16, stock=80, pending=0, cat_max_stock=90
# ceil=16, floor=0
# stock + pending + ceil = 80 + 0 + 16 = 96 > 90 and floor=0
# → 이 경우 floor=0이므로 max_stock 조건 불성립 → surplus 체크로 이동
# → surplus 체크에서 취소 or 올림 결정
```

#### TC-A7: needs_ceil 우선 적용 확인

```python
# order_qty=3, unit=16, stock=0, safety=9.2, prediction=5.0
# days_cover = 0 / 5.0 = 0 < 0.5 → needs_ceil=True
# → return 16 (surplus 체크 도달 안 함)
```

#### TC-A8: 담배 영향 없음 확인

```python
# mid_cd=033 (담배), order_qty=3, unit=10
# → 담배 분기(line 1968) 진입, cat_max_stock 분기 미진입
# → return ceil_qty=10
```

### 6.2 Fix B 테스트 (행사 보정 재고 체크)

#### TC-B1: 재고 충분 → 보정 스킵

```python
# stock=14, pending=0, promo_avg=5.0, weekday_coef=1.67
# promo_daily_demand = 5.0 * 1.67 = 8.35
# stock + pending = 14 >= 8.35 → 보정 스킵
# order_qty 변경 없음
```

#### TC-B2: 재고 부족 → 보정 적용

```python
# stock=5, pending=0, promo_avg=5.0, weekday_coef=1.67
# promo_daily_demand = 5.0 * 1.67 = 8.35
# stock + pending = 5 < 8.35 → 보정 적용
# promo_need = 8.35 + 9.2 - 5 - 0 = 12.55 → 12
```

#### TC-B3: pending 포함 충분 → 스킵

```python
# stock=0, pending=10, promo_avg=5.0, weekday_coef=1.67
# promo_daily_demand = 8.35
# stock + pending = 10 >= 8.35 → 스킵
```

#### TC-B4: Case A (행사 종료 임박) 영향 없음

```python
# promo_end_date < 3일 → Case A 진입, Case C 미진입
# Fix B 코드 도달 안 함
```

#### TC-B5: Case B (행사 시작 임박) 영향 없음

```python
# next_promo 있고 D-3 이내 → Case B 진입, Case C 미진입
```

#### TC-B6: Case D (비행사 보정) 영향 없음

```python
# current_promo=None → Case D 진입, Case C 미진입
```

### 6.3 통합 테스트

#### TC-INT1: 8801043022262 시뮬레이션

```python
# 전체 파이프라인 시뮬레이션
# 입력: stock=14, unit=16, promo=2+1, promo_avg=5.0, daily_avg=1.17, safety=9.2
# Fix B: promo_daily=8.35, stock(14) >= 8.35 → 보정 스킵 → order_qty=0
# Fix A: order_qty=0 → ceil=0 → return 0
# 최종: 발주 0개 확인
```

#### TC-INT2: 재고 부족 시 정상 발주

```python
# 입력: stock=2, unit=16, promo=2+1, promo_avg=5.0, daily_avg=1.17, safety=9.2
# Fix B: promo_daily=8.35, stock(2) < 8.35 → 보정 적용 → order_qty=12
# Fix A: ceil=16, floor=0, surplus=4 < 9.2 → 올림 유지 → 16
# 최종: 발주 16개 확인
```

## 7. 수정 파일 목록

| 파일 | 수정 유형 | 수정 내용 |
|------|-----------|-----------|
| `src/prediction/improved_predictor.py` | 수정 | Fix A: `_round_to_order_unit()` line 1951-1953 surplus 취소 추가 |
| `src/prediction/improved_predictor.py` | 수정 | Fix B: `_apply_promotion_adjustment()` line 1639-1654 재고 체크 추가 |
| `tests/test_promo_unit_guard.py` | 신규 | Fix A 테스트 8개 + Fix B 테스트 6개 + 통합 2개 = 16개 |

## 8. 구현 순서

1. **Fix B** 구현 (행사 Case C 재고 체크) — 원인 차단
2. **Fix A** 구현 (발주단위 surplus 취소) — 결과 방지
3. 테스트 작성 및 실행
4. 기존 테스트 전체 통과 확인

## 9. 위험 분석 및 대응

| 위험 | 수준 | 대응 |
|------|------|------|
| Fix A로 필요한 발주까지 취소 | 중 | `needs_ceil`(결품위험 days_cover<0.5) 체크가 우선 적용되므로, 결품 위험 상품은 항상 올림 유지 |
| Fix B로 행사 수요 과소대응 | 저 | `재고 >= 행사일수요` 조건이 보수적 — 재고가 하루치를 못 커버하면 보정 적용 |
| 기존 테스트 실패 | 저 | Fix A 영향은 floor_qty=0인 특수 경우만 한정, Fix B는 재고 충분 시에만 스킵 |
| 다른 카테고리 의도치 않은 영향 | 저 | 맥주/소주/푸드도 동일하게 surplus 취소 혜택 받음 (의도된 효과) |

## 10. 완료 기준

- [ ] Fix A: cat_max_stock 분기에 surplus 취소 로직 추가
- [ ] Fix B: 행사 Case C에 재고 충분 체크 추가
- [ ] 신규 테스트 16개 전체 통과
- [ ] 기존 테스트 전체 통과
- [ ] 8801043022262 시뮬레이션: 재고 14 → 발주 0 확인
