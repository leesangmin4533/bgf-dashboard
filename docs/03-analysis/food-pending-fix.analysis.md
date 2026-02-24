# food-pending-fix Analysis Report

> **Analysis Type**: Gap Analysis (PDCA Check Phase)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector agent
> **Date**: 2026-02-24
> **Plan Doc**: `.claude/plans/spicy-prancing-hamming.md`

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the `food-pending-fix` implementation exactly matches the plan document.
The fix addresses the problem where short-expiry food items (expiry <= 1 day) were losing
40% of predicted order quantities during the pending-stock adjustment phase of auto_order.py.

### 1.2 Analysis Scope

- **Plan Document**: `C:\Users\kanur\.claude\plans\spicy-prancing-hamming.md`
- **Implementation Files**:
  - `bgf_auto/src/settings/constants.py`
  - `bgf_auto/src/order/auto_order.py`
  - `bgf_auto/tests/test_stock_adjustment.py`
- **Analysis Date**: 2026-02-24

---

## 2. Gap Analysis (Plan vs Implementation)

### 2.1 Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| Test Coverage | 100% | PASS |
| **Overall** | **100%** | **PASS** |

### 2.2 Check Item Detail

#### Step 1: `src/settings/constants.py` -- Constant Addition

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 1 | FOOD_SHORT_EXPIRY_PENDING_DISCOUNT exists | 0.5 | 0.5 (line 139) | MATCH |
| 2 | Location after PROMO_MIN_STOCK_UNITS | line 134+ | line 136 (after INVENTORY_CLEANUP_HOURS on 134) | MATCH |
| 3 | Comment/docstring present | Required | 3-line comment block (lines 136-138) | MATCH |

**Evidence** (`constants.py` lines 136-139):
```python
# 단기유통 푸드류 미입고 할인율 (유통기한 1일 이하)
# 미입고 수량의 50%만 내일 발주 차감에 반영
# 근거: 유통기한 1일 미입고 = 오늘 배송분 → 오늘 소진 예상 → 내일 재고에 미반영
FOOD_SHORT_EXPIRY_PENDING_DISCOUNT = 0.5
```

Score: 3/3 (100%)

---

#### Step 2: `src/order/auto_order.py` -- Import Changes (A)

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 4 | FOOD_CATEGORIES imported | Required | line 34 | MATCH |
| 5 | FOOD_SHORT_EXPIRY_PENDING_DISCOUNT imported | Required | line 35 | MATCH |
| 6 | CATEGORY_EXPIRY_DAYS imported | Required | line 36 | MATCH |

**Evidence** (`auto_order.py` lines 27-37):
```python
from src.settings.constants import (
    DEFAULT_ORDERABLE_DAYS,
    LOG_SEPARATOR_NORMAL, LOG_SEPARATOR_WIDE, LOG_SEPARATOR_EXTRA,
    LOG_SEPARATOR_FULL,
    PASS_MAX_ORDER_QTY, ENABLE_PASS_SUPPRESSION,
    DEFAULT_STORE_ID,
    FORCE_MAX_DAYS,
    FOOD_CATEGORIES,
    FOOD_SHORT_EXPIRY_PENDING_DISCOUNT,
    CATEGORY_EXPIRY_DAYS,
)
```

Score: 3/3 (100%)

---

#### Step 3: `src/order/auto_order.py` -- int to round Fix (B)

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 7 | `int(result.adjusted_qty)` changed to `round(result.adjusted_qty, 2)` | Required | line 718 | MATCH |

**Evidence** (`auto_order.py` line 718):
```python
"predicted_sales": round(result.adjusted_qty, 2),
```

Score: 1/1 (100%)

---

#### Step 4: `src/order/auto_order.py` -- expiration_days in dict (C)

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 8 | `expiration_days` key added to order dict | Required | line 732 | MATCH |
| 9 | Uses `_get_expiration_days_for_item()` helper | Required | line 732 | MATCH |

**Evidence** (`auto_order.py` line 732):
```python
"expiration_days": self._get_expiration_days_for_item(result, product_detail),
```

Score: 2/2 (100%)

---

#### Step 5: `src/order/auto_order.py` -- `_get_expiration_days_for_item()` Helper (D)

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 10 | Method exists | Required | lines 735-762 | MATCH |
| 11 | Fallback 1: PredictionResult.food_expiration_days | Required | lines 745-747 | MATCH |
| 12 | Fallback 2: product_detail.expiration_days | Required | lines 750-753 | MATCH |
| 13 | Fallback 3: CATEGORY_EXPIRY_DAYS[mid_cd] | Required | lines 757-759 | MATCH |
| 14 | Fallback 4: 365 (default) | Required | line 762 | MATCH |

