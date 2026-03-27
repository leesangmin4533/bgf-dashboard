# Project Architecture Consistency Review

> **Analysis Type**: Architecture Compliance Review (Full Codebase)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector agent
> **Date**: 2026-03-01
> **Status**: Review

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Comprehensive architecture consistency review of the BGF Retail auto-order project's layered architecture. This review evaluates compliance with the designed 5-layer architecture (Settings, Domain, Infrastructure, Application, Presentation) and identifies violations, inconsistencies, and migration debt.

### 1.2 Analysis Scope

- **Architecture Document**: `bgf_auto/CLAUDE.md` (Layer definitions, conventions, coding rules)
- **Implementation Path**: `bgf_auto/src/` (all layers)
- **Analysis Date**: 2026-03-01

### 1.3 Designed Architecture

```
                Presentation Layer
                (CLI, Web Dashboard)
                       |
                Application Layer
                (Use Cases, Services, Scheduler)
                       |
          +------------+-------------+
          |            |             |
     Domain Layer  Infrastructure  Settings
     (Strategy,    (DB, BGF,       (Config,
      Models,      Collectors,     Constants,
      Evaluation)  External)       StoreContext)
```

**Dependency Rules**:
- Presentation -> Application, Domain, Settings (NOT Infrastructure directly)
- Application -> Domain, Infrastructure, Settings
- Domain -> Settings, Utils only (NO I/O, NO Infrastructure)
- Infrastructure -> Settings, Utils only (NOT Application, NOT Presentation)
- Settings -> None (independent)

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Formal Layer Isolation | 95% | PASS |
| Repository Pattern Consistency | 97% | PASS |
| Strategy Pattern Consistency | 100% | PASS |
| DB Split Compliance | 94% | WARNING |
| StoreContext Usage | 70% | WARNING |
| Code Organization (Legacy Debt) | 55% | FAIL |
| Naming Convention Compliance | 99% | PASS |
| **Overall Architecture Score** | **78%** | WARNING |

---

## 3. Layer Violation Analysis

### 3.1 Formal Layer Imports (Settings/Domain/Infrastructure/Application/Presentation)

The formal 5-layer packages under `src/settings/`, `src/domain/`, `src/infrastructure/`, `src/application/`, `src/presentation/` show **zero cross-layer violations**:

| Check | Result |
|-------|--------|
| Domain -> Application | 0 violations |
| Domain -> Presentation | 0 violations |
| Domain -> Infrastructure (direct) | 0 violations |
| Infrastructure -> Application | 0 violations |
| Infrastructure -> Presentation | 0 violations |
| Application -> Presentation | 0 violations |
| Settings -> Presentation | 0 violations |

**Score: 100%** -- Formal layer boundaries are well-enforced.

### 3.2 Domain Layer Purity

The Domain layer (`src/domain/`) is designed to contain pure business logic with no I/O.

**Pure modules (PASS)**:
- `src/domain/models.py` -- No external imports. Pure data classes.
- `src/domain/order/order_adjuster.py` -- Only imports from `src.utils.logger`.
- `src/domain/order/order_filter.py` -- Only imports from `src.utils.logger`, `src.settings.constants`.
- `src/domain/new_product/score_calculator.py` -- Pure calculation logic.
- `src/domain/new_product/convenience_order_scheduler.py` -- Pure scheduling logic + settings imports.
- `src/domain/new_product/replacement_strategy.py` -- Pure strategy + logger.
- `src/domain/prediction/base_strategy.py` -- Pure ABC. No imports.
- `src/domain/prediction/strategy_registry.py` -- Imports only from domain.

**Impure modules via re-export (WARNING)**:

All 15 Strategy implementations in `src/domain/prediction/strategies/` are thin wrappers that delegate to legacy `src/prediction/categories/` modules:

| Domain Strategy | Delegates To | I/O in Delegate? |
|-----------------|-------------|:-----------------:|
| `strategies/food.py` | `src/prediction/categories/food.py` | YES (DB access) |
| `strategies/beer.py` | `src/prediction/categories/beer.py` | No |
| `strategies/soju.py` | `src/prediction/categories/soju.py` | No |
| `strategies/tobacco.py` | `src/prediction/categories/tobacco.py` | No |
| `strategies/ramen.py` | `src/prediction/categories/ramen.py` | No |
| ... (10 more) | `src/prediction/categories/...` | No |

