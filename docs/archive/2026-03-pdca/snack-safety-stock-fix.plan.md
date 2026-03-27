# Plan: snack-safety-stock-fix

> **Date**: 2026-03-24 | **Status**: Draft | **Priority**: High

## 1. Problem Statement

### 현상
스낵/과자류(015~020, 029, 030) 안전재고가 과잉 산출되어 저회전 상품에 과잉발주 발생.

**실제 사례**: 47863 매장 `0009800800056` (페레로누텔라앤고)
- 30일 중 **2일만** 판매 (총 7개)
- `daily_avg = 7/2 = 3.5` (팽창된 값)
- 실제 일평균: `7/30 = 0.23`
- 안전재고: `3.5 × 2(간격) × 1.03(요일) = 7.21` → **실재고 5개 있는데 6개 추가 발주**

### 근본 원인
`snack_confection.py` L313-318:
```python
data_days = COUNT(DISTINCT sales_date)  # 판매가 있었던 날만 셈
daily_avg = total_sales / data_days     # 2일/7개 = 3.5 (팽창!)
```
판매 기록이 있는 날수로 나누므로, 저회전 상품일수록 `daily_avg`가 과대평가됨.

### 영향 범위
| 매장 | 스낵 총 상품 | sparse (<14일) | 비율 |
|------|:-----------:|:-------------:|:----:|
| 46513 | 508 | **471 (93%)** | 최대 차이 7.47배 |
| 46704 | 592 | **543 (92%)** | 최대 차이 4.33배 |
| 47863 | 337 | **335 (99%)** | 최대 차이 7.73배 |

**3매장 전체 스낵 상품의 90%+ 가 영향권**.

## 2. Root Cause Analysis

### 현재 코드 흐름
```
snack_confection.py:analyze_snack_confection_pattern()
  L294-308: SQL → COUNT(DISTINCT sales_date), SUM(sale_qty)
  L313-318: daily_avg = total_sales / data_days
            → 판매 없는 날 무시 → 과대평가
  L315:     has_enough_data = data_days >= 14
            → False여도 daily_avg는 이미 계산됨 (보정 없음)
  L329-330: safety_days = order_interval (발주간격)
            final_safety_stock = daily_avg × order_interval × weekday_coef
            → 팽창된 daily_avg로 안전재고 과대 산출
```

### 대조: WMA (base_predictor)
```
improved_predictor → base_predictor._predict_wma()
  → 최근 7일 전체를 대상으로 WMA 계산
  → 판매 없는 날 = 0으로 포함
  → daily_avg = 0.00 (정상)
```

**두 모듈이 같은 상품에 대해 다른 daily_avg를 산출**: WMA=0.00 vs snack=3.50

## 3. Proposed Fix

### Fix A: daily_avg 계산 정상화 (1파일 1개소)

**파일**: `src/prediction/categories/snack_confection.py` L313-318

**Before**:
```python
data_days = row[0] or 0          # 판매 있는 날 수
total_sales = row[1] or 0
daily_avg = (total_sales / data_days) if data_days > 0 else 0.0
```

**After**:
```python
data_days = row[0] or 0          # 판매 있는 날 수
total_sales = row[1] or 0
analysis_days = config["analysis_days"]  # 30
# 전체 기간으로 나누어 실제 일평균 산출
daily_avg = (total_sales / analysis_days) if total_sales > 0 else 0.0
```

**효과**:
- 페레로누텔라앤고: `3.5 → 0.23`, safety: `7.21 → 0.47` → 발주 0개 (정상)
- 전체 스낵 90%+ 상품의 안전재고 정상화

### Fix B: 유령재고 정리에 active 배치 보호 추가 (보완)

**파일**: `src/infrastructure/database/repos/inventory_repo.py` `cleanup_stale_stock()`

queried_at이 오래됐어도 `inventory_batches`에 active(remaining_qty>0) 배치가 있으면 정리 제외.

```python
# 상품별 TTL 판정 루프 내
if self._has_active_batch(item_cd, cursor):
    continue  # active 배치 있으면 보호
```

**효과**: daily_avg와 무관하게, 입고된 재고가 유령으로 잘못 삭제되는 것 방지.

## 4. Risk Assessment

| 리스크 | 확률 | 영향 | 대응 |
|--------|:----:|:----:|------|
| Fix A로 안전재고 과소 → 품절 | 낮음 | 중간 | max_stock_days(5일) 상한이 있으므로 고회전 상품은 영향 없음. ROP 최소 보장 유지 |
| Fix B 배치 데이터 오류 시 유령재고 잔존 | 낮음 | 낮음 | BatchSync(Phase 1.66)에서 이중 정리 |
| 기존 테스트 실패 | 중간 | 낮음 | snack_confection 테스트에서 daily_avg 기대값 수정 필요 |

## 5. Implementation Plan

| 순서 | 작업 | 파일 | 예상 |
|:----:|------|------|:----:|
| 1 | daily_avg 계산 정상화 | snack_confection.py L313-318 | 5분 |
| 2 | active 배치 보호 | inventory_repo.py cleanup_stale_stock() | 10분 |
| 3 | 기존 테스트 수정 | tests/ snack 관련 | 10분 |
| 4 | 시뮬레이션 검증 | 3매장 × 샘플 상품 | 5분 |

## 6. Verification

- 페레로누텔라앤고 시뮬레이션: safety 7.21 → ~0.47
- 3매장 snack sparse 상품 일평균 비교 (before/after)
- 기존 snack 테스트 통과
- 유령재고 정리에서 active 배치 상품 보호 확인

## 7. Scope Limitation

- **Fix A만으로도 근본 해결**: daily_avg가 정확하면 과잉발주 없음
- **Fix B는 방어적 보완**: stale RI 문제 전반에 대한 안전망
- 다른 카테고리(food, beer 등)는 자체 모듈에서 daily_avg를 별도 계산하므로 영향 없음
