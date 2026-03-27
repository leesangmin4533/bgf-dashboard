# cut-replacement-fix Gap Analysis Report

> **Analysis Type**: Design vs Implementation Gap Analysis
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector
> **Date**: 2026-03-09
> **Design Doc**: [cut-replacement-fix.design.md](../02-design/features/cut-replacement-fix.design.md)
> **Plan Doc**: [cut-replacement-fix.plan.md](../01-plan/features/cut-replacement-fix.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

CUT(발주중지) 상품의 수요 데이터가 폐기되어 `CutReplacementService`가 한 번도 실행되지 않는
구조적 버그 수정(cut-replacement-fix)의 설계 대비 구현 일치율을 검증한다.

### 1.2 Analysis Scope

- **Design Document**: `bgf_auto/docs/02-design/features/cut-replacement-fix.design.md`
- **Plan Document**: `bgf_auto/docs/01-plan/features/cut-replacement-fix.plan.md`
- **Implementation Files**:
  - `bgf_auto/src/prediction/pre_order_evaluator.py` (lines 1130-1217)
  - `bgf_auto/src/order/auto_order.py` (lines 953-996, 1140, 1196-1250)
  - `bgf_auto/tests/test_cut_replacement.py` (test_15 ~ test_21)

---

## 2. Gap Analysis: Design vs Implementation

### 2.1 Fix A: pre_order_evaluator.py -- CUT SKIP 수요 데이터 보존

#### Checklist

| # | Design Requirement | Implementation | Status | Notes |
|---|-------------------|----------------|:------:|-------|
| A1 | `cut_conn` 닫기 전에 CUT 수요 배치 조회 수행 | L1161-1194: `cut_in_candidates` + `cut_demand_map` 조회 후 L1195 `cut_conn.close()` | PASS | try 블록 내 배치 조회 설계 그대로 |
| A2 | `cut_in_candidates` = item_codes 중 cut_items 교집합 | L1162: `[cd for cd in item_codes if cd in cut_items] if cut_items else []` | PASS | cut_items 빈 경우 빈 리스트 안전 처리 추가 (positive) |
| A3 | `cut_demand_map` dict 초기화 | L1163: `cut_demand_map = {}` | PASS | 정확 일치 |
| A4 | daily_avg 배치 조회 (daily_sales, SUM/days) | L1172-1182: `SELECT item_cd, CAST(SUM(sale_qty) AS REAL) / ? as daily_avg FROM daily_sales ds` | PASS | 설계와 동일 SQL 패턴 |
| A5 | store_filter 조건부 적용 | L1167-1168: `store_filter_ds` + `store_params_ds` | PASS | 설계 그대로 |
| A6 | `_chunked()` 사용 (SQLite IN 제한) | L1169: `for chunk in self._chunked(cut_in_candidates)` | PASS | `_chunked` 메서드 L398 확인 (chunk_size=900) |
| A7 | mid_cd, item_nm 배치 조회 (products via ATTACH) | L1184-1192: `SELECT item_cd, item_nm, mid_cd FROM products` | PASS | |
| A8 | row 인덱스: `row[2]`=mid_cd, `row[1]`=item_nm | L1190: `entry["mid_cd"] = row[2] or ""`, L1191: `entry["item_nm"] = row[1] or ""` | PASS | 설계 초안의 row 인덱스 오류가 구현에서 올바르게 수정됨 |
| A9 | 조회 실패 시 예외 무시 (try/except) | L1193-1194: `except Exception as e: logger.warning(...)` | PASS | 설계에 명시 안 됐으나 안전 방어 추가 (positive) |
| A10 | PreOrderEvalResult에 demand_info 채우기 | L1200-1212: `demand_info.get("item_nm", "")`, `demand_info.get("mid_cd", "")`, `demand_info.get("daily_avg", 0)` | PASS | 설계 그대로 |
| A11 | daily_avg에 round(,2) 적용 | L1210: `round(demand_info.get("daily_avg", 0), 2)` | PASS | 설계에 미명시, 구현에서 정밀도 보호 추가 (positive) |
| A12 | 수요 보존 건수 로깅 | L1215-1216: `수요보존: {count}건` | PASS | 설계에 미명시, 디버그 가시성 향상 (positive) |
| A13 | cut_conn 이후 새 conn에서 all_daily_avgs/products_map 로딩 유지 | L1222-1224: `conn = self._get_conn()`, `all_daily_avgs = ...` | PASS | 기존 흐름 보존 |

**Fix A Score**: 13/13 (100%)

### 2.2 Fix B: auto_order.py -- eval_results CUT SKIP -> _cut_lost_items 변환

#### Checklist

| # | Design Requirement | Implementation | Status | Notes |
|---|-------------------|----------------|:------:|-------|
| B1 | `cut_replacement_cfg.get("enabled")` 체크 | L957: `if cut_replacement_cfg.get("enabled", False)` | PASS | |
| B2 | `eval_results` 존재 시 CUT SKIP 추출 | L959: `if eval_results:` | PASS | |
| B3 | `EvalDecision` import | L960: `from src.prediction.pre_order_evaluator import EvalDecision` | PASS | |
| B4 | 중복 방지: `existing_cut_cds` set | L961: `existing_cut_cds = {item.get("item_cd") for item in self._cut_lost_items}` | PASS | 설계와 동일 |
| B5 | 조건: `r.decision == EvalDecision.SKIP` | L963: 동일 | PASS | |
| B6 | 조건: `"CUT" in r.reason` | L964: 동일 | PASS | |
| B7 | 조건: `r.mid_cd in FOOD_CATEGORIES` | L965: 동일 | PASS | |
| B8 | 조건: `r.daily_avg > 0` | L966: 동일 | PASS | |
| B9 | 조건: `cd not in existing_cut_cds` (중복 방지) | L967: 동일 | PASS | |
| B10 | append dict: item_cd, mid_cd, predicted_sales, item_nm | L968-973: `{"item_cd": cd, "mid_cd": r.mid_cd, "predicted_sales": r.daily_avg, "item_nm": r.item_nm}` | PASS | |
| B11 | `_cut_lost_items` 비어있지 않으면 CutReplacementService 호출 | L980-988: `if self._cut_lost_items: svc = CutReplacementService(...)` | PASS | |
| B12 | 보충 수량 로깅 | L990-993: `before_qty -> after_qty` 비교 로깅 | PASS | 설계에 미명시, 운영 가시성 추가 (positive) |
| B13 | eval 복원 건수 로깅 | L976-977: `(eval={count}건 복원)` | PASS | 설계에 미명시 (positive) |
| B14 | `_cut_lost_items = []` 실행 사이클 초기화 | L1140: `self._cut_lost_items = []` | PASS | 이전 실행 오염 방지 |
| B15 | `_refilter_cut_items` prefetch CUT 기존 동작 유지 | L1197: `order_list, _lost = self._refilter_cut_items(order_list)` + L1198: `self._cut_lost_items.extend(_lost)` | PASS | 2개소(L1197, L1249) 모두 유지 |
| B16 | `__getattr__` 기본값: `_cut_lost_items: list` | L208: 확인 | PASS | 테스트 호환 |

**Fix B Score**: 16/16 (100%)

### 2.3 수정하지 않는 파일 검증

| # | Design Requirement | Status | Notes |
|---|-------------------|:------:|-------|
| C1 | `cut_replacement.py` 변경 없음 | PASS | 구현 미변경 확인 |
| C2 | `_refilter_cut_items()` 역할 유지 | PASS | L218-243 변경 없음 |
| C3 | `prediction_config.py` 변경 없음 | PASS | `cut_replacement.enabled=True` 기존 유지 |
| C4 | `PreOrderEvalResult` dataclass 변경 없음 | PASS | L57-71 기존 필드 그대로 (mid_cd, daily_avg, item_nm 모두 기존 존재) |

**Unchanged Files Score**: 4/4 (100%)

### 2.4 Test Coverage (Design 4.1-4.3 vs tests test_15~test_21)

| # | Design Test | Impl Test | Status | Notes |
|---|------------|-----------|:------:|-------|
| T1 | CUT SKIP에 daily_avg/mid_cd 보존 (4.1 test_cut_skip_preserves_demand) | test_15: eval_results CUT -> _cut_lost_items 변환 | PARTIAL | test_15는 Fix B 관점에서 검증. Fix A의 PreOrderEvaluator 직접 호출 통합 테스트는 미작성 (DB 모킹 복잡) |
| T2 | 판매 없는 CUT daily_avg=0 (4.1 test_cut_skip_no_sales_zero_avg) | test_17: daily_avg=0 CUT 제외 | PASS | 동일 시나리오 |
| T3 | eval_results CUT -> _cut_lost_items 변환 (4.2 test_eval_results_cut_to_lost_items) | test_15 | PASS | 2건 복원, predicted_sales > 0, mid_cd == "001" 검증 |
| T4 | 비푸드 CUT 제외 (4.2 test_non_food_cut_excluded) | test_16: mid_cd="042" CUT 미포함 | PASS | FOOD_CATEGORIES 필터 정확 |
| T5 | 중복 방지 (4.2 test_duplicate_prevention) | test_18: refilter 기존 + eval 중복 | PASS | existing_cut_cds 체크 동작 |
| T6 | daily_avg=0 CUT 제외 (4.2 test_zero_avg_cut_excluded) | test_17 | PASS | |
| T7 | CutReplacementService 정상 호출 E2E (4.3 test_cut_replacement_actually_called) | test_20: eval 복원 -> CutReplacementService 실행 -> 보충 발생 | PASS | lost_demand 2건, 보충 >= 1 검증 |
| T8 | 기존 _refilter_cut_items 동작 유지 (4.3 test_existing_refilter_still_works) | test_21: prefetch CUT 포착 | PASS | |
| - | 비CUT SKIP 제외 (설계 미명시) | test_19: 일반 SKIP "재고 충분 + 저인기"는 미포함 | PASS (bonus) | 설계에 없는 추가 엣지 케이스 |

**Test Score**: 7/8 PASS + 1 PARTIAL + 1 bonus

---

## 3. Gap Details

### 3.1 Missing Features (Design O, Implementation X)

| # | Item | Design Location | Severity | Notes |
|---|------|-----------------|:--------:|-------|
| G-1 | Fix A 직접 통합 테스트 (PreOrderEvaluator.evaluate_all 호출 -> CUT result 필드 검증) | Design 4.1 test_cut_skip_preserves_demand | Low | DB ATTACH + daily_sales 모킹 복잡도 때문에 생략 가능. test_15~20이 Fix B 관점에서 end-to-end 동작을 간접 검증함. |

### 3.2 Added Features (Design X, Implementation O)

| # | Item | Implementation Location | Severity | Notes |
|---|------|------------------------|:--------:|-------|
| P-1 | `round(daily_avg, 2)` 정밀도 보호 | pre_order_evaluator.py L1210 | Positive | 부동소수점 누적 오차 방지 |
| P-2 | `cut_demand_map` 조회 실패 시 try/except 방어 | pre_order_evaluator.py L1193-1194 | Positive | 조회 실패해도 빈 demand_info로 진행 |
| P-3 | 수요보존 건수 로깅 | pre_order_evaluator.py L1215-1216 | Positive | 운영 디버그 가시성 |
| P-4 | eval 복원 건수 로깅 | auto_order.py L976-977 | Positive | `(eval={N}건 복원)` |
| P-5 | `cut_items` 빈 경우 `cut_in_candidates` 빈 리스트 안전 처리 | pre_order_evaluator.py L1162 `if cut_items else []` | Positive | cut_items가 None/빈 set일 때 안전 |
| P-6 | test_19 비CUT SKIP 제외 테스트 | test_cut_replacement.py L701-731 | Positive | 설계에 없는 엣지 케이스 커버 |

### 3.3 Changed Features (Design != Implementation)

None. 설계와 구현 간 불일치 항목 없음.

---

## 4. Data Flow Verification

설계 Section 3의 데이터 흐름 vs 구현 추적:

```
[pre_order_evaluator.evaluate_all()]
  L1133: cut_conn open
  L1138-1142: cut_items = {is_cut_item=1 상품들}       -- Design 2.1 OK
  L1161-1194: cut_demand_map = {daily_avg, mid_cd, item_nm 배치 조회}  -- Design Fix A OK
  L1195: cut_conn.close()
  L1198-1212: PreOrderEvalResult(daily_avg=실제값, mid_cd=실제값)  -- Design Fix A OK
    -> eval_results dict에 포함

[auto_order.get_recommendations()]
  L797: eval_results = self.pre_order_evaluator.evaluate_all()
    -> eval_results에 CUT SKIP 항목 (daily_avg>0, mid_cd 채워짐)

  L1140: self._cut_lost_items = []  (실행 사이클 초기화)
  L1197: _refilter_cut_items -> _cut_lost_items.extend(_lost)  (prefetch 실시간 CUT)
  L1249: _refilter_cut_items -> _cut_lost_items.extend(_lost)  (2번째 호출)

  L957-973: eval_results CUT SKIP -> _cut_lost_items.append(...)  -- Design Fix B OK
  L980-988: CutReplacementService.supplement_cut_shortage(
              order_list, cut_lost_items=[N건])          -- Design 호출 OK
    -> 동일 mid_cd 대체 상품에 보충
```

Data flow verification: **PASS** -- 설계 Section 3과 완전 일치.

---

## 5. Overall Scores

| Category | Items | Matched | Score | Status |
|----------|:-----:|:-------:|:-----:|:------:|
| Fix A (pre_order_evaluator) | 13 | 13 | 100% | PASS |
| Fix B (auto_order) | 16 | 16 | 100% | PASS |
| Unchanged Files | 4 | 4 | 100% | PASS |
| Tests (Design 8 scenarios) | 8 | 7.5 | 93.8% | PASS |
| Data Flow | 1 | 1 | 100% | PASS |
| **Total** | **42** | **41.5** | **98.8%** | **PASS** |

```
+-----------------------------------------------+
|  Overall Match Rate: 98.8%                     |
+-----------------------------------------------+
|  PASS  Fix A (CUT demand preservation): 13/13  |
|  PASS  Fix B (eval_results -> lost_items): 16/16|
|  PASS  Unchanged files verification:    4/4    |
|  PASS  Test coverage:                  7.5/8   |
|  PASS  Data flow correctness:          1/1     |
|  (+6)  Positive additions (bonus)              |
+-----------------------------------------------+
```

---

## 6. Convention Compliance

| Category | Convention | Status |
|----------|-----------|:------:|
| Naming: variables | snake_case (`cut_demand_map`, `cut_in_candidates`) | PASS |
| Naming: methods | snake_case (`_refilter_cut_items`, `_chunked`) | PASS |
| Naming: constants | UPPER_SNAKE_CASE (`FOOD_CATEGORIES`) | PASS |
| Error handling | `except Exception as e: logger.warning(...)` (no silent pass) | PASS |
| DB access | Repository/cursor pattern with try/finally | PASS |
| Logging | `get_logger(__name__)` used | PASS |
| Architecture | Prediction layer (pre_order_evaluator) -> Order layer (auto_order) dependency direction correct | PASS |

Convention Score: **100%**

---

## 7. Regression Risk Assessment

| Item | Risk | Mitigation |
|------|:----:|------------|
| Existing 14 CutReplacementService tests (test_01~14) | Low | No changes to `cut_replacement.py` |
| PreOrderEvaluator existing tests | Low | Only additive code within try block; failure falls through to empty `cut_demand_map` |
| `_refilter_cut_items` 2-location consistency | None | Method unchanged (L218-243) |
| `evaluate_all` performance | Negligible | CUT count typically < 10; 1 extra SQL per chunk |
| `_cut_lost_items` initialization | None | L1140 explicit reset per execution cycle |

---

## 8. Summary

### Verdict: **PASS** (98.8%)

This fix correctly resolves the root cause: CUT SKIP items now preserve their demand data
(`daily_avg`, `mid_cd`, `item_nm`) through two coordinated changes:

1. **Fix A** (pre_order_evaluator.py): Batch-queries demand data for CUT items within the
   existing `cut_conn` session, before it closes, and populates `PreOrderEvalResult` with
   real values instead of zeros/empty strings.

2. **Fix B** (auto_order.py): Extracts CUT SKIP items from `eval_results` (where demand
   is now preserved) into `_cut_lost_items`, with proper guards for FOOD_CATEGORIES,
   positive daily_avg, and duplicate prevention. This enables `CutReplacementService` to
   finally receive non-empty input.

The single gap (G-1) is a missing direct integration test for Fix A's PreOrderEvaluator,
which is low severity since the end-to-end path is covered by test_15 and test_20.

Six positive additions (P-1 through P-6) improve robustness beyond the design specification.

### Recommended Actions

- **Optional**: Add a mocked integration test for `PreOrderEvaluator.evaluate_all()` that
  verifies CUT SKIP results contain non-zero `daily_avg` and non-empty `mid_cd` directly
  (addresses G-1, low priority).

---

## 9. Files Analyzed

| File | Lines Examined | Role |
|------|:--------------:|------|
| `bgf_auto/src/prediction/pre_order_evaluator.py` | 1125-1224 | Fix A: CUT demand preservation |
| `bgf_auto/src/order/auto_order.py` | 200-210, 218-243, 940-996, 1130-1250 | Fix B: eval_results -> _cut_lost_items |
| `bgf_auto/tests/test_cut_replacement.py` | 535-783 (test_15 ~ test_21) | 7 new tests |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-09 | Initial gap analysis | gap-detector |
