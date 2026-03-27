# Design: 전품목 발주가능요일 체크 + 실시간 검증/DB 교정

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | orderable-day-all |
| 작성일 | 2026-02-25 |
| 변경 파일 | 4개 (`improved_predictor.py`, `auto_order.py`, `order_executor.py`, `product_detail_repo.py`) |
| 테스트 | `tests/test_orderable_day_all.py` — 34개 |

### 배경

기존 orderable_day 체크는 스낵류(015~030)와 라면(006, 032)에만 적용.
담배, 주류, 생활용품, 잡화 등 나머지 카테고리는 비발주일에도 발주가 시도되어 BGF 사이트에서 거부됨.
또한 DB의 orderable_day 정보가 실제 BGF 데이터와 불일치할 수 있어 검증/교정이 필요.

### 목표

1. **개선 A**: 푸드(001~005, 012) 제외 전품목에 orderable_day 기반 비발주일 스킵 적용
2. **개선 B**: 자동발주 실행 중 비발주일 스킵 상품의 상세팝업을 열어 실제 orderable_day 수집 → DB 검증/교정 → 실제 발주가능 상품 복구 발주

---

## 2. 변경 파일 상세

### 변경 파일 (4개)

| # | 파일 | 개선 항목 |
|---|------|----------|
| 1 | `src/prediction/improved_predictor.py` | A. 전품목 orderable_day 스킵 |
| 2 | `src/order/auto_order.py` | B. 발주 목록 분리 + 검증/교정/복구 발주 |
| 3 | `src/order/order_executor.py` | B. 검증 전용 상품정보 수집 메서드 |
| 4 | `src/infrastructure/database/repos/product_detail_repo.py` | B. orderable_day 단일 업데이트 |

### 변경하지 않는 파일

| 파일 | 이유 |
|------|------|
| `src/prediction/categories/snack_confection.py` | `_is_orderable_today()` 기존 함수 재사용만 (수정 없음) |
| `src/prediction/categories/food.py` | `is_food_category()` 기존 함수 재사용만 (수정 없음) |
| `src/prediction/categories/ramen.py` | 기존 비발주일 스킵 로직 그대로 유지 |
| 개별 카테고리 모듈 (beer, tobacco, soju 등) | 공통 체크를 improved_predictor에서 처리 |
| `src/db/models.py` | 스키마 변경 없음 (product_details.orderable_day 이미 존재) |

> **푸드류 면제 사유**: 001~005, 012는 유통기한 1~3일로 매일 발주 필수.
> orderable_day 체크 적용 시 품절 위험.

---

## 3. 개선 A: 전품목 orderable_day 스킵

### 3.1 변경: `improved_predictor.py`

#### ctx 초기화 (line 1552)

```python
# 추가
"orderable_day_skip": False,
```

#### 공통 orderable_day 체크 블록 (line 1903~1914)

**삽입 위치**: 모든 카테고리별 분기 이후, 카테고리별 상한선 체크 앞

```python
# ===== 전품목 orderable_day 체크 (푸드 면제) =====
ORDERABLE_DAY_EXEMPT_MIDS = {"001", "002", "003", "004", "005", "012"}
if mid_cd not in ORDERABLE_DAY_EXEMPT_MIDS:
    from src.prediction.categories.snack_confection import _is_orderable_today
    item_orderable_day = product.get("orderable_day") or DEFAULT_ORDERABLE_DAYS
    if not _is_orderable_today(item_orderable_day):
        if not (ctx.get("ramen_skip_order") or new_cat_skip_order):
            logger.info(
                f"[비발주일] {product['item_nm']} ({item_cd}): "
                f"orderable_day={item_orderable_day} -> 오늘 발주 스킵"
            )
        ctx["orderable_day_skip"] = True
```

#### need_qty=0 적용 (line 1932~1933)

기존 skip_order 블록들 뒤에 추가:

