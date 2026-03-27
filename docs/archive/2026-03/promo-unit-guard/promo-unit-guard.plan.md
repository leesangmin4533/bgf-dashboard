# Plan: promo-unit-guard

> 행사 보정 + 발주단위 과잉발주 방지

## 1. 배경 및 문제

### 현상
- 상품 8801043022262 (컵라면, mid_cd=032): 일평균 1.17개 판매, 재고 14개
- 예측 시스템이 **3일 연속 16개(1박스)씩 자동발주** (총 48개 = 41일치)
- 재고 충분함에도 불필요한 발주 발생

### 근본 원인 (2개 버그)

**Bug 1: `_round_to_order_unit` cat_max_stock 분기에 surplus 취소 로직 누락**
- `improved_predictor.py:1933-1953`
- default 카테고리에는 "올림 잉여가 안전재고보다 크면 발주 취소" 로직 존재 (line 1956-1959)
- cat_max_stock 분기 (라면/맥주/소주/푸드)에는 이 체크가 **빠져있음**
- floor_qty=0이면 무조건 ceil_qty 반환 → 3개 필요해도 16개 발주

**Bug 2: 행사 Case C 보정이 재고 충분 여부를 무시**
- `improved_predictor.py:1639-1654`
- `promo_need = promo_avg * weekday_coef + safety_stock - current_stock`
- safety_stock(9.2)를 그대로 더해서, 재고 14개인데도 3개 추가 발주 트리거
- 재고가 행사 일수요(promo_avg * weekday_coef)를 커버하면 발주 불필요

### 영향 범위
- 46513 점포 032(라면) 카테고리: order_unit_qty=16 상품 **14개**
- 동일 조건 (행사 + 높은 unit + 충분한 재고) 상품 모두 영향
- 맥주/소주/푸드 등 cat_max_stock 사용하는 모든 카테고리에 잠재적 영향

## 2. 수정 범위

### Fix A: `_round_to_order_unit` surplus 취소 로직 추가 (우선순위 1)

**파일**: `src/prediction/improved_predictor.py`
**위치**: `_round_to_order_unit()` cat_max_stock 분기 (line 1933-1953)

**현재 코드**:
```python
if cat_max_stock and cat_max_stock > 0:
    if current_stock + pending_qty + ceil_qty > cat_max_stock and floor_qty > 0:
        return floor_qty
    elif needs_ceil:
        return ceil_qty
    else:
        if floor_qty > 0:
            return floor_qty
        else:
            return ceil_qty  # floor=0이면 무조건 올림 ← 문제
```

**수정 후**:
```python
if cat_max_stock and cat_max_stock > 0:
    if current_stock + pending_qty + ceil_qty > cat_max_stock and floor_qty > 0:
        return floor_qty
    elif needs_ceil:
        return ceil_qty
    else:
        if floor_qty > 0:
            return floor_qty
        else:
            # ★ Fix A: floor=0일 때 surplus 취소 체크 (default 카테고리와 동일)
            surplus = ceil_qty - order_qty
            if (surplus >= safety_stock
                and current_stock + surplus >= adjusted_prediction + safety_stock):
                logger.info(
                    f"[발주단위] {product['item_nm']}: "
                    f"올림 {ceil_qty}개 잉여({surplus}) >= 안전재고({safety_stock:.0f}), "
                    f"재고 충분 → 발주 취소"
                )
                return 0
            return ceil_qty
```

### Fix B: 행사 Case C 재고 충분 체크 추가 (우선순위 2)

**파일**: `src/prediction/improved_predictor.py`
**위치**: `_apply_promotion_adjustment()` Case C (line 1639-1654)

**현재 코드**:
```python
# (C) 행사 안정기 -> 예측 부족 시 행사 일평균 보정
elif (promo_status.current_promo
      and promo_status.promo_avg > 0
      and daily_avg < promo_status.promo_avg * 0.8):
    promo_need = (promo_status.promo_avg * weekday_coef
                  + safety_stock - current_stock - pending_qty)
    promo_order = int(max(0, promo_need))
    if promo_order > order_qty:
        order_qty = promo_order
```

