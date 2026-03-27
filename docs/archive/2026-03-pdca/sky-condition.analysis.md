# sky-condition (Phase A-2) Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Feature**: Phase A-2 -- Sky Condition (weather_cd_nm) Coefficient
> **Analyst**: gap-detector
> **Date**: 2026-03-08
> **Design Doc**: project_knowledge_improvement_plan.md (Phase A-2 section)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the Phase A-2 sky-condition coefficient implementation matches the design specification in `project_knowledge_improvement_plan.md`. The feature adds weather type-based demand adjustment coefficients (cloudy, fog, dust storm, etc.) using the already-collected `weather_cd_nm` data from `external_factors`.

### 1.2 Analysis Scope

- **Design Document**: `project_knowledge_improvement_plan.md` Phase A-2 section
- **Implementation Files**:
  - `bgf_auto/src/settings/constants.py` (L487-488)
  - `bgf_auto/src/prediction/coefficient_adjuster.py` (L130-141, L372-406, L577-585)
- **Test Files**: None (no dedicated Phase A-2 tests found)
- **Analysis Date**: 2026-03-08

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 Checklist Verification

| # | Checklist Item | Design | Implementation | Status |
|---|----------------|--------|----------------|--------|
| C-1 | Toggle: SKY_CONDITION_ENABLED | constants.py | constants.py L488: `SKY_CONDITION_ENABLED = True` | PASS |
| C-2 | Toggle OFF returns 1.0 | Required | L384: `if not SKY_CONDITION_ENABLED: return 1.0` | PASS |
| C-3 | No double-application with precip | Required | sky_coef is independent signal, merged via *= | PASS |
| C-4 | NULL/empty fallback to 1.0 | Required | L390-396: empty factors + empty sky_nm both return 1.0 | PASS |
| C-5 | Logging added | Required | L399-402: debug log on non-1.0 coef + L582-585: apply() debug log | PASS |

### 2.2 Coefficient Values

| Weather Type | Design | Implementation (L134-141) | Status |
|:-------------|:------:|:-------------------------:|:------:|
| 맑음 (clear) | 1.00 | 1.00 | PASS |
| 구름많음 (mostly cloudy) | 0.98 | 0.98 | PASS |
| 흐림 (overcast) | 0.95 | 0.95 | PASS |
| 안개 (fog) | 0.93 | 0.93 | PASS |
| 황사 (dust storm) | 0.90 | 0.90 | PASS |
| 소나기 (shower) | 1.00 | 1.00 | PASS |

All 6 coefficient values match exactly.

### 2.3 Integration Point

| Item | Design | Implementation | Status |
|------|--------|----------------|--------|
| Method location | CoefficientAdjuster | `get_sky_condition_coefficient()` L372-406 | PASS |
| Merge strategy | weather_coef *= sky_coef | L581: `weather_coef *= sky_coef` | PASS |
| Merge position | After precip_coef, before food_wx_coef | L574-585: precip->sky->food_wx order | PASS |
| Unknown type fallback | 1.0 (dict.get default) | L398: `.get(sky_nm, 1.0)` | PASS |
| Category independence | All categories equally affected | Method takes no mid_cd param | PASS |

### 2.4 Data Flow Verification

| Stage | Key Name | File | Status |
|-------|----------|------|--------|
| Collection | `weather_cd_nm` (in forecast_precipitation dict) | weather_collector.py L192 | PASS |
| DB Storage | `factor_key="weather_cd_nm_forecast"` | daily_job.py L1227 | PASS |
| DB Query | `factor_map.get('weather_cd_nm', '')` | coefficient_adjuster.py L394 | **FAIL** |

---

## 3. Gaps Found

### 3.1 Gap Summary

| # | Severity | Category | Description |
|---|:--------:|----------|-------------|
| G-1 | HIGH | Bug | factor_key mismatch: DB stores `weather_cd_nm_forecast`, code queries `weather_cd_nm` |
| G-2 | MEDIUM | Efficiency | Redundant ExternalFactorRepository calls (3 separate instances for same date) |
| G-3 | MEDIUM | Testing | No dedicated Phase A-2 tests |

