# calibrator-unique-fix Analysis Report

> **Analysis Type**: Design vs Implementation Gap Analysis
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-09
> **Design Doc**: [calibrator-unique-fix.design.md](../02-design/features/calibrator-unique-fix.design.md)
> **Implementation**: `src/infrastructure/database/schema.py`

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the `calibrator-unique-fix` implementation in `schema.py` matches the design document specification for fixing the `food_waste_calibration` UNIQUE constraint bug. The design specifies 3 changes (2-A, 2-B, 2-C) limited to a single file, plus a "no changes" list for 3 other files.

### 1.2 Analysis Scope

- **Design Document**: `bgf_auto/docs/02-design/features/calibrator-unique-fix.design.md`
- **Implementation File**: `bgf_auto/src/infrastructure/database/schema.py`
- **Verification Files** (no changes expected): `food_waste_calibrator.py`, `food.py`, `food_daily_cap.py`
- **Test Files**: `bgf_auto/tests/test_calibrator_schema_fix.py` (pre-existing, not feature-specific)
- **Analysis Date**: 2026-03-09

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Change 2-A: UNIQUE Constraint in CREATE TABLE (schema.py:755)

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Location | schema.py:755 | schema.py:755 | MATCH |
| Old UNIQUE | `(store_id, mid_cd, calibration_date)` | N/A (already changed) | MATCH |
| New UNIQUE | `(store_id, mid_cd, small_cd, calibration_date)` | `UNIQUE(store_id, mid_cd, small_cd, calibration_date)` | MATCH |
| Table comment | v32, small_cd v48 | `# food_waste_calibration (... v32, small_cd v48)` | MATCH |
| Column order | small_cd before UNIQUE | `small_cd TEXT DEFAULT ''` at line 754, UNIQUE at 755 | MATCH |

**Verdict**: 5/5 items match. The CREATE TABLE statement exactly matches the design specification.

### 2.2 Change 2-B: Migration Function `_fix_calibration_unique(cursor)`

| # | Design Requirement | Implementation (lines 1105-1165) | Status |
|---|-------------------|----------------------------------|--------|
| B1 | Function name `_fix_calibration_unique(cursor)` | `def _fix_calibration_unique(cursor) -> None:` | MATCH |
| B2 | Docstring: UNIQUE change description | Lines 1106-1110: exact docstring match | MATCH |
| B3 | Step 1: `sqlite_master` query for current CREATE SQL | Lines 1113-1115: `SELECT sql FROM sqlite_master WHERE type='table' AND name='food_waste_calibration'` | MATCH |
| B4 | Step 2: Return if table not found | Lines 1116-1117: `if not row: return` | MATCH |
| B5 | Step 2: Idempotency check (small_cd already in UNIQUE) | Line 1120: `if "store_id, mid_cd, small_cd, calibration_date" in create_sql: return` | MATCH |
| B6 | Logger info message | Line 1122: `logger.info("food_waste_calibration ... UNIQUE ... small_cd ...")` | MATCH |
| B7 | Step 3: CREATE fixed table with all 16 columns | Lines 1123-1143: all 16 columns present | MATCH |
| B8 | Fixed table UNIQUE includes small_cd | Line 1142: `UNIQUE(store_id, mid_cd, small_cd, calibration_date)` | MATCH |
| B9 | Step 4: INSERT OR IGNORE with COALESCE(small_cd, '') | Lines 1146-1159: SELECT with `COALESCE(small_cd, '')` | MATCH |
| B10 | Step 4: Contamination filter `WHERE NOT (small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0)` | Line 1159: exact match | MATCH |
| B11 | Step 5: DROP original table | Line 1161: `DROP TABLE food_waste_calibration` | MATCH |
| B12 | Step 5: RENAME fixed to original | Line 1162: `ALTER TABLE food_waste_calibration_fixed RENAME TO food_waste_calibration` | MATCH |
| B13 | Logger info on completion | Line 1163: `logger.info("food_waste_calibration ... UNIQUE ... 완료")` | MATCH |
| B14 | Exception handling: warning + skip | Lines 1164-1165: `except Exception as e: logger.warning(...)` | MATCH |
| B15 | Follows `_fix_promotions_unique` pattern | Structural match: try/sqlite_master/check/CREATE/INSERT/DROP/RENAME/except | MATCH |

