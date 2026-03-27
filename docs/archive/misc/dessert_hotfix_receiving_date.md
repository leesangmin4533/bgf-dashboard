# 디저트 발주 판단 시스템 — 수정 명세서

**이슈**: 기존 운영 상품 즉시 적용을 위한 첫 입고일 해석 전략 변경
**근거**: 실데이터 조사 결과 (2026-03-04)
**영향 범위**: lifecycle.py, dessert_decision_service.py

---

## 배경 (데이터 조사 결과 요약)

| 항목 | 결과 |
|---|---|
| detected_new_products 디저트 건수 | **0건** (1순위 소스 사용 불가) |
| daily_sales 범위 | 2025-08-09 ~ 2026-03-04 (7개월) |
| daily_sales 커버리지 (sale_qty>0) | 133개 / 174개 (76%) |
| daily_sales 커버리지 (buy_qty>0) | 63개 / 174개 (36%) |
| products.created_at 오염도 | 154개/174개(89%)가 3개 날짜에 집중 → 생애주기 판별 불가 |
| 판매 이력 없는 상품 | 41개 (미입고 또는 발주불가 상태) |

---

## 수정 1: 첫 입고일 해석 우선순위 변경

### 파일: `dessert_decision_service.py`
### 메서드: `_resolve_first_receiving_date()`

#### 기존 로직

```python
# 1순위: detected_new_products.first_receiving_date
# 2순위: MIN(daily_sales.sale_date)
# 3순위: products.created_at
```

#### 변경 로직

```python
# 1순위: detected_new_products.first_receiving_date
#   - 디저트 현재 0건이지만, 향후 신규 입고 시 작동하므로 유지
#
# 2순위: MIN(daily_sales.sale_date) WHERE sale_qty > 0
#   - 첫 판매일 (133개/174개 커버, 76%)
#   - 실질적으로 현재 기존 상품의 1순위 소스
#
# 2-1순위 (신규): MIN(daily_sales.sale_date) WHERE buy_qty > 0 AND sale_qty = 0
#   - 입고는 있었으나 아직 판매 없는 상품용
#   - 2순위에서 못 잡힌 상품 중 입고 이력이 있는 경우 보완
#
# 3순위: products.created_at
#   - 최후 폴백
#   - 단, created_at이 일괄등록일(2026-01-25, 2026-01-26, 2026-02-06)인 경우
#     → first_receiving_source를 'products_bulk'로 표기
#     → 생애주기를 'established'로 강제 (신상품으로 오판 방지)
```

#### 변경 상세

```python
def _resolve_first_receiving_date(self, item_cd: str) -> tuple[str | None, str]:
    """
    Returns: (first_date_str, source_name)
    source_name: 'detected_new_products' | 'daily_sales_sold' | 'daily_sales_bought' | 'products' | 'products_bulk' | None
    """
    
    # 1순위: detected_new_products (기존 유지)
    date = self._query_detected_new_products(item_cd)
    if date:
        return date, 'detected_new_products'
    
    # 2순위: 첫 판매일
    date = self._query_first_sale_date(item_cd)  # MIN(sale_date) WHERE sale_qty > 0
    if date:
        return date, 'daily_sales_sold'
    
    # 2-1순위 (신규): 첫 입고일 (판매 없는 상품)
    date = self._query_first_buy_date(item_cd)   # MIN(sale_date) WHERE buy_qty > 0
    if date:
        return date, 'daily_sales_bought'
    
    # 3순위: products.created_at
    date = self._query_products_created_at(item_cd)
    if date:
        # 일괄등록일 판별
        BULK_DATES = {'2026-01-25', '2026-01-26', '2026-02-06'}
        if date[:10] in BULK_DATES:
            return date, 'products_bulk'   # 생애주기에서 특별 처리
        return date, 'products'
    
    return None, 'none'
```

#### 신규 쿼리 메서드 추가

```python
def _query_first_buy_date(self, item_cd: str) -> str | None:
    """daily_sales에서 buy_qty > 0인 최초 날짜 조회"""
    sql = """
        SELECT MIN(sale_date) FROM daily_sales 
        WHERE item_cd = ? AND mid_cd = '014' AND buy_qty > 0
    """
    # 기존 _query_first_sale_date()와 동일 패턴
```

---

## 수정 2: 생애주기 판별에서 일괄등록 상품 처리

### 파일: `lifecycle.py` (또는 `dessert_decision_service.py`에서 호출 시)
### 함수: `determine_lifecycle()`

#### 기존 로직

```python
def determine_lifecycle(first_date, ref_date, category) -> tuple[DessertLifecycle, int]:
    weeks = calc_weeks_since(first_date, ref_date)
    # weeks 기반으로 new/growth_decline/established 판별
```

#### 변경 로직

