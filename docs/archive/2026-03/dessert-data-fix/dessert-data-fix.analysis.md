# dessert-data-fix Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-04
> **Design Doc**: [dessert-data-fix.design.md](../02-design/features/dessert-data-fix.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Design 문서(dessert-data-fix.design.md)에 정의된 3개 버그 수정(Fix 1/2/3)과 10개 테스트 케이스가 실제 구현 코드에 정확히 반영되었는지 검증한다.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/dessert-data-fix.design.md`
- **Implementation Files**:
  - `src/infrastructure/database/repos/dessert_decision_repo.py` (Fix 1, 2, 3)
  - `tests/test_dessert_data_fix.py` (10개 테스트)
- **Analysis Date**: 2026-03-04

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

### 3.1 Fix 1: `get_weekly_trend()` -- strftime `%%W` to `%W`

**Design (Section 2, Fix 1)**:
```python
CAST(strftime('%W', judgment_period_end) AS INTEGER) as week_num,
```

**Implementation (`dessert_decision_repo.py` L367)**:
```python
CAST(strftime('%W', judgment_period_end) AS INTEGER) as week_num,
```

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| strftime format | `'%W'` | `'%W'` | MATCH |
| Location | L366 | L367 (1-indexed) | MATCH (offset due to docstring) |

**Result**: MATCH -- The `%%W` literal percent bug has been fixed to `%W`.

---

### 3.2 Fix 2: `get_pending_stop_count()` -- MAX(id) to MAX(judgment_period_end)

**Design (Section 2, Fix 2 After)**:
```sql
SELECT item_cd, MAX(judgment_period_end) as max_date
...
) latest ON d.item_cd = latest.item_cd
           AND d.judgment_period_end = latest.max_date
```

**Implementation (`dessert_decision_repo.py` L401-414)**:
```python
cursor = conn.execute("""
    SELECT COUNT(*)
    FROM dessert_decisions d
    INNER JOIN (
        SELECT item_cd, MAX(judgment_period_end) as max_date
        FROM dessert_decisions
        WHERE store_id = ?
        GROUP BY item_cd
    ) latest ON d.item_cd = latest.item_cd
               AND d.judgment_period_end = latest.max_date
    WHERE d.store_id = ?
      AND d.decision = 'STOP_RECOMMEND'
      AND d.operator_action IS NULL
""", (sid, sid))
```

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Subquery aggregate | `MAX(judgment_period_end) as max_date` | `MAX(judgment_period_end) as max_date` | MATCH |
| JOIN condition (1) | `d.item_cd = latest.item_cd` | `d.item_cd = latest.item_cd` | MATCH |
| JOIN condition (2) | `d.judgment_period_end = latest.max_date` | `d.judgment_period_end = latest.max_date` | MATCH |
| WHERE clauses | `STOP_RECOMMEND` + `operator_action IS NULL` | identical | MATCH |
| Parameters | `(sid, sid)` | `(sid, sid)` | MATCH |

**Result**: MATCH -- `MAX(id) as max_id` + `ON d.id = latest.max_id` has been replaced with `MAX(judgment_period_end)` pattern.

---

### 3.3 Fix 3: `batch_update_operator_action()` -- MAX(id) to MAX(judgment_period_end)

**Design (Section 2, Fix 3 After)**:
```sql
SELECT item_cd, MAX(judgment_period_end) as max_date
...
) latest ON d.item_cd = latest.item_cd
           AND d.judgment_period_end = latest.max_date
