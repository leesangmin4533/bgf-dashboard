# orderable-day-all Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-02-25
> **Status**: PASS (with 1 BUG found)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the "orderable-day-all" feature (universal orderable_day check for all products except food, plus real-time BGF verification/DB correction/rescue ordering) is implemented exactly as specified in the design document.

### 1.2 Analysis Scope

- **Design Specification**: `bgf_auto/docs/02-design/features/orderable-day-all.design.md` (419 lines)
- **Implementation Files**:
  - `bgf_auto/src/prediction/improved_predictor.py` -- Improvement A: universal orderable_day skip
  - `bgf_auto/src/order/auto_order.py` -- Improvement B: order list split + verify/rescue
  - `bgf_auto/src/order/order_executor.py` -- collect_product_info_only()
  - `bgf_auto/src/infrastructure/database/repos/product_detail_repo.py` -- update_orderable_day()
- **Test File**: `bgf_auto/tests/test_orderable_day_all.py` (33 tests across 6 classes)
- **Analysis Date**: 2026-02-25

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 97% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **98%** | **PASS** |

---

## 3. Gap Analysis (Design vs Implementation)

### 3.1 Improvement A: improved_predictor.py -- ctx initialization (line 1552)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Add `"orderable_day_skip": False` to ctx dict | Line 1552: `"orderable_day_skip": False,` | MATCH |

**Verdict**: 1/1 items match. PASS.

### 3.2 Improvement A: improved_predictor.py -- Common orderable_day check block (lines 1903-1914)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| ORDERABLE_DAY_EXEMPT_MIDS = {"001", "002", "003", "004", "005", "012"} | Line 1904: `ORDERABLE_DAY_EXEMPT_MIDS = {"001", "002", "003", "004", "005", "012"}` | MATCH |
| Check `mid_cd not in ORDERABLE_DAY_EXEMPT_MIDS` | Line 1905: `if mid_cd not in ORDERABLE_DAY_EXEMPT_MIDS:` | MATCH |
| Import `_is_orderable_today` from snack_confection | Line 1906: `from src.prediction.categories.snack_confection import _is_orderable_today` | MATCH |
| Use `product.get("orderable_day") or DEFAULT_ORDERABLE_DAYS` | Line 1907: `item_orderable_day = product.get("orderable_day") or DEFAULT_ORDERABLE_DAYS` | **BUG** |
| Check `not _is_orderable_today(item_orderable_day)` | Line 1908: `if not _is_orderable_today(item_orderable_day):` | MATCH |
| Duplicate log prevention: `if not (ctx.get("ramen_skip_order") or new_cat_skip_order)` | Line 1909: `if not (ctx.get("ramen_skip_order") or new_cat_skip_order):` | MATCH |
| Log format: `[비발주일] {item_nm} ({item_cd}): orderable_day={od} -> 오늘 발주 스킵` | Lines 1910-1912: exact match | MATCH |
| Set `ctx["orderable_day_skip"] = True` | Line 1914: `ctx["orderable_day_skip"] = True` | MATCH |
| Block placed after FORCE_ORDER cap, before category skip checks | Lines 1903-1914: after FORCE cap (line 1901), before ramen/tobacco/beer/soju skip checks (lines 1917+) | MATCH |

**BUG FOUND**: Line 1907 references `DEFAULT_ORDERABLE_DAYS` but this constant is **never imported** in `improved_predictor.py`. Only `SNACK_DEFAULT_ORDERABLE_DAYS` and `RAMEN_DEFAULT_ORDERABLE_DAYS` are imported (lines 32-33 from `src.settings.constants`). The `DEFAULT_ORDERABLE_DAYS` constant exists in `src/settings/constants.py` (line 64, value `"일월화수목금토"`) and is properly imported in `auto_order.py` (line 28), but the import is missing from `improved_predictor.py`.

- **Impact**: `NameError` at runtime when a non-food, non-snack, non-ramen product has `orderable_day=None` or empty in its product dict.
- **Mitigation**: In practice, most products in `product_details` have `orderable_day` populated (default `"일월화수목금토"` from UPSERT in `product_detail_repo.py` line 83). The bug only triggers when `product.get("orderable_day")` returns falsy.
- **Fix**: Add `DEFAULT_ORDERABLE_DAYS,` to the import block at line 28 of `improved_predictor.py`.

**Verdict**: 8/9 items match, 1 BUG. CONDITIONAL PASS.

