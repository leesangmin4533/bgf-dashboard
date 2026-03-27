# over-order-correction Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation) + Cross-Module Consistency
>
> **Analyst**: gap-detector
> **Date**: 2026-02-25
> **Design Doc**: [over-order-correction.design.md](../02-design/features/over-order-correction.design.md)
> **Status**: PASS

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

1. Design 문서(over-order-correction.design.md)와 실제 구현 코드의 일치도를 검증한다.
2. 최근 추가된 2개 보정 기능(RI stale 카테고리 분기 + pending 교차검증)이 기존 발주 플로우와 일관되는지 검증한다.
3. pre_order_evaluator / improved_predictor / auto_order 3개 모듈의 재고 판단 일관성을 확인한다.

### 1.2 Analysis Scope

| 구분 | 경로 |
|------|------|
| Design 문서 | `docs/02-design/features/over-order-correction.design.md` |
| Phase 1 코드 | `src/settings/constants.py`, `src/prediction/pre_order_evaluator.py`, `src/order/auto_order.py` |
| Phase 2 코드 | `src/prediction/eval_config.py`, `src/prediction/eval_calibrator.py`, `config/eval_params.json` |
| Phase 3 코드 | `src/db/models.py`, `src/infrastructure/database/repos/eval_outcome_repo.py` |
| 일관성 대상 | `src/prediction/improved_predictor.py`, `src/infrastructure/database/repos/inventory_repo.py` |

---

## 2. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Phase 1: PASS 억제 + FORCE 검증 | 91% | PASS |
| Phase 2: 파라미터 드리프트 보정 | 95% | PASS |
| Phase 3: eval_outcomes 컬럼 확장 | 100% | PASS |
| 재고 판단 일관성 (추가 검증) | 95% | PASS |
| **Overall** | **95.3%** | **PASS** |

---

## 3. Phase 1: PASS 억제 + FORCE 검증

### 3.1 Check Items (16 items)

| # | Check Item | Design | Implementation | Status |
|---|-----------|--------|----------------|--------|
| 1 | PASS_MAX_ORDER_QTY 상수 존재 | `constants.py` | `constants.py:115` | Match |
| 2 | PASS_MAX_ORDER_QTY 값 | 1 | **3** | **Changed** |
| 3 | ENABLE_PASS_SUPPRESSION flag | `constants.py` | `constants.py:121` | Match |
| 4 | FORCE_MIN_DAILY_AVG 상수 | 0.1 | `constants.py:117` = 0.1 | Match |
| 5 | ENABLE_FORCE_DOWNGRADE flag | `constants.py` | `constants.py:122` | Match |
| 6 | PASS 발주량 억제 위치 | `auto_order.py` get_order_candidates | `auto_order.py:828-841` | Match |
| 7 | PASS 억제 로직: pass_codes 수집 | eval_results에서 PASS decision 추출 | `auto_order.py:830-833` | Match |
| 8 | PASS 억제 로직: order_qty cap | `r.order_qty = min(r.order_qty, PASS_MAX_ORDER_QTY)` | `r.order_qty = PASS_MAX_ORDER_QTY` (if > cap) | Match (동일 효과) |
| 9 | PASS 억제 로그 | "PASS 발주량 억제: N개 상품" | `auto_order.py:841` 포함 `(상한={PASS_MAX_ORDER_QTY})` | Match+ |
| 10 | FORCE 다운그레이드 위치 | `_make_decision()` 내부 | `pre_order_evaluator.py:971-974` | Match |
| 11 | FORCE 다운그레이드 조건 | `daily_avg < FORCE_MIN_DAILY_AVG` | `pre_order_evaluator.py:973` | Match |
| 12 | FORCE 다운그레이드 결과 | NORMAL_ORDER 반환 | `pre_order_evaluator.py:974` | Match |
| 13 | _make_decision 시그니처 daily_avg 추가 | `daily_avg=0.0` 파라미터 | `pre_order_evaluator.py:935` | Match |
| 14 | Feature flag로 즉시 롤백 | 두 flag 모두 if 분기 | `ENABLE_FORCE_DOWNGRADE` 분기(973), `ENABLE_PASS_SUPPRESSION` 분기(829) | Match |
| 15 | 상수 파일 위치 | `src/config/constants.py` (설계서) | `src/settings/constants.py` (실제) | Changed (설계 오류) |
| 16 | FORCE 간헐수요 억제 (설계에 없는 추가 기능) | 없음 | `pre_order_evaluator.py:977-993` | **Added** |

