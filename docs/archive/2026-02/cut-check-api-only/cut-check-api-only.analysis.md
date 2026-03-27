# cut-check-api-only Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector agent
> **Date**: 2026-02-28
> **Design Doc**: [cut-check-api-only.design.md](../02-design/features/cut-check-api-only.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the `cut-check-api-only` feature implementation matches the design document. This feature prevents unnecessary Selenium fallback (~2.5 min) for CUT/untreated items by recognizing HTTP 200 + 0 rows as a valid "item unavailable" response instead of treating it as a failure.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/cut-check-api-only.design.md`
- **Implementation Files**:
  - `src/collectors/direct_api_fetcher.py` (lines 103-181, `extract_item_data()`)
  - `src/collectors/order_prep_collector.py` (lines 912-950, `_process_api_result()`; lines 1193-1295, `_collect_via_direct_api()`)
- **Test File**: `tests/test_cut_check_api_only.py` (15 tests)
- **Analysis Date**: 2026-02-28

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Change Target #1: `extract_item_data()` in `direct_api_fetcher.py`

| Design Specification | Implementation | Status |
|---------------------|----------------|--------|
| `result` dict includes `is_empty_response: False` as default | Line 123: `'is_empty_response': False` | MATCH |
| `elif ds_item and not ds_item['rows']` branch added | Lines 141-145: exact branch added | MATCH |
| Sets `result['success'] = True` in empty branch | Line 143: `result['success'] = True` | MATCH |
| Sets `result['is_empty_response'] = True` in empty branch | Line 144: `result['is_empty_response'] = True` | MATCH |
| Sets `result['is_cut_item'] = True` in empty branch | Line 145: `result['is_cut_item'] = True` | MATCH |
| gdList rows present -> overwrite `is_cut_item` with `CUT_ITEM_YN` value | Line 168: `result['is_cut_item'] = last_row.get('CUT_ITEM_YN', '0') == '1'` | MATCH |
| gdList parsing preserved (not removed) | Lines 162-170: unchanged gdList block | MATCH |

**Result: 7/7 items match (100%)**

### 2.2 Change Target #2: `_process_api_result()` in `order_prep_collector.py`

| Design Specification | Implementation | Status |
|---------------------|----------------|--------|
| Check `is_empty_response` flag for early return | Line 932: `if api_data.get('is_empty_response'):` | MATCH |
| Log message: `[DirectAPI] {item_cd}: unavailable (empty response)` | Line 933: `logger.info(f"[DirectAPI] {item_cd}: 발주불가 (빈 응답 -> CUT/미취급)")` | MATCH |
| Return dict with `success: True` | Line 949: `'success': True` | MATCH |
| Return dict with `is_cut_item: True` | Line 943: `'is_cut_item': True` | MATCH |
| Return dict with `is_empty_response: True` | Line 944: `'is_empty_response': True` | MATCH |
| Return dict with `pending_qty: 0` | Line 939: `'pending_qty': 0` | MATCH |
| Return dict with `current_stock: 0` | Line 938: `'current_stock': 0` | MATCH |
| Early return skips pending calculation | Verified: early return before line 952 pending calc | MATCH |

**Additional fields in implementation not in design**: `item_nm`, `order_unit_qty`, `expiration_days`, `current_month_promo`, `next_month_promo`, `sell_price`, `margin_rate`, `history`, `week_dates` -- these are default/empty values added for consistency with the normal return format. This is a beneficial addition that does not conflict with design intent.

**Result: 8/8 items match (100%)**

### 2.3 Change Target #3: `_collect_via_direct_api()` in `order_prep_collector.py`

| Design Specification | Implementation | Status |
|---------------------|----------------|--------|
| Selenium fallback condition only triggers on real failures (not empty responses) | Line 1273: `failed = [ic for ic in remaining_codes if not results.get(ic, {}).get('success')]` -- CUT items have `success=True`, so they are excluded from fallback list | MATCH |
| Log addition for empty response count | Lines 1292-1294: counts `is_empty_response` items and logs in completion summary | MATCH |

**Result: 2/2 items match (100%)**

### 2.4 Non-Change Verification

| Design: "Do NOT change" | Actual | Status |
|--------------------------|--------|--------|
| `auto_order.py` prefetch logic | No `is_empty_response` references in `auto_order.py` | MATCH |
| `order_executor.py` | No changes to `order_executor.py` | MATCH |
| Selenium fallback logic (not removed) | Lines 1274-1290: Selenium fallback preserved for real failures | MATCH |

**Result: 3/3 items match (100%)**

### 2.5 Error Handling Matrix (Design Section 5)

| Scenario | Design Expectation | Implementation | Status |
|----------|-------------------|----------------|--------|
| HTTP 200 + 0 rows | `success=True, is_cut_item=True` | Lines 141-145: exact match | MATCH |
| HTTP 200 + 1 row + CUT_ITEM_YN=1 | `success=True, is_cut_item=True` | Line 168: gdList CUT_ITEM_YN check | MATCH |
| HTTP 200 + 1 row + CUT_ITEM_YN=0 | `success=True, is_cut_item=False` | Line 168: `'0' == '1'` is False | MATCH |
| HTTP error (4xx/5xx) | `success=False` -> Selenium fallback | JS fetch returns `ok: false` -> not in batch_data -> `success: False` | MATCH |
| Network timeout | `success=False` -> Selenium fallback | JS catch block -> `ok: false` | MATCH |
| SSV parse failure (no dsItem) | `success=False` -> Selenium fallback | Lines 132-145: neither `if` nor `elif` fires -> `success` stays False | MATCH |

**Result: 6/6 items match (100%)**

### 2.6 Data Flow (Design Section 3)

| Flow Step | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| `fetch_items_batch()` -> `parse_full_ssv_response()` -> `extract_item_data()` | Correctly chains | Lines 530-531 in `direct_api_fetcher.py` | MATCH |
| Empty response -> `success=True, is_cut=True` returned to batch results | `item_data.get('success')` is True -> included in results dict | Line 532-533: correctly included | MATCH |
| `_collect_via_direct_api` -> `_process_api_result` -> early return for empty | Lines 1264-1265 -> Lines 928-950 | MATCH |
| `failed` list excludes CUT items | `not results.get(ic, {}).get('success')` | Line 1273 | MATCH |
| Caller (`auto_order`) uses `is_cut_item` to add to `_cut_items` | Design says no change needed (already handled) | Confirmed: no changes to `auto_order.py` | MATCH |

**Result: 5/5 items match (100%)**

---

## 3. Test Analysis

### 3.1 Design Test Plan vs Actual Tests

| # | Design Test | Actual Test(s) | Status |
|---|------------|-----------------|--------|
| 1 | `test_extract_empty_rows_returns_cut` | `TestExtractEmptyRowsCut::test_extract_empty_rows_returns_cut` (line 95) + `test_extract_empty_rows_no_selenium_trigger` (line 115) | MATCH (2 tests for 1 design test) |
| 2 | `test_extract_normal_item` | `TestExtractNormalItem::test_extract_normal_item` (line 134) | MATCH |
| 3 | `test_extract_actual_cut_item` | `TestExtractActualCutItem::test_extract_actual_cut_item` (line 158) + `test_cut_item_yn_overrides_empty_default` (line 172) | MATCH (2 tests for 1 design test) |
| 4 | `test_extract_no_dsitem` | `TestExtractNoDsitem::test_extract_no_dsitem` (line 194) + `test_extract_no_dsitem_only_gdlist` (line 202) | MATCH (2 tests for 1 design test) |
| 5 | `test_process_api_result_empty_response` | `TestProcessApiResultEmptyResponse::test_process_api_result_empty_response` (line 228) + `test_empty_response_skips_pending_calc` (line 263) | MATCH (2 tests for 1 design test) |
| 6 | `test_process_api_result_normal` | `TestProcessApiResultNormal::test_process_api_result_normal` (line 297) | MATCH |
| 7 | `test_selenium_fallback_only_on_real_failure` | `TestSeleniumFallbackOnlyRealFailure::test_empty_response_not_in_fallback_list` (line 343) + `test_batch_result_empty_vs_failure` (line 367) | MATCH (2 tests for 1 design test) |
| 8 | `test_prefetch_cut_detection_from_empty` | `TestPrefetchCutDetectionFromEmpty::test_cut_detection_end_to_end` (line 393) + `test_mixed_batch_cut_and_normal` (line 413) + `test_process_then_prefetch_integration` (line 452) | MATCH (3 tests for 1 design test) |

**Design planned 8 tests, implementation has 15 tests. All 8 design test scenarios are covered, with 7 additional edge-case tests that strengthen coverage.**

### 3.2 Test Quality Assessment

| Aspect | Assessment |
|--------|------------|
| SSV test data generation | Excellent -- dedicated `make_full_ssv_response()` helper covers all combinations |
| Mock usage | Proper -- patches Repository/PromotionManager for `_process_api_result` tests |
| End-to-end flow | Covered in `TestPrefetchCutDetectionFromEmpty` (3 tests) |
| Edge cases | CUT overwrite by gdList, dsItem-only missing, mixed batch scenarios |
| Assertion completeness | Each test verifies `success`, `is_cut_item`, `is_empty_response`, and related fields |

---

## 4. Architecture Compliance

| Layer | File | Expected Layer | Actual Layer | Status |
|-------|------|---------------|--------------|--------|
| `direct_api_fetcher.py` | Infrastructure (collectors) | `src/collectors/` | MATCH |
| `order_prep_collector.py` | Infrastructure (collectors) | `src/collectors/` | MATCH |
| No domain changes | Domain unchanged | Confirmed | MATCH |
| No application changes | Application unchanged | Confirmed | MATCH |

**Architecture Score: 100%**

---

## 5. Convention Compliance

| Convention | Check | Status |
|-----------|-------|--------|
| Function naming: `snake_case` | `extract_item_data`, `_process_api_result`, `_collect_via_direct_api` | MATCH |
| Constants: `UPPER_SNAKE` | `RS`, `US` separators | MATCH |
| Korean comments/docstrings | All new code has Korean comments | MATCH |
| Logger usage (no print) | `logger.info()` used throughout | MATCH |
| Exception handling | No bare except, no silent pass | MATCH |
| `is_empty_response` field name | Consistent across both files and all tests | MATCH |

**Convention Score: 100%**

---

## 6. Overall Score

```
+---------------------------------------------+
|  Overall Match Rate: 100.0%                  |
+---------------------------------------------+
|  Design Match:            31/31 items (100%) |
|  Architecture Compliance: 100%               |
|  Convention Compliance:   100%               |
|  Test Coverage:           15/8 (188%)        |
+---------------------------------------------+
```

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| Test Coverage | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## 7. Differences Found

### Missing Features (Design O, Implementation X)

None.

### Added Features (Design X, Implementation O)

| # | Item | Implementation Location | Description | Impact |
|---|------|------------------------|-------------|--------|
| 1 | Extra return fields in `_process_api_result` empty branch | `order_prep_collector.py:934-948` | Returns `item_nm`, `order_unit_qty`, `expiration_days`, promo fields, `sell_price`, `margin_rate`, `history`, `week_dates` with default values for format consistency | None (beneficial) |
| 2 | 7 additional test cases | `test_cut_check_api_only.py` | Design planned 8 tests; implementation has 15 -- extra edge cases for gdList CUT override, dsItem-only missing, mixed batch, integration flow | None (beneficial) |

### Changed Features (Design != Implementation)

None.

---

## 8. Detailed Check Items Summary

| # | Category | Check Item | Result |
|---|----------|-----------|--------|
| 1 | extract_item_data | `is_empty_response` default field added | PASS |
| 2 | extract_item_data | `elif ds_item and not ds_item['rows']` branch | PASS |
| 3 | extract_item_data | Empty branch sets `success=True` | PASS |
| 4 | extract_item_data | Empty branch sets `is_empty_response=True` | PASS |
| 5 | extract_item_data | Empty branch sets `is_cut_item=True` | PASS |
| 6 | extract_item_data | gdList CUT_ITEM_YN override preserved | PASS |
| 7 | extract_item_data | Normal item path unchanged | PASS |
| 8 | _process_api_result | `is_empty_response` early return check | PASS |
| 9 | _process_api_result | Log message for empty response | PASS |
| 10 | _process_api_result | Early return dict: success=True | PASS |
| 11 | _process_api_result | Early return dict: is_cut_item=True | PASS |
| 12 | _process_api_result | Early return dict: is_empty_response=True | PASS |
| 13 | _process_api_result | Early return dict: pending_qty=0 | PASS |
| 14 | _process_api_result | Early return dict: current_stock=0 | PASS |
| 15 | _process_api_result | Skips pending calculation for empty | PASS |
| 16 | _collect_via_direct_api | Fallback excludes CUT items (success=True) | PASS |
| 17 | _collect_via_direct_api | Empty count logging added | PASS |
| 18 | Non-change | auto_order.py not modified | PASS |
| 19 | Non-change | order_executor.py not modified | PASS |
| 20 | Non-change | Selenium fallback logic preserved | PASS |
| 21 | Error: HTTP 200 + 0 rows | success=True, is_cut=True | PASS |
| 22 | Error: HTTP 200 + CUT_ITEM_YN=1 | success=True, is_cut=True | PASS |
| 23 | Error: HTTP 200 + CUT_ITEM_YN=0 | success=True, is_cut=False | PASS |
| 24 | Error: HTTP error | success=False, Selenium fallback | PASS |
| 25 | Error: Network timeout | success=False, Selenium fallback | PASS |
| 26 | Error: SSV parse failure | success=False, Selenium fallback | PASS |
| 27 | Flow: batch -> extract -> success in results | PASS |
| 28 | Flow: _process_api_result -> early return for CUT | PASS |
| 29 | Flow: failed list excludes CUT | PASS |
| 30 | Flow: caller uses is_cut_item for exclusion | PASS |
| 31 | Tests: All 8 design test scenarios covered | PASS |

**Total: 31/31 PASS**

---

## 9. Recommended Actions

No actions required. Design and implementation are fully aligned.

### Optional Documentation Enhancement

- Consider adding an inline comment in `_collect_via_direct_api()` at line 1273 noting that CUT items with `success=True` are intentionally excluded from the fallback list (for future maintainer clarity).

---

## 10. Next Steps

- [x] Implementation complete
- [x] All 15 tests passing
- [x] Gap analysis complete (100% match)
- [ ] Write completion report (`cut-check-api-only.report.md`)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-28 | Initial analysis -- 100% match, 31 check items, 15 tests | gap-detector |
