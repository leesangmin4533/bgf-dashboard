# Direct API 트러블슈팅 & 운영 기술문서

> 작성일: 2026-02-28
> 범위: Direct API 운영 중 발견된 버그, 수정 내역, 실패 패턴 분석

---

## 1. 개요

`nexacro-direct-api-pattern.md`가 Direct API의 **설계 원리**를 다뤘다면,
이 문서는 **실제 운영에서 발생한 문제와 해결**을 기록한다.

### 관련 모듈

| 모듈 | 파일 | 역할 |
|------|------|------|
| DirectApiFetcher | `src/collectors/direct_api_fetcher.py` | stbj030 읽기 (selSearch) |
| OrderPrepCollector | `src/collectors/order_prep_collector.py` | 발주 준비 수집 (Direct API + Selenium) |
| DirectApiSaver | `src/order/direct_api_saver.py` | stbjz00 쓰기 (saveOrd) |
| OrderExecutor | `src/order/order_executor.py` | 3단계 폴백 발주 실행 |

---

## 2. 버그 #1: 배치 조회 전체 실패 (0/203건)

### 2.1 증상

```
2026-02-28 07:14:31 | [DirectAPI] 배치 수집 시작: 203개 상품
2026-02-28 07:14:39 | [DirectAPI] 배치 조회 결과 없음
2026-02-28 07:14:39 | [DirectAPI] 전체 실패 → Selenium 폴백
```

두 매장 모두 0/203, 0/137 전부 실패하고 Selenium으로 폴백.

### 2.2 근본 원인

JS `fetch()` 응답의 **HTTP 상태 코드를 확인하지 않았다**.

```javascript
// 수정 전 (문제)
var resp = await fetch('/stbj030/selSearch', { ... });
var text = await resp.text();
return { barcode: barcode, text: text };
// → HTTP 403/500 등 에러 응답도 text로 반환 → SSV 파싱 실패 → "결과 없음"
```

### 2.3 수정 내용 (`direct_api_fetcher.py`)

**a) `resp.ok` 체크 추가 (fetchOne)**

```javascript
// 수정 후
var resp = await fetch('/stbj030/selSearch', { ... });
if (!resp.ok) {
    return { barcode: barcode, error: 'HTTP ' + resp.status, ok: false, status: resp.status };
}
var text = await resp.text();
return { barcode: barcode, text: text, ok: true, status: resp.status };
```

**b) `_validate_template()` 배치 전 1건 검증 추가**

```python
def _validate_template(self, sample_item_cd: str) -> bool:
    """배치 실행 전 1건으로 템플릿 유효성 검증"""
    probe = self.driver.execute_script("""
        var body = arguments[0].replace(/strItemCd=[^\\u001e]*/, 'strItemCd=' + arguments[1]);
        try {
            var resp = await fetch('/stbj030/selSearch', {
                method: 'POST',
                headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                body: body,
                signal: AbortSignal.timeout(arguments[2])
            });
            var text = await resp.text();
            return { status: resp.status, ok: resp.ok, len: text.length,
                     hasItem: text.indexOf('ITEM_NM') > -1,
                     snippet: text.substring(0, 200) };
        } catch(e) {
            return { status: 0, ok: false, error: e.message };
        }
    """, self._request_template, sample_item_cd, self.timeout_ms)
    # 검증 조건: ok=True, hasItem=True, len >= 100
```

검증 실패 시 `_request_template = None`으로 무효화하여 Selenium 폴백으로 전환.

**c) 진단 로깅 (실패 유형 구분)**

```python
js_error_count = 0     # JS fetch 자체 실패 (네트워크, 타임아웃, HTTP 에러)
parse_error_count = 0  # fetch 성공했으나 SSV에 dsItem 없음

# 첫 번째 실패에 대해 샘플 로그 기록
logger.warning(
    f"[DirectAPI] JS fetch 실패 샘플: barcode={barcode}, "
    f"status={entry.get('status')}, error={entry.get('error')}"
)
```