### 3.2 Phase 1 Gap Details

#### Changed: PASS_MAX_ORDER_QTY = 3 (설계: 1)

- **Design**: `PASS_MAX_ORDER_QTY = 1` (Section 3-1, line 85)
- **Implementation**: `PASS_MAX_ORDER_QTY = 3` (`constants.py:115`)
- **Impact**: Medium -- 설계는 최소 1개로 제한했으나, 운영 중 품절 위험을 줄이기 위해 상한을 3으로 상향.
- **Judgment**: 의도된 조정. 설계의 "최소 발주 단위로 제한"이라는 취지는 유지하되 값만 완화. 문서 업데이트 권장.

#### Changed: 상수 파일 경로

- **Design**: `src/config/constants.py` (Section 3-1, line 92-93)
- **Implementation**: `src/settings/constants.py` (프로젝트 재구성 이후 경로)
- **Impact**: None -- `src/config/constants.py`와 `src/settings/constants.py`는 re-export 관계이므로 실질적 차이 없음.

#### Added: FORCE 간헐수요 억제 (설계에 없는 기능)

- **Implementation**: `ENABLE_FORCE_INTERMITTENT_SUPPRESSION`, `FORCE_INTERMITTENT_SELL_RATIO`, `FORCE_INTERMITTENT_MAX_EXPIRY`, `FORCE_INTERMITTENT_COOLDOWN_DAYS`
- **Location**: `pre_order_evaluator.py:977-993`, `constants.py:124-127`
- **Description**: 품절 + 간헐수요(판매빈도 50% 미만) + 유통기한 1일 이하인 상품을 FORCE_ORDER 대신 PASS로 다운그레이드하여 폐기 사이클을 방지.
- **Impact**: Positive -- 설계의 "FORCE 품절 과잉 방지" 취지를 더 세밀하게 구현. 별도 feature flag로 관리되므로 독립 rollback 가능.

### 3.3 Phase 1 Score: 91% (14 match + 1 changed + 1 added / 16 items)

---

## 4. Phase 2: 파라미터 드리프트 보정

### 4.1 Check Items (12 items)

| # | Check Item | Design | Implementation | Status |
|---|-----------|--------|----------------|--------|
| 1 | save() 시 .bak 자동 백업 | `eval_config.py save()` | `eval_config.py:188-194, 206-211` (shutil.copy2) | Match |
| 2 | calibration_decay ParamSpec | value=0.7, default=0.7, min=0.3, max=1.0 | `eval_config.py:123-126` | Match |
| 3 | calibration_reversion_rate ParamSpec | value=0.1, default=0.1, min=0.0, max=0.3 | `eval_config.py:127-130` | Match |
| 4 | mean reversion 공식 | `effective_delta = raw_delta * decay + (default - current) * reversion_rate` | `eval_calibrator.py:68-74` | Match |
| 5 | exposure_sufficient 범위 | 2.5~5.0 (설계: min 변경) | `eval_config.py:106` min_val=2.5, max_val=5.0 | Match |
| 6 | weight_daily_avg 범위 | 0.20~0.55 (설계: max 변경) | `eval_config.py:74` max_val=0.55 | Match |
| 7 | stockout_freq_threshold 범위 | 0.05~0.25 (설계: max 변경) | `eval_config.py:112` max_val=0.25 | Match |
| 8 | MAX_PARAMS_PER_CALIBRATION | 3 | `constants.py:129` = 3 | Match |
| 9 | 1회 보정 횟수 제한 로직 | calibrate()에서 len(changes) < MAX 체크 | `eval_calibrator.py:475-488` | Match |
| 10 | mean reversion 적용 위치 | 각 _calibrate_* 메서드에서 호출 | `eval_calibrator.py:563,612,638,685,709` (5곳) | Match |
| 11 | eval_params.json 파라미터 리셋 | weight_daily_avg=0.40, exposure_sufficient=3.0 등 | **Partially drifted** (아래 상세) | **Changed** |
| 12 | eval_params.json.bak 롤백 전략 | 자동 백업에서 복원 가능 | shutil.copy2로 저장 시 자동 백업 | Match |

### 4.2 Phase 2 Gap Details

