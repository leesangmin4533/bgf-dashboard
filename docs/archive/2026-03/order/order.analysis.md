# Order Pipeline Gap Analysis Report (v4)

> **Analysis Type**: Design-Implementation Gap Analysis (PDCA Check Phase)
>
> **Project**: BGF Retail Auto-Order System
> **Analyst**: gap-detector (v4 update -- _finalize_order_unit_qty analysis added)
> **Date**: 2026-03-14 (v4)
> **Design Documents**:
>   - `docs/pipeline_design.md` (Section 7: P-1~P-7 principles, Section 8: defense rules, Section 9: bug lessons)
>   - `docs/order_flow.md` (Step-by-step flow, responsibilities, promo branch details)
>   - `docs/data_contracts.md` (Required fields, site_order_counts contract, invariants)
>   - Session design intent (2026-03-14): `_finalize_order_unit_qty` specification

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Verify that the actual implementation code faithfully implements the design principles (P-1 through P-7),
defense rules, data contracts, and execution order documented in the three pipeline design documents.
This is the v2 analysis following Phase E GAP fixes and Phase F comprehensive bug review.

### 1.2 Changes from v1

| Item | v1 (Initial) | v2 | v3 | v4 (Current) |
|------|-------------|-----|-----|--------------|
| Match Rate | 95.3% (41/43) | 91.1% (41/45) | 100% (47/47) | **100% (76/76)** |
| GAPs | 2 (CUT data_days, replaced_mid_cd) | 4 (order_unit_qty x3, is_cut_item x1) | 0 | **0** |
| Fixed since prev | - | 2 (v1 GAPs) | 4 (v2 GAPs) | 0 (no new GAPs) |
| Total checks | 43 | 47 | 47 | 76 (+29 finalize) |
| New scope | - | - | - | `_finalize_order_unit_qty` design-vs-impl |

### 1.3 Analysis Scope

| Implementation File | Design Reference | Check Focus |
|---------------------|------------------|-------------|
| `src/order/auto_order.py` L750-1060 | order_flow.md Steps 4-8 | Floor/CUT/Cap order, site_order_counts |
| `src/prediction/improved_predictor.py` L1955-2110 | order_flow.md Promo branches | Branch A/B/C Fix B (stock check) |
| `src/prediction/improved_predictor.py` L1703-1727 | pipeline_design.md P-7 | promo_floor restoration after DiffFeedback |
| `src/prediction/category_demand_forecaster.py` | data_contracts.md Section 6 | is_available, COALESCE, data_days, order_unit_qty |
| `src/prediction/large_category_forecaster.py` | data_contracts.md Section 6 | is_available, COALESCE, data_days, order_unit_qty |
| `src/prediction/categories/food_daily_cap.py` | order_flow.md Step 8 | Cap as final gate, classify_items data_days |
| `src/order/cut_replacement.py` | order_flow.md Step 7 | is_available filter, data_days, order_unit_qty |

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| P-1: Cap is final gate | 100% | PASS |
| P-2: Floor within Cap range | 100% | PASS |
| P-3: Manual orders recognized | 100% | PASS |
| P-4: Stock check on force-order branches | 100% | PASS |
| P-5: is_available=0 excluded | 100% | PASS |
| P-6: NULL handling with COALESCE | 100% | PASS |
| P-7: No post-override of prior corrections | 100% | PASS |
| Data Contract: order_list item fields | 100% | PASS (v3 fixed) |
| Data Contract: site_order_counts | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## 3. Detailed Check Results

### 3.1 P-1: Cap is final gate — MATCH (3/3)

**Implementation** (`auto_order.py` L967-1059):

```
L967:  Step 5 - CategoryDemandForecaster.supplement_orders()   [Floor mid]
L985:  Step 6 - LargeCategoryForecaster.supplement_orders()    [Floor large]
L1002: Step 7 - CutReplacementService.supplement_cut_shortage() [CUT]
L1048: Step 8 - apply_food_daily_cap()                          [Cap - LAST]
```

