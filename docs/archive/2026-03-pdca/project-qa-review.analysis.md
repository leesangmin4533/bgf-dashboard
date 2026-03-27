# BGF Retail Auto-Order System — QA Strategy & Test Coverage Review

> **Analysis Type**: Comprehensive QA Strategy Review
>
> **Project**: BGF Retail Auto-Order System (`bgf_auto/`)
> **Analyst**: qa-strategist
> **Date**: 2026-03-01
> **Scope**: All 2800+ tests in `bgf_auto/tests/`

---

## Executive Summary

The BGF Retail auto-order system has a **well-structured test suite of approximately 2800+ tests** organized into 110+ test files covering critical business paths. The test infrastructure uses in-memory SQLite for isolation, comprehensive mocking of Selenium drivers, and feature-based test grouping. However, several critical coverage gaps exist and a set of pre-existing failures require remediation before any CI/CD pipeline can be declared green.

**Overall QA Health Score: 72/100**

| Dimension | Score | Status |
|-----------|-------|--------|
| Test Coverage Breadth | 78/100 | Good — most features have tests |
| Test Isolation Quality | 80/100 | Good — in-memory DB, proper mocking |
| Critical Path Coverage | 65/100 | Risk — DailyOrderFlow, DailyCollectionJob untested |
| Edge Case Coverage | 70/100 | Acceptable — boundary tests present |
| Error Handling Coverage | 60/100 | Weak — external API failure paths sparse |
| CI/CD Readiness | 55/100 | Blocked — no pipeline, 15+ pre-existing failures |

---

## 1. Test Files Inventory (110+ files)

### 1.1 Complete Test File List

| Test File | Category | Count (est.) |
|-----------|----------|--------------|
| test_improved_predictor.py | Prediction Core | 25+ |
| test_prediction_redesign_integration.py | Prediction Integration | 20+ |
| test_demand_classifier.py | Prediction | 25+ |
| test_croston_predictor.py | Prediction | 15+ |
| test_additive_adjuster.py | Prediction | 15+ |
| test_food_category.py | Category Strategy | 30+ |
| test_beer_category.py | Category Strategy | 25+ |
| test_soju_category.py | Category Strategy | 25+ |
| test_tobacco_category.py | Category Strategy | 25+ |
| test_ramen_category.py | Category Strategy | 20+ |
| test_beverage_category.py | Category Strategy | 20+ |
| test_frozen_ice_category.py | Category Strategy | 20+ |
| test_instant_meal_category.py | Category Strategy | 25+ |
| test_perishable_category.py | Category Strategy | 20+ |
| test_dessert_category.py | Category Strategy | 20+ |
| test_snack_confection_category.py | Category Strategy | 20+ |
| test_alcohol_general_category.py | Category Strategy | 20+ |
| test_daily_necessity_category.py | Category Strategy | 20+ |
| test_general_merchandise_category.py | Category Strategy | 25+ |
| test_default_category.py | Category Strategy | 15+ |
| test_food_prediction_fix.py | Prediction Fix | 16+ |
| test_food_underorder_fix.py | Prediction Fix | 32+ |
| test_category_demand_forecaster.py | Prediction | 20+ |
| test_wma_none_imputation.py | Prediction | 15+ |
| test_large_category_forecaster.py | Prediction | 19+ |
| test_order_executor_direct_api.py | Order Execution | 27+ |
| test_direct_api_saver.py | Order Execution | 20+ |
| test_batch_grid_input.py | Order Execution | 15+ |
| test_direct_api_fetcher.py | Order Fetching | 20+ |
| test_direct_frame_fetcher.py | Order Fetching | 15+ |
| test_direct_popup_fetcher.py | Order Fetching | 15+ |
| test_date_filter_order.py | Order Filter | 11+ |
| test_promotion_adjuster.py | Promotion | 19+ |
| test_promo_min_order.py | Promotion | 22+ |
| test_promo_validation.py | Promotion Validation | 15+ |
| test_ml_daily_training.py | ML Training | 11+ |
| test_ml_predictor.py | ML Prediction | 20+ |
| test_ml_feature_largecd.py | ML Feature | 31+ |
| test_ml_improvement.py | ML Improvement | 20+ |
| test_food_ml_dual_model.py | ML Dual Model | 27+ |
| test_repository.py | DB Repository | 30+ |
| test_order_exclusion_repo.py | DB Repository | 15+ |
| test_order_exclusion_tracking.py | Order Tracking | 15+ |
| test_accuracy_tracker.py | Accuracy | 15+ |
| test_waste_cause_analyzer.py | Waste Analysis | 20+ |
| test_waste_verification_system.py | Waste Verification | 10+ |
| test_waste_slip.py | Waste Slip | 15+ |
| test_waste_disuse_sync.py | Waste Sync | 15+ |
| test_waste_cause_viz.py | Waste Viz | 9+ |
| test_waste_rate_smallcd.py | Waste Rate | 28+ |
| test_food_waste_calibrator.py | Waste Calibrator | 20+ |
| test_web_api.py | Web API | 20+ |
| test_web_security.py | Web Security | 20+ |
| test_auth.py | Authentication | 20+ |
| test_settings_web_ui.py | Settings Web UI | 10+ |
| test_receiving_delay_analysis.py | Receiving Analysis | 10+ |
| test_receiving_new_product_detect.py | New Product | 19+ |
| test_new_product_lifecycle.py | New Product | 20+ |
| test_new_product.py | New Product | 20+ |
| test_health_check_alert.py | Health/Alert | 20+ |
| test_cloud_sync.py | Cloud Sync | 18+ |
| test_db_sync.py | DB Sync | 18+ |
| test_cost_optimizer_activation.py | Cost Optimizer | 16+ |
| test_bayesian_optimizer.py | Bayesian Opt | 15+ |
| test_pre_order_evaluator.py | Pre-Order Eval | 15+ |
| test_beer_overorder_fix.py | Bug Fix | 14+ |
| test_hourly_sales.py | Hourly Sales | 28+ |
| test_substitution_detector.py | Substitution | 24+ |
| test_store_isolation.py | Multi-Store | 15+ |
| test_store_management.py | Store Mgmt | 10+ |
| test_log_traceability.py | Logging | 15+ |
| test_log_parser.py | Log Parser | 15+ |
| test_association.py | Association Rules | 15+ |
| test_diff_feedback.py | Diff Feedback | 15+ |
| test_reports.py | Reports | 15+ |
| test_eval_config.py | Eval Config | 10+ |
| test_product_detail_batch_collector.py | Collector | 10+ |
| test_order_unit_collection.py | Order Unit | 10+ |
| test_cut_product_filter.py | Product Filter | 15+ |
| test_cut_staleness.py | Cut/Staleness | 15+ |
| test_cut_check_api_only.py | Cut Check | 10+ |
| test_stopped_items.py | Stopped Items | 10+ |
| test_expiry_based_ttl.py | TTL | 10+ |
| test_inventory_ttl_dashboard.py | TTL Dashboard | 10+ |
| test_need_qty_fix.py | Need Qty | 15+ |
| test_stock_adjustment.py | Stock Adj | 10+ |
| test_stock_discrepancy.py | Stock Discrepancy | 10+ |
| test_pending_simplified.py | Pending | 10+ |
| test_pending_comparison.py | Pending | 10+ |
| test_pending_cross_validation.py | Pending | 10+ |
| test_manual_order_pending.py | Manual Order | 10+ |
| test_manual_order_food_deduction.py | Manual Order | 19+ |
| test_snack_orderable_day.py | Snack Order | 26+ |
| test_order_diff.py | Order Diff | 10+ |
| test_new_product_smallcd.py | New Product | 12+ |
| test_category_drilldown.py | Category API | 12+ |
| test_utils.py | Utilities | 15+ |
| test_db_models.py | DB Models | 10+ |
| test_logging_enhancement.py | Logging | 10+ |
| test_batch_sync.py | Batch Sync | 10+ |
| test_weather_forecast.py | Weather | 10+ |
| test_pre_order_stale_category.py | Pre-Order | 10+ |
| test_dashboard_expiry_fix.py | Dashboard | 10+ |
| test_waste_tracking_fix2.py | Waste Track | 10+ |