**Evidence** (`auto_order.py` lines 735-762):
```python
def _get_expiration_days_for_item(self, result, product_detail: Optional[Dict]) -> int:
    """상품의 유통기한 일수 조회 (3단계 폴백)"""
    # 1. PredictionResult에서 조회
    food_exp = getattr(result, 'food_expiration_days', None)
    if food_exp and food_exp > 0:
        return food_exp
    # 2. product_detail에서 조회
    if product_detail:
        pd_exp = product_detail.get('expiration_days')
        if pd_exp and pd_exp > 0:
            return pd_exp
    # 3. 카테고리 기본값
    mid_cd = getattr(result, 'mid_cd', '')
    cat_exp = CATEGORY_EXPIRY_DAYS.get(mid_cd)
    if cat_exp and cat_exp > 0:
        return cat_exp
    # 4. 기본값
    return 365
```

Note: The plan says "3-level fallback" but the implementation has 4 levels (the 4th being `return 365`).
This is consistent with the plan since the plan's text states "3단계 폴백: PredictionResult.food_expiration_days -> product_detail.expiration_days -> CATEGORY_EXPIRY_DAYS[mid_cd] -> 365", listing all 4 levels despite saying "3".

Score: 5/5 (100%)

---

#### Step 6: `src/order/auto_order.py` -- `_recalculate_need_qty()` Signature Extension & Discount Logic (E)

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 15 | `expiration_days: Optional[int] = None` param added | Required | line 1331 | MATCH |
| 16 | `mid_cd: str = ""` param added | Required | line 1332 | MATCH |
| 17 | `effective_pending = new_pending` initialized | Required | line 1359 | MATCH |
| 18 | Condition: `expiration_days <= 1 and mid_cd in FOOD_CATEGORIES and new_pending > 0` | Required | lines 1360-1362 | MATCH |
| 19 | `effective_pending = max(0, int(new_pending * FOOD_SHORT_EXPIRY_PENDING_DISCOUNT))` | Required | line 1363 | MATCH |
| 20 | `need = predicted_sales + safety_stock - new_stock - effective_pending` | Required | line 1371 | MATCH |
| 21 | Debug log for discount applied | Expected | lines 1365-1368 | MATCH |
| 22 | Docstring updated with new params | Expected | lines 1340-1352 | MATCH |

**Evidence** (`auto_order.py` lines 1357-1371):
```python
effective_pending = new_pending
if (expiration_days is not None and expiration_days <= 1
        and mid_cd in FOOD_CATEGORIES
        and new_pending > 0):
    effective_pending = max(0, int(new_pending * FOOD_SHORT_EXPIRY_PENDING_DISCOUNT))
    if effective_pending != new_pending:
        logger.debug(
            f"[단기유통할인] mid_cd={mid_cd}, 유통기한={expiration_days}일: "
            f"미입고 {new_pending} → {effective_pending} "
            f"(할인율={FOOD_SHORT_EXPIRY_PENDING_DISCOUNT})"
        )

need = predicted_sales + safety_stock - new_stock - effective_pending
```

Score: 8/8 (100%)

---

#### Step 7: `src/order/auto_order.py` -- `_apply_pending_and_stock_to_order_list()` Modifications (F)

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 23 | Reads `expiration_days` from item dict | Required | line 1473 | MATCH |
| 24 | Reads `mid_cd` from item dict | Required | line 1474 | MATCH |
| 25 | Passes `expiration_days` to `_recalculate_need_qty()` | Required | line 1484 | MATCH |
| 26 | Passes `mid_cd` to `_recalculate_need_qty()` | Required | line 1485 | MATCH |
| 27 | Short-expiry protection condition: `expiration_days <= 1 and mid_cd in FOOD_CATEGORIES and original_qty > 0 and stock_changed and new_pending > original_pending` | Required | lines 1518-1522 | MATCH |
| 28 | Protection action: `new_qty = original_qty` | Required | line 1524 | MATCH |
| 29 | Protection log: `[단기유통보호]` | Required | line 1526 | MATCH |

**Evidence** (`auto_order.py` lines 1473-1529):
```python
expiration_days = item.get('expiration_days')
mid_cd = item.get('mid_cd', '')

new_qty = self._recalculate_need_qty(
    ...
    expiration_days=expiration_days,
    mid_cd=mid_cd
)
...
if new_qty < min_order_qty:
    item_exp_days = adjusted_item.get('expiration_days')
    item_mid_cd = adjusted_item.get('mid_cd', '')
    if (item_exp_days is not None and item_exp_days <= 1
            and item_mid_cd in FOOD_CATEGORIES
            and original_qty > 0
            and stock_changed
            and new_pending > original_pending):
        new_qty = original_qty
        logger.info(
            f"[단기유통보호] {item_name[:20]}: "
            ...
        )
```

Score: 7/7 (100%)

---