`src/prediction/categories/food.py` contains 3 direct DB access calls:
- Line 217: `from src.infrastructure.database.repos.hourly_sales_repo import HourlySalesRepository`
- Line 236: `from src.infrastructure.database.connection import DBRouter`
- Line 315: `from src.infrastructure.database.connection import DBRouter`

**Evaluation re-exports (WARNING)**:
- `src/domain/evaluation/__init__.py` re-exports from `src/prediction/pre_order_evaluator`, which inherits `BaseRepository` and performs extensive DB I/O. This makes `PreOrderEvaluator` accessible from the domain path despite containing Infrastructure-level code.

**Domain Purity Score: 85%** -- Core models and filters are pure; strategy delegation creates transitive I/O contamination through legacy modules.

### 3.3 Presentation Layer Bypass

The web routes in `src/web/routes/` (acting as Presentation layer) show significant **Application layer bypass**:

| Route File | Uses Application Service? | Direct Infrastructure Access? |
|------------|:------------------------:|:-----------------------------:|
| `api_home.py` | YES (`DashboardService`) | No |
| `api_auth.py` | No | YES (repos directly) |
| `api_order.py` | No | YES (repos, DBRouter, ImprovedPredictor) |
| `api_prediction.py` | No | YES (DBRouter, AccuracyTracker, MLPredictor) |
| `api_report.py` | No | YES (DBRouter, ImprovedPredictor) |
| `api_waste.py` | No | YES (repos, DBRouter, food_waste_calibrator) |
| `api_receiving.py` | No | YES (raw sqlite3.connect) |
| `api_inventory.py` | No | YES (raw sqlite3.connect) |
| `api_category.py` | No | YES (raw sqlite3.connect) |
| `api_settings.py` | No | YES (raw sqlite3.connect) |
| `api_new_product.py` | No | YES (repos directly) |
| `api_health.py` | No | YES (DBRouter) |
| `api_logs.py` | No | YES (log_parser directly) |

**Only 1 out of 13 route files** uses the Application service layer. The remaining 12 access Infrastructure (repos, DBRouter) or legacy modules directly.

Additionally, **5 route files** define duplicate `_get_store_db_path()` helper functions:
- `api_report.py:25`
- `api_receiving.py:30`
- `api_prediction.py:20`
- `api_inventory.py:25`
- `api_category.py:51`

These functions duplicate `DBRouter.get_store_db_path()` logic and create raw `sqlite3.connect()` calls, completely bypassing both the Repository pattern and the DBRouter.

A 6th duplicate exists in the legacy scheduler:
- `src/scheduler/daily_job.py:993`

**Presentation Layer Compliance Score: 15%** (1/13 routes properly layered)

---

## 4. Repository Pattern Consistency

### 4.1 BaseRepository Inheritance

All 33 repositories in `src/infrastructure/database/repos/` extend `BaseRepository`, **except one**:

| Repository | Extends BaseRepository | db_type |
|------------|:----------------------:|:-------:|
| `OrderAnalysisRepository` | **NO** | N/A (independent) |
| All other 32 repos | YES | Correctly set |

`OrderAnalysisRepository` (`order_analysis_repo.py:138`) explicitly documents:
> "BaseRepository를 상속하지 않고 독립 커넥션 관리. DB 파일은 첫 사용 시 자동 생성 (lazy init)."

This is an intentional design decision for an analysis-only DB (`data/order_analysis.db`) separated from operational DBs, so it is a documented exception rather than a bug.

### 4.2 db_type Assignments

| db_type | Count | Repos |
|---------|:-----:|-------|
| `"store"` | 24 | SalesRepo, OrderRepo, PredictionRepo, OrderTrackingRepo, ReceivingRepo, InventoryRepo, InventoryBatchRepo, PromotionRepo, EvalOutcomeRepo, CalibrationRepo, AutoOrderItemRepo, SmartOrderItemRepo, FailReasonRepo, ValidationRepo, CollectionLogRepo, NewProductStatusRepo, WasteCauseRepo, WasteSlipRepo, ManualOrderItemRepo, DetectedNewProductRepo, HourlySalesRepo, NpTrackingRepo, OrderExclusionRepo, SubstitutionRepo, BayesianOptimizationRepo, AppSettingsRepo |
| `"common"` | 6 | ProductDetailRepo, ExternalFactorRepo, StoreRepo, StoppedItemRepo, SignupRepo, UserRepo |
| N/A (independent) | 1 | OrderAnalysisRepo |

