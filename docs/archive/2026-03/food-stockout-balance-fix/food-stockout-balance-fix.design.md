# Design: food-stockout-balance-fix

> 푸드 예측 과소편향 해소 - 기회손실/폐기 균형 최적화

---

## 1. 설계 개요

### 1-1. Plan 참조
- `docs/01-plan/features/food-stockout-balance-fix.plan.md`
- `docs/03-analysis/food-prediction-store-analysis.md`
- `docs/03-analysis/food-prediction-code-data-analysis.md`

### 1-2. 핵심 변경 3가지

| # | 변경 | 파일 | 삽입 위치 |
|---|------|------|----------|
| A | 폐기계수 조건부 적용 | improved_predictor.py | `_compute_safety_and_order()` L1169-1184 |
| B | compound floor 이후 최종 하한 | improved_predictor.py | `_compute_safety_and_order()` L1177-1184 (A와 동일 블록) |
| C | stockout 부스트 피드백 계수 | food.py + improved_predictor.py | food.py 신규 함수 + L1169 블록 내 |

### 1-3. 설계 원칙

1. **최소 침습**: 기존 메서드 시그니처 변경 없음, 기존 반환값 구조 유지
2. **푸드 전용**: `is_food_category(mid_cd)` 가드로 비푸드 영향 차단
3. **토글 가능**: `STOCKOUT_BOOST_ENABLED` 설정으로 C 기능 비활성화 가능
4. **sell_day_ratio 활용**: 이미 `_compute_safety_and_order()`에 전달되는 파라미터 재활용 (신규 DB 쿼리 불필요)

---

## 2. 상세 설계

### 2-A. 폐기계수 조건부 적용

#### 현재 코드 (improved_predictor.py L1169-1184)

```python
elif is_food_category(mid_cd):
    from src.prediction.categories.food import get_unified_waste_coefficient
    unified_waste_coef = get_unified_waste_coefficient(
        item_cd, mid_cd, store_id=self.store_id, db_path=self.db_path
    )
    food_disuse_coef = unified_waste_coef
    disuse_rate = self._get_disuse_rate(item_cd)

    if unified_waste_coef < 1.0:
        adjusted_prediction *= unified_waste_coef   # <-- 무조건 적용
        daily_avg = adjusted_prediction
        ctx["adjusted_prediction"] = adjusted_prediction
        logger.info(...)
```

#### 변경 설계

