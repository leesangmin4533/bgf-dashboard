# 발주 파이프라인 설계 문서

> 작성일: 2026-03-08 | 기준 코드: bgf_auto/src/

---

## 1. 파이프라인 5단계

### 1단계: 관측값 수집

| 항목 | 내용 |
|------|------|
| **담당 파일/함수** | `collectors/order_prep_collector.py` → `collect_for_item()` (재고/미입고/행사) |
| | `collectors/receiving_collector.py` → `collect_all()` (입고 이력, Phase 1.1) |
| | `collectors/order_status_collector.py` → `collect_all_order_unit_qty()` (발주단위, 00:00 독립) |
| | `collectors/promotion_collector.py` → `collect_all_promotions()` (행사 캐시, Phase 1.2) |
| **입력값** | BGF 넥사크로 화면: STBJ030 (NOW_QTY, EXPECT_QTY, MONTH_EVT, NEXT_MONTH_EVT), STGJ010 (입고), STBJ070 (발주단위) |
| **출력값** | `realtime_inventory` (stock_qty, pending_qty), `receiving_history`, `promotions`, `product_details.order_unit_qty` |
| **충돌 위험** | Phase 1.2 행사 캐시 vs Phase 2 실시간 조회 이중 경로. Phase 2가 덮어쓰므로 실질적 위험은 낮음. **C-04 버그**: store_id 미전달로 행사 저장 실패 (수정완료) |

### 2단계: 예측

| 항목 | 내용 |
|------|------|
| **담당 파일/함수** | `prediction/improved_predictor.py` → `predict_single()` → `_compute_safety_and_order()` |
| | `prediction/base_predictor.py` → WMA(7일) 기본 예측 |
| | `prediction/categories/*.py` → 카테고리별 Strategy (food, beer, tobacco 등) |
| **입력값** | daily_sales(판매이력), realtime_inventory(재고), product_details(상품정보), external_factors(날씨/기온), promotions(행사) |
| **출력값** | `base_prediction`, `adjusted_prediction`, `safety_stock`, `lead_time_demand` |
| **충돌 위험** | 수요패턴 분류(DemandClassifier)에 따라 예측 경로가 갈림 (WMA vs Croston/TSB). 면제 카테고리(food/dessert)와 비면제 카테고리의 계수 적용 방식이 다름 (곱셈 vs 덧셈) |

### 3단계: 정책 보정

| 항목 | 내용 |
|------|------|
| **담당 파일/함수** | `prediction/improved_predictor.py` 내부 순차 호출: |
| | `_apply_order_rules()` → float→int 변환, 과재고 방지 |
| | `_apply_promotion_adjustment()` → 행사 배수 보정 (1+1→최소2, 2+1→최소3) |
| | `_apply_ml_ensemble()` → ML 적응형 블렌딩 (MAE 기반 가중치 0.1~0.5) |
| | DiffFeedback → 제거 이력 패널티 |
| | SubstitutionDetector → 잠식 감지 보정 |
| | CategoryDemandForecaster → mid_cd 총량 배분 |
| | LargeCategoryForecaster → large_cd 총량 배분 |
| | `_round_to_order_unit()` → 발주배수 정렬 (최종) |
| **입력값** | `need_qty` (2단계 산출), 행사정보, ML 예측값, 피드백 이력 |
| **출력값** | `order_qty` (정수, 발주단위 정렬 완료) |
| **충돌 위험** | **핵심 위험**: `order_qty`가 7회 이상 재할당됨 (order_rules→promo→ML→boost→penalty→sub→cap→round). 각 단계가 이전 결과를 덮어쓰는 구조. 0→양수 변환 가능 지점 존재 (promo 최소배수, new_product_boost) |

### 4단계: 결정