**Column-by-column comparison of CREATE TABLE in migration:**

| Column | Design | Implementation | Match |
|--------|--------|----------------|:-----:|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | INTEGER PRIMARY KEY AUTOINCREMENT | YES |
| store_id | TEXT NOT NULL | TEXT NOT NULL | YES |
| mid_cd | TEXT NOT NULL | TEXT NOT NULL | YES |
| calibration_date | TEXT NOT NULL | TEXT NOT NULL | YES |
| actual_waste_rate | REAL NOT NULL | REAL NOT NULL | YES |
| target_waste_rate | REAL NOT NULL | REAL NOT NULL | YES |
| error | REAL NOT NULL | REAL NOT NULL | YES |
| sample_days | INTEGER NOT NULL | INTEGER NOT NULL | YES |
| total_order_qty | INTEGER | INTEGER | YES |
| total_waste_qty | INTEGER | INTEGER | YES |
| total_sold_qty | INTEGER | INTEGER | YES |
| param_name | TEXT | TEXT | YES |
| old_value | REAL | REAL | YES |
| new_value | REAL | REAL | YES |
| current_params | TEXT | TEXT | YES |
| created_at | TEXT NOT NULL | TEXT NOT NULL | YES |
| small_cd | TEXT DEFAULT '' | TEXT DEFAULT '' | YES |

**INSERT OR IGNORE column-by-column comparison:**

| Column Position | Design | Implementation | Match |
|----------------|--------|----------------|:-----:|
| Column list (16) | store_id, mid_cd, calibration_date, actual_waste_rate, target_waste_rate, error, sample_days, total_order_qty, total_waste_qty, total_sold_qty, param_name, old_value, new_value, current_params, created_at, small_cd | Exact match | YES |
| SELECT list | Same columns with COALESCE(small_cd, '') | Exact match | YES |
| WHERE filter | `NOT (small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0)` | Exact match | YES |

**Verdict**: 15/15 items match. The migration function is an exact implementation of the design.

### 2.3 Change 2-C: Call Site in `_apply_store_column_patches`

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Function | `_apply_store_column_patches(cursor)` | Line 1168: `def _apply_store_column_patches(cursor)` | MATCH |
| Call after `_fix_promotions_unique` | `_fix_calibration_unique(cursor)` | Line 1185: `_fix_calibration_unique(cursor)` | MATCH |
| Call order | `_fix_promotions_unique` then `_fix_calibration_unique` | Lines 1184-1185: exact order | MATCH |

**Verdict**: 3/3 items match.

### 2.4 Section 3: Idempotency Guarantee

| # | Design Requirement | Implementation | Status |
|---|-------------------|----------------|--------|
| I1 | 1st run: migration executes | Lines 1112-1162: full migration path | MATCH |
| I2 | 2nd+ run: skip if small_cd already in UNIQUE | Line 1120: `if "store_id, mid_cd, small_cd, calibration_date" in create_sql: return` | MATCH |
| I3 | Detection string: `"store_id, mid_cd, small_cd, calibration_date"` | Line 1120: exact string | MATCH |

**Verdict**: 3/3 items match.

### 2.5 Section 4: No Changes Verification

| File | Design: No Changes | Implementation Verified | Status |
|------|-------------------|------------------------|--------|
| `food_waste_calibrator.py` | No modification | Grep: 0 references to `_fix_calibration_unique` or `calibration_unique` | MATCH |
| `food.py` (strategies) | No modification | Grep: 0 references | MATCH |
| `food_daily_cap.py` | No modification | Grep: 0 references | MATCH |
| `idx_food_waste_cal_small_cd` index | Existing index preserved | Line 959: index still present | MATCH |

**Verification of existing query compatibility:**
- `get_calibrated_food_params()` at line 101: still queries with `small_cd='' OR NULL` filter -- UNCHANGED
- `INSERT OR REPLACE` logic in calibrator: compatible with new 4-column UNIQUE -- UNCHANGED