#### Step 8: `tests/test_stock_adjustment.py` -- Test Coverage

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 30 | TestShortExpiryFoodPendingDiscount class exists | Required | line 484 | MATCH |
| 31 | test_pending_discount_preserves_order | Required | line 528 | MATCH |
| 32 | test_no_discount_for_2day_food | Required | line 544 | MATCH |
| 33 | test_no_discount_for_non_food | Required | line 554 | MATCH |
| 34 | test_no_discount_when_no_pending | Required | line 564 | MATCH |
| 35 | test_backward_compat_no_expiry_param | Required | line 574 | MATCH |
| 36 | test_pending_discount_2units | Required | line 584 | MATCH |
| 37 | test_short_expiry_protection_keeps_order | Required | line 601 | MATCH |
| 38 | test_non_food_pending_cancels_normally | Required | line 627 | MATCH |
| 39 | test_stock_increase_not_protected | Required | line 649 | MATCH |
| 40 | test_protection_only_when_pending_increased | Required | line 670 | MATCH |
| 41 | TestIntCastingFix class exists | Required | line 696 | MATCH |
| 42 | test_predicted_sales_preserves_float | Required | line 734 | MATCH |
| 43 | test_predicted_sales_integer_unchanged | Required | line 741 | MATCH |
| 44 | test_expiration_days_in_dict | Required | line 748 | MATCH |
| 45 | test_expiration_days_fallback (product_detail) | Required | line 759 | MATCH |

**Additive Enhancement (not in plan):**

| # | Check Item | Description | Status |
|---|-----------|-------------|--------|
| A1 | test_expiration_days_fallback_to_category | Additional test: CATEGORY_EXPIRY_DAYS fallback (line 770) | ADDITIVE |

The plan specified 14 tests (10 pending discount + 4 int casting fix = tests 1-14 in plan table).
Implementation has 15 tests (10 + 5). The extra test `test_expiration_days_fallback_to_category`
is an additive enhancement covering the 3rd fallback level in `_get_expiration_days_for_item()`.

Score: 16/16 (100%) -- all planned tests present, 1 bonus test

---

#### Step 9: Backward Compatibility

| # | Check Item | Plan | Implementation | Status |
|---|-----------|------|----------------|--------|
| 46 | `expiration_days=None` default preserves existing behavior | Required | line 1331 default=None | MATCH |
| 47 | `mid_cd=""` default preserves existing behavior | Required | line 1332 default="" | MATCH |
| 48 | Existing tests unaffected (pre-existing 28 tests in file) | Required | Classes TestRecalculateNeedQty (10), TestApplyPendingAndStockV11 (13), TestRecalculateNeedQtyEdgeCases (5) unchanged | MATCH |

Score: 3/3 (100%)

---

## 3. Summary

### 3.1 Check Item Totals

```
+---------------------------------------------------+
|  Overall Match Rate: 100%                          |
+---------------------------------------------------+
|  Total Check Items:    48                          |
|  MATCH:                48 items (100%)             |
|  MISSING:               0 items (0%)              |
|  CHANGED:               0 items (0%)              |
+---------------------------------------------------+
|  Additive Enhancements: 1                          |
|  (test_expiration_days_fallback_to_category)       |
+---------------------------------------------------+
```

### 3.2 File-Level Summary

| File | Planned Changes | Implemented | Match |
|------|:--------------:|:-----------:|:-----:|
| `src/settings/constants.py` | 1 constant | 1 constant + 3-line comment | 100% |
| `src/order/auto_order.py` | 5 changes (A-F) | 5 changes exactly | 100% |
| `tests/test_stock_adjustment.py` | 14 tests (10+4) | 15 tests (10+5) | 100% + 1 bonus |

### 3.3 Test Summary

| Class | Plan Count | Actual Count | Status |
|-------|:----------:|:------------:|:------:|
| TestShortExpiryFoodPendingDiscount | 10 | 10 | MATCH |
| TestIntCastingFix | 4 | 5 | MATCH + 1 bonus |
| **Total New Tests** | **14** | **15** | **107%** |

Pre-existing tests in file: 28 (unchanged)
Total tests in test_stock_adjustment.py: 43

### 3.4 Architecture Compliance

| Check | Status |
|-------|--------|
| Constants in `src/settings/constants.py` (Settings layer) | PASS |
| Business logic in `src/order/auto_order.py` (Application/Infrastructure layer) | PASS |
| No new cross-layer dependency violations | PASS |
| Import order: external -> internal -> relative | PASS |
| Naming: UPPER_SNAKE for constants, snake_case for methods | PASS |
| Docstrings present on new methods | PASS |
| Logger usage (no print) | PASS |

---

## 4. Differences Found

### Missing Features (Plan O, Implementation X)

None.

### Added Features (Plan X, Implementation O)

| Item | Implementation Location | Description | Impact |
|------|------------------------|-------------|--------|
| test_expiration_days_fallback_to_category | test_stock_adjustment.py:770 | Tests the 3rd fallback path (CATEGORY_EXPIRY_DAYS) | LOW (positive) |

### Changed Features (Plan != Implementation)

None.

---

## 5. Recommended Actions

No actions required. All planned changes are implemented exactly as specified.

### Documentation Update Needed

- [x] Plan document is self-contained; no design doc to update.
- [ ] Consider updating `MEMORY.md` with food-pending-fix completion record.

---

## 6. Verdict

```
+---------------------------------------------------+
|                                                     |
|  Match Rate: 100%    --    PASS                    |
|                                                     |
|  All 48 check items verified.                      |
|  Zero gaps. 1 additive enhancement.                |
|  15 new tests (10 pending discount + 5 int cast).  |
|                                                     |
+---------------------------------------------------+
```

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-24 | Initial gap analysis | gap-detector |