| Check Item | Status | Evidence |
|------------|--------|----------|
| Cap called after all Floor steps | MATCH | L1048 after L967, L985 |
| Cap called after CUT step | MATCH | L1048 after L1002 |
| No code between Cap and return | MATCH | L1059 try/except ends, L1061 return |

---

### 3.2 P-2: Floor within Cap range — MATCH (3/3)

| Check Item | Status | Evidence |
|------------|--------|----------|
| Floor(mid) before Cap | MATCH | L967 before L1048 |
| Floor(large) before Cap | MATCH | L985 before L1048 |
| Floor only increases, Cap only decreases | MATCH | supplement_orders adds; apply_food_daily_cap selects/removes |

---

### 3.3 P-3: Manual orders recognized — MATCH (6/6)

**Implementation** (`auto_order.py` L752-785):

```python
sql = """
    SELECT p.mid_cd, COALESCE(SUM(ot.order_qty), 0) as total_qty
    FROM order_tracking ot
    JOIN common.products p ON ot.item_cd = p.item_cd
    WHERE ot.order_source = 'site'
      AND ot.order_date = ?
      AND ot.store_id = ?
    GROUP BY p.mid_cd
"""
```

| Check Item | Status | Evidence |
|------------|--------|----------|
| Uses SUM (not COUNT) | MATCH | `COALESCE(SUM(ot.order_qty), 0)` at L770 |
| No NOT IN manual_order_items exclusion | MATCH | No NOT IN clause in SQL |
| Passed to Floor(mid) | MATCH | L974 `site_order_counts=site_order_counts` |
| Passed to Floor(large) | MATCH | L991 `site_order_counts=site_order_counts` |
| Passed to Cap | MATCH | L1053 `site_order_counts=site_order_counts` |
| Output is dict[str, int] | MATCH | L779 `{row[0]: row[1] for row in rows}` |

---

### 3.4 P-4: Stock check on all force-order branches — MATCH (7/7)

#### Branch A (L1955-1994): Ending promotion D-3

```python
_skip_branch_a = False
if promo_status.promo_avg > 0:                              # promo_avg guard
    promo_daily_demand = promo_status.promo_avg * weekday_coef
    if current_stock + pending_qty >= promo_daily_demand:    # stock check
        _skip_branch_a = True
```

#### Branch B (L1996-2045): Starting promotion D-3

```python
_skip_branch_b = False
if promo_status.promo_avg > 0:                              # promo_avg guard
    promo_daily_demand = promo_status.promo_avg * weekday_coef
    if current_stock + pending_qty >= promo_daily_demand:    # stock check
        _skip_branch_b = True
```

#### Branch C (L2047-2085): Active promotion — promo_avg guard implicit in elif

#### Branch D (L2087-2109): Non-promo — exempt (decrease only)

| Check Item | Status | Evidence |
|------------|--------|----------|
| Branch A has promo_avg > 0 guard | MATCH | L1963 |
| Branch A has stock >= demand check | MATCH | L1966 |
| Branch B has promo_avg > 0 guard | MATCH | L2014 |
| Branch B has stock >= demand check | MATCH | L2017 |
| Branch C has stock >= demand check | MATCH | L2055 |
| Branch C promo_avg guard (implicit) | MATCH | L2049 elif condition requires promo_avg > 0 |
| Branch D exempt (decrease only) | MATCH | L2097 |

---

### 3.5 P-5: is_available=0 excluded — MATCH (3/3)

| Check Item | Status | Evidence |
|------------|--------|----------|
| category_demand_forecaster SQL has COALESCE(ri.is_available, 1) = 1 | MATCH | L243 |
| large_category_forecaster SQL has COALESCE(ri.is_available, 1) = 1 | MATCH | L405 |
| cut_replacement SQL has COALESCE(ri.is_available, 1) = 1 | MATCH | L242 |