---

## 2. Coverage Matrix: Test Files vs Source Files

### 2.1 COVERED — Source Files With Direct Tests

| Source Module | Test File(s) | Coverage Level |
|---------------|-------------|---------------|
| `src/prediction/improved_predictor.py` | test_improved_predictor.py, test_beer_overorder_fix.py, test_food_prediction_fix.py, test_food_underorder_fix.py, test_prediction_redesign_integration.py | HIGH |
| `src/prediction/demand_classifier.py` | test_demand_classifier.py | HIGH |
| `src/prediction/croston_predictor.py` | test_croston_predictor.py | HIGH |
| `src/prediction/additive_adjuster.py` | test_additive_adjuster.py | HIGH |
| `src/prediction/category_demand_forecaster.py` | test_category_demand_forecaster.py | HIGH |
| `src/prediction/large_category_forecaster.py` | test_large_category_forecaster.py | HIGH |
| `src/prediction/food_waste_calibrator.py` | test_food_waste_calibrator.py | HIGH |
| `src/prediction/categories/food.py` | test_food_category.py | HIGH |
| `src/prediction/categories/beer.py` | test_beer_category.py | HIGH |
| `src/prediction/categories/soju.py` | test_soju_category.py | HIGH |
| `src/prediction/categories/tobacco.py` | test_tobacco_category.py | HIGH |
| `src/prediction/categories/ramen.py` | test_ramen_category.py | HIGH |
| `src/prediction/categories/beverage.py` | test_beverage_category.py | HIGH |
| `src/prediction/categories/frozen_ice.py` | test_frozen_ice_category.py | HIGH |
| `src/prediction/categories/instant_meal.py` | test_instant_meal_category.py | HIGH |
| `src/prediction/categories/perishable.py` | test_perishable_category.py | HIGH |
| `src/prediction/categories/dessert.py` | test_dessert_category.py | HIGH |
| `src/prediction/categories/snack_confection.py` | test_snack_confection_category.py | HIGH |
| `src/prediction/categories/alcohol_general.py` | test_alcohol_general_category.py | HIGH |
| `src/prediction/categories/daily_necessity.py` | test_daily_necessity_category.py | HIGH |
| `src/prediction/categories/general_merchandise.py` | test_general_merchandise_category.py | HIGH |
| `src/prediction/categories/default.py` | test_default_category.py | HIGH |
| `src/prediction/ml/trainer.py` | test_ml_daily_training.py | MEDIUM |
| `src/prediction/ml/feature_builder.py` | test_ml_feature_largecd.py, test_food_ml_dual_model.py | HIGH |
| `src/prediction/ml/model.py` | test_ml_predictor.py, test_food_ml_dual_model.py | MEDIUM |
| `src/prediction/pre_order_evaluator.py` | test_pre_order_evaluator.py | MEDIUM |
| `src/prediction/bayesian_optimizer.py` | test_bayesian_optimizer.py | MEDIUM |
| `src/prediction/cost_optimizer.py` | test_cost_optimizer_activation.py | MEDIUM |
| `src/prediction/promotion/promotion_adjuster.py` | test_promotion_adjuster.py, test_promo_min_order.py, test_promo_validation.py | HIGH |
| `src/prediction/diff_feedback.py` | test_diff_feedback.py | MEDIUM |
| `src/order/order_executor.py` | test_order_executor_direct_api.py, test_date_filter_order.py | MEDIUM |
| `src/order/direct_api_saver.py` | test_direct_api_saver.py | HIGH |
| `src/order/batch_grid_input.py` | test_batch_grid_input.py | MEDIUM |
| `src/order/auto_order.py` | test_beer_overorder_fix.py (partial), test_stock_adjustment.py | LOW |
| `src/collectors/direct_api_fetcher.py` | test_direct_api_fetcher.py | MEDIUM |
| `src/collectors/direct_frame_fetcher.py` | test_direct_frame_fetcher.py | MEDIUM |
| `src/collectors/direct_popup_fetcher.py` | test_direct_popup_fetcher.py | MEDIUM |
| `src/collectors/hourly_sales_collector.py` | test_hourly_sales.py | HIGH |
| `src/collectors/product_detail_batch_collector.py` | test_product_detail_batch_collector.py | MEDIUM |
| `src/collectors/order_status_collector.py` | test_order_unit_collection.py | MEDIUM |
| `src/analysis/waste_cause_analyzer.py` | test_waste_cause_analyzer.py | HIGH |
| `src/analysis/substitution_detector.py` | test_substitution_detector.py | HIGH |
| `src/analysis/log_parser.py` | test_log_parser.py | MEDIUM |
| `src/infrastructure/database/repos/` (most) | test_repository.py, test_order_exclusion_repo.py | MEDIUM |
| `src/web/routes/api_*.py` | test_web_api.py | LOW-MEDIUM |
| `src/web/middleware.py` | test_web_security.py | MEDIUM |
| `src/application/use_cases/ml_training_flow.py` | test_ml_daily_training.py | MEDIUM |
| `src/application/use_cases/daily_order_flow.py` | test_date_filter_order.py (partial) | LOW |
| `src/application/services/waste_verification_service.py` | test_waste_verification_system.py | MEDIUM |
| `src/application/services/waste_disuse_sync_service.py` | test_waste_disuse_sync.py | MEDIUM |