```

**Implementation (`dessert_decision_repo.py` L309-323)**:
```python
cursor = conn.execute(f"""
    SELECT d.id, d.item_cd
    FROM dessert_decisions d
    INNER JOIN (
        SELECT item_cd, MAX(judgment_period_end) as max_date
        FROM dessert_decisions
        WHERE store_id = ?
        GROUP BY item_cd
    ) latest ON d.item_cd = latest.item_cd
               AND d.judgment_period_end = latest.max_date
    WHERE d.store_id = ?
      AND d.item_cd IN ({placeholders})
      AND d.decision = 'STOP_RECOMMEND'
      AND d.operator_action IS NULL
""", [sid, sid] + list(item_cds))
```

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Subquery aggregate | `MAX(judgment_period_end) as max_date` | `MAX(judgment_period_end) as max_date` | MATCH |
| JOIN condition (1) | `d.item_cd = latest.item_cd` | `d.item_cd = latest.item_cd` | MATCH |
| JOIN condition (2) | `d.judgment_period_end = latest.max_date` | `d.judgment_period_end = latest.max_date` | MATCH |
| SELECT columns | `d.id, d.item_cd` | `d.id, d.item_cd` | MATCH |
| WHERE clauses | `STOP_RECOMMEND` + `operator_action IS NULL` + `IN ({placeholders})` | identical | MATCH |
| Parameters | `[sid, sid] + list(item_cds)` | `[sid, sid] + list(item_cds)` | MATCH |

**Result**: MATCH -- Same fix pattern as Fix 2 applied consistently.

---

### 3.4 Tests: 10 Cases

**Design (Section 3-2)** vs **Implementation (`tests/test_dessert_data_fix.py`)**:

| # | Design Test Name | Impl Location | Fixture Used | Verification Content | Status |
|---|-----------------|---------------|--------------|---------------------|--------|
| 1 | `test_weekly_trend_correct_week_numbers` | L192 (TestWeeklyTrend) | repo_multi_week | 3개 주차 분리 + W0 아닌 유의미한 값 | MATCH |
| 2 | `test_weekly_trend_not_all_w0` | L204 (TestWeeklyTrend) | repo_multi_week | len(result) > 1 확인 | MATCH |
| 3 | `test_weekly_trend_empty_returns_empty_list` | L209 (TestWeeklyTrend) | repo_empty | result == [] | MATCH |
| 4 | `test_pending_stop_count_with_reversed_ids` | L221 (TestPendingStopCount) | repo_with_data | count == 1 (A001 only) | MATCH |
| 5 | `test_pending_stop_count_zero_when_no_stops` | L235 (TestPendingStopCount) | repo_empty | count == 0 | MATCH |
| 6 | `test_pending_stop_count_excludes_actioned` | L240 (TestPendingStopCount) | inline fixture | X001 actioned excluded, X002 counted | MATCH |
| 7 | `test_batch_update_targets_latest_period` | L267 (TestBatchUpdateOperatorAction) | inline fixture | id=1 (latest period) updated | MATCH |
| 8 | `test_batch_update_ignores_old_period_stops` | L313 (TestBatchUpdateOperatorAction) | inline fixture | len(results) == 0 | MATCH |
| 9 | `test_batch_update_no_items_returns_empty` | L342 (TestBatchUpdateOperatorAction) | repo_with_data | results == [] | MATCH |
| 10 | `test_summary_and_pending_count_consistent` | L358 (TestCrossValidation) | repo_with_data | summary STOP_RECOMMEND == pending count | MATCH |

**Result**: 10/10 MATCH

---

### 3.5 Fixture: Reversed ID Pattern

**Design (Section 3-1)**:
> "역순 ID 데이터로 MAX(id) vs MAX(judgment_period_end) 차이를 재현"

**Implementation (`tests/test_dessert_data_fix.py` L102-140)**:
```python
# A001: 최신=STOP_RECOMMEND (낮은 ID), 과거=KEEP (높은 ID)
_insert_record(conn, "A001", "2026-03-04", "STOP_RECOMMEND")  # id=1
_insert_record(conn, "A001", "2026-02-25", "KEEP")            # id=2
_insert_record(conn, "A001", "2026-02-18", "KEEP")            # id=3
```

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| A001 reversed IDs | lowest ID = newest period | id=1 -> 2026-03-04, id=3 -> 2026-02-18 | MATCH |
| A001 decision pattern | newest=STOP_RECOMMEND, oldest=KEEP | id=1=STOP_RECOMMEND, id=2,3=KEEP | MATCH |
| A002 control data | newest=KEEP, past=STOP_RECOMMEND | id=4=KEEP(03-04), id=5=STOP_RECOMMEND(02-25) | MATCH |
| A003 single record | newest=WATCH | id=6=WATCH(03-04) | MATCH |
| Bug reproduction | MAX(id)=3 -> KEEP (wrong), MAX(period_end) -> STOP_RECOMMEND (correct) | Correctly demonstrated | MATCH |

**Result**: MATCH -- Fixture faithfully reproduces the reversed-ID scenario.

---

### 3.6 Cross-Reference: Method Consistency (Design Section 5)

Design Section 5 lists all methods in `dessert_decision_repo.py` and their "latest record" criteria. After fixes, all methods should use `MAX(judgment_period_end)`.

| Method | Design Expected | Implementation | Status |
|--------|----------------|----------------|--------|
| `get_latest_decisions()` | `MAX(judgment_period_end)` (unchanged) | L137: `MAX(judgment_period_end) as max_date` | MATCH |
| `get_confirmed_stop_items()` | `MAX(judgment_period_end)` (unchanged) | L166: `MAX(judgment_period_end) as max_date` | MATCH |
| `get_stop_recommended_items()` | `MAX(judgment_period_end)` (unchanged) | L189: `MAX(judgment_period_end) as max_date` | MATCH |
| `get_decision_summary()` | `MAX(judgment_period_end)` (unchanged) | L437: `MAX(judgment_period_end) as max_date` | MATCH |
| `get_pending_stop_count()` | **Fix 2: MAX(judgment_period_end)** | L405: `MAX(judgment_period_end) as max_date` | MATCH |
| `batch_update_operator_action()` | **Fix 3: MAX(judgment_period_end)** | L313: `MAX(judgment_period_end) as max_date` | MATCH |
| `get_weekly_trend()` | **Fix 1: strftime '%W'** | L367: `strftime('%W', ...)` | MATCH |
| `get_item_decision_history()` | `ORDER BY judgment_period_end DESC` (unchanged) | L212 | MATCH |
| `get_decisions_by_period()` | Period filter (unchanged) | L229-239 | MATCH |
| `update_operator_action()` | `WHERE id = ?` (unchanged) | L271 | MATCH |
| `save_decisions_batch()` | UPSERT conflict key (unchanged) | L67 | MATCH |

**Result**: 11/11 MATCH -- All methods now consistently use `MAX(judgment_period_end)`.

---

## 4. Match Rate Summary

```
+-----------------------------------------------+
|  Overall Match Rate: 100%                      |
+-----------------------------------------------+
|  Fix 1 (strftime):            1/1  MATCH       |
|  Fix 2 (pending_stop_count):  5/5  MATCH       |
|  Fix 3 (batch_update):        6/6  MATCH       |
|  Tests (10 cases):           10/10  MATCH       |
|  Fixture (reversed IDs):      5/5  MATCH       |
|  Cross-ref (11 methods):    11/11  MATCH       |
+-----------------------------------------------+
|  Total items checked:        38/38              |
|  Missing features:            0                 |
|  Added features:              0                 |
|  Changed features:            0                 |
+-----------------------------------------------+
```

---

## 5. Differences Found

### Missing Features (Design O, Implementation X)

None.

### Added Features (Design X, Implementation O)

None.

### Changed Features (Design != Implementation)

None.

---

## 6. Convention Compliance

| Category | Rule | Status |
|----------|------|--------|
| Naming | Repository class PascalCase | PASS (`DessertDecisionRepository`) |
| Naming | Method snake_case | PASS (`get_weekly_trend`, `get_pending_stop_count`, `batch_update_operator_action`) |
| Naming | Test functions snake_case `test_` prefix | PASS (all 10 tests) |
| DB Access | Repository pattern via BaseRepository | PASS (`db_type = "store"`) |
| Logging | `get_logger(__name__)` | PASS (line 13) |
| Error Handling | `except Exception as e: logger.warning(...)` | PASS (lines 119, 343) |
| Docstring | Korean docstrings present | PASS (all public methods) |
| Test Organization | Class-based grouping | PASS (4 test classes) |

---

## 7. Architecture Compliance

| Layer | File | Expected Location | Actual Location | Status |
|-------|------|-------------------|-----------------|--------|
| Infrastructure | `dessert_decision_repo.py` | `src/infrastructure/database/repos/` | `src/infrastructure/database/repos/` | PASS |
| Test | `test_dessert_data_fix.py` | `tests/` | `tests/` | PASS |

No dependency violations detected. Repository correctly extends `BaseRepository` from infrastructure layer.

---

## 8. Test Quality Assessment

| Criterion | Assessment | Status |
|-----------|-----------|--------|
| Reversed ID fixture reproduces MAX(id) bug | Yes -- id=1 is newest, id=3 is oldest | PASS |
| Each fix has dedicated test coverage | Fix 1: 3 tests, Fix 2: 3 tests, Fix 3: 3 tests, Cross: 1 test | PASS |
| Edge cases covered | Empty DB, empty input list, actioned exclusion | PASS |
| Negative tests present | `_zero_when_no_stops`, `_ignores_old_period_stops`, `_no_items_returns_empty` | PASS |
| Cross-validation test | summary vs pending_stop_count consistency | PASS |
| Assert messages descriptive | Bug scenario explained in assertion messages | PASS |

---

## 9. Recommended Actions

### Immediate Actions

None required. All 3 fixes match design exactly, all 10 tests implemented.

### Documentation Update Needed

None. Design document is fully consistent with implementation.

---

## 10. Conclusion

**Match Rate: 100% -- PASS**

All 38 comparison items verified:
- **Fix 1** (`get_weekly_trend` strftime): 1 change, exact match
- **Fix 2** (`get_pending_stop_count` MAX(judgment_period_end)): 5 items, all match
- **Fix 3** (`batch_update_operator_action` MAX(judgment_period_end)): 6 items, all match
- **Tests**: 10/10 cases implemented with correct fixture pattern
- **Fixture**: Reversed-ID scenario correctly reproduces the bug
- **Cross-reference**: All 11 methods in the repository now use consistent `MAX(judgment_period_end)` criteria

The implementation faithfully reflects the design document with zero gaps.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-04 | Initial analysis -- 100% match, 38/38 items | gap-detector |
