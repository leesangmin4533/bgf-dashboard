# 단계 간 데이터 계약

> 작성일: 2026-03-13 | 기준: order_flow.md 단계별 입출력 명세

---

## 1. order_list item 필수 필드

> `get_recommendations()`를 통과하는 order_list의 각 item(dict)이 보장해야 하는 필드.

### 전 단계 공통 (Step 1에서 생성)

| 필드 | 타입 | 단위 | 생성자 | 용도 |
|------|------|------|--------|------|
| `item_cd` | str | - | predict_single | 상품 고유 식별자 |
| `item_nm` | str | - | predict_single | 상품명 (로깅용) |
| `mid_cd` | str | 3자리 | predict_single | 중분류 코드 |
| `final_order_qty` | int | 개 | predict_single | 발주 수량 (배수 정렬 완료) |
| `order_unit_qty` | int | 개 | predict_single | 발주 배수 (6,10,12,24 등) |
| `adjusted_prediction` | float | 개 | predict_single | 보정된 수요 예측 |
| `safety_stock` | float | 개 | predict_single | 안전재고 |
| `current_stock` | int | 개 | predict_single | 현재 재고 |
| `pending_qty` | int | 개 | predict_single | 미입고 수량 |
| `daily_avg` | float | 개/일 | predict_single | 일평균 판매 |
| `data_days` | int | 일 | predict_single | 판매 데이터 보유 일수 |

### Floor(mid/large) 추가 시 필수 포함 필드

| 필드 | 타입 | 생성자 | 위반 시 영향 |
|------|------|--------|-------------|
| `data_days` | int | appear_days SQL → candidate → distribute → order_list | Cap의 classify_items에서 "new"로 오분류 |
| `source` | str | forecaster | "floor_mid" / "floor_large" (진단용) |
| `item_nm` | str | SQL JOIN products | 로깅 필수 |

### CUT 보충 추가 시 필수 포함 필드

| 필드 | 타입 | 생성자 | 위반 시 영향 |
|------|------|--------|-------------|
| `data_days` | int | SQL 조회 | Cap에서 오분류 |
| `source` | str | CutReplacementService | "cut_replacement" (진단용) |
| `replaced_mid_cd` | str | CutReplacementService | 원본 CUT 카테고리 추적 (mid_cd 단위 분배이므로 개별 item이 아닌 카테고리) |

---

## 2. site_order_counts 계약

### 생성자

| 항목 | 내용 |
|------|------|
| **메서드** | `auto_order.py::_get_site_order_counts_by_midcd()` |
| **SQL** | `SELECT mid_cd, COALESCE(SUM(ot.order_qty), 0) FROM order_tracking ...` |
| **필터** | `ot.order_date = ?`, store DB, 수동/자동 발주 모두 포함 |

### 출력 형식

```python
site_order_counts: dict[str, int]
# key: mid_cd (예: "001", "002")
# value: 오늘 발주된 수량 합계 (SUM, 건수 아님)
# 예: {"001": 15, "003": 8}
```

### 소비자 계약

| 소비자 | 사용 방식 | 위반 시 영향 |
|--------|----------|-------------|
| Floor(mid) | `adjusted_floor = max(0, floor_qty - site_count)` | site 차감 없이 이중 보충 |
| Floor(large) | 동일 | 동일 |
| Cap | `adjusted_cap = max(0, total_cap - site_count)` | site 차감 없이 상한 초과 허용 |

### 단위 규칙 (P-3)

- **올바름**: `SUM(order_qty)` = 수량 기준
- **틀림**: `COUNT(*)` = 건수 기준 (Floor/Cap은 수량 비교)
- **틀림**: `NOT IN manual_order_items` = 수동발주 제외 (이중 발주 발생)

---

## 3. eval_results 계약

### 형식

```python
eval_results: dict[str, PreOrderEvalResult]
# key: item_cd
# value: PreOrderEvalResult(decision, reason, daily_avg, mid_cd, item_nm, ...)
```

### 소비자

| 소비자 | 사용 방식 |
|--------|----------|
| Step 0 (skip_codes) | `decision == SKIP` → 예측 제외 |
| Step 7 (CUT 보충) | `decision == SKIP and "CUT" in reason` → _cut_lost_items에 추가 |
| Floor(mid/large) | `eval_results` 전달 → 내부에서 FORCE/SKIP 참조 |

### 보존 규칙

- eval_results의 CUT SKIP 항목에서 `daily_avg > 0`이고 `mid_cd in FOOD_CATEGORIES`인 것만 _cut_lost_items로 변환
- eval_results 자체는 불변 (읽기 전용으로만 사용)

---

## 4. predict_single → order_list item 변환 계약

### predict_single() 반환값

```python
PredictionResult:
  item_cd: str
  order_qty: int          # 배수 정렬 완료
  adjusted_prediction: float
  safety_stock: float
  need_qty: float
  current_stock: int
  pending_qty: int
  daily_avg: float
  data_days: int          # ★ 필수 (Cap 분류용)
  model_type: str
  ctx: dict               # 진단 정보 (trace, stage, promo_branch 등)
```