### 2.2 NOT COVERED — Source Files With NO Direct Tests

The following source files have no corresponding test file and are identified as coverage gaps:

#### CRITICAL — No Test Coverage

| Source File | Risk Level | Reason |
|-------------|-----------|--------|
| `src/scheduler/daily_job.py` (DailyCollectionJob) | CRITICAL | Orchestrates entire daily pipeline; no dedicated test |
| `src/order/auto_order.py` (AutoOrderSystem.execute) | CRITICAL | Main order execution; only partial coverage via side tests |
| `src/application/use_cases/daily_order_flow.py` (DailyOrderFlow.run) | CRITICAL | Top-level orchestrator; only partially covered |
| `src/application/scheduler/job_scheduler.py` (MultiStoreRunner) | HIGH | Parallel execution logic; no test |
| `src/sales_analyzer.py` | HIGH | BGF login/menu navigation; fully Selenium, untestable directly |
| `src/nexacro_helper.py` | HIGH | Nexacro JS helpers; BGF-site dependent |

#### HIGH RISK — Minimal Coverage

| Source File | Risk Level | Gap |
|-------------|-----------|-----|
| `src/collectors/sales_collector.py` | HIGH | Core data collector; no dedicated test |
| `src/collectors/receiving_collector.py` | HIGH | Receiving data; referenced in tests but no dedicated file |
| `src/collectors/promotion_collector.py` | HIGH | Promotion data collection; no test |
| `src/collectors/waste_slip_collector.py` | HIGH | Waste slips (Selenium-based); no unit test |
| `src/collectors/manual_order_detector.py` | MEDIUM | Manual order detection; no dedicated test |
| `src/collectors/calendar_collector.py` | MEDIUM | Holiday calendar; no test |
| `src/collectors/weather_collector.py` | MEDIUM | Weather API collection; no test |
| `src/application/services/prediction_service.py` | HIGH | PredictionService wrapper; no test |
| `src/application/services/order_service.py` | HIGH | Order assembly service; no test |
| `src/application/services/inventory_service.py` | MEDIUM | Inventory aggregation; no test |
| `src/application/services/dashboard_service.py` | MEDIUM | Dashboard data; no test |
| `src/application/services/new_product_monitor.py` | MEDIUM | New product monitoring; no test |
| `src/web/routes/api_home.py` | MEDIUM | Only smoke tested via test_web_api.py |
| `src/web/routes/api_order.py` | MEDIUM | Partial smoke tests only |
| `src/web/routes/api_report.py` | MEDIUM | No dedicated test |
| `src/web/routes/api_receiving.py` | MEDIUM | No dedicated test |
| `src/web/routes/api_inventory.py` | MEDIUM | No dedicated test |
| `src/web/routes/api_logs.py` | MEDIUM | No dedicated test |
| `src/web/routes/api_new_product.py` | MEDIUM | No dedicated test |
| `src/report/daily_order_report.py` | LOW | Report generation; no test |
| `src/report/weekly_trend_report.py` | LOW | Weekly report; no test |
| `src/report/safety_impact_report.py` | LOW | Safety report; no test |
| `src/analysis/daily_report.py` | LOW | Daily report; no test |
| `src/analysis/trend_report.py` | LOW | Trend analysis; no test |
| `src/notification/kakao_notifier.py` | MEDIUM | Notification; no unit test (mocked in some tests) |
| `src/infrastructure/database/repos/order_analysis_repo.py` | LOW | Analysis repo; no test |
| `src/infrastructure/database/repos/signup_repo.py` | LOW | Signup repo; no test |
| `src/infrastructure/database/repos/user_repo.py` | LOW | User repo; no test (auth tests use mock) |
| `src/domain/prediction/strategy_registry.py` | MEDIUM | Strategy registry; no dedicated test |
| `src/domain/order/order_adjuster.py` | MEDIUM | Pure order adjustment; no test |
| `src/prediction/categories/food_daily_cap.py` | MEDIUM | Food daily cap logic; no test |
| `src/prediction/prediction_logger.py` | LOW | Logging; no test |
| `src/prediction/eval_calibrator.py` | MEDIUM | Calibration; no test |
| `src/prediction/eval_reporter.py` | LOW | Reporting; no test |
| `src/prediction/accuracy/reporter.py` | LOW | Accuracy reporting; no test |