| 항목 | 내용 |
|------|------|
| **담당 파일/함수** | `prediction/improved_predictor.py` → `need_qty` 계산 (line 1464) |
| | `order/auto_order.py` → `final_order_qty` 딕셔너리 생성 (line 679) |
| | `order/order_filter.py` → `_deduct_manual_food_orders()` (수동발주 차감) |
| **입력값** | `adjusted_prediction`, `safety_stock`, `effective_stock_for_need`, `pending_qty` |
| **출력값** | `final_order_qty` (발주 딕셔너리에 포함) |
| **충돌 위험** | `deduct_manual_food_orders()`가 `final_order_qty`를 직접 수정 (max(0, original - manual)). 카테고리 총량 예산(site 차감)과 이중차감 가능성 |

### 5단계: 실행

| 항목 | 내용 |
|------|------|
| **담당 파일/함수** | `order/order_executor.py` → `execute_orders()` (3단계 폴백 오케스트레이션) |
| | `order/direct_api_saver.py` → L1 Direct API (`/stbjz00/saveOrd`) |
| | `order/batch_grid_input.py` → L2 Batch Grid (JS dataset fill) |
| | `order/order_executor.py` → L3 Selenium (UI 입력) |
| **입력값** | `final_order_qty`, `order_unit_qty`, `multiplier` |
| **출력값** | BGF 서버에 PYUN_QTY 제출, `order_tracking` DB 기록 |
| **충돌 위험** | multiplier 계산 시 ceiling division 적용 → `actual_qty >= target_qty` (상향). L1 검증 전체 실패(0/N) 시 폴백 트리거. OLD_PYUN_QTY 타입 불일치 버그 (수정완료) |

---

## 2. 변수 사전

| 변수명 | 의미 | 단위 | 생성 시점 | 수정 가능 계층 |
|--------|------|------|-----------|----------------|
| `adjusted_prediction` | 계수 보정된 수요 예측값 | 개 (float) | 2단계: base_prediction에 요일/날씨/계절/행사 계수 적용 후 | 2단계 내부에서만 (카테고리별 추가 보정 포함) |
| `lead_time_demand` | 리드타임 기간 추가 수요 | 개 (float) | 2단계: `adjusted_prediction * (lead_time - 1)` | 생성 후 불변 (현재 대부분 0, lead_time=1) |
| `safety_stock` | 안전재고 수량 | 개 (float) | 2단계: 카테고리별 Strategy에서 산출 | cost_optimizer 보정, intermittent 최소값 보정 후 불변 |
| `effective_stock_for_need` | need 계산용 유효 재고 | 개 (int) | 2단계: 푸드 유통기한 1일 이하→0, 그 외→current_stock | 생성 후 불변 |
| `pending_qty` | 미입고(발주 대기) 수량 | 개 (int) | 1단계: BGF EXPECT_QTY에서 수집 | 생성 후 불변 |
| `need_qty` | 순수 필요 수량 | 개 (float) | 4단계: `adj_pred + lead_time + safety - eff_stock - pending` | FORCE_MAX_DAYS cap, pending 충분성 체크 후 불변 |
| `order_qty` | 발주 수량 (보정 중간값) | 개 (int) | 3단계: `_apply_order_rules(need_qty)` | **7회 재할당**: rules→promo→ML→boost→penalty→sub→cap→round |
| `promo_delta` | 행사 보정 변화량 | 개 (int) | 명시적 변수 아님 — `_apply_promotion_adjustment()` 전후 order_qty 차이로만 존재 | 코드에 변수 없음. 진단 시 전후값 비교로 산출 |
| `floor_qty` | 발주단위 하한 정렬값 | 개 (int) | 3단계: `_round_to_order_unit()` 내 `(order_qty // unit) * unit` | 함수 내부 임시값, 반환 후 order_qty에 대입 |
| `ceil_qty` | 발주단위 상한 정렬값 | 개 (int) | 3단계: `_round_to_order_unit()` 내 `((order_qty + unit - 1) // unit) * unit` | 함수 내부 로컬 변수. ctx["_round_ceil"]에도 저장. 결품위험(days_cover<0.5) 시 ceil 선택, 그 외 floor 우선 |
| `final_order_qty` | 최종 발주 수량 (실행 전달용) | 개 (int) | 4단계: auto_order.py에서 딕셔너리에 대입 | `deduct_manual_food_orders()`에서 차감 가능 |
| `multiplier` | BGF 배수 (PYUN_QTY) | 배 (int) | 5단계: `(final_order_qty + unit - 1) // unit` (ceiling) | 생성 후 불변 |
| `pyun_qty` | BGF 서버 제출 배수 | 배 (int) | 5단계: `= multiplier` (dsGeneralGrid PYUN_QTY 컬럼) | 생성 후 불변 |

