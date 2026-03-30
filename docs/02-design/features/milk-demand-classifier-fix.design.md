# Design: 우유(047) DemandClassifier 수요분류 버그 수정

> Plan 참조: `docs/01-plan/features/milk-demand-classifier-fix.plan.md`

## 1. ~~변경 1: sell_days 쿼리 수정~~ → 기각 (토론 결과)

### 기각 이유 (2026-03-31 토론, 악마의변호인+ML엔지니어 합의)

1. **효과 없음**: 시뮬레이션 결과 비면제 카테고리 분류 변경 0건
2. **부작용**: sell_days/available_days 분자/분모 기준 불일치 → ratio>100% 이상값
3. **불일치**: base_predictor.py에 동일 조건 별도 쿼리 존재 → 두 곳 불일치
4. **근본 원인 다름**: 우유 TOP 15는 이미 daily. 50개 예측=0은 진짜 무판매 상품
5. **047 면제만으로 충분**: 1줄 변경, 부작용 없음, 검증된 메커니즘

## 2. 변경 2: DEMAND_PATTERN_EXEMPT_MIDS에 047 추가

### 현재 코드

**파일**: `src/prediction/demand_classifier.py` **L37-39:**
```python
DEMAND_PATTERN_EXEMPT_MIDS = {
    "001", "002", "003", "004", "005", "012",  # 푸드류
}
```

### 변경
```python
DEMAND_PATTERN_EXEMPT_MIDS = {
    "001", "002", "003", "004", "005", "012",  # 푸드류
    "047",  # 우유 — 당일소진 고회전으로 SLOW 오분류 방지
}
```

### 면제의 의미
- 면제 상품은 DemandClassifier를 거치지 않고 **DAILY 패턴**으로 처리
- WMA 파이프라인으로 직행 → 예측=0이 아닌 WMA 기반 예측값 생성
- 이미 6개 푸드 카테고리(001~005, 012)에 적용 중인 검증된 메커니즘

### 면제의 참조 위치 (기존 코드에서 사용하는 곳)
| 파일 | 라인 | 용도 |
|------|------|------|
| `demand_classifier.py` | L75 | 단일 분류 시 면제 체크 |
| `demand_classifier.py` | L97-98 | 배치 분류 시 면제/대상 분리 |
| `base_predictor.py` | L68, L75 | 예측 시 면제 상품 WMA 직행 |
| `improved_predictor.py` | L1046, L1056 | 예측 시 면제 상품 WMA 직행 |
| `coefficient_adjuster.py` | L810, L888 | 면제 상품 곱셈 계수 적용 |

## 3. 구현 순서 (토론 반영)

```
Step 1: demand_classifier.py — DEMAND_PATTERN_EXEMPT_MIDS에 "047" 추가 (L38)
Step 2: 테스트 실행
Step 3: 커밋
```

## 4. 수정 파일 요약

| Step | 파일 | 라인 | 변경 |
|------|------|------|------|
| 1 | `src/prediction/demand_classifier.py` | L38 | `"047"` 추가 (1줄) |

**sell_days 쿼리(L221, L251)는 변경하지 않음** (토론 합의)

## 5. 향후 조사 항목 (별도 이슈)

- stock_qty=0 + sale_qty>0 레코드의 수집기 원인 조사
- base_predictor.py `_get_sell_day_ratio()`와의 일관성 확인
- 50개 예측=0 상품 중 진짜 무판매 vs 레코드 미존재 구분

## 6. 검증 계획

### 단위 검증
1. 047 면제 후: 바나나우유 예측값이 0 → WMA 기반 양수로 변경 확인
2. 기존 푸드(001~005, 012) 분류 결과가 변경되지 않았는지 확인

### 통합 검증
3. prediction_logs에서 047 상품 order_qty > 0 비율 확인

### 회귀 검증
4. pytest 전체 실행 — demand_classifier 관련 테스트 통과 확인
