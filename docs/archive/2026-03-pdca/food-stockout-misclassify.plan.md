# Plan: food-stockout-misclassify

> 푸드류 유통기한 만료에 의한 stock=0을 품절로 오판하여 발생하는 삼중 악순환 해결

## 1. 문제 정의

### 현상
46513 매장 도)밥반찬반함박스테이크2 (8801771036333):
- daily_avg=0.1 (10일에 1번 판매), 14일간 판매 2건 / 폐기 5건
- 매일 1개 발주 → 안 팔림 → 유통기한 만료(폐기) → stock=0
- stock=0을 "품절"로 오판 → UNDER_ORDER → 다음날 또 발주 → 무한 반복
- 예상 연간 폐기율: **90%** (365개 발주, 36개 판매, 329개 폐기)

### 삼중 악순환 구조

```
① DemandClassifier 면제 (mid_cd=001 EXEMPT)
   → 실제 SLOW인 상품도 DAILY 취급 → WMA 파이프라인 강제

② 유통기한 만료 후 stock=0 → was_stockout=True (오판)
   → eval_calibrator.py:259 next_day_stock<=0 → 품절 판정
   → 실제: 안 팔려서 폐기된 것이지 품절이 아님

③ 품절 오판 → 폐기 감량 면제 + 품절 부스트
   → stockout_freq=0.86 (>0.50) → waste_coef=1.0 (감량 면제)
   → stockout_freq>0.70 → stockout_boost=1.30x (예측 30% 상승)
   → 매일 1개 발주 → ②로 돌아감
```

### 영향 범위
- mid_cd=001~005,012 (푸드류) 중 daily_avg < 0.3인 초저회전 상품 전체
- 3매장(46513, 46704, 47863) 공통 발생

## 2. 근본 원인 (code-analyzer 결과)

| # | 원인 | 코드 위치 | 심각도 |
|---|------|----------|--------|
| 1 | EXEMPT 면제로 DemandClassifier 미작동 | `demand_classifier.py:37-39` | HIGH |
| 2 | 유통기한 만료를 품절로 오판 | `eval_calibrator.py:259,448-453` | CRITICAL |
| 3 | 품절 면제로 폐기 계수 무효화 | `improved_predictor.py:1343-1350` | HIGH |
| 4 | 품절 부스트로 예측 증가 | `food.py:1237-1241` | MEDIUM |
| 5 | pre_order_evaluator 간헐수요 스킵 미작동 | `pre_order_evaluator.py:970-1000` | LOW |

## 3. 수정 방안

### Fix A: was_stockout 판정에 폐기 구분 추가 [CRITICAL, 핵심]

**파일**: `src/prediction/eval_calibrator.py`
**위치**: `_judge_normal_order()` (L448-453) + `was_stockout` 판정 (L259)

**현재**:
```python
# L259: next_day_stock <= 0 → was_stockout = True
```

**변경**:
```python
# next_day_stock <= 0 AND disuse_qty == 0 → was_stockout = True
# next_day_stock <= 0 AND disuse_qty > 0 → was_waste_expiry = True (품절 아님)
```

- 폐기가 있었으면(disuse_qty > 0) stock=0이어도 "폐기 소멸"로 분류
- UNDER_ORDER 대신 OVER_ORDER 또는 WASTE_EXPIRY 판정
- 이로써 ②→③ 악순환 차단

### Fix B: 품절 면제/부스트에 폐기율 교차 검증 [HIGH]

**파일**: `src/prediction/improved_predictor.py` (L1343-1362)
**파일**: `src/prediction/categories/food.py` (L1237-1241)

**현재**: stockout_freq > 0.50 → 폐기계수 완전 면제
**변경**: stockout_freq > 0.50 **AND** mid_cd 폐기율 < 25% 일 때만 면제
- 폐기율 25% 이상이면 품절 면제 해제 → waste_coef 정상 적용
- stockout_boost도 동일 조건 추가

### Fix C: EXEMPT 내 초저회전 예외 허용 [MEDIUM, 선택]

**파일**: `src/prediction/demand_classifier.py` (L37-39)

**현재**: mid_cd=001~005,012 → 무조건 EXEMPT (DAILY)
**변경**: EXEMPT이더라도 daily_avg < 0.2 → SLOW 분류 허용
- 10일에 1번도 안 팔리는 상품은 도시락이어도 SLOW 취급
- SLOW → pred=0, ROP에서 1개만 보장 → 발주 빈도 급감

## 4. 수정 우선순위

1. **Fix A** (핵심): was_stockout 폐기 구분 → 악순환의 시작점 차단
2. **Fix B** (보완): 품절 면제에 폐기율 교차 → 이중 안전망
3. **Fix C** (선택): EXEMPT 예외 → 근본적이지만 영향 범위 넓어 신중하게

Fix A만으로도 UNDER_ORDER 오판이 해결되어 ③ 악순환이 끊어짐.
Fix A + Fix B 동시 적용이 최적.

## 5. 테스트 계획

- eval_calibrator: disuse_qty>0 + stock=0 → was_stockout=False 확인
- improved_predictor: 폐기율>25% + stockout_freq>0.50 → 면제 해제 확인
- 통합: daily_avg=0.1 상품의 pred 변화 (매일 1→간헐 0~1)
- 회귀: 기존 3700+ 테스트 통과 확인
- 시뮬레이션: 46513 함박스테이크2 발주 패턴 변화 검증

## 6. 리스크

| 리스크 | 대응 |
|--------|------|
| Fix A로 실제 품절 상품까지 WASTE_EXPIRY로 오판 | disuse_qty > 0 조건으로 구분 (폐기 없으면 기존대로 품절) |
| Fix B 폐기율 25% 기준이 너무 높거나 낮음 | 설정 상수로 추출, 추후 조정 가능 |
| Fix C EXEMPT 해제 시 다른 도시락 상품에 영향 | daily_avg < 0.2 조건으로 초저회전만 대상 |

## 7. 완료 기준

- Match Rate >= 90%
- 46513 함박스테이크2: 연속 UNDER_ORDER 해소
- mid_cd=001 폐기율 32% → 25% 이하 목표
- 기존 테스트 전체 통과