---

## 3. 각 함수의 권한 범위

| 함수 | 할 수 있는 것 | 할 수 없는 것 |
|------|--------------|--------------|
| `_apply_promotion_adjustment()` | order_qty를 행사 배수 기준으로 조정 (1+1→최소2, 2+1→최소3). 행사 종료 D-3 이내 감소, 행사 시작 D-3 이내 증가. order_qty를 0→양수로 변환 가능 (최소배수 보정) | effective_stock_for_need, safety_stock, adjusted_prediction 수정 불가. 발주단위 정렬은 하지 않음 |
| `_round_to_order_unit()` | order_qty를 발주단위(6,10,12,24)의 배수로 floor 정렬. floor=0이면 1단위로 올림 (order 소멸 방지) | order_qty를 0으로 만들 수 없음 (양수 입력 시). 다른 변수 수정 불가 |
| `_apply_order_rules()` | float need_qty를 int order_qty로 변환. 과재고 방지 (stock+pending >= threshold → 0). 금요일 주류/담배 부스트. 최소 임계값(0.5 미만→0) 적용 | safety_stock, adjusted_prediction 수정 불가. 발주단위 정렬 하지 않음 |
| `deduct_manual_food_orders()` | final_order_qty에서 수동발주 수량 차감 (`max(0, orig - manual)`). 차감 후 min_order_qty 미만이면 항목 제거 | 비푸드 카테고리는 건드리지 않음. 새 항목 추가 불가 |

---

## 4. 충돌 위험 지점

### 0 → 양수 변환이 발생할 수 있는 지점

| 위치 | 파일:함수 | 조건 | 설명 |
|------|-----------|------|------|
| 행사 최소배수 | `improved_predictor.py` : `_apply_promotion_adjustment()` | 행사 활성 + order_qty=0 | 1+1→최소2, 2+1→최소3으로 강제 부여 |
| 신제품 부스트 | `improved_predictor.py` : 신제품 lifecycle monitoring | order_qty=0 + 모니터링 상태 | mid_cd 유사상품 기반 부스트 |
| round_to_order_unit | `improved_predictor.py` : `_round_to_order_unit()` | floor_qty=0 + order_qty>0 | floor=0이면 1단위(order_unit_qty)로 올림 |
| FORCE_ORDER | `order/order_filter.py` | is_available=0 상품도 포함 | 수정완료 (force-order-fix) |

### 재고 체크가 분산된 위치

| 위치 | 파일:라인 | 체크 내용 |
|------|-----------|-----------|
| need_qty 계산 | `improved_predictor.py` : line 1464 | `- effective_stock_for_need - pending_qty` |
| 과재고 방지 | `improved_predictor.py` : `_apply_order_rules()` | `effective_stock + pending >= threshold * daily_avg` → 0 |
| ML 앙상블 | `improved_predictor.py` : `_apply_ml_ensemble()` | `ml_order = ml_pred + safety - current_stock - pending` |
| 푸드 유통기한 1일 | `improved_predictor.py` : line 1451 | `effective_stock_for_need = 0` (재고 무시) |
| 발주 실행 | `order/order_executor.py` : `input_product()` | BGF 그리드에서 NOW_QTY 재확인 (정보용) |

### 덮어쓰기(재할당)가 발생하는 지점

