# waste-slip-migration Analysis Report

> **Analysis Type**: Plan vs Implementation Gap Analysis
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-07
> **Plan Doc**: [waste-slip-migration.plan.md](../01-plan/features/waste-slip-migration.plan.md)
> **Implementation**: `src/infrastructure/database/schema.py`

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that all 6 requirements from the `waste-slip-migration` plan document have been correctly implemented in `schema.py`. This feature addresses DB schema inconsistency across 3 stores (46513, 46704, 47863) where the `waste_slips` table was missing columns and the `promotions` UNIQUE constraint was incorrect.

### 1.2 Analysis Scope

- **Plan Document**: `docs/01-plan/features/waste-slip-migration.plan.md`
- **Implementation File**: `src/infrastructure/database/schema.py` (lines 789-1082)
- **Analysis Date**: 2026-03-07

---

## 2. Gap Analysis (Plan vs Implementation)

### Requirement 1: STORE_SCHEMA waste_slips table (17 columns, nap_plan_ymd included)

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| Table location | STORE_SCHEMA list | Line 789-809 | MATCH |
| Column count | 17 | 17 (id, store_id, chit_date, chit_no, chit_flag, chit_id, chit_id_nm, item_cnt, center_cd, center_nm, wonga_amt, maega_amt, nap_plan_ymd, conf_id, cre_ymdhms, created_at, updated_at) | MATCH |
| nap_plan_ymd | Required | Line 803: `nap_plan_ymd TEXT` | MATCH |
| UNIQUE constraint | `UNIQUE(store_id, chit_date, chit_no)` | Line 808: `UNIQUE(store_id, chit_date, chit_no)` | MATCH |
| wonga_amt type | `REAL DEFAULT 0` | Line 801: `wonga_amt REAL DEFAULT 0` | MATCH |
| maega_amt type | `REAL DEFAULT 0` | Line 802: `maega_amt REAL DEFAULT 0` | MATCH |
| Comment | v33 reference | Line 789: `# waste_slips (--- --- -- v33)` | MATCH |

**Result**: PASS (7/7 items match)

---

### Requirement 2: STORE_SCHEMA waste_slip_items table

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| Table location | STORE_SCHEMA list | Line 811-832 | MATCH |
| Table exists | Required | `CREATE TABLE IF NOT EXISTS waste_slip_items` | MATCH |
| Column: store_id | Required | Line 814: `store_id TEXT NOT NULL` | MATCH |
| Column: chit_date | Required | Line 815: `chit_date TEXT NOT NULL` | MATCH |
| Column: chit_no | Required | Line 816: `chit_no TEXT NOT NULL` | MATCH |
| Column: chit_seq | Required | Line 817: `chit_seq INTEGER` | MATCH |
| Column: item_cd | Required | Line 818: `item_cd TEXT NOT NULL` | MATCH |
| Column: item_nm | Required | Line 819: `item_nm TEXT` | MATCH |
| Column: qty | Required | Line 822: `qty INTEGER DEFAULT 0` | MATCH |
| UNIQUE constraint | Expected | Line 831: `UNIQUE(store_id, chit_date, chit_no, item_cd)` | MATCH |
| Comment | v34 reference | Line 811: `# waste_slip_items (--- --- --- --- -- v34)` | MATCH |

**Result**: PASS (11/11 items match)

---

### Requirement 3: promotions UNIQUE constraint modified to 4 columns

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| Old UNIQUE | `(item_cd, promo_type, start_date)` 3 cols | N/A (replaced) | -- |
| New UNIQUE | `(store_id, item_cd, promo_type, start_date)` 4 cols | Line 350: `UNIQUE(store_id, item_cd, promo_type, start_date)` | MATCH |

**Result**: PASS (1/1 items match)

---

### Requirement 4: _STORE_COLUMN_PATCHES waste_slips 4 ALTER statements

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| ALTER nap_plan_ymd | `ALTER TABLE waste_slips ADD COLUMN nap_plan_ymd TEXT` | Line 1009 | MATCH |
| ALTER conf_id | `ALTER TABLE waste_slips ADD COLUMN conf_id TEXT` | Line 1010 | MATCH |
| ALTER cre_ymdhms | `ALTER TABLE waste_slips ADD COLUMN cre_ymdhms TEXT` | Line 1011 | MATCH |
| ALTER updated_at | `ALTER TABLE waste_slips ADD COLUMN updated_at TEXT` | Line 1012 | MATCH |
| Comment | Expected | Line 1008: `# waste_slips --- --- --- (v33 --- --- --- ---)` | MATCH |

**Result**: PASS (5/5 items match)

---