---

### 3.6 P-6: NULL handling with COALESCE — MATCH (6/6)

| Check Item | Status | Evidence |
|------------|--------|----------|
| is_available COALESCE in Floor(mid) | MATCH | category_demand_forecaster L243 |
| is_available COALESCE in Floor(large) | MATCH | large_category_forecaster L405 |
| is_available COALESCE in CUT | MATCH | cut_replacement L242 |
| stock_qty COALESCE in CUT | MATCH | cut_replacement L234 |
| pending_qty COALESCE in CUT | MATCH | cut_replacement L235 |
| SUM COALESCE in site_order_counts | MATCH | auto_order L770 |

---

### 3.7 P-7: No post-override of prior corrections — MATCH (3/3)

| Check Item | Status | Evidence |
|------------|--------|----------|
| promo_floor restoration exists after DiffFeedback | MATCH | L1716-1727 |
| _promo_current stored for restoration | MATCH | L1717 ctx |
| Cap runs after Floor (order invariant) | MATCH | L1048 after L967-1000 |

---

### 3.8 Data Contract: order_list item fields — 14/18 MATCH, 4 GAP

#### v1 GAPs (Fixed ✅)

| Check Item | v1 | v2 | Evidence |
|------------|:--:|:--:|----------|
| CUT items include data_days | GAP | **MATCH** | cut_replacement.py L162: `"data_days": cand.get("sell_days", 0)` |
| CUT items include replaced_mid_cd | GAP | **MATCH** | cut_replacement.py L163: `"replaced_mid_cd": mid_cd` |

#### v2 New Checks (Phase F findings)

**A-1: order_unit_qty field (data_contracts.md Section 1)**

`order_unit_qty` is listed as a required field for all pipeline steps (`predict_single`에서 생성).
Floor/CUT에서 새로 추가하는 품목에는 이 필드가 누락됨.

| Check Item | Status | Evidence |
|------------|--------|----------|
| Floor(mid) items include order_unit_qty | MATCH (v3) | category_demand_forecaster: common DB batch query + new item dict + ceil alignment |
| Floor(large) items include order_unit_qty | MATCH (v3) | large_category_forecaster: same pattern applied |
| CUT items include order_unit_qty | MATCH (v3) | cut_replacement.py: product_details query extended + new item dict + ceil alignment |

**B-1: is_cut_item filter in Floor SQL**

order_flow.md Step 5 specifies: "후보 필터: `is_available=1`, `is_cut_item=0`".
Floor SQL queries do not include `is_cut_item` filter.

| Check Item | Status | Evidence |
|------------|--------|----------|
| Floor(mid) SQL excludes is_cut_item=1 | MATCH (v3) | category_demand_forecaster + large_category_forecaster: `AND COALESCE(ri.is_cut_item, 0) = 0` added |

**Other v2 checks (confirmed OK)**

| Check Item | Status | Evidence |
|------------|--------|----------|
| Floor(mid) items include data_days | MATCH | category_demand_forecaster L143 |
| Floor(mid) items include source | MATCH | category_demand_forecaster L144 |
| Floor(large) items include data_days | MATCH | large_category_forecaster L251 |
| Floor(large) items include source | MATCH | large_category_forecaster L252 |
| CUT items include source | MATCH | cut_replacement.py L164 |
| predict_single passes data_days | MATCH | auto_order.py L699 |
| classify_items uses data_days | MATCH | food_daily_cap.py L268 |
| site_order_counts SUM output | MATCH | auto_order L770 |
| site_order_counts dict type | MATCH | auto_order L779 |
| site_order_counts no NOT IN | MATCH | auto_order SQL |

---

## 4. Differences Found (v2)

### 4.1 GAP: Floor/CUT items missing order_unit_qty (A-1)