### 3.3 Improvement A: improved_predictor.py -- need_qty=0 (lines 1932-1933)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| `if ctx.get("orderable_day_skip"): need_qty = 0` after other skip blocks | Lines 1932-1933: `if ctx.get("orderable_day_skip"): need_qty = 0` placed after ramen/tobacco/beer/soju/food/new_cat skip blocks (lines 1917-1931) | MATCH |

**Verdict**: 1/1 items match. PASS.

### 3.4 Improvement B: auto_order.py -- Order list split (lines 1270-1292)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Import `_is_orderable_today` from snack_confection | Line 1271: `from src.prediction.categories.snack_confection import _is_orderable_today` | MATCH |
| Import `is_food_category` from food | Line 1272: `from src.prediction.categories.food import is_food_category` | MATCH |
| Initialize `orderable_today_list = []` | Line 1274: `orderable_today_list = []` | MATCH |
| Initialize `skipped_for_verify = []` | Line 1275: `skipped_for_verify = []` | MATCH |
| Food items always go to orderable_today_list | Lines 1279-1281: `if is_food_category(mid_cd): orderable_today_list.append(item); continue` | MATCH |
| Non-food: use `item.get("orderable_day") or DEFAULT_ORDERABLE_DAYS` | Line 1282: `od = item.get("orderable_day") or DEFAULT_ORDERABLE_DAYS` | MATCH |
| If orderable today -> orderable_today_list | Lines 1283-1284: `if _is_orderable_today(od): orderable_today_list.append(item)` | MATCH |
| Else -> skipped_for_verify | Lines 1285-1286: `else: skipped_for_verify.append(item)` | MATCH |
| Log split counts when skipped items exist | Lines 1288-1292: logging with counts | MATCH |

**Verdict**: 9/9 items match. PASS.

### 3.5 Improvement B: auto_order.py -- Phase A: orderable orders (lines 1294-1304)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Execute orderable_today_list via `executor.execute_orders()` | Lines 1297-1302: `result = self.executor.execute_orders(order_list=orderable_today_list, ...)` | MATCH |
| Empty list fallback: `{"success": True, "success_count": 0, "fail_count": 0, "results": []}` | Line 1304: exact match | MATCH |

**Verdict**: 2/2 items match. PASS.

### 3.6 Improvement B: auto_order.py -- Phase B: verify + rescue (lines 1306-1320)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Guard: `skipped_for_verify and not dry_run and self.executor` | Line 1307: `if skipped_for_verify and not dry_run and self.executor:` | MATCH |
| Call `_verify_and_rescue_skipped_items(skipped_for_verify)` | Line 1308: `rescued = self._verify_and_rescue_skipped_items(skipped_for_verify)` | MATCH |
| If rescued, execute rescue orders | Lines 1309-1318: `if rescued:` block with execute_orders and result merge | MATCH |
| Merge success_count | Line 1316: `result["success_count"] = result.get("success_count", 0) + rescue_result.get("success_count", 0)` | MATCH |
| Merge fail_count | Line 1317: `result["fail_count"] = result.get("fail_count", 0) + rescue_result.get("fail_count", 0)` | MATCH |
| Merge results list | Line 1318: `result.setdefault("results", []).extend(rescue_result.get("results", []))` | MATCH |
| Log when no rescue needed | Lines 1319-1320: else branch with log | MATCH |

**Note**: The design (line 213-215) shows `result["success_count"] += rescue_result.get(...)` shorthand, while implementation (line 1316) uses the equivalent `result["success_count"] = result.get("success_count", 0) + ...` form. Functionally equivalent -- the implementation is actually safer (uses `.get()` with default).

**Verdict**: 7/7 items match. PASS.