### G-1: factor_key Mismatch (HIGH -- Functional Bug)

**Root Cause**: The `daily_job.py` saves the weather type to `external_factors` with the key `"weather_cd_nm_forecast"` (L1227), following the same `_forecast` suffix pattern used by `rain_rate_forecast`, `rain_qty_forecast`, and `is_snow_forecast`. However, `get_sky_condition_coefficient()` queries for `"weather_cd_nm"` (L394) -- a key that is never stored.

**Evidence**:
- `daily_job.py` L1227: `factor_key="weather_cd_nm_forecast"`
- `coefficient_adjuster.py` L394: `factor_map.get('weather_cd_nm', '')`
- Compare with `get_precipitation_for_date()` which correctly uses `'rain_rate_forecast'` (L422), `'rain_qty_forecast'` (L429), `'is_snow_forecast'` (L436)

**Impact**: The `get_sky_condition_coefficient()` method will **always return 1.0** in production because `factor_map.get('weather_cd_nm', '')` will always return `''` (the key does not exist in the DB). The feature is effectively dead code despite being enabled.

**Fix**: Change L394 from `factor_map.get('weather_cd_nm', '')` to `factor_map.get('weather_cd_nm_forecast', '')`.

### G-2: Redundant Repository Calls (MEDIUM -- Performance)

In `apply()`, three separate methods each instantiate a new `ExternalFactorRepository()` and call `get_factors(date_str, factor_type='weather')` for the same date:

1. `get_weather_coefficient()` (L277-278) -- temperature data
2. `get_precipitation_coefficient()` -> `get_precipitation_for_date()` (L416-417) -- rain data
3. `get_sky_condition_coefficient()` (L388-389) -- sky condition data

Additionally, for food categories, `get_precipitation_for_date()` is called again at L592.

That is 3-4 separate DB queries for the same `(date, 'weather')` key set. This is the same pattern that existed before Phase A-2 (rain_rate already had its own call), but adding sky_condition adds yet another.

**Impact**: Low per-item (SQLite is fast for small reads), but across 200+ items this is ~600 extra queries per prediction run. Not a blocking issue.

**Recommendation**: Future optimization -- cache weather factors per date in `apply()` and pass to sub-methods. This is a pre-existing pattern, not introduced by Phase A-2.

### G-3: No Dedicated Tests (MEDIUM)

No test file or test class exists for the Phase A-2 sky-condition feature. Searching for `test_sky`, `TestSky`, or `sky_condition` in the test directory returned zero results. The existing `test_precipitation.py` (29 tests) does not cover sky condition at all.

**Expected test scenarios**:
1. Each of the 6 weather types returns correct coefficient
2. Unknown weather type returns 1.0
3. Empty/NULL weather_cd_nm returns 1.0
4. SKY_CONDITION_ENABLED=False returns 1.0
5. sky_coef integrates into weather_coef in apply()
6. Exception handling returns 1.0

---

## 4. Match Rate Calculation

| Category | Total Items | Pass | Fail | Rate |
|----------|:-----------:|:----:|:----:|:----:|
| Checklist (C-1 to C-5) | 5 | 5 | 0 | 100% |
| Coefficient Values (6 types) | 6 | 6 | 0 | 100% |
| Integration Points | 5 | 5 | 0 | 100% |
| Data Flow | 3 | 2 | 1 | 66.7% |
| Tests | 1 | 0 | 1 | 0% |
| **Total** | **20** | **18** | **2** | **90.0%** |

### Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 90.0% | PASS (with critical fix needed) |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **90.0%** | **PASS** (conditional) |