**수정 후**:
```python
# (C) 행사 안정기 -> 예측 부족 시 행사 일평균 보정
elif (promo_status.current_promo
      and promo_status.promo_avg > 0
      and daily_avg < promo_status.promo_avg * 0.8):
    # ★ Fix B: 재고가 행사 일수요를 커버하면 스킵
    promo_daily_demand = promo_status.promo_avg * weekday_coef
    if current_stock + pending_qty >= promo_daily_demand:
        logger.info(
            f"[행사중보정] {item_cd}: 재고({current_stock}+{pending_qty}) >= "
            f"행사일수요({promo_daily_demand:.1f}), 보정 스킵"
        )
    else:
        promo_need = (promo_daily_demand
                      + safety_stock - current_stock - pending_qty)
        promo_order = int(max(0, promo_need))
        if promo_order > order_qty:
            old_qty = order_qty
            order_qty = promo_order
            logger.info(...)
```

## 3. 테스트 계획

### Fix A 테스트 (발주단위 surplus 취소)
1. unit=16, order_qty=3, stock=14, safety=9.2 → **0 반환** (취소)
2. unit=16, order_qty=3, stock=2, safety=3.0 → **16 반환** (결품 위험이라 발주)
3. unit=4, order_qty=3, stock=5, safety=2.0 → **4 반환** (소량 잉여라 발주)
4. unit=16, order_qty=15, stock=2, safety=9.0 → **16 반환** (need 높으니 발주)
5. unit=24, order_qty=5, stock=20, safety=8.0 → **0 반환** (잉여 19 > safety 8)
6. cat_max_stock 초과 시 기존 로직 유지 확인
7. needs_ceil(days_cover < 0.5) 시 올림 유지 확인
8. 담배(올림 유지) 영향 없음 확인

### Fix B 테스트 (행사 보정 재고 체크)
1. stock=14, promo_daily=8.35 → **스킵** (14 >= 8.35)
2. stock=5, promo_daily=8.35 → **보정 적용** (5 < 8.35)
3. stock=0, pending=10, promo_daily=8.35 → **스킵** (0+10 >= 8.35)
4. 행사 종료 임박(Case A) 영향 없음 확인
5. 행사 시작 임박(Case B) 영향 없음 확인
6. 비행사 보정(Case D) 영향 없음 확인

### 통합 테스트
- 8801043022262: 재고 14, 행사 2+1, unit=16 → **발주 0** 확인
- 기존 테스트 전체 통과 확인

## 4. 위험 분석

| 위험 | 수준 | 대응 |
|------|------|------|
| Fix A로 필요한 발주까지 취소 | 중 | needs_ceil(결품위험) 체크가 우선 적용되므로 안전 |
| Fix B로 행사 수요 과소대응 | 저 | 재고 >= 행사일수요 조건이므로 보수적 |
| 기존 테스트 실패 | 저 | 영향 범위가 floor_qty=0 경우만 한정 |

## 5. 수정 파일 목록

| 파일 | 수정 내용 |
|------|-----------|
| `src/prediction/improved_predictor.py` | Fix A: _round_to_order_unit surplus 취소 추가 |
| `src/prediction/improved_predictor.py` | Fix B: _apply_promotion_adjustment 재고 체크 추가 |
| `tests/test_promo_unit_guard.py` | 신규: Fix A + Fix B 테스트 |

## 6. 완료 기준

- [ ] Fix A: cat_max_stock 분기에 surplus 취소 로직 추가
- [ ] Fix B: 행사 Case C에 재고 충분 체크 추가
- [ ] 신규 테스트 전체 통과
- [ ] 기존 테스트 전체 통과
- [ ] 8801043022262 시뮬레이션: 재고 14 → 발주 0 확인