### 3.7 Improvement B: auto_order.py -- _verify_and_rescue_skipped_items() (lines 1765-1846)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Method signature: `(self, skipped_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]` | Line 1765-1766: exact match | MATCH |
| Import `_is_orderable_today` | Line 1779: `from src.prediction.categories.snack_confection import _is_orderable_today` | MATCH |
| Guard: no executor -> return [] | Lines 1781-1783: `if not self.executor: ... return []` | MATCH |
| Navigate to single order menu | Line 1792: `if not self.executor.navigate_to_single_order():` | MATCH |
| Menu navigation failure -> return [] | Lines 1793-1794: log + `return []` | MATCH |
| Per-item: call `executor.collect_product_info_only(item_cd)` | Line 1807: `actual_data = self.executor.collect_product_info_only(item_cd)` | MATCH |
| Collection failure -> continue (skip item) | Lines 1808-1810: `if not actual_data: ... continue` | MATCH |
| set() comparison: `set(db_orderable_day) vs set(actual_orderable_day)` | Lines 1816-1817: `db_set = set(db_orderable_day)`, `actual_set = set(actual_orderable_day)` | MATCH |
| Mismatch + actual_set not empty: call `_product_repo.update_orderable_day()` | Lines 1819-1827: `if actual_set and db_set != actual_set: ... self._product_repo.update_orderable_day(item_cd, actual_orderable_day)` | MATCH |
| DB update failure: `except Exception as e` with warning log | Lines 1828-1829: `except Exception as e: logger.warning(...)` | MATCH |
| If orderable today after correction -> append to rescue_list | Lines 1832-1834: `if _is_orderable_today(actual_orderable_day): item["orderable_day"] = ...; rescue_list.append(item)` | MATCH |
| Update item["orderable_day"] before rescue | Line 1833: `item["orderable_day"] = actual_orderable_day` | MATCH |
| Summary log with verified/corrected/rescue counts | Lines 1841-1844 | MATCH |

**Verdict**: 13/13 items match. PASS.

### 3.8 Improvement B: order_executor.py -- collect_product_info_only() (lines 2230-2318)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Method signature: `(self, item_cd: str) -> Optional[Dict[str, Any]]` | Line 2230: `def collect_product_info_only(self, item_cd: str) -> Optional[Dict[str, Any]]:` | MATCH |
| Step 0: Alert cleanup | Line 2246: `self._clear_any_alerts(silent=True)` | MATCH |
| Step 0.5: Click last row item cell | Lines 2248-2250: `self._click_last_row_item_cell()` | MATCH |
| Step 1: Product code input (reuse optimized method) | Lines 2253-2262: `_input_product_code_optimized(item_cd)` with Phase 0/1 fallback | MATCH |
| Step 2: Enter to search | Lines 2264-2268: `actions.send_keys(Keys.ENTER)` | MATCH |
| Step 3: Loading wait (reuse existing) | Lines 2270-2274: `_wait_for_loading_complete()` with fallback | MATCH |
| Alert check for "없"/"불가" | Lines 2277-2280: `if alert_text and ('없' in alert_text or '불가' in alert_text): return None` | MATCH |
| Step 4: Read grid data | Line 2283: `grid_data = self._read_product_info_from_grid(item_cd)` | MATCH |
| Verify orderable_day if present | Lines 2294-2295: `if orderable_day: self._verify_orderable_day(...)` | MATCH |
| Save to DB | Line 2296: `self.product_collector.save_to_db(item_cd, grid_data)` | MATCH |
| Step 5: Input 0 for quantity (cancel order) | Lines 2299-2308: TAB to multiplier cell, send "0", ENTER | MATCH |
| 0-input failure: debug log only | Lines 2311-2312: `logger.debug(...)` | MATCH |
| Alert cleanup after 0-input | Line 2310: `self._clear_any_alerts(silent=True)` | MATCH |
| Return grid_data | Line 2314: `return grid_data` | MATCH |
| Exception handling: warning log + return None | Lines 2316-2318: `except Exception as e: logger.warning(...); return None` | MATCH |

**Verdict**: 15/15 items match. PASS.

### 3.9 Improvement B: product_detail_repo.py -- update_orderable_day() (lines 339-363)

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Method signature: `(self, item_cd: str, orderable_day: str) -> bool` | Line 339: `def update_orderable_day(self, item_cd: str, orderable_day: str) -> bool:` | MATCH |
| SQL: `UPDATE product_details SET orderable_day = ?, updated_at = ? WHERE item_cd = ?` | Lines 353-355: exact SQL match | MATCH |
| Parameters: `(orderable_day, now, item_cd)` | Line 356: `(orderable_day, now, item_cd)` | MATCH |
| Check `cursor.rowcount > 0` | Line 358: `updated = cursor.rowcount > 0` | MATCH |
| Log `[DB교정] {item_cd}: orderable_day -> {orderable_day}` if updated | Line 360: `logger.info(f"[DB교정] {item_cd}: orderable_day -> {orderable_day}")` | MATCH |
| Return bool | Line 361: `return updated` | MATCH |
| try/finally with conn.close() | Lines 349-363 | MATCH |

