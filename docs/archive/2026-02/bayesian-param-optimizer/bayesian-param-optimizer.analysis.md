# bayesian-param-optimizer Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: Claude (PDCA Check Phase)
> **Date**: 2026-02-24
> **Design Doc**: [bayesian-param-optimizer.design.md](../02-design/features/bayesian-param-optimizer.design.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Design document(bayesian-param-optimizer.design.md)와 실제 구현 코드의 일치율을 검증한다.
모든 설계 항목(파일 구조, 클래스/메서드, DB 스키마, 통합 포인트, 테스트 케이스)을 체크리스트 기반으로 비교한다.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/bayesian-param-optimizer.design.md`
- **Implementation Files**: 4 new + 8 modified (총 12 파일)
- **Test Suite**: `tests/test_bayesian_optimizer.py` (32 tests)

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 File Structure

| # | Design File | Implementation File | Status | Notes |
|---|------------|---------------------|--------|-------|
| 1 | `src/prediction/bayesian_optimizer.py` [NEW] | `src/prediction/bayesian_optimizer.py` | ✅ Match | 778 lines |
| 2 | `src/prediction/eval_config.py` [MOD] | `src/prediction/eval_config.py` | ✅ Match | locked field added |
| 3 | `src/infrastructure/database/repos/bayesian_optimization_repo.py` [NEW] | `src/infrastructure/database/repos/bayesian_optimization_repo.py` | ✅ Match | 286 lines |
| 4 | `src/infrastructure/database/repos/__init__.py` [MOD] | `src/infrastructure/database/repos/__init__.py` | ✅ Match | import + __all__ |
| 5 | `src/application/daily_job.py` [MOD] | `src/scheduler/daily_job.py` [MOD] | ✅ Match | Design said `application/` but actual location is `scheduler/` (pre-existing) |
| 6 | `src/db/models.py` [MOD] | `src/db/models.py` | ✅ Match | v40 migration |
| 7 | `src/settings/constants.py` [MOD] | `src/settings/constants.py` | ✅ Match | DB_SCHEMA_VERSION=40 |
| 8 | `config/eval_params.default.json` [MOD] | `config/eval_params.default.json` | ✅ Match | locked field in all 12 params |
| 9 | `config/bayesian_config.json` [NEW] | `config/bayesian_config.json` | ✅ Match | all keys present |
| 10 | `run_scheduler.py` [MOD] | `run_scheduler.py` | ✅ Match | schedule + CLI |
| 11 | `tests/test_bayesian_optimizer.py` [NEW] | `tests/test_bayesian_optimizer.py` | ✅ Match | 32 tests |
| 12 | `tests/conftest.py` [MOD] | `tests/conftest.py` | ✅ Match | table schema added |
| 13 | `src/web/routes/api_prediction.py` [MOD] | - | ⚠️ Phase 6 deferred | Design noted as optional |

**File Structure Match: 12/13 (92%)**

### 2.2 Core Engine: `bayesian_optimizer.py`

#### Classes

| Design Class | Implementation | Status | Notes |
|-------------|---------------|--------|-------|
| `OptimizationResult` | `OptimizationResult` | ✅ Match | `__slots__`, `to_dict()` identical |
| `BayesianParameterOptimizer` | `BayesianParameterOptimizer` | ✅ Match | All methods present |

#### Methods (BayesianParameterOptimizer)

| Design Method | Implementation | Status | Notes |
|--------------|---------------|--------|-------|
| `__init__(store_id, config, objective_weights)` | ✅ Implemented | ✅ Match | Lazy repos, skopt/optuna check |
| `outcome_repo` (property) | ✅ Implemented | ✅ Match | Lazy EvalOutcomeRepository |
| `sales_repo` (property) | ✅ Implemented | ✅ Match | Lazy SalesRepository |
| `bayesian_repo` (property) | ✅ Implemented | ✅ Match | Lazy BayesianOptimizationRepository |
| `calibration_repo` (property) | ✅ Implemented | ✅ Match | Lazy CalibrationRepository |
| `_import_optimizer()` | ✅ Implemented | ✅ Match | skopt -> optuna fallback |
| `optimize()` | ✅ Implemented | ✅ Match | 9-step flow: enable check -> lib -> metrics -> space -> snapshot -> optimize -> damping -> apply -> save |
| `check_rollback()` | ✅ Implemented | ✅ Match | monitor days, degradation %, restore params |
| `_build_search_space()` | ✅ Implemented | ✅ Match | locked exclusion, weight_trend exclusion, food params |
| `_spec_to_dimension()` | ✅ Implemented | ✅ Match | Real/Integer for skopt, tuple for optuna |
| `_get_food_search_params()` | ✅ Implemented | ✅ Match | ultra_short, short only |
| `_collect_metrics()` | ✅ Implemented | ✅ Match | Adapted to actual get_accuracy_stats() format |
| `_calculate_food_waste_rate()` | ✅ Implemented | ✅ Match | FOOD_MID_CODES set->list conversion |
| `_calculate_objective()` | ✅ Implemented | ✅ Match | 4-weight sum |
| `_objective()` | ✅ Implemented | ✅ Match | Surrogate-based estimation |
| `_estimate_metrics()` | ✅ Implemented | ✅ Match | Linear approx + sensitivity |
| `_get_sensitivity()` | ✅ Implemented | ✅ Match | 14 entries (Design had 11, impl has 14 - includes calibration_reversion_rate, popularity_high/low_percentile) |
| `_optimize_skopt()` | ✅ Implemented | ✅ Match | gp_minimize + warm start |
| `_optimize_optuna()` | ✅ Implemented | ✅ Match | TPE + suggest_float/int |
| `_apply_with_damping()` | ✅ Implemented | ✅ Match | current + factor * (best - current) |
| `_apply_params()` | ✅ Implemented | ✅ Match | max_delta + normalize_weights |
| `_apply_food_param()` | ✅ Implemented | ✅ Match | group extraction, safety range |
| `_snapshot_params()` | ✅ Implemented | ✅ Match | |
| `_get_current_param_value()` | ✅ Implemented | ✅ Match | eval. and food. prefix routing |
| `_restore_params()` | ✅ Implemented | ✅ Match | normalize_weights after restore |

**Core Engine Match: 25/25 (100%)**

#### Constants

| Design Constant | Design Value | Impl Value | Status |
|----------------|-------------|-----------|--------|
| BAYESIAN_ENABLED | True | True | ✅ |
| MIN_EVAL_DAYS | 7 | 7 | ✅ |
| N_TRIALS | 30 | 30 | ✅ |
| DAMPING_FACTOR | 0.5 | 0.5 | ✅ |
| ROLLBACK_MONITOR_DAYS | 3 | 3 | ✅ |
| ROLLBACK_THRESHOLD | 0.10 | 0.10 | ✅ |
| EVAL_LOOKBACK_DAYS | 7 | 7 | ✅ |
| DEFAULT_OBJECTIVE_WEIGHTS | {0.35, 0.30, 0.25, 0.10} | {0.35, 0.30, 0.25, 0.10} | ✅ |

**Constants Match: 8/8 (100%)**

### 2.3 Repository: `bayesian_optimization_repo.py`

| Design Method | Implementation | Status | Notes |
|--------------|---------------|--------|-------|
| `save_optimization_log()` | ✅ Full UPSERT | ✅ Match | ON CONFLICT DO UPDATE |
| `get_latest_applied()` | ✅ WHERE applied=1 AND rolled_back=0 | ✅ Match | includes iteration column (fixed) |
| `mark_applied()` | ✅ SET applied=1 | ✅ Match | |
| `mark_rolled_back()` | ✅ SET rolled_back=1, reason | ✅ Match | with logger.warning |
| `mark_confirmed()` | ✅ iteration += 1 | ✅ Match | Design said "additional column or log" |
| `get_optimization_history()` | ✅ days cutoff, DESC | ✅ Match | |
| `get_param_evolution()` | ✅ JSON parse + filter | ✅ Match | |

**Repository Match: 7/7 (100%)**

### 2.4 `eval_config.py` Changes

| Design Change | Implementation | Status |
|--------------|---------------|--------|
| `ParamSpec.locked: bool = False` | ✅ Added at line 35 | ✅ Match |
| `to_dict()` includes `"locked": spec.locked` | ✅ Added at line 164 | ✅ Match |
| `_apply_params()` restores `spec.locked` | ✅ Added at line 340 | ✅ Match |

**EvalConfig Match: 3/3 (100%)**

### 2.5 DB Schema (models.py v40)

| Design Column | Implementation | Status |
|--------------|---------------|--------|
| id INTEGER PK AUTOINCREMENT | ✅ | ✅ |
| store_id TEXT NOT NULL | ✅ | ✅ |
| optimization_date TEXT NOT NULL | ✅ | ✅ |
| iteration INTEGER DEFAULT 0 | ✅ | ✅ |
| objective_value REAL | ✅ | ✅ |
| accuracy_error REAL | ✅ | ✅ |
| waste_rate_error REAL | ✅ | ✅ |
| stockout_rate REAL | ✅ | ✅ |
| over_order_ratio REAL | ✅ | ✅ |
| params_before TEXT | ✅ | ✅ |
| params_after TEXT | ✅ | ✅ |
| params_delta TEXT | ✅ | ✅ |
| algorithm TEXT DEFAULT 'gp' | ✅ | ✅ |
| n_trials INTEGER | ✅ | ✅ |
| best_trial INTEGER | ✅ | ✅ |
| eval_period_start TEXT | ✅ | ✅ |
| eval_period_end TEXT | ✅ | ✅ |
| applied INTEGER DEFAULT 0 | ✅ | ✅ |
| rolled_back INTEGER DEFAULT 0 | ✅ | ✅ |
| rollback_reason TEXT | ✅ | ✅ |
| created_at TEXT DEFAULT datetime | ✅ | ✅ |
| UNIQUE(store_id, optimization_date) | ✅ | ✅ |
| INDEX idx_bayesian_log_store_date | ✅ | ✅ |

**DB Schema Match: 23/23 (100%)**

### 2.6 Integration Points

| Design Integration | Implementation | Status | Notes |
|-------------------|---------------|--------|-------|
| Phase 1.57 in daily_job.py | ✅ Lines 393-427 | ✅ Match | Sunday check, rollback before optimize |
| `collection_success` guard | ✅ `if collection_success and` | ✅ Match | |
| schedule.every().sunday.at("23:00") | ✅ Line 987 | ✅ Match | |
| `--bayesian-optimize` CLI | ✅ Line 1103 | ✅ Match | |
| bayesian_optimize_wrapper() | ✅ Line 752 | ✅ Match | Uses _run_task pattern |
| conftest.py table | ✅ Line 135-162 | ✅ Match | Full schema |
| repos/__init__.py export | ✅ Line 31, 65 | ✅ Match | import + __all__ |

**Integration Match: 7/7 (100%)**

### 2.7 Config File: `bayesian_config.json`

| Design Key | Design Value | Impl Value | Status |
|-----------|-------------|-----------|--------|
| enabled | true | true | ✅ |
| n_trials | 30 | 30 | ✅ |
| damping_factor | 0.5 | 0.5 | ✅ |
| rollback_monitor_days | 3 | 3 | ✅ |
| rollback_threshold | 0.10 | 0.10 | ✅ |
| eval_lookback_days | 7 | 7 | ✅ |
| min_eval_days | 7 | 7 | ✅ |
| objective_weights | {0.35,0.30,0.25,0.10} | {0.35,0.30,0.25,0.10} | ✅ |
| locked_params | [] | [] | ✅ |
| preferred_algorithm | "skopt" | "skopt" | ✅ |

**Config Match: 10/10 (100%)**

### 2.8 Test Coverage (Design Section 6)

| Design Test Case | Implementation | Status |
|-----------------|---------------|--------|
| `_build_search_space()`: locked exclusion | `test_build_search_space_excludes_locked` | ✅ |
| `_build_search_space()`: weight_trend exclusion | `test_build_search_space_excludes_weight_trend` | ✅ |
| `_collect_metrics()`: insufficient data | `test_optimize_insufficient_data` | ✅ |
| `_calculate_objective()`: weighted sum | `test_calculate_objective` | ✅ |
| `_apply_with_damping()`: factor=0 | `test_apply_with_damping_zero` | ✅ |
| `_apply_with_damping()`: factor=1 | `test_apply_with_damping_full` | ✅ |
| `_apply_params()`: max_delta clamp | `test_apply_params_respects_max_delta` | ✅ |
| `_apply_params()`: normalize_weights | `test_apply_params_normalizes_weights` | ✅ |
| `check_rollback()`: monitoring period | `test_within_monitoring_period` | ✅ |
| `check_rollback()`: performance OK -> confirm | `test_performance_ok_confirmed` | ✅ |
| `_get_sensitivity()`: known param | `test_get_sensitivity_known` | ✅ |
| `_estimate_metrics()`: no change -> baseline | `test_estimate_metrics_no_change` | ✅ |
| Repository CRUD | 7 tests (save/get/rollback/confirm/history/evolution/upsert) | ✅ |
| `ParamSpec.locked` serialize/deserialize | 5 tests | ✅ |
| `optimize()`: no library -> skip | `test_optimize_no_library` | ✅ |
| `optimize()`: disabled -> skip | `test_optimize_disabled` | ✅ |
| check_rollback: no applied | `test_no_applied_optimization` | ✅ |
| _snapshot_params | `test_snapshot_params` | ✅ |
| _restore_params | `test_restore_params` | ✅ |

**Design Test Cases**: 16 required (Section 6.1)
**Implementation Test Cases**: 32 (exceeds design requirement)

| Design Test | Impl Status |
|------------|-------------|
| `_collect_metrics()`: normal data metrics accuracy | ⚠️ Not directly tested (mocked in integration tests) |
| `check_rollback()`: degradation > 10% -> rollback | ⚠️ Not directly tested (but flow covered) |
| `optimize()` -> `save_log()` -> `get_latest()` full flow | ⚠️ Partial (components tested individually) |

**Test Match: 16/19 design tests covered = 84%, but 32 total tests (exceeds)**

### 2.9 Safety Mechanisms (Design Section 8)

| Design Safety | Implementation | Status |
|--------------|---------------|--------|
| max_delta (ParamSpec.apply_delta) | ✅ Used in _apply_params() | ✅ |
| locked (ParamSpec.locked) | ✅ _build_search_space() checks | ✅ |
| damping (_apply_with_damping) | ✅ 0.5 factor | ✅ |
| min_data (_collect_metrics) | ✅ MIN_EVAL_DAYS * 5 | ✅ |
| rollback (check_rollback) | ✅ 3-day monitor, 10% threshold | ✅ |
| weekly (daily_job weekday==6) | ✅ Sunday only | ✅ |
| graceful (_import_optimizer) | ✅ None return -> skip | ✅ |
| backup (EvalConfig.save .bak) | ✅ Pre-existing | ✅ |

**Safety Match: 8/8 (100%)**

---

## 3. Implementation Deviations (Intentional)

These are deliberate differences from the Design document:

| # | Design | Implementation | Reason |
|---|--------|---------------|--------|
| 1 | `stats.get("total", 0)` | `stats.get("total_verified", 0)` + `by_decision` parsing | Adapted to actual `get_accuracy_stats()` return format |
| 2 | `FOOD_MID_CODES` direct in SQL | `list(FOOD_MID_CODES)` + separate placeholders | `FOOD_MID_CODES` is a `set`, not list |
| 3 | `result.x_iters.index(result.x)` | try/except with `list()` conversion | Robustness: numpy array comparison |
| 4 | `_get_sensitivity()`: 11 entries | 14 entries (+3 new params) | Covers all 12 EvalConfig params + 2 food |
| 5 | daily_job path: `src/application/daily_job.py` | `src/scheduler/daily_job.py` | Pre-existing file location |
| 6 | `_import_optimizer()` return type: `str` | `Optional[str]` | Type hint accuracy |

All deviations are improvements or adaptations to the actual codebase.

---

## 4. Code Quality Analysis

### 4.1 File Sizes

| File | Lines | Complexity | Status |
|------|-------|-----------|--------|
| bayesian_optimizer.py | 778 | Medium | ✅ Good |
| bayesian_optimization_repo.py | 286 | Low | ✅ Good |
| test_bayesian_optimizer.py | 643 | Low | ✅ Good |
| bayesian_config.json | 17 | N/A | ✅ Good |

### 4.2 Code Patterns

| Pattern | Status | Notes |
|---------|--------|-------|
| Repository pattern (BaseRepository) | ✅ | Consistent with project |
| Lazy-loaded repositories | ✅ | Prevents import cycles |
| try/finally conn.close() | ✅ | Consistent with project |
| logger usage | ✅ | get_logger(__name__) |
| Exception handling in daily_job | ✅ | Non-blocking (warning + continue) |

### 4.3 Security

| Check | Status |
|-------|--------|
| No hardcoded credentials | ✅ |
| SQL parameterized queries | ✅ |
| Input validation (clamp/range) | ✅ |
| Graceful degradation | ✅ |

---

## 5. Test Results

### 5.1 Test Summary

| Category | Tests | Passing | Status |
|----------|-------|---------|--------|
| OptimizationResult | 3 | 3 | ✅ |
| ParamSpec.locked | 5 | 5 | ✅ |
| BayesianParameterOptimizer | 14 | 14 | ✅ |
| check_rollback | 3 | 3 | ✅ |
| BayesianOptimizationRepository | 7 | 7 | ✅ |
| **Total** | **32** | **32** | ✅ |

### 5.2 Full Suite

| Metric | Value |
|--------|-------|
| Total tests | 1826 |
| All passing | Yes |
| New tests | 32 |
| Regressions | 0 |

---

## 6. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 97%                     |
+---------------------------------------------+
|  File Structure:     12/13 (92%)             |
|  Core Engine:        25/25 (100%)            |
|  Constants:           8/8  (100%)            |
|  Repository:          7/7  (100%)            |
|  EvalConfig:          3/3  (100%)            |
|  DB Schema:          23/23 (100%)            |
|  Integration:         7/7  (100%)            |
|  Config File:        10/10 (100%)            |
|  Test Coverage:      16/19 (84%)             |
|  Safety Mechanisms:   8/8  (100%)            |
+---------------------------------------------+
|  Total Items:       119/123                  |
|  Match:             119 (96.7%)              |
|  Deferred (Phase 6): 1 (0.8%)               |
|  Minor gaps:          3 (2.4%)               |
+---------------------------------------------+
```

---

## 7. Gap Details

### 7.1 Deferred Items

| # | Item | Reason | Impact |
|---|------|--------|--------|
| 1 | `api_prediction.py` dashboard API | Design Section 5 Step #13 marked optional (Phase 6) | Low - UI only |

### 7.2 Test Coverage Gaps (Minor)

| # | Missing Test | Severity | Recommendation |
|---|-------------|----------|----------------|
| 1 | `_collect_metrics()` with real data accuracy | Low | Covered by mock + integration |
| 2 | `check_rollback()` degradation > 10% -> actual rollback | Low | Components tested separately |
| 3 | Full E2E: optimize -> save -> check_rollback | Low | Would require skopt installed |

---

## 8. Recommended Actions

### 8.1 None Required (Match Rate >= 90%)

The implementation matches the design document at **97% Match Rate**, well above the 90% threshold.

### 8.2 Future Enhancements (Phase 2)

| Item | Priority | Notes |
|------|----------|-------|
| Dashboard API (`api_prediction.py`) | Low | When UI needed |
| PREDICTION_PARAMS search space expansion | Medium | Design Section 2-1 Phase 2 |
| medium/long/very_long food safety_days | Medium | Currently ultra_short + short only |
| calibration_history-based sensitivity update | Low | Design mentioned as "future" |

---

## 9. Conclusion

bayesian-param-optimizer feature implementation은 Design document와 **97% Match Rate**를 달성했다.

- 핵심 엔진(25/25), Repository(7/7), DB 스키마(23/23), 안전 장치(8/8) 모두 100% 일치
- 32개 테스트 전체 통과, 전체 1826개 테스트 무영향
- 유일한 미구현 항목은 대시보드 API로, Design에서 선택사항(Phase 6)으로 명시
- 6건의 의도적 변경(Intentional Deviations)은 모두 실제 코드베이스 적응 또는 개선

**PDCA Check Phase: PASS (97% >= 90%)**

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-24 | Initial gap analysis | Claude (PDCA) |