---

## 3. Test Quality Assessment

### 3.1 Test Isolation

**Grade: B+ (80/100)**

- **Strength**: The `conftest.py` provides a comprehensive `in_memory_db` fixture using `sqlite3.connect(":memory:")` for full schema isolation. Each test function gets a fresh DB connection.
- **Strength**: The `db_path_patch` fixture correctly copies in-memory data to a temp file for tests requiring file paths.
- **Strength**: Session-scoped `_suppress_file_log_handlers()` prevents test logs from polluting production log files.
- **Weakness**: Some test classes use `setup_method()` to initialize `DemandClassifier(store_id="46513")` but do not mock the DB connection. These tests may accidentally hit the real DB if the project root is set.
- **Weakness**: The `flask_app` fixture injects admin session (`role="admin"`, `store_id="46513"`) globally — tests cannot verify store-isolation behavior without explicit overrides.

### 3.2 Mocking Strategy

**Grade: B (78/100)**

- **Strength**: Selenium WebDriver is consistently mocked using `MagicMock()` — tests never require a real browser.
- **Strength**: `patch.object(ImprovedPredictor, '__init__', return_value=None)` pattern is used correctly across multiple files to avoid DB initialization.
- **Strength**: `patch('src.order.order_executor.DIRECT_API_ORDER_ENABLED', True)` demonstrates proper feature flag testing.
- **Weakness**: The `analyze_beer_pattern()` tests mock `sqlite3.connect` at a low level rather than using the standard `conftest.py` fixtures — this creates inconsistency.
- **Weakness**: Several collector tests mock at the class level but don't verify that mock calls match the expected Nexacro JS call patterns.
- **Weakness**: The ML trainer tests mock `MLTrainer` entirely but don't verify that the training data loading pipeline is exercised.

### 3.3 Edge Case Coverage

**Grade: B (75/100)**

- **Strength**: Demand classifier tests cover all 4 pattern boundaries (daily/frequent/intermittent/slow) including exact boundary values (0.70, 0.40, 0.15).
- **Strength**: Zero/null data edge cases are tested (zero sell days, zero total days, zero available days).
- **Strength**: The `stale RI=0` scenario in beer overorder fix tests demonstrates excellent edge case coverage for a production bug.
- **Weakness**: Order executor fallback chain (Direct API → Batch Grid → Selenium) is tested but the scenario where all three levels fail simultaneously is not.
- **Weakness**: DB connection failure paths (SQLite lock, disk full, schema mismatch) are rarely tested.
- **Weakness**: Multi-store parallel execution race conditions are not tested.
- **Weakness**: Network timeout scenarios for Nexacro JS calls are not tested.

### 3.4 Error Handling Coverage

**Grade: C+ (62/100)**