```python
elif is_food_category(mid_cd):
    from src.prediction.categories.food import (
        get_unified_waste_coefficient,
        get_stockout_boost_coefficient,      # NEW (C)
    )
    unified_waste_coef = get_unified_waste_coefficient(
        item_cd, mid_cd, store_id=self.store_id, db_path=self.db_path
    )
    food_disuse_coef = unified_waste_coef
    disuse_rate = self._get_disuse_rate(item_cd)

    # --- A: 폐기계수 조건부 적용 ---
    # sell_day_ratio는 이미 파라미터로 전달됨
    # stockout_freq = 1.0 - sell_day_ratio (비판매일 비율 ≈ 품절 빈도)
    stockout_freq = 1.0 - sell_day_ratio if sell_day_ratio is not None else 0.0

    if stockout_freq > 0.50:
        # 50% 이상 품절: 폐기계수 면제
        effective_waste_coef = 1.0
        logger.info(
            f"[폐기계수면제] {product['item_nm']}: "
            f"stockout={stockout_freq:.0%} > 50% → waste_coef={unified_waste_coef:.2f} 면제"
        )
    elif stockout_freq > 0.30:
        # 30~50% 품절: 최소 0.90 보장
        effective_waste_coef = max(unified_waste_coef, 0.90)
        if effective_waste_coef != unified_waste_coef:
            logger.info(
                f"[폐기계수완화] {product['item_nm']}: "
                f"stockout={stockout_freq:.0%}, "
                f"waste_coef {unified_waste_coef:.2f} → {effective_waste_coef:.2f}"
            )
    else:
        # 30% 미만 품절: 기존 로직 유지
        effective_waste_coef = unified_waste_coef

    if effective_waste_coef < 1.0:
        adjusted_prediction *= effective_waste_coef
        daily_avg = adjusted_prediction
        ctx["adjusted_prediction"] = adjusted_prediction
        logger.info(
            f"폐기 보정: {product['item_nm']} "
            f"(unified={unified_waste_coef:.2f}, effective={effective_waste_coef:.2f})"
        )

    # --- B: compound floor 이후 최종 하한 보장 ---
    # _apply_multiplicative()의 compound_floor(15%)는 이미 적용됨
    # unified_waste_coef가 추가 곱셈으로 실효 하한을 10.5%로 낮추는 문제 해결
    final_floor = base_prediction * 0.20  # base의 20% 보장
    if adjusted_prediction < final_floor and base_prediction > 0:
        logger.info(
            f"[최종하한] {product['item_nm']}: "
            f"adj={adjusted_prediction:.2f} < floor={final_floor:.2f} "
            f"→ {final_floor:.2f}"
        )
        adjusted_prediction = final_floor
        daily_avg = adjusted_prediction
        ctx["adjusted_prediction"] = adjusted_prediction

    # --- C: stockout 부스트 계수 ---
    stockout_boost = get_stockout_boost_coefficient(stockout_freq)
    if stockout_boost > 1.0:
        before_boost = adjusted_prediction
        adjusted_prediction *= stockout_boost
        daily_avg = adjusted_prediction
        ctx["adjusted_prediction"] = adjusted_prediction
        ctx["stockout_boost"] = stockout_boost
        logger.info(
            f"[품절부스트] {product['item_nm']}: "
            f"stockout={stockout_freq:.0%}, "
            f"boost={stockout_boost:.2f}x, "
            f"{before_boost:.2f} → {adjusted_prediction:.2f}"
        )

    # (이후 기존 safety_stock 로직 계속)
```

#### 데이터 흐름

```
sell_day_ratio (파라미터, 0.0~1.0)
  ↓
stockout_freq = 1.0 - sell_day_ratio
  ↓
  ├─ >0.50 → effective_waste_coef = 1.0 (면제)
  ├─ >0.30 → effective_waste_coef = max(원본, 0.90)
  └─ else  → effective_waste_coef = 원본
  ↓
adjusted_prediction *= effective_waste_coef
  ↓
final_floor = base_prediction * 0.20 (B)
adjusted_prediction = max(adjusted_prediction, final_floor)
  ↓
stockout_boost = get_stockout_boost_coefficient(stockout_freq) (C)
adjusted_prediction *= stockout_boost
```

#### sell_day_ratio vs 직접 stockout 쿼리

| 방식 | 장점 | 단점 |
|------|------|------|
| `1.0 - sell_day_ratio` (선택) | DB 쿼리 추가 없음, 이미 계산된 값 | 판매일 비율 ≠ 정확한 품절 빈도 |
| `_get_stockout_frequency()` 직접 | stock_qty=0 직접 체크 | item별 추가 쿼리, 성능 부담 |

**결정**: `sell_day_ratio` 활용. sell_day_ratio가 낮으면 판매일이 적다는 것이고, 푸드류에서 이는 대부분 품절(stock=0)과 직결된다. 정밀도보다 성능과 단순성 우선.

**sell_day_ratio 출처**: `DemandClassifier._query_sell_stats_batch()` → 60일 윈도우, available_days(영업일) 기준 sell_days 비율. `_compute_base_prediction()`에서 반환 → `_compute_safety_and_order()` 파라미터로 전달.

---

### 2-B. compound floor 이후 최종 하한

#### 현재 문제 시각화

```
coefficient_adjuster._apply_multiplicative():
  compound_floor = base * 0.15          # L591
  adjusted = max(adjusted, compound_floor)  # 15% 보장

improved_predictor._compute_safety_and_order():
  adjusted *= unified_waste_coef(0.70)     # L1178
  → 최종: base * 0.15 * 0.70 = base * 0.105  # 실효 10.5%
```