**d) 타임아웃 증가**: 5000ms → 8000ms

### 2.4 영향 범위

- `fetch_items_batch()`: 배치 조회 전 `_validate_template()` 호출
- `fetch_item_data()`: 단건 조회에도 `resp.ok` 체크 추가
- 테스트: `side_effect`로 순차 반환 (validation probe + batch results)

---

## 3. 버그 #2: 템플릿 캡처 타이밍 문제 (prefetch 실패)

### 3.1 증상

```
2026-02-28 08:01:47 | [DirectAPI] 요청 템플릿 준비 실패
2026-02-28 08:01:47 | [DirectAPI] 전체 실패 → Selenium 폴백
```

Phase 2 시작 시 DirectAPI prefetch가 **매번** 실패.

### 3.2 근본 원인

`_collect_via_direct_api()` → `ensure_template()` 호출 시점에
브라우저가 아직 **단품별 발주(STBJ030_M0) 페이지에 있지 않아서**
`/stbj030/selSearch` 요청이 발생한 적이 없음.

```
ensure_template()
  → capture_request_template()
    → window._capturedRequests.length → 0 (아무것도 캡처 안 됨)
  → 인터셉터 설치 + 1초 대기
  → 재시도 → 여전히 0
  → return False
```

### 3.3 수정 내용 (`order_prep_collector.py`)

`_collect_via_direct_api()`에서 템플릿이 없을 때 **자동 복구 플로우** 추가:

```python
def _collect_via_direct_api(self, item_codes):
    results = {}
    remaining_codes = list(item_codes)

    # 템플릿 준비 — 없으면 Selenium 1건 검색으로 캡처
    if not self._direct_api.ensure_template():
        # 1) 메뉴 이동 (단품별 발주)
        if not self._menu_navigated:
            self.navigate_to_menu()
        # 2) 날짜 선택
        if not self._date_selected:
            self.select_order_date()
        # 3) 첫 번째 상품을 Selenium으로 검색 → selSearch 요청 발생
        first_item = remaining_codes[0]
        first_result = self.collect_for_item(first_item)
        results[first_item] = first_result
        remaining_codes = remaining_codes[1:]
        # 4) 캡처 재시도 → 인터셉터가 selSearch body를 잡았을 것
        time.sleep(0.5)
        if not self._direct_api.capture_request_template():
            return None  # 여전히 실패 → 전체 Selenium 폴백

    # 나머지 배치 처리
    batch_data = self._direct_api.fetch_items_batch(remaining_codes)
    ...
```

### 3.4 플로우 다이어그램 (수정 후)

```
_collect_via_direct_api(item_codes=[A,B,C,...,Z])
  │
  ├── ensure_template() → True ───────────────────────┐
  │                                                     │
  └── ensure_template() → False                         │
        │                                               │
        ├── navigate_to_menu() (단품별 발주)              │
        ├── select_order_date()                          │
        ├── collect_for_item(A) ← Selenium 1건 검색      │
        │     └── selSearch XHR 발생 → 인터셉터 캡처!     │
        ├── capture_request_template() → True ──────────┤
        │                                               │
        └── capture_request_template() → False          │
              └── return None (전체 Selenium 폴백)       │
                                                        │
  ┌─────────────────────────────────────────────────────┘
  │
  ├── _validate_template(B) → 1건 프로브
  │     ├── True → 배치 계속
  │     └── False → 템플릿 무효화 → return {}
  │
  ├── fetch_items_batch([B,C,...,Z]) ← JS 병렬 fetch
  │     ├── 성공 → SSV 파싱 → results에 추가
  │     └── 실패 → failed 목록에 추가
  │
  └── failed 항목 최대 50건 Selenium 폴백
```

### 3.5 실행 결과 (수정 전 vs 후)

