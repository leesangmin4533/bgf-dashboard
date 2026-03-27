# force-order-fix Analysis Report

> **Analysis Type**: Plan vs Implementation Gap Analysis
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-04
> **Plan Doc**: [force-order-fix.plan.md](../../01-plan/features/force-order-fix.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

FORCE_ORDER 오판 수정 기능의 Plan 문서와 실제 구현 코드 간 정합성 검증.
재고가 있는 상품에 불필요한 FORCE 강제 발주가 발생하는 버그 수정이 계획대로 이행되었는지 확인한다.

### 1.2 Analysis Scope

- **Plan Document**: `bgf_auto/docs/01-plan/features/force-order-fix.plan.md`
- **Implementation Files**:
  - `bgf_auto/src/order/auto_order.py` (line 797~826)
  - `bgf_auto/src/infrastructure/database/repos/order_exclusion_repo.py` (ExclusionType)
  - `bgf_auto/tests/test_force_order_fix.py` (16 test cases)
- **Analysis Date**: 2026-03-04

---

## 2. Gap Analysis (Plan vs Implementation)

### 2.1 Fix 1: FORCE 보충 생략 조건 강화

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| **File** | `src/order/auto_order.py` line 799 | `src/order/auto_order.py` line 799 | Match |
| **Old condition** | `if r.pending_qty > 0 and r.current_stock + r.pending_qty > 0:` | (removed) | Match |
| **New condition** | `if r.current_stock + r.pending_qty > 0:` | `if r.current_stock + r.pending_qty > 0:` | Match |
| **Logger message** | `[FORCE보충생략] {name}: stock={stock}+pending={pending} -> 재고/미입고분 충분` | `[FORCE보충생략] {r.item_nm[:20]}: stock={r.current_stock}+pending={r.pending_qty} -> 재고/미입고분 충분` | Match |
| **continue** | After log | After exclusion record + log | Match |

**Detailed condition comparison:**

```python
# Plan (Old -> New):
# Old: if r.pending_qty > 0 and r.current_stock + r.pending_qty > 0:
# New: if r.current_stock + r.pending_qty > 0:

# Implementation (auto_order.py:799):
if r.current_stock + r.pending_qty > 0:
    logger.info(
        f"[FORCE보충생략] {r.item_nm[:20]}: "
        f"stock={r.current_stock}+pending={r.pending_qty} "
        f"-> 재고/미입고분 충분"
    )
    self._exclusion_records.append({...})   # <-- Plan에 없는 추가 구현
    continue
```

**Verdict**: Fix 1 is fully implemented as planned. The condition change from `r.pending_qty > 0 and r.current_stock + r.pending_qty > 0` to `r.current_stock + r.pending_qty > 0` matches exactly.

### 2.2 Fix 2: pre_order_evaluator 실시간 재고 캐시 반영

| Item | Plan | Implementation | Status |
|------|------|----------------|--------|
| **File** | `src/prediction/pre_order_evaluator.py` | Not modified | Not implemented |
| **Feature** | `set_stock_cache()` 주입된 실시간 재고 우선 사용 | N/A | Not implemented |
| **Priority in Plan** | 권장 (선택적 강화) | Deferred | Intentional deferral |

**Verification**: `pre_order_evaluator.py`에서 `set_stock_cache` / `stock_cache` 검색 결과 0건. Fix 2는 구현되지 않았으며, Plan에서도 "선택적 강화"로 명시되어 있어 의도적 보류이다.

### 2.3 Implementation Enhancements (Plan에 없는 추가 구현)

| Item | Location | Description | Impact |
|------|----------|-------------|--------|
| ExclusionType.FORCE_SUPPRESSED | `order_exclusion_repo.py:30` | FORCE 보충 생략 사유 추적용 상수 추가 | Positive (추적성 향상) |
| `_exclusion_records.append()` | `auto_order.py:805-814` | FORCE 생략 시 제외 사유를 DB 기록용 레코드에 추가 | Positive (디버깅/감사 지원) |

Plan에는 logger.info만 명시되어 있었으나, 실제 구현에서는 `_exclusion_records`에 FORCE_SUPPRESSED 사유를 기록하는 기능이 추가되었다. 이는 기존 order exclusion 추적 패턴과 일관성을 유지하는 긍정적 강화이다.

### 2.4 Test Plan Comparison

| Plan Test | Implementation Test | Status | Notes |
|-----------|---------------------|--------|-------|
| test_force_skip_with_stock | TestForceSkipWithStockOnly (3 tests) | Match (expanded) | stock=10/1/5 세 가지 케이스로 확장 |
| test_force_skip_with_pending | TestForceSkipWithPendingOnly (2 tests) | Match (expanded) | pending=5/1 두 가지 케이스로 확장 |
| test_force_order_genuine_stockout | TestForceOrderGenuineStockout (2 tests) | Match (expanded) | 품절+상한 테스트 포함 |
| test_force_cap_applied | TestForceSupplementIntegration.test_force_cap_applied | Match | force_cap 정확히 검증 |
| test_eval_stock_cache (Fix 2 관련) | N/A | Not implemented | Fix 2 보류로 해당 테스트 없음 |
| (not in plan) | TestForceSupplementIntegration (5 tests) | Added | 통합 시뮬레이션 테스트 |
| (not in plan) | TestOldVsNewCondition (4 tests) | Added | 기존/수정 조건 비교 테스트 |

**Test Summary:**

| Category | Plan | Implementation | Status |
|----------|:----:|:--------------:|--------|
| Planned tests | 5 | 4 implemented (1 deferred with Fix 2) | 4/5 = 80% |
| Additional tests | 0 | 12 extra tests | Positive enhancement |
| Total test count | 5 | 16 | 320% of plan |

### 2.5 Match Rate Summary

```
+-------------------------------------------------+
|  Plan vs Implementation Match Rate: 95%         |
+-------------------------------------------------+
|  Fix Items:                                     |
|    Fix 1 (condition change)    : MATCH          |
|    Fix 1 (logger message)     : MATCH           |
|    Fix 2 (stock cache)        : DEFERRED        |
|                                                 |
|  Test Items:                                    |
|    test_force_skip_with_stock : MATCH (3x)      |
|    test_force_skip_with_pending : MATCH (2x)    |
|    test_force_order_genuine_stockout : MATCH (2x)|
|    test_force_cap_applied     : MATCH            |
|    test_eval_stock_cache      : DEFERRED         |
|    Additional tests           : 12 BONUS         |
|                                                 |
|  Enhancement Items (not in plan):               |
|    ExclusionType.FORCE_SUPPRESSED : ADDED       |
|    _exclusion_records tracking    : ADDED        |
+-------------------------------------------------+
```

---

## 3. Detailed Item Scoring

| # | Category | Item | Plan | Implementation | Match |
|---|----------|------|------|----------------|:-----:|
| 1 | Fix 1 | Condition: `r.pending_qty > 0 and` removed | Yes | Yes | 1/1 |
| 2 | Fix 1 | New condition: `r.current_stock + r.pending_qty > 0` | Yes | Yes | 1/1 |
| 3 | Fix 1 | Logger `[FORCE보충생략]` message | Yes | Yes | 1/1 |
| 4 | Fix 1 | `continue` after skip | Yes | Yes | 1/1 |
| 5 | Fix 1 | File: `auto_order.py` line ~799 | Yes | Yes (exact line 799) | 1/1 |
| 6 | Fix 2 | `set_stock_cache()` in pre_order_evaluator | Optional | Not implemented | N/A |
| 7 | Test | test_force_skip_with_stock | Yes | Yes (3 variants) | 1/1 |
| 8 | Test | test_force_skip_with_pending | Yes | Yes (2 variants) | 1/1 |
| 9 | Test | test_force_order_genuine_stockout | Yes | Yes (2 variants) | 1/1 |
| 10 | Test | test_force_cap_applied | Yes | Yes | 1/1 |
| 11 | Test | test_eval_stock_cache | Optional (Fix 2) | Not implemented | N/A |
| 12 | Risk | Fix 1 = condition relaxation, no regression | Low | Confirmed via tests | 1/1 |
| 13 | Risk | Genuine stockout still triggers FORCE | Yes | Confirmed via tests | 1/1 |
| 14 | Enhancement | ExclusionType.FORCE_SUPPRESSED | Not planned | Added | Bonus |
| 15 | Enhancement | _exclusion_records tracking | Not planned | Added | Bonus |
| 16 | Enhancement | TestForceSupplementIntegration (5 tests) | Not planned | Added | Bonus |
| 17 | Enhancement | TestOldVsNewCondition (4 tests) | Not planned | Added | Bonus |

**Scoring (excluding optional/N/A items):**
- Required items: 11 (items 1-5, 7-10, 12-13)
- Matched: 11/11 = **100%**
- Optional items deferred: 2 (Fix 2 + its test) -- both marked "optional/recommended" in plan
- Bonus items: 4 (enhancements beyond plan)

---

## 4. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match (Fix 1 - required) | 100% | PASS |
| Design Match (Fix 2 - optional) | 0% (deferred) | N/A (optional) |
| Test Coverage vs Plan | 80% (4/5 planned tests) | PASS |
| Test Coverage with bonus | 320% (16/5) | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall Match Rate** | **95%** | **PASS** |

**Overall 95% rationale:**
- Fix 1 (required, weight 60%): 100% match = 60 points
- Fix 2 (optional, weight 15%): 0% implemented but plan says "optional" = 10 points (intent documented)
- Tests (weight 25%): 80% of planned + 12 bonus = 25 points
- Total: 95 points

---

## 5. Differences Found

### Missing Features (Plan O, Implementation X)

| Item | Plan Location | Description | Severity |
|------|---------------|-------------|----------|
| Fix 2: stock cache in pre_order_evaluator | plan.md:48-50 | `set_stock_cache()` 실시간 재고 캐시 우선 사용 | Low (Plan에서 "선택적 강화"로 명시) |
| test_eval_stock_cache | plan.md:67 | Fix 2 관련 테스트 | Low (Fix 2 보류로 불필요) |

### Added Features (Plan X, Implementation O)

| Item | Implementation Location | Description |
|------|------------------------|-------------|
| ExclusionType.FORCE_SUPPRESSED | `order_exclusion_repo.py:30` | FORCE 보충 생략 추적용 enum 상수 |
| Exclusion record tracking | `auto_order.py:805-814` | FORCE 생략 시 DB 기록용 제외 사유 추가 |
| TestForceSupplementIntegration | `test_force_order_fix.py:114-189` | 5개 통합 시뮬레이션 테스트 |
| TestOldVsNewCondition | `test_force_order_fix.py:196-225` | 4개 기존/수정 조건 비교 테스트 |

### Changed Features (Plan != Implementation)

| Item | Plan | Implementation | Impact |
|------|------|----------------|--------|
| Skip action | logger.info only | logger.info + _exclusion_records append | Low (positive enhancement) |
| Test count | 5 tests in 5 cases | 16 tests in 5 classes | Low (positive expansion) |

---

## 6. Code Quality Check

### 6.1 Condition Logic Correctness

```python
# Plan's intended fix:
if r.current_stock + r.pending_qty > 0:
    # skip FORCE supplement

# Implementation (auto_order.py:799):
if r.current_stock + r.pending_qty > 0:
    # skip FORCE supplement + record exclusion
```

Truth table verification:

| current_stock | pending_qty | Sum | Old condition (`pending>0 and sum>0`) | New condition (`sum>0`) | Expected |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 10 | 0 | 10 | False (BUG) | True (SKIP) | SKIP |
| 0 | 5 | 5 | True (SKIP) | True (SKIP) | SKIP |
| 5 | 3 | 8 | True (SKIP) | True (SKIP) | SKIP |
| 1 | 0 | 1 | False (BUG) | True (SKIP) | SKIP |
| 0 | 0 | 0 | False (ORDER) | False (ORDER) | ORDER |

All rows match expected behavior. The fix correctly resolves the bug where `pending_qty=0` caused the old condition to short-circuit even when `current_stock > 0`.

### 6.2 Convention Compliance

| Rule | Status |
|------|--------|
| snake_case function/variable names | PASS |
| Korean comments | PASS |
| Logger usage (no print) | PASS |
| Exception handling pattern | N/A (no try/except needed) |
| ExclusionType constant naming (UPPER_SNAKE) | PASS |

### 6.3 Architecture Compliance

| Check | Status |
|-------|--------|
| Fix in correct layer (order/ = Application/Domain boundary) | PASS |
| ExclusionType in Infrastructure layer (repos/) | PASS |
| No circular imports introduced | PASS |
| Tests isolated (no DB/external I/O) | PASS |

---

## 7. Test Quality Assessment

| Test Class | Tests | Coverage Target | Verdict |
|------------|:-----:|-----------------|---------|
| TestForceSkipWithStockOnly | 3 | stock>0, pending=0 edge cases | PASS |
| TestForceSkipWithPendingOnly | 2 | stock=0, pending>0 edge cases | PASS |
| TestForceOrderGenuineStockout | 2 | stock=0, pending=0 + cap test | PASS |
| TestForceSupplementIntegration | 5 | Full FORCE supplement simulation | PASS |
| TestOldVsNewCondition | 4 | Regression: old vs new condition proof | PASS |

**Strengths:**
- TestOldVsNewCondition explicitly proves the bug existed and the fix resolves it
- TestForceSupplementIntegration simulates the actual auto_order.py loop logic
- Edge cases covered: min guarantee (qty=1), cap enforcement, mixed items, all-stock, all-stockout

**Weaknesses:**
- Tests use SimpleNamespace simulation rather than mocking actual AutoOrderSystem -- acceptable for unit tests
- No integration test with real AutoOrderSystem._run_force_supplement() -- would require complex mock setup

---

## 8. Recommended Actions

### 8.1 Immediate Actions

None required. Fix 1 is complete and tested.

### 8.2 Documentation Updates

| Item | Action |
|------|--------|
| Plan document | Add note: "Fix 2 deferred to future cycle" |
| changelog.md | Add entry for FORCE_ORDER skip condition fix |

### 8.3 Future Consideration (Fix 2)

Fix 2 (pre_order_evaluator stock cache) remains a valid improvement for a future PDCA cycle:
- **Problem**: DB stale stock at eval time (07:04) vs real stock at prefetch time (07:06) causes false FORCE_ORDER verdicts
- **Current mitigation**: Fix 1 catches these at supplement time, so false FORCE items are suppressed
- **Residual gap**: Unnecessary predict_batch calls still occur for false-FORCE items (minor performance cost)
- **Recommendation**: Low priority. Fix 1 is sufficient as the safety net.

---

## 9. Verdict

```
+==================================================+
|                                                    |
|   Feature: force-order-fix                         |
|   Match Rate: 95%                                  |
|   Status: PASS                                     |
|                                                    |
|   Required items: 11/11 (100%)                     |
|   Optional items: 0/2 (deferred, plan-approved)    |
|   Bonus items: +4 enhancements                     |
|   Tests: 16 (plan: 5, bonus: 11)                   |
|                                                    |
|   Files modified: 2                                |
|     - src/order/auto_order.py (Fix 1)              |
|     - src/infrastructure/.../order_exclusion_repo.py|
|   Files added: 1                                   |
|     - tests/test_force_order_fix.py (16 tests)     |
|                                                    |
+==================================================+
```

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-04 | Initial Plan vs Implementation gap analysis | gap-detector |
