# promo-min-order Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-02-23
> **Status**: PASS

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the "promo min order" feature (promotion-based minimum order quantity correction) is implemented exactly as specified in the design. This feature ensures that when a promotion like 1+1 or 2+1 is active, the order quantity is never less than the promotion unit (2 or 3 respectively), so that customers can actually use the promotion.

### 1.2 Analysis Scope

- **Design Specification**: 5 change points as described in the PDCA request
- **Implementation Files**:
  - `bgf_auto/src/settings/constants.py` (PROMO_MIN_STOCK_UNITS constant)
  - `bgf_auto/src/prediction/improved_predictor.py` (lines 1840-1864)
  - `bgf_auto/src/order/auto_order.py` (_recalculate_need_qty, _convert_prediction_result_to_dict, _apply_pending_and_stock_to_order_list)
  - `bgf_auto/tests/test_promo_min_order.py` (22 tests)
- **Analysis Date**: 2026-02-23

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Change 1: improved_predictor.py `_apply_promotion_adjustment()`

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Add `order_qty < promo_unit` check before existing expected_stock check | Lines 1840-1852: `if promo_status.current_promo and order_qty > 0` block with `if order_qty < promo_unit` sub-check placed BEFORE the existing expected_stock block (lines 1854-1864) | MATCH |
| Only correct when `order_qty > 0` (leave 0 alone) | Line 1841: `and order_qty > 0` guard | MATCH |
| Use PROMO_MIN_STOCK_UNITS constant | Line 1842: `promo_unit = PROMO_MIN_STOCK_UNITS.get(promo_status.current_promo, 1)` | MATCH |
| PROMO_MIN_STOCK_UNITS maps "1+1"->2, "2+1"->3 | constants.py lines 130-133: `{"1+1": 2, "2+1": 3}` | MATCH |
| Import PROMO_MIN_STOCK_UNITS at module level | Line 25: `PROMO_MIN_STOCK_UNITS,` in import block from `src.settings.constants` | MATCH |
| Existing expected_stock logic preserved | Lines 1854-1864: unchanged `expected_stock = current_stock + pending_qty + order_qty` check still follows | MATCH |

**Verdict**: 6/6 items match. PASS.

### 2.2 Change 2: auto_order.py `_recalculate_need_qty()`

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Add `promo_type: str = ""` parameter | Line 1190: `promo_type: str = ""` | MATCH |
| Apply promo correction after order_unit_qty rounding | Lines 1233-1238: promo block placed after line 1230-1231 (order_unit_qty rounding) | MATCH |
| Only apply when `promo_type` is truthy and `order_qty > 0` | Line 1234: `if promo_type and order_qty > 0:` | MATCH |
| Import PROMO_MIN_STOCK_UNITS | Line 1235: lazy import `from src.settings.constants import PROMO_MIN_STOCK_UNITS` | MATCH |
| Round up to promo_unit when `order_qty < promo_unit` | Lines 1237-1238: `if order_qty < promo_unit: order_qty = promo_unit` | MATCH |
| Docstring updated to document promo_type parameter | Lines 1205, 1208: promo_type documented in Args and Returns | MATCH |

**Verdict**: 6/6 items match. PASS.

### 2.3 Change 3: auto_order.py `_convert_prediction_result_to_dict()`

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Read `promo_type` from product_detail | Line 650: `promo_type = product_detail.get("promo_type") or ""` | MATCH |
| Default to empty string if product_detail is None | Line 645: `promo_type = ""` initialized before the `if product_detail:` guard | MATCH |
| Default to empty string if promo_type key missing | Line 650: `or ""` fallback handles None/missing | MATCH |
| Include promo_type in returned dict | Line 658: `"promo_type": promo_type,` in the return dict | MATCH |

**Verdict**: 4/4 items match. PASS.

### 2.4 Change 4: auto_order.py `_apply_pending_and_stock_to_order_list()`

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Extract promo_type from item dict | Line 1310: `promo_type = item.get('promo_type', '')` | MATCH |
| Pass promo_type to _recalculate_need_qty() | Line 1319: `promo_type=promo_type` in the call | MATCH |

**Verdict**: 2/2 items match. PASS.

### 2.5 Change 5: tests/test_promo_min_order.py

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| 22 total tests | 22 test functions across 3 classes | MATCH |
| Predictor tests: 10 | TestPromoAdjustmentPredictor: 10 tests (test_1plus1_order1_becomes_2 through test_expected_stock_fallback_still_works) | MATCH |
| Recalculate tests: 8 | TestRecalculateNeedQtyPromo: 8 tests (test_1plus1_need_1_becomes_2 through test_backward_compatible_without_promo_type) | MATCH |
| Dict tests: 4 | TestRecommendationPromoType: 4 tests (test_promo_type_in_dict through test_promo_type_2plus1) | MATCH |