| Item | Design Location | Implementation Location | Severity |
|------|-----------------|------------------------|----------|
| order_unit_qty | data_contracts.md Section 1: "전 단계 공통" 필수 필드 | 3 files: Floor(mid), Floor(large), CUT | **Medium** |

**Description**: `data_contracts.md` Section 1에서 `order_unit_qty`는 모든 order_list item의 필수 필드로 정의.
`predict_single()`에서 생성된 아이템은 `auto_order.py` L662에서 `product.get("order_unit_qty", 1)`로 포함.
하지만 Floor(mid/large)와 CUT에서 새로 추가하는 아이템에는 이 필드가 누락.

**Impact**: order_executor.py 5단계 실행 시 `multiplier = (final_order_qty + unit - 1) // unit`에서
`order_unit_qty` 누락 → `1`로 기본값 → 실제 발주 배수(6, 12, 24)와 불일치 → 오발주.
단, 현재 Floor/CUT 추가 품목은 대부분 `final_order_qty=1~2`이므로 실제 영향은 제한적.

**Fix approach**: 3개 파일 각각의 새 아이템 dict에 `order_unit_qty` 추가.
후보 SQL에서 product_details.order_unit_qty를 조회하거나, COALESCE 기본값 1 적용.

```python
# category_demand_forecaster.py, large_category_forecaster.py, cut_replacement.py
"order_unit_qty": cand.get("order_unit_qty", 1),  # ADD THIS
```

---

### 4.2 GAP: Floor SQL missing is_cut_item filter (B-1)

| Item | Design Location | Implementation Location | Severity |
|------|-----------------|------------------------|----------|
| is_cut_item filter | order_flow.md Step 5: "후보 필터: is_cut_item=0" | category_demand_forecaster, large_category_forecaster SQL | **Medium** |

**Description**: `order_flow.md` Step 5에서 Floor 후보 필터 조건에 `is_cut_item=0` 포함.
하지만 실제 SQL에는 `is_available` 필터만 있고 `is_cut_item` 필터가 없음.

**Mitigation**: `auto_order.py`의 `supplement_orders()` 호출 시 `cut_items` 파라미터를 전달하여
Python 레벨에서 CUT 품목을 후보에서 제외하고 있음 (부분적 방어).
하지만 SQL 레벨에서 필터링하는 것이 더 효율적이고 안전함.

**Impact**: CUT 품목이 Floor 보충 후보에 포함될 수 있음.
Python 레벨 필터가 존재하므로 실제 CUT 품목이 발주되지는 않지만,
후보 슬롯을 점유하여 실제 가용 후보 수가 줄어들 수 있음.

**Fix approach**: SQL에 `AND COALESCE(ri.is_cut_item, 0) = 0` 조건 추가.

```sql
AND COALESCE(ri.is_cut_item, 0) = 0   -- CUT 품목 제외
```

---

## 5. Informational Findings (Not Design GAPs)

아래 항목들은 설계 문서에 명시적 요구사항이 없거나, 의도적 설계 결정인 항목.
GAP 아닌 참고 사항으로 분류.

### 5.1 CUT target missing "012" (bread) — A-2

| Severity | Location | Description |
|----------|----------|-------------|
| Low | cut_replacement.py L33 | `target_mid_cds = ["001","002","003","004","005"]` — "012" 미포함 |

`data_contracts.md`에서 CUT 대상 카테고리 목록을 명시하지 않음. 설계 GAP이 아닌 기능 개선 사항.
빵(012)은 `FOOD_CATEGORIES`에 포함되므로 CUT 보충 대상에 추가하는 것이 일관적.

### 5.2 Floor hardcodes current_stock: 0 — B-2

| Severity | Location | Description |
|----------|----------|-------------|
| Low | Floor(mid) L142, Floor(large) L250 | `"current_stock": 0` 하드코딩 |

실제 재고를 SQL에서 조회 가능하지만, Floor 보충의 목적이 "부족분 추가"이므로
보수적으로 stock=0 가정은 합리적. Cap이 최종 게이트로 폐기를 방지함.

