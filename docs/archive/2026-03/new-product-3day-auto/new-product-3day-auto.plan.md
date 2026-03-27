# Plan: new-product-3day-auto

> **Date**: 2026-03-24 | **Status**: Draft | **Priority**: High

## 1. Problem Statement

### 현상
3일발주 미달성(mids) 상품의 `is_ordered=0` — 3매장 전부 **자동발주 0건**.

| 매장 | mids 총 | 발주됨 | 미발주 | W13 달성률 |
|:----:|:------:|:-----:|:------:|:---------:|
| 46513 | 46 | 0 | **46** | 14.2% |
| 46704 | 49 | 0 | **49** | 57.1% |
| 47863 | 56 | 0 | **56** | 14.2% |

46513은 달성률 하락으로 지원금 **4만원 감소** (150,000→110,000).

### 현재 상태
- `NEW_PRODUCT_MODULE_ENABLED = True` — 수집+모니터링 **활성**
- `NEW_PRODUCT_AUTO_INTRO_ENABLED = False` — 미도입 자동발주 **보류**
- 3일발주 후속 로직(`_process_3day_follow_orders`) — 코드 **완성**, 하지만 tracking 테이블 0건
- Blocker: **신제품 유통기한 매핑 미구현** → shelf_life_days 없이 과발주 판단 불가

### 기존 코드 현황
```
auto_order.py: _process_3day_follow_orders()  ← 완성
convenience_order_scheduler.py: plan/should_order  ← 완성
new_product_order_service.py: merge_with_ai  ← 완성
np_3day_tracking_repo.py: CRUD  ← 완성
new_product_3day_tracking 테이블  ← 정의됨, 0건
```

**코드는 전부 완성**됐지만, tracking 테이블에 데이터가 없어서 Phase B가 작동 안 함.

## 2. Root Cause Analysis

### 왜 tracking 데이터가 0건인가?

tracking 데이터 생성 경로:
```
daily_job.py Phase 1.3: BGF 수집 → new_product_items(mids) 저장
                         ↓
auto_order.py: _process_3day_follow_orders()
  Phase A: new_product_items(mids) 조회 → placed > 0만 대상
                                          ↑↑↑ placed=0이면 스킵!
  Phase B: new_product_3day_tracking 조회 → 0건 (생성 안 됨)
```

**Phase A에서 `placed > 0` 조건**: BGF의 `ds_yn` 파싱 결과 `placed` = 이미 발주한 횟수.
- `ds_yn = "1/3(미달성)"` → placed=1 (BGF에서 1회는 발주됨)
- `ds_yn = "0/3"` → placed=0 → **Phase A 스킵**

현재 DB의 mids 상품들은 `ds_yn = "1/3(미달성)"` 또는 `"2/3(미달성)"` → placed > 0.
하지만 **tracking 테이블에 INSERT하는 로직이 Phase A에서 누락**됐거나, Phase A가 실행되지 않는 조건이 있음.

### 진짜 Blocker: shelf_life_days

`convenience_order_scheduler.should_order_today()`의 과발주 방지:
```python
overstock_threshold = shelf_life_days * daily_avg_sales * 1.5
if current_stock > overstock_threshold:
    return False  # 스킵
```

신제품은 `product_details.expiration_days`가 없을 수 있음 → shelf_life_days=None → 계산 불가.

## 3. Proposed Fix

### Fix A: shelf_life_days 폴백 (blocker 해소)

신제품 유통기한 결정 우선순위:
1. `product_details.expiration_days` (입고 시 자동 등록)
2. `detected_new_products.mid_cd` → 카테고리 기본값 (`FOOD_EXPIRY_FALLBACK`)
3. 하드코딩 기본값: **30일** (비식품 안전 기본값)

**파일**: `convenience_order_scheduler.py` `should_order_today()`

```python
# shelf_life_days가 None이면 카테고리 폴백 → 기본 30일
if shelf_life_days is None or shelf_life_days <= 0:
    from src.prediction.categories.food import FOOD_EXPIRY_FALLBACK
    shelf_life_days = FOOD_EXPIRY_FALLBACK.get(mid_cd, 30)
```

### Fix B: tracking 테이블 초기 데이터 생성

현재 `_process_3day_follow_orders` Phase A에서 mids 상품을 조회하지만 tracking에 INSERT하지 않음.
→ Phase A에서 mids 상품을 tracking 테이블에 초기 등록하는 로직 추가.

**파일**: `auto_order.py` `_process_3day_follow_orders()` 또는 `new_product_order_service.py`

```python
# Phase A에서 mids 조회 후, tracking 미등록 상품을 자동 등록
for item in mids_items:
    if not tracking_repo.exists(store_id, week_label, base_name):
        tracking_repo.create_initial(
            store_id, week_label, week_start, week_end,
            product_code, product_name, base_name,
            bgf_order_count=placed,  # BGF에서 이미 발주한 횟수
            our_order_count=0,
            order_interval=plan_3day_orders(...)
        )
```

### Fix C: daily_avg_sales 추정 (신상품)

판매 이력 없는 신상품의 daily_avg_sales:
1. `detected_new_products.similar_item_avg` (유사상품 기반, 이미 구현됨)
2. mid_cd 중위값 (`NEW_PRODUCT_INTRO_ORDER_QTY` = 1 폴백)
3. 기본값: **0.5** (보수적)

### Fix D: `NEW_PRODUCT_AUTO_INTRO_ENABLED` 활성화 불필요

**mids(3일 미달성)는 AUTO_INTRO와 별개**. mids는 이미 BGF에서 도입된 상품이므로:
- AUTO_INTRO_ENABLED = False 유지 (미도입 자동발주는 보류)
- MODULE_ENABLED = True (수집+모니터링 유지)
- **3일발주 후속 관리만 활성화** → 별도 토글 불필요 (코드 경로가 이미 분리됨)

## 4. Implementation Plan

| 순서 | 작업 | 파일 | 예상 |
|:----:|------|------|:----:|
| 1 | shelf_life_days 폴백 로직 | convenience_order_scheduler.py | 5분 |
| 2 | daily_avg_sales 추정 로직 | convenience_order_scheduler.py | 5분 |
| 3 | tracking 초기 데이터 생성 | auto_order.py / new_product_order_service.py | 15분 |
| 4 | _process_3day_follow_orders 디버그 | auto_order.py | 10분 |
| 5 | 테스트 + 시뮬레이션 | | 10분 |

## 5. Verification

- mids 상품이 tracking 테이블에 등록되는지 확인
- should_order_today()가 shelf_life_days=None에서도 작동하는지
- 과발주 방지 (stock > threshold 시 스킵)
- food_daily_cap과의 충돌 없는지 (np_3day 보호 로직 확인)
- 기존 테스트 통과

## 6. Expected Impact

| 매장 | 현재 달성률 | 예상 달성률 | 점수 변화 | 지원금 변화 |
|:----:|:---------:|:---------:|:--------:|:----------:|
| 46513 | 14.2% | ~70%+ | 81→~95 | 110,000→**160,000** |
| 46704 | 57.1% | ~80%+ | 60→~80 | 0→**110,000** |
| 47863 | 14.2% | ~50%+ | 33→~50 | 0→**40,000** |

## 7. Risk Assessment

| 리스크 | 확률 | 대응 |
|--------|:----:|------|
| 신상품 과발주 (유통기한 짧은 상품) | 중간 | 과발주 방지 threshold + food_daily_cap 이중 보호 |
| BGF 발주가능 상태 불일치 | 낮음 | ord_pss_nm 검사 유지 |
| 기존 site 발주와 중복 | 낮음 | tracking our_order_count + pending 체크 |
