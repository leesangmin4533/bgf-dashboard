# settings-web-ui Gap Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: BGF 리테일 자동 발주 시스템
> **Analyst**: gap-detector
> **Date**: 2026-02-26
> **Design Doc**: [settings-web-ui.design.md](../../02-design/features/settings-web-ui.design.md)
> **Plan Doc**: [settings-web-ui.plan.md](../../01-plan/features/settings-web-ui.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Check phase of the PDCA cycle for the "settings-web-ui" feature. Compare the design specification against the actual implementation to identify gaps, mismatches, and additions.

### 1.2 Analysis Scope

- **Design Document**: `docs/02-design/features/settings-web-ui.design.md`
- **Plan Document**: `docs/01-plan/features/settings-web-ui.plan.md`
- **Implementation Files**:
  - `src/web/routes/api_settings.py` (backend API)
  - `src/web/routes/__init__.py` (blueprint registration)
  - `src/web/templates/index.html` (HTML panels)
  - `src/web/static/js/settings.js` (frontend JS)
  - `src/web/static/js/app.js` (tab trigger)
  - `tests/test_settings_web_ui.py` (tests)
- **Analysis Date**: 2026-02-26

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 92% | OK |
| Architecture Compliance | 95% | OK |
| Convention Compliance | 96% | OK |
| Test Coverage | 100% | OK |
| **Overall** | **95%** | **OK** |

---

## 3. API Endpoint Comparison

### 3.1 Endpoint Match Table

| # | Design Endpoint | Implementation | Method | Status | Notes |
|---|----------------|----------------|--------|--------|-------|
| 1 | `GET /api/settings/eval-params` | `settings_bp.route("/eval-params")` | GET | MATCH | Registered with `url_prefix="/api/settings"` |
| 2 | `POST /api/settings/eval-params` | `settings_bp.route("/eval-params", methods=["POST"])` | POST | MATCH | Body: `{key, value}` |
| 3 | `POST /api/settings/eval-params/reset` | `settings_bp.route("/eval-params/reset", methods=["POST"])` | POST | MATCH | Body: `{key}` or `{key: "__all__"}` |
| 4 | `GET /api/settings/feature-flags` | `settings_bp.route("/feature-flags")` | GET | MATCH | |
| 5 | `POST /api/settings/feature-flags` | `settings_bp.route("/feature-flags", methods=["POST"])` | POST | CHANGED | See Section 4.3 |
| 6 | `GET /api/settings/audit-log` | `settings_bp.route("/audit-log")` | GET | MATCH | `?hours=` parameter |

**API Match Rate: 6/6 endpoints implemented (100%)**

### 3.2 Response Format Comparison

| Endpoint | Design Response | Implementation Response | Status |
|----------|----------------|------------------------|--------|
| GET eval-params | `{params, groups}` | `{params, nested, groups}` | CHANGED |
| POST eval-params | `{ok, old_value, new_value}` | `{ok, old_value, new_value}` | MATCH |
| POST eval-params/reset | `{ok, reset_count}` | `{ok, reset_count}` | MATCH |
| GET feature-flags | `{flags: [{key, value, description}]}` | `{flags: [{key, value, description}]}` | MATCH |
| POST feature-flags | `{ok, key, old_value, new_value}` | `{ok, key, old_value, new_value}` | MATCH |
| GET audit-log | `{logs: [{id, ...}]}` | `{logs: [{id, ...}]}` | MATCH |

---

## 4. Differences Found

### 4.1 CHANGED: Feature-flags POST URL (Plan vs Design vs Implementation)

| Source | URL |
|--------|-----|
| Plan Document (line 36) | `POST /api/settings/feature-flags/<key>` |
| Design Document (line 49) | `POST /api/settings/feature-flags` with body `{key, value}` |
| Implementation (line 250) | `POST /api/settings/feature-flags` with body `{key, value}` |

**Impact**: Low. The plan document specified a URL parameter pattern (`/<key>`), but the design and implementation both use a request body pattern (`{key, value}`). The design and implementation are consistent with each other; only the plan document needs updating.

**Recommendation**: Update plan document line 36 to match the design/implementation convention.

### 4.2 ADDED: `nested` field in GET eval-params response

| Source | Response |
|--------|----------|
| Design Document | `{params, groups}` |
| Implementation (line 134) | `{params, nested, groups}` |

**Details**: The implementation separates eval_params.json entries into two categories:
- `params`: simple parameters with `{value, default, min, max, description}` structure
- `nested`: complex objects (e.g., `cost_optimization`) that do not have a `value` key

This is a practical addition that prevents nested configuration objects from being editable through the simple parameter UI, which would cause errors.

**Impact**: Low (additive, non-breaking). The frontend `settings.js` only uses `data.params` and `data.groups`, so `nested` is safely ignored.

**Recommendation**: Document the `nested` field in the design document for completeness.

### 4.3 CHANGED: Default audit-log hours

| Source | Default Hours |
|--------|:------------:|
| Design Document (line 54) | 24 hours |
| Implementation (line 292) | 168 hours (7 days) |
| Frontend request (settings.js line 14) | 168 hours (7 days) |

**Impact**: Low. The design specified `?hours=24` as the example query parameter, and the implementation defaults to 168 (7 days) when no `hours` parameter is provided. This provides more useful historical data by default.

**Recommendation**: Update design document to reflect the 7-day default.

### 4.4 CHANGED: PARAM_GROUPS incomplete vs design

| Source | Groups Defined |
|--------|---------------|
| Design Document (line 16-21) | 5 groups: prediction, calibration, cost_optimization, holiday, waste_cause |
| Implementation (lines 42-52) | 2 groups: prediction, calibration |

**Details**: The design document specifies 5 parameter groups, but only 2 are defined in `PARAM_GROUPS` in the implementation. The missing groups are: `cost_optimization`, `holiday`, and `waste_cause`. The implementation handles ungrouped parameters by rendering them under a "기타" (Other) section, so they are still displayed, just not categorized.

**Impact**: Medium. Users see a large "기타" section instead of organized group headers for cost_optimization, holiday, and waste_cause parameters.

**Recommendation**: Add the 3 missing groups to `PARAM_GROUPS` in `api_settings.py`.

### 4.5 MINOR: Tab click handler does not call `loadSettingsData`

| Source | Behavior |
|--------|----------|
| Design Document (Section 2) | Settings tab shows 3 sub-panels on activation |
| app.js tab click handler (line 184) | Only calls `loadUsersTab()`, does NOT call `loadSettingsData()` |
| app.js `reloadActiveTab()` (line 148) | Calls both `loadUsersTab()` and `loadSettingsData()` |

**Details**: In `app.js`, the direct tab click event handler (line 183-186) only calls `loadUsersTab()` when the settings tab is activated. The `loadSettingsData()` call is only present in `reloadActiveTab()` (line 146-149), which is triggered by store changes. This means when a user first clicks the Settings tab, the eval params / feature flags / audit log panels will show "로딩중..." and never load.

**Impact**: High. The three new panels will not load on first tab activation.

**Recommendation**: Add `loadSettingsData()` call to the tab click handler alongside `loadUsersTab()`.

### 4.6 ADDED: Range validation (excluded from scope, but implemented)

| Source | Behavior |
|--------|----------|
| Plan Document (Section 3, Excluded) | "실시간 검증 (범위 체크 등)" listed as excluded from v1 scope |
| Implementation (lines 170-175) | Server-side min/max range validation implemented |

**Details**: The plan document explicitly excluded range checking from the v1 scope, but the implementation includes full server-side range validation (`min_val`, `max_val` checks) in `update_eval_param()`. This is a positive addition that prevents invalid parameter values.

**Impact**: None (beneficial addition).

**Recommendation**: Remove the exclusion note from the plan document, or document this as a scope expansion.

---

## 5. UI Comparison

### 5.1 Panel Structure

| Design Panel | Implementation Element | Status |
|-------------|----------------------|--------|
| "파라미터" sub-panel | `<div id="evalParamsContainer">` in `index.html:1090` | MATCH |
| "기능 토글" sub-panel | `<div id="featureFlagsContainer">` in `index.html:1100` | MATCH |
| "변경 이력" sub-panel | `<table id="auditLogTable">` in `index.html:1111` | MATCH |
| [전체 리셋] button | `resetAllEvalParams()` in `index.html:1087` | MATCH |
| [저장] per parameter | `saveEvalParam()` in `settings.js:109` | MATCH |
| 7 feature flags | `FEATURE_FLAG_DEFS` in `api_settings.py:31-39` (7 items) | MATCH |
| Switch UI for toggles | CSS toggle-switch in `settings.js:176-179` | MATCH |
| Group fold/unfold | `param-group` div with h4 headers in `settings.js:71-77` | PARTIAL |

**Note on fold/unfold**: The design specifies "그룹별 접기/펼치기 폼" (collapsible groups), but the implementation renders groups as static `<div>` sections without collapse/expand toggle. This is a minor UI simplification.

**Impact**: Low (cosmetic).

### 5.2 Admin-only Access Control

| Design Requirement | Implementation | Status |
|-------------------|---------------|--------|
| admin-only for edit endpoints | `@admin_required` decorator on POST routes | MATCH |
| admin-only for parameter panel | `data-admin-only` attribute on panel divs | MATCH |
| admin-only for toggle panel | `data-admin-only` attribute on panel div | MATCH |
| audit log visible to all | No `data-admin-only` on audit log panel | MATCH |

---

## 6. Data Model Comparison

### 6.1 settings_audit_log Table

| Field | Design Type | Implementation Type | Status |
|-------|------------|---------------------|--------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | INTEGER PRIMARY KEY AUTOINCREMENT | MATCH |
| setting_key | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| old_value | TEXT | TEXT | MATCH |
| new_value | TEXT | TEXT | MATCH |
| changed_by | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| changed_at | TEXT NOT NULL | TEXT NOT NULL | MATCH |
| category | TEXT NOT NULL | TEXT NOT NULL | MATCH |

**Data Model Match Rate: 7/7 fields (100%)**

---

## 7. Test Coverage

### 7.1 Test Match Table

| # | Design Test | Implementation Test | Status |
|---|-------------|---------------------|--------|
| 1 | test_get_eval_params | `TestGetEvalParams.test_get_eval_params_format` | MATCH |
| 2 | test_update_eval_param | `TestUpdateEvalParam.test_update_eval_param` | MATCH |
| 3 | test_update_eval_param_range | `TestEvalParamRange.test_reject_out_of_range` | MATCH |
| 4 | test_reset_eval_param | `TestResetEvalParam.test_reset_single_param` | MATCH |
| 5 | test_reset_all_eval_params | `TestResetAllEvalParams.test_reset_all_params` | MATCH |
| 6 | test_get_feature_flags | `TestGetFeatureFlags.test_get_feature_flags_format` | MATCH |
| 7 | test_toggle_feature_flag | `TestToggleFeatureFlag.test_toggle_feature_flag` | MATCH |
| 8 | test_audit_log_recorded | `TestAuditLogRecorded.test_change_creates_audit_log` | MATCH |
| 9 | test_audit_log_query | `TestAuditLogQuery.test_audit_log_format` | MATCH |
| 10 | test_blueprint_registered | `TestBlueprintRegistered.test_settings_blueprint_in_register` | MATCH |

**Test Match Rate: 10/10 (100%)**
**Test Execution: 10/10 passed**

---

## 8. Architecture Compliance

### 8.1 Layer Placement

| Component | Expected Layer | Actual Location | Status |
|-----------|---------------|-----------------|--------|
| api_settings.py | Presentation (Web Routes) | `src/web/routes/api_settings.py` | MATCH |
| settings.js | Presentation (Frontend) | `src/web/static/js/settings.js` | MATCH |
| index.html panels | Presentation (Template) | `src/web/templates/index.html` | MATCH |
| Blueprint registration | Presentation (Config) | `src/web/routes/__init__.py` | MATCH |

### 8.2 Dependency Direction

| Source File | Import | Target Layer | Allowed | Status |
|------------|--------|-------------|---------|--------|
| api_settings.py | `src.utils.logger` | Infrastructure | Yes | OK |
| api_settings.py | `.api_auth` (decorators) | Presentation | Yes (same layer) | OK |
| api_settings.py | `src.settings.constants` | Settings | Yes | OK |
| api_settings.py | `flask` | External lib | Yes | OK |

No dependency violations detected.

### 8.3 Architecture Notes

- The `api_settings.py` module directly reads/writes `config/eval_params.json` files and `common.db` without going through a Repository class. This is acceptable for a configuration management module that operates on JSON files and a simple audit log table, but is worth noting as a deviation from the Repository pattern used elsewhere in the project.
- The `setattr(const, key, new_value)` approach for feature flags (line 271) modifies Python module-level constants at runtime. This works for the single-process Flask server but would not persist across restarts. This is consistent with the design's approach of managing flags through `constants.py`.

**Architecture Compliance: 95%**

---

## 9. Convention Compliance

### 9.1 Naming Convention

| Category | Convention | Checked | Compliance | Violations |
|----------|-----------|:-------:|:----------:|-----------|
| Python functions | snake_case | 12 | 100% | None |
| Python constants | UPPER_SNAKE | 5 | 100% | None |
| Blueprint name | lowercase | 1 | 100% | `"settings"` |
| JS functions | camelCase | 9 | 100% | None |
| JS variables | camelCase | 8 | 100% | None |
| HTML IDs | camelCase | 7 | 100% | None |

### 9.2 Code Style

| Rule | Status | Notes |
|------|--------|-------|
| Logger usage (no print) | OK | Uses `get_logger(__name__)` |
| Docstrings | OK | All route functions have Korean docstrings |
| Exception handling | OK | try/except with logger.error, never silent pass |
| Connection management | OK | try/finally pattern for SQLite connections |

### 9.3 Import Order

`api_settings.py` imports:
1. `json`, `sqlite3`, `datetime`, `pathlib` (stdlib) -- OK
2. `flask` (external) -- OK
3. `src.utils.logger` (internal absolute) -- OK
4. `.api_auth` (relative) -- OK

**Convention Compliance: 96%**

---

## 10. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 95%                     |
+---------------------------------------------+
|  MATCH:             27 items (90%)           |
|  CHANGED (minor):    3 items (10%)           |
|  MISSING in impl:    0 items (0%)            |
|  ADDED in impl:      2 items (beneficial)    |
+---------------------------------------------+
```

---

## 11. Differences Detail Summary

### MISSING Features (Design O, Implementation X)

| # | Item | Design Location | Description | Impact |
|---|------|----------------|-------------|--------|
| 1 | 3 PARAM_GROUPS | design.md:17-20 | cost_optimization, holiday, waste_cause groups not defined | Medium |
| 2 | Collapsible groups | design.md:70 | "접기/펼치기" not implemented (static divs) | Low |

### ADDED Features (Design X, Implementation O)

| # | Item | Implementation Location | Description | Impact |
|---|------|------------------------|-------------|--------|
| 1 | `nested` response field | api_settings.py:127-132 | Separates complex config objects from editable params | Beneficial |
| 2 | Range validation | api_settings.py:170-175 | Server-side min/max validation (plan excluded, but implemented) | Beneficial |

### CHANGED Features (Design != Implementation)

| # | Item | Design | Implementation | Impact |
|---|------|--------|---------------|--------|
| 1 | Default audit hours | 24 hours | 168 hours (7 days) | Low |
| 2 | Tab click handler | Loads all 3 panels | Only loads users panel; settings panels require store change to trigger | High |
| 3 | Plan: feature-flags URL | `POST .../feature-flags/<key>` | `POST .../feature-flags` with body | Low (plan vs impl, design matches impl) |

---

## 12. Recommended Actions

### 12.1 Immediate Actions (High Impact)

| # | Priority | Item | File | Action |
|---|----------|------|------|--------|
| 1 | HIGH | Tab click handler missing `loadSettingsData()` | `src/web/static/js/app.js:184` | Add `if (typeof loadSettingsData === 'function') loadSettingsData();` after `loadUsersTab()` call |

### 12.2 Short-term Actions (Medium Impact)

| # | Priority | Item | File | Action |
|---|----------|------|------|--------|
| 2 | MEDIUM | Add missing PARAM_GROUPS | `src/web/routes/api_settings.py:42-52` | Add `cost_optimization`, `holiday`, `waste_cause` group definitions with their parameter keys |
| 3 | LOW | Add collapsible group UI | `src/web/static/js/settings.js:71` | Add `<details>`/`<summary>` or click-toggle for param groups |

### 12.3 Documentation Updates

| # | Item | File | Action |
|---|------|------|--------|
| 4 | Plan: feature-flags URL | `docs/01-plan/features/settings-web-ui.plan.md:36` | Change `POST .../feature-flags/<key>` to `POST .../feature-flags` with body |
| 5 | Design: add `nested` field | `docs/02-design/features/settings-web-ui.design.md:11` | Document the `nested` field in GET eval-params response |
| 6 | Design: default hours | `docs/02-design/features/settings-web-ui.design.md:54` | Update default from 24 to 168 hours |
| 7 | Plan: remove range check exclusion | `docs/01-plan/features/settings-web-ui.plan.md:27` | Remove "실시간 검증" from exclusion list since it was implemented |

---

## 13. Next Steps

- [x] Gap analysis complete
- [ ] Fix item #1: Add `loadSettingsData()` to tab click handler (High priority)
- [ ] Fix item #2: Add 3 missing PARAM_GROUPS (Medium priority)
- [ ] Update design/plan documents (items #4-#7)
- [ ] Re-run analysis after fixes
- [ ] Write completion report (`settings-web-ui.report.md`)

---

## Related Documents

- Plan: [settings-web-ui.plan.md](../../01-plan/features/settings-web-ui.plan.md)
- Design: [settings-web-ui.design.md](../../02-design/features/settings-web-ui.design.md)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-26 | Initial gap analysis | gap-detector |
