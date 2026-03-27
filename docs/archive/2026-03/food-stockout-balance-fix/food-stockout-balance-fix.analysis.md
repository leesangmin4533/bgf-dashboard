# food-stockout-balance-fix Gap Analysis Report

> **Analysis Type**: Design vs Implementation Gap Analysis (Check Phase)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector agent
> **Date**: 2026-03-03
> **Design Doc**: [food-stockout-balance-fix.design.md](../02-design/features/food-stockout-balance-fix.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the implementation of "food-stockout-balance-fix" (3 changes: waste coefficient conditional application, final floor protection, stockout boost feedback) matches the design document.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/food-stockout-balance-fix.design.md`
- **Implementation Files**:
  - `src/prediction/categories/food.py` (lines 1239-1272)
  - `src/prediction/improved_predictor.py` (lines 1169-1254)
  - `tests/test_food_stockout_balance.py` (297 lines)
- **Analysis Date**: 2026-03-03

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| Test Coverage | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## 3. Detailed Gap Analysis

### 3-A. Waste Coefficient Conditional Application (Change A)

#### 3-A-1. stockout_freq Calculation

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Formula | `1.0 - sell_day_ratio if sell_day_ratio is not None else 0.0` | `1.0 - sell_day_ratio if sell_day_ratio is not None else 0.0` (L1181) | MATCH |
| ctx storage | `ctx["stockout_freq"] = stockout_freq` | `ctx["stockout_freq"] = stockout_freq` (L1182) | MATCH |

#### 3-A-2. Conditional Branches

| Condition | Design | Implementation | Status |
|-----------|--------|----------------|--------|
| stockout_freq > 0.50 | effective_waste_coef = 1.0 (exempt) | effective_waste_coef = 1.0 (L1186) | MATCH |
| stockout_freq > 0.30 | max(unified_waste_coef, 0.90) | max(unified_waste_coef, 0.90) (L1194) | MATCH |
| else (<= 0.30) | effective_waste_coef = unified_waste_coef | effective_waste_coef = unified_waste_coef (L1203) | MATCH |

#### 3-A-3. Waste Coefficient Application

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Guard | `if effective_waste_coef < 1.0:` | `if effective_waste_coef < 1.0:` (L1207) | MATCH |
| Application | `adjusted_prediction *= effective_waste_coef` | `adjusted_prediction *= effective_waste_coef` (L1208) | MATCH |
| daily_avg sync | `daily_avg = adjusted_prediction` | `daily_avg = adjusted_prediction` (L1209) | MATCH |
| ctx update | `ctx["adjusted_prediction"] = adjusted_prediction` | `ctx["adjusted_prediction"] = adjusted_prediction` (L1210) | MATCH |
| ctx field | `ctx["effective_waste_coef"]` | `ctx["effective_waste_coef"] = effective_waste_coef` (L1205) | MATCH |

#### 3-A-4. Logging

| Condition | Design Log Pattern | Implementation Log Pattern | Status |
|-----------|-------------------|---------------------------|--------|
| > 0.50 exempt | `[폐기계수면제] ... stockout=...% > 50% ... 면제` | `[폐기계수면제] ... stockout=...% > 50% ... 면제` (L1187-1190) | MATCH |
| > 0.30 clamp | `[폐기계수완화] ... stockout=...%, waste_coef ... -> ...` | `[폐기계수완화] ... stockout=...%, waste_coef ... -> ...` (L1196-1199) | MATCH |
| Application | `폐기 보정: ... (unified=..., effective=...)` | `폐기 보정: ... (unified=..., effective=...)` (L1211-1214) | MATCH |

---

### 3-B. Final Floor Protection (Change B)

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Floor formula | `base_prediction * 0.20` | `base_prediction * 0.20` (L1218) | MATCH |
| Guard | `if adjusted_prediction < final_floor and base_prediction > 0:` | `if adjusted_prediction < final_floor and base_prediction > 0:` (L1219) | MATCH |
| Application | `adjusted_prediction = final_floor` | `adjusted_prediction = final_floor` (L1225) | MATCH |
| daily_avg sync | `daily_avg = adjusted_prediction` | `daily_avg = adjusted_prediction` (L1226) | MATCH |
| ctx update | `ctx["adjusted_prediction"] = adjusted_prediction` | `ctx["adjusted_prediction"] = adjusted_prediction` (L1227) | MATCH |
| Log pattern | `[최종하한] ... adj=... < floor=... -> ...` | `[최종하한] ... adj=... < floor=... -> ...` (L1220-1223) | MATCH |
| Floor ratio | 0.20 (20% of base) | 0.20 (L1218) | MATCH |

---

### 3-C. Stockout Boost Feedback Coefficient (Change C)

#### 3-C-1. Constants in food.py

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| STOCKOUT_BOOST_ENABLED | `True` | `True` (L1242) | MATCH |
| STOCKOUT_BOOST_THRESHOLDS | `{0.70: 1.30, 0.50: 1.15, 0.30: 1.05}` | `{0.70: 1.30, 0.50: 1.15, 0.30: 1.05}` (L1243-1247) | MATCH |
| Location | After `get_unified_waste_coefficient()` | Lines 1239-1272 (after L981 unified func) | MATCH |
| Section comment | `# food-stockout-balance-fix` | `# 품절 기반 예측 부스트 (food-stockout-balance-fix)` (L1240) | MATCH |

#### 3-C-2. get_stockout_boost_coefficient() Function

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Signature | `get_stockout_boost_coefficient(stockout_freq: float) -> float` | `get_stockout_boost_coefficient(stockout_freq: float) -> float` (L1250) | MATCH |
| Toggle check | `if not STOCKOUT_BOOST_ENABLED: return 1.0` | `if not STOCKOUT_BOOST_ENABLED: return 1.0` (L1263-1264) | MATCH |
| Sort order | `sorted(..., reverse=True)` | `sorted(STOCKOUT_BOOST_THRESHOLDS.items(), reverse=True)` (L1266-1267) | MATCH |
| Comparison | `if stockout_freq >= threshold: return boost` | `if stockout_freq >= threshold: return boost` (L1269-1270) | MATCH |
| Default | `return 1.0` | `return 1.0` (L1271) | MATCH |
| Docstring | Present with Args/Returns/Examples | Present with matching docstring (L1251-1261) | MATCH |

#### 3-C-3. Boost Application in improved_predictor.py

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Import | `from ... import get_stockout_boost_coefficient` | `get_stockout_boost_coefficient,` (L1172) | MATCH |
| Call | `get_stockout_boost_coefficient(stockout_freq)` | `get_stockout_boost_coefficient(stockout_freq)` (L1230) | MATCH |
| ctx storage | `ctx["stockout_boost"] = stockout_boost` | `ctx["stockout_boost"] = stockout_boost` (L1231) | MATCH |
| Guard | `if stockout_boost > 1.0:` | `if stockout_boost > 1.0:` (L1232) | MATCH |
| before_boost | `before_boost = adjusted_prediction` | `before_boost = adjusted_prediction` (L1233) | MATCH |
| Application | `adjusted_prediction *= stockout_boost` | `adjusted_prediction *= stockout_boost` (L1234) | MATCH |
| daily_avg sync | `daily_avg = adjusted_prediction` | `daily_avg = adjusted_prediction` (L1235) | MATCH |
| ctx update | `ctx["adjusted_prediction"] = adjusted_prediction` | `ctx["adjusted_prediction"] = adjusted_prediction` (L1236) | MATCH |
| Log pattern | `[품절부스트] ... stockout=...%, boost=...x, ... -> ...` | `[품절부스트] ... stockout=...%, boost=...x, ... -> ...` (L1237-1241) | MATCH |

---

### 3-D. Application Order Verification

Design specifies: A (conditional waste) -> B (final floor) -> C (stockout boost)

| Step | Design Order | Implementation Order | Status |
|------|-------------|---------------------|--------|
| 1 | stockout_freq calculation | L1181 | MATCH |
| 2 | A: conditional waste_coef | L1184-1215 | MATCH |
| 3 | B: final_floor protection | L1217-1227 | MATCH |
| 4 | C: stockout boost | L1229-1242 | MATCH |
| 5 | safety_stock calculation (existing) | L1244-1253 | MATCH |

---

### 3-E. ctx Fields

| Field | Design | Implementation | Status |
|-------|--------|----------------|--------|
| `ctx["stockout_freq"]` | float | L1182 | MATCH |
| `ctx["effective_waste_coef"]` | float | L1205 | MATCH |
| `ctx["stockout_boost"]` | float | L1231 | MATCH |

---

## 4. Test Analysis

### 4-1. Test Class Comparison

| Design Test Group | Implementation Class | Design Cases | Impl Cases | Status |
|-------------------|---------------------|:------------:|:----------:|--------|
| TestWasteCoefConditional | TestWasteCoefConditional | 5 | 5 | MATCH |
| TestFinalFloor | TestFinalFloor | 4 | 4 | MATCH |
| TestStockoutBoost | TestStockoutBoost | 5 | 5 | MATCH |
| TestIntegration | TestIntegration | 4 | 4 | MATCH |
| - | TestConstants (bonus) | 0 | 3 | ADDED |
| **Total** | | **18** | **21** | 18/18 + 3 bonus |

### 4-2. Test Case Detail Comparison

#### TestWasteCoefConditional (A)

| # | Design Case | Test Method | Status |
|---|-------------|-------------|--------|
| 1 | High stockout >50% -> exempt | `test_high_stockout_exempts_waste_coef` | MATCH |
| 2 | Medium stockout 30~50% -> clamp 0.90 | `test_medium_stockout_clamps_waste_coef` | MATCH |
| 3 | Medium stockout, original > 0.90 -> keep | `test_medium_stockout_keeps_higher_coef` | MATCH |
| 4 | Low stockout <30% -> original | `test_low_stockout_keeps_original` | MATCH |
| 5 | sell_day_ratio=None -> 0.0 | `test_none_sell_day_ratio_defaults_zero` | MATCH |

#### TestFinalFloor (B)

| # | Design Case | Test Method | Status |
|---|-------------|-------------|--------|
| 1 | Below floor -> apply | `test_floor_applied_when_below` | MATCH |
| 2 | Above floor -> skip | `test_floor_not_applied_when_above` | MATCH |
| 3 | base=0 -> floor=0 | `test_floor_zero_base` | MATCH |
| 4 | Waste exempt + floor | `test_floor_after_waste_exemption` | MATCH |

#### TestStockoutBoost (C)

| # | Design Case | Test Method | Status |
|---|-------------|-------------|--------|
| 1 | 80% -> 1.30 | `test_severe_stockout_boost` | MATCH |
| 2 | 55% -> 1.15 | `test_high_stockout_boost` | MATCH |
| 3 | 35% -> 1.05 | `test_medium_stockout_boost` | MATCH |
| 4 | 10% -> 1.00 | `test_low_stockout_no_boost` | MATCH |
| 5 | Toggle OFF -> 1.0 | `test_boost_disabled_toggle` | MATCH |

#### TestIntegration (A+B+C)

| # | Design Scenario | Test Method | Status |
|---|-----------------|-------------|--------|
| 1 | High stockout + boost (sell_day_ratio=0.25) | `test_high_stockout_full_pipeline` | MATCH |
| 2 | Medium stockout + floor (sell_day_ratio=0.60) | `test_medium_stockout_with_floor` | MATCH |
| 3 | Normal + waste correction (sell_day_ratio=0.90) | `test_normal_stockout_original_behavior` | MATCH |
| 4 | Non-food not affected | `test_non_food_not_affected` | MATCH |

#### TestConstants (Bonus - not in design)

| # | Test Method | Description |
|---|-------------|-------------|
| 1 | `test_boost_thresholds_order` | Verifies threshold keys [0.30, 0.50, 0.70] and values [1.05, 1.15, 1.30] |
| 2 | `test_boost_max_cap` | Verifies max boost = 1.30 |
| 3 | `test_final_floor_is_20_percent` | Verifies floor ratio = 0.20 |

---

## 5. Design Principles Compliance

| Principle | Design Requirement | Implementation | Status |
|-----------|-------------------|----------------|--------|
| Minimal invasion | No method signature changes | No changes to method signatures | MATCH |
| Food-only | `is_food_category(mid_cd)` guard | Inside `elif is_food_category(mid_cd):` block (L1169) | MATCH |
| Toggle-capable | `STOCKOUT_BOOST_ENABLED` setting | `STOCKOUT_BOOST_ENABLED = True` (L1242), checked in function (L1263) | MATCH |
| sell_day_ratio reuse | Use existing parameter, no new DB queries | `sell_day_ratio` parameter used directly (L1181), no new queries | MATCH |

---

## 6. File Modification Summary

### 6-1. src/prediction/categories/food.py

| Design Item | Implementation | Status |
|-------------|----------------|--------|
| Add `STOCKOUT_BOOST_ENABLED` constant | L1242 | MATCH |
| Add `STOCKOUT_BOOST_THRESHOLDS` dict | L1243-1247 | MATCH |
| Add `get_stockout_boost_coefficient()` function | L1250-1271 | MATCH |
| Location: after `get_unified_waste_coefficient()` | After L981 (unified waste ends), before weather cross coefficients | MATCH |

### 6-2. src/prediction/improved_predictor.py

| Design Item | Implementation | Status |
|-------------|----------------|--------|
| Import `get_stockout_boost_coefficient` | L1172 | MATCH |
| `elif is_food_category(mid_cd):` block replacement | L1169-1253 | MATCH |
| A: stockout_freq + conditional waste_coef | L1180-1215 | MATCH |
| B: final_floor protection | L1217-1227 | MATCH |
| C: stockout_boost application | L1229-1242 | MATCH |
| ctx fields: stockout_freq, effective_waste_coef, stockout_boost | L1182, L1205, L1231 | MATCH |

### 6-3. tests/test_food_stockout_balance.py

| Design Item | Implementation | Status |
|-------------|----------------|--------|
| TestWasteCoefConditional: 5 cases | 5 cases (L22-78) | MATCH |
| TestFinalFloor: 4 cases | 4 cases (L85-127) | MATCH |
| TestStockoutBoost: 5 cases | 5 cases (L134-176) | MATCH |
| TestIntegration: 4 cases | 4 cases (L183-267) | MATCH |
| Total: 18 tests | 21 tests (18 + 3 bonus) | MATCH (exceeded) |

---

## 7. Interaction Verification

| Interaction Point | Design Description | Implementation | Status |
|-------------------|-------------------|----------------|--------|
| FoodWasteRateCalibrator | A exempts waste_coef, calibrator safety_days independent | Calibrator called separately in safety_stock (L1244-1247) | MATCH |
| unified_waste_coef | Calculation unchanged, only conditional application | `get_unified_waste_coefficient()` called before A block (L1174-1176) | MATCH |
| DemandClassifier | Food exempt, sell_day_ratio read-only | sell_day_ratio consumed at L1181, no writes | MATCH |
| ML ensemble | stockout_boost affects adjusted_prediction -> need_qty -> order_qty | Boost applied before safety_stock (L1234), ML at later stage | MATCH |
| MAX_ORDER_QTY_BY_CATEGORY | Upper cap still applies | Existing code at L1503+ unchanged | MATCH |

---

## 8. Match Rate Calculation

### Item Counts

| Category | Items | Matched | Gaps |
|----------|:-----:|:-------:|:----:|
| A: Waste conditional (branches+logic+logging) | 14 | 14 | 0 |
| B: Final floor (formula+guard+application) | 7 | 7 | 0 |
| C: Boost (constants+function+application) | 16 | 16 | 0 |
| ctx fields | 3 | 3 | 0 |
| Application order | 5 | 5 | 0 |
| Design principles | 4 | 4 | 0 |
| Tests (18 required) | 18 | 18 | 0 |
| File modifications | 6 | 6 | 0 |
| Interactions | 5 | 5 | 0 |
| **Total** | **78** | **78** | **0** |

### Match Rate: **100%** (78/78 items)

---

## 9. Differences Found

### Missing Features (Design O, Implementation X)

None.

### Added Features (Design X, Implementation O)

| Item | Implementation Location | Description | Impact |
|------|------------------------|-------------|--------|
| TestConstants class | tests/test_food_stockout_balance.py:274-296 | 3 bonus tests verifying constants | Positive (extra safety) |

### Changed Features (Design != Implementation)

None.

---

## 10. Verdict

```
+=============================================+
|  Match Rate: 100%  --  PASS                 |
|  78/78 items verified                       |
|  3 files, 21 tests (18 required + 3 bonus)  |
|  0 gaps found                               |
+=============================================+
```

All 3 design changes (A: waste coefficient conditional application, B: final floor protection, C: stockout boost feedback) are implemented exactly as specified in the design document. The implementation preserves the exact order (A -> B -> C), all threshold values, all ctx field additions, and all logging patterns. The test file covers all 18 designed test cases plus 3 bonus constant verification tests.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-03 | Initial gap analysis | gap-detector |
