# 카테고리 대규모 업데이트 기술 분석

> 일회성 전체 DB 상품 카테고리 일괄 수집
> 작성일: 2026-03-01

---

## 1. 현황 진단

### DB 상태 (common.db)

| 항목 | 수량 | 비율 |
|------|------|------|
| 전체 상품 (products) | 5,249 | 100% |
| product_details 존재 | 5,229 | 99.6% |
| large_cd 있음 | 5,198 | 99.0% |
| small_cd 있음 | 5,198 | 99.0% |
| class_nm 있음 | 5,198 | 99.0% |
| **카테고리 미수집** | **51** | **1.0%** |

### 카테고리 3단계 구조

```
대분류(large_cd, 2자리) → 중분류(mid_cd, 3자리) → 소분류(small_cd, 3자리)
    18종                     72종                   198종

예시: 01(간편식사) → 001(도시락) → 001(정식도시락)
      class_nm: "간편식사 > 도시락 > 정식도시락"
```

### 저장 위치

| 테이블 | 카테고리 필드 | DB |
|--------|-------------|-----|
| `products` | mid_cd | common.db |
| `product_details` | large_cd, small_cd, small_nm, class_nm | common.db |
| `mid_categories` | mid_cd, mid_nm, large_cd, large_nm | common.db |

---

## 2. 수집 방식: Direct API

### 엔드포인트

```
POST /stbjz00/selItemDetailSearch
→ dsItemDetail (98 컬럼) — 카테고리 + 상품 기본정보

POST /stbjz00/selItemDetailOrd
→ dsItemDetailOrd (30 컬럼) — 발주가능요일, 발주단위
```

### 카테고리 관련 응답 컬럼 (dsItemDetail)

| 컬럼 | 설명 | 예시 |
|------|------|------|
| LARGE_CD (or LCLS_CD) | 대분류 코드 | "01" |
| LARGE_NM (or LCLS_NM) | 대분류 명 | "간편식사" |
| MID_CD (or MCLS_CD) | 중분류 코드 | "001" |
| MID_NM (or MCLS_NM) | 중분류 명 | "도시락" |
| SMALL_CD (or SCLS_CD) | 소분류 코드 | "001" |
| SMALL_NM (or SCLS_NM) | 소분류 명 | "정식도시락" |
| CLASS_NM | 분류 전체 경로 | "간편식사 > 도시락 > 정식도시락" |

### SSV 프로토콜

```
요청 Body: SSV 형식 (RS=\u001e, US=\u001f)
  - strItemCd={바코드}
  - strOrdYmd={발주일자 YYYYMMDD}

응답: SSV 형식
  - Dataset:dsItemDetail
  - _RowType_ | LARGE_CD:string(2) | MID_CD:string(3) | ...
  - N | 01 | 001 | ...
```

---

## 3. 기존 인프라 (재사용 가능)

### DirectPopupFetcher (`src/collectors/direct_popup_fetcher.py`)

```python
# 이미 구현된 배치 조회 기능
fetcher = DirectPopupFetcher(driver, concurrency=5, timeout_ms=8000)
fetcher.capture_template()  # XHR 인터셉터로 body 템플릿 캡처

# 배치 조회: JS worker pool (concurrency=5, delay=50ms)
results = fetcher.fetch_product_details(item_codes)
# → {item_cd: {mid_cd, large_cd, small_cd, class_nm, ...}, ...}
```

**성능**: ~50건/초 (concurrency=5, delay=50ms)

### ProductDetailBatchCollector (`src/collectors/product_detail_batch_collector.py`)

```python
collector = ProductDetailBatchCollector(driver)

# 1. 수집 대상 선별 (미수집 항목만)
items = collector.get_items_to_fetch(limit=200)

# 2. Direct API → Selenium 폴백
stats = collector.collect_all(item_codes=items)
```

**제약**: `get_items_to_fetch()`가 미수집 항목만 반환 → 전체 갱신에 부적합

### Repository 저장 (`ProductDetailRepository`)

```python
# 이미 구현된 DB 저장 메서드
repo.bulk_update_from_popup(item_cd, data)    # product_details UPSERT
repo.update_product_mid_cd(item_cd, mid_cd)   # products.mid_cd 갱신
repo.upsert_mid_category_detail(mid_cd, ...)  # mid_categories 갱신
```

---

## 4. 수집 프로세스 (크롬 확장 → 발주사이트 → Direct API)

### 전체 플로우