**Repository Pattern Score: 97%** (32/33 follow pattern; 1 intentional exception)

### 4.3 app_settings DB Placement Inconsistency

**Inconsistency Found**:
- `CLAUDE.md` lists `app_settings` under **Common DB** tables
- `connection.py` line 54 lists `app_settings` in `STORE_TABLES`
- `schema.py` creates `app_settings` in **both** COMMON_SCHEMA (line 89) and STORE_SCHEMA (line 592)
- `AppSettingsRepository` has `db_type = "store"`
- `_COMMON_VIEW_TABLES` (line 174) includes `"app_settings"` for temp VIEW creation

**Impact**: Settings stored in the store DB are isolated per store, which may be intentional for per-store configuration. However, this contradicts the CLAUDE.md documentation that lists `app_settings` as a common table. The temp VIEW creation in `attach_common_with_views` creates an additional copy from common.db that could shadow the store-local version.

---

## 5. Strategy Pattern Consistency

### 5.1 Registration Completeness

`create_default_registry()` in `strategy_registry.py` registers all 15 strategies:

| # | Strategy | mid_cd Coverage |
|---|----------|----------------|
| 1 | RamenStrategy | 006, 032 |
| 2 | TobaccoStrategy | 072, 073 |
| 3 | BeerStrategy | 049 |
| 4 | SojuStrategy | 050 |
| 5 | FoodStrategy | 001-005, 012 |
| 6 | PerishableStrategy | 013, 026, 046 |
| 7 | BeverageStrategy | 010, 039, 043, 045, 048 |
| 8 | FrozenIceStrategy | 021, 034, 100 |
| 9 | InstantMealStrategy | 027, 028, 031, 033, 035 |
| 10 | DessertStrategy | (dessert codes) |
| 11 | SnackConfectionStrategy | 014-020, 029, 030 |
| 12 | AlcoholGeneralStrategy | 052, 053 |
| 13 | DailyNecessityStrategy | 036, 037, 056, 057, 086 |
| 14 | GeneralMerchandiseStrategy | 054-071 (excl. others) |
| 15 | DefaultStrategy (fallback) | All unmatched mid_cd |

All strategies:
- Extend `CategoryStrategy` ABC
- Implement required abstract methods (`name`, `matches`, `calculate_safety_stock`)
- Follow consistent constructor pattern (no args)
- Use consistent delegation pattern to legacy `src/prediction/categories/` modules

**Strategy Pattern Score: 100%**

---

## 6. DB Split Compliance

### 6.1 Common vs Store DB Routing

| Check | Status |
|-------|--------|
| Common repos use `db_type = "common"` | PASS (6/6) |
| Store repos use `db_type = "store"` | PASS (24/24) |
| `COMMON_TABLES` frozenset matches actual common repos | PASS |
| `STORE_TABLES` frozenset matches actual store repos | WARNING (see 6.2) |

### 6.2 Issues Found

**Issue 1: `app_settings` dual placement**

As described in Section 4.3, `app_settings` exists in both common and store schemas. The `AppSettingsRepository` has `db_type = "store"`, but `_COMMON_VIEW_TABLES` creates a temp VIEW from `common.app_settings` which could shadow the store table.

**Issue 2: `validation_log` table**

`ValidationRepository` has `db_type = "store"` but the CLAUDE.md documentation lists `validation_log` under common DB. The `connection.py` includes `'validation_log'` in `STORE_TABLES` which aligns with the repo, but documentation is inconsistent.

**Issue 3: Presentation layer bypasses DBRouter**

5 web routes create their own `_get_store_db_path()` and use raw `sqlite3.connect()`. These bypass all routing, connection pooling, and common DB ATTACH logic:
- `api_receiving.py` -- `sqlite3.connect(str(db_path), timeout=10)` with manual ATTACH
- `api_inventory.py` -- `sqlite3.connect(str(store_db))`
- `api_category.py` -- `sqlite3.connect(str(common_db))` and store DB
- `api_prediction.py` -- Mix of `_get_store_db_path` and `DBRouter`
- `api_report.py` -- `_get_store_db_path` for all queries

