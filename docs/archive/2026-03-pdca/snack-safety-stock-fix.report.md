# Completion Report: snack-safety-stock-fix

> **Date**: 2026-03-24 | **Match Rate**: 100% | **Status**: Completed

## 1. Summary

스낵/과자류(015~020, 029, 030) 안전재고 과잉 산출 버그 수정 + 유령재고 정리 시 실재고 보호 추가.

### Problem
`snack_confection.py`에서 `daily_avg = total_sales / data_days` (판매일만 셈)로 계산하여 저회전 상품의 일평균이 최대 **15배 과대평가**됨. 3매장 스낵 상품의 **90%+** (1,349/1,437개)가 영향.

### Root Cause
```python
# Before: 30일 중 2일만 판매 → 7/2 = 3.5 (실제: 7/30 = 0.23)
daily_avg = total_sales / data_days  # data_days = COUNT(DISTINCT sales_date)
```

### Fix
```python
# After: 전체 분석기간으로 나눔 → 7/30 = 0.23
daily_avg = total_sales / analysis_days  # analysis_days = 30 (config)
```

## 2. Changes

| # | File | Change | Lines |
|:-:|------|--------|:-----:|
| A | `src/prediction/categories/snack_confection.py` | daily_avg divisor: data_days → analysis_days(30) | L316-321 |
| B | `src/infrastructure/database/repos/inventory_repo.py` | cleanup_stale_stock: active 배치 보호 | L549-601 |

### Fix A Detail
- `analysis_days = config["analysis_days"]` (=30) 추가
- `total_sales / data_days` → `total_sales / analysis_days`
- `has_enough_data` 판정은 기존 `data_days >= 14` 유지 (회전율 레벨용)

### Fix B Detail
- `inventory_batches`에서 `status='active' AND remaining_qty > 0` 인 상품 목록 사전 조회
- stale 판정 시 active 배치 존재하면 `continue` (정리 제외)
- 배치 조회 실패 시 graceful degradation (보호 없이 기존 동작)
- 보호 건수 로깅: `유령 재고 보호: N개 상품 (active 배치 존재)`

## 3. Impact

### Before vs After (페레로누텔라앤고, 47863)

| Metric | Before | After |
|--------|:------:|:-----:|
| daily_avg | 3.5 | **0.23** |
| safety_stock | 7.21 | **0.48** |
| 재고=5일 때 발주 | 6개 (과잉) | **0개** (정상) |
| 재고=0일 때 발주 | 9개 (과잉) | **0개** (정상) |

### 3매장 영향 범위

| Store | Snack Items | Sparse (<14d) | Ratio |
|:-----:|:-----------:|:-------------:|:-----:|
| 46513 | 508 | 471 | 93% |
| 46704 | 592 | 543 | 92% |
| 47863 | 337 | 335 | 99% |

### 고회전 상품 영향 (안전)
고회전 상품은 data_days ≈ analysis_days이므로 차이 미미:
- 46704 top seller: old_avg=6.2 → new_avg=4.33 (safe=6.93, 적정)
- max_stock_days(5일) 상한 유지

## 4. Test Results

| Category | Count | Result |
|----------|:-----:|:------:|
| snack 관련 | 95 | All Pass |
| inventory/stale/cleanup | 118 | All Pass |
| **Total** | **213** | **All Pass** |

Pre-existing failures (무관): test_schema_version(64≠67), test_feature_count(48≠45)

## 5. PDCA Cycle

```
[Plan] ✅ → [Design] (skipped) → [Do] ✅ → [Check] ✅ → [Report] ✅
```

| Phase | Date | Result |
|-------|------|--------|
| Plan | 2026-03-24 | 2 fixes identified, risk assessed |
| Do | 2026-03-24 | 2 files modified |
| Check | 2026-03-24 | Match Rate 100%, 213 tests pass |
| Report | 2026-03-24 | This document |

## 6. Related Fixes (Same Session)

| Feature | File | Description |
|---------|------|-------------|
| eval-outcomes-unique-fix | schema.py | UNIQUE(eval_date,item_cd) → UNIQUE(store_id,eval_date,item_cd) + migration |

## 7. Lessons Learned

1. **daily_avg 계산 시 분모 주의**: `COUNT(DISTINCT date)` vs `전체 기간` — 저회전 상품에서 극적 차이 발생
2. **유령재고 정리의 부작용**: TTL 기반 정리가 실재고를 삭제할 수 있음 → 배치 데이터로 교차 검증 필요
3. **모듈 간 daily_avg 불일치 감지**: WMA(0.00) vs snack(3.50) 괴리가 조기 경고 신호였으나 알림 없었음