```python
if ctx.get("orderable_day_skip"):
    need_qty = 0
```

### 3.2 설계 포인트

**중복 스킵 방지**: 라면/스낵은 각 카테고리 모듈(`ramen.py`, `snack_confection.py`)에서 이미 `skip_order=True`를 설정. 공통 블록에서 로그만 중복 방지 (`if not (ctx.get("ramen_skip_order") or new_cat_skip_order)`), `orderable_day_skip` 플래그 자체는 항상 설정하여 안전망 역할.

**면제 카테고리 정의**: `ORDERABLE_DAY_EXEMPT_MIDS` 상수를 함수 내부에 정의. 전역 상수보다 변경 범위가 명확하고, 이 블록에서만 사용되므로 적절.

### 3.3 영향 분석

**변경 전 (비발주일 상품):**
```
담배(072) 월~토 발주가능, 일요일:
  → orderable_day 체크 없음
  → need_qty = 양수
  → BGF 사이트에서 발주 실패/거부
```

**변경 후:**
```
담배(072) 월~토 발주가능, 일요일:
  → ORDERABLE_DAY_EXEMPT_MIDS 미포함 → 체크 진입
  → _is_orderable_today("월화수목금토") → False (일요일)
  → ctx["orderable_day_skip"] = True
  → need_qty = 0 → 발주 스킵
```

**영향받는 카테고리 예시:**

| mid_cd | 카테고리 | 기존 | 변경 후 |
|--------|----------|------|---------|
| 072 | 담배 | orderable_day 무시 | 비발주일 자동 스킵 |
| 049 | 맥주 | orderable_day 무시 | 비발주일 자동 스킵 |
| 050 | 소주 | orderable_day 무시 | 비발주일 자동 스킵 |
| 044 | 생활용품 | orderable_day 무시 | 비발주일 자동 스킵 |
| 013 | 디저트 | orderable_day 무시 | 비발주일 자동 스킵 |
| 001~005 | 푸드 | 매일 발주 | **면제** (변경 없음) |
| 012 | 푸드(기타) | 매일 발주 | **면제** (변경 없음) |
| 015~030 | 스낵 | 카테고리 내 체크 | 기존+공통 이중 안전망 |
| 006, 032 | 라면 | 카테고리 내 체크 | 기존+공통 이중 안전망 |

---

## 4. 개선 B: 발주 목록 분리 + 실시간 검증/DB 교정

### 4.1 전체 흐름

```
execute() 진입
    ↓
[Phase A-0] 발주 목록 분리
    ├── 푸드: → orderable_today_list (무조건)
    ├── _is_orderable_today(od) == True: → orderable_today_list
    └── _is_orderable_today(od) == False: → skipped_for_verify
    ↓
[Phase A] orderable_today_list → execute_orders() (정상 발주)
    ↓
[Phase B] skipped_for_verify → _verify_and_rescue_skipped_items()
    ├── 상품별: collect_product_info_only(item_cd)
    │     ├── 상품코드 입력 → Enter
    │     ├── 그리드에서 상품정보 읽기
    │     ├── DB 저장 (최신값)
    │     └── 배수 0 입력 (발주 취소)
    ├── 비교: set(DB orderable_day) vs set(실제 orderable_day)
    │     ├── 불일치: update_orderable_day() → DB 교정
    │     │     ├── 오늘 발주가능: rescue_list에 추가
    │     │     └── 오늘 비발주일: 스킵 유지
    │     └── 일치: 스킵 유지
    └── rescue_list → execute_orders() (복구 발주)
    ↓
결과 병합 → 반환
```

### 4.2 변경: `auto_order.py` — execute() 메서드 (line 1270~1320)

#### 발주 목록 분리 (line 1270~1292)

