# Design: new-product-smallcd

## 개요

신제품 초기발주 부스트에서 small_cd(소분류) 기반 유사상품 매칭을 우선 적용하고,
데이터 부족 시 기존 mid_cd(중분류) 폴백하는 2단계 매칭 로직.

## 수정 대상 파일

### 1. `src/application/services/new_product_monitor.py`

#### 1-1. `calculate_similar_avg()` 메서드 변경

**현재 시그니처:**
```python
def calculate_similar_avg(self, item_cd: str, mid_cd: str) -> Optional[float]:
```

**변경 시그니처:**
```python
def calculate_similar_avg(
    self, item_cd: str, mid_cd: str, small_cd: Optional[str] = None
) -> Optional[float]:
```

**변경 로직:**
```
1. mid_cd == "999" → return None (기존 유지)
2. small_cd가 유효하면 (not None, not empty):
   a. product_details JOIN으로 같은 mid_cd + small_cd 상품 조회
   b. 결과 >= 3개 → 중위값 반환
   c. 결과 < 3개 → 3단계로 폴백
3. small_cd 무효 또는 폴백:
   a. 기존 로직 (mid_cd 전체 상품 조회)
   b. 중위값 반환
```

**SQL 변경 (small_cd 우선 쿼리):**
```sql
SELECT p.item_cd,
       COALESCE(SUM(ds.sell_qty), 0) as total_sales,
       COUNT(DISTINCT ds.sales_date) as data_days
FROM common.products p
JOIN common.product_details pd ON p.item_cd = pd.item_cd
LEFT JOIN daily_sales ds
    ON p.item_cd = ds.item_cd
    AND ds.sales_date BETWEEN ? AND ?
WHERE p.mid_cd = ?
  AND pd.small_cd = ?
  AND p.item_cd != ?
GROUP BY p.item_cd
HAVING data_days > 0
```

#### 1-2. `update_lifecycle_status()` 내 `calculate_similar_avg()` 호출부 변경

**현재:**
```python
similar_avg = self.calculate_similar_avg(
    item_cd, item.get("mid_cd", "999")
)
```

**변경:**
```python
small_cd = self._get_small_cd(item_cd)
similar_avg = self.calculate_similar_avg(
    item_cd, item.get("mid_cd", "999"), small_cd=small_cd
)
```

#### 1-3. 신규 private 메서드 `_get_small_cd()`

```python
def _get_small_cd(self, item_cd: str) -> Optional[str]:
    """상품의 소분류 코드 조회 (product_details.small_cd)"""
```

common.db의 product_details에서 해당 item_cd의 small_cd를 조회하여 반환.
없으면 None 반환.

### 2. `src/prediction/improved_predictor.py`

#### 2-1. `_load_new_product_cache()` 변경

캐시 로딩 시 각 아이템의 small_cd도 함께 조회하여 캐시에 포함.

**변경:** 캐시 로딩 후 product_details에서 small_cd 배치 조회하여 병합.

```python
def _load_new_product_cache(self) -> None:
    # ... 기존 로직 ...
    # 캐시 로딩 후 small_cd 병합
    if self._new_product_cache:
        self._enrich_cache_with_small_cd()
```

#### 2-2. 신규 private 메서드 `_enrich_cache_with_small_cd()`

```python
def _enrich_cache_with_small_cd(self) -> None:
    """캐시에 small_cd 정보 추가 (product_details에서 배치 조회)"""
```

ProductDetailRepository에서 배치 조회하여 캐시의 각 아이템에 small_cd 필드 추가.

#### 2-3. `_apply_new_product_boost()` 변경

**현재:**
```python
boosted = max(similar_avg * 0.7, prediction)
```

**변경:** 로직 자체는 동일. 로그 메시지에 small_cd 정보 추가.

```python
small_cd = np_info.get("small_cd")
# ... (보정 로직 동일) ...
logger.info(
    f"[신제품보정] {item_cd}: {order_qty}->{new_order} "
    f"(유사avg={similar_avg:.1f}, small_cd={small_cd})"
)
```

## 폴백 로직 상세

```
┌─────────────────────────┐
│ small_cd 존재?           │
├────┬────────────────────┤
│ No │ → mid_cd 전체 조회  │
├────┘                    │
│ Yes                     │
│   ┌─────────────────────┤
│   │ small_cd 내 상품     │
│   │ >= 3개?              │
│   ├────┬────────────────┤
│   │ No │ → mid_cd 폴백   │
│   ├────┘                │
│   │ Yes → small_cd 중위값│
│   └─────────────────────┘
```

## DB 변경

없음. product_details.small_cd 컬럼과 인덱스 이미 존재.

## 테스트 설계 (tests/test_new_product_smallcd.py)

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | test_smallcd_similar_avg | small_cd 기반 유사상품 중위값 정확성 |
| 2 | test_smallcd_fallback_insufficient | small_cd 내 < 3개 → mid_cd 폴백 |
| 3 | test_smallcd_fallback_null | small_cd=NULL → mid_cd 폴백 |
| 4 | test_smallcd_boost_applied | small_cd 기반 similar_avg로 부스트 적용 |
| 5 | test_smallcd_boost_with_midcd_fallback | 폴백 시 mid_cd 기반 부스트 적용 |
| 6 | test_smallcd_cache_enrichment | 캐시에 small_cd 정보 포함 확인 |
| 7 | test_smallcd_mixed_scenario | small_cd 있는 상품 + 없는 상품 혼합 |
| 8 | test_get_small_cd_exists | _get_small_cd 정상 조회 |
| 9 | test_get_small_cd_not_found | _get_small_cd 미등록 → None |
| 10 | test_smallcd_only_same_category | small_cd 매칭 시 다른 small_cd 배제 확인 |
| 11 | test_smallcd_empty_string | small_cd="" → mid_cd 폴백 |
| 12 | test_boost_log_includes_smallcd | 로그 메시지에 small_cd 포함 확인 |