```
+---------------------------------------------+
|  Overall Match Rate: 90.0%                  |
+---------------------------------------------+
|  PASS:  18 items (90%)                      |
|  FAIL:   2 items (10%)                      |
|    G-1: factor_key mismatch (HIGH)          |
|    G-3: no tests (MEDIUM)                   |
+---------------------------------------------+
```

---

## 5. Double-Application Analysis

A key concern for this feature is whether the sky_coef can double-apply with existing precipitation coefficients. Analysis:

| Signal | Source | Controls | Independent? |
|--------|--------|----------|:------------:|
| rain_rate (probability) | `rain_rate_forecast` | PRECIPITATION_COEFFICIENTS rules | Yes |
| rain_qty (amount) | `rain_qty_forecast` | RAIN_QTY_BASE_COEFFICIENTS levels | Yes |
| sky condition | `weather_cd_nm_forecast` | SKY_CONDITION_COEFFICIENTS dict | Yes |
| food precip cross | `rain_rate_forecast` | FOOD_PRECIPITATION_CROSS_COEFFICIENTS | Yes |

**Conclusion**: The sky condition coefficient is correctly independent. The "소나기" (shower) entry is set to 1.0, avoiding double-counting with the rain_rate coefficient. Overcast/fog/dust are genuinely separate signals from precipitation amount -- they reduce outdoor activity even without rain. The design intent is correctly implemented.

However, in a worst-case scenario (heavy rain + overcast + food category), the compound effect would be:
- `weather_coef = 1.0 * precip(0.85) * sky(0.95) = 0.8075`
- Plus `food_wx_coef` and `food_precip_coef` applied separately.
- The compound floor in `_apply_multiplicative` (L717: `base_prediction * 0.15`) provides protection.

---

## 6. Recommended Actions

### 6.1 Immediate (G-1 Fix Required)

| Priority | Item | File | Line |
|----------|------|------|------|
| HIGH | Fix factor_key: `'weather_cd_nm'` -> `'weather_cd_nm_forecast'` | coefficient_adjuster.py | L394 |

### 6.2 Short-term (G-3 Tests)

| Priority | Item | File | Expected Tests |
|----------|------|------|:--------------:|
| MEDIUM | Add Phase A-2 test class to test_precipitation.py | tests/test_precipitation.py | 6-8 tests |

Test scenarios to add:
1. `test_sky_clear_returns_1` -- 맑음 = 1.0
2. `test_sky_cloudy` -- 구름많음 = 0.98
3. `test_sky_overcast` -- 흐림 = 0.95
4. `test_sky_fog` -- 안개 = 0.93
5. `test_sky_dust` -- 황사 = 0.90
6. `test_sky_shower` -- 소나기 = 1.0 (no double-apply)
7. `test_sky_unknown_type` -- 알 수 없는 유형 = 1.0
8. `test_sky_disabled` -- SKY_CONDITION_ENABLED=False = 1.0
9. `test_sky_empty_value` -- 빈 문자열 = 1.0
10. `test_sky_integrated_in_apply` -- apply()에서 weather_coef에 곱해지는지

### 6.3 Long-term (Performance)

| Priority | Item | Notes |
|----------|------|-------|
| LOW | Cache weather factors per date in apply() | Pre-existing pattern, not Phase A-2 specific |

---

## 7. Files Analyzed

| File | Path | Lines Modified | Purpose |
|------|------|:--------------:|---------|
| constants.py | `bgf_auto/src/settings/constants.py` | 2 (L487-488) | SKY_CONDITION_ENABLED toggle |
| coefficient_adjuster.py | `bgf_auto/src/prediction/coefficient_adjuster.py` | 34 (L130-141, L372-406, L577-585) | SKY_CONDITION_COEFFICIENTS + get_sky_condition_coefficient() + apply() integration |
| daily_job.py | `bgf_auto/src/scheduler/daily_job.py` | 0 (reference only) | DB storage key verification |
| weather_collector.py | `bgf_auto/src/collectors/weather_collector.py` | 0 (reference only) | Collection key verification |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-08 | Initial analysis | gap-detector |