| 구분 | prefetch | 발주 단계 배치 | 총 소요시간 |
|------|----------|--------------|-----------|
| **수정 전** | 실패 (Selenium 폴백) | 부분 성공 (Selenium 후 캡처됨) | ~20분 |
| **수정 후** | 성공 (1건 Selenium + 나머지 API) | 성공 | ~3분 예상 |

---

## 4. 버그 #3: Phase 1.61 mid_cd 컬럼 에러

### 4.1 증상

```
2026-02-28 07:10:47 | [Phase 1.61] 수요 패턴 분류 실패: 'ProductDetailRepository' has no attribute 'get_detail'
2026-02-28 08:00:47 | [Phase 1.61] 수요 패턴 분류 실패: no such column: mid_cd
```

### 4.2 원인

- 1차: `pd_repo.get_detail(item_cd)` 메서드가 존재하지 않음
- 2차: `SELECT item_cd, mid_cd FROM product_details` → `product_details`에 `mid_cd` 컬럼 없음

### 4.3 수정 (`daily_job.py` Phase 1.61)

```python
# 수정 전
detail = pd_repo.get_detail(item_cd)  # 존재하지 않는 메서드

# 수정 후 — 벌크 쿼리로 products 테이블에서 mid_cd 일괄 조회
conn = DBRouter.get_common_connection()
cursor = conn.cursor()
placeholders = ",".join("?" for _ in active_items)
cursor.execute(
    f"SELECT item_cd, mid_cd FROM products WHERE item_cd IN ({placeholders})",
    active_items
)
mid_cd_map = {row[0]: row[1] for row in cursor.fetchall()}
```

**핵심**: `mid_cd`는 `products` 테이블에만 존재. `product_details`에는 `large_cd`, `small_cd`만 있음.

---

## 5. 버그 #4: Store DB 누락 테이블 (manual_order_items)

### 5.1 증상

```
2026-02-28 08:12:13 | 수동 발주 조회 실패 (차감 건너뜀): no such table: manual_order_items
```

### 5.2 원인

`STORE_SCHEMA`에 v44로 `manual_order_items` 추가했지만,
`init_store_db()`는 DB **최초 생성 시에만** 호출됨.
이미 존재하는 store DB(46513, 46704)에는 새 테이블이 미적용.

### 5.3 수정 (`daily_job.py` `run_optimized()`)

```python
# run_optimized() 시작 부분에 추가
try:
    from src.infrastructure.database.schema import init_store_db
    init_store_db(self.store_id)
except Exception as e:
    logger.warning(f"Store DB 테이블 보장 실패 (계속 진행): {e}")
```

`CREATE TABLE IF NOT EXISTS` 사용으로 기존 데이터에 영향 없음.
매 실행마다 누락 테이블 자동 보장.

### 5.4 적용 결과

| Store | Before | After |
|-------|--------|-------|
| 46513 | 32 테이블 | 36 테이블 (+manual_order_items, detected_new_products 등) |
| 46704 | 30 테이블 | 33 테이블 (+manual_order_items, detected_new_products 등) |

---

## 6. Direct API 발주 저장 (DirectApiSaver)

### 6.1 아키텍처

```
OrderExecutor (3단계 폴백)
  ├── Level 1: DirectApiSaver (dataset + gfn_transaction)
  │     ├── 전략 1: POPULATE_DATASET_JS + CALL_GFN_TRANSACTION_JS
  │     └── 전략 2: fetch() 직접 POST (SSV body 구성)
  ├── Level 2: BatchGridInput (Hybrid 배치)
  └── Level 3: Selenium 개별 입력 (최종 폴백)
```

### 6.2 핵심 JS: dataset 채우기

```javascript
// POPULATE_DATASET_JS 핵심 (의사코드)
// dsGeneralGrid에 행 추가 + selSearch 응답의 dsItem 값으로 채움

var ds = workForm.gdList._binddataset;  // dsGeneralGrid
for (each item in items) {
    var newRow = ds.addRow();
    ds.setColumn(newRow, '_RowType_', 'U');  // Update type

    // 1. selSearch 프리페치로 얻은 dsItem 전체 컬럼 복사
    var selData = prefetchedData[item.item_cd];
    for (col in selData) {
        ds.setColumn(newRow, col, selData[col]);
    }

    // 2. 발주 수량 설정
    ds.setColumn(newRow, 'ORD_MUL_QTY', item.qty);
}
```