### 5.3 Cap buffer site_qty double participation — D-1

| Severity | Location | Description |
|----------|----------|-------------|
| Low | food_daily_cap.py L459-470 | site_qty가 buffer 계산에 포함되고 동시에 차감됨 |

```python
category_total = weekday_avg + site_qty         # buffer 계산에 포함
effective_buffer = int(category_total * 0.20)
total_cap = round(weekday_avg) + effective_buffer
adjusted_cap = max(0, total_cap - site_count)    # 다시 차감
```

site_qty가 2번 영향: (1) buffer 증가에 기여, (2) adjusted_cap에서 차감.
결과적으로 `site_qty × 0.20`만큼 순효과가 남음 (약간의 여유).
설계 문서에서 이 동작을 명시하지 않았으나, 보수적 방향이므로 수정 불요.

### 5.4 Floor/CUT items not aligned to order_unit — C-1

| Severity | Location | Description |
|----------|----------|-------------|
| Medium | 3 files | Floor/CUT 추가 품목이 `_round_to_order_unit()` 미통과 |

`order_flow.md` 순서 불변 조건에서 "Floor/CUT 추가 품목도 배수 정렬 필요" 명시.
하지만 `_round_to_order_unit()`은 `predict_single()` 내부에서만 호출되며,
Floor/CUT 경로는 `auto_order.py` get_recommendations()에서 직접 order_list에 추가됨.

**현재 완화 요인**: order_executor.py L2104-2153에서 `actual_qty = multiplier × order_unit_qty`로
ceiling division 적용되므로, 실행 시점에서 배수 정렬은 보장됨.
다만 order_list 반환 시점의 `final_order_qty`는 정렬되지 않은 값.

---

## 6. Item-by-Item Summary

| # | Check Item | Principle | v1 | v2 |
|---|------------|-----------|:--:|:--:|
| 1 | Cap called after all Floor steps | P-1 | MATCH | MATCH |
| 2 | Cap called after CUT step | P-1 | MATCH | MATCH |
| 3 | No code between Cap and return | P-1 | MATCH | MATCH |
| 4 | Floor(mid) before Cap | P-2 | MATCH | MATCH |
| 5 | Floor(large) before Cap | P-2 | MATCH | MATCH |
| 6 | Floor only increases, Cap only decreases | P-2 | MATCH | MATCH |
| 7 | site_order_counts uses SUM | P-3 | MATCH | MATCH |
| 8 | No NOT IN manual_order_items | P-3 | MATCH | MATCH |
| 9 | Passed to Floor(mid) | P-3 | MATCH | MATCH |
| 10 | Passed to Floor(large) | P-3 | MATCH | MATCH |
| 11 | Passed to Cap | P-3 | MATCH | MATCH |
| 12 | Output is dict[str, int] | P-3 | MATCH | MATCH |
| 13 | Branch A promo_avg guard | P-4 | MATCH | MATCH |
| 14 | Branch A stock check | P-4 | MATCH | MATCH |
| 15 | Branch B promo_avg guard | P-4 | MATCH | MATCH |
| 16 | Branch B stock check | P-4 | MATCH | MATCH |
| 17 | Branch C stock check | P-4 | MATCH | MATCH |
| 18 | Branch C promo_avg implicit guard | P-4 | MATCH | MATCH |
| 19 | Branch D exempt | P-4 | MATCH | MATCH |
| 20 | Floor(mid) is_available filter | P-5 | MATCH | MATCH |
| 21 | Floor(large) is_available filter | P-5 | MATCH | MATCH |
| 22 | CUT is_available filter | P-5 | MATCH | MATCH |
| 23 | is_available COALESCE Floor(mid) | P-6 | MATCH | MATCH |
| 24 | is_available COALESCE Floor(large) | P-6 | MATCH | MATCH |
| 25 | is_available COALESCE CUT | P-6 | MATCH | MATCH |
| 26 | stock_qty COALESCE CUT | P-6 | MATCH | MATCH |
| 27 | pending_qty COALESCE CUT | P-6 | MATCH | MATCH |
| 28 | SUM COALESCE site_order_counts | P-6 | MATCH | MATCH |
| 29 | promo_floor after DiffFeedback | P-7 | MATCH | MATCH |
| 30 | _promo_current stored for restoration | P-7 | MATCH | MATCH |
| 31 | Cap after Floor (order invariant) | P-7 | MATCH | MATCH |
| 32 | Floor(mid) data_days in added items | Data | MATCH | MATCH |
| 33 | Floor(mid) source in added items | Data | MATCH | MATCH |
| 34 | Floor(large) data_days in added items | Data | MATCH | MATCH |
| 35 | Floor(large) source in added items | Data | MATCH | MATCH |
| 36 | CUT data_days in added items | Data | ~~GAP~~ | **MATCH** ✅ |
| 37 | CUT source in added items | Data | MATCH | MATCH |
| 38 | CUT replaced_mid_cd in added items | Data | ~~GAP~~ | **MATCH** ✅ |
| 39 | predict_single passes data_days | Data | MATCH | MATCH |
| 40 | classify_items uses data_days | Data | MATCH | MATCH |
| 41 | site_order_counts SUM output | Data | MATCH | MATCH |
| 42 | site_order_counts dict type | Data | MATCH | MATCH |
| 43 | site_order_counts no NOT IN | Data | MATCH | MATCH |
| 44 | Floor(mid) items include order_unit_qty | Data | NEW | MATCH (v3) |
| 45 | Floor(large) items include order_unit_qty | Data | NEW | MATCH (v3) |
| 46 | CUT items include order_unit_qty | Data | NEW | MATCH (v3) |
| 47 | Floor(mid) SQL excludes is_cut_item=1 | Data | NEW | MATCH (v3) |