```python
# ===== 전품목 발주가능요일 분리 (푸드 면제) =====
from src.prediction.categories.snack_confection import _is_orderable_today
from src.prediction.categories.food import is_food_category

orderable_today_list = []     # 오늘 발주 가능
skipped_for_verify = []       # 비발주일 → 검증 대상

for item in order_list:
    mid_cd = item.get("mid_cd", "")
    if is_food_category(mid_cd):
        orderable_today_list.append(item)  # 푸드는 항상 발주
        continue
    od = item.get("orderable_day") or DEFAULT_ORDERABLE_DAYS
    if _is_orderable_today(od):
        orderable_today_list.append(item)
    else:
        skipped_for_verify.append(item)
```

**설계 근거**: `improved_predictor`에서 `need_qty=0`으로 스킵해도, auto_order의 `_recalculate_need_qty()`에서 need_qty가 재계산될 수 있음. 따라서 발주 실행 직전에도 한 번 더 분리하여 이중 안전망 확보.

#### Phase A: 정상 발주 (line 1294~1304)

```python
if orderable_today_list:
    result = self.executor.execute_orders(
        order_list=orderable_today_list,
        target_date=target_date,
        dry_run=dry_run
    )
else:
    result = {"success": True, "success_count": 0, "fail_count": 0, "results": []}
```

#### Phase B: 검증 + 복구 발주 (line 1306~1320)

```python
# dry_run이 아닐 때만 실행 (실제 BGF 사이트 접근 필요)
if skipped_for_verify and not dry_run and self.executor:
    rescued = self._verify_and_rescue_skipped_items(skipped_for_verify)
    if rescued:
        rescue_result = self.executor.execute_orders(
            order_list=rescued,
            target_date=target_date,
            dry_run=dry_run
        )
        # 결과 병합
        result["success_count"] += rescue_result.get("success_count", 0)
        result["fail_count"] += rescue_result.get("fail_count", 0)
        result.setdefault("results", []).extend(rescue_result.get("results", []))
```

**dry_run 제외**: 검증은 실제 BGF 사이트 상세팝업을 열어야 하므로 dry_run 시 불가.

### 4.3 신규 메서드: `_verify_and_rescue_skipped_items()` (line 1765~1846)

```python
def _verify_and_rescue_skipped_items(
    self, skipped_items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
```

**로직**:
1. `executor.navigate_to_single_order()`로 단품별 발주 메뉴 이동
2. 각 스킵 상품에 대해 `executor.collect_product_info_only(item_cd)` 호출
3. `set()` 비교로 DB vs 실제 orderable_day 비교 (문자 순서 무시)
4. 불일치 시: `_product_repo.update_orderable_day()` 호출하여 DB 교정
5. 교정 후 `_is_orderable_today(actual_orderable_day)` → True면 rescue_list에 추가
6. 결과 요약 로그 출력

**set 비교 근거**: orderable_day가 "화목토" 또는 "토목화"로 저장될 수 있음. 문자 집합으로 비교하여 순서 무관하게 일치 판단.

### 4.4 신규 메서드: `order_executor.py` — `collect_product_info_only()` (line 2230~2318)

**기존 `input_product()` (line 900~1074)와의 차이**:

| 단계 | input_product() | collect_product_info_only() |
|------|:---------------:|:--------------------------:|
| 상품코드 입력 | O | O (동일 로직 재사용) |
| Enter 검색 | O | O |
| 로딩 대기 | O | O |
| 그리드 읽기 | O | O (`_read_product_info_from_grid()`) |
| DB 저장 | O | O (`save_to_db()`) |
| 배수 입력 | 실제 발주 배수 | **0** (발주 취소) |
| orderable_day 검증 | O | O (`_verify_orderable_day()`) |

**핵심**: 기존 `input_product()`의 1~3단계를 그대로 재사용하되, 4단계(배수 입력)에서 0을 입력하여 실제 발주를 하지 않음.

```python
# 5. 배수 0 입력 (발주 취소)
actions = ActionChains(self.driver)
actions.send_keys(Keys.TAB)     # 배수 셀로 이동
actions.perform()
time.sleep(0.3)
actions = ActionChains(self.driver)
actions.send_keys("0")
actions.send_keys(Keys.ENTER)
actions.perform()
```

