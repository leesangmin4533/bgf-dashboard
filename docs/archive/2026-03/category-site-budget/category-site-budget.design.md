# 설계 명세서: 카테고리 총량 사이트 발주 차감 (category-site-budget)

> **작성일**: 2026-03-05
> **최종 수정**: 2026-03-05 (리뷰 피드백 반영 v2)
> **상태**: 설계 완료 (미구현)
> **대상 파일**: 6개 (수정 5 + 신규 1)

---

## 1. 개요

### 1.1 배경
현재 시스템은 **상품 단위(item-level)** 로 기존 발주를 차감한다.
`prefetch_pending_quantities()`가 BGF 사이트에서 상품별 미입고 수량을 조회하여
동일 상품의 중복 발주를 방지한다.

그러나 **카테고리 총량(category-level)** 관점에서는 차감하지 않아,
사용자가 이미 특정 카테고리의 상품을 충분히 발주한 경우에도
시스템이 같은 카테고리의 **다른 상품**을 추가 발주하여 과잉 발주가 발생한다.

### 1.2 문제 사례 (2026-03-05 매장 47863)

| 카테고리 | 요일평균 | site(사용자) | auto(시스템) | 합계 | 과잉 |
|---------|:-------:|:---------:|:----------:|:---:|:---:|
| 도시락 | ~5 | 3 | 4 | **7** | +2 |
| 주먹밥 | ~15 | 10 | 12 | **22** | +7 |
| 김밥 | ~12 | 10 | 7 | **17** | +5 |
| 샌드위치류 | ~6 | 4 | 5 | **9** | +3 |
| 햄버거 | ~4 | 2 | 4 | **6** | +2 |

auto와 site의 겹침 = 0개 (상품 단위 차감은 정상 작동 중).
카테고리 총량 차감이 없어서 합계가 요일평균을 크게 초과.

### 1.3 목표
- 카테고리 예산 = `weekday_avg + waste_buffer` (기존 `apply_food_daily_cap` 계산값)
- auto 발주 상한 = `max(0, 예산 - site 발주 수)`
- site + auto <= 예산

---

## 2. 현재 시스템 분석

### 2.1 발주 파이프라인 (Phase 2)

```
execute() [auto_order.py]
  |
  +-- load_auto_order_items()          # 자동/스마트발주 목록 조회
  |
  +-- get_recommendations()            # 예측 기반 발주 목록 생성
  |   +-- predict_batch()              #   상품별 예측
  |   +-- apply_food_daily_cap()       #   * 푸드류 총량 상한 (현재 site 미고려)
  |   +-- CutReplacementService        #   CUT 대체 보충
  |   +-- CategoryDemandForecaster     #   mid_cd 하한 보충
  |   +-- LargeCategoryForecaster      #   large_cd 하한 보충
  |
  +-- prefetch_pending_quantities()    # BGF 사이트 실시간 미입고 조회
  +-- apply_pending_and_stock()        # 상품별 pending/stock 차감
  +-- deduct_manual_food_orders()      # 수동발주 상품 차감
  |
  +-- execute_orders()                 # 실제 발주 제출
```

### 2.2 기존 총량 상한 메커니즘

**파일**: `src/prediction/categories/food_daily_cap.py`

```python
# 현재 로직 (line 448)
total_cap = round(weekday_avg) + waste_buffer   # 예: 15 + 3 = 18

# cap 비교 (line 451-452)
current_count = len(items)                       # auto 발주 SKU 수만 카운트
if current_count <= total_cap:
    result.extend(items)                         # 전부 유지
```

**문제점**: `current_count`에 site 발주가 포함되지 않음.
site 10 + auto 12 = 22이지만, current_count = 12(auto만)로 계산되어
cap 18 이내로 판정 -> 전부 통과.

### 2.3 하한 보충 메커니즘

**CategoryDemandForecaster** (`src/prediction/category_demand_forecaster.py`):
- `current_sum` = order_list 내 해당 mid_cd의 `final_order_qty` 합계
- `floor_qty` = `forecast * 0.7`
- `current_sum < floor_qty`면 부족분만큼 후보 상품 추가

