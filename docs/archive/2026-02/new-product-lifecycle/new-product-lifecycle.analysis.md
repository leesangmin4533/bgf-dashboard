# new-product-lifecycle Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector agent
> **Date**: 2026-02-26
> **Design Doc**: [new-product-lifecycle.design.md](../02-design/features/new-product-lifecycle.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Design-Implementation gap detection for the "new-product-lifecycle" feature.
Verifies that the implementation matches the design document across DB schema,
repositories, service logic, predictor integration, scheduler wiring, web API, and tests.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/new-product-lifecycle.design.md`
- **Implementation Files**: 12 files (4 new, 8 modified)
- **Analysis Date**: 2026-02-26

### 1.3 Files Analyzed

| # | File | Role | Status |
|---|------|------|--------|
| 1 | `src/settings/constants.py` | DB_SCHEMA_VERSION = 46 | Verified |
| 2 | `src/db/models.py` | SCHEMA_MIGRATIONS[46] | Verified |
| 3 | `src/infrastructure/database/schema.py` | STORE_SCHEMA + STORE_INDEXES | Verified |
| 4 | `src/infrastructure/database/repos/np_tracking_repo.py` | NewProductDailyTrackingRepository (NEW) | Verified |
| 5 | `src/infrastructure/database/repos/detected_new_product_repo.py` | lifecycle methods added | Verified |
| 6 | `src/infrastructure/database/repos/__init__.py` | re-export | Verified |
| 7 | `src/application/services/new_product_monitor.py` | NewProductMonitor (NEW) | Verified |
| 8 | `src/prediction/improved_predictor.py` | _apply_new_product_boost + cache | Verified |
| 9 | `src/scheduler/daily_job.py` | Phase 1.35 | Verified |
| 10 | `src/web/routes/api_receiving.py` | monitoring + tracking endpoints | Verified |
| 11 | `tests/conftest.py` | test DB tables updated | Verified |
| 12 | `tests/test_new_product_lifecycle.py` | 20 tests (NEW) | Verified |

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 97% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **98%** | **PASS** |

---

## 3. Detailed Gap Analysis

### 3.1 DB Schema (v46) -- 10 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | DB_SCHEMA_VERSION = 46 | `constants.py:210` -- `DB_SCHEMA_VERSION = 46` | EXACT |
| 2 | SCHEMA_MIGRATIONS[46] exists | `models.py:1470` -- key 46 present | EXACT |
| 3 | ALTER lifecycle_status TEXT DEFAULT 'detected' | `models.py:1472` -- exact match | EXACT |
| 4 | ALTER monitoring_start_date TEXT | `models.py:1473` -- exact match | EXACT |
| 5 | ALTER monitoring_end_date TEXT | `models.py:1474` -- exact match | EXACT |
| 6 | ALTER total_sold_qty INTEGER DEFAULT 0 | `models.py:1475` -- exact match | EXACT |
| 7 | ALTER sold_days INTEGER DEFAULT 0 | `models.py:1476` -- exact match | EXACT |
| 8 | ALTER similar_item_avg REAL | `models.py:1477` -- exact match | EXACT |
| 9 | ALTER status_changed_at TEXT | `models.py:1478` -- exact match | EXACT |
| 10 | CREATE TABLE new_product_daily_tracking (7 cols + UNIQUE) | `models.py:1480-1490` -- exact match | EXACT |

**schema.py reflection:**

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 11 | STORE_SCHEMA: new_product_daily_tracking added | `schema.py:677-688` -- table present with all columns | EXACT |
| 12 | STORE_SCHEMA: detected_new_products has lifecycle cols | `schema.py:649-675` -- all 7 lifecycle columns present | EXACT |
| 13 | STORE_INDEXES: idx_np_tracking_item | `schema.py:765` -- exact match | EXACT |
| 14 | STORE_INDEXES: idx_np_tracking_date | `schema.py:766` -- exact match | EXACT |
| 15 | STORE_INDEXES: idx_detected_new_products_lifecycle | `schema.py:763` -- bonus index on lifecycle_status | ADDED |

**Sub-total: 14 exact + 1 added = 15/15**

---

### 3.2 NewProductDailyTrackingRepository (`np_tracking_repo.py`) -- 8 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | db_type = "store" | Line 18: `db_type = "store"` | EXACT |
| 2 | save(item_cd, tracking_date, sales_qty, stock_qty, order_qty, store_id) -> int | Lines 20-66: exact signature + UPSERT on CONFLICT | EXACT |
| 3 | get_tracking_history(item_cd, store_id) -> List[Dict] | Lines 68-100: ORDER BY tracking_date ASC | EXACT |
| 4 | get_sold_days_count(item_cd, store_id) -> int | Lines 102-133: COUNT WHERE sales_qty > 0 | EXACT |
| 5 | get_total_sold_qty(item_cd, store_id) -> int | Lines 135-166: COALESCE(SUM(sales_qty), 0) | EXACT |
| 6 | UPSERT behavior (ON CONFLICT DO UPDATE) | Lines 53-56: updates sales_qty, stock_qty, order_qty | EXACT |
| 7 | store_id fallback (self.store_id) | Line 59: `store_id or self.store_id` | EXACT |
| 8 | Connection cleanup (try/finally conn.close) | All methods use try/finally pattern | EXACT |

**Sub-total: 8/8 exact**

---

### 3.3 DetectedNewProductRepository lifecycle extension -- 9 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | get_by_lifecycle_status(statuses, store_id) -> List[Dict] | Lines 218-252: IN clause + ORDER BY detected_at DESC | EXACT |
| 2 | Empty list guard | Line 230-231: `if not statuses: return []` | EXACT |
| 3 | update_lifecycle(item_cd, status, monitoring_start, monitoring_end, ...) | Lines 254-312: dynamic SET clause | EXACT |
| 4 | status_changed_at auto-set | Line 282: always set `status_changed_at = ?` with `self._now()` | EXACT |
| 5 | get_monitoring_summary(store_id) -> Dict[str, int] | Lines 314-343: GROUP BY lifecycle_status | EXACT |
| 6 | Conditional field updates (monitoring_start, monitoring_end, etc.) | Lines 285-299: `if xxx is not None` guards | EXACT |
| 7 | store_id WHERE clause (optional) | Lines 305-307: appended if store_id provided | EXACT |
| 8 | Connection cleanup | try/finally pattern in all methods | EXACT |
| 9 | get_count_by_date(target_date, store_id) -> int | Lines 346-373: bonus method not in design | ADDED |

**Sub-total: 8 exact + 1 added = 9/9**

---

### 3.4 repos/__init__.py re-export -- 2 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | Import NewProductDailyTrackingRepository | Line 35: `from .np_tracking_repo import NewProductDailyTrackingRepository` | EXACT |
| 2 | Export in __all__ | Line 73: `"NewProductDailyTrackingRepository"` in __all__ | EXACT |

**Sub-total: 2/2 exact**

---

### 3.5 NewProductMonitor (`new_product_monitor.py`) -- 18 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | MONITORING_DAYS = 14 | Line 30: `MONITORING_DAYS = 14` | EXACT |
| 2 | STABLE_THRESHOLD_DAYS = 3 | Line 31: `STABLE_THRESHOLD_DAYS = 3` | EXACT |
| 3 | NORMAL_DAYS_AFTER_STABLE = 30 | Line 32: `NORMAL_DAYS_AFTER_STABLE = 30` | EXACT |
| 4 | __init__(store_id) with detect_repo | Lines 34-37: both repos created | EXACT |
| 5 | run() returns dict with active_items, tracking_saved, status_changes | Lines 39-62: exact return structure | EXACT |
| 6 | get_active_monitoring_items() | Lines 64-68 | CHANGED |
| 7 | collect_daily_tracking(items, today) -> int | Lines 70-99: sales_map + stock_map + order_map | EXACT |
| 8 | Data sources: daily_sales.sell_qty | Line 265: `SELECT item_cd, sell_qty FROM daily_sales WHERE sales_date = ?` | EXACT |
| 9 | Data sources: realtime_inventory.stock_qty | Line 283: `SELECT item_cd, stock_qty FROM realtime_inventory WHERE is_available = 1` | EXACT |
| 10 | Data sources: auto_order_items.order_qty | Line 300: `SELECT item_cd, order_qty FROM auto_order_items WHERE order_date = ?` | **BUG** |
| 11 | detected -> monitoring transition | Lines 110-122: first run sets monitoring_start=today | EXACT |
| 12 | monitoring -> stable (14d + sold_days>=3) | Lines 124-168: elapsed >= MONITORING_DAYS check | EXACT |
| 13 | monitoring -> no_demand (14d + sold_days==0) | Lines 141-142: `sold_days == 0` branch | EXACT |
| 14 | monitoring -> slow_start (14d + 0<sold_days<3) | Lines 143-146: else branch (implicit 0 < sold_days < 3) | EXACT |
| 15 | stable -> normal (monitoring_start + 30d) | Lines 170-185: elapsed >= NORMAL_DAYS_AFTER_STABLE | EXACT |
| 16 | calculate_similar_avg(item_cd, mid_cd) -> Optional[float] | Lines 189-253: median of daily_avgs | EXACT |
| 17 | mid_cd == '999' guard | Line 200: returns None | EXACT |
| 18 | common.products JOIN daily_sales | Lines 215-229: ATTACH common + JOIN | EXACT |

**Details on findings:**

**Item 6 -- CHANGED**: `get_active_monitoring_items()` queries `["detected", "monitoring", "stable"]` (3 statuses) but the design specifies only `["detected", "monitoring"]` (2 statuses). The implementation adds `"stable"` to also track `stable -> normal` transitions, which is required for the state machine to work correctly. This is a design document underspecification -- the implementation is correct because the `update_lifecycle_status()` method handles the `stable -> normal` transition (lines 170-185), which requires `stable` items to be in the active list. **Functionally equivalent enhancement.**

**Item 10 -- BUG FOUND**: `_get_order_map()` at line 300 queries:
```sql
SELECT item_cd, order_qty FROM auto_order_items WHERE order_date = ?
```
However, the `auto_order_items` table schema (`schema.py:397-405`) does NOT contain `order_date` or `order_qty` columns. The actual columns are: `store_id, item_cd, item_nm, mid_cd, detected_at, updated_at`. This query will always fail with `sqlite3.OperationalError`, caught by the `except Exception: return {}` handler, causing `order_qty` to always be 0 in tracking records. The correct table should be `order_tracking` (which has both `order_date` and `order_qty`) or `order_history`. **Impact: LOW** -- tracking data records `order_qty=0` for all items; the core lifecycle status transitions and boost logic are unaffected.

**Sub-total: 16 exact + 1 changed (enhancement) + 1 bug = 18/18**

---

### 3.6 ImprovedPredictor boost integration -- 10 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | `_new_product_cache: Dict[str, Dict] = {}` in __init__ | Line 294: `self._new_product_cache: Dict[str, Dict] = {}` | EXACT |
| 2 | `_load_new_product_cache()` method | Lines 339-355: lazy 1-time load | EXACT |
| 3 | Cache loads ["monitoring"] status only | Line 349: `["monitoring"]` | EXACT |
| 4 | `_apply_new_product_boost()` signature | Lines 357-361: `(item_cd, mid_cd, order_qty, prediction, current_stock, pending_qty, safety_stock)` | CHANGED |
| 5 | Cache empty guard | Line 373: `if not self._new_product_cache: return order_qty` | EXACT |
| 6 | lifecycle_status != "monitoring" guard | Line 377: `if not np_info or np_info.get("lifecycle_status") != "monitoring"` | EXACT |
| 7 | similar_avg None/<=0 guard | Lines 380-381: `if similar_avg is None or similar_avg <= 0` | EXACT |
| 8 | Boost formula: max(similar_avg * 0.7, prediction) | Line 384: `boosted = max(similar_avg * 0.7, prediction)` | EXACT |
| 9 | New order: max(1, round(boosted - stock - pending + safety)) | Line 386: exact formula | EXACT |
| 10 | Position: after ML ensemble, before order unit rounding | Lines 2050-2055: after ML (line 2044), before round_to_order_unit (line 2060) | CHANGED |

**Details on findings:**

**Item 4 -- CHANGED**: Design shows `_apply_new_product_boost(self, item_cd, mid_cd, order_qty, prediction)` with 4 params, but implementation takes 7 params: `(item_cd, mid_cd, order_qty, prediction, current_stock, pending_qty, safety_stock)`. The extra params are needed for the `new_order = max(1, round(boosted - current_stock - pending_qty + safety_stock))` formula. The design doc's pseudocode section (line 234) references `current_stock`, `pending`, `safety` but omits them from the signature. **Functionally correct -- design doc pseudocode is internally inconsistent.**

**Item 10 -- CHANGED**: Design says "DiffFeedback **before** (i.e., position before DiffFeedback)" but implementation places the boost **after ML ensemble, before order unit rounding**, which is actually **before** DiffFeedback (line 2066). So the relative order matches the design intent. However, the design says "DiffFeedback **before**, order unit rounding **before**" -- the actual sequence is: ML ensemble -> new product boost -> order unit rounding -> DiffFeedback -> waste feedback. The boost is correctly placed between ML and rounding, consistent with the design's stated position.

**Sub-total: 8 exact + 2 changed (trivial) = 10/10**

---

### 3.7 daily_job.py Phase 1.35 -- 6 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | Phase 1.35 placement (between 1.3 and 1.5) | Lines 326-341: after Phase 1.3 new product, before Phase 1.5 EvalCalibration | EXACT |
| 2 | Guard: `if collection_success` | Line 327: `if collection_success:` | EXACT |
| 3 | Logger: `[Phase 1.35] New Product Lifecycle Monitoring` | Line 329: exact string | EXACT |
| 4 | Import NewProductMonitor | Line 331: lazy import from `src.application.services.new_product_monitor` | EXACT |
| 5 | monitor.run() stats logging | Lines 334-339: logs active_items, tracking_saved, status_changes | EXACT |
| 6 | Exception handling (warning, continue flow) | Lines 340-341: `logger.warning(f"... (발주 플로우 계속): {e}")` | EXACT |

**Sub-total: 6/6 exact**

---

### 3.8 Web API endpoints -- 6 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | GET /api/receiving/new-products/monitoring | Lines 326-350: `@receiving_bp.route("/new-products/monitoring")` | EXACT |
| 2 | Monitoring response: {summary, items, count} | Lines 343-347: exact keys | EXACT |
| 3 | GET /api/receiving/new-products/<item_cd>/tracking | Lines 353-372: `@receiving_bp.route("/new-products/<item_cd>/tracking")` | EXACT |
| 4 | Tracking response: {item_cd, tracking} | Lines 366-368: exact keys | EXACT |
| 5 | store_id query param with DEFAULT_STORE_ID | Lines 333, 360: `request.args.get("store_id", DEFAULT_STORE_ID)` | EXACT |
| 6 | Error handling (try/except, 500) | Lines 349-350, 371-372: `return jsonify({"error": str(e)}), 500` | EXACT |

**Sub-total: 6/6 exact**

---

### 3.9 conftest.py test DB -- 2 check items

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 1 | detected_new_products with lifecycle columns | conftest.py:780-808: all 7 lifecycle columns present | EXACT |
| 2 | new_product_daily_tracking table | conftest.py:810-822: exact schema | EXACT |

**Sub-total: 2/2 exact**

---

### 3.10 Tests (`test_new_product_lifecycle.py`) -- 20 check items

| # | Design Test | Implementation | Status |
|---|------------|----------------|--------|
| 1 | test_collect_daily_tracking | Line 166: present, verifies sales/stock/order saved | EXACT |
| 2 | test_detected_to_monitoring | Line 200: present, checks status change + monitoring_start | EXACT |
| 3 | test_monitoring_to_stable | Line 227: present, 14d + sold_days>=3 | EXACT |
| 4 | test_monitoring_to_no_demand | Line 258: present, 14d + sold_days==0 | EXACT |
| 5 | test_monitoring_to_slow_start | Line 282: present, 14d + 1<=sold_days<3 | EXACT |
| 6 | test_stable_to_normal | Line 307: present, stable + 30d elapsed | EXACT |
| 7 | test_similar_avg_calculation | Line 331: present, ATTACH common + median check (==4.0) | EXACT |
| 8 | test_similar_avg_no_match | Line 385: present, mid_cd=999 -> None | EXACT |
| 9 | test_boost_applied | Line 410: monitoring + similar_avg -> boosted to 8 | EXACT |
| 10 | test_boost_skip_stable | Line 430: stable -> no change (==2) | EXACT |
| 11 | test_boost_skip_no_similar | Line 447: similar_avg=None -> no change (==5) | EXACT |
| 12 | test_boost_cap | Line 464: similar*0.7 < prediction -> no change (==5) | EXACT |
| 13 | test_boost_cache_loading | Line 481: _load_new_product_cache() populates dict | EXACT |
| 14 | test_lifecycle_status_query | Line 518: get_by_lifecycle_status multi-status query | EXACT |
| 15 | test_update_lifecycle | Line 544: all fields updated correctly | EXACT |
| 16 | test_tracking_save_and_get | Line 570: UPSERT + sold_days + total_sold | EXACT |
| 17 | test_monitoring_summary | Line 595: status-grouped counts | EXACT |
| 18 | test_monitoring_api | Line 621: Flask test client, /monitoring endpoint | EXACT |
| 19 | test_tracking_api | Line 646: Flask test client, /tracking endpoint | EXACT |
| 20 | test_schema_v46 | Line 668: SCHEMA_MIGRATIONS[46] + in-memory execution | EXACT |

**Sub-total: 20/20 exact**

**Test organization matches design exactly:**
- Monitor tests (8): #1-8
- Booster tests (5): #9-13
- Repository tests (4): #14-17
- API + Schema tests (3): #18-20

---

## 4. Differences Found

### 4.1 Missing Features (Design O, Implementation X)

None.

### 4.2 Added Features (Design X, Implementation O)

| # | Item | Implementation Location | Description | Impact |
|---|------|------------------------|-------------|--------|
| 1 | idx_detected_new_products_lifecycle index | `schema.py:763` | Bonus index on lifecycle_status for query performance | LOW (beneficial) |
| 2 | get_count_by_date() method | `detected_new_product_repo.py:346-373` | Bonus query method for date-based count | LOW (additive) |

### 4.3 Changed Features (Design != Implementation)

| # | Item | Design | Implementation | Impact |
|---|------|--------|----------------|--------|
| 1 | get_active_monitoring_items statuses | `["detected", "monitoring"]` | `["detected", "monitoring", "stable"]` | LOW -- required for stable->normal transition |
| 2 | _apply_new_product_boost params | 4 params (item_cd, mid_cd, order_qty, prediction) | 7 params (+current_stock, pending_qty, safety_stock) | LOW -- design pseudocode internally references these params |

### 4.4 Bugs Found

| # | Severity | Location | Description |
|---|----------|----------|-------------|
| 1 | MEDIUM | `new_product_monitor.py:300` | `_get_order_map()` queries `auto_order_items WHERE order_date = ?` but `auto_order_items` table has no `order_date` or `order_qty` columns. Query always fails silently, returning empty dict. Tracking records will have `order_qty=0` for all items. Should query `order_tracking` or `order_history` instead. |

---

## 5. Architecture Compliance

| Layer | Component | Expected Layer | Actual Layer | Status |
|-------|-----------|----------------|--------------|--------|
| Settings | constants.py (DB_SCHEMA_VERSION) | Settings | `src/settings/` | PASS |
| Infrastructure | np_tracking_repo.py | Infrastructure | `src/infrastructure/database/repos/` | PASS |
| Infrastructure | detected_new_product_repo.py | Infrastructure | `src/infrastructure/database/repos/` | PASS |
| Infrastructure | schema.py | Infrastructure | `src/infrastructure/database/` | PASS |
| Application | new_product_monitor.py | Application | `src/application/services/` | PASS |
| Application | daily_job.py (Phase 1.35) | Application | `src/scheduler/` | PASS |
| Presentation | api_receiving.py | Presentation | `src/web/routes/` | PASS |

**Dependency direction**: All correct.
- Application (NewProductMonitor) imports from Infrastructure (repos) -- OK
- Presentation (api_receiving) imports from Infrastructure (repos) -- OK per project convention
- Scheduler (daily_job) lazy-imports Application (NewProductMonitor) -- OK

---

## 6. Convention Compliance

| Category | Convention | Compliance | Notes |
|----------|-----------|:----------:|-------|
| Class naming | PascalCase | 100% | NewProductMonitor, NewProductDailyTrackingRepository |
| Method naming | snake_case | 100% | get_active_monitoring_items, collect_daily_tracking |
| Constants | UPPER_SNAKE_CASE | 100% | MONITORING_DAYS, STABLE_THRESHOLD_DAYS |
| File naming | snake_case.py | 100% | np_tracking_repo.py, new_product_monitor.py |
| Docstrings | Korean + Args/Returns | 100% | All public methods documented |
| Logger usage | get_logger(__name__) | 100% | No print() |
| DB patterns | BaseRepository + try/finally | 100% | All repos follow pattern |
| Exception handling | logger.warning + continue | 100% | No silent pass |

---

## 7. Check Item Summary

| Section | Total | Exact | Changed | Added | Bug | Missing |
|---------|:-----:|:-----:|:-------:|:-----:|:---:|:-------:|
| 3.1 DB Schema | 15 | 14 | 0 | 1 | 0 | 0 |
| 3.2 NP Tracking Repo | 8 | 8 | 0 | 0 | 0 | 0 |
| 3.3 Detected NP Repo | 9 | 8 | 0 | 1 | 0 | 0 |
| 3.4 __init__.py | 2 | 2 | 0 | 0 | 0 | 0 |
| 3.5 NewProductMonitor | 18 | 16 | 1 | 0 | 1 | 0 |
| 3.6 ImprovedPredictor | 10 | 8 | 2 | 0 | 0 | 0 |
| 3.7 daily_job Phase 1.35 | 6 | 6 | 0 | 0 | 0 | 0 |
| 3.8 Web API | 6 | 6 | 0 | 0 | 0 | 0 |
| 3.9 conftest.py | 2 | 2 | 0 | 0 | 0 | 0 |
| 3.10 Tests | 20 | 20 | 0 | 0 | 0 | 0 |
| **TOTAL** | **96** | **90** | **3** | **2** | **1** | **0** |

---

## 8. Match Rate Calculation

```
Total check items:    96
Exact match:          90
Changed (trivial):     3  (weight 0.5 deduction each)
Added (additive):      2  (no deduction)
Bugs:                  1  (weight 1.0 deduction)
Missing:               0

Deductions: (3 * 0.5 + 1 * 1.0) = 2.5
Match Rate: (96 - 2.5) / 96 = 93.5 / 96 = 97.4%

Rounded: 97%
```

```
+-----------------------------------------------+
|  Overall Match Rate: 97%                PASS   |
+-----------------------------------------------+
|  Exact match:          90 items (93.8%)        |
|  Changed (trivial):     3 items ( 3.1%)        |
|  Added (additive):      2 items ( 2.1%)        |
|  Bugs found:            1 item  ( 1.0%)        |
|  Missing:               0 items ( 0.0%)        |
+-----------------------------------------------+
```

---

## 9. Bug Report

### BUG-1: _get_order_map queries non-existent columns (MEDIUM)

**Location**: `src/application/services/new_product_monitor.py:292-308`

**Problem**:
```python
def _get_order_map(self, date: str) -> Dict[str, int]:
    cursor.execute(
        "SELECT item_cd, order_qty FROM auto_order_items WHERE order_date = ?",
        (date,),
    )
```

The `auto_order_items` table schema:
```sql
CREATE TABLE auto_order_items (
    store_id TEXT,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    detected_at TEXT,
    updated_at TEXT,
    PRIMARY KEY(item_cd)
);
```

Neither `order_date` nor `order_qty` exist in this table.

**Impact**: Query always fails with `sqlite3.OperationalError`, caught by `except Exception: return {}`. All tracking records will have `order_qty=0`. The core lifecycle status transitions and prediction boost are unaffected, but the daily tracking data is incomplete (missing order_qty dimension).

**Recommended Fix**: Query `order_tracking` instead:
```python
cursor.execute(
    "SELECT item_cd, order_qty FROM order_tracking WHERE order_date = ?",
    (date,),
)
```

**Note**: The design document itself specifies `auto_order_items.order_qty` as the data source, so the bug originates from the design. Both design and implementation should reference `order_tracking.order_qty`.

---

## 10. Recommended Actions

### 10.1 Immediate (Bug Fix)

| Priority | Item | File | Description |
|----------|------|------|-------------|
| MEDIUM | Fix _get_order_map SQL | `new_product_monitor.py:300` | Change `auto_order_items` to `order_tracking` |
| LOW | Update design doc data source | `new-product-lifecycle.design.md:114` | Change "auto_order_items: order_qty" to "order_tracking: order_qty" |

### 10.2 Design Document Update

| Item | Current | Should Be |
|------|---------|-----------|
| get_active_monitoring_items statuses | `["detected", "monitoring"]` | `["detected", "monitoring", "stable"]` |
| _apply_new_product_boost params | 4 params | 7 params (add current_stock, pending_qty, safety_stock) |
| Data source for order_qty | auto_order_items | order_tracking |

---

## 11. Test Verification

| Metric | Design | Implementation | Status |
|--------|--------|----------------|--------|
| Total new tests | 20 | 20 | EXACT |
| Monitor tests | 8 | 8 | EXACT |
| Booster tests | 5 | 5 | EXACT |
| Repository tests | 4 | 4 | EXACT |
| API + Schema tests | 3 | 3 | EXACT |
| Total passing (target) | 2294 | TBD | -- |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-26 | Initial gap analysis | gap-detector |