| 순서 | 파일:함수 | 변수 | 설명 |
|------|-----------|------|------|
| 1 | `_apply_order_rules()` | `order_qty =` | need_qty(float) → order_qty(int) 최초 생성 |
| 2 | `_apply_promotion_adjustment()` | `order_qty =` | 행사 보정 후 재할당 |
| 3 | `_apply_ml_ensemble()` | `order_qty =` | ML 블렌딩 후 재할당 |
| 4 | 신제품 부스트 | `order_qty =` | 부스트 적용 후 재할당 |
| 5 | DiffFeedback | `order_qty =` | `max(1, int(order_qty * penalty))` |
| 6 | SubstitutionDetector | `order_qty =` | `max(1, int(order_qty * sub_coef))` |
| 7 | CategoryDemandForecaster | `order_qty =` | mid_cd 총량 캡 적용 |
| 8 | LargeCategoryForecaster | `order_qty =` | large_cd 총량 캡 적용 |
| 9 | `_round_to_order_unit()` | `order_qty =` | 발주단위 정렬 (최종) |
| 10 | `deduct_manual_food_orders()` | `final_order_qty =` | 수동발주 차감 (auto_order 레벨) |

---

## 5. 리팩토링 목표 구조

### 현재 구조의 문제점

```
predict_single()
  └─ _compute_safety_and_order()
       ├─ need_qty = ... (계산)
       ├─ order_qty = _apply_order_rules(need_qty)     # 1차 덮어쓰기
       ├─ order_qty = _apply_promotion_adjustment(...)  # 2차 덮어쓰기
       ├─ order_qty = _apply_ml_ensemble(...)           # 3차 덮어쓰기
       ├─ order_qty = max(1, int(order_qty * penalty))  # 4차 덮어쓰기
       ├─ order_qty = max(1, int(order_qty * sub_coef)) # 5차 덮어쓰기
       ├─ order_qty = ... (category cap)                # 6차 덮어쓰기
       └─ order_qty = _round_to_order_unit(order_qty)   # 7차 덮어쓰기
```

- 각 함수가 `order_qty`를 직접 덮어씀 → 이전 단계 결과 추적 불가
- 재고 체크가 4곳 이상에 분산 → 어떤 체크가 최종인지 불명확
- 중간값 로그 부재 (trace_id/ctx로 부분 보완되었으나 불충분)
- 0→양수 변환 지점이 숨겨져 있음 (promo, new_product_boost)

### 목표 구조

```
predict_single()
  └─ _compute_safety_and_order()
       ├─ proposal = OrderProposal(need_qty=..., reason="base")
       ├─ proposal = apply_order_rules(proposal)      # 제안만, 원본 보존
       ├─ proposal = apply_promotion(proposal)         # 제안만, 원본 보존
       ├─ proposal = apply_ml_ensemble(proposal)       # 제안만, 원본 보존
       ├─ proposal = apply_penalties(proposal)         # 제안만, 원본 보존
       ├─ proposal = apply_category_cap(proposal)      # 제안만, 원본 보존
       └─ final = finalize_order(proposal)             # 유일한 확정 지점
            ├─ 재고 차단 (단일 지점)
            ├─ 발주단위 정렬
            └─ 변경 이력 로그
```

**OrderProposal 객체 (목표)**:
```python
@dataclass
class OrderProposal:
    need_qty: float          # 원본 필요량 (불변)
    current_qty: int         # 현재 제안 수량
    adjustments: list        # [(단계명, 이전값, 이후값, 사유)]
    flags: set               # {"promo_boosted", "ml_blended", "sub_penalized", ...}

    def adjust(self, new_qty: int, stage: str, reason: str) -> 'OrderProposal':
        """새 제안 생성 (원본 보존, 이력 기록)"""
        ...
```

**핵심 원칙**:
1. **덮어쓰기 금지**: 각 단계는 `proposal.adjust()`로 제안만 하고, 직접 `order_qty =`  재할당하지 않음
2. **재고 차단 단일화**: `finalize_order()` 한 곳에서만 재고 기반 차단 수행
3. **권한 범위 명시**: 각 단계가 수정할 수 있는 필드와 수정 불가 필드를 타입으로 강제
4. **이력 추적**: `proposal.adjustments`에 모든 변경 기록 → 디버깅/진단 즉시 가능

