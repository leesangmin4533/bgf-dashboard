# CUT 상품 확인 Direct API 전환 설계

> Feature: `cut-check-api-only`
> Date: 2026-02-28
> Phase: Design

---

## 1. 문제 정의

### 현상
- Phase 2 자동발주 시작 시 `CUT 미확인 의심 상품 100개 우선 재조회` 실행
- Direct API(`/stbj030/selSearch`) 배치 호출 → 대부분 SSV 파싱 실패 (95/99건)
- 실패 50건을 Selenium 폴백으로 단품별 발주 화면에서 개별 조회 (~3초/건, ~2.5분 소요)
- **전부 "발주 가능한 상품이 없습니다"** = 발주불가 상품

### 근본 원인
`direct_api_fetcher.py`의 `extract_item_data()` 성공 판정 기준:

```python
# line 132: dsItem에 행이 있어야만 success=True
if ds_item and ds_item['rows']:
    result['success'] = True
```

CUT/미취급 상품은 BGF 서버가 **행 데이터 0개**를 반환 → `success=False` → Selenium 폴백

### 크롬 테스트 검증 결과 (2026-02-28 18:32)

| 구분 | 정상 상품 | CUT/미취급 상품 |
|------|-----------|----------------|
| HTTP 상태 | 200 | 200 |
| 데이터셋 수 | 1 (58 컬럼) | 1 (58 컬럼) |
| **행 수** | **1** | **0** |
| 응답 크기 | ~1650B | ~1345B |
| `CUT_ITEM_YN` | `"0"` | (행 없음) |

**핵심 발견**: SSV 응답에는 `dsItem`과 `gdList`가 별도가 아닌 **하나의 통합 데이터셋** (58개 컬럼).
CUT/미취급 상품은 행 자체가 없으므로, `rowCount == 0`으로 판별 가능.

---

## 2. 설계

### 변경 대상: `direct_api_fetcher.py` > `extract_item_data()`

#### Before
```python
def extract_item_data(parsed, item_cd):
    result = { ..., 'success': False, 'is_cut_item': False }

    ds_item = parsed.get('dsItem')
    if ds_item and ds_item['rows']:      # ← 행이 있어야만 success
        result['success'] = True
        ...

    ds_gdlist = parsed.get('gdList')     # ← 별도 gdList 파싱
    if ds_gdlist and ds_gdlist['rows']:
        result['is_cut_item'] = ...
```

#### After
```python
def extract_item_data(parsed, item_cd):
    result = { ..., 'success': False, 'is_cut_item': False, 'is_empty_response': False }

    ds_item = parsed.get('dsItem')
    if ds_item and ds_item['rows']:
        # 정상 상품: 행 데이터 존재
        result['success'] = True
        ...
    elif ds_item and not ds_item['rows']:
        # CUT/미취급 상품: 헤더만 있고 행이 없음
        result['success'] = True           # ← 성공 처리 (발주불가 확인됨)
        result['is_empty_response'] = True  # ← 빈 응답 플래그
        result['is_cut_item'] = True        # ← 발주불가 → CUT 취급

    # gdList 파싱 (통합 데이터셋이므로 dsItem과 동일할 수 있음)
    ds_gdlist = parsed.get('gdList')
    if ds_gdlist and ds_gdlist['rows']:
        # 행이 있으면 실제 CUT_ITEM_YN 값으로 덮어씀
        result['is_cut_item'] = last_row.get('CUT_ITEM_YN', '0') == '1'
```

### 변경 대상: `order_prep_collector.py` > `_process_api_result()`

빈 응답을 받은 경우에도 결과를 성공으로 반환하되, 호출자가 CUT으로 처리할 수 있도록:

```python
def _process_api_result(self, item_cd, api_data):
    ...
    is_empty = api_data.get('is_empty_response', False)
    if is_empty:
        # 빈 응답 = 발주불가 상품 → 성공 처리 + CUT 마킹
        logger.info(f"[DirectAPI] {item_cd}: 발주불가 (빈 응답)")
        return {
            'item_cd': item_cd,
            'success': True,
            'is_cut_item': True,
            'is_empty_response': True,
            'pending_qty': 0,
            'current_stock': 0,
        }
```