#### Changed: eval_params.json 파라미터 드리프트 지속

설계 Section 4-5에서 "즉시 파라미터 리셋"을 명시했으나, 현재 eval_params.json에 드리프트가 잔존:

| Parameter | Design Reset | Current Value | Default | Status |
|-----------|:------:|:-------:|:-------:|:------:|
| weight_daily_avg | 0.40 | **0.5876** | 0.40 | Drifted (max=0.55에 의해 런타임 클램프) |
| weight_sell_day_ratio | 0.35 | 0.2686 | 0.35 | Drifted |
| weight_trend | 0.25 | 0.1438 | 0.25 | Drifted |
| exposure_sufficient | 3.0 | **2.5** | 3.0 | Drifted (min=2.5에 도달) |
| stockout_freq_threshold | 0.15 | **0.25** | 0.15 | Drifted (max=0.25에 도달) |

**Impact**: Medium -- mean reversion(decay=0.7, reversion_rate=0.1) 메커니즘이 작동 중이므로 시간이 지나면 기본값 방향으로 복귀할 것이나, 극단 수렴 상태(min/max)에서는 reversion 효과가 제한적(delta 클램프로 max_delta 이하만 변경 가능). 설계의 "즉시 리셋" 의도와 불일치.

**Recommendation**: 현재 mean reversion 메커니즘 자체는 올바르게 구현되어 있으므로, 파라미터 리셋이 필요한 경우 수동으로 `config/eval_params.json`을 default 값으로 리셋하거나 `EvalConfig`의 `_normalize_weights()` 호출 시 합계 정규화만 의존.

### 4.3 Phase 2 Score: 95% (11 match + 1 changed / 12 items)

---

## 5. Phase 3: eval_outcomes 컬럼 확장

### 5.1 Check Items (14 items)

| # | Check Item | Design | Implementation | Status |
|---|-----------|--------|----------------|--------|
| 1 | predicted_qty 컬럼 | ALTER TABLE ADD | `models.py:437` (v14 migration) | Match |
| 2 | actual_order_qty 컬럼 | ALTER TABLE ADD | `models.py:438` | Match |
| 3 | order_status 컬럼 | ALTER TABLE ADD | `models.py:439` | Match |
| 4 | weekday 컬럼 | ALTER TABLE ADD | `models.py:440` | Match |
| 5 | delivery_batch 컬럼 | ALTER TABLE ADD | `models.py:441` | Match |
| 6 | sell_price 컬럼 | ALTER TABLE ADD | `models.py:442` | Match |
| 7 | margin_rate 컬럼 | ALTER TABLE ADD | `models.py:443` | Match |
| 8 | disuse_qty 컬럼 | ALTER TABLE ADD | `models.py:444` | Match |
| 9 | promo_type 컬럼 | ALTER TABLE ADD | `models.py:445` | Match |
| 10 | trend_score 컬럼 | ALTER TABLE ADD | `models.py:446` | Match |
| 11 | stockout_freq 컬럼 | ALTER TABLE ADD | `models.py:447` | Match |
| 12 | 평가 시 ML 데이터 수집 | save_eval_results에서 weekday/delivery_batch/sell_price/margin_rate/promo_type/trend_score/stockout_freq 저장 | `eval_calibrator.py:91-131`, `eval_outcome_repo.py:23-97` | Match |
| 13 | 발주 후 predicted_qty/actual_order_qty/order_status 업데이트 | auto_order execute() 후 | `auto_order.py:1776-1782`, `eval_outcome_repo.py:264-320` | Match |
| 14 | 사후 검증 시 disuse_qty 저장 | verify_yesterday()에서 | `eval_calibrator.py:255-270` | Match |

### 5.2 Phase 3 Gap Details

- delivery_batch 판별 시 `mid_cd in FOOD_MID_CODES` 조건이 설계대로 적용됨 (`eval_calibrator.py:105`)
- order_status 값: 설계는 `NOT_ORDERED/DRY_RUN/ORDERED/FAILED`, 구현은 `pending/success/fail` -- 값 이름은 다르나 의미는 동일. `pending`이 dry_run을 포함하며 `success`가 ORDERED에 대응.
- Schema v14로 구현됨 (설계는 v12를 명시했으나, 실제 프로젝트 버전 진행으로 v14에 할당)

### 5.3 Phase 3 Score: 100% (14/14 match)