```
┌─────────────────────────────────────────────────────────┐
│ 1. 크롬에서 BGF 점포시스템 로그인 상태 확인              │
│    (https://store.bgfretail.com)                        │
│                                                         │
│ 2. Selenium이 크롬 쿠키/세션 공유                       │
│    (로그인된 브라우저 내 fetch() 실행)                   │
│                                                         │
│ 3. XHR 인터셉터 설치 → body 템플릿 캡처                 │
│    (CallItemDetailPopup 1회 트리거 → 템플릿 확보)       │
│                                                         │
│ 4. Direct API 배치 호출                                 │
│    POST /stbjz00/selItemDetailSearch                    │
│    (strItemCd 교체하며 반복)                             │
│                                                         │
│ 5. SSV 응답 파싱 → 카테고리 정보 추출                   │
│                                                         │
│ 6. DB 저장 (common.db)                                  │
│    - product_details: large_cd, small_cd, class_nm      │
│    - products: mid_cd 갱신                              │
│    - mid_categories: large_cd, large_nm 갱신            │
└─────────────────────────────────────────────────────────┘
```

### 상세 단계

#### Step 1: 템플릿 캡처

```python
# window.__popupCaptures에서 캡처된 요청 body 추출
# 또는 CallItemDetailPopup 1회 트리거하여 캡처
fetcher.capture_template()
```

- 넥사크로 팝업이 열릴 때 발생하는 XHR 요청의 body를 가로채어 저장
- `selItemDetailSearch`의 body에서 `strItemCd=` 부분만 교체하면 다른 상품 조회 가능
- **1회만 실행**: 이후 모든 상품에 동일 템플릿 재사용

#### Step 2: 배치 API 호출

```javascript
// 브라우저 내 JS 실행 (Selenium execute_script)
async function fetchOne(barcode) {
    var body = replaceParam(template, 'strItemCd', barcode);
    var resp = await fetch('/stbjz00/selItemDetailSearch', {
        method: 'POST',
        headers: {'Content-Type': 'text/plain;charset=UTF-8'},
        body: body
    });
    return await resp.text();  // SSV 응답
}

// Worker pool: concurrency=5, delay=50ms
```

#### Step 3: SSV 파싱

```python
# RS(\u001e)로 레코드 분리 → US(\u001f)로 필드 분리
detail_row = parse_popup_detail(ssv_text)
# → {'LARGE_CD': '01', 'MID_CD': '001', 'SMALL_CD': '001', ...}

product = extract_product_detail(detail_row, ord_row, item_cd)
# → {'large_cd': '01', 'mid_cd': '001', 'small_cd': '001',
#     'class_nm': '간편식사 > 도시락 > 정식도시락', ...}
```

---

## 5. 구현 방안

### Option A: 기존 인프라 활용 (권장)

`ProductDetailBatchCollector.collect_all()`에 전체 item_cd 목록을 직접 전달.

```python
# 일회성 스크립트: scripts/category_bulk_update.py

def run_category_bulk_update(driver, force_all=False):
    """전체 상품 카테고리 일괄 갱신"""
    repo = ProductDetailRepository()

    if force_all:
        # 전체 상품 코드 조회
        conn = repo._get_conn()
        rows = conn.execute("SELECT item_cd FROM products").fetchall()
        item_codes = [r[0] for r in rows]
    else:
        # 미수집 상품만
        item_codes = repo.get_items_needing_detail_fetch(9999)

    collector = ProductDetailBatchCollector(driver)

    # 배치 분할 (500개씩)
    batch_size = 500
    for i in range(0, len(item_codes), batch_size):
        batch = item_codes[i:i+batch_size]
        stats = collector.collect_all(item_codes=batch)
        logger.info(f"Batch {i//batch_size+1}: {stats}")
        time.sleep(2)  # 배치 간 쿨다운
```

**장점**: 코드 재사용, Direct API+Selenium 폴백, DB 저장 로직 검증됨
**수정 필요**: `_save_to_db()`의 NULL-only 갱신 → 강제 덮어쓰기 옵션 추가

### Option B: 카테고리 전용 경량 수집기

카테고리 정보만 빠르게 수집하는 전용 스크립트.

```python
# selItemDetailSearch만 호출 (selItemDetailOrd 생략)
# → 카테고리 정보만 필요하므로 API 호출 50% 감소
results = fetcher.fetch_items_batch(
    item_codes,
    include_ord=False,  # ord 생략
    delay_ms=30,        # 딜레이 축소
    concurrency=8,      # 동시성 증가
)
```

**장점**: 빠름 (~80건/초), 서버 부하 감소
**주의**: 발주정보(orderable_day 등)는 갱신 안 됨

---

## 6. 성능 추정

### 전체 5,249개 상품 기준