---

## 6. 우선순위별 리팩토링 계획

| 단계 | 작업 | 전제 조건 | 예상 범위 |
|------|------|-----------|-----------|
| 1단계 | **OrderProposal 도입 + 이력 로깅** — `_compute_safety_and_order()` 내부에 proposal 객체 추가, 각 단계 전후값 기록, 기존 동작 변경 없이 로깅만 추가 | 회귀 테스트 완비 (3077개 통과 확인) | 소규모: improved_predictor.py 1파일, 새 dataclass 1파일 |
| 2단계 | **order_priority.json + 함수 권한 범위 docstring** — 파이프라인 5단계 권한/충돌 선언 파일 생성, 핵심 함수 3개에 권한 범위 docstring 추가 | 1단계 완료 | 소규모: config 1파일 신규, improved_predictor.py docstring 3개 |
| 3단계 | **stock_gate 가시화** — 3개 재고 차단 지점(입구/중간/출구)에 proposal stock_gate 기록 추가. 로직 이동 없이 가시화만 강화 (설계 변경: 계산 시점이 달라 통합 불가) | 2단계 완료 | 소규모: improved_predictor.py 3개소 + order_proposal.py 1메서드 |
| 4단계 | **OrderProposal 기반 전면 구조 전환** — 각 보정 함수를 독립 클래스(Strategy)로 추출, `OrderProposal`의 `adjust()` 인터페이스 통일, 0→양수 변환 지점 명시적 플래그 도입 | 3단계 완료 + 각 단계 입출력 계약 확정 | 대규모: prediction/ 디렉토리 구조 변경, 8+ 파일 |

---

## 리팩토링 진행 현황

| 단계 | 내용 | 상태 |
|------|------|------|
| 1단계 | OrderProposal 추적 객체 도입 | ✅ 완료 |
| 2단계 | order_priority.json + 함수 권한 범위 docstring | ✅ 완료 |
| 3단계 | stock_gate 가시화 (finalize_order 통합 → 설계 변경) | ✅ 완료 |
| 4단계 | OrderProposal 기반 전면 구조 전환 | 예정 |

> **3단계 설계 변경**: 3개 차단 지점은 계산 시점이 달라 통합 불가 (설계 의도).
> 로직 유지 + proposal stock_gate 기록으로 가시화만 강화.
> - `stock_gate_entry`: 입구 차단 (need_qty 계산 직후, cover_need 기준)
> - `stock_gate_overstock`: 중간 차단 (_apply_order_rules, stock_days 기준)
> - `stock_gate_surplus`: 출구 차단 (_round_to_order_unit, surplus 기준)

---

## 7. 파이프라인 설계 원칙

> **목적**: 아래 원칙은 새 기능 추가/버그 수정 시 "왜 이렇게 되어야 하는가"를 판단하는 기준이다.
> CLAUDE.md의 체크리스트가 "어떻게 수정해라"를 다룬다면, 이 문서는 "왜 이렇게 설계했다"를 다룬다.

### P-1. Cap은 항상 최종 게이트다

```
Floor(mid) → Floor(large) → CUT 보충 → Cap ← 항상 마지막
```

- **원칙**: Floor/CUT 보충이 아무리 많은 수량을 추가해도, Cap이 최종적으로 요일평균+버퍼 이하로 절삭한다.
- **존재 이유**: 폐기 방지. 편의점 푸드류는 유통기한이 1~3일이므로, 수요 초과 발주는 곧 폐기다.
- **위반 사례**: `floor-before-cap` 이전에는 Cap→CUT→Floor 순서로 실행되어 Floor가 Cap 결과를 덮어씀 → 폐기 증가.
- **검증 방법**: `get_recommendations()` 반환 직전, 모든 food mid_cd의 합계가 `apply_food_daily_cap`의 adjusted_cap 이하인지 확인.

