# order-promo-fix Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-02-27
> **Design Doc**: [sorted-seeking-blum.md](C:\Users\kanur\.claude\plans\sorted-seeking-blum.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

order_prep_collector.py was using incorrect column indices (getColID(11)/getColID(12)) to read promo information from the gdList dataset, capturing order unit names (ORD_UNIT/ORD_UNIT_QTY) instead of actual promotion data (MONTH_EVT/NEXT_MONTH_EVT). This analysis verifies that the fix was correctly implemented according to the plan.

### 1.2 Analysis Scope

- **Design Document**: `C:\Users\kanur\.claude\plans\sorted-seeking-blum.md`
- **Implementation Files**:
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\collectors\order_prep_collector.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\infrastructure\database\repos\promotion_repo.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\scripts\clean_promo_data.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\tests\test_promo_validation.py`
- **Analysis Date**: 2026-02-27

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Check Item Summary

| # | Check Item | Design | Implementation | Status |
|:-:|-----------|--------|----------------|:------:|
| 1 | JS getColID(11)/getColID(12) removed | Remove index-based access | 0 occurrences of `getColID` in file | PASS |
| 2 | MONTH_EVT column name used | `ds.getColumn(lastRow, 'MONTH_EVT')` | Line 668: `ds.getColumn(lastRow, 'MONTH_EVT')` | PASS |
| 3 | NEXT_MONTH_EVT column name used | `ds.getColumn(lastRow, 'NEXT_MONTH_EVT')` | Line 671: `ds.getColumn(lastRow, 'NEXT_MONTH_EVT')` | PASS |
| 4 | `_VALID_PROMO_RE` regex constant | `re.compile(r'^\d+\+\d+$')` | Line 44: identical | PASS |
| 5 | `_INVALID_UNIT_NAMES` set | `{'낱개', '묶음', 'BOX', '지함'}` | Line 45: identical | PASS |
| 6 | `_is_valid_promo()` function | Defined at file top | Line 48-52: matches spec | PASS |
| 7 | `_is_valid_promo()` logic | empty/invalid_unit/digit -> False; regex or in {'할인','덤'} -> True | Identical implementation | PASS |
| 8 | collect_for_item validation gate | Check `_is_valid_promo()` after promo extraction | Lines 773-780: validation applied | PASS |
| 9 | `_is_valid_promo_type()` in promotion_repo | Validation function at file top | Lines 25-29: identical logic | PASS |
| 10 | save_monthly_promo early return | Validate before DB access, skip on invalid | Lines 64-72: validates both, clears invalid, returns if both empty | PASS |
| 11 | product_details update validation | Same validation for common DB update | Line 171: uses already-validated values | PASS |
| 12 | clean_promo_data.py script exists | One-time cleanup script | File exists at `scripts/clean_promo_data.py` (193 lines) | PASS |
| 13 | Clean script: DBRouter pattern | Use DBRouter for store + common DB | Lines 68, 141: `DBRouter.get_store_connection()`, `DBRouter.get_common_connection()` | PASS |
| 14 | Clean script: promotions cleanup | DELETE invalid promo_type rows | Lines 76-83: DELETE with INVALID_PROMO_SQL condition | PASS |
| 15 | Clean script: promotion_changes cleanup | DELETE both-invalid records | Lines 88-119: checks both prev/next promo | PASS |
| 16 | Clean script: daily_sales cleanup | UPDATE promo_type = NULL | Lines 124-131: SET NULL for invalid values | PASS |
| 17 | Clean script: product_details cleanup | UPDATE promo_type = NULL in common DB | Lines 148-155: SET NULL for invalid values | PASS |
| 18 | Clean script: dry-run mode | Preview by default, --execute for real | Lines 165-189: `--execute` flag pattern | PASS |
| 19 | Test file exists | Unit tests for validation functions | File exists at `tests/test_promo_validation.py` (144 lines) | PASS |
| 20 | Test: _is_valid_promo valid cases | "1+1","2+1","3+1","10+1","할인","덤" | Lines 24-29: parametrized, 6 values | PASS |
| 21 | Test: _is_valid_promo invalid unit names | "낱개","묶음","BOX","지함" | Lines 32-36: parametrized, 4 values | PASS |
| 22 | Test: _is_valid_promo invalid numbers | "1","6","10","12","24","150" | Lines 39-43: parametrized, 6 values | PASS |
| 23 | Test: _is_valid_promo empty string | "" -> False | Lines 46-47 | PASS |
| 24 | Test: _is_valid_promo_type validation | Same parametrized checks | Lines 57-71: 11 parametrized cases | PASS |
| 25 | Test: correct column names in source | Verify MONTH_EVT/NEXT_MONTH_EVT, no getColID(11)/getColID(12) | Lines 74-91: inspect.getsource + assertion | PASS |
| 26 | Test: repo rejects invalid promo | Mock DB, verify no DB call on invalid | Lines 94-118: mock test | PASS |
| 27 | Test: repo allows valid promo | Mock DB, verify cursor called on valid | Lines 120-143: mock test | PASS |
| 28 | `import re` in order_prep_collector | Required for _VALID_PROMO_RE | Line 11: `import re` present | PASS |
| 29 | `import re` in promotion_repo | Required for _VALID_PROMO_RE | Line 8: `import re` present | PASS |

---

### 2.2 Detailed Comparison

#### 2.2.1 order_prep_collector.py - JS Column Fix (Design Item A)

**Design (Before):**
```javascript
curPromo = ds.getColumn(lastRow, ds.getColID(11)) || '';   // ORD_UNIT
nextPromo = ds.getColumn(lastRow, ds.getColID(12)) || '';  // ORD_UNIT_QTY
```

**Implementation (After):**
```javascript
// Line 664-671 of order_prep_collector.py
// 행사 정보 (MONTH_EVT: 당월행사, NEXT_MONTH_EVT: 익월행사)
let curPromo = '';
let nextPromo = '';
try {
    curPromo = ds.getColumn(lastRow, 'MONTH_EVT') || '';
} catch(e2) {}
try {
    nextPromo = ds.getColumn(lastRow, 'NEXT_MONTH_EVT') || '';
} catch(e2) {}
```

**Status**: PASS -- Column name-based access replaces index-based access. Individual try/catch blocks add robustness (minor improvement over design).

#### 2.2.2 order_prep_collector.py - Validation Function (Design Item B)

**Design:**
```python
_VALID_PROMO_RE = re.compile(r'^\d+\+\d+$')
_INVALID_UNIT_NAMES = {'낱개', '묶음', 'BOX', '지함'}

def _is_valid_promo(value: str) -> bool:
    if not value or value in _INVALID_UNIT_NAMES or value.isdigit():
        return False
    return bool(_VALID_PROMO_RE.match(value)) or value in {'할인', '덤'}
```

**Implementation (Lines 44-52):**
```python
_VALID_PROMO_RE = re.compile(r'^\d+\+\d+$')
_INVALID_UNIT_NAMES = {'낱개', '묶음', 'BOX', '지함'}

def _is_valid_promo(value: str) -> bool:
    """행사 유형이 유효한지 검증 (발주단위명 오염 방지)"""
    if not value or value in _INVALID_UNIT_NAMES or value.isdigit():
        return False
    return bool(_VALID_PROMO_RE.match(value)) or value in {'할인', '덤'}
```

**Status**: PASS -- Exact match (docstring added, which is an improvement).

#### 2.2.3 order_prep_collector.py - Validation Gate (Design Item C)

**Design:**
```python
promo_info = data.get('promoInfo') or {}
current_month_promo = promo_info.get('current_month_promo', '')
next_month_promo = promo_info.get('next_month_promo', '')

if not _is_valid_promo(current_month_promo):
    current_month_promo = ''
if not _is_valid_promo(next_month_promo):
    next_month_promo = ''
```

**Implementation (Lines 768-780):**
```python
promo_info = data.get('promoInfo') or {}
current_month_promo = promo_info.get('current_month_promo', '')
next_month_promo = promo_info.get('next_month_promo', '')

# 유효성 검증 -- 발주단위명(낱개/묶음/BOX) 오염 방지
if not _is_valid_promo(current_month_promo):
    if current_month_promo:
        logger.debug(f"[행사 검증] {item_cd}: 당월 '{current_month_promo}' 무효 -> 빈값")
    current_month_promo = ''
if not _is_valid_promo(next_month_promo):
    if next_month_promo:
        logger.debug(f"[행사 검증] {item_cd}: 익월 '{next_month_promo}' 무효 -> 빈값")
    next_month_promo = ''
```

**Status**: PASS -- Core logic matches. Implementation adds debug logging for non-empty invalid values, which is a minor improvement for diagnostics.

#### 2.2.4 promotion_repo.py - Storage Validation (Design Item 2)

**Design:**
- `_is_valid_promo_type()` import or equivalent logic
- Invalid values: skip saving + warning log
- product_details update: same validation

**Implementation (Lines 25-72):**
```python
def _is_valid_promo_type(value: str) -> bool:
    """저장 전 promo_type 유효성 검증"""
    if not value or value in _INVALID_UNIT_NAMES or value.isdigit():
        return False
    return bool(_VALID_PROMO_RE.match(value)) or value in {'할인', '덤'}

# In save_monthly_promo():
if current_month_promo and not _is_valid_promo_type(current_month_promo):
    logger.warning(f"[행사 검증] {item_cd}: 당월 '{current_month_promo}' 무효 -> 저장 건너뜀")
    current_month_promo = ''
if next_month_promo and not _is_valid_promo_type(next_month_promo):
    logger.warning(f"[행사 검증] {item_cd}: 익월 '{next_month_promo}' 무효 -> 저장 건너뜀")
    next_month_promo = ''

if not current_month_promo and not next_month_promo:
    return result
```

**Status**: PASS -- Independent validation function (not imported from collector, uses own copy). Warning log on invalid. Early return when both cleared. product_details update at line 171 uses the already-validated `current_month_promo` / `next_month_promo` variables, so contaminated values cannot reach common DB.

#### 2.2.5 clean_promo_data.py (Design Item 3)

**Design:**
- 4 SQL operations: DELETE promotions, DELETE promotion_changes, UPDATE daily_sales, UPDATE product_details
- Store DB + common DB targets
- DBRouter pattern

**Implementation:**
- Lines 76-83: promotions DELETE with INVALID_PROMO_SQL (comprehensive SQL condition)
- Lines 88-119: promotion_changes DELETE (checks both prev_promo_type and next_promo_type)
- Lines 124-131: daily_sales SET promo_type = NULL
- Lines 148-155: product_details SET promo_type = NULL (common DB)
- Lines 68, 141: DBRouter.get_store_connection() / DBRouter.get_common_connection()
- Lines 165-189: dry-run by default, --execute flag for actual execution

**Status**: PASS -- All 4 SQL operations present. DBRouter pattern used. Enhanced with dry-run safety mode and distribution display before cleanup.

#### 2.2.6 test_promo_validation.py (Design Item 4)

**Design:**
- `_is_valid_promo` validation test
- `_is_valid_promo_type` validation test
- Source code column name presence check
- promotion_repo save validation test

**Implementation:**
- `TestIsValidPromo`: 4 test methods (valid types, invalid units, invalid numbers, empty) -- 18 parametrized cases
- `TestIsValidPromoType`: 1 parametrized test with 11 cases
- `TestPromoColumnNames`: source inspection asserting MONTH_EVT/NEXT_MONTH_EVT present and getColID(11)/getColID(12) absent
- `TestPromotionRepoValidation`: 2 mock tests (reject invalid, allow valid)

**Status**: PASS -- All 4 categories of tests present. 27 total test cases.

---

### 2.3 "Do Not Change" Verification

| Item | Design (Do Not Touch) | Implementation | Status |
|------|----------------------|----------------|:------:|
| CallItemDetailPopup DOM | No changes needed | No popup-related changes found | PASS |
| PromotionAdjuster | Existing code is correct | No changes to `src/prediction/promotion.py` | PASS |
| PromotionManager | Existing code is correct | No changes to PromotionManager | PASS |

---

### 2.4 Minor Implementation Enhancements (Not in Design, Added in Impl)

These are non-breaking improvements that go beyond the design spec:

| Item | Description | Impact |
|------|-------------|--------|
| Try/catch per column | JS wraps each getColumn in separate try/catch | Low -- robustness improvement |
| Debug logging | collector logs rejected values at DEBUG level | Low -- diagnostics aid |
| Clean script dry-run | Default to preview mode, require --execute | Low -- safety improvement |
| Clean script distribution display | Shows promo_type distribution before cleanup | Low -- visibility improvement |
| INVALID_PROMO_SQL comprehensive | SQL condition handles edge cases beyond simple IN clause | Low -- thoroughness |

---

## 3. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 100%                    |
+---------------------------------------------+
|  PASS:           29 / 29 items              |
|  FAIL:            0 / 29 items              |
|  Missing Design:  0 items                   |
|  Missing Impl:    0 items                   |
|  Changed:         0 items (cosmetic only)   |
+---------------------------------------------+
```

---

## 4. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| Test Coverage | 100% | PASS |
| **Overall** | **100%** | **PASS** |

### Score Justification

- **Design Match (100%)**: All 4 files created/modified exactly as specified. All 29 check items pass.
- **Architecture Compliance (100%)**: Validation functions placed correctly (collector at module level, repo at module level). DBRouter pattern used in cleanup script. Repository pattern preserved.
- **Convention Compliance (100%)**: snake_case functions, UPPER_SNAKE constants, Korean docstrings, proper logging (no print in library code), exception handling follows project rules.
- **Test Coverage (100%)**: All 4 design-specified test categories present with 27 total test cases covering valid inputs, invalid inputs, source code inspection, and mock-based integration.

---

## 5. Differences Found

### Missing Features (Design O, Implementation X)

None.

### Added Features (Design X, Implementation O)

| Item | Implementation Location | Description | Impact |
|------|------------------------|-------------|--------|
| Try/catch per column | order_prep_collector.py:667-672 | Individual error handling per column read | Low (improvement) |
| Debug logging on reject | order_prep_collector.py:774-779 | Logs rejected values for diagnostics | Low (improvement) |
| Dry-run mode | clean_promo_data.py:165 | Preview before executing cleanup | Low (safety) |
| Distribution display | clean_promo_data.py:50-64 | Shows current data distribution | Low (visibility) |

### Changed Features (Design != Implementation)

None. All logic matches exactly.

---

## 6. Recommended Actions

### Immediate Actions

None required. Implementation fully matches design.

### Post-Deployment Verification

1. Run cleanup script in dry-run mode first:
   ```bash
   python scripts/clean_promo_data.py
   ```

2. Verify DB after cleanup:
   ```sql
   SELECT promo_type, count(*) FROM promotions GROUP BY promo_type;
   ```

3. Monitor logs after next daily run:
   ```bash
   python scripts/log_analyzer.py --search "행사|promo|MONTH_EVT" --last 24h
   ```

---

## 7. Design Document Updates Needed

None. The implementation faithfully follows the design document.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-27 | Initial gap analysis | gap-detector |