**문제점**: `current_sum`에 site 발주가 포함되지 않아,
site가 이미 충분한데도 "부족"으로 판정하여 불필요한 보충 발생 가능.

### 2.4 Phase 1.95 site 발주 동기화

**파일**: `src/collectors/order_status_collector.py`
- `sync_pending_to_order_tracking(days_back=7)`
- BGF 발주현황(전체) 에서 미입고 건 -> `order_tracking` 테이블에 `order_source='site'`로 저장
- Phase 2 직전에 실행되므로 데이터는 최신 상태

-> `order_tracking`에서 `order_source='site'`인 레코드를 조회하면
  site 발주 수를 카테고리별로 집계 가능.

---

## 3. 설계

### 3.1 변경 파일 목록

| # | 파일 | 변경 유형 | 변경 내용 |
|---|------|----------|----------|
| 1 | `src/settings/constants.py` | 추가 | `CATEGORY_SITE_BUDGET_ENABLED` 토글 |
| 2 | `src/prediction/categories/food_daily_cap.py` | 수정 | `site_order_counts` 파라미터 + 차감 로직 |
| 3 | `src/order/auto_order.py` | 수정 | site 발주 조회 메서드 + 호출부 수정 |
| 4 | `src/prediction/category_demand_forecaster.py` | 수정 | `site_order_counts`로 current_sum 보정 |
| 5 | `src/prediction/large_category_forecaster.py` | 수정 | `site_order_counts`로 mid_cd_sum 보정 |
| 6 | `tests/test_category_site_budget.py` | 신규 | 통합 테스트 |

### 3.2 토글 상수

**파일**: `src/settings/constants.py`

```python
# 카테고리 총량 예산에서 site(사용자) 발주 차감 여부
# True: 카테고리별 예산(weekday_avg+buffer)에서 site 발주 수를 빼고 auto 상한 산정
# False: 기존 동작 (상품 단위 차감만)
CATEGORY_SITE_BUDGET_ENABLED = True
```

### 3.3 site 발주 수 조회 메서드

**파일**: `src/order/auto_order.py`
**메서드**: `_get_site_order_counts_by_midcd(order_date) -> Dict[str, int]`

```
입력: order_date (str, 발주일 = 오늘, 'YYYY-MM-DD' 형식)
출력: {mid_cd: count}  예: {'001': 3, '002': 10, '003': 10, '004': 4, '005': 2}

SQL:
  SELECT p.mid_cd, COUNT(*) as cnt
  FROM order_tracking ot
  JOIN common.products p ON ot.item_cd = p.item_cd
  WHERE ot.order_source = 'site'
    AND ot.order_date = :order_date
    AND ot.store_id = :store_id
  GROUP BY p.mid_cd

에러 시: 빈 dict 반환 (기존 동작 유지, 안전 폴백)
```

> **v2 수정사항**:
> - `store_id` 필터 추가 (매장 DB에 타매장 레코드 오염 사례 확인됨: 46704.db에 46513 레코드 403건)
> - 파라미터명 `target_date` → `order_date`로 변경 (`order_tracking.order_date`는 발주일=오늘이며, `apply_food_daily_cap`의 `target_date`는 배송일=내일이므로 혼동 방지)
> - 호출 시 `order_date = datetime.now().strftime('%Y-%m-%d')` 사용 (target_date가 아닌 오늘 날짜)

### 3.4 apply_food_daily_cap 수정

**파일**: `src/prediction/categories/food_daily_cap.py`

**시그니처 변경**:
```python
def apply_food_daily_cap(
    order_list, target_date=None, db_path=None, store_id=None,
    site_order_counts=None  # NEW: {mid_cd: int} site 발주 건수
) -> List[Dict[str, Any]]:
```