### P-2. Floor는 Cap 범위 안에서만 보충한다

- **원칙**: Floor(mid/large)는 "최소 카테고리 총량"을 보장하지만, Cap의 상한을 초과해서는 안 된다.
- **존재 이유**: Floor의 목적은 품절 방지. Cap의 목적은 폐기 방지. 두 목적이 충돌하면 **폐기 방지가 우선**.
- **구현**: Floor가 먼저 실행되고, Cap이 나중에 실행되어 자연스럽게 절삭 (실행 순서가 원칙을 강제).
- **위반 감지**: Floor 추가 품목 수 > Cap 절삭 품목 수 → 정상. 반대면 Floor 기준 재검토 필요.

### P-3. 수동발주는 모든 단계에서 인식되어야 한다

- **원칙**: 사용자가 수동으로 입력한 발주(site 발주)는 Floor/CUT/Cap 모든 단계에서 차감되어야 한다.
- **존재 이유**: 이중 발주 방지. 수동으로 3개 넣었는데 자동으로 또 3개 넣으면 6개 → 폐기.
- **데이터 경로**: `_get_site_order_counts_by_midcd()` → `site_order_counts` dict → Floor/CUT/Cap에 전달.
- **위반 사례**: `site-budget-manual-include` 이전에는 `NOT IN manual_order_items` 조건으로 수동발주를 제외 → Floor가 수동발주를 인식 못해 이중 보충.
- **단위**: site_order_counts는 **수량(SUM)** 기준. COUNT(건수)가 아님 (site_count COUNT→SUM 수정 참조).

### P-4. 재고 체크는 강제 발주하는 모든 분기에 존재해야 한다

- **원칙**: pred=0 상태에서 order_qty를 0→양수로 만드는 모든 분기는 재고 체크를 포함해야 한다.
- **존재 이유**: 재고가 충분한데 강제 발주하면 과발주 → 폐기.
- **0→양수 변환 지점**:
  1. `_apply_promotion_adjustment()` Branch A/B/C: 행사 기간 강제 보정 → **Fix B (재고 체크)** 적용
  2. `_round_to_order_unit()`: floor=0, order_qty>0일 때 1단위 올림 → stock_gate_surplus 적용
  3. 신제품 부스트: monitoring 상태 → order_qty>0 조건이 가드
  4. ROP: effective_stock=0 조건이 가드
  5. FORCE_ORDER: is_available 체크 필수 (force-order-fix)
- **검증 방법**: `grep -n "order_qty = "` 결과에서 0→양수 가능 지점을 목록화하고, 각 지점에 재고 체크 존재 여부 확인.

### P-5. is_available=0 상품은 후보에 포함하지 않는다