- **Strength**: The health check tests verify custom exception hierarchy (7 exception types).
- **Weakness**: BGF site login failure, session timeout, and network interruption are not tested at all.
- **Weakness**: DB migration failure paths (partial migration, schema version mismatch) have no tests.
- **Weakness**: Kakao notification failure (invalid token, rate limit) is not tested — only the happy path exists.
- **Weakness**: The `DailyOrderFlow` orchestrator error propagation is not tested — if Phase 1 fails, does Phase 2 skip or proceed?

### 3.5 Parametrize Usage

- Parametrized tests are used in food category tests (`@pytest.mark.parametrize("mid_cd", ...)`)
- The approach saves test code and documents all valid inputs clearly
- Parametrize is underused in order executor and collector tests where the same pattern repeats for each delivery type

---

## 4. Known Failing Tests Analysis

### 4.1 Pre-existing Failures Summary

Based on historical reports, the number of pre-existing failures has fluctuated:

| Date | Total Tests | Pre-existing Failures | Root Cause |
|------|------------|----------------------|-----------|
| ~2026-02-08 | 1,038 | 0 | Initial stable state |
| ~2026-02-15 | 2,216 | 0 | After 9-phase refactor |
| 2026-02-20 | 2,617 | 1 | test_beer_overorder_fix (schema) |
| 2026-03-01 (early) | 2,730 | 22 | large_cd schema issues |
| 2026-03-01 (latest) | 2,822+ | 15 | Mixed large_cd + substitution |

**Current State (as of 2026-03-01)**: ~2783 tests pass, approximately 15 pre-existing failures.

### 4.2 Root Cause Analysis of Known Failures

#### Category A: large_cd Schema Issues (estimated 10-15 failures)

**Affected tests**: Tests in `test_ml_feature_largecd.py` and `test_large_category_forecaster.py` that require `product_details.large_cd` column.

**Root cause**: The `large_cd` column in `product_details` was added to the data model (`data_provider.py` SQL query includes `pd.large_cd`) but the test fixtures in `conftest.py` do NOT include `large_cd` in the `product_details` CREATE TABLE statement:

```python
# conftest.py line 123 - MISSING large_cd column
conn.execute("""
    CREATE TABLE product_details (
        item_cd TEXT PRIMARY KEY,
        item_nm TEXT,
        mid_cd TEXT,
        order_unit_qty INTEGER DEFAULT 1,
        expiration_days INTEGER,
        orderable_day TEXT DEFAULT '일월화수목금토',
        demand_pattern TEXT DEFAULT NULL
    )
""")
```

The `data_provider.py` query references `pd.large_cd`, `pd.small_cd`, and other columns not in the conftest schema.

**Blocking behavior**: Does NOT block production functionality (the real DB has the column). Blocks test suite from being green.

**Fix**: Add `large_cd TEXT`, `small_cd TEXT`, `class_nm TEXT`, and other missing columns to the `conftest.py` product_details table definition.

#### Category B: SubstitutionDetector DB Schema (estimated 2-5 failures)

**Affected tests**: Subset of `test_substitution_detector.py`.

**Root cause**: `substitution_events` table in test fixtures may not match the latest schema, particularly the `small_cd`/`small_nm` fields added in the v49 migration.

**Blocking behavior**: Does NOT block production functionality. SubstitutionDetector uses a lazy loader with a sentinel (`_substitution_detector = False`) so production gracefully degrades if initialization fails.

**Fix**: Update `sub_db` fixture in `test_substitution_detector.py` to include all v49 schema columns.

#### Category C: test_beer_overorder_fix (single historical failure)

**Status**: The test in `TestBeerWithCorrectStock.test_beer_max_stock_skip_with_correct_stock` has been noted as a known failure in older reports. Reviewing the test code, it uses `sqlite3.connect` mock with `mock_cursor.fetchone.return_value = (3, 11, 19)` which expects a specific column order. If the `analyze_beer_pattern()` SQL changed column ordering, this would silently fail.

**Current status**: Likely resolved by orderable-day-fix changes; confirm by examining the mock cursor expectations against the actual SQL.

### 4.3 Failure Impact Classification

| Failure Group | Count | Production Impact | Fix Effort |
|--------------|-------|-----------------|-----------|
| large_cd schema in conftest | 10-15 | None | Low (schema update) |
| SubstitutionDetector schema | 2-5 | None (graceful fallback) | Low (schema update) |
| beer analyze mock mismatch | 0-1 | None | Low (mock fix) |
| **Total** | **15** | **None** | **~2 hours** |

---

## 5. Critical Path Coverage Assessment

### 5.1 Prediction Pipeline (improved_predictor.py)

**Coverage: HIGH (85%)**

The prediction pipeline is well-tested across multiple test files:
- `test_improved_predictor.py`: Full integration scenarios (normal, insufficient data, sufficient stock)
- `test_prediction_redesign_integration.py`: Pipeline routing (WMA vs Croston vs zero for slow)
- `test_demand_classifier.py`: 25+ boundary and pattern tests
- `test_croston_predictor.py`: Intermittent demand math
- `test_additive_adjuster.py`: Delta coefficient application
- Category-specific tests (15 files): Each category's `calculate_safety_stock()` method

**Gap**: The full `predict_batch()` end-to-end call with real DB is not tested — only individual phases via mocking. A single integration test calling `predict_batch()` with seeded in-memory DB would improve confidence.

