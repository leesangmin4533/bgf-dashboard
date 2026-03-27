# 발주 단계별 책임과 경계

> 작성일: 2026-03-13 | 기준: pipeline_design.md 7-9절 원칙 기반

---

## 전체 흐름도

```
get_recommendations() 내부 실행 순서
═══════════════════════════════════════════════════════════

  [Step 0] Skip codes 수집 (eval_results SKIP 코드)
      │
  [Step 1] ImprovedPredictor.get_order_candidates()
      │    ├─ predict_single() per item
      │    │   ├─ WMA 기본예측 → 계수 보정 → 재고 차감
      │    │   ├─ order_rules → promo → ML → boost → penalty → sub
      │    │   └─ round_to_order_unit (배수 정렬)
      │    └─ 결과: order_list [{item_cd, final_order_qty, mid_cd, ...}]
      │
  [Step 2] 신상품 3일발주 후속 (_process_3day_follow_orders)
      │
  [Step 3] 스마트발주 오버라이드 (_inject_smart_order_items)
      │
  [Step 4] site_order_counts 집계 (CATEGORY_SITE_BUDGET_ENABLED)
      │    └─ _get_site_order_counts_by_midcd() → {mid_cd: sum_qty}
      │
  ┌───▼──── Floor 영역 (수량 증가만 허용) ────┐
  │ [Step 5] Floor(mid): CategoryDemandForecaster │
  │    └─ mid_cd별 최소 총량 보충                   │
  │                                                 │
  │ [Step 6] Floor(large): LargeCategoryForecaster  │
  │    └─ large_cd별 상위 레벨 보충                  │
  └─────────────────────────────────────────────────┘
      │
  [Step 7] CUT 대체 보충: CutReplacementService
      │    └─ 발주중지(CUT) 상품의 손실수요 → 동일 mid_cd 대체품
      │
  ┌───▼──── Cap 영역 (수량 감소만, 최종 게이트) ──┐
  │ [Step 8] Cap: apply_food_daily_cap()            │
  │    └─ 요일평균 + 20%버퍼 - site_count 이하 절삭   │
  │    └─ ★ P-1 원칙: 항상 마지막에 실행              │
  └─────────────────────────────────────────────────┘
      │
  [Step 9] 반환 → order_list
```

---

## 단계별 상세

### Step 1: ImprovedPredictor — 개별 상품 예측

| 항목 | 내용 |
|------|------|
| **파일** | `src/prediction/improved_predictor.py` |
| **메서드** | `get_order_candidates()` → `predict_single()` → `_compute_safety_and_order()` |
| **책임** | 개별 상품의 최적 발주 수량 산출 |
| **할 수 있는 것** | order_qty 산출, 배수 정렬, 재고 차감, 프로모션 보정, ML 블렌딩 |
| **할 수 없는 것** | 카테고리 총량 조절, 다른 상품의 수량 변경, order_list 구조 변경 |
| **출력 보장** | 각 item에 `final_order_qty`(int, ≥0), `mid_cd`, `data_days` 필드 포함 |

#### 내부 서브 단계 (order_qty 재할당 순서)

```
순서  │ 서브 단계                   │ 변환 방향  │ 재고 체크
──────┼─────────────────────────────┼────────────┼──────────
1     │ _apply_order_rules          │ float→int  │ ★ 과재고 방지
2     │ ROP                         │ 0→1 가능   │ ★ stock=0 가드
3     │ _apply_promotion_adjustment │ 0→양수 가능 │ ★ Fix B (A/B/C)
4     │ _apply_ml_ensemble          │ 조정       │ 내부 stock 계산
5     │ new_product_boost           │ 증가만     │ qty>0 가드
6     │ DiffFeedback                │ 감소만     │ 없음 (후행)
7     │ promo_floor 복원            │ 하한 보장  │ 없음 (복원)
8     │ SubstitutionDetector        │ 감소만     │ 없음 (후행)
9     │ category max cap            │ 상한 절삭  │ 없음
10    │ _round_to_order_unit        │ 배수 정렬  │ ★ surplus 체크
```

### Step 4: site_order_counts — 수동발주 집계

| 항목 | 내용 |
|------|------|
| **파일** | `src/order/auto_order.py` L752-785 |
| **메서드** | `_get_site_order_counts_by_midcd()` |
| **책임** | 오늘 이미 발주된 수동발주 수량을 mid_cd별 집계 |
| **단위** | **수량(SUM)** — COUNT(건수)가 아님 (P-3 원칙) |
| **범위** | 수동발주(manual) + 자동발주 모두 포함 (NOT IN 제거됨) |
| **소비자** | Step 5 (Floor mid), Step 6 (Floor large), Step 8 (Cap) |

### Step 5: Floor(mid) — 카테고리 총량 최소 보충

| 항목 | 내용 |
|------|------|
| **파일** | `src/prediction/category_demand_forecaster.py` |
| **메서드** | `supplement_orders()` |
| **책임** | mid_cd별 총량이 카테고리 예측의 70% 이하일 때 보충 |
| **할 수 있는 것** | 기존 품목 수량 증가, 새 품목 추가 (추천) |
| **할 수 없는 것** | 기존 품목 수량 감소, 품목 제거 |
| **후보 필터** | `is_available=1`, `is_cut_item=0`, 최근 판매 있음, `COALESCE` 적용 |
| **data_days 전달** | appear_days → candidate["appear_days"] → order_list item["data_days"] |
| **대상 mid_cd** | ["001","002","003","004","005","012"] |