### order_list item 변환 (auto_order.py L679~)

```python
{
    "item_cd": result.item_cd,
    "item_nm": product["item_nm"],
    "mid_cd": product["mid_cd"],
    "final_order_qty": result.order_qty,
    "order_unit_qty": product.get("order_unit_qty", 1),
    "adjusted_prediction": result.adjusted_prediction,
    "safety_stock": result.safety_stock,
    "current_stock": result.current_stock,
    "pending_qty": result.pending_qty,
    "daily_avg": result.daily_avg,
    "data_days": result.data_days,      # ★ predict_single에서 전달
    # ... 추가 진단 필드
}
```

---

## 5. Cap 입출력 계약

### 입력

```python
apply_food_daily_cap(
    order_list: list[dict],       # 전체 order_list (food + non-food)
    target_date: str,             # YYYY-MM-DD (요일 결정용)
    store_id: str,                # 매장별 DB 접근
    site_order_counts: dict       # ★ Step 4에서 생성된 dict
) -> list[dict]                   # 절삭된 order_list
```

### 내부 classify_items 분류 기준

| 분류 | 조건 | 설명 |
|------|------|------|
| proven | `data_days >= 6` | 검증된 상품 (우선 유지) |
| new | `data_days < 5` | 신규 상품 (탐색 비율 25% 내) |
| failed_exploration | new + 3일 이상 판매=0 | 탐색 실패 (제거 우선) |

### 출력 보장

- food mid_cd별 합계 ≤ `adjusted_cap`
- non-food 항목은 변경 없이 통과
- 제거된 품목은 반환 list에 미포함

---

## 6. Floor 후보 SQL 계약

### _get_supplement_candidates() 필수 조건

```sql
SELECT p.item_cd,
       COUNT(DISTINCT ds.sales_date) as appear_days,  -- ★ data_days 원본
       AVG(ds.sale_qty) as avg_qty
FROM products p
JOIN daily_sales ds ON p.item_cd = ds.item_cd
LEFT JOIN realtime_inventory ri ON p.item_cd = ri.item_cd
WHERE p.mid_cd = ?
  AND ds.sales_date >= date('now', '-30 days')
  AND ds.sale_qty > 0                                 -- 최근 판매 있음
  AND COALESCE(ri.is_available, 1) = 1                -- ★ P-5
  AND p.item_cd NOT IN (SELECT item_cd FROM ...)      -- CUT/자동발주 제외
GROUP BY p.item_cd
HAVING appear_days >= 2
ORDER BY avg_qty DESC
```

### 필수 COALESCE 패턴

| 컬럼 | COALESCE | 기본값 | 이유 |
|------|----------|--------|------|
| `ri.is_available` | `COALESCE(ri.is_available, 1)` | 1 (취급) | 재고 테이블 미등록 상품 |
| `ri.stock_qty` | `COALESCE(ri.stock_qty, 0)` | 0 | 재고 없음으로 간주 |
| `SUM(...)` | `COALESCE(SUM(...), 0)` | 0 | 빈 결과셋 |

---

## 7. 단계 간 불변 조건 (Invariants)

### 데이터 흐름 불변 조건

| 불변 조건 | 검증 위치 | 위반 시 |
|-----------|----------|---------|
| order_list의 모든 item에 `item_cd` 존재 | 모든 Step | KeyError |
| order_list의 모든 item에 `mid_cd` 존재 | Step 5,6,8 | 카테고리 분류 실패 |
| order_list의 모든 item에 `data_days` 존재 | Step 8 | classify_items 오분류 |
| `final_order_qty >= 0` (음수 없음) | 모든 Step | 음수 발주 제출 |
| site_order_counts의 value는 int ≥ 0 | Step 4 | 음수 차감 → 상한 초과 |
| eval_results는 불변 (읽기 전용) | Step 0,7 | CUT 정보 유실 |

### 순서 불변 조건

| 불변 조건 | 근거 |
|-----------|------|
| Step 5 (Floor mid) → Step 6 (Floor large) | Step 6이 Step 5 결과를 기반으로 상위 검증 |
| Step 5,6,7 → Step 8 (Cap) | P-1: Cap은 항상 최종 게이트 |
| Step 4 → Step 5,6,8 | P-3: site_order_counts 없이 실행 금지 |
| predict_single의 배수 정렬 → Step 5,6,7 | Floor/CUT 추가 품목도 배수 정렬 필요 |

### 행사 불변 조건

| 불변 조건 | 근거 |
|-----------|------|
| promo_floor ≤ order_qty (후행 단계 후) | P-7: DiffFeedback/ML 후 행사 하한 복원 |
| Fix B: stock ≥ demand → order_qty 변경 없음 | P-4: 재고 충분 시 강제 발주 금지 |
| promo_avg=0 → Fix B 가드 미작동 | 통계 없으면 판단 불가 → 기존 adjuster 위임 |