### 변경하지 않는 것

- `auto_order.py`의 `prefetch_pending_quantities()` — 이미 `is_cut_item` 플래그를 처리
- `order_executor.py` — 변경 없음
- Selenium 폴백 로직 — 제거하지 않음 (네트워크 에러 등 진짜 실패 시 필요)

---

## 3. 데이터 흐름

```
auto_order.execute()
  │
  ├─ CUT 의심 상품 100개
  │   └─ prefetch_pending_quantities(suspect_items)
  │       └─ order_prep_collector.collect_for_items()
  │           └─ _collect_via_direct_api()
  │               └─ direct_api_fetcher.fetch_items_batch()
  │                   ├─ HTTP 200, rowCount=1 → success=True, is_cut=False (정상)
  │                   ├─ HTTP 200, rowCount=0 → success=True, is_cut=True  (★ 변경)
  │                   └─ HTTP 에러/타임아웃 → success=False (기존대로 Selenium 폴백)
  │
  ├─ prefetch 결과에서 is_cut_item=True → self._cut_items.add(item_cd)
  │
  └─ 발주 목록에서 CUT 상품 제외
```

---

## 4. 예상 효과

| 항목 | Before | After |
|------|--------|-------|
| CUT 의심 100개 조회 시간 | API 1.2초 + Selenium 폴백 50건 × 3초 = **~152초** | API 1.2초 (폴백 불필요) = **~1.2초** |
| Selenium 폴백 발생 건수 | 50건/100건 (50%) | 네트워크 에러만 (<<5%) |
| 단품별 발주 화면 점유 | prefetch 중 ~2.5분 점유 | 점유 없음 |

---

## 5. 에러 처리

| 상황 | 처리 |
|------|------|
| HTTP 200 + 행 0개 | `success=True, is_cut_item=True` (발주불가 확인) |
| HTTP 200 + 행 1개 + CUT_ITEM_YN=1 | `success=True, is_cut_item=True` (기존 로직) |
| HTTP 200 + 행 1개 + CUT_ITEM_YN=0 | `success=True, is_cut_item=False` (정상) |
| HTTP 에러 (4xx/5xx) | `success=False` → Selenium 폴백 (기존) |
| 네트워크 타임아웃 | `success=False` → Selenium 폴백 (기존) |
| SSV 파싱 자체 실패 (dsItem 없음) | `success=False` → Selenium 폴백 (기존) |

---

## 6. 테스트 계획

### 단위 테스트 (8개)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `test_extract_empty_rows_returns_cut` | dsItem.rows=[] → success=True, is_cut=True, is_empty=True |
| 2 | `test_extract_normal_item` | dsItem.rows=[data] → success=True, is_cut=False |
| 3 | `test_extract_actual_cut_item` | rows=[data], CUT_ITEM_YN=1 → is_cut=True |
| 4 | `test_extract_no_dsitem` | dsItem=None → success=False (기존 유지) |
| 5 | `test_process_api_result_empty_response` | is_empty_response=True → CUT 마킹 |
| 6 | `test_process_api_result_normal` | is_empty_response=False → 기존 로직 |
| 7 | `test_selenium_fallback_only_on_real_failure` | HTTP 에러만 폴백, 빈 응답은 폴백 안 함 |
| 8 | `test_prefetch_cut_detection_from_empty` | 전체 흐름: 빈 응답 → _cut_items에 추가 |

---

## 7. 구현 순서

1. `direct_api_fetcher.py` — `extract_item_data()` 빈 응답 처리 추가
2. `order_prep_collector.py` — `_process_api_result()` 빈 응답 결과 반환
3. `order_prep_collector.py` — `_collect_via_direct_api()` Selenium 폴백 조건 수정
4. 테스트 작성 및 실행