**Verdict**: 4/4 items match. No unintended changes to adjacent files.

### 2.6 Section 5: Implementation Order

| Step | Design | Implementation Status |
|------|--------|----------------------|
| Step 1 | schema.py:755 UNIQUE modify (1 line) | DONE: line 755 |
| Step 2 | `_fix_calibration_unique()` add (~40 lines) | DONE: lines 1105-1165 (61 lines) |
| Step 3 | `_apply_store_column_patches` call add (1 line) | DONE: line 1185 |
| Step 4 | Test writing and execution | PARTIAL (see Section 3 below) |
| Step 5 | 46704/47863 verification | N/A (operational verification) |

**Note on Step 2 line count**: Design estimated ~40 lines; implementation is 61 lines. The difference is due to the full exception handling block and logging statements which follow the `_fix_promotions_unique` pattern exactly. This is a cosmetic difference, not a functional gap.

### 2.7 Section 6: Test Design

| # | Test Name (Design) | Implementation Status | Status |
|---|--------------------|-----------------------|--------|
| T1 | `test_unique_constraint_allows_different_small_cd` | NOT FOUND in any test file | MISSING |
| T2 | `test_migration_preserves_valid_data` | NOT FOUND | MISSING |
| T3 | `test_migration_removes_contaminated_rows` | NOT FOUND | MISSING |
| T4 | `test_migration_idempotent` | NOT FOUND | MISSING |
| T5 | `test_get_calibrated_params_after_migration` | NOT FOUND | MISSING |
| T6 | `test_calibrate_saves_both_phases` | NOT FOUND | MISSING |
| T7 | `test_init_store_db_creates_correct_unique` | NOT FOUND (pre-existing `test_creates_tables_in_empty_db` covers table creation but not UNIQUE verification) | MISSING |
| T8 | `test_existing_store_db_migrated` | NOT FOUND | MISSING |

**Pre-existing test coverage** (`test_calibrator_schema_fix.py`):
- `test_food_waste_calibration_in_schema` -- verifies DDL text contains table name
- `test_indexes_contain_calibration_small_cd` -- verifies index exists
- `test_creates_tables_in_empty_db` -- verifies table creation
- `test_idempotent_init` -- verifies double-init safety
- `test_food_waste_calibration_has_small_cd` -- verifies small_cd column exists

These pre-existing tests provide **partial indirect coverage** for T1 (small_cd column exists) and T7 (table creation), but do NOT specifically test the new UNIQUE constraint behavior, migration data preservation, or contamination row removal.

**Verdict**: 0/8 design-specified tests explicitly implemented. However, the design document Section 6 header says "Test Design" -- these were test *plans*, and the feature description notes tests were "not explicitly required in the Do phase."

---

## 3. Overall Scores

| Category | Items | Matched | Score | Status |
|----------|:-----:|:-------:|:-----:|:------:|
| 2-A: CREATE TABLE UNIQUE | 5 | 5 | 100% | PASS |
| 2-B: Migration Function | 15 | 15 | 100% | PASS |
| 2-C: Call Site | 3 | 3 | 100% | PASS |
| Section 3: Idempotency | 3 | 3 | 100% | PASS |
| Section 4: No Changes | 4 | 4 | 100% | PASS |
| Section 5: Impl Order | 5 | 4 | 80% | PASS (Step 4 partial) |
| Section 6: Tests (T1-T8) | 8 | 0 | 0% | INFO (not required in Do) |
| **Core Implementation** | **30** | **30** | **100%** | **PASS** |

### Match Rate Calculation

The core implementation (Sections 2-A through 2-C plus Sections 3-4) consists of **30 checklist items**, all of which match exactly. Tests (Section 6) were explicitly noted as not required during the Do phase.

```
Core Match Rate: 30/30 = 100%

Including tests (informational):
  30 core matches + 0 test matches out of 30 + 8 = 38 total
  Full rate: 30/38 = 78.9%

Adjusted Match Rate (tests excluded per Do phase scope): 100%
```

---

## 4. Differences Found

