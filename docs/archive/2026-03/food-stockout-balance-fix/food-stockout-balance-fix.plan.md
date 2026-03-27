# Plan: food-stockout-balance-fix

> 푸드 예측 과소편향 해소 — 기회손실·폐기 균형 최적화

---

## 1. 문제 정의

### 현상
- 양쪽 매장(46513, 46704) 푸드 전 카테고리에서 **체계적 과소예측**
- adjusted_prediction이 실제 판매의 **30~60% 수준** (편향 -58%~-86%)
- 기회손실(stockout) **60~93%** — 재고=0인데 판매 발생
- 폐기율은 목표 대비 양호~약간 초과 → 현재 시스템이 **폐기 감소에 과도 초점**

### 근본 원인 (3중 감소 구조)

```
[1단계] WMA 과소추정: 0-sale 일 포함으로 실제의 70% 수준
[2단계] 곱셈 계수 누적: 8개 계수 상시 감소 → 추가 40~54% 감소
[3단계] 통합 폐기계수: compound floor 이후 별도 곱셈 → 최종 바닥 base×10.5%
```

### 근거 분석 문서
- `docs/03-analysis/food-prediction-store-analysis.md` — 매장 데이터 지표
- `docs/03-analysis/food-prediction-code-data-analysis.md` — 코드 데이터흐름

---

## 2. 해결 범위

### In-scope (이번 PDCA)

| # | 개선 항목 | 대상 파일 | 복잡도 |
|---|----------|----------|--------|
| A | **폐기계수 조건부 적용**: stockout_freq > 50%이면 waste_coef 면제 | improved_predictor.py | Low |
| B | **compound floor 이후 waste_coef 보호**: 2차 곱셈에 하한 적용 | improved_predictor.py | Low |
| C | **stockout → 예측 부스트 피드백**: stockout_freq 기반 예측 증가 계수 | improved_predictor.py, food.py | Medium |

### Out-of-scope (별도 PDCA)

| 항목 | 이유 |
|------|------|
| 입고율 보정 (fulfillment rate) | 46704 전용, 별도 데이터 수집 체계 필요 |
| WMA 0-sale 보정 (판매일 기준 평균) | base_predictor 핵심 변경, 영향 범위 큼 |
| 기회손실 대시보드 | presentation 레이어, 별도 기능 |
| DemandClassifier slow 분류 기준 변경 | prediction-redesign 범위 |

---

## 3. 상세 설계 방향

### A. 폐기계수 조건부 적용

**문제**: stockout 60~93%인데 폐기계수(0.70~1.0)가 예측을 추가 감소
**해결**: stockout_freq가 임계값 이상이면 waste_coef를 면제 또는 완화

```python
# improved_predictor.py _compute_safety_and_order()
stockout_freq = self._get_stockout_frequency(item_cd, 14)  # 14일
if stockout_freq > 0.50:  # 50% 이상 품절
    waste_coef = 1.0  # 폐기계수 면제
elif stockout_freq > 0.30:
    waste_coef = max(waste_coef, 0.90)  # 최소 0.90 보장
# else: 기존 로직 유지
```

**기대 효과**: stockout 상품의 과소예측 즉시 해소

### B. compound floor 이후 waste_coef 보호

**문제**: compound_floor(15%) 통과 후 waste_coef(0.70)가 별도 곱셈 → 실효 하한 10.5%
**해결**: waste_coef 적용 후에도 최종 하한 보장

```python
# improved_predictor.py
adjusted_prediction *= unified_waste_coef
# 새로 추가: 최종 하한 보장
final_floor = base_prediction * 0.20  # base의 20% 이하로 떨어지지 않음
adjusted_prediction = max(adjusted_prediction, final_floor)
```

**기대 효과**: 최악 케이스에서도 base의 20% 보장 (10.5% → 20%)

### C. stockout → 예측 부스트 피드백

**문제**: stockout 데이터가 수집되지만 예측에 피드백 없음
**해결**: stockout_freq 기반 예측 증가 계수 신규 추가

```python
# food.py (또는 coefficient_adjuster.py)
def get_stockout_boost_coefficient(item_cd, store_id):
    """기회손실 기반 예측 부스트"""
    stockout_freq = _get_stockout_freq(item_cd, store_id, days=14)
    if stockout_freq > 0.70:
        return 1.30  # 30% 부스트
    elif stockout_freq > 0.50:
        return 1.15  # 15% 부스트
    elif stockout_freq > 0.30:
        return 1.05  # 5% 부스트
    return 1.00
```

**적용 위치**: 곱셈 계수 적용 단계에서 stockout_boost 추가
**기대 효과**: 품절 빈번 상품의 발주량 자동 증가 → 기회손실 감소

---

## 4. 수정 파일 예상

| 파일 | 변경 내용 |
|------|----------|
| `src/prediction/improved_predictor.py` | A: waste_coef 조건부, B: final_floor, C: stockout_boost 적용 |
| `src/prediction/categories/food.py` | C: get_stockout_boost_coefficient() 신규 |
| `src/prediction/coefficient_adjuster.py` | C: stockout_boost를 곱셈 파이프라인에 추가 (선택) |
| `tests/test_food_stockout_balance.py` | 전체 테스트 |

---

## 5. 성공 기준

| 지표 | 현재 | 목표 | 측정 방법 |
|------|------|------|----------|
| 예측 편향 (bias) | -0.36~-0.65 | **-0.15 이내** | prediction_logs JOIN daily_sales |
| stockout 비율 | 60~93% | **40% 이하** | stock_qty=0 AND sale_qty>0 비율 |
| 폐기율 | 46513: 16%, 46704: 5% | **목표±5%p 이내** | disuse/(sale+disuse) |
| pred=0 비율 | 46~54% | **30% 이하** | prediction_logs에서 predicted_qty=0 비율 |

---

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 폐기율 증가 | 예측 부스트로 과잉 발주 | stockout_boost 상한 1.30, 토글(STOCKOUT_BOOST_ENABLED) |
| 캘리브레이터 충돌 | 부스트↑ vs 캘리브레이터↓ 진동 | 캘리브레이터가 stockout_freq를 참조하도록 연동 |
| 비푸드 영향 | 의도치 않은 범위 확장 | is_food_category() 체크로 푸드만 적용 |

---

## 7. 복잡도 평가

- **전체**: Medium
- **예상 수정**: 3~4파일, ~100줄 추가
- **테스트**: 15~20개
- **예상 소요**: 1 세션