**Total: 47 items checked, 43 MATCH, 4 GAP**

---

## 7. Match Rate Calculation

```
Match Rate = 43 / 47 = 91.5%

Breakdown:
  P-1 (Cap final gate):           3/3   = 100%
  P-2 (Floor within Cap):         3/3   = 100%
  P-3 (Manual orders):            6/6   = 100%
  P-4 (Stock check branches):     7/7   = 100%
  P-5 (is_available filter):      3/3   = 100%
  P-6 (NULL COALESCE):            6/6   = 100%
  P-7 (Post-override prevention): 3/3   = 100%
  Data Contracts:                 12/16  =  75%

Overall: 91.5% → PASS (>= 90%)
```

---

## 8. Recommended Actions

### 8.1 Priority 1: order_unit_qty 추가 (3파일 통합 수정)

| # | Action | File | Severity |
|---|--------|------|----------|
| 1 | Floor(mid) 새 아이템에 `order_unit_qty` 추가 | `category_demand_forecaster.py` L135-146 | Medium |
| 2 | Floor(large) 새 아이템에 `order_unit_qty` 추가 | `large_category_forecaster.py` L243-254 | Medium |
| 3 | CUT 새 아이템에 `order_unit_qty` 추가 | `cut_replacement.py` L153-166 | Medium |

**수정 방향**: 후보 SQL에서 `product_details.order_unit_qty` 조회 (배치),
새 아이템 dict에 `"order_unit_qty": cand.get("order_unit_qty", 1)` 추가.

### 8.2 Priority 2: is_cut_item SQL 필터 (2파일)

| # | Action | File | Severity |
|---|--------|------|----------|
| 4 | Floor(mid) SQL에 `is_cut_item` 조건 추가 | `category_demand_forecaster.py` | Medium |
| 5 | Floor(large) SQL에 동일 적용 | `large_category_forecaster.py` | Medium |