### 6.3 핵심 JS: gfn_transaction 호출

```javascript
// CALL_GFN_TRANSACTION_JS 핵심
var workForm = stbjForm.div_workForm.form.div_work_01.form;
workForm.gfn_transaction(
    'savOrd',                     // txId
    'saveOrd',                    // svcUrl → POST /stbjz00/saveOrd
    'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk',  // inDatasets
    'gds_ErrMsg=gds_ErrMsg',      // outDatasets
    'strPyunsuId=0 strOrdInputFlag=04',  // args
    'fn_save_callback'            // callback
);
```

### 6.4 발주 시간 제한

- `ErrorCode=99999`: BGF 서버의 **발주 가능 시간 제한** (비즈니스 로직)
- 발주 가능 시간대 밖에서는 Direct API든 Selenium이든 저장 불가
- `CHECK_ORDER_AVAILABILITY_JS`로 사전 확인: `fv_OrdYn`, `fv_OrdClose`

### 6.5 SSV 프로토콜 (saveOrd)

```
Request:
  POST /stbjz00/saveOrd
  Content-Type: text/plain;charset=UTF-8
  Body: SSV (세션변수 + dsGeneralGrid 51컬럼 + dsSaveChk 6컬럼)

Response:
  SSV (ErrorCode + gds_ErrMsg)
  성공: ErrorCode=0
  실패: ErrorCode=99999 (발주시간 제한), 그 외 코드
```

---

## 7. Direct API 실행 흐름 (Phase 2 전체)

```
[Phase 2] Auto Order 시작
  │
  ├── auto_order.py: 발주 추천 목록 생성 (ML 예측기)
  │     └── 138개 상품 확정
  │
  ├── order_prep_collector._collect_via_direct_api(138개)
  │     ├── ensure_template() → False (첫 실행)
  │     ├── navigate_to_menu() → 단품별 발주
  │     ├── select_order_date() → 발주일 선택
  │     ├── collect_for_item(첫 상품) → Selenium 1건 (템플릿 캡처용)
  │     ├── capture_request_template() → True (인터셉터가 잡음)
  │     ├── _validate_template(2번째 상품) → 1건 프로브
  │     └── fetch_items_batch(나머지 137개) → JS 병렬 fetch
  │           ├── 성공: 122건 (API)
  │           └── 실패: 16건 → Selenium 폴백
  │
  ├── order_executor.execute_orders(발주 목록)
  │     ├── Level 1: DirectApiSaver
  │     │     ├── prefetch: selSearch → dsItem 전체 컬럼 수집
  │     │     ├── populate_dataset: dsGeneralGrid에 50건 채우기
  │     │     └── gfn_transaction: saveOrd 호출
  │     ├── Level 2: BatchGridInput (실패 시)
  │     └── Level 3: Selenium 개별 (최종 폴백)
  │
  └── 발주 완료: 성공 41건, 실패 0건
```

---

## 8. 운영 로그 분석 가이드

### 8.1 정상 동작 시 로그 패턴

```
[DirectAPI] 템플릿 캡처용 Selenium 검색: 8801234567890   ← 첫 1건
[DirectAPI] 템플릿 캡처 성공 (Selenium 1건 검색 후)       ← 캡처 완료
[DirectAPI] 템플릿 검증 성공: HTTP 200, len=1234, hasItem=True
[DirectAPI] 배치 조회 시작: 137개 상품 (concurrency=5)
[DirectAPI] 배치 조회 완료: 130/137건 성공, 7건 실패 (JS에러=2, SSV파싱실패=5)
[DirectAPI] API 실패 7건 중 7건 Selenium 폴백
[DirectAPI] 수집 완료: API 130건 + 폴백 7건
```