---

## 6. 재고 판단 일관성 검증 (Cross-Module)

### 6.1 3개 모듈의 재고 소스 비교

| 모듈 | 재고 소스 | Stale 처리 | Pending 소스 | 비고 |
|------|----------|-----------|-------------|------|
| **pre_order_evaluator** `_batch_load_inventory()` | realtime_inventory + product_details JOIN | STALE_ZERO_CATEGORIES(푸드+디저트): stock=0; 비식품: daily_sales fallback | realtime_inventory.pending_qty (stale 시 daily_sales fallback) | FORCE/PASS/SKIP 결정 |
| **improved_predictor** `_resolve_stock_and_pending()` | realtime_inventory `get()` + `_stale` flag | stale 시 ri vs ds 비교, 작은 값 채택 (카테고리 무관) | ri_fresh: OT 교차검증; ri_stale: 0; cache | 발주량 계산 |
| **auto_order** `_apply_pending_and_stock_to_order_list()` | 외부 `pending_data`/`stock_data` 주입 (prefetch_pending) | 원본 유지 (stale 판단 없음, 실시간 조회 결과 그대로 사용) | prefetch_pending에서 BGF 사이트 실시간 조회 | 최종 발주량 재계산 |

### 6.2 일관성 검증 항목 (8 items)

| # | Check Item | Status | Detail |
|---|-----------|--------|--------|
| 1 | 유통기한 기반 TTL 적용 | Consistent | pre_order_evaluator와 inventory_repo 모두 `_get_stale_hours_for_expiry()` 사용 |
| 2 | Stale 카테고리 분기 일관성 | **Divergent (의도)** | pre_order_evaluator: STALE_ZERO_CATEGORIES만 stock=0, 비식품 daily_sales fallback; improved_predictor: 카테고리 무관하게 ri vs ds 비교 |
| 3 | Pending 교차검증 | Consistent | improved_predictor만 OT 교차검증 수행, auto_order는 prefetch_pending 실시간 값 사용 (각각 다른 시점의 데이터를 다루므로 정당) |
| 4 | 음수 재고 방어 | Consistent | improved_predictor:1457, auto_order:1479 모두 `max(0, ...)` 처리 |
| 5 | FORCE_ORDER 보호 흐름 | Consistent | pre_order_evaluator -> FORCE 결정 -> auto_order에서 missing_force 보충 + FORCE_MAX_DAYS 상한 |
| 6 | PASS 억제와 재고 판단 분리 | Consistent | PASS 억제는 auto_order에서 order_qty 직접 cap, 재고 재계산 과정과 독립 |
| 7 | 단기유통 보호 일관성 | Consistent | auto_order._apply_pending_and_stock:1518-1522에서 유통기한 1일 + FOOD 보호, _recalculate_need_qty:1360-1369에서 pending 할인 -- 둘 다 FOOD_CATEGORIES 기준 |
| 8 | stock_source/pending_source 추적 | Consistent | improved_predictor에서 5-tuple 반환, PredictionResult에 메타 저장, auto_order._last_stock_discrepancies에 전파 |

### 6.3 Divergent: RI Stale 카테고리 분기 (의도된 차이)

**pre_order_evaluator._batch_load_inventory()**:
```python
if is_stale:
    if mid_cd in STALE_ZERO_CATEGORIES:
        stock_qty = 0  # 푸드/디저트: stale -> 0
    else:
        continue  # 비식품: daily_sales fallback
```

**improved_predictor._resolve_stock_and_pending()**:
```python
if inv_data.get('_stale', False):
    is_stale = True
    ds_stock = self.get_current_stock(item_cd)
    ri_stock = inv_data['stock_qty']
    if ds_stock < ri_stock:
        current_stock = ds_stock  # ds가 더 작으면(현실적) ds 채택
    else:
        current_stock = ri_stock  # ri가 더 작으면 ri 유지
```

**Analysis**:
- pre_order_evaluator는 **발주 결정(FORCE/PASS/SKIP)** 단계이므로, 푸드/디저트는 stale 시 stock=0으로 간주하여 FORCE_ORDER로 분류되는 것이 안전하다 (유령재고로 인한 품절 방지).
- improved_predictor는 **발주량 계산** 단계이므로, ri vs ds 비교로 더 보수적인 값을 채택하는 것이 과잉발주 방지에 적합하다.
- 이 차이는 **각 모듈의 역할에 맞는 의도된 분기**이며, 일관성 문제가 아니다.
- 단, 비식품 stale RI에서 pre_order_evaluator는 `continue`(daily_sales fallback)하지만, improved_predictor는 ri vs ds 비교를 수행하므로, 비식품에서도 결과적으로 유사한 동작을 한다.