**핵심 로직 변경** (기존 line 448-462):
```
BEFORE:
  total_cap = round(weekday_avg) + waste_buffer
  if len(items) <= total_cap -> 그대로 유지
  else -> select_items_with_cap(items, total_cap)

AFTER:
  total_cap = round(weekday_avg) + waste_buffer
  site_count = site_order_counts.get(mid_cd, 0) if site_order_counts else 0
  adjusted_cap = max(0, total_cap - site_count)

  로그: "mid_cd=002: 예산18 - site10 = auto상한8"

  if len(items) <= adjusted_cap -> 그대로 유지
  else -> select_items_with_cap(items, adjusted_cap)
```

**select_items_with_cap 호출 시**:
- 기존 선별 로직(활용 75% + 탐색 25%) 그대로 유지
- 단, cap이 줄어들었으므로 선별되는 상품 수가 감소

### 3.5 get_recommendations 호출부 수정

**파일**: `src/order/auto_order.py`
**위치**: `get_recommendations()` 내 `apply_food_daily_cap` 호출 직전 (약 line 877)

```python
# 현재
order_list = apply_food_daily_cap(order_list, target_date=target_date, store_id=self.store_id)

# 변경
site_order_counts = {}
from src.settings.constants import CATEGORY_SITE_BUDGET_ENABLED
if CATEGORY_SITE_BUDGET_ENABLED:
    from datetime import datetime
    today_str = datetime.now().strftime('%Y-%m-%d')
    site_order_counts = self._get_site_order_counts_by_midcd(today_str)

order_list = apply_food_daily_cap(
    order_list, target_date=target_date, store_id=self.store_id,
    site_order_counts=site_order_counts
)
```

> **v2 수정사항**: `target_date`(배송일=내일) 대신 `today_str`(발주일=오늘) 전달.
> `order_tracking.order_date`는 발주일 기준이므로 오늘 날짜로 조회해야 정합.

### 3.6 CategoryDemandForecaster 수정

**파일**: `src/prediction/category_demand_forecaster.py`
**메서드**: `supplement_orders(order_list, ...)`

**시그니처 변경**:
```python
def supplement_orders(self, order_list, target_date=None,
                      site_order_counts=None):  # NEW
```

**로직 변경**:
```python
# 현재
current_sum = sum(item['final_order_qty'] for item in order_list
                  if item.get('mid_cd') == mid_cd)

# 변경
current_sum = sum(item['final_order_qty'] for item in order_list
                  if item.get('mid_cd') == mid_cd)
site_count = (site_order_counts or {}).get(mid_cd, 0)
current_sum += site_count  # site 발주를 이미 채워진 것으로 간주
```

-> site가 이미 충분하면 `current_sum >= floor_qty`가 되어 불필요한 보충 방지.

### 3.7 LargeCategoryForecaster 수정

**파일**: `src/prediction/large_category_forecaster.py`
**메서드**: `supplement_orders(order_list, ...)`

동일 패턴:
```python
# mid_cd별 현재 합계 계산 시
current_sum += (site_order_counts or {}).get(mid_cd, 0)
```

### 3.8 auto_order.py 호출 체인 수정

`get_recommendations()` 내에서 `site_order_counts`를 forecaster에 전달:

```python
# 현재 (약 line 905)
order_list = self._category_forecaster.supplement_orders(order_list, target_date)

# 변경
order_list = self._category_forecaster.supplement_orders(
    order_list, target_date, site_order_counts=site_order_counts
)

# 현재 (약 line 920)
order_list = self._large_category_forecaster.supplement_orders(order_list, target_date)

# 변경
order_list = self._large_category_forecaster.supplement_orders(
    order_list, target_date, site_order_counts=site_order_counts
)
```

### 3.9 deduct_manual_food_orders 이중 차감 방지 (v2 추가)

**문제**: `manual_order_items`(Phase 1.2 수집)와 `order_tracking(site)`(Phase 1.95 수집)에
동일 상품이 중복 존재할 수 있음 (2026-03-05 실측: 7건 겹침).

- `deduct_manual_food_orders()`: `manual_order_items`에서 **상품 단위** 차감 (item_cd 기반)
- 본 설계의 카테고리 예산: `order_tracking(site)`에서 **카테고리 단위** 차감 (mid_cd 기반)

→ 겹치는 상품은 **상품 단위 + 카테고리 단위** 이중 차감됨.