### 8.2 실패 진단 키워드

| 로그 메시지 | 의미 | 대응 |
|------------|------|------|
| `요청 템플릿 준비 실패` | 인터셉터 미설치 or 검색 미실행 | 수정 완료 (자동 Selenium 1건) |
| `템플릿 검증 실패: HTTP 403` | 세션 만료 or 권한 없음 | 재로그인 필요 |
| `템플릿 검증 실패: hasItem=False` | 잘못된 템플릿 or 상품 없음 | 템플릿 재캡처 |
| `JS fetch 실패 샘플` | 네트워크/타임아웃 | timeout_ms 증가 검토 |
| `SSV 파싱 실패 샘플` | 서버 응답은 있으나 dsItem 없음 | 상품코드 유효성 확인 |
| `배치 조회 결과 없음` | 전체 실패 (템플릿 문제) | 위의 진단 후 재시도 |

### 8.3 CLI 로그 검색

```bash
# DirectAPI 관련 로그만 필터
python scripts/log_analyzer.py --search "DirectAPI" --last 24h

# 배치 성공률 확인
python scripts/log_analyzer.py --search "배치 조회 완료" --last 7d

# 템플릿 문제 확인
python scripts/log_analyzer.py --search "템플릿" --last 24h
```

---

## 9. 설정값 참조

| 설정 | 파일 | 기본값 | 설명 |
|------|------|--------|------|
| `USE_DIRECT_API` | constants.py | `True` | Direct API 사용 여부 |
| `DIRECT_API_CONCURRENCY` | constants.py | `5` | 동시 요청 수 |
| `DIRECT_API_TIMEOUT_MS` | constants.py | `8000` | 개별 요청 타임아웃 |
| `DIRECT_API_ORDER_MAX_BATCH` | constants.py | `50` | 발주 저장 1회 배치 크기 |
| `DIRECT_API_ORDER_VERIFY` | constants.py | `True` | 발주 후 검증 여부 |
| `DIRECT_API_SAVE_TIMEOUT_MS` | timing.py | - | saveOrd 타임아웃 |
| `DIRECT_API_VERIFY_WAIT` | timing.py | - | 발주 후 검증 대기 |

---

## 10. 테스트 현황

| 테스트 파일 | 건수 | 범위 |
|------------|------|------|
| `test_direct_api_fetcher.py` | 47 | SSV 파싱, 배치 조회, 템플릿 검증 |
| `test_direct_api_saver.py` | 26 | 발주 저장, 프리페치, gfn_transaction |
| `test_direct_api_order.py` | 10 | 통합 발주 시나리오 |
| **합계** | **83** | |

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| `docs/collectors/nexacro-direct-api-pattern.md` | Direct API 설계 원리 (SSV, 인터셉터, 체크리스트) |
| `docs/archive/2026-02/direct-api-prefetch/` | prefetch PDCA 문서 |
| `docs/archive/2026-02/direct-api-order/` | 발주 저장 PDCA 문서 |

---

## 변경 이력

| 날짜 | 변경 | 파일 |
|------|------|------|
| 2026-02-27 | Direct API 패턴 문서 초안 | nexacro-direct-api-pattern.md |
| 2026-02-28 | fetch() resp.ok 체크 추가 | direct_api_fetcher.py |
| 2026-02-28 | _validate_template() 배치 전 검증 추가 | direct_api_fetcher.py |
| 2026-02-28 | 진단 로깅 (JS에러 vs SSV파싱실패) | direct_api_fetcher.py |
| 2026-02-28 | 타임아웃 5000→8000ms | direct_api_fetcher.py |
| 2026-02-28 | 템플릿 자동 캡처 (Selenium 1건 검색) | order_prep_collector.py |
| 2026-02-28 | Phase 1.61 mid_cd 쿼리 수정 | daily_job.py |
| 2026-02-28 | Store DB 테이블 자동 보장 | daily_job.py |