- **원칙**: 미취급(is_available=0) 상품은 Floor/CUT 보충 후보, 예측 대상에서 제외해야 한다.
- **존재 이유**: 미취급 상품을 발주하면 BGF 시스템에서 거부되거나, 의도하지 않은 상품 입고.
- **구현 패턴**: `LEFT JOIN realtime_inventory ri ON ... WHERE COALESCE(ri.is_available, 1) = 1`
- **위반 사례**: `force-order-fix` — FORCE_ORDER 판정 시 is_available=0까지 포함되어 미취급 상품 강제 발주.
- **보호 규칙**: `inventory_repo.py`의 `preserve_stock_if_zero()`에서 is_available을 변경하면 안 됨 (무결성 체크 #6).

### P-6. NULL은 항상 안전한 기본값으로 처리한다

- **원칙**: LEFT JOIN 결과의 NULL 컬럼은 `COALESCE(col, default)`로 안전하게 변환해야 한다.
- **기본값 규칙**:
  - `is_available`: `COALESCE(ri.is_available, 1)` → NULL이면 취급 가능(1)으로 간주
  - `stock_qty`: `COALESCE(ri.stock_qty, 0)` → NULL이면 재고 0으로 간주
  - `order_unit_qty`: `COALESCE(pd.order_unit_qty, 1)` → NULL이면 단위 1로 간주
  - `pending_qty`: `COALESCE(ri.pending_qty, 0)` → NULL이면 미입고 0으로 간주
- **존재 이유**: 상품 마스터에는 있지만 재고 테이블에 아직 없는 상품 (신규 입고 전)이 빈번.

### P-7. 후행 덮어쓰기 방지

- **원칙**: 새 로직 추가 시 "이 단계가 앞 단계의 의도를 깰 수 있는가?"를 반드시 확인한다.
- **존재 이유**: order_qty가 7회+ 재할당되는 구조에서, 후행 로직이 선행 보정을 무효화하는 버그 반복 발생.
- **실제 버그**:
  - `preserve_stock_if_zero` → `is_available` 덮어씀 (unavailable 상품이 available로 변경)
  - DiffFeedback → 행사 최소 배수 아래로 감소 (promo-floor-after-diff로 해결)
  - Cap → Floor 결과 절삭 (floor-before-cap으로 순서 변경하여 해결)
- **해결 패턴**: 후행 로직 직후에 선행 보정의 floor/invariant를 재적용.
  - 예: DiffFeedback 후 `promo_floor` 재적용 (L1716-1727)

---

## 8. 방어 로직 적용 기준

> 새 코드 작성 시, 아래 체크리스트를 적용해야 하는 위치를 판단하는 기준.

### 재고 체크 (`stock + pending >= demand`)

| 적용 필수 상황 | 예시 |
|---------------|------|
| order_qty를 0→양수로 변환하는 모든 분기 | promo Branch A/B/C, ROP, new_product_boost |
| 강제 발주(FORCE_ORDER, URGENT) | pre_order_evaluator |
| Floor 보충으로 새 품목 추가 시 | category_demand_forecaster, large_category_forecaster |

### is_available 필터

| 적용 필수 상황 | 예시 |
|---------------|------|
| DB에서 후보 상품 조회하는 모든 쿼리 | `_get_supplement_candidates()`, `_get_candidates()` |
| CUT 대체 상품 선정 | `CutReplacementService` |
| 예측 대상 목록 생성 | `get_order_candidates()` |

### NULL 처리 (COALESCE)

| 적용 필수 상황 | 예시 |
|---------------|------|
| LEFT JOIN으로 realtime_inventory 접근 | 모든 재고/is_available 조회 |
| product_details 참조 | order_unit_qty, expiration_days |
| SUM/COUNT 집계 | `COALESCE(SUM(...), 0)` |

### 프로모션 하한 보장

| 적용 필수 상황 | 예시 |
|---------------|------|
| order_qty를 감소시키는 모든 후행 단계 | DiffFeedback, SubstitutionDetector, category cap |
| 패턴: `if _promo_current and order_qty < promo_floor: order_qty = promo_floor` | L1716-1727 |

---

## 9. 실제 버그에서 배운 교훈 (2026-03-13)

| 버그 | 원인 분류 | 적용 원칙 |
|------|----------|----------|
| Branch A/B 재고 체크 누락 | P-4 위반 | 0→양수 변환 시 재고 체크 필수 |
| site_count COUNT→SUM | P-3 위반 | 수동발주 수량 기준 인식 |
| site_count NOT IN manual | P-3 위반 | 수동발주 모든 단계 인식 |
| Floor가 Cap 덮어씀 | P-1, P-2 위반 | Cap 최종 게이트 |
| waste_buffer 미반영 | 문서 부재 | deprecated 상태 명시 |
| data_days 미전달 | 데이터 계약 부재 | data_contracts.md 필요 |
| FORCE_ORDER + is_available=0 | P-5 위반 | 미취급 필터 |
| DiffFeedback → promo_min 하회 | P-7 위반 | 후행 덮어쓰기 방지 |
| preserve_stock → is_available 변경 | P-7 위반 | 후행 덮어쓰기 방지 |