**DB Split Compliance Score: 94%** -- Core infrastructure is correct; presentation layer bypasses are the main issue.

---

## 7. StoreContext Usage

### 7.1 Adoption Analysis

`StoreContext` is the designed mechanism for store isolation and identity.

**Properly using StoreContext**:
- `run_scheduler.py`
- `src/application/scheduler/job_scheduler.py` (MultiStoreRunner)
- `src/application/services/store_service.py`
- `src/application/services/prediction_service.py`
- `src/application/services/dashboard_service.py`
- `src/application/use_cases/daily_order_flow.py`
- `src/presentation/cli/main.py`
- `src/scheduler/daily_job.py`
- `src/prediction/category_demand_forecaster.py`

**NOT using StoreContext** (using raw `DEFAULT_STORE_ID` or `store_id` strings instead):
- All 13 web route files (`src/web/routes/api_*.py`) -- use `DEFAULT_STORE_ID` constant
- Most collectors (`src/collectors/*.py`) -- receive `store_id` string parameter
- `src/prediction/improved_predictor.py` -- receives `store_id` string
- `src/order/auto_order.py` -- receives `store_id` string

**StoreContext Usage Score: 70%** -- Application and CLI layers properly use it; Presentation and legacy modules do not.

---

## 8. Code Organization (Legacy Migration Debt)

### 8.1 Files Outside Formal Layers

The project has a significant amount of code that lives outside the formal 5-layer structure. These are documented as "legacy compatible" modules:

| Legacy Location | File Count | Should Be In |
|----------------|:----------:|-------------|
| `src/prediction/` | 59 files | Domain (pure logic) + Infrastructure (I/O) |
| `src/collectors/` | 21 files | Infrastructure |
| `src/order/` | 6 files | Application + Infrastructure |
| `src/scheduler/` | 2 files | Application |
| `src/analysis/` | 12 files | Application (services) |
| `src/web/` | 3 + 15 routes | Presentation |
| `src/db/` | 5 files | Infrastructure (shim) |
| `src/report/` | 6 files | Application/Presentation |
| `src/alert/` | 4 files | Application |
| `src/notification/` | 2 files | Infrastructure (external) |
| `src/config/` | 6 files | Settings (shim) |
| `src/utils/` | 9 files | Cross-cutting |
| `src/*.py` (root) | 3 files | Various |
| **Total Legacy** | **~153 files** | |

Versus formal layer files:

| Formal Layer | File Count |
|-------------|:----------:|
| `src/settings/` | 6 files |
| `src/domain/` | 31 files |
| `src/infrastructure/database/` | ~40 files |
| `src/application/` | ~20 files |
| `src/presentation/` | ~12 files |
| **Total Formal** | **~109 files** |

**Legacy modules (153) outnumber formal layer modules (109).**

### 8.2 Layer Mapping of Legacy Modules

The legacy modules have mixed concerns:

**`src/prediction/` (59 files) -- the largest legacy package**:
- Contains both pure business logic (should be Domain) and I/O-bound code (should be Infrastructure/Application)
- `improved_predictor.py` (~3300 lines) is the core engine -- it orchestrates prediction (Application role) while also directly accessing repositories (Infrastructure role)
- Category modules (`categories/*.py`) contain business rules (Domain role) but `food.py` has DB access
- ML modules (`ml/*.py`) mix training logic (Application) with data access (Infrastructure)

**`src/collectors/` (21 files)**:
- All should be in `src/infrastructure/collectors/` -- they are I/O bound (Selenium scraping, DB writes)
- Currently referenced via `src/infrastructure/collectors/` re-export

**`src/scheduler/daily_job.py`**:
- Functions as the main orchestrator -- should logically be in Application layer
- Already imports from Application (`DailyOrderFlow`, `waste_verification_service`)
- Lives outside the formal layer structure

**`src/web/` (18 files)**:
- Functions as Presentation layer
- `src/presentation/web/__init__.py` re-exports from here
- Contains 5 duplicate `_get_store_db_path()` functions

**Code Organization Score: 55%** -- Formal layers exist but >50% of functional code remains in legacy locations.

---

