# Plan: 우유(047) DemandClassifier 수요분류 버그 수정

## 1. 배경 및 문제 정의

### 현재 문제
- 우유(047) **50개 상품**이 예측=0인데 14일간 **330개 실제 판매** (기회손실)
- 바나나우유(일 3.5개), 초코에몽(일 2.0개) 같은 **필수 상품도 예측=0**
- 근본 원인: DemandClassifier의 `sell_days` 쿼리 버그

### 근본 원인 (토론에서 특정)
```sql
-- 현재 (버그): EOD 재고=0인 날을 판매일에서 제외
SUM(CASE WHEN stock_qty > 0 AND sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
```

우유처럼 당일 소진되는 고회전 상품은 EOD `stock_qty=0`이 되어,
실제 판매가 있었어도 sell_days에서 누락 → sell_day_ratio 하락 → SLOW 분류 → 예측=0

### 영향 범위
- 우유(047)뿐 아니라 **전 카테고리 고회전 상품**에 동일 버그 적용
- 특히 유동인구형 매장(47863, 49965)에서 품절→재고0→SLOW 악순환

## 2. 변경 범위

### 변경 1: sell_days 쿼리 수정
```sql
-- 변경: sale_qty > 0이면 재고 여부와 무관하게 판매일로 인정
SUM(CASE WHEN sale_qty > 0 THEN 1 ELSE 0 END) as sell_days
```
- `available_days`(분모)는 `stock_qty > 0` **유지** (진열되지 않은 날은 분모에서 제외)
- 분자(sell_days)만 수정: "판매가 있었으면 판매일"

### 변경 2: DEMAND_PATTERN_EXEMPT_MIDS에 047 추가 (안전망)
```python
DEMAND_PATTERN_EXEMPT_MIDS = {
    "001", "002", "003", "004", "005", "012",  # 푸드류 (기존)
    "047",  # 우유 — slow 분류 방지 안전망
}
```

## 3. 수정 파일

| 파일 | 변경 |
|------|------|
| `src/prediction/demand_classifier.py` | sell_days 쿼리 2곳 수정 (`_query_sell_stats`, `_query_sell_stats_batch`) |
| `src/settings/constants.py` | DEMAND_PATTERN_EXEMPT_MIDS에 "047" 추가 |
| 테스트 | demand_classifier 관련 테스트 확인/수정 |

## 4. 리스크

| 리스크 | 완화 |
|--------|------|
| sell_days 변경으로 전 카테고리 분류 영향 | available_days(분모)는 유지 → ratio 계산 변화 최소 |
| 047 면제로 slow 상품도 발주 | 면제는 WMA 파이프라인으로만 보냄, 실제 발주량은 WMA 결과에 따름 |
| 기존 테스트 깨짐 | sell_days 관련 기대값 수정 필요 |

## 5. 성공 지표

- 우유(047) 기회손실: 330개/14일 → **50개 이하** (85% 감소)
- slow 분류 우유 상품: 22개 → **5개 이하**
- 바나나우유 예측=0 해소 → **예측>0 매일 발생**