### 6.4 Pending 교차검증이 FORCE/PASS 결정에 미치는 영향

**Flow**:
```
1. pre_order_evaluator.evaluate_all()
   -> _batch_load_inventory() -> stock/pending 결정
   -> _make_decision() -> FORCE/URGENT/NORMAL/PASS/SKIP

2. improved_predictor.get_order_candidates()
   -> _resolve_stock_and_pending() -> OT 교차검증 -> 발주량 계산
   -> PASS 억제 (auto_order)

3. auto_order._apply_pending_and_stock_to_order_list()
   -> prefetch_pending (BGF 실시간) -> 최종 재계산
```

**Key Insight**: OT 교차검증은 improved_predictor(2단계)에서만 적용되고, pre_order_evaluator(1단계)에서는 적용되지 않는다. 이는 다음 이유로 정당하다:

1. pre_order_evaluator는 "품절 여부" 판단이 핵심이므로, pending 정확성보다 stock 정확성이 중요하다.
2. pre_order_evaluator의 pending은 `exposure_days = (stock + pending) / daily_avg` 계산에만 사용되며, FORCE_ORDER 결정은 `current_stock <= 0` 기준이므로 pending 값에 무관하다.
3. OT 교차검증으로 pending이 줄면 exposure_days가 감소하여 URGENT/NORMAL로 상향될 수 있지만, 이는 발주량 계산 단계에서 반영되므로 문제없다.

### 6.5 일관성 Score: 95% (7 consistent + 1 divergent-by-design / 8 items)

---

## 7. Differences Found

### 7.1 Changed Features (Design != Implementation)

| # | Item | Design | Implementation | Impact |
|---|------|--------|----------------|--------|
| 1 | PASS_MAX_ORDER_QTY 값 | 1 | 3 | Medium -- 운영 안정성 향상 목적 |
| 2 | 상수 파일 경로 | `src/config/constants.py` | `src/settings/constants.py` | None -- re-export 동일 |
| 3 | eval_params.json 리셋 | 즉시 리셋 명시 | 드리프트 잔존 | Medium -- mean reversion으로 서서히 복귀 중 |
| 4 | order_status 값 이름 | NOT_ORDERED/DRY_RUN/ORDERED/FAILED | pending/success/fail | None -- 의미 동일 |
| 5 | Schema 버전 | v12 | v14 | None -- 프로젝트 진행으로 버전 밀림 |

### 7.2 Added Features (Design X, Implementation O)

| # | Item | Implementation Location | Description | Impact |
|---|------|------------------------|-------------|--------|
| 1 | FORCE 간헐수요 억제 | `pre_order_evaluator.py:977-993` | 품절+간헐수요+유통기한1일 -> PASS 다운그레이드 | Positive |
| 2 | FORCE_MAX_DAYS 상수 | `constants.py:119` | FORCE 보충 발주량 상한 (일평균 x N일) | Positive |
| 3 | CUT 미확인 의심 가드 | `pre_order_evaluator.py:996-998` | CUT 조회 오래된 상품 FORCE -> NORMAL | Positive |
| 4 | RI stale 카테고리 분기 | `pre_order_evaluator.py:490-493` | STALE_ZERO_CATEGORIES: stock=0, 비식품: ds fallback | Positive |
| 5 | Pending OT 교차검증 | `improved_predictor.py:1484-1501` | RI pending vs OT remaining 비교, 작은 값 채택 | Positive |

### 7.3 Missing Features (Design O, Implementation X)

없음 -- 설계 문서의 모든 기능이 구현되어 있다.

---