#### 변경 후

```
improved_predictor._compute_safety_and_order():
  adjusted *= effective_waste_coef         # A에서 조건부 적용
  final_floor = base * 0.20               # NEW: 20% 하한
  adjusted = max(adjusted, final_floor)    # 최악에서도 20% 보장
```

#### 상수 결정 근거

- 현재 실효 하한: `base * 0.105` (10.5%)
- 새 하한: `base * 0.20` (20%)
- 이유: compound_floor(15%)보다 약간 높은 값으로 설정. 폐기계수가 면제되는 경우(A)에는 이 floor에 도달하지 않으므로, 주로 stockout 30% 미만(폐기계수 원본 적용)인 경우의 보호 장치.

---

### 2-C. stockout 부스트 피드백 계수

#### 신규 함수: `food.py`

```python
# food.py 끝부분에 추가 (get_unified_waste_coefficient 이후)

# 품절 기반 예측 부스트 설정
STOCKOUT_BOOST_ENABLED = True           # 토글
STOCKOUT_BOOST_THRESHOLDS = {
    0.70: 1.30,   # 70%+ 품절 → 30% 부스트
    0.50: 1.15,   # 50%+ 품절 → 15% 부스트
    0.30: 1.05,   # 30%+ 품절 → 5% 부스트
}


def get_stockout_boost_coefficient(stockout_freq: float) -> float:
    """
    기회손실(stockout) 기반 예측 부스트 계수.

    품절 빈도가 높은 상품의 예측을 증가시켜 발주량을 늘린다.
    푸드 전용으로 improved_predictor에서 is_food_category() 체크 후 호출.

    Args:
        stockout_freq: 품절 빈도 (0.0~1.0). 1.0 - sell_day_ratio로 계산.

    Returns:
        부스트 계수 (1.00~1.30). 1.0이면 부스트 없음.

    Examples:
        >>> get_stockout_boost_coefficient(0.80)  # 80% 품절
        1.30
        >>> get_stockout_boost_coefficient(0.55)  # 55% 품절
        1.15
        >>> get_stockout_boost_coefficient(0.10)  # 10% 품절 (정상)
        1.00
    """
    if not STOCKOUT_BOOST_ENABLED:
        return 1.0

    for threshold, boost in sorted(
        STOCKOUT_BOOST_THRESHOLDS.items(), reverse=True
    ):
        if stockout_freq >= threshold:
            return boost
    return 1.0
```

#### 함수 위치 결정

| 후보 | 이유 | 결정 |
|------|------|------|
| `food.py` (선택) | 폐기계수(get_unified_waste_coefficient)와 같은 파일, 푸드 전용 로직 | O |
| `coefficient_adjuster.py` | 기존 곱셈 파이프라인에 추가 | X (waste_coef 이후 단계에 적용되므로 별개) |
| `prediction_config.py` | 설정값만 두는 파일 | X (함수 로직 포함이므로 부적합) |

#### 적용 순서 (improved_predictor.py 내)

```
1. adjusted_prediction = base × 8개 계수 (coefficient_adjuster)
2. compound_floor = base × 0.15 (coefficient_adjuster)
3. adjusted *= effective_waste_coef (A: 조건부 폐기계수)
4. adjusted = max(adjusted, base × 0.20) (B: 최종 하한)
5. adjusted *= stockout_boost (C: 품절 부스트)
```

**C가 B 이후에 적용되는 이유**: 하한 보장(B) 후 부스트(C)를 적용해야 최종 예측이 하한 이상 + 부스트 반영.

---

## 3. 수정 파일 상세

### 3-1. `src/prediction/categories/food.py`

| 변경 | 내용 |
|------|------|
| 추가 | `STOCKOUT_BOOST_ENABLED` 상수 |
| 추가 | `STOCKOUT_BOOST_THRESHOLDS` 딕셔너리 |
| 추가 | `get_stockout_boost_coefficient(stockout_freq)` 함수 |
| 위치 | `get_unified_waste_coefficient()` 함수 이후 (파일 끝부분, 약 L981 이후) |