| 설정 | API 호출 | 예상 시간 | 비고 |
|------|---------|-----------|------|
| Option A (concurrency=5, delay=50ms) | 5,249×2 | ~3.5분 | detail+ord 모두 |
| Option B (concurrency=8, delay=30ms, ord 생략) | 5,249×1 | ~1.1분 | 카테고리만 |
| 배치 분할 500개씩 + 쿨다운 2초 | - | +20초 | 안정성 보장 |

### 예상 소요 시간

```
Option A: ~4분 (템플릿 캡처 30초 + API 3.5분 + DB 저장 ~10초)
Option B: ~2분 (템플릿 캡처 30초 + API 1.1분 + DB 저장 ~5초)
```

---

## 7. DB 저장 전략

### 강제 갱신 vs 선택적 갱신

현재 `bulk_update_from_popup()`은 **기존 NULL인 필드만 갱신** 정책.
전체 갱신 시에는 **강제 덮어쓰기** 옵션 필요.

```python
def bulk_update_from_popup(self, item_cd, data, force=False):
    """
    force=False (기본): 기존 NULL인 필드만 갱신
    force=True (벌크 갱신): 모든 카테고리 필드 강제 덮어쓰기
    """
    if force:
        # large_cd, small_cd, small_nm, class_nm 무조건 갱신
        sql = """
            UPDATE product_details SET
                large_cd = ?, small_cd = ?, small_nm = ?,
                class_nm = ?, updated_at = ?
            WHERE item_cd = ?
        """
    else:
        # 기존 로직 유지 (NULL만 채움)
        ...
```

### 갱신 대상 테이블

| 테이블 | 갱신 필드 | 조건 |
|--------|----------|------|
| product_details | large_cd, small_cd, small_nm, class_nm | 강제 갱신 |
| products | mid_cd | API 응답값과 불일치 시만 |
| mid_categories | mid_cd, mid_nm, large_cd, large_nm | UPSERT |

---

## 8. 전제 조건 및 리스크

### 전제 조건

1. **BGF 로그인 상태**: Selenium 브라우저가 `store.bgfretail.com`에 로그인
2. **XHR 인터셉터**: `window.__popupCaptures` 또는 팝업 1회 트리거로 템플릿 캡처
3. **네트워크**: BGF 서버 응답 안정성 (타임아웃 8초/건)

### 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| BGF 서버 rate limit | 요청 차단/세션 만료 | concurrency 낮추기, 배치 간 쿨다운 |
| 세션 만료 (1시간) | API 401 에러 | 배치 중간 세션 체크, 재로그인 로직 |
| 일부 상품 조회 불가 | 테스트/단종 상품 | 실패 건 로깅, Selenium 폴백 |
| 넥사크로 업데이트 | 컬럼명/구조 변경 | 파싱 실패 시 경고 로그 |

---

## 9. 실행 계획

```bash
# 1. 테스트 (10개만)
python scripts/category_bulk_update.py --test --limit 10

# 2. 미수집 상품만 (51개)
python scripts/category_bulk_update.py --missing-only

# 3. 전체 강제 갱신 (5,249개)
python scripts/category_bulk_update.py --force-all

# 4. 결과 검증
python scripts/category_bulk_update.py --verify
```

### 검증 쿼리

```sql
-- 갱신 결과 확인
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN large_cd IS NOT NULL THEN 1 ELSE 0 END) as has_large,
    SUM(CASE WHEN small_cd IS NOT NULL THEN 1 ELSE 0 END) as has_small,
    SUM(CASE WHEN class_nm IS NOT NULL THEN 1 ELSE 0 END) as has_class
FROM product_details;

-- 3단계 분류 일관성 검증
SELECT pd.item_cd, pd.large_cd, p.mid_cd, pd.small_cd, pd.class_nm
FROM product_details pd
JOIN products p ON pd.item_cd = p.item_cd
WHERE pd.class_nm NOT LIKE p.mid_cd || '%'  -- class_nm과 mid_cd 불일치
LIMIT 20;
```

---

## 10. 요약

| 항목 | 내용 |
|------|------|
| **목적** | 전체 DB 상품 카테고리 3단계(대/중/소) 일괄 갱신 |
| **방식** | Direct API (`/stbjz00/selItemDetailSearch`) |
| **대상** | 5,249개 상품 (또는 미수집 51개만) |
| **소요 시간** | ~2~4분 |
| **기존 코드** | `DirectPopupFetcher` + `ProductDetailBatchCollector` 재사용 |
| **신규 코드** | `scripts/category_bulk_update.py` (일회성 스크립트) |
| **DB 변경** | `bulk_update_from_popup(force=True)` 옵션 추가 |
| **리스크** | BGF rate limit, 세션 만료 → 배치 분할+쿨다운으로 대응 |