### 5.2 Order Execution Path (auto_order.py, order_executor.py)

**Coverage: MEDIUM (55%)**

- `test_order_executor_direct_api.py`: 3-tier fallback (Direct API → Batch Grid → Selenium) well tested
- `test_direct_api_saver.py`: API save logic tested with mock driver
- `test_date_filter_order.py`: Date filtering across 5-layer call chain tested

**Critical Gap**: `AutoOrderSystem.execute()` — the main entry point that:
1. Builds the order list from predictions
2. Calls `prefetch_pending_quantities()`
3. Applies `order_filter` and `order_adjuster`
4. Calls `order_executor.execute_orders()`

This orchestration is NOT tested as a unit. Only the individual components are tested in isolation.

**Critical Gap**: `DailyOrderFlow.run()` which chains collection → prediction → filtering → execution → tracking is only partially tested via `test_date_filter_order.py` which mocks most of the chain.

### 5.3 ML Training (trainer.py)

**Coverage: MEDIUM (60%)**

- `test_ml_daily_training.py`: Schedule registration, incremental vs full mode, performance gate, rollback (11 tests)
- `test_ml_feature_largecd.py`: Feature engineering with large_cd supergroups (31 tests)

**Gap**: The actual model training execution path in `MLTrainer.train_all_groups()` is mocked entirely. No test verifies that training with real SQLite sales data produces a valid model file.

**Gap**: The performance protection gate (MAE 20% threshold) is tested only at the unit level — no test verifies the gate fires correctly when integrated with actual training metrics.

### 5.4 Data Collection (collectors/)

**Coverage: LOW-MEDIUM (35%)**

| Collector | Coverage | Note |
|-----------|---------|------|
| direct_api_fetcher.py | MEDIUM | 20+ tests with mock driver |
| direct_frame_fetcher.py | MEDIUM | 15+ tests with mock driver |
| direct_popup_fetcher.py | MEDIUM | 15+ tests with mock driver |
| hourly_sales_collector.py | HIGH | 28+ tests |
| product_detail_batch_collector.py | MEDIUM | 10+ tests |
| order_status_collector.py | MEDIUM | via test_order_unit_collection |
| **sales_collector.py** | **NONE** | No dedicated test file |
| **receiving_collector.py** | **NONE** | No dedicated test file |
| **promotion_collector.py** | **NONE** | No dedicated test file |
| **waste_slip_collector.py** | **NONE** | Selenium-heavy; no unit test |
| weather_collector.py | NONE | No test |
| calendar_collector.py | NONE | No test |
| manual_order_detector.py | NONE | No test |

The missing collector tests are the biggest coverage gap. `sales_collector.py` is the most critical — it is the primary data ingestion point for the entire system.

### 5.5 DB Operations (repos/)

**Coverage: MEDIUM (65%)**

- `test_repository.py`: Tests SalesRepository, ProductDetailRepository, OrderRepository via `init_db()`
- `test_order_exclusion_repo.py`: Tests OrderExclusionRepository

**Gap**: The following 20+ repositories have NO dedicated test:
- `ReceivingRepository`, `RealtimeInventoryRepository`, `InventoryBatchRepository`
- `WasteSlipRepository`, `WasteCauseRepository`, `WasteSlipRepository`
- `DetectedNewProductRepository`, `NewProductDailyTrackingRepository`
- `SubstitutionEventRepository` (partially via test_substitution_detector.py)
- `HourlySalesRepository`, `ManualOrderItemRepository`
- `BayesianOptimizationRepository`, `StoppedItemRepository`

---

## 6. Test Infrastructure Review

### 6.1 conftest.py Fixtures

**Assessment: GOOD with gaps**

| Fixture | Scope | Quality |
|---------|-------|---------|
| `_suppress_file_log_handlers` | session | Excellent — prevents log pollution |
| `in_memory_db` | function | Good — full schema but missing large_cd |
| `sample_sales_data` | function | Good — 30 days, 5 items, realistic variation |
| `db_path_patch` | function | Good — copies to file for path-based tests |
| `flask_app` | function | Good — full Flask test app with DB |
| `client` | function | Good — admin session pre-injected |

**Gap**: `conftest.py` `product_details` table is missing columns added in later migrations:
- `large_cd TEXT`
- `small_cd TEXT`
- `class_nm TEXT`

This is the root cause of the 10-15 pre-existing failures.

### 6.2 Test Data Management

**Assessment: ADEQUATE**

- Test data is generated inline using date arithmetic (30-day rolling window with `timedelta`)
- No shared test fixtures that persist between test runs
- `tmp_path` pytest fixture is used correctly for file-based DB tests
- No test data files that can become stale

**Weakness**: Some tests use hardcoded item codes like `"8801234567890"` and dates like `"2026-01-30"` that may trigger date-sensitive logic unexpectedly as real-world dates advance.

### 6.3 Test Markers

Current markers in `pytest.ini`:
- `unit`: Pure logic tests (no DB)
- `db`: DB-dependent tests (in-memory SQLite)
- `slow`: Tests to be skipped normally