### Requirement 5: _fix_promotions_unique function

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| Function exists | Required | Line 1018: `def _fix_promotions_unique(cursor) -> None:` | MATCH |
| Checks existing UNIQUE | Required | Line 1024-1031: queries `sqlite_master` and checks for `store_id` in UNIQUE | MATCH |
| Creates fixed table | Required | Line 1036-1049: `CREATE TABLE IF NOT EXISTS promotions_fixed` with 4-col UNIQUE | MATCH |
| Data preservation | `INSERT INTO ... SELECT` pattern | Line 1050-1057: `INSERT OR IGNORE INTO promotions_fixed SELECT ...` | MATCH |
| Drop old table | Required | Line 1058: `cursor.execute("DROP TABLE promotions")` | MATCH |
| Rename to promotions | Required | Line 1059: `ALTER TABLE promotions_fixed RENAME TO promotions` | MATCH |
| Called from patches | Required | Line 1081: `_fix_promotions_unique(cursor)` in `_apply_store_column_patches` | MATCH |
| Error handling | Expected | Line 1061-1062: `except Exception as e: logger.warning(...)` | MATCH |

**Result**: PASS (8/8 items match)

---

### Requirement 6: STORE_INDEXES for waste_slips and waste_slip_items

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| waste_slips index | `idx_waste_slips_store_date ON waste_slips(store_id, chit_date)` | Line 935: exact match | MATCH |
| waste_slip_items index (store_date) | Expected | Line 937: `idx_wsi_store_date ON waste_slip_items(store_id, chit_date)` | MATCH |
| waste_slip_items index (item) | Expected | Line 938: `idx_wsi_item ON waste_slip_items(item_cd)` | MATCH |

**Result**: PASS (3/3 items match)

---

## 3. Positive Additions (Implementation has, Plan does not mention)

| Item | Implementation Location | Description | Impact |
|------|------------------------|-------------|--------|
| waste_slip_items extra indexes | Lines 937-938 | Two indexes for waste_slip_items (store_date, item_cd) beyond the plan's single waste_slips index | Positive: faster queries |
| waste_verification_log table | Lines 834-848 | Also present in STORE_SCHEMA (was already there, not part of this migration) | Neutral: pre-existing |
| Type correction comment | Line 1013-1014 | Note about `INTEGER -> REAL` type correction for waste_slips amounts | Positive: documentation |

These are all positive additions that do not conflict with the plan.

---

## 4. Live Verification Results

As confirmed by the user prior to this analysis:

| Store | waste_slips cols | nap_plan_ymd | promotions UNIQUE | Data preserved |
|:-----:|:----------------:|:------------:|:-----------------:|:--------------:|
| 46513 | 17 | Yes | (store_id, item_cd, promo_type, start_date) | 5,067 rows |
| 46704 | 17 | Yes | (store_id, item_cd, promo_type, start_date) | 3,763 rows |
| 47863 | 17 | Yes | (store_id, item_cd, promo_type, start_date) | Confirmed |

All 3 stores verified successfully.

---

## 5. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 100%                    |
+---------------------------------------------+
|  Requirement 1 (waste_slips table):    7/7   |
|  Requirement 2 (waste_slip_items):    11/11  |
|  Requirement 3 (promotions UNIQUE):    1/1   |
|  Requirement 4 (column patches):       5/5   |
|  Requirement 5 (fix function):         8/8   |
|  Requirement 6 (indexes):             3/3    |
+---------------------------------------------+
|  Total:  35/35 items  PASS                   |
|  Positive additions:  3 (no conflicts)       |
+---------------------------------------------+
```

---

## 6. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Plan Match | 100% | PASS |
| Schema Correctness | 100% | PASS |
| Data Safety | 100% | PASS (INSERT OR IGNORE + DROP + RENAME pattern) |
| Live Verification | 100% | PASS (3/3 stores confirmed) |
| **Overall** | **100%** | **PASS** |

---

## 7. Risk Mitigation Verification

The plan identified 3 risks. Verification of mitigation:

| Risk | Mitigation in Implementation | Status |
|------|------------------------------|--------|
| promotions data loss during recreation | `INSERT OR IGNORE INTO promotions_fixed ... SELECT ... FROM promotions` (L1050-1057) | Mitigated |
| 47863 waste_slips already exists with 13 cols | `_STORE_COLUMN_PATCHES` 4x ALTER TABLE (L1009-1012) + `CREATE TABLE IF NOT EXISTS` skips existing | Mitigated |
| Future stores need correct schema | `STORE_SCHEMA` now contains full 17-col waste_slips definition (L790-809) | Mitigated |

---

## 8. Recommended Actions

None required. All plan requirements are fully implemented and verified.

---

## 9. Conclusion

The `waste-slip-migration` implementation achieves a **100% match rate** against the plan document. All 6 requirements (waste_slips table, waste_slip_items table, promotions UNIQUE fix, column patches, fix function, indexes) are correctly implemented in `schema.py`. Live verification confirms all 3 stores (46513, 46704, 47863) now have consistent schema with data preserved.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-07 | Initial analysis | gap-detector |