### 3-2. `src/prediction/improved_predictor.py`

| 변경 | 내용 | 위치 |
|------|------|------|
| 수정 | import에 `get_stockout_boost_coefficient` 추가 | L1170 |
| 수정 | `elif is_food_category(mid_cd):` 블록 전체 교체 | L1169-1184 → L1169-~1215 |
| 추가 | `stockout_freq` 계산 + 조건부 waste_coef (A) | |
| 추가 | `final_floor` 하한 보장 (B) | |
| 추가 | `stockout_boost` 적용 (C) | |
| 추가 | `ctx["stockout_freq"]`, `ctx["stockout_boost"]`, `ctx["effective_waste_coef"]` | |

### 3-3. `tests/test_food_stockout_balance.py` (신규)

| 테스트 그룹 | 케이스 수 | 설명 |
|------------|----------|------|
| TestWasteCoefConditional | 5 | A: 폐기계수 조건부 적용 |
| TestFinalFloor | 4 | B: 최종 하한 보장 |
| TestStockoutBoost | 5 | C: 부스트 계수 계산 |
| TestIntegration | 4 | A+B+C 통합 시나리오 |
| **합계** | **18** | |

---

## 4. 테스트 설계

### 4-1. TestWasteCoefConditional (A)

| # | 케이스 | sell_day_ratio | stockout_freq | unified_waste | effective_waste | 검증 |
|---|--------|---------------|---------------|---------------|-----------------|------|
| 1 | 고품절(>50%) | 0.30 | 0.70 | 0.75 | 1.00 | waste_coef 면제 |
| 2 | 중품절(30~50%) | 0.55 | 0.45 | 0.80 | 0.90 | 최소 0.90 보장 |
| 3 | 중품절, 원본>0.90 | 0.55 | 0.45 | 0.95 | 0.95 | 원본 유지 |
| 4 | 저품절(<30%) | 0.85 | 0.15 | 0.80 | 0.80 | 원본 그대로 |
| 5 | sell_day_ratio=None | None | 0.0 | 0.80 | 0.80 | None 안전처리 |

### 4-2. TestFinalFloor (B)

| # | 케이스 | base | adjusted (waste후) | final_floor | 결과 |
|---|--------|------|-------------------|-------------|------|
| 1 | 하한 적중 | 10.0 | 1.5 | 2.0 | 2.0 (하한 적용) |
| 2 | 하한 미적중 | 10.0 | 5.0 | 2.0 | 5.0 (그대로) |
| 3 | base=0 | 0.0 | 0.0 | 0.0 | 0.0 (하한 0) |
| 4 | waste면제+하한 | 10.0 | 1.2 (compound floor 후) | 2.0 | 2.0 |

### 4-3. TestStockoutBoost (C)

| # | 케이스 | stockout_freq | boost | 검증 |
|---|--------|---------------|-------|------|
| 1 | 극심 품절 | 0.80 | 1.30 | 30% 부스트 |
| 2 | 높은 품절 | 0.55 | 1.15 | 15% 부스트 |
| 3 | 중간 품절 | 0.35 | 1.05 | 5% 부스트 |
| 4 | 낮은 품절 | 0.10 | 1.00 | 부스트 없음 |
| 5 | 토글 OFF | 0.80 | 1.00 | 비활성화 시 1.0 |

### 4-4. TestIntegration (A+B+C)

| # | 시나리오 | sell_day_ratio | base | waste_coef | 기대 결과 |
|---|---------|---------------|------|-----------|----------|
| 1 | 고품절+부스트 | 0.25 | 10.0 | 0.75 | A: 면제(1.0), B: 패스, C: 1.30x → adj=13.0 |
| 2 | 중품절+하한 | 0.60 | 2.0 | 0.70 | A: 0.90, B: base×0.20=0.4>adj=0.36→0.4, C: 1.05x → 0.42 |
| 3 | 정상+폐기보정 | 0.90 | 10.0 | 0.80 | A: 0.80, B: 패스, C: 1.0 → adj=8.0 |
| 4 | 비푸드 미적용 | 0.25 | 10.0 | N/A | A/B/C 모두 미적용, 기존 로직 |