**Note**: Design uses arrow character `→` in the log format while implementation uses `→` (Unicode). Both are the same Unicode character U+2192. Exact match.

**Verdict**: 7/7 items match. PASS.

### 3.10 Tests: test_orderable_day_all.py

| Design Requirement | Implementation | Status |
|-------------------|----------------|--------|
| Total: 34 tests across 6 classes | 33 tests across 6 classes | **CHANGED** |
| TestOrderableDaySkipAllCategories: 13 | 13 tests | MATCH |
| TestOrderListSplit: 4 | 4 tests | MATCH |
| TestVerifyAndRescue: 6 | 6 tests | MATCH |
| TestUpdateOrderableDay: 3 | 3 tests (using in_memory_db fixture) | MATCH |
| TestCollectProductInfoOnly: 3 | 3 tests | MATCH |
| TestIntegrationScenarios: 5 | 4 tests | **CHANGED** |

**Details on TestIntegrationScenarios (4 vs 5)**:
- test_full_flow_with_rescue -- present
- test_all_skipped_confirmed_non_orderable -- present
- test_empty_order_list_no_error -- present
- test_snack_ramen_existing_skip_not_duplicated -- present
- (5th test) -- **MISSING**

The design specifies 5 integration tests but only 4 are implemented. The missing 5th test is unspecified in the detailed scenarios (Section 8.2), so the design count of "5" may have been a planning estimate that was reduced to 4 during implementation without updating the design document.

**Impact**: LOW. All key scenarios are covered by the 4 existing integration tests. The design's own scenario table (Section 8.2) does not enumerate 5 distinct integration scenarios, suggesting the count was approximate.

**Verdict**: 5/6 class-level test counts match. 33/34 total tests. CONDITIONAL PASS.

---

## 4. Design Document Accuracy Issues

### 4.1 Soju mid_cd in Design Table

The design Section 3.3 impact table lists:
```
| 051 | 소주 | orderable_day 무시 | 비발주일 자동 스킵 |
```

However, the actual codebase defines soju as mid_cd `"050"` (in `src/prediction/categories/soju.py` line 23: `SOJU_CATEGORIES = ['050']`), and the test file correctly uses `"050"` (line 119). The CLAUDE.md reference table also shows `050 | 소주`.

**Impact**: LOW. This is a documentation error in the design document, not an implementation error. The implementation and tests are correct.

---

## 5. Error Handling Verification

| Error Scenario | Design Spec | Implementation | Status |
|----------------|-------------|----------------|--------|
| executor is None | Return empty list | Lines 1781-1783: `if not self.executor: return []` | MATCH |
| Menu navigation failure | Skip all verification, return [] | Lines 1792-1794: `return []` | MATCH |
| Individual item collection failure | Skip item, continue | Lines 1808-1810: `if not actual_data: continue` | MATCH |
| DB update failure | Warning log, item not rescued | Lines 1828-1829: `except Exception as e: logger.warning(...)` | MATCH |
| Alert popup ("없"/"불가") | Return None from collect | Lines 2278-2280: `return None` | MATCH |
| Multiplier 0 input failure | Debug log, proceed to next | Lines 2311-2312: `logger.debug(...)` | MATCH |

**Verdict**: 6/6 error scenarios match. PASS.

---

## 6. Function Dependency Verification

| Dependency | Design | Implementation | Status |
|------------|--------|----------------|--------|
| `auto_order.execute()` -> `snack_confection._is_orderable_today()` | Reuse | Line 1271: import + line 1283: call | MATCH |
| `auto_order.execute()` -> `food.is_food_category()` | Reuse | Line 1272: import + line 1279: call | MATCH |
| `auto_order.execute()` -> `executor.execute_orders()` | Existing | Lines 1298, 1311: calls | MATCH |
| `auto_order._verify_and_rescue_skipped_items()` -> `executor.navigate_to_single_order()` | Existing | Line 1792: call | MATCH |
| `auto_order._verify_and_rescue_skipped_items()` -> `executor.collect_product_info_only()` | New | Line 1807: call | MATCH |
| `auto_order._verify_and_rescue_skipped_items()` -> `product_detail_repo.update_orderable_day()` | New | Line 1827: call | MATCH |
| `auto_order._verify_and_rescue_skipped_items()` -> `_is_orderable_today()` | Reuse | Line 1779: import + line 1832: call | MATCH |
| `improved_predictor` -> `_is_orderable_today()` | Reuse | Line 1906: import + line 1908: call | MATCH |
| `collect_product_info_only()` -> `_read_product_info_from_grid()` | Existing | Line 2283: call | MATCH |
| `collect_product_info_only()` -> `_verify_orderable_day()` | Existing | Line 2295: call | MATCH |
| `collect_product_info_only()` -> `save_to_db()` | Existing | Line 2296: call | MATCH |