**수정 방향**: SQL WHERE 절에 `AND COALESCE(ri.is_cut_item, 0) = 0` 추가.

### 8.3 No Action Needed

All 7 design principles (P-1 through P-7) are fully implemented:
- Execution order (Floor → CUT → Cap) ✅
- Branch A/B/C Fix B stock checks ✅
- site_order_counts SUM + no NOT IN ✅
- is_available filtering + COALESCE ✅
- promo_floor restoration after DiffFeedback ✅

---

## 9. Positive Findings

| Finding | File | Description |
|---------|------|-------------|
| v1 GAP 2건 완전 해결 | cut_replacement.py L162-163 | data_days + replaced_mid_cd 필드 추가 |
| Diagnostic logging | All 3 candidate queries | is_available=0 제외 건수 사전 로깅 |
| Error isolation per step | auto_order.py L968-1059 | Floor/CUT/Cap 각 단계 try/except |
| OrderProposal tracking | improved_predictor.py | Phase 3 stock_gate 3단계 기록 |

---

## 10. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-13 | Initial gap analysis (43 items, 95.3%) | gap-detector |
| 2.0 | 2026-03-13 | v1 GAP 2건 수정 확인 + Phase F 4건 신규 GAP (47 items, 91.5%) | gap-detector v2 |
| 3.0 | 2026-03-13 | v2 GAP 4건 수정 확인 (47 items, 100%) | gap-detector v3 |
| 4.0 | 2026-03-14 | `_finalize_order_unit_qty` design-vs-impl analysis (29 items, 100%) -- total 76 items | gap-detector v4 |

---

## Appendix A: _finalize_order_unit_qty Gap Analysis (v4)

> **Scope**: `_finalize_order_unit_qty` design intent vs implementation
> **Date**: 2026-03-14
> **Files Analyzed**:
>   - `src/order/auto_order.py` L1531-1612 (new method), L39 (import), L1349-1351 (call site)
>   - `src/order/order_executor.py` (removal verification)
>   - `tests/test_multiplier_cap.py` L92-309 (test class rewrite)

### A.1 Design Requirement 1: New function `_finalize_order_unit_qty` -- MATCH (11/11)

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 48 | Function name: `_finalize_order_unit_qty(self, order_list)` | `auto_order.py:1531` with type hints `(self, order_list: List[Dict[str, Any]]) -> None` | Match |
| 49 | Location: `auto_order.py` | `src/order/auto_order.py:1531-1612` (82 lines) | Match |
| 50 | Batch query common.db product_details (500-chunk) | L1556: `CHUNK_SIZE = 500`, L1559: `DBRouter.get_connection(table="product_details")`, L1562-1573: chunk loop with IN clause | Match |
| 51 | Compare ALL items (superset, not just unit=1) | L1590: `if fresh_unit == old_unit: continue` -- every item compared regardless of current unit | Match |
| 52 | Update order_unit_qty in-place | L1602: `item["order_unit_qty"] = fresh_unit` | Match |
| 53 | Do NOT change final_order_qty | No write to `final_order_qty` anywhere in method body | Match |
| 54 | MAX_ORDER_MULTIPLIER cap on multiplier calculation | L1599: `new_mult = min(new_mult, MAX_ORDER_MULTIPLIER)` | Match |
| 55 | MAX_ORDER_MULTIPLIER import | L39: `MAX_ORDER_MULTIPLIER,` from `src.settings.constants` | Match |
| 56 | Insertion point: after `_ensure_clean_screen_state()`, before `execute_orders()` | L1347 clean_screen -> L1349-1351 finalize -> L1357 execute_orders | Match |
| 57 | Empty list guard | L1547-1548: `if not order_list: return` | Match |
| 58 | DB error resilience (no crash) | L1576-1578: `except Exception as e: logger.warning(...); return` | Match |