**대응 방안**: `_get_site_order_counts_by_midcd()` SQL에서 `manual_order_items`에
이미 존재하는 item_cd를 제외:

```sql
SELECT p.mid_cd, COUNT(*) as cnt
FROM order_tracking ot
JOIN common.products p ON ot.item_cd = p.item_cd
WHERE ot.order_source = 'site'
  AND ot.order_date = :order_date
  AND ot.store_id = :store_id
  AND ot.item_cd NOT IN (
      SELECT item_cd FROM manual_order_items
      WHERE order_date = :order_date AND store_id = :store_id
  )
GROUP BY p.mid_cd
```

→ `manual_order_items`에 있는 상품은 `deduct_manual_food_orders()`가 상품 단위로 처리하므로,
카테고리 예산 계산에서 제외하여 이중 차감 방지.

> **대안**: `MANUAL_ORDER_FOOD_DEDUCTION = False`로 기존 수동차감을 끄고
> 본 설계의 카테고리 차감만 사용하는 방법도 가능하나,
> 상품 단위 정밀 차감(수동)과 카테고리 총량 차감(본 설계)은 역할이 다르므로
> 둘 다 유지하되 겹침을 SQL에서 제거하는 것이 안전.

---

## 4. 예상 동작 시뮬레이션

### 4.1 주먹밥(002) 시나리오

```
입력:
  weekday_avg = 15.0
  waste_buffer = 3
  total_cap = 18
  site_order_counts = {'002': 10}

apply_food_daily_cap:
  adjusted_cap = 18 - 10 = 8
  auto 예측 = 12개 SKU
  12 > 8 -> select_items_with_cap(12개, cap=8) -> 8개 선별
  로그: "mid_cd=002: 예산18 - site10 = auto상한8, 12개->8개"

CategoryDemandForecaster:
  current_sum = 8(auto) + 10(site) = 18
  floor_qty = 15 * 0.7 = 10.5
  18 >= 10.5 -> 보충 불필요

최종: site 10 + auto 8 = 18 (예산 이내)
```

### 4.2 도시락(001) 시나리오 (site 없는 경우)

```
입력:
  weekday_avg = 5.0, waste_buffer = 3
  total_cap = 8
  site_order_counts = {} (도시락 site 발주 없음)

apply_food_daily_cap:
  adjusted_cap = 8 - 0 = 8 (변화 없음)
  -> 기존 동작과 동일
```

### 4.3 엣지 케이스: site가 예산 초과

```
입력:
  weekday_avg = 5.0, waste_buffer = 3
  total_cap = 8
  site_order_counts = {'001': 12} (사용자가 이미 12개 발주)

apply_food_daily_cap:
  adjusted_cap = max(0, 8 - 12) = 0
  -> auto 발주 0개 (사용자가 이미 충분히 발주함)

CategoryDemandForecaster:
  current_sum = 0(auto) + 12(site) = 12
  floor_qty = 5 * 0.7 = 3.5
  12 >= 3.5 -> 보충 불필요
```

---

## 5. 안전장치

### 5.1 토글
`CATEGORY_SITE_BUDGET_ENABLED = False`로 즉시 비활성화 가능.
비활성화 시 기존 동작과 100% 동일.

### 5.2 에러 폴백
`_get_site_order_counts_by_midcd()` 실패 시 빈 dict 반환
-> `site_count = 0` -> `adjusted_cap = total_cap` -> 기존 동작 유지.

### 5.3 FORCE/URGENT 보호
`apply_food_daily_cap`의 `select_items_with_cap()`은 eval 우선순위를
반영하므로 FORCE/URGENT 상품은 우선 유지됨.

### 5.4 최소값 보장
`adjusted_cap = max(0, ...)` -> 음수 방지.
cap=0인 경우 해당 카테고리의 auto 발주 전량 제거 (site가 이미 충분).

### 5.5 store_id 격리 (v2 추가)
SQL에 `ot.store_id = :store_id` 필터 포함.
매장 DB에 타매장 레코드가 오염되어 있어도 현재 매장만 집계.