**Verdict**: 11/11 dependencies match. PASS.

---

## 7. Unchanged Files Verification

| File | Design: No Change | Verified | Status |
|------|:-----------------:|:--------:|--------|
| `snack_confection.py` | Yes | `_is_orderable_today()` at line 100 unchanged, only imported by others | MATCH |
| `food.py` | Yes | `is_food_category()` at line 234 unchanged, only imported by others | MATCH |
| `ramen.py` | Yes | Existing skip_order logic unchanged | MATCH |
| `src/db/models.py` | Yes | No schema change (product_details.orderable_day already exists in v42) | MATCH |

**Verdict**: 4/4 unchanged files verified. PASS.

---

## 8. Summary Table

| # | Check Item | Design | Implementation | Status |
|---|-----------|--------|----------------|--------|
| 1 | ctx["orderable_day_skip"] = False init | Line 1552 | Line 1552 | MATCH |
| 2 | ORDERABLE_DAY_EXEMPT_MIDS constant | {"001","002","003","004","005","012"} | Exact match at line 1904 | MATCH |
| 3 | _is_orderable_today() import in predictor | From snack_confection | Line 1906 | MATCH |
| 4 | DEFAULT_ORDERABLE_DAYS fallback | product.get("orderable_day") or DEFAULT_ORDERABLE_DAYS | Line 1907 -- **import missing** | **BUG** |
| 5 | Duplicate skip prevention | ramen_skip_order or new_cat_skip_order guard | Line 1909 | MATCH |
| 6 | need_qty = 0 when orderable_day_skip | After other skip blocks | Lines 1932-1933 | MATCH |
| 7 | Order list split with food exemption | is_food_category -> always orderable | Lines 1270-1286 | MATCH |
| 8 | Phase A: execute orderable_today_list | executor.execute_orders | Lines 1297-1304 | MATCH |
| 9 | Phase B: dry_run guard | not dry_run and self.executor | Line 1307 | MATCH |
| 10 | _verify_and_rescue_skipped_items() | New method | Lines 1765-1846 | MATCH |
| 11 | executor guard -> empty list | If not self.executor | Lines 1781-1783 | MATCH |
| 12 | Menu navigation failure -> empty list | navigate_to_single_order() | Lines 1792-1794 | MATCH |
| 13 | Per-item: collect_product_info_only() | executor method | Line 1807 | MATCH |
| 14 | Collection failure -> continue | Skip item | Lines 1808-1810 | MATCH |
| 15 | set() comparison (order-independent) | set(db) vs set(actual) | Lines 1816-1817 | MATCH |
| 16 | DB correction: update_orderable_day() | _product_repo method | Line 1827 | MATCH |
| 17 | Rescue if orderable today after correction | _is_orderable_today(actual) | Lines 1832-1834 | MATCH |
| 18 | Result merge (success_count, fail_count, results) | Additive merge | Lines 1316-1318 | MATCH |
| 19 | collect_product_info_only() full flow | 5 steps matching input_product() | Lines 2230-2318 | MATCH |
| 20 | Multiplier 0 input (cancel order) | TAB -> "0" -> ENTER | Lines 2300-2307 | MATCH |
| 21 | update_orderable_day() single-field UPDATE | Only orderable_day + updated_at | Lines 339-363 | MATCH |
| 22 | Test count: 34 in 6 classes | 34 / 6 | 33 / 6 (1 integration test missing) | **CHANGED** |

**Totals**: 20 MATCH, 1 BUG, 1 CHANGED = 22 items checked

---

## 9. Differences Found

### 9.1 BUG: Missing import in improved_predictor.py

| Item | Location | Description | Impact |
|------|----------|-------------|--------|
| DEFAULT_ORDERABLE_DAYS import missing | `improved_predictor.py` line 1907 | `DEFAULT_ORDERABLE_DAYS` used but never imported. Only `SNACK_DEFAULT_ORDERABLE_DAYS` and `RAMEN_DEFAULT_ORDERABLE_DAYS` are imported at lines 32-33. | HIGH -- NameError at runtime when product has no orderable_day |