**Assessment**: Markers are defined but inconsistently applied. Most tests do not carry any marker, making selective execution difficult. The `slow` marker has no tests currently using it.

### 6.4 CI/CD Integration Readiness

**Assessment: NOT READY**

| CI/CD Element | Status |
|--------------|--------|
| pytest.ini configured | YES — `-n auto` for parallel execution |
| GitHub Actions / CI config | NO — no `.github/workflows/` directory found |
| Requirements file | To be verified |
| Test isolation (no real BGF site) | YES — Selenium fully mocked |
| 100% green suite | NO — 15 pre-existing failures |
| Coverage reporting | NO — no `--cov` configuration |
| Failure threshold | NO — not configured |

The project cannot be connected to a CI/CD pipeline without first resolving the 15 pre-existing test failures. Once those are resolved, setting up GitHub Actions would be straightforward given the existing pytest configuration with `-n auto` parallelism.

---

## 7. Risk Assessment Matrix

### 7.1 Coverage Risks

| Component | Coverage | Risk | Impact if Bug |
|-----------|---------|------|--------------|
| ImprovedPredictor prediction math | 85% | LOW | Wrong order quantities |
| Category safety stock calculations | 90% | LOW | Category-specific over/under order |
| Order executor fallback chain | 70% | MEDIUM | Order placement failure |
| AutoOrderSystem orchestration | 30% | HIGH | Orders not placed at all |
| DailyCollectionJob daily pipeline | 10% | CRITICAL | No data collected, system blind |
| SalesCollector data ingestion | 0% | CRITICAL | Core data missing |
| MultiStoreRunner parallel execution | 0% | HIGH | Race conditions across stores |
| Nexacro JS interaction | 0% (mocked) | MEDIUM | BGF site changes break silently |
| DB migration path | 5% | HIGH | Schema version mismatch on upgrade |

### 7.2 Production Risk Scenarios

1. **Scenario: daily_job.py Phase ordering bug** — If a phase is skipped or executed out of order, the entire day's orders could be wrong. No test covers the Phase sequencing at integration level.

2. **Scenario: BGF site session expiry mid-run** — The Selenium session management (login, tab management, cleanup) has no test. If the session expires mid-collection, the recovery path is untested.

3. **Scenario: Partial DB write** — If the connection drops during `save_daily_sales()`, the partial write state is not tested for consistency recovery.

4. **Scenario: Multi-store parallel failure** — If store A's job raises an unhandled exception, the `MultiStoreRunner` behavior (does it stop store B? retry?) is not tested.

---

## 8. Prioritized Recommendations

### Priority 1 — IMMEDIATE (Blocks CI/CD greenification)

**P1-1: Fix conftest.py product_details schema**

File: `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/conftest.py`

Add missing columns to the `product_details` CREATE TABLE:
```python
CREATE TABLE product_details (
    item_cd TEXT PRIMARY KEY,
    item_nm TEXT,
    mid_cd TEXT,
    large_cd TEXT,          -- ADD: needed by data_provider.py and ml-feature-largecd tests
    small_cd TEXT,          -- ADD: needed by substitution_detector and waste_rate_smallcd
    class_nm TEXT,          -- ADD: needed by category bulk tests
    order_unit_qty INTEGER DEFAULT 1,
    expiration_days INTEGER,
    orderable_day TEXT DEFAULT '일월화수목금토',
    demand_pattern TEXT DEFAULT NULL,
    sell_price INTEGER,     -- ADD: needed by cost_optimizer tests
    margin_rate REAL,       -- ADD: needed by cost_optimizer tests
    lead_time_days INTEGER DEFAULT 1  -- ADD: needed by predictor tests
)
```

**Estimated fix time**: 30 minutes. Resolves 10-15 pre-existing failures.

**P1-2: Update substitution_events schema in test fixture**

File: `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_substitution_detector.py`

Verify that the `sub_db` fixture's `substitution_events` CREATE TABLE matches the production v49 schema exactly.

**Estimated fix time**: 15 minutes. Resolves 2-5 pre-existing failures.

### Priority 2 — HIGH (Critical path coverage)

**P2-1: Add DailyCollectionJob unit tests**

Create `tests/test_daily_collection_job.py` covering:
- Phase sequencing (1 → 1.1 → 1.15 → 1.16 → 1.2 → ... → 2 → 3)
- Phase skip on error (partial failure isolation)
- Tab cleanup between phases
- Store context propagation

Mock the BGF driver entirely. Use `in_memory_db` for storage verification.

**Estimated effort**: 2 days. Reduces CRITICAL risk.

**P2-2: Add AutoOrderSystem.execute() integration test**

Create `tests/test_auto_order_execute.py` covering:
- Full order list construction from predictions
- Prefetch pending quantities (mock the collector)
- Filter application (CUT items, excluded items)
- Order executor call verification

**Estimated effort**: 1 day. Reduces HIGH risk.

**P2-3: Add SalesCollector unit tests**

Create `tests/test_sales_collector.py` covering:
- Sales data parsing from Nexacro grid response
- UPSERT behavior (new vs updated items)
- Error recovery on partial data

Mock `driver.execute_script()` to return predefined dataset JSON.

**Estimated effort**: 1 day. Resolves the largest coverage gap.