**Test Coverage Analysis**:

| Scenario | Covered By |
|----------|------------|
| 1+1 order=1 -> 2 | Predictor #1, Recalculate #1 |
| 2+1 order=1 -> 3 | Predictor #2, Recalculate #2 |
| 2+1 order=2 -> 3 | Predictor #3 |
| 1+1 order=0 -> 0 (no force) | Predictor #4, Predictor #10, Recalculate #3 |
| No promo -> unchanged | Predictor #5, Recalculate #4 |
| Already above unit -> unchanged | Predictor #6, Predictor #7, Recalculate #5 |
| Stock present but order < unit -> corrected | Predictor #8, Predictor #9 |
| Order unit qty interaction | Recalculate #6, Recalculate #7 |
| Backward compatibility (no promo_type param) | Recalculate #8 |
| Dict includes promo_type from product_detail | Dict #1, Dict #4 |
| Dict defaults to "" when missing | Dict #2, Dict #3 |

**Verdict**: 4/4 test structure items match. All key scenarios covered. PASS.

---

## 3. Architecture Compliance

| Criterion | Status | Notes |
|-----------|--------|-------|
| Constant in Settings layer (constants.py) | PASS | PROMO_MIN_STOCK_UNITS at line 130 |
| Prediction logic in prediction/ layer | PASS | improved_predictor.py is in src/prediction/ |
| Order logic in order/ layer | PASS | auto_order.py is in src/order/ |
| No cross-layer violations | PASS | auto_order lazy-imports from settings (acceptable) |
| Tests isolated with mocks | PASS | No DB/network access; all dependencies mocked |

---

## 4. Convention Compliance

| Criterion | Status | Notes |
|-----------|--------|-------|
| Constant naming: UPPER_SNAKE_CASE | PASS | `PROMO_MIN_STOCK_UNITS` |
| Function naming: snake_case | PASS | `_recalculate_need_qty`, `_apply_promotion_adjustment` |
| Parameter naming: snake_case | PASS | `promo_type`, `order_qty`, `promo_unit` |
| Korean log messages | PASS | All logger.info messages use Korean |
| Docstring present | PASS | _recalculate_need_qty has full docstring with Args/Returns |
| No magic numbers | PASS | All numeric values come from PROMO_MIN_STOCK_UNITS dict |
| No silent pass/return | PASS | Exception in _apply_promotion_adjustment logged with logger.warning |

---

## 5. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 100%                    |
+---------------------------------------------+
|  Change 1 (predictor):        6/6  = 100%   |
|  Change 2 (recalculate):      6/6  = 100%   |
|  Change 3 (dict conversion):  4/4  = 100%   |
|  Change 4 (pending/stock):    2/2  = 100%   |
|  Change 5 (tests):            4/4  = 100%   |
+---------------------------------------------+
|  Total Check Items:          22/22           |
|  Architecture Compliance:    5/5   = 100%    |
|  Convention Compliance:      7/7   = 100%    |
+---------------------------------------------+
```

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## 6. Differences Found

### Missing Features (Design O, Implementation X)

None.

### Added Features (Design X, Implementation O)

None.

### Changed Features (Design != Implementation)

None.

---

## 7. Logic Correctness Verification

Beyond matching design-to-implementation, the following logic correctness checks were performed:

| Check | Result | Detail |
|-------|--------|--------|
| Order of operations in predictor | CORRECT | (1) promo unit correction runs first, (2) expected_stock correction runs second. This prevents the old bug where stock+pending masked low order_qty |
| Order of operations in recalculate | CORRECT | (1) need calculation, (2) order_unit_qty rounding, (3) promo_unit correction. Promo runs last so it never goes below the promotion minimum |
| Zero-order guard consistency | CORRECT | Both predictor (line 1841) and recalculate (line 1234) check `order_qty > 0` before promo correction |
| Backward compatibility | CORRECT | `promo_type: str = ""` default parameter means existing callers without promo_type work unchanged |
| Dict pipeline integrity | CORRECT | promo_type flows: product_detail -> _convert_prediction_result_to_dict -> item dict -> _apply_pending_and_stock_to_order_list -> _recalculate_need_qty |

---

## 8. Recommended Actions

No actions required. All 5 design changes are implemented exactly as specified, all 22 tests pass (as part of 1633 total), and the feature correctly handles:

- 1+1 promotions: minimum order of 2
- 2+1 promotions: minimum order of 3
- Zero orders: no forced promotion ordering
- Integration with both prediction pipeline and inventory-adjustment pipeline

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-23 | Initial gap analysis | gap-detector |