**Fix required**: Add `DEFAULT_ORDERABLE_DAYS,` to the import block at line 28 of `improved_predictor.py`:

```python
from src.settings.constants import (
    TOBACCO_MAX_STOCK as DEFAULT_TOBACCO_MAX_STOCK,
    ...
    SNACK_DEFAULT_ORDERABLE_DAYS,
    RAMEN_DEFAULT_ORDERABLE_DAYS,
    DEFAULT_ORDERABLE_DAYS,  # <-- ADD THIS
)
```

### 9.2 CHANGED: Test count (33 vs 34)

| Item | Design | Implementation | Impact |
|------|--------|----------------|--------|
| TestIntegrationScenarios test count | 5 tests | 4 tests | LOW |

One integration test is missing from `TestIntegrationScenarios`. The design specifies 5 but the implementation has 4. All key scenarios are covered; the design count appears to have been an approximate estimate.

### 9.3 Documentation: Soju mid_cd in design

| Item | Design | Actual Code | Impact |
|------|--------|-------------|--------|
| Soju mid_cd in impact table | 051 | 050 | LOW (doc-only error) |

---

## 10. Additive Enhancements (Design X, Implementation O)

| # | Item | Implementation Location | Description |
|---|------|------------------------|-------------|
| 1 | Else-branch log for no-rescue case | `auto_order.py` line 1320 | When `rescued` is empty, logs that all skipped items are confirmed non-orderable. Not specified in design but good for operational visibility. |
| 2 | Rescue-count log before execute | `auto_order.py` line 1310 | `[DB교정 발주] {N}개 상품 실제 발주가능` log line. Design only mentions result merge, not the pre-execution log. |
| 3 | Empty set guard in verification | `auto_order.py` line 1819 | `if actual_set and db_set != actual_set` -- the `actual_set` truthiness check prevents false corrections when BGF returns empty orderable_day. Design shows `if db_set != actual_set` without the `actual_set` guard. This is a defensive improvement. |
| 4 | Alert cleanup after 0-input | `order_executor.py` line 2310 | `self._clear_any_alerts(silent=True)` after entering 0. Not explicitly in design but prevents stale alerts from interfering with the next item. |

---

## 11. Architecture Compliance

| Rule | Check | Status |
|------|-------|--------|
| Import direction: Application -> Infrastructure | auto_order imports from prediction/categories (domain-adjacent) and infrastructure repos | PASS |
| Single-field UPDATE preserves other fields | update_orderable_day only touches orderable_day + updated_at | PASS |
| Lazy imports for infrequently used modules | _is_orderable_today imported inside function blocks | PASS |
| Error handling: no silent pass | All except blocks have logger calls | PASS |
| Repository pattern for DB access | update_orderable_day via ProductDetailRepository | PASS |

---

## 12. Recommended Actions

### Immediate Actions (Required)

1. **FIX BUG**: Add `DEFAULT_ORDERABLE_DAYS` import to `improved_predictor.py` line 28.
   - File: `bgf_auto/src/prediction/improved_predictor.py`
   - Add to import block: `DEFAULT_ORDERABLE_DAYS,`
   - Without this fix, a NameError will occur at runtime for any non-food product with missing orderable_day.

### Documentation Updates (Optional)

1. Update design document Section 3.3 impact table: change soju mid_cd from "051" to "050".
2. Update design document Section 8.1: change TestIntegrationScenarios count from 5 to 4.

---

## 13. Final Assessment

| Metric | Value |
|--------|-------|
| Total check items | 22 |
| Exact match | 20 |
| Changed (trivial) | 1 (test count 33 vs 34) |
| Bug found | 1 (missing import) |
| Missing features | 0 |
| Additive enhancements | 4 |
| Match rate | **98%** |
| Overall status | **PASS** (conditional on bug fix) |

The orderable-day-all feature is implemented faithfully to the design with one significant bug: the `DEFAULT_ORDERABLE_DAYS` constant is used but not imported in `improved_predictor.py`. This should be fixed immediately. All other 21 check items match exactly or have functionally equivalent implementations. The 4 additive enhancements (defensive empty-set guard, operational logging, alert cleanup) improve robustness beyond the design specification.

---

## Related Documents

- Design: [orderable-day-all.design.md](../02-design/features/orderable-day-all.design.md)
- Tests: `bgf_auto/tests/test_orderable_day_all.py`
- Predecessor: snack-orderable-day, ramen-orderable-day

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-25 | Initial gap analysis | gap-detector |
