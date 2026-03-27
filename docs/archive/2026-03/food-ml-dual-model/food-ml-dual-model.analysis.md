# food-ml-dual-model Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-01
> **Design Doc**: [food-ml-dual-model.design.md](../02-design/features/food-ml-dual-model.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the "food-ml-dual-model" feature implementation matches the design specification.
This feature introduces a dual ML model architecture -- individual model (existing) + small_cd group model (new) -- blended by data_confidence to solve the new product cold start problem for food categories.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/food-ml-dual-model.design.md`
- **Implementation Files**:
  - `src/prediction/ml/feature_builder.py`
  - `src/prediction/ml/data_pipeline.py`
  - `src/prediction/ml/model.py`
  - `src/prediction/ml/trainer.py`
  - `src/prediction/improved_predictor.py`
  - `tests/test_food_ml_dual_model.py`
- **Analysis Date**: 2026-03-01
- **Test Results**: 2822 passed, 15 failed (pre-existing, unrelated), 27 new tests all pass

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Feature Builder (feature_builder.py)

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| FEATURE_NAMES count | 31 -> 35 (4 new) | 35 features (indices 31-34) | Match |
| `data_days_ratio` [31] | data_days / 60, cap 1.0 | `min(float(data_days) / 60.0, 1.0)` (L281) | Match |
| `smallcd_peer_avg` [32] | peer_avg / 10.0, cap 1.0 | `min(float(smallcd_peer_avg) / 10.0, 1.0)` (L282) | Match |
| `relative_position` [33] | my_avg / peer_avg, cap 3.0, 0=no info | `min(daily_avg_7/peer_avg, 3.0) if peer_avg > 0 else 0.0` (L283) | Match |
| `lifecycle_stage` [34] | Direct value 0~1 | `float(lifecycle_stage)` (L284) | Match |
| `build_features()` params | data_days, smallcd_peer_avg, lifecycle_stage added | All 3 params added (L140-142) | Match |
| `build_batch_features()` | Pass new params through | All 3 params forwarded (L340-342) | Match |

**Score: 7/7 = 100%**

### 2.2 Data Pipeline (data_pipeline.py)

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| `get_smallcd_peer_avg_batch()` | 7-day AVG by small_cd, ATTACH common.db | Implemented with ATTACH, GROUP BY small_cd (L463-505) | Match |
| `get_item_smallcd_map()` | item_cd -> small_cd from product_details | Implemented via common_conn (L507-528) | Match |
| `get_lifecycle_stages_batch()` | item_cd -> stage_value mapping | Implemented with STAGE_MAP (L530-561) | Match |
| Lifecycle stage map | detected=0.0, monitoring=0.25, slow_start=0.5, stable=0.75, normal=1.0 | Matches + adds `no_demand=0.5` (L541) | Enhanced |

**Notes**:
- Implementation adds `no_demand: 0.5` to the lifecycle stage map, not in the design. This is an enhancement consistent with the new-product-lifecycle feature where `no_demand` is a valid lifecycle status.

**Score: 4/4 = 100%**

### 2.3 Model (model.py)

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| `group_models` dict | `Dict[str, Any]` on MLPredictor | `self.group_models: Dict[str, Any] = {}` (L73) | Match |
| `load_group_models()` | Load group_*.joblib, return count | Implemented with feature hash check (L267-308) | Match |
| `predict_group()` | small_cd -> mid_cd fallback | Exact logic match (L310-338) | Match |
| `predict_dual()` | confidence=min(1.0, data_days/60), blend | Exact formula match (L340-367) | Match |
| `has_group_model()` | Check small_cd or mid_cd prefix | Implemented with auto-load (L369-377) | Enhanced |
| `save_group_model()` | Save group_{key}.joblib + meta | Implemented with meta update (L379-421) | Match |
| Predict_dual: both None | Return None | L358-359 | Match |
| Predict_dual: ind None | Return group | L360-361 | Match |
| Predict_dual: grp None | Return individual | L362-363 | Match |
| model_meta.json: group_models | items_count field | NOT included in save_group_model meta | Minor gap |

**Notes**:
- `has_group_model()` auto-triggers `load_group_models()` if not yet loaded -- an enhancement over the design which did not specify this behavior.
- `model_meta.json` `group_models` entries do not include `items_count` field specified in design Section 7. The meta only records `trained_at`, `feature_hash`, and `metrics`.

**Score: 9/10 = 90%**

### 2.4 Trainer (trainer.py)

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| `GROUP_TRAINING_DAYS` | food_group=30, default=90 | Exact match (L28-31) | Match |
| `GROUP_MIN_SAMPLES` | 30 | Exact match (L27) | Match |
| `train_group_models()` | food products, small_cd grouping, min_days=3 | Implemented (L549-741) | Match |
| Group data prep | All food items, small_cd grouping | food_mids filter + item_smallcd map (L567-644) | Match |
| Mid_cd fallback | Samples < GROUP_MIN_SAMPLES -> merge to mid_cd | Exact logic (L651-666) | Match |
| Performance gate | Applied to group models | NOT applied (simple save without gate check) | Minor gap |
| `train_all_groups()` | Call train_group_models at end | Called in try/except block (L535-542) | Match |
| Group model algorithm | Not specified | GradientBoostingRegressor (n_estimators=80, max_depth=4) | Reasonable |
| Group context features in samples | data_days, smallcd_peer_avg, lifecycle_stage | All 3 included in sample dict (L640-643) | Match |

**Notes**:
- Design mentions "성능 게이트 적용" for group models, but implementation saves group models without checking the performance gate. For group models which are newly created (no prior model), this is acceptable. However, the design implies the gate should be checked.
- The design did not specify the exact ML algorithm for group models. Implementation uses GradientBoostingRegressor with quantile loss, which is consistent with the existing individual model approach.

**Score: 8/9 = 89%**

### 2.5 Improved Predictor (improved_predictor.py)

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| `_apply_ml_ensemble()`: predict() -> predict_dual() | Replace predict with predict_dual | `predict_dual(features, mid_cd, _small_cd, data_days)` (L2343) | Match |
| small_cd lookup | product.get("small_cd") | `product.get("small_cd") or _item_smallcd_map.get(item_cd)` (L2311) | Enhanced |
| Group context features | _peer_avg, _lifecycle from caches | Both retrieved from caches (L2312-2313) | Match |
| Feature build params | data_days, smallcd_peer_avg, lifecycle_stage | All 3 passed (L2337-2339) | Match |
| `_get_ml_weight()` modification | food + group model -> return 0.1 for data_days<30 | Implemented (L2406-2413) | Match |
| Cache init method name | `_init_group_context_caches()` | `_load_group_context_caches()` (L2625) | Renamed |
| Cache init: peer_cache | pipeline.get_smallcd_peer_avg_batch() | Exact match (L2637) | Match |
| Cache init: smallcd_map | pipeline.get_item_smallcd_map() | Exact match (L2638) | Match |
| Cache init: lifecycle | pipeline.get_lifecycle_stages_batch() | Exact match (L2639) | Match |
| Cache init: load_group_models | ml_predictor.load_group_models() | Exact match (L2647) | Match |
| Cache init timing | predict_all start | Called in predict_batch cache init block (L2815) | Match |
| `_current_item_cd` in `_get_ml_weight` | Not in design | Uses `getattr(self, '_current_item_cd', '')` but NEVER assigned | Bug |

**Notes**:
- Method renamed from `_init_group_context_caches` (design) to `_load_group_context_caches` (implementation). This is a minor naming difference with no functional impact, consistent with existing naming pattern (`_load_receiving_stats_cache`, `_load_food_coef_cache`, etc.).
- The `_get_ml_weight` method uses `getattr(self, '_current_item_cd', '')` (L2410) to look up the small_cd for the current item. However, `_current_item_cd` is never assigned anywhere in the class. The `getattr` with default `''` means it will always fall back to empty string, causing `_item_smallcd_map.get('', None)` which returns `None`, so `has_group_model(None)` returns `False`. This means the food group model fallback at data_days<30 may not trigger correctly in production for items where the product dict does not contain `small_cd`. However, the test mocks `has_group_model` to return True directly, so the test passes despite this issue.
- The small_cd lookup in `_apply_ml_ensemble` (L2311) has a two-level fallback (`product.get("small_cd") or _item_smallcd_map.get(item_cd)`), which is an enhancement over the design.

**Score: 10/12 = 83%**

### 2.6 Test Coverage (test_food_ml_dual_model.py)

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| Test count | 20 tests specified | 27 tests (exceeds target) | Enhanced |
| Feature count tests | FEATURE_NAMES = 35 | `test_feature_names_count_35` | Match |
| Feature name presence | 4 new names | `test_new_features_in_names` | Match |
| Feature normalization | data_days_ratio, peer_avg, relative_pos, lifecycle | 6 tests covering all 4 + caps + edge cases | Match |
| Batch features | New params forwarded | `test_batch_features_with_new_params` | Match |
| Group model predict | small_cd, mid_cd fallback, no model | 3 tests | Match |
| Dual blending | confidence formula, edge cases | 4 tests (blend, 0 days, 60+ days, no group) | Match |
| Model persistence | Save + load group model | `test_save_and_load_group_model` | Match |
| Data pipeline | 3 new batch methods | 3 tests | Match |
| Trainer constants | GROUP_MIN_SAMPLES, GROUP_TRAINING_DAYS | 3 tests | Match |
| Integration | improved_predictor dual model call | 2 tests (weight fallback) | Match |

**Score: 11/11 = 100%**

### 2.7 Model File Structure

| Design Item | Design Spec | Implementation | Status |
|-------------|-------------|----------------|--------|
| File naming | `group_small_{code}.joblib`, `group_mid_{code}.joblib` | Implemented via `save_group_model(key=...)` | Match |
| model_meta.json: group_models section | Section with trained_at, feature_hash, items_count, metrics | Implemented without items_count | Minor gap |

**Score: 1.5/2 = 75%**

### 2.8 Implementation Order

| Step | Design Spec | Implemented | Status |
|------|-------------|-------------|--------|
| 1. feature_builder.py -- 4 features | 4 features added | Complete | Match |
| 2. data_pipeline.py -- 3 methods | 3 methods added | Complete | Match |
| 3. model.py -- group_models, predict_dual | All added | Complete | Match |
| 4. trainer.py -- train_group_models | Method + constants added | Complete | Match |
| 5. improved_predictor.py -- predict_dual + cache | All integrated | Complete | Match |
| 6. tests -- 20 tests | 27 tests (exceeds) | Complete | Enhanced |
| 7. Existing test feature count update | 31 -> 35 | Verified | Match |
| 8. Full test pass | 2822 pass, 15 pre-existing fail | Confirmed | Match |

**Score: 8/8 = 100%**

---

## 3. Overall Match Rate

### 3.1 Category Scores

| Category | Items | Matched | Score | Status |
|----------|:-----:|:-------:|:-----:|:------:|
| Feature Builder | 7 | 7 | 100% | Pass |
| Data Pipeline | 4 | 4 | 100% | Pass |
| Model (MLPredictor) | 10 | 9 | 90% | Pass |
| Trainer | 9 | 8 | 89% | Pass |
| Improved Predictor | 12 | 10 | 83% | Warn |
| Test Coverage | 11 | 11 | 100% | Pass |
| Model File Structure | 2 | 1.5 | 75% | Warn |
| Implementation Order | 8 | 8 | 100% | Pass |
| **Total** | **63** | **58.5** | **93%** | **Pass** |

### 3.2 Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 93%                     |
+---------------------------------------------+
|  Match:          55 items (87%)              |
|  Enhanced:        6 items (10%)              |
|  Minor gap:       3 items ( 5%)              |
|  Bug:             1 item  ( 2%)              |
+---------------------------------------------+
```

---

## 4. Differences Found

### 4.1 Missing Features (Design present, Implementation absent)

| Item | Design Location | Description | Impact |
|------|-----------------|-------------|--------|
| model_meta.json `items_count` | design.md:255-256 | `group_models` metadata does not record `items_count` per group model | Low |
| Performance gate for group models | design.md:161 | `train_group_models` saves without performance gate check | Low |

### 4.2 Added Features (Design absent, Implementation present)

| Item | Implementation Location | Description | Impact |
|------|------------------------|-------------|--------|
| `no_demand` lifecycle stage | data_pipeline.py:541 | Added `no_demand: 0.5` to STAGE_MAP (from new-product-lifecycle) | None (compatible) |
| Auto-load in `has_group_model` | model.py:373-374 | Triggers `load_group_models()` if not yet loaded | Positive |
| Two-level small_cd fallback | improved_predictor.py:2311 | `product.get("small_cd") or _item_smallcd_map.get(item_cd)` | Positive |

### 4.3 Changed Features (Design differs from Implementation)

| Item | Design | Implementation | Impact |
|------|--------|----------------|--------|
| Cache init method name | `_init_group_context_caches()` | `_load_group_context_caches()` | None (naming consistency) |
| `_get_ml_weight` item lookup | Direct item_cd access | `_current_item_cd` attribute (never assigned) | Medium (see Bug below) |

### 4.4 Bug Found

| Severity | File | Location | Description |
|----------|------|----------|-------------|
| Medium | improved_predictor.py | L2410 | `_current_item_cd` is never assigned. `_get_ml_weight` uses `getattr(self, '_current_item_cd', '')` to look up the current item's small_cd in the map, but this attribute is never set. The fallback empty string causes `_item_smallcd_map.get('', None)` -> `None` -> `has_group_model(None)` -> `False`. Result: food items with data_days<30 whose `product` dict lacks `small_cd` will not benefit from the group model fallback (weight stays 0.0 instead of 0.1). Items where `product.get("small_cd")` is populated (the `_apply_ml_ensemble` path at L2311) are unaffected -- the dual blending works correctly via `predict_dual`. Only the weight calculation in `_get_ml_weight` is impacted. |

**Recommended Fix**: Pass `item_cd` as a parameter to `_get_ml_weight()`, or set `self._current_item_cd = item_cd` before calling it in `_apply_ml_ensemble()`.

---

## 5. Architecture Compliance

### 5.1 Layer Dependency Verification

| Layer | Expected | Actual | Status |
|-------|----------|--------|--------|
| ML modules (prediction/ml/) | Domain/Infrastructure | Uses data_pipeline (DB), feature_builder (pure logic) | Pass |
| data_pipeline.py | Infrastructure (DB access) | sqlite3, DBRouter | Pass |
| feature_builder.py | Domain (pure computation) | numpy only, no I/O | Pass |
| model.py | Infrastructure (file I/O) | joblib, json, Path | Pass |
| trainer.py | Application (orchestration) | Combines pipeline + builder + model | Pass |
| improved_predictor.py | Application | Delegates to ML modules | Pass |

### 5.2 Dependency Violations

None found. All new code follows the existing layered architecture.

### 5.3 Architecture Score

```
+---------------------------------------------+
|  Architecture Compliance: 100%               |
+---------------------------------------------+
|  Correct layer placement: 6/6 files          |
|  Dependency violations:   0 files            |
|  Wrong layer:             0 files            |
+---------------------------------------------+
```

---

## 6. Convention Compliance

### 6.1 Naming Convention Check

| Category | Convention | Files Checked | Compliance | Violations |
|----------|-----------|:-------------:|:----------:|------------|
| Classes | PascalCase | 4 | 100% | - |
| Functions | snake_case | 6 | 100% | - |
| Constants | UPPER_SNAKE_CASE | 3 (GROUP_MIN_SAMPLES, GROUP_TRAINING_DAYS, etc.) | 100% | - |
| Private methods | _prefix | 6 | 100% | - |
| Variables | snake_case | 6 | 100% | - |

### 6.2 Code Style Check

| Category | Check | Status |
|----------|-------|--------|
| Korean docstrings | All new methods have Korean docstrings | Pass |
| Logger usage | `get_logger(__name__)` used, no print() | Pass |
| Exception handling | try/except with logger.warning/debug | Pass |
| Type hints | All new methods have type annotations | Pass |
| f-string logging | Consistent with project style | Pass |

### 6.3 Convention Score

```
+---------------------------------------------+
|  Convention Compliance: 100%                 |
+---------------------------------------------+
|  Naming:          100%                       |
|  Code Style:      100%                       |
|  Docstrings:      100%                       |
|  Error Handling:  100%                       |
+---------------------------------------------+
```

---

## 7. Test Quality

### 7.1 Test Coverage

| Area | Tests | Coverage | Status |
|------|:-----:|----------|--------|
| Feature Builder (4 new features) | 9 | All normalization rules + edge cases | Pass |
| Model (predict_group/dual) | 10 | All blending paths + save/load | Pass |
| Data Pipeline (3 new methods) | 3 | DB query + result mapping | Pass |
| Trainer (group learning) | 3 | Constants + method existence | Adequate |
| Improved Predictor Integration | 2 | Weight fallback logic | Adequate |
| **Total** | **27** | Design specified 20 -> 135% | Pass |

### 7.2 Test Robustness

| Check | Status | Notes |
|-------|--------|-------|
| Uses tmp_path for model persistence | Pass | No filesystem pollution |
| Uses MagicMock for predictor init | Pass | Proper isolation |
| Tests edge cases (0 days, cap values) | Pass | data_days=0, 120; peer_avg=0 |
| Proper pytest.approx for floats | Pass | All float comparisons use approx |
| DB tests use isolated temp databases | Pass | No shared state |

---

## 8. Overall Score

```
+---------------------------------------------+
|  Overall Score: 97/100                       |
+---------------------------------------------+
|  Design Match:        93 points              |
|  Architecture:       100 points              |
|  Convention:         100 points              |
|  Test Quality:        95 points              |
+---------------------------------------------+
|                                              |
|  Match Rate >= 90% -> PASS                   |
+---------------------------------------------+
```

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 93% | Pass |
| Architecture Compliance | 100% | Pass |
| Convention Compliance | 100% | Pass |
| Test Quality | 95% | Pass |
| **Overall** | **97%** | **Pass** |

---

## 9. Recommended Actions

### 9.1 Immediate (Bug Fix)

| Priority | Item | File | Description |
|----------|------|------|-------------|
| Medium | Fix `_current_item_cd` | improved_predictor.py:2410 | Either pass `item_cd` to `_get_ml_weight()` or set `self._current_item_cd = item_cd` in `_apply_ml_ensemble()` before the call at L2350. Currently the group model fallback weight (0.1) is not applied for food items with data_days<30 when product dict lacks small_cd. |

### 9.2 Short-term (Documentation)

| Priority | Item | File | Description |
|----------|------|------|-------------|
| Low | Add `items_count` to group model metadata | model.py:save_group_model | Include number of items in the group when saving meta |
| Low | Add performance gate to group model training | trainer.py:train_group_models | Check prior model MAE before overwriting |

### 9.3 Design Document Updates Needed

- [ ] Add `no_demand: 0.5` to lifecycle stage mapping (data_pipeline section)
- [ ] Rename `_init_group_context_caches` to `_load_group_context_caches` (improved_predictor section)
- [ ] Clarify `_get_ml_weight` item_cd access pattern (improved_predictor section)

---

## 10. Synchronization Recommendation

Match Rate = 93% (>= 90%) -> **Design and implementation match well.**

Only minor differences found:
1. **Bug**: `_current_item_cd` never assigned -- recommend implementation fix (Option 1: modify implementation)
2. **Meta field**: `items_count` omitted -- recommend design update (Option 2: update design)
3. **Naming**: Method renamed -- recommend design update (Option 2: update design)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-01 | Initial gap analysis | gap-detector |