### 5.6 이중 차감 방지 (v2 추가)
`manual_order_items`에 존재하는 item_cd는 site_count에서 제외.
`deduct_manual_food_orders()`(상품 단위)와 카테고리 예산(총량 단위)의 이중 차감 방지.

---

## 6. 테스트 계획

| # | 테스트 케이스 | 검증 포인트 |
|---|-------------|-----------|
| 1 | site 없는 경우 | 기존 동작과 동일 (adjusted_cap = total_cap) |
| 2 | site < 예산 | auto = 예산 - site |
| 3 | site = 예산 | auto = 0 |
| 4 | site > 예산 | auto = 0 (max(0,...)) |
| 5 | 토글 OFF | site_order_counts 무시, 기존 동작 |
| 6 | 조회 실패 | 빈 dict -> 기존 동작 |
| 7 | 복수 카테고리 | 각 mid_cd 독립 차감 |
| 8 | floor 보충 비간섭 | site 포함 current_sum >= floor 시 보충 안 함 |
| 9 | FORCE/URGENT | cap 축소 시에도 FORCE 상품 우선 유지 |
| 10 | 비푸드 카테고리 | 담배/주류 등은 영향 없음 |
| 11 | manual+site 겹침 | manual_order_items 겹치는 item_cd는 site_count에서 제외 |
| 12 | store_id 오염 격리 | 타매장 레코드가 있어도 현재 매장만 집계 |
| 13 | order_date 정합 | 발주일(오늘) 기준 조회, 배송일(내일)과 혼동 없음 |

---

## 7. 의존성
- 신규 패키지 없음
- 기존 인프라 재사용: `DBRouter`, `attach_common_with_views`, `order_tracking` 테이블
- Phase 1.95 (`sync_pending_to_order_tracking`)이 선행 실행되어야 site 데이터 존재

---

## 부록 A: 리뷰 피드백 검증 결과 (v2)

### A.1 store_id 필터 누락 (심각도: 🔴 치명적)
**지적**: SQL에 store_id 필터가 없어 전매장 합산 위험.
**검증 결과**: 매장별 DB 분리(`data/stores/{store_id}.db`)로 기본 격리되나,
46704.db에 46513 매장의 레코드 403건이 오염되어 있음을 확인.
**조치**: 3.3절 SQL에 `AND ot.store_id = :store_id` 추가, 5.5절 안전장치 추가.

### A.2 COUNT vs SUM (심각도: 🟡)
**지적**: COUNT(*)인지 SUM(qty)인지 불명확.
**검증 결과**: `apply_food_daily_cap`이 `len(items)` (SKU 개수)로 비교하므로
site 발주도 **건수(COUNT)** 가 정합. SUM(qty)이면 단위 불일치 발생.
**조치**: 현행 COUNT(*) 유지, 설계 의도 명확화.

### A.3 deduct_manual_food_orders 이중 차감 (심각도: 🟡)
**지적**: `manual_order_items`와 `order_tracking(site)` 겹침 시 이중 차감.
**검증 결과**: 2026-03-05 기준 7건 겹침 확인.
- `manual_order_items`: Phase 1.2에서 일반탭 수동발주 수집
- `order_tracking(site)`: Phase 1.95에서 발주현황(전체) 수집
- 동일 상품이 양쪽에 존재 가능.
**조치**: 3.9절 SQL에 `NOT IN (SELECT item_cd FROM manual_order_items ...)` 추가.

### A.4 order_date 필터 범위 (심각도: 🟡)
**지적**: `order_tracking.order_date`와 `target_date`의 의미 차이.
**검증 결과**:
- `order_tracking.order_date` = **발주일** (오늘, 예: 2026-03-05)
- `apply_food_daily_cap`의 `target_date` = **배송일** (내일, 예: 2026-03-06)
- 원안대로 `order_date = target_date`로 쿼리하면 불일치 발생.
**조치**: 3.3절 파라미터명 `target_date` → `order_date`로 변경,
3.5절 호출부에서 `datetime.now().strftime('%Y-%m-%d')` 사용.