### MISSING Features (Design O, Implementation X)

| Item | Design Location | Description | Impact |
|------|-----------------|-------------|--------|
| T1-T8 Tests | Section 6 | 8 unit/integration tests not implemented | Low (explicitly excluded from Do phase) |

### ADDED Features (Design X, Implementation O)

None found. Implementation is strictly scoped to the design.

### CHANGED Features (Design != Implementation)

| Item | Design | Implementation | Impact |
|------|--------|----------------|--------|
| Migration function line count | ~40 lines (Section 5, Step 2) | 61 lines (1105-1165) | None (cosmetic, includes standard exception pattern) |

---

## 5. Detailed Verification

### 5.1 SQL Exact Match: Migration CREATE TABLE

Design (Section 2-B, lines 63-83) vs Implementation (schema.py lines 1124-1143):

**Design:**
```sql
CREATE TABLE IF NOT EXISTS food_waste_calibration_fixed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    mid_cd TEXT NOT NULL,
    calibration_date TEXT NOT NULL,
    actual_waste_rate REAL NOT NULL,
    target_waste_rate REAL NOT NULL,
    error REAL NOT NULL,
    sample_days INTEGER NOT NULL,
    total_order_qty INTEGER,
    total_waste_qty INTEGER,
    total_sold_qty INTEGER,
    param_name TEXT,
    old_value REAL,
    new_value REAL,
    current_params TEXT,
    created_at TEXT NOT NULL,
    small_cd TEXT DEFAULT '',
    UNIQUE(store_id, mid_cd, small_cd, calibration_date)
)
```

**Implementation** (schema.py:1124-1143):
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS food_waste_calibration_fixed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        mid_cd TEXT NOT NULL,
        calibration_date TEXT NOT NULL,
        actual_waste_rate REAL NOT NULL,
        target_waste_rate REAL NOT NULL,
        error REAL NOT NULL,
        sample_days INTEGER NOT NULL,
        total_order_qty INTEGER,
        total_waste_qty INTEGER,
        total_sold_qty INTEGER,
        param_name TEXT,
        old_value REAL,
        new_value REAL,
        current_params TEXT,
        created_at TEXT NOT NULL,
        small_cd TEXT DEFAULT '',
        UNIQUE(store_id, mid_cd, small_cd, calibration_date)
    )
""")
```

**Result**: Character-for-character match on all 17 columns and UNIQUE constraint.

### 5.2 SQL Exact Match: Data Copy with Contamination Filter

Design (Section 2-B, lines 87-100) vs Implementation (schema.py lines 1146-1159):

**Design:**
```sql
INSERT OR IGNORE INTO food_waste_calibration_fixed
    (store_id, mid_cd, calibration_date,
     actual_waste_rate, target_waste_rate, error,
     sample_days, total_order_qty, total_waste_qty, total_sold_qty,
     param_name, old_value, new_value,
     current_params, created_at, small_cd)
SELECT store_id, mid_cd, calibration_date,
       actual_waste_rate, target_waste_rate, error,
       sample_days, total_order_qty, total_waste_qty, total_sold_qty,
       param_name, old_value, new_value,
       current_params, created_at, COALESCE(small_cd, '')
FROM food_waste_calibration
WHERE NOT (small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0)
```

**Implementation** (schema.py:1146-1159):
```python
cursor.execute("""
    INSERT OR IGNORE INTO food_waste_calibration_fixed
        (store_id, mid_cd, calibration_date,
         actual_waste_rate, target_waste_rate, error,
         sample_days, total_order_qty, total_waste_qty, total_sold_qty,
         param_name, old_value, new_value,
         current_params, created_at, small_cd)
    SELECT store_id, mid_cd, calibration_date,
           actual_waste_rate, target_waste_rate, error,
           sample_days, total_order_qty, total_waste_qty, total_sold_qty,
           param_name, old_value, new_value,
           current_params, created_at, COALESCE(small_cd, '')
    FROM food_waste_calibration
    WHERE NOT (small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0)