---

## 5. ctx 필드 추가

`_compute_safety_and_order()`의 반환 ctx에 추가되는 필드:

| 필드 | 타입 | 설명 |
|------|------|------|
| `stockout_freq` | float | 1.0 - sell_day_ratio (품절 빈도 추정) |
| `effective_waste_coef` | float | 조건부 적용 후 실제 사용된 폐기 계수 |
| `stockout_boost` | float | 부스트 계수 (1.0~1.30) |

---

## 6. 기존 코드와의 상호작용

### 6-1. 캘리브레이터 충돌 방지

| 모듈 | 현재 동작 | 이번 변경 영향 |
|------|----------|---------------|
| FoodWasteRateCalibrator | 폐기율 기반 safety_days/gap_coef 조정 | A에서 waste_coef 면제해도 캘리브레이터의 safety_days 조정은 독립적으로 유지 |
| unified_waste_coef | 0.70~1.0 계산 | A에서 적용 여부만 조건부, 계산 자체는 변경 없음 |
| DiffFeedbackPenalty | order_qty에 penalty 적용 | stockout_boost는 adjusted_prediction에 적용되므로 간접 영향만 (need_qty 증가 → order_qty 증가) |

### 6-2. DemandClassifier 면제

- 푸드류(001~005, 012)는 `DEMAND_PATTERN_EXEMPT_MIDS`에 포함
- `sell_day_ratio`는 DemandClassifier 분류와 별도로 `_compute_base_prediction()`에서 계산
- 이번 변경은 sell_day_ratio를 읽기만 하므로 DemandClassifier에 영향 없음

### 6-3. ML 앙상블 영향

- ML 앙상블은 `order_qty` 단계에서 적용 (L1451)
- stockout_boost로 인해 `adjusted_prediction`이 높아지면 → `need_qty` 증가 → `order_qty` 증가
- ML이 order_qty를 재조정할 수 있지만, 블렌딩 비율(0.1~0.5)이므로 부스트 효과 일부 유지

### 6-4. 카테고리 최대 발주량

- `MAX_ORDER_QTY_BY_CATEGORY` (L1503)에서 최종 상한 적용
- stockout_boost로 과도한 발주량이 나와도 상한으로 제한됨

---

## 7. 구현 순서

```
1. food.py: get_stockout_boost_coefficient() 함수 + 상수 추가
2. improved_predictor.py: L1169-1184 블록 수정 (A+B+C 통합)
3. tests/test_food_stockout_balance.py: 테스트 18개 작성
4. 기존 테스트 실행 (회귀 확인)
```

---

## 8. 성공 기준 (Plan 참조)

| 지표 | 현재 | 목표 | 비고 |
|------|------|------|------|
| 예측 편향 (bias) | -0.36 ~ -0.65 | -0.15 이내 | 라이브 데이터 기준 |
| stockout 비율 | 60 ~ 93% | 40% 이하 | 라이브 데이터 기준 |
| 폐기율 | 5 ~ 16% | 목표 +/- 5%p | 증가 허용 범위 내 |
| pred=0 비율 | 46 ~ 54% | 30% 이하 | A+B 효과 |

---

## 9. 리스크 대응

| 리스크 | 대응 |
|--------|------|
| 폐기율 급증 | `STOCKOUT_BOOST_ENABLED = False`로 C 비활성화 |
| 캘리브레이터 진동 | 캘리브레이터는 14일 윈도우라 부스트 효과 서서히 반영 |
| sell_day_ratio 부정확 | 60일 윈도우 기반이라 14일 단기 품절 감지 부족 가능 → 별도 PDCA로 개선 |
| 비푸드 영향 | `is_food_category(mid_cd)` 가드 + 비푸드 통합 테스트 |
