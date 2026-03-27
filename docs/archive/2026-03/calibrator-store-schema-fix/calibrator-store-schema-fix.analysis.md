# calibrator-store-schema-fix Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-03
> **Design Doc**: [calibrator-store-schema-fix.design.md](../02-design/features/calibrator-store-schema-fix.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

STORE_SCHEMA에 누락된 2개 테이블(food_waste_calibration, waste_verification_log) DDL 추가 및
silent failure 경고 로그 전환이 설계 문서와 일치하는지 검증한다.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/calibrator-store-schema-fix.design.md`
- **Implementation Files**:
  - `src/infrastructure/database/schema.py`
  - `src/prediction/food_waste_calibrator.py`
  - `tests/test_calibrator_schema_fix.py`
- **Analysis Date**: 2026-03-03

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## 3. Gap Analysis (Design vs Implementation)

### 3.1 STORE_SCHEMA: food_waste_calibration DDL (Design Item 1)

| Item | Design | Implementation | Status |
|------|--------|----------------|:------:|
| Table name | food_waste_calibration | food_waste_calibration | MATCH |
| Location | STORE_SCHEMA list, after substitution_events | schema.py L725-745 (after substitution_events L699-723) | MATCH |
| Column count | 17 columns | 17 columns | MATCH |
| Column: id | INTEGER PRIMARY KEY AUTOINCREMENT | INTEGER PRIMARY KEY AUTOINCREMENT | MATCH |
| Column: store_id | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| Column: mid_cd | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| Column: calibration_date | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| Column: actual_waste_rate | REAL NOT NULL | REAL NOT NULL | MATCH |
| Column: target_waste_rate | REAL NOT NULL | REAL NOT NULL | MATCH |
| Column: error | REAL NOT NULL | REAL NOT NULL | MATCH |
| Column: sample_days | INTEGER NOT NULL | INTEGER NOT NULL | MATCH |
| Column: total_order_qty | INTEGER | INTEGER | MATCH |
| Column: total_waste_qty | INTEGER | INTEGER | MATCH |
| Column: total_sold_qty | INTEGER | INTEGER | MATCH |
| Column: param_name | TEXT | TEXT | MATCH |
| Column: old_value | REAL | REAL | MATCH |
| Column: new_value | REAL | REAL | MATCH |
| Column: current_params | TEXT | TEXT | MATCH |
| Column: created_at | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| Column: small_cd | TEXT DEFAULT '' | TEXT DEFAULT '' | MATCH |
| UNIQUE constraint | (store_id, mid_cd, calibration_date) | (store_id, mid_cd, calibration_date) | MATCH |
| Comment | v32, small_cd v48 | v32, small_cd v48 | MATCH |

**Result**: 22/22 items match. DDL is character-for-character identical.

### 3.2 STORE_SCHEMA: waste_verification_log DDL (Design Item 2)

| Item | Design | Implementation | Status |
|------|--------|----------------|:------:|
| Table name | waste_verification_log | waste_verification_log | MATCH |
| Location | After food_waste_calibration in STORE_SCHEMA | schema.py L747-761 (after food_waste_calibration) | MATCH |
| Column count | 11 columns | 11 columns | MATCH |
| Column: id | INTEGER PRIMARY KEY AUTOINCREMENT | INTEGER PRIMARY KEY AUTOINCREMENT | MATCH |
| Column: store_id | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| Column: verification_date | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| Column: slip_count | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | MATCH |
| Column: slip_item_count | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | MATCH |
| Column: daily_sales_disuse_count | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | MATCH |
| Column: gap | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | MATCH |
| Column: gap_percentage | REAL DEFAULT 0 | REAL DEFAULT 0 | MATCH |
| Column: status | TEXT NOT NULL DEFAULT 'UNKNOWN' | TEXT NOT NULL DEFAULT 'UNKNOWN' | MATCH |
| Column: details | TEXT | TEXT | MATCH |
| Column: created_at | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| UNIQUE constraint | (store_id, verification_date) | (store_id, verification_date) | MATCH |
| Comment | v33 | v33 | MATCH |

**Result**: 16/16 items match. DDL is character-for-character identical.

### 3.3 STORE_INDEXES: 3 Indexes (Design Item 3)

| Index Name | Design | Implementation (schema.py) | Status |
|------------|--------|---------------------------|:------:|
| idx_food_waste_cal_store_mid | food_waste_calibration(store_id, mid_cd, calibration_date) | L845: identical | MATCH |
| idx_food_waste_cal_small_cd | food_waste_calibration(store_id, mid_cd, small_cd, calibration_date) | L846: identical | MATCH |
| idx_waste_verify_store_date | waste_verification_log(store_id, verification_date) | L848: identical | MATCH |
| Location | After idx_substitution_small_cd | After idx_substitution_small_cd (L843) | MATCH |

**Result**: 4/4 items match. All index definitions and placement are identical.

### 3.4 food_waste_calibrator.py: Silent Failure to Warning Log (Design Item 4)

| Item | Design | Implementation (L175-181) | Status |
|------|--------|--------------------------|:------:|
| Exception variable | `as e` | `as e` | MATCH |
| Condition check | `if "no such table" in str(e)` | `if "no such table" in str(e)` | MATCH |
| Log level | `logger.warning` | `logger.warning` | MATCH |
| Message prefix | `[폐기율보정]` | `[폐기율보정]` | MATCH |
| Message content | `food_waste_calibration 테이블 누락` | `food_waste_calibration 테이블 누락` | MATCH |
| Store parameter | `(store={store_id})` | `(store={store_id})` | MATCH |
| Action hint | `init_store_db() 재실행 필요` | `init_store_db() 재실행 필요` | MATCH |
| Return value | `return None` | `return None` | MATCH |

**Result**: 8/8 items match. The exception handler is character-for-character identical.

### 3.5 Test Scenarios (Design Item 5)

| # | Design Scenario | Test Class / Method | Verified |
|---|----------------|---------------------|:--------:|
| 1 | STORE_SCHEMA에 food_waste_calibration DDL 포함 | `TestStoreSchemaContainsTables.test_food_waste_calibration_in_schema` | MATCH |
| 2 | STORE_SCHEMA에 waste_verification_log DDL 포함 | `TestStoreSchemaContainsTables.test_waste_verification_log_in_schema` | MATCH |
| 3 | STORE_INDEXES에 3개 인덱스 포함 | `TestStoreSchemaContainsTables.test_indexes_contain_calibration` + `test_indexes_contain_calibration_small_cd` + `test_indexes_contain_waste_verify` | MATCH |
| 4 | init_store_db()로 빈 DB에 테이블 생성 | `TestInitStoreDbCreation.test_creates_tables_in_empty_db` | MATCH |
| 5 | init_store_db() 중복 실행 시 에러 없음 | `TestInitStoreDbCreation.test_idempotent_init` | MATCH |
| 6 | food_waste_calibration에 small_cd 컬럼 포함 | `TestInitStoreDbCreation.test_food_waste_calibration_has_small_cd` | MATCH |
| 7 | 테이블 없을 때 warning 로그 출력 | `TestSilentFailureWarning.test_warning_on_missing_table` | MATCH |

**Result**: 7/7 design scenarios implemented. Scenario 3 is split into 3 granular test methods (3-1, 3-2, 3-3), totaling 9 test methods -- this is a positive refinement.

---

## 4. Differences Found

### Missing Features (Design O, Implementation X)

None.

### Added Features (Design X, Implementation O)

None.

### Changed Features (Design != Implementation)

None.

---

## 5. Match Rate Summary

```
Total Design Items:    5 (major categories)
  Detailed Sub-items: 57 (columns, indexes, code lines, test scenarios)
  Matching:           57 / 57
  Missing:             0
  Changed:             0

Match Rate:          100%
Verdict:             PASS
```

---

## 6. Files Analyzed

| File | Path | Lines | Role |
|------|------|------:|------|
| schema.py | `src/infrastructure/database/schema.py` | 949 | STORE_SCHEMA + STORE_INDEXES DDL definitions |
| food_waste_calibrator.py | `src/prediction/food_waste_calibrator.py` | 1134 | Silent OperationalError -> logger.warning |
| test_calibrator_schema_fix.py | `tests/test_calibrator_schema_fix.py` | 133 | 9 test methods covering 7 design scenarios |

---

## 7. Recommended Actions

No actions required. All 5 design items are implemented exactly as specified.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-03 | Initial gap analysis | gap-detector |