### Step 6: Floor(large) — 대분류 총량 보충

| 항목 | 내용 |
|------|------|
| **파일** | `src/prediction/large_category_forecaster.py` |
| **메서드** | `supplement_orders()` |
| **책임** | large_cd 레벨에서 mid_cd 간 비율 배분 후 부족분 보충 |
| **동일 규칙** | Step 5와 동일 (is_available, COALESCE, data_days 전달) |
| **실행 시점** | Step 5 이후 — Step 5의 보충 결과를 반영하여 상위 레벨 점검 |

### Step 7: CUT 대체 보충

| 항목 | 내용 |
|------|------|
| **파일** | `src/order/cut_replacement.py` |
| **메서드** | `supplement_cut_shortage()` |
| **책임** | CUT(발주중지) 상품의 손실 수요를 동일 mid_cd 대체품에 80% 배분 |
| **후보 필터** | `is_available=1`, 동일 mid_cd, 최근 판매 > 0 |
| **CUT 정보 출처** | `_cut_lost_items` (auto_order.py에서 수집) + eval_results CUT SKIP |

### Step 8: Cap — 요일별 총량 상한 (최종 게이트)

| 항목 | 내용 |
|------|------|
| **파일** | `src/prediction/categories/food_daily_cap.py` |
| **메서드** | `apply_food_daily_cap()` |
| **책임** | 푸드류(001~005,012) 요일평균 + 20%버퍼 이하로 절삭 |
| **할 수 있는 것** | 품목 제거, 수량 감소 |
| **할 수 없는 것** | 품목 추가, 수량 증가 |
| **설계 원칙** | P-1: 항상 마지막에 실행. Floor/CUT 결과를 절삭하는 최종 게이트 |
| **Cap 공식** | `adjusted_cap = max(0, round(weekday_avg) + round(category_total×0.20) - site_count)` |
| **선별 기준** | classify_items: proven(data_days≥6) vs new(data_days<5), 탐색비율 25% |
| **차원 근사** | total_cap(qty) vs len(items)(count) 비교는 의도적 (food qty≈1) |

---

## 단계 간 관계 규칙

### 허용되는 상호작용

| From → To | 관계 | 설명 |
|-----------|------|------|
| Step 4 → Step 5,6,8 | 데이터 전달 | site_order_counts를 모든 후속 단계에 전달 |
| Step 5 → Step 6 | 순차 의존 | Step 5의 보충 결과를 Step 6이 기반으로 사용 |
| Step 5,6,7 → Step 8 | 순차 의존 | 모든 보충 결과가 Cap에 의해 최종 절삭 |

### 금지되는 상호작용

| 금지 | 이유 | 원칙 |
|------|------|------|
| Step 8이 Step 5,6 앞에 실행 | Floor 결과를 Cap이 인식 못함 → Floor가 Cap 덮어씀 | P-1, P-2 |
| Step 5,6에서 품목 제거 | Floor의 책임은 보충만. 제거는 Cap의 책임 | 단일 책임 |
| Step 8에서 품목 추가 | Cap의 책임은 절삭만. 추가는 Floor의 책임 | 단일 책임 |
| site_order_counts 없이 Floor/Cap 실행 | 수동발주 미인식 → 이중 발주 | P-3 |

---

## Promo 분기 상세 (Step 1 내부)

### 4개 분기와 재고 체크

```
행사 정보 조회
    │
    ├─ [A] 행사 종료 임박 (D-3 이내)
    │   ├─ promo_avg > 0? → demand = promo_avg × weekday_coef
    │   │   ├─ stock + pending ≥ demand → ★ 스킵 (Fix B)
    │   │   └─ stock + pending < demand → adjuster 호출
    │   └─ promo_avg = 0? → adjuster 호출 (가드 없음)
    │
    ├─ [B] 행사 시작 임박 (D-3 이내)
    │   └─ (A와 동일 패턴, 독립 플래그 _skip_branch_b)
    │
    ├─ [C] 행사 안정기 (활성 + avg > daily×0.8)
    │   ├─ stock + pending ≥ demand → ★ 스킵 (Fix B 원본)
    │   └─ stock < demand → promo_order 계산
    │
    └─ [D] 비행사 안정기 (일평균 > normal×1.3)
        └─ normal_need 계산 → 감소만
```

### Fix B 적용 원칙 (P-4 구현)

- **적용 대상**: 0→양수 변환 가능한 모든 promo 분기 (A, B, C)
- **가드 조건**: `promo_avg > 0` (통계 없으면 가드 불가 → 기존 로직 유지)
- **스킵 조건**: `current_stock + pending_qty >= promo_daily_demand`
- **스킵 시 동작**: order_qty 변경 없음, `_skipped = True`, 로그 출력
- **Branch D 면제**: D는 감소만 하므로 재고 체크 불필요