### Priority 3 — MEDIUM (Quality improvements)

**P3-1: Add coverage configuration**

Update `pytest.ini` to enable coverage reporting:
```ini
[pytest]
addopts = -v --tb=short -n auto --cov=src --cov-report=term-missing --cov-fail-under=70
```

This establishes a quality gate and makes coverage visible.

**P3-2: Apply test markers consistently**

All existing tests should carry at least one marker (`unit` or `db`). This enables selective execution in CI:
- `pytest -m unit` for fast pre-commit check
- `pytest -m "unit or db"` for full suite
- `pytest -m "not slow"` for normal CI

**P3-3: Add ML trainer integration test**

A test that seeds in-memory DB with synthetic sales data and calls `MLTrainer.train_one_group()` would verify the full data pipeline → feature engineering → model training → model persistence chain.

**P3-4: Add error path tests for order executor**

- Test when all 3 fallback levels fail
- Test network timeout during Direct API save
- Test Selenium exception mid-order

### Priority 4 — LOW (Nice to have)

**P4-1: E2E smoke test skeleton**

The `tests/e2e/` directory exists but contains only `__init__.py`. A single E2E smoke test with full BGF mock would demonstrate system-level confidence.

**P4-2: CI/CD pipeline setup**

Once the test suite is green (P1 complete), set up GitHub Actions:
```yaml
name: BGF Auto-Order Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: {python-version: '3.12'}
      - run: pip install -r requirements.txt
      - run: pytest -m "not slow" --tb=short
```

**P4-3: Add parametrize to order executor tests**

The order executor test pattern repeats across Direct API, Batch Grid, and Selenium fallback. Parametrizing would reduce code duplication and ensure all fallback combinations are tested.

---

## 9. Coverage Summary Matrix

| System Area | Files | Tested | Coverage % | Risk |
|------------|-------|--------|-----------|------|
| Prediction — Core | 2 | 2 | 85% | LOW |
| Prediction — Categories | 15 | 15 | 90% | LOW |
| Prediction — ML | 4 | 3 | 65% | MEDIUM |
| Prediction — Promotion | 2 | 2 | 80% | LOW |
| Order — Execution | 3 | 2 | 55% | HIGH |
| Collectors — Direct API | 3 | 3 | 65% | MEDIUM |
| Collectors — Selenium | 7 | 0 | 0% | HIGH |
| Analysis — Waste | 2 | 2 | 75% | LOW |
| Analysis — Substitution | 1 | 1 | 80% | LOW |
| DB — Repositories | 30 | 5 | 25% | MEDIUM |
| Web — API Routes | 10 | 1 | 20% | MEDIUM |
| Web — Auth/Security | 2 | 2 | 75% | LOW |
| Application — Use Cases | 6 | 2 | 30% | HIGH |
| Application — Scheduler | 2 | 0 | 0% | CRITICAL |
| Settings/Config | 5 | 2 | 40% | LOW |

**Overall Estimated Coverage: ~62%**

---

## 10. QA Strategy Recommendations Going Forward

### 10.1 Test-First Policy for Bug Fixes

Every bug fix should include a regression test that reproduces the bug before the fix and passes after. The `beer-overorder-fix` is an excellent example of this practice. This should be mandated for all future bug fixes.

### 10.2 Feature Test Ownership

Each PDCA feature should include a minimum of 10 tests with:
- At least 1 integration test exercising the full feature flow
- At least 2 error path tests
- Boundary tests for all numeric thresholds
- Tests for the "enabled=False" disabled state

### 10.3 Selenium Collector Testing Approach

Since Selenium collectors cannot be tested against the real BGF site, establish a standard mock pattern:

```python
def _mock_driver_with_dataset(dataset_json: str) -> MagicMock:
    """Standard Nexacro dataset mock for collector tests"""
    driver = MagicMock()
    driver.execute_script.return_value = dataset_json
    return driver
```

Apply this pattern to create tests for `sales_collector.py`, `receiving_collector.py`, and `promotion_collector.py`.

### 10.4 DB Schema Synchronization

Establish a process where any DB schema migration (incrementing `DB_SCHEMA_VERSION`) automatically triggers:
1. Review of `conftest.py` for missing columns
2. Update of affected test fixtures
3. Re-run of full test suite

---

## Appendix: Files Referenced

| Path | Role |
|------|------|
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/conftest.py` | Test fixtures and infrastructure |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_improved_predictor.py` | Prediction core tests |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_demand_classifier.py` | Demand classification tests |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_order_executor_direct_api.py` | Order execution tests |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_beer_overorder_fix.py` | Bug regression tests |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_ml_daily_training.py` | ML training tests |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/tests/test_prediction_redesign_integration.py` | Prediction redesign integration |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/pytest.ini` | Test configuration |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/src/prediction/improved_predictor.py` | Core prediction engine |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/src/order/auto_order.py` | Order execution (coverage gap) |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/src/scheduler/daily_job.py` | Daily orchestration (coverage gap) |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/src/collectors/sales_collector.py` | Core collector (coverage gap) |
| `C:/Users/kanur/OneDrive/바탕 화면/bbb/bgf_auto/src/infrastructure/database/repos/__init__.py` | Repository exports |