## 9. Naming Convention Compliance

| Convention | Rule | Compliance |
|-----------|------|:----------:|
| Classes | PascalCase | 100% (0 violations found) |
| Functions | snake_case | 100% (0 violations: no `def [A-Z]` found) |
| Constants | UPPER_SNAKE_CASE | 99% |
| Files (modules) | snake_case.py | 100% |
| Folders | snake_case | 100% |
| Logger usage | `get_logger(__name__)` | 99% |
| Docstrings | Korean comments | 99% |

**Naming Convention Score: 99%**

---

## 10. Summary of All Issues

### 10.1 Critical Issues (RED)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| C-1 | Presentation bypasses Application layer | `src/web/routes/` (12/13 files) | Violates layered architecture; business logic duplicated in routes |
| C-2 | 5 duplicate `_get_store_db_path()` functions | `api_receiving`, `api_inventory`, `api_category`, `api_prediction`, `api_report` | Bypasses DBRouter, no ATTACH views, connection handling inconsistent |
| C-3 | Raw `sqlite3.connect()` in web routes | `api_receiving`, `api_inventory`, `api_category`, `api_settings` | Bypasses Repository pattern entirely |

### 10.2 Warning Issues (YELLOW)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| W-1 | Domain Strategy delegates to I/O-containing legacy code | `src/domain/prediction/strategies/food.py` -> `src/prediction/categories/food.py` | Transitive I/O contamination in Domain layer |
| W-2 | `app_settings` dual placement | `connection.py`, `schema.py`, `AppSettingsRepository` | Potential data shadowing between common.db and store DB |
| W-3 | `src/domain/evaluation/` re-exports I/O-bound classes | `evaluation/__init__.py` -> `PreOrderEvaluator` | Makes Infrastructure code accessible from Domain path |
| W-4 | StoreContext not used in web layer | All `src/web/routes/api_*.py` files | Store isolation relies on raw string matching instead of typed context |
| W-5 | `OrderAnalysisRepository` does not extend BaseRepository | `order_analysis_repo.py:138` | Documented intentional exception; separate analysis DB |
| W-6 | Legacy code volume exceeds formal layer code | 153 vs 109 files | Architecture migration incomplete |
| W-7 | `daily_job.py` in `src/scheduler/` instead of Application | `src/scheduler/daily_job.py` | Main orchestrator lives outside formal layer |

### 10.3 Informational (BLUE)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| I-1 | `src/db/repository.py` is a deprecation shim | `src/db/repository.py` | Emits DeprecationWarning; correct behavior |
| I-2 | `src/config/*.py` duplicates `src/settings/*.py` | Both locations | Documented re-export for backward compatibility |
| I-3 | `src/web/app.py` references legacy DB path | `app.py:15` (`DB_PATH = bgf_sales.db`) | `DB_PATH` variable set but may not be actively used |

---

## 11. Detailed Metrics

### 11.1 Import Violation Summary

| From Layer | To Layer | Violation Count | Severity |
|-----------|----------|:---------------:|:--------:|
| Domain -> Infrastructure | (transitive via legacy) | 3 (food.py) | WARNING |
| Domain -> Application | 0 | PASS |
| Infrastructure -> Application | 0 | PASS |
| Infrastructure -> Presentation | 0 | PASS |
| Application -> Presentation | 0 | PASS |
| Presentation -> Infrastructure (direct) | ~50+ imports | FAIL |
| Settings -> any higher layer | 0 | PASS |

### 11.2 File Placement Audit

| Layer | Expected Location | Correctly Placed | Misplaced |
|-------|-------------------|:----------------:|:---------:|
| Settings | `src/settings/` | 6/6 (100%) | 0 |
| Domain | `src/domain/` | 31/31 (100%) | 0 (but 15 delegate to legacy) |
| Infrastructure | `src/infrastructure/` | ~40/~40 (100%) | 0 (but collectors are re-exports) |
| Application | `src/application/` | ~20/~20 (100%) | 0 |
| Presentation | `src/presentation/` | ~12/~12 (100%) | 0 (but web routes are in `src/web/`) |
| **Legacy (unplaced)** | N/A | **~153 files** | Should be distributed |

---

## 12. Recommended Actions

### 12.1 Immediate Actions (High Priority)