### A.2 Design Requirement 2: Remove old `_refetch_order_unit_qty` -- MATCH (3/3)

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 59 | Remove method body (~50 lines) from `order_executor.py` | `grep _refetch_order_unit_qty order_executor.py` returns **no matches** | Match |
| 60 | Remove call site (2 lines) from `execute_orders()` | No references to `_refetch` in `order_executor.py` | Match |
| 61 | No residual references in src/ code | Only docstring/changelog references remain (documentation-only) | Match |

### A.3 Design Requirement 3: Test migration -- MATCH (15/15)

| # | Design Item | Implementation | Status |
|---|-------------|----------------|--------|
| 62 | Class rename: `TestRefetchOrderUnitQty` -> `TestFinalizeOrderUnitQty` | L92: `class TestFinalizeOrderUnitQty(unittest.TestCase)` | Match |
| 63 | Test count: 9 -> 14 tests | 14 test methods in `TestFinalizeOrderUnitQty` class | Match |
| 64 | Test: corrects unit=1 | `test_corrects_unit1` (L126) | Match |
| 65 | Test: no change when matching | `test_no_change_when_unit_matches` (L142) | Match |
| 66 | Test: corrects small qty (<=5) | `test_corrects_even_small_qty` (L154) | Match |
| 67 | Test: DB unit=1 unchanged | `test_db_unit1_no_change` (L166) | Match |
| 68 | Test: no DB record | `test_db_no_record_no_change` (L178) | Match |
| 69 | Test: DB error resilience | `test_db_error_no_crash` (L190) | Match |
| 70 | Test: multiple items mixed | `test_multiple_items_mixed` (L202) | Match |
| 71 | Test: AUDIT calc consistency | `test_audit_matches_calc_multiplier` (L269) | Match |
| 72 | Test: superset (unit>1 mismatch) -- NEW | `test_superset_corrects_nonunit1` (L220) | Match |
| 73 | Test: empty list -- NEW | `test_empty_list_no_crash` (L232) | Match |
| 74 | Test: batch chunks (500+) -- NEW | `test_batch_query_uses_chunks` (L252) | Match |
| 75 | Test: final_order_qty unchanged -- NEW | `test_final_order_qty_unchanged` (L239) | Match |
| 76 | Test: qty=0 handling -- NEW | `test_qty_zero_no_crash` (L298) | Match |

### A.4 Positive Additions (not in design, beneficial)

| # | Item | Location | Description |
|---|------|----------|-------------|
| A-1 | Comprehensive docstring | `auto_order.py:1532-1543` | Detailed rationale + modification history beyond function name |
| A-2 | Extra empty `item_codes` guard | `auto_order.py:1552-1553` | Defense-in-depth: `if not item_codes: return` after filtering empty item_cd values |
| A-3 | DB NULL handling | `auto_order.py:1572` | `int(row[1] or 1) if row[1] else 1` handles NULL/None from product_details |

### A.5 Cosmetic Gap

| # | Item | Design | Implementation | Impact |
|---|------|--------|----------------|--------|
| C-1 | Total test count label | "22 multiplier cap tests" | 23 test methods (7+2+14) in file | Low -- finalize class count of 14 matches exactly; file-level tally off by 1 |

### A.6 Finalize Analysis Score

```
Design Match:      29/29 items = 100%
  Requirement 1 (new function):    11/11
  Requirement 2 (remove old):       3/3
  Requirement 3 (test migration):  15/15

Positive Additions: 3 (all beneficial)
Cosmetic Gaps:      1 (test count label)
Architecture:       All import/layer checks pass
Convention:         All naming/logging checks pass

Verdict: PASS (100%)
```

### A.7 Test Results

| Metric | Value | Status |
|--------|-------|--------|
| Total tests passing | 3,692 | Good |
| Pre-existing failures | 12 | Unchanged |
| New failures introduced | 0 | Good |
| Multiplier cap file (23 tests) | 23/23 pass | Good |
| `TestFinalizeOrderUnitQty` (14 tests) | 14/14 pass | Good |
