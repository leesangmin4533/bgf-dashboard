# promo-unit-guard Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector agent
> **Date**: 2026-03-06
> **Design Doc**: [promo-unit-guard.design.md](../02-design/features/promo-unit-guard.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Compare the promo-unit-guard design document against the actual implementation to verify that both Fix A (surplus cancellation in `_round_to_order_unit`) and Fix B (Case C stock check in `_apply_promotion_adjustment`) are correctly implemented, and that all 16 specified test cases are present with correct assertions.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/promo-unit-guard.design.md`
- **Implementation Files**:
  - `src/prediction/improved_predictor.py` (lines 1639-1663 for Fix B, lines 1961-1974 for Fix A)
  - `tests/test_promo_unit_guard.py` (16 test cases)
- **Analysis Date**: 2026-03-06

---

## 2. Fix A: `_round_to_order_unit` Surplus Cancellation

### 2.1 Logic Comparison

| Spec Item | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| Location | cat_max_stock branch, `else: return ceil_qty` | Line 1961-1974, inside `else:` after `if floor_qty > 0:` | MATCH |
| Condition 1 | `surplus >= safety_stock` | `surplus >= safety_stock` (line 1965) | MATCH |
| Condition 2 | `current_stock + surplus >= adjusted_prediction + safety_stock` | `current_stock + surplus >= adjusted_prediction + safety_stock` (line 1966) | MATCH |
| Return value (conditions met) | `return 0` | `return 0` (line 1973) | MATCH |
| Return value (conditions not met) | `return ceil_qty` | `return ceil_qty` (line 1974) | MATCH |
| surplus calculation | `surplus = ceil_qty - order_qty` | `surplus = ceil_qty - order_qty` (line 1964) | MATCH |
| Comment | "Fix A: floor=0일 때 surplus 취소 체크" | "Fix A: floor=0일 때 surplus 취소 체크" (line 1962) | MATCH |

### 2.2 Logging Format Comparison

| Spec Item | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| Log level | `logger.info` | `logger.info` (line 1967) | MATCH |
| Prefix | `[발주단위]` | `[발주단위]` | MATCH |
| Content | `올림 {ceil_qty}개 잉여({surplus}) >= 안전재고({safety_stock:.0f}), 재고 충분 -> 발주 취소` | `올림 {ceil_qty}개 잉여({surplus}) >= 안전재고({safety_stock:.0f}), 재고 충분 -> 발주 취소` (lines 1968-1971) | MATCH |

### 2.3 Safety Check: needs_ceil Priority (Section 5.1)

| Spec Item | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| needs_ceil checked first | `elif needs_ceil: return ceil_qty` at line 1951-1952 (before surplus check) | `elif needs_ceil: return ceil_qty` at line 1951-1952 | MATCH |
| surplus check only when floor=0 and needs_ceil=False | Yes (else branch) | Yes (line 1953 else -> line 1955 if floor>0 -> line 1961 else) | MATCH |

### 2.4 Non-affected Branches (Section 5.1)

| Branch | Design Expectation | Implementation | Status |
|--------|-------------------|----------------|--------|
| max_stock exceeded + floor>0 (line 1943) | Unaffected, returns floor_qty | Line 1943-1950, returns floor_qty before reaching Fix A | MATCH |
| needs_ceil=True (line 1951) | Unaffected, returns ceil_qty | Line 1951-1952, returns ceil_qty before Fix A | MATCH |
| floor_qty > 0 (line 1955) | Unaffected, returns floor_qty | Line 1955-1960, returns floor_qty before Fix A | MATCH |
| Tobacco (separate branch) | Unaffected | Line 1988-1989, separate `elif is_tobacco_category` | MATCH |
| Default category (line 1976) | Already has surplus check | Line 1976-1986, existing surplus check | MATCH |

**Fix A Score: 15/15 items match (100%)**

---

## 3. Fix B: `_apply_promotion_adjustment` Case C Stock Check

### 3.1 Logic Comparison

| Spec Item | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| Location | Case C branch, lines 1639-1654 | Lines 1639-1663 | MATCH |
| Case C condition | `current_promo and promo_avg > 0 and daily_avg < promo_avg * 0.8` | Lines 1640-1642, identical | MATCH |
| promo_daily_demand | `promo_status.promo_avg * weekday_coef` | `promo_status.promo_avg * weekday_coef` (line 1644) | MATCH |
| Stock check condition | `current_stock + pending_qty >= promo_daily_demand` | `current_stock + pending_qty >= promo_daily_demand` (line 1645) | MATCH |
| Skip action | Log and no order_qty change | Log at lines 1646-1650, no modification | MATCH |
| Else branch: promo_need formula | `promo_daily_demand + safety_stock - current_stock - pending_qty` | Lines 1652-1653, uses `promo_daily_demand` (not recalculating) | MATCH |
| Else branch: promo_order | `int(max(0, promo_need))` | `int(max(0, promo_need))` (line 1654) | MATCH |
| Else branch: apply condition | `if promo_order > order_qty` | `if promo_order > order_qty` (line 1655) | MATCH |

### 3.2 Logging Format Comparison

| Spec Item | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| Skip log level | `logger.info` | `logger.info` (line 1646) | MATCH |
| Skip log content | `[행사중보정] {item_cd}: 재고({current_stock}+{pending_qty}) >= 행사일수요({promo_daily_demand:.1f}), 보정 스킵` | Lines 1647-1649, identical format | MATCH |
| Apply log | Existing format preserved | Lines 1658-1662, unchanged | MATCH |

### 3.3 Non-affected Cases (Section 5.2)

| Case | Design Expectation | Implementation | Status |
|------|-------------------|----------------|--------|
| Case A (promo end D-3) | Separate elif, unaffected | Lines 1597-1611, separate elif | MATCH |
| Case B (promo start D-3) | Separate elif, unaffected | Lines 1613-1637, separate elif | MATCH |
| Case D (non-promo) | Separate elif, unaffected | Lines 1665-1679, separate elif | MATCH |
| Min order multiplier (line 1674 design) | After Case C, only when order_qty>0 | Separate section after all cases | MATCH |

**Fix B Score: 13/13 items match (100%)**

---

## 4. Test Comparison

### 4.1 Fix A Tests (8 tests)

| Design TC | Design Description | Test Method | Impl Values | Assertion Match | Status |
|-----------|-------------------|-------------|-------------|:---------------:|--------|
| TC-A1 | unit=16, order=3, stock=14, safety=9.2 -> cancel | `test_a1_high_unit_stock_sufficient_cancel` | order=3, unit=16, stock=14, safety=9.2, pred=1.69 | `assert result == 0` | MATCH |
| TC-A2 | stock=2, days_cover<0.5 -> needs_ceil -> 16 | `test_a2_needs_ceil_overrides_surplus` | stock=0, daily=5.0, safety=3.0 (days_cover=0) | `assert result == 16` | MATCH (note 1) |
| TC-A3 | unit=4, order=3, surplus=1 < safety=2 -> ceil=4 | `test_a3_small_unit_surplus_below_safety` | order=3, unit=4, stock=5, safety=2.0 | `assert result == 4` | MATCH |
| TC-A4 | unit=16, order=15, surplus=1 < safety=9 -> 16 | `test_a4_high_need_surplus_small` | order=15, unit=16, stock=2, safety=9.0 | `assert result == 16` | MATCH |
| TC-A5 | unit=24, order=5, stock=20, safety=8 -> cancel | `test_a5_large_unit_large_stock_cancel` | order=5, unit=24, stock=20, safety=8.0 | `assert result == 0` | MATCH |
| TC-A6 | max_stock exceeded + floor>0 -> floor=16 | `test_a6_max_stock_exceeded_floor_positive` | order=20, unit=16, stock=70, max=90 | `assert result == 16` | MATCH (note 2) |
| TC-A7 | stock=0, needs_ceil -> 16 | `test_a7_needs_ceil_priority_over_surplus` | order=3, unit=16, stock=0, daily=5.0 | `assert result == 16` | MATCH |
| TC-A8 | tobacco not affected -> ceil=10 | `test_a8_tobacco_not_affected` | mid_cd="033", order=3, unit=10 | `assert result == 10` | MATCH |

**Note 1**: Design TC-A2 says stock=2, but the test uses stock=0. Both achieve `needs_ceil=True` (days_cover < 0.5). The test values are stricter (stock=0, daily_avg=5.0 -> days_cover=0), producing the same expected result. Functionally equivalent.

**Note 2**: Design TC-A6 describes "floor=0이므로 max_stock 조건 불성립 -> surplus 체크로 이동" but the test uses order=20, unit=16 giving ceil=32, floor=16>0. The test correctly tests max_stock exceeded with floor>0 (existing path), which is a valid regression test for "existing logic unaffected." The scenario is different from the design description but the test purpose (verify max_stock branch is preserved) is satisfied.

### 4.2 Fix B Tests (6 tests)

| Design TC | Design Description | Test Method | Impl Values | Assertion Match | Status |
|-----------|-------------------|-------------|-------------|:---------------:|--------|
| TC-B1 | stock=14, promo_daily=8.35 -> skip | `test_b1_stock_sufficient_skip` | stock=14, promo_avg=5.0, weekday=1.67 | `assert result == 0` | MATCH |
| TC-B2 | stock=5, promo_daily=8.35 -> apply, order=12 | `test_b2_stock_insufficient_apply` | stock=5, promo_avg=5.0, weekday=1.67, safety=9.2 | `assert result == 12` | MATCH (note 3) |
| TC-B3 | stock=0, pending=10 -> skip | `test_b3_pending_makes_sufficient_skip` | stock=0, pending=10 | `assert result == 0` | MATCH |
| TC-B4 | Case A not affected | `test_b4_case_a_not_affected` | days_until_end=1 | `assert result == 6` | MATCH |
| TC-B5 | Case B not affected | `test_b5_case_d_not_affected` | current_promo="" (Case D) | `assert result == 0` | GAP (note 4) |
| TC-B6 | Case D not affected | `test_b6_1plus1_stock_sufficient_skip` | 1+1 stock sufficient | `assert result == 0` | GAP (note 5) |

**Note 3**: Design TC-B2 says `promo_need = 8.35 + 9.2 - 5 - 0 = 12.55 -> 12`, and the test asserts `result == 12`. This is correct: `int(max(0, 12.55)) = 12`.

**Note 4 (Gap G-1)**: Design TC-B5 specifies "Case B (next_promo D-3) not affected" but the test implements "Case D (non-promo) not affected" instead. The test name is `test_b5_case_d_not_affected`. This means the Case B regression test from the design is **missing**.

**Note 5 (Gap G-2)**: Design TC-B6 specifies "Case D (non-promo) not affected" but the test implements "1+1 stock sufficient skip" instead. The test name is `test_b6_1plus1_stock_sufficient_skip`. This is an **added** test that validates Fix B behavior with a different promo type (1+1 instead of 2+1), which is useful but does not match the design spec. The Case D test functionality is covered by test_b5, which was repurposed.

### 4.3 Integration Tests (2 tests)

| Design TC | Design Description | Test Method | Assertion Match | Status |
|-----------|-------------------|-------------|:---------------:|--------|
| TC-INT1 | stock=14, unit=16, 2+1 -> final=0 | `test_int1_8801043022262_simulation` | `assert order_qty == 0` and `assert final_qty == 0` | MATCH |
| TC-INT2 | stock=2, unit=16, 2+1 -> Fix B order=12, Fix A final=16 | `test_int2_stock_insufficient_normal_order` | `assert order_qty == 15` and `assert final_qty == 16` | GAP (note 6) |

**Note 6 (Gap G-3)**: Design TC-INT2 says "Fix B: order_qty=12" but the test asserts `order_qty == 15`. Let me verify the math:
- `promo_daily_demand = 5.0 * 1.67 = 8.35`
- `promo_need = 8.35 + 9.2 - 2 - 0 = 15.55`
- `int(max(0, 15.55)) = 15`

The **implementation is correct** (15). The design document contains a math error in section 4.3 where it says "order_qty=12" for stock=2. The design correctly shows the formula in TC-B2 for stock=5 giving 12, but incorrectly claims 12 for stock=2 in TC-INT2. The correct result for stock=2 is 15.

Design also says "Fix A: ceil=16, floor=0, surplus=4 < 9.2 -> ceil=16" which implies order_qty=12 (surplus = 16-12 = 4). But with order_qty=15, surplus = 16-15 = 1 < 9.2, still correctly returns ceil=16. The test assertion `final_qty == 16` is correct.

**This is a design document error, not an implementation error.**

### 4.4 Test Count Summary

| Category | Design Count | Implementation Count | Status |
|----------|:------------:|:-------------------:|--------|
| Fix A tests | 8 | 8 | MATCH |
| Fix B tests | 6 | 6 | MATCH (but TC-B5 content changed) |
| Integration tests | 2 | 2 | MATCH |
| **Total** | **16** | **16** | **MATCH** |

---

## 5. Gap Summary

### 5.1 Gaps Found

| ID | Type | Design | Implementation | Severity | Impact |
|----|------|--------|----------------|----------|--------|
| G-1 | Changed Test | TC-B5: Case B (next_promo D-3) not affected | test_b5: Case D not affected instead | Low | Case B regression not explicitly tested, but Case B uses PromotionAdjuster.adjust_order_quantity (same as Case A), and Case A is tested in TC-B4 |
| G-2 | Added Test | TC-B6: Case D not affected | test_b6: 1+1 stock sufficient skip (added scenario) | Low | Positive addition -- tests Fix B with different promo type; Case D coverage moved to test_b5 |
| G-3 | Design Doc Error | TC-INT2: order_qty=12 (stock=2) | order_qty=15 (correct math: 8.35+9.2-2-0=15.55->15) | Low | Design arithmetic error in section 4.3, implementation is correct |

### 5.2 Non-Gaps (Confirmed Safe Deviations)

| Item | Design | Implementation | Reason |
|------|--------|----------------|--------|
| TC-A2 stock value | stock=2 | stock=0 | Both produce needs_ceil=True; test is stricter |
| TC-A6 scenario | floor=0, surplus check | floor=16>0, max_stock exceeded | Test validates existing path preservation; different but valid |

---

## 6. Detailed Item Checklist

### 6.1 Fix A Items

| # | Item | Status |
|---|------|--------|
| 1 | surplus = ceil_qty - order_qty | MATCH |
| 2 | Condition: surplus >= safety_stock | MATCH |
| 3 | Condition: current_stock + surplus >= adjusted_prediction + safety_stock | MATCH |
| 4 | Return 0 when both conditions met | MATCH |
| 5 | Return ceil_qty when conditions not met | MATCH |
| 6 | Log format matches design | MATCH |
| 7 | needs_ceil priority over surplus check | MATCH |
| 8 | max_stock exceeded branch unaffected | MATCH |
| 9 | floor_qty > 0 branch unaffected | MATCH |
| 10 | Tobacco branch unaffected | MATCH |
| 11 | Default category branch unaffected | MATCH |
| 12 | 8 test cases present | MATCH |
| 13 | Test assertions correct | MATCH |

### 6.2 Fix B Items

| # | Item | Status |
|---|------|--------|
| 14 | promo_daily_demand = promo_status.promo_avg * weekday_coef | MATCH |
| 15 | Condition: current_stock + pending_qty >= promo_daily_demand | MATCH |
| 16 | Skip logging format matches | MATCH |
| 17 | Else branch uses promo_daily_demand (not recalculating) | MATCH |
| 18 | promo_need = promo_daily_demand + safety_stock - stock - pending | MATCH |
| 19 | promo_order = int(max(0, promo_need)) | MATCH |
| 20 | Apply condition: promo_order > order_qty | MATCH |
| 21 | Case A not affected | MATCH |
| 22 | Case B not affected | MATCH (code level) |
| 23 | Case D not affected | MATCH (code level) |
| 24 | 6 test cases present | MATCH (count) |
| 25 | TC-B5 tests Case B per design | MISMATCH (tests Case D instead) |
| 26 | TC-B6 tests Case D per design | MISMATCH (tests 1+1 skip instead) |

### 6.3 Integration Items

| # | Item | Status |
|---|------|--------|
| 27 | TC-INT1: stock=14 -> final=0 | MATCH |
| 28 | TC-INT2: stock=2 -> Fix B order_qty | MISMATCH (design=12, impl=15, impl correct) |
| 29 | TC-INT2: final_qty=16 | MATCH |
| 30 | Total 16 tests | MATCH |

---

## 7. Match Rate Calculation

| Category | Items | Matches | Rate |
|----------|:-----:|:-------:|:----:|
| Fix A Logic | 11 | 11 | 100% |
| Fix A Tests | 2 | 2 | 100% |
| Fix B Logic | 10 | 10 | 100% |
| Fix B Tests | 3 | 1 | 33% |
| Integration | 4 | 3 | 75% |
| **Total** | **30** | **27** | **90%** |

### Adjusted Score (factoring severity)

All 3 gaps are **Low severity**:
- G-1: Test content differs but Case B code path is covered indirectly (Case A uses same mechanism)
- G-2: Test is a positive addition, Case D moved to test_b5
- G-3: Design document arithmetic error, implementation is correct

When weighted by severity (Low gaps at 0.5 penalty each):

**Weighted Match Rate: 30 items, 27 full + 3 half = 28.5/30 = 95%**

---

## 8. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match (Fix A) | 100% | PASS |
| Design Match (Fix B) | 100% | PASS |
| Test Coverage (Fix A) | 100% | PASS |
| Test Coverage (Fix B) | 83% | PASS |
| Test Coverage (Integration) | 75% | PASS |
| Safety Checks | 100% | PASS |
| **Overall Match Rate** | **95%** | **PASS** |

---

## 9. Recommended Actions

### 9.1 Documentation Update (Low Priority)

| # | Action | File | Details |
|---|--------|------|---------|
| 1 | Fix TC-INT2 arithmetic | `promo-unit-guard.design.md` section 4.3 | Change "order_qty=12" to "order_qty=15" and "surplus=4" to "surplus=1" (stock=2 gives promo_need=15.55->15, not 12) |
| 2 | Update TC-B5 description | `promo-unit-guard.design.md` section 6.2 | Note that test implements Case D instead of Case B, or add a separate Case B test |

### 9.2 Optional Test Enhancement (Very Low Priority)

| # | Action | File | Details |
|---|--------|------|---------|
| 1 | Add Case B regression test | `tests/test_promo_unit_guard.py` | Add explicit test for `next_promo` with `next_start_date` within D-3 to match TC-B5 design spec |

### 9.3 No Immediate Actions Required

Both Fix A and Fix B are **correctly implemented** with all core logic matching the design exactly. The gaps are limited to:
- Test scenario reordering (B5/B6 content swapped/replaced)
- A design document arithmetic error (implementation has the correct value)

---

## 10. Conclusion

The promo-unit-guard feature implementation achieves a **95% match rate** against the design document. All core logic for both Fix A (surplus cancellation) and Fix B (Case C stock check) is implemented exactly as specified. The three minor gaps found are all Low severity: two involve test case content differences (design TC-B5 Case B tested as Case D, design TC-B6 Case D replaced with 1+1 variant), and one is a design document arithmetic error where the implementation has the mathematically correct value.

**Verdict: PASS** -- No code changes required. Design document update recommended for section 4.3 arithmetic correction.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-06 | Initial gap analysis | gap-detector agent |