1. **Extract Application services for web routes** -- Create service classes in `src/application/services/` for each major web API area (order, prediction, report, waste, inventory, receiving, category). Routes should call services, not repos/DBRouter directly.

2. **Eliminate `_get_store_db_path()` duplication** -- Replace all 5 instances in web routes with `DBRouter.get_store_db_path()` or `DBRouter.get_store_connection()`. This ensures consistent connection handling and ATTACH behavior.

3. **Resolve `app_settings` dual placement** -- Decide: is `app_settings` per-store or global? If per-store, remove from `COMMON_SCHEMA` and `_COMMON_VIEW_TABLES`. If global, change `AppSettingsRepository.db_type` to `"common"` and remove from `STORE_TABLES`/`STORE_SCHEMA`.

### 12.2 Medium Priority

4. **Extract I/O from `src/prediction/categories/food.py`** -- Move the `HourlySalesRepository` and `DBRouter` calls out of the food category module into a data provider that the Strategy receives as a parameter. This would make the Domain layer truly I/O-free.

5. **Remove `src/domain/evaluation/` re-export** -- Either move `PreOrderEvaluator`'s pure logic into Domain and its I/O into Infrastructure, or remove the domain re-export path to prevent confusion.

6. **Adopt StoreContext in web routes** -- Replace `DEFAULT_STORE_ID` constant usage with `StoreContext.from_store_id(store_id)` to get proper store isolation and validation.

### 12.3 Long-Term (Low Priority)

7. **Migrate legacy `src/prediction/` to formal layers** -- This is the largest migration task (~59 files). Pure prediction logic goes to Domain; I/O-bound code goes to Infrastructure; orchestration goes to Application.

8. **Move `src/scheduler/daily_job.py` to Application** -- This file is the main data collection orchestrator and belongs in `src/application/use_cases/` or `src/application/scheduler/`.

9. **Move `src/web/` routes to `src/presentation/web/routes/`** -- Complete the Presentation layer migration.

### 12.4 Documentation Updates Needed

10. **Update CLAUDE.md**: Correct `app_settings` table placement (currently lists it under Common DB but code has it in Store DB).
11. **Update CLAUDE.md**: Note that `validation_log` is in store DB, not common DB.

---

## 13. Architecture Match Rate Calculation

```
Category Weights:
  - Formal Layer Isolation:           20% weight x 95%  = 19.0
  - Repository Pattern:               15% weight x 97%  = 14.55
  - Strategy Pattern:                 10% weight x 100% = 10.0
  - DB Split Compliance:              15% weight x 94%  = 14.1
  - StoreContext Usage:               10% weight x 70%  =  7.0
  - Code Organization (Legacy Debt):  15% weight x 55%  =  8.25
  - Naming Conventions:                5% weight x 99%  =  4.95
  - Presentation Layer Compliance:    10% weight x 15%  =  1.5
  ─────────────────────────────────────────────────────
  Weighted Total:                                        79.35%
```

**Overall Architecture Match Rate: 79%** -- WARNING

The formal layer boundaries are well-designed and correctly enforced within the formal packages. However, the large volume of legacy code (~153 files) outside the formal layers, combined with web routes that bypass the Application layer entirely, reduces the effective architecture compliance significantly.

---

## 14. Conclusion

The BGF Retail auto-order project has a **well-designed** layered architecture with clean formal boundaries. The Domain layer's core modules (models, order logic, new product logic) are properly I/O-free. The Repository pattern is consistently applied with correct `db_type` assignments. The Strategy pattern for category-specific prediction is clean and complete.

The primary weakness is **migration completeness**: the architecture was introduced after significant legacy code existed, and ~60% of functional code (especially `src/prediction/`, `src/collectors/`, `src/web/routes/`) remains outside the formal layers. The most impactful gap is that **12 of 13 web API routes bypass the Application service layer**, creating a direct Presentation-to-Infrastructure coupling that defeats the purpose of the layered architecture.

The recommended prioritization is:
1. Fix the Presentation -> Application bypass (highest architectural impact)
2. Eliminate `_get_store_db_path()` and raw `sqlite3.connect()` duplication
3. Resolve `app_settings` ambiguity
4. Gradually migrate legacy modules to formal layers

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-01 | Initial comprehensive architecture review | gap-detector |