### 4.5 신규 메서드: `product_detail_repo.py` — `update_orderable_day()` (line 339~363)

```python
def update_orderable_day(self, item_cd: str, orderable_day: str) -> bool:
    """orderable_day만 업데이트 (검증 교정용)"""
    conn = self._get_conn()
    try:
        cursor = conn.cursor()
        now = self._now()
        cursor.execute(
            "UPDATE product_details SET orderable_day = ?, updated_at = ? WHERE item_cd = ?",
            (orderable_day, now, item_cd)
        )
        conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info(f"[DB교정] {item_cd}: orderable_day → {orderable_day}")
        return updated
    finally:
        conn.close()
```

**설계 근거**: 기존 `save()` 메서드는 전체 필드를 UPSERT하므로, orderable_day만 교정할 때 다른 필드가 덮어씌워질 위험. 단일 필드 UPDATE로 안전하게 교정.

---

## 5. 함수 의존 관계

```
auto_order.execute()
  ├── snack_confection._is_orderable_today()    # 재사용
  ├── food.is_food_category()                    # 재사용
  ├── executor.execute_orders()                  # 기존
  └── auto_order._verify_and_rescue_skipped_items()   # 신규
        ├── executor.navigate_to_single_order()       # 기존
        ├── executor.collect_product_info_only()       # 신규
        │     ├── executor._click_last_row_item_cell() # 기존
        │     ├── executor._input_product_code_optimized() # 기존
        │     ├── executor._wait_for_loading_complete()    # 기존
        │     ├── executor._read_product_info_from_grid()  # 기존
        │     ├── executor._verify_orderable_day()         # 기존
        │     └── product_collector.save_to_db()           # 기존
        ├── product_detail_repo.update_orderable_day()     # 신규
        └── snack_confection._is_orderable_today()         # 재사용

improved_predictor._calculate_safety_stock_and_need()
  └── snack_confection._is_orderable_today()    # 재사용
```

---

## 6. 에러 처리

| 상황 | 처리 |
|------|------|
| executor 없음 | `_verify_and_rescue_skipped_items()` 즉시 빈 리스트 반환 |
| 단품별 발주 메뉴 이동 실패 | 검증 전체 스킵, 빈 리스트 반환 |
| 개별 상품 수집 실패 | 해당 상품만 스킵 (continue), 나머지 계속 |
| DB 업데이트 실패 | 경고 로그, 해당 상품 rescue 불가 |
| Alert 팝업 ("없"/"불가") | 상품 없음으로 판단, None 반환 |
| 배수 0 입력 실패 | debug 로그만, 다음 상품 진행 |

---

## 7. 수정하지 않는 파일 확인

| 파일 | 확인 항목 |
|------|----------|
| `snack_confection.py` | `_is_orderable_today()`, `_calculate_order_interval()`은 그대로 재사용. public interface 변경 없음 |
| `ramen.py` | 기존 `skip_order=True` 로직 유지. 공통 블록에서 중복 로그 방지 처리됨 |
| `beer.py`, `tobacco.py`, `soju.py` 등 | 개별 카테고리 모듈 내부 수정 없음. 공통 체크가 improved_predictor에서 처리 |
| `food.py` | `is_food_category()` 기존 함수 그대로 사용 |
| `src/db/models.py` | product_details.orderable_day 컬럼 이미 존재 (스키마 v42) |

---

## 8. 테스트 설계

### 8.1 테스트 파일: `tests/test_orderable_day_all.py` (34개)