```python
def determine_lifecycle(first_date, ref_date, category, source=None) -> tuple[DessertLifecycle, int]:
    """
    source 파라미터 추가:
    - source='products_bulk' → 무조건 established, weeks=999 (일괄등록 오염)
    - source='none' → 판단 보류 (SKIP)
    - 그 외 → 기존 로직 유지
    """
    if source == 'products_bulk':
        return DessertLifecycle.ESTABLISHED, 999
    
    if source == 'none':
        return None, 0  # 호출부에서 SKIP 처리
    
    weeks = calc_weeks_since(first_date, ref_date)
    # 이하 기존 로직 동일
```

---

## 수정 3: 판매 이력 없는 상품 판단 보류

### 파일: `dessert_decision_service.py`
### 메서드: 메인 판단 루프 (run 또는 _process_item)

#### 추가 로직

```python
# 판단 루프 내에서:

first_date, source = self._resolve_first_receiving_date(item_cd)

# 케이스 1: 소스가 없음 (판매도 입고도 created_at도 없음)
# → 판단 대상 제외, 로그만 기록
if source == 'none':
    logger.info(f"[SKIP] {item_cd} {item_nm}: 첫 입고일 판별 불가, 판단 보류")
    continue

# 케이스 2: 생애주기 판별
lifecycle, weeks = determine_lifecycle(first_date, ref_date, category, source)

if lifecycle is None:
    logger.info(f"[SKIP] {item_cd} {item_nm}: 생애주기 판별 불가, 판단 보류")
    continue

# 케이스 3: 정상 판단 진행
# → 이하 기존 로직 동일
```

#### 판매 이력 없는 41개 상품의 향후 처리

```
첫 판매 발생 시:
  → 다음 스케줄러 실행에서 MIN(daily_sales.sale_date)로 첫 판매일이 잡힘
  → source='daily_sales_sold'
  → determine_lifecycle()에서 정상적으로 'new'(신상품)로 판별
  → 이후 일반 흐름 진행
```

---

## 수정 4: dessert_decisions 테이블 기록 보완

### 파일: `dessert_decision_repo.py`

`first_receiving_source` 컬럼에 새로운 값이 추가되므로 확인:

| 값 | 설명 | 기존/신규 |
|---|---|---|
| `detected_new_products` | 센터매입 감지 시스템 | 기존 |
| `daily_sales` | 첫 판매일 역추정 | 기존 (→ `daily_sales_sold`로 변경) |
| `daily_sales_sold` | 첫 판매일 (sale_qty>0) | **신규** (기존 `daily_sales` 대체) |
| `daily_sales_bought` | 첫 입고일 (buy_qty>0, 판매 없음) | **신규** |
| `products` | DB 등록일 (정상) | 기존 |
| `products_bulk` | DB 등록일 (일괄등록 오염) | **신규** |

기존에 `daily_sales`로 저장된 레코드가 있다면 `daily_sales_sold`와 동일 의미이므로 하위 호환됩니다. 신규 저장분부터 세분화된 값을 사용합니다.

---

## 수정 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `dessert_decision_service.py` | `_resolve_first_receiving_date()` 우선순위 변경, `_query_first_buy_date()` 추가, 판단 루프에 SKIP 분기 추가 |
| `lifecycle.py` | `determine_lifecycle()` source 파라미터 추가, products_bulk/none 처리 |
| `dessert_decision_repo.py` | `first_receiving_source` 값 세분화 확인 (DDL 변경 없음) |

---

## 영향 분석

| 항목 | 영향 |
|---|---|
| DB 스키마 | 변경 없음 (first_receiving_source는 TEXT 컬럼) |
| 기존 테스트 | `determine_lifecycle()` 시그니처 변경 → 기존 호출부에 `source=None` 기본값이므로 기존 테스트 통과 |
| 신규 테스트 추가 | `_resolve_first_receiving_date()` 5개 분기 테스트, `determine_lifecycle()` products_bulk/none 테스트 (~8개 추가) |
| OrderFilter | 영향 없음 |
| 스케줄러 | 영향 없음 |
| API | 영향 없음 |

---

## 적용 후 예상 결과 (174개 상품)

| 그룹 | 수량 | 생애주기 | 동작 |
|---|---|---|---|
| 첫 판매일 있음, 8주+ 경과 | ~100개 | established | 즉시 정착기 기준으로 판단 |
| 첫 판매일 있음, 8주 미만 | ~33개 | new 또는 growth_decline | 실제 경과 기간에 맞는 기준 적용 |
| 판매 없음 + 입고만 있음 | ~수 개 | new (입고일 기준) | 입고일 기준 생애주기 |
| 판매·입고 없음 + 일괄등록 | ~35개 | established (강제) | products_bulk로 표기, 정착기 기준 |
| 판매·입고·등록일 없음 | ~3개 | SKIP | 판단 보류, 첫 판매 시 신상품으로 시작 |