""")
```

**Result**: Exact match on INSERT column list, SELECT column list, COALESCE usage, and WHERE NOT contamination filter.

### 5.3 Pattern Compliance: `_fix_promotions_unique` Reference

| Pattern Element | `_fix_promotions_unique` | `_fix_calibration_unique` | Match |
|----------------|--------------------------|---------------------------|:-----:|
| try/except wrapper | Lines 1063-1102 | Lines 1112-1165 | YES |
| sqlite_master query | Line 1064-1065 | Line 1113-1114 | YES |
| Early return if not found | Line 1067-1068 | Line 1116-1117 | YES |
| Idempotency string check | Line 1071 | Line 1120 | YES |
| logger.info before | Line 1074 | Line 1122 | YES |
| CREATE _fixed table | Line 1075-1088 | Line 1123-1143 | YES |
| INSERT OR IGNORE | Line 1090-1097 | Line 1146-1159 | YES |
| DROP original | Line 1098 | Line 1161 | YES |
| ALTER RENAME | Line 1099 | Line 1162 | YES |
| logger.info after | Line 1100 | Line 1163 | YES |
| except + logger.warning | Lines 1101-1102 | Lines 1164-1165 | YES |

**Result**: 11/11 structural pattern elements match. The new function is a faithful reproduction of the `_fix_promotions_unique` pattern with appropriate table-specific modifications.

---

## 6. Architecture and Convention Compliance

### 6.1 Layer Placement

| Item | Expected Layer | Actual Location | Status |
|------|---------------|-----------------|--------|
| `_fix_calibration_unique` | Infrastructure (database/schema) | `src/infrastructure/database/schema.py` | PASS |

### 6.2 Naming Convention

| Item | Convention | Actual | Status |
|------|-----------|--------|--------|
| Function name | snake_case, private prefix | `_fix_calibration_unique` | PASS |
| Logger messages | Korean, descriptive | "food_waste_calibration ... UNIQUE ... small_cd ..." | PASS |

### 6.3 Error Handling Convention

| Item | Convention | Actual | Status |
|------|-----------|--------|--------|
| Exception pattern | `except Exception as e: logger.warning(...)` | Lines 1164-1165 | PASS |
| Fail-safe behavior | Skip on error, do not crash | Warning logged, migration skipped | PASS |

---

## 7. Summary

```
+-----------------------------------------------+
|  calibrator-unique-fix Gap Analysis            |
+-----------------------------------------------+
|  Match Rate:    100% (30/30 core items)        |
|  Verdict:       PASS                           |
+-----------------------------------------------+
|  Core Changes:                                 |
|    2-A (UNIQUE constraint):    5/5   MATCH     |
|    2-B (Migration function):  15/15  MATCH     |
|    2-C (Call site):            3/3   MATCH     |
|    Idempotency:                3/3   MATCH     |
|    No-changes guard:           4/4   MATCH     |
+-----------------------------------------------+
|  Tests: 0/8 (excluded from Do phase scope)     |
|  Added features: 0                             |
|  Changed features: 0 functional                |
+-----------------------------------------------+
|  Files modified: 1 (schema.py)                 |
|  Lines added: ~61                              |
|  Adjacent files verified unchanged: 3          |
+-----------------------------------------------+
```

---

## 8. Recommended Actions

### Immediate Actions

None required. The core implementation is a 100% match with the design specification.

### Optional (Low Priority)

1. **T1-T8 Tests**: Consider implementing the 8 tests from Section 6 to verify migration behavior at the unit test level. Pre-existing tests in `test_calibrator_schema_fix.py` provide indirect coverage for table creation and column existence but do not specifically test:
   - UNIQUE constraint allows coexistence of different `small_cd` values (T1)
   - Migration preserves valid data while removing contaminated rows (T2, T3)
   - Migration idempotency under repeated execution (T4)
   - End-to-end calibration after migration (T5, T6)

2. **Operational Verification**: Run the migration against production stores (46704, 47863) and confirm that the calibrator re-execution produces correct per-small_cd results.

---

## 9. Design Document Updates Needed

None. The implementation is faithful to the design; no design document updates are required.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-09 | Initial gap analysis | gap-detector |