| # | 클래스 | 테스트 수 | 검증 대상 |
|---|--------|:---------:|----------|
| 1 | `TestOrderableDaySkipAllCategories` | 13 | 개선 A: 전품목 스킵 + 면제 |
| 2 | `TestOrderListSplit` | 4 | 개선 B: 발주 목록 분리 |
| 3 | `TestVerifyAndRescue` | 6 | 개선 B: DB 교정 + 복구 |
| 4 | `TestUpdateOrderableDay` | 3 | DB 메서드 단위 테스트 |
| 5 | `TestCollectProductInfoOnly` | 3 | Executor 수집 메서드 |
| 6 | `TestIntegrationScenarios` | 5 | 전체 흐름 통합 |

### 8.2 핵심 시나리오

**개선 A 테스트:**

| 테스트 | mid_cd | orderable_day | 오늘 | 기대 |
|--------|--------|:-------------:|:----:|:----:|
| tobacco_skip | 072 | 월화수목금토 | 일 | skip |
| beer_skip | 049 | 화목토 | 월 | skip |
| soju_skip | 051 | 월수금 | 화 | skip |
| daily_necessity_skip | 044 | 화목 | 월 | skip |
| general_merchandise_skip | 040 | 월수금 | 화 | skip |
| food_exempt_001 | 001 | 월화수목금토 | 일 | **면제** (skip 안함) |
| food_exempt_002 | 002 | 월수금 | 화 | **면제** |
| food_exempt_012 | 012 | 월화수 | 목 | **면제** |
| dessert_not_exempt | 013 | 월수금 | 화 | skip (면제 아님) |
| orderable_today | 072 | 월화수목금토 | 월 | 발주 진행 |

**개선 B 테스트:**

| 시나리오 | DB | 실제 | 결과 |
|----------|:--:|:----:|------|
| 불일치 + 오늘 발주가능 | 화목토 | 월화수목금토 | DB 교정 + rescue |
| 불일치 + 오늘도 비발주일 | 화목 | 월수금 | DB 교정 + 스킵 유지 |
| 일치 | 화목토 | 화목토 | 스킵 유지 |
| 팝업 실패 | 화목토 | None | 스킵 유지 |
| set 비교 순서 무시 | 화목토 | 토목화 | 일치 판정 |

### 8.3 기존 테스트 호환

- `test_snack_orderable_day.py` (26개): 스낵 전용 로직 변경 없음 → 통과
- `test_ramen_orderable_day.py` (20개): 라면 전용 로직 변경 없음 → 통과
- `conftest.py`: 변경 불필요 (기존 테이블 스키마 사용)
- **전체 테스트**: 2116개 통과

---

## 9. 구현 순서

```
1. improved_predictor.py: ctx 초기화 + 공통 orderable_day 스킵 블록 (개선 A)
      ↓
2. product_detail_repo.py: update_orderable_day() 추가 (개선 B 전제조건)
      ↓
3. order_executor.py: collect_product_info_only() 추가 (개선 B 전제조건)
      ↓
4. auto_order.py: 발주 목록 분리 + _verify_and_rescue_skipped_items() (개선 B)
      ↓
5. tests/test_orderable_day_all.py: 전체 테스트 작성
      ↓
6. 기존 테스트 전체 실행 → 2116개 통과 확인
```

---

## 10. 롤백 계획

| 단계 | 롤백 방법 | 소요 |
|------|----------|------|
| 개선 A 롤백 | `improved_predictor.py`에서 orderable_day_skip 블록 제거 + ctx 초기화 제거 | 15줄 |
| 개선 B 롤백 | `auto_order.py`에서 분리/검증 로직 제거, `orderable_today_list` → `order_list` 복원 | 60줄 |
| DB 메서드 롤백 | `update_orderable_day()` 제거 (다른 코드에서 미사용이면 안전) | 25줄 |
| Executor 롤백 | `collect_product_info_only()` 제거 | 90줄 |

> **참고**: 개선 A와 B는 독립적. A만 롤백해도 B는 동작 (분리 로직이 자체적으로 orderable_day 체크). B만 롤백해도 A는 동작 (예측 단계에서 need_qty=0 처리).
