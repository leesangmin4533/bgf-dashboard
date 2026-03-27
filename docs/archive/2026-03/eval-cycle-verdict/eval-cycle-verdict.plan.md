# Plan: eval-cycle-verdict

> NORMAL_ORDER 판정 기준 개선 - 판매주기 기반 판정 + 최소 진열 + 안전재고

## 1. 배경 및 문제

### 현재 상황
- NORMAL_ORDER 적중률 **7.3%** (30일 누적, 1,105건)
- 과소(UNDER_ORDER) 38.0%, 과잉(OVER_ORDER) 44.6%
- 58개 중분류가 NORMAL_ORDER로 분류되나 대부분 저회전 상품

### 근본 원인
`eval_calibrator.py:407-413`의 판정 기준이 상품 특성을 무시:
```python
elif decision == "NORMAL_ORDER":
    if actual_sold > 0:      # 당일 판매 필수 → 저회전 상품에 부적합
        return "CORRECT"
    if was_stockout:
        return "UNDER_ORDER"
    return "OVER_ORDER"
```

- 일평균 < 1.0 상품: 판매=0이 **정상**인데 "과잉/과소" 판정
- 일평균 ≥ 1.0 상품: 안전재고 유지 여부 미반영
- 행사 상품: 최소 진열 기준 미반영

## 2. 목표

| 지표 | 현재 | 목표 |
|------|------|------|
| NORMAL_ORDER 적중률 | 7.3% (전체 17.4%) | **55%+** |
| 과소 판정 비율 | 38.0% | **15% 이하** |
| 과잉 판정 비율 | 44.6% | **30% 이하** |

## 3. 범위

### 포함
- NORMAL_ORDER 판정 로직 개선 (`_judge_outcome`)
- 저회전 상품 (일평균 < 1.0): 판매주기 기반 판정
- 고회전 상품 (일평균 ≥ 1.0): 다음 발주일 안전재고 기반 판정
- 행사 상품: 최소 진열 기준 반영
- 비행사 상품: 기본 진열 최소 2개

### 제외
- **푸드류(001~005, 012)**: 기존 카테고리 Strategy로 별도 관리
- PreOrderEvaluator 결정 로직 자체 (분류 경계 변경 없음)
- ML 모델/예측 파이프라인 변경 없음

## 4. 제안 방식

### 4.1 저회전 상품 (daily_avg < 1.0) — 판매주기 기반 판정

```
판매주기 = ceil(1 / daily_avg)   # 예: 0.5 → 2일, 0.33 → 3일

판정:
  - 주기 내 1회 이상 판매 → CORRECT
  - 주기 완료 + 0회 판매 + 품절 → UNDER_ORDER
  - 주기 완료 + 0회 판매 + 재고충분 → OVER_ORDER
  - 주기 미완료 → 판정 보류 (PENDING)
```

### 4.2 고회전 상품 (daily_avg ≥ 1.0) — 안전재고 기반 판정

```
다음 발주일까지 필요량 = daily_avg × days_until_next_order

판정:
  - next_day_stock ≥ daily_avg (1일치 이상 유지) → CORRECT
  - was_stockout → UNDER_ORDER
  - next_day_stock > 필요량 × 1.5 (과다 재고) → OVER_ORDER
```

### 4.3 최소 진열 기준

```
행사 상품:
  - 1+1 → 최소 2개 (기존 PROMO_MIN_STOCK_UNITS 활용)
  - 2+1 → 최소 3개

비행사 상품:
  - 기본 최소 2개 진열 유지
  - 푸드류 제외 (별도 관리)

판정 반영:
  - next_day_stock >= min_display → 진열 충분 → CORRECT 후보
  - next_day_stock < min_display → 진열 부족 → UNDER_ORDER
```

## 5. 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/prediction/eval_calibrator.py` | `_judge_outcome()` NORMAL_ORDER 분기 개선, 다일 룩백 헬퍼 |
| `src/settings/constants.py` | `MIN_DISPLAY_QTY = 2`, 관련 상수 |
| `tests/test_eval_calibrator.py` | 판정 로직 테스트 케이스 |

## 6. 리스크

| 리스크 | 대응 |
|--------|------|
| 판정 보류(PENDING) 누적 | 주기 상한(7일) 설정, 초과 시 강제 판정 |
| 다일 룩백 쿼리 성능 | eval_outcomes 자체 테이블에서 최근 N일 조회 (추가 JOIN 없음) |
| 기존 자동 보정과 충돌 | 판정 기준만 변경, 보정 로직은 그대로 → 자연 수렴 |

## 7. 성공 기준

- NORMAL_ORDER 적중률 55% 이상
- 기존 테스트 전체 통과 (2904개)
- 새 테스트 15개 이상 추가