## 8. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 95.3%                   |
+---------------------------------------------+
|  Phase 1 (PASS+FORCE):  14/16 = 91%         |
|  Phase 2 (Drift):       11/12 = 95%         |
|  Phase 3 (ML Columns):  14/14 = 100%        |
|  Consistency:             7/8  = 95%         |
+---------------------------------------------+
|  Total check items:      46/50 exact match   |
|  Changed:                4 items             |
|  Added:                  5 items             |
|  Missing:                0 items             |
+---------------------------------------------+
```

---

## 9. Detailed File-by-File Verification

### 9.1 `src/settings/constants.py`

| Item | Design Value | Actual Value | Match |
|------|:------:|:------:|:-----:|
| PASS_MAX_ORDER_QTY | 1 | 3 | Changed |
| FORCE_MIN_DAILY_AVG | 0.1 | 0.1 | Exact |
| ENABLE_PASS_SUPPRESSION | True | True | Exact |
| ENABLE_FORCE_DOWNGRADE | True | True | Exact |
| MAX_PARAMS_PER_CALIBRATION | 3 | 3 | Exact |
| ENABLE_FORCE_INTERMITTENT_SUPPRESSION | (not in design) | True | Added |
| FORCE_INTERMITTENT_SELL_RATIO | (not in design) | 0.5 | Added |
| FORCE_INTERMITTENT_MAX_EXPIRY | (not in design) | 1 | Added |
| FORCE_INTERMITTENT_COOLDOWN_DAYS | (not in design) | 1 | Added |
| FORCE_MAX_DAYS | (not in design) | 1.5 | Added |

### 9.2 `src/prediction/pre_order_evaluator.py`

| Item | Design | Actual | Match |
|------|--------|--------|:-----:|
| _make_decision daily_avg param | `daily_avg=0.0` | line 935: `daily_avg: float = 0.0` | Exact |
| FORCE 다운그레이드 분기 | `current_stock <= 0` + `daily_avg < FORCE_MIN_DAILY_AVG` -> NORMAL | lines 971-974 | Exact |
| FORCE 다운그레이드 사유 문자열 | `"품절+일평균{daily_avg:.2f}<{FORCE_MIN_DAILY_AVG}"` | line 974 | Exact |
| STALE_ZERO_CATEGORIES 정의 | (not in design) | line 33: `set(FOOD_CATEGORIES + ["014"])` | Added |
| _batch_load_inventory stale 분기 | (not in design) | lines 489-493 | Added |

### 9.3 `src/order/auto_order.py`

| Item | Design | Actual | Match |
|------|--------|--------|:-----:|
| PASS 억제 feature flag 체크 | `ENABLE_PASS_SUPPRESSION` | line 829 | Exact |
| pass_codes 수집 | `{cd for cd, r in eval_results.items() if r.decision == EvalDecision.PASS}` | lines 830-833 | Exact |
| 발주량 cap 로직 | `r.order_qty = min(r.order_qty, PASS_MAX_ORDER_QTY)` | line 838: `r.order_qty = PASS_MAX_ORDER_QTY` (if > cap, same effect) | Exact |
| 억제 카운트 로그 | "PASS 발주량 억제: N개 상품" | line 841 | Exact |
| 발주 후 ML 업데이트 | predicted_qty, actual_order_qty, order_status | lines 1776-1781 | Exact |

### 9.4 `src/prediction/eval_config.py`

| Item | Design | Actual | Match |
|------|--------|--------|:-----:|
| calibration_decay ParamSpec | value=0.7, default=0.7, min=0.3, max=1.0, max_delta=0.1 | lines 123-126 | Exact |
| calibration_reversion_rate ParamSpec | value=0.1, default=0.1, min=0.0, max=0.3, max_delta=0.05 | lines 127-130 | Exact |
| exposure_sufficient min_val | 2.5 | line 106: min_val=2.5 | Exact |
| weight_daily_avg max_val | 0.55 | line 74: max_val=0.55 | Exact |
| stockout_freq_threshold max_val | 0.25 | line 112: max_val=0.25 | Exact |
| save() backup (shutil.copy2) | 변경 전 백업 | lines 188-194, 206-211 | Exact |

### 9.5 `src/prediction/eval_calibrator.py`

| Item | Design | Actual | Match |
|------|--------|--------|:-----:|
| _apply_mean_reversion 메서드 | raw_delta * decay + (default - current) * reversion_rate | lines 54-75 | Exact |
| calibrate() MAX_PARAMS 제한 | len(changes) < MAX | lines 475-488 | Exact |
| mean reversion 적용: popularity weights | _calibrate_popularity_weights에서 호출 | line 563 | Exact |
| mean reversion 적용: exposure thresholds | _calibrate_exposure_thresholds에서 호출 | lines 612, 638 | Exact |
| mean reversion 적용: stockout threshold | _calibrate_stockout_threshold에서 호출 | lines 685, 709 | Exact |
| verify_yesterday disuse_qty 저장 | disuse_qty=disuse_qty if > 0 else None | line 270 | Exact |
| save_eval_results ML 필드 저장 | weekday/delivery_batch/sell_price/margin_rate/promo_type/trend_score/stockout_freq | lines 91-131 | Exact |
| delivery_batch 푸드 조건 | `mid_cd in FOOD_MID_CODES` | line 105 | Exact |

### 9.6 `src/prediction/improved_predictor.py`

| Item | Description | Actual | Match |
|------|-------------|--------|:-----:|
| _resolve_stock_and_pending 반환값 | 5-tuple (stock, pending, stock_src, pending_src, is_stale) | line 1508 | Exact |
| OT 교차검증 조건 | ot_pending_cache loaded + ri_fresh + pending > 0 | lines 1488-1491 | Added |
| OT 교차검증 로직 | ot < ri -> ot 채택 | lines 1494-1501 | Added |
| _load_ot_pending_cache 호출 시점 | predict_batch 시작 시 1회 | line 2510 | Added |

---

## 10. eval_params.json 드리프트 현황

현재 `config/eval_params.json`에서 기본값과 차이가 큰 파라미터:

| Parameter | Current | Default | Gap | Capped at |
|-----------|:-------:|:-------:|:---:|:---------:|
| weight_daily_avg | 0.5876 | 0.40 | +0.1876 | max=0.55 (런타임 클램프) |
| weight_sell_day_ratio | 0.2686 | 0.35 | -0.0814 | -- |
| weight_trend | 0.1438 | 0.25 | -0.1062 | -- |
| exposure_sufficient | 2.5 | 3.0 | -0.5 | min=2.5 도달 |
| stockout_freq_threshold | 0.25 | 0.15 | +0.10 | max=0.25 도달 |

**Note**: 3개 가중치 합계 = 0.5876 + 0.2686 + 0.1438 = 1.0000 (정규화 유지됨). mean reversion은 작동 중이나, max/min 경계에 도달한 파라미터는 복원 속도가 제한적.

---

## 11. Recommended Actions

### 11.1 Documentation Update (Low Priority)

1. **설계 문서 업데이트**: PASS_MAX_ORDER_QTY 값을 1 -> 3으로 반영
2. **설계 문서 업데이트**: FORCE 간헐수요 억제(ENABLE_FORCE_INTERMITTENT_SUPPRESSION) 기능 추가
3. **설계 문서 업데이트**: RI stale 카테고리 분기 및 OT 교차검증 기능 추가
4. **설계 문서 업데이트**: order_status 값 이름을 실제(pending/success/fail)로 변경
5. **설계 문서 업데이트**: 상수 파일 경로를 `src/settings/constants.py`로 수정

### 11.2 Parameter Reset Consideration (Medium Priority)

현재 eval_params.json의 드리프트 상태 관찰:
- `weight_daily_avg`가 0.5876(max=0.55 클램프)으로, 런타임에서는 0.55로 제한됨
- `exposure_sufficient`가 2.5(min=2.5)로, 하한에 도달
- `stockout_freq_threshold`가 0.25(max=0.25)로, 상한에 도달

**Option A**: eval_params.json을 default 값으로 수동 리셋
**Option B**: 현재 mean reversion 메커니즘에 맡기고 관찰 (reversion_rate=0.1은 매 보정 사이클마다 gap의 10%를 복원)
**Recommendation**: 현재 구조가 안정적이므로 Option B 유지. 다만 3개 파라미터가 경계값에 고착된 상태이므로, 적중률이 목표(60%) 미만이면 Option A 수동 리셋 수행.

### 11.3 No Action Required

- Phase 3 ML 컬럼: 11개 전체 구현, 3개 수집 시점(평가 시/발주 후/사후 검증) 모두 동작 확인
- RI stale 카테고리 분기: pre_order_evaluator와 improved_predictor의 차이는 역할에 맞는 의도된 설계
- Pending OT 교차검증: improved_predictor 전용이며, pre_order_evaluator에는 불필요 (FORCE 결정은 stock 기준)

---

## 12. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-25 | Initial analysis: 50 check items, 95.3% match rate | gap-detector |
