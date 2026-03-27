# 넥사크로 Direct API 패턴 기술문서

> 작성일: 2026-02-27
> 범위: BGF 리테일 넥사크로 웹 → Direct API 전환 기법 (XHR 인터셉터 + SSV 프로토콜)

---

## 1. 개요

BGF 리테일 사이트는 **넥사크로(Nexacro)** 프레임워크 기반으로,
일반 Selenium `find_element` 접근이 불가하며 JS 직접 실행(`execute_script`)으로 데이터에 접근한다.

Selenium UI 조작(팝업 열기/닫기, 그리드 클릭) 대신
**XHR body 캡처 → fetch() 직접 호출**로 전환하면 속도를 **10~30배** 향상할 수 있다.

### 성과 비교

| 방식 | 속도 (건당) | 5,000건 소요시간 | 비고 |
|------|-----------|----------------|------|
| Selenium (팝업) | ~3초 | ~4.4시간 | UI 조작 필요 |
| Direct API | ~0.04초 | ~3분 24초 | fetch() 병렬 |
| **개선율** | **75배** | **96% 단축** | |

---

## 2. 핵심 개념: 두 개의 API 체계

BGF 넥사크로에는 **서로 다른 두 개의 API 엔드포인트 체계**가 존재한다.
이 구분을 모르면 잘못된 인터셉터를 설치하여 캡처가 실패한다.

### 2.1 stbj030 계열 (발주준비 화면)

| 항목 | 값 |
|------|---|
| **화면** | STBJ030_M0 (발주준비조회) |
| **엔드포인트** | `/stbj030/selSearch` |
| **데이터셋** | dsItem, dsOrderSale, gdList |
| **호출 시점** | gfn_transaction() → F_10 조회 버튼 |
| **사용 모듈** | `DirectApiFetcher` |
| **인터셉터** | `window._apiCaptures` |

```
발주준비 화면 → F_10 클릭 → gfn_transaction("svc_01", "/stbj030/selSearch", ...)
  → XHR POST /stbj030/selSearch (SSV body)
  → SSV response (dsItem + dsOrderSale + gdList)
```

### 2.2 stbjz00 계열 (상품상세 팝업)

| 항목 | 값 |
|------|---|
| **화면** | CallItemDetailPopup (공통 팝업) |
| **엔드포인트** | `/stbjz00/selItemDetailSearch`, `/stbjz00/selItemDetailOrd`, `/stbjz00/selItemDetailSale` |
| **데이터셋** | dsItemDetail(98 cols), dsItemDetailOrd(30 cols), dsOrderSale |
| **호출 시점** | 바코드 Quick Search → 팝업 오픈 |
| **사용 모듈** | `DirectPopupFetcher` |
| **인터셉터** | `window.__popupCaptures` |

```
홈/발주 화면 → edt_pluSearch 바코드 입력 → Enter
  → Quick Search 결과 클릭
  → CallItemDetailPopup 오픈
  → XHR POST /stbjz00/selItemDetailSearch (SSV body)
  → XHR POST /stbjz00/selItemDetailOrd (SSV body)
```

### 2.3 교차 사용 불가

- `DirectApiFetcher`의 템플릿(stbj030)으로 `DirectPopupFetcher`(stbjz00) 호출 불가
- 반대도 마찬가지: stbjz00 body를 stbj030에 보내면 서버 오류
- **각 API마다 별도의 인터셉터 설치 + 별도의 템플릿 캡처** 필요

---

## 3. XHR 인터셉터 패턴

### 3.1 핵심 원리

넥사크로는 내부적으로 `XMLHttpRequest`로 서버와 SSV 형식으로 통신한다.
`XMLHttpRequest.prototype.open`과 `send`를 오버라이드하여 **URL + body를 가로채는** 패턴이다.

### 3.2 stbjz00 팝업 인터셉터 (검증 완료)

```javascript
// 설치 타이밍: 로그인 직후, 팝업 오픈 전
if (!window.__popupCaptures) {
    window.__popupCaptures = [];
}
if (!window.__popupInterceptorInstalled) {
    window.__popupInterceptorInstalled = true;
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this.__captureUrl = url;
        this.__captureMethod = method;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var url = this.__captureUrl || '';
        if (url.indexOf('stbjz00') >= 0 ||
            url.indexOf('selItemDetail') >= 0) {
            window.__popupCaptures.push({
                url: url,
                method: this.__captureMethod,
                bodyPreview: body ? String(body).substring(0, 5000) : '',
                timestamp: new Date().toISOString()
            });
        }
        return origSend.apply(this, arguments);
    };
}
```

### 3.3 stbj030 발주준비 인터셉터

```javascript
// DirectApiFetcher.ensure_template() 내부에서 자동 설치
if (!window._apiCaptures) {
    window._apiCaptures = [];
}
// ... XMLHttpRequest.prototype 오버라이드
// 필터: url.indexOf('stbj030') >= 0
```

### 3.4 인터셉터 설치 주의사항

| 조건 | 설명 |
|------|------|
| **타이밍** | 반드시 XHR 발생 **전**에 설치. 이미 발생한 요청은 캡처 불가 |
| **중복 방지** | `__popupInterceptorInstalled` 플래그로 재설치 방지 |
| **body 길이** | `substring(0, 5000)` — SSV body가 길 수 있어 5000자 제한 |
| **메모리** | 장시간 사용 시 `__popupCaptures` 배열이 커질 수 있음. 필요시 초기화 |

---

## 4. 템플릿 캡처 플로우

### 4.1 전체 흐름 (상품상세 팝업)

```
[1] 로그인
    └── SalesAnalyzer.do_login()

[2] 인터셉터 설치 (Python에서 execute_script)
    └── window.__popupCaptures = [] + XHR 오버라이드

[3] Selenium으로 팝업 1회 트리거
    └── ProductDetailBatchCollector._fetch_single_item(sample_barcode)
        ├── edt_pluSearch에 바코드 입력 → Enter
        ├── Quick Search 결과 그리드 클릭
        └── CallItemDetailPopup 오픈
            → [인터셉터가 stbjz00 XHR body 캡처]

[4] 캡처 확인
    └── driver.execute_script("return window.__popupCaptures.length")

[5] DirectPopupFetcher.capture_template()
    └── __popupCaptures에서 selItemDetailSearch body → _detail_template
    └── __popupCaptures에서 selItemDetailOrd body → _ord_template

[6] 배치 API 호출
    └── fetcher.fetch_items_batch(barcodes)
        ├── _detail_template에서 strItemCd=<바코드> 치환
        └── fetch() POST → SSV 파싱 → 결과 반환
```

### 4.2 핵심: 왜 Selenium 1회가 필요한가

- `DirectPopupFetcher`는 **인터셉터를 스스로 설치하지 않는다**
- `capture_template()`은 이미 `window.__popupCaptures`에 캡처된 body를 **읽기만** 한다
- 따라서 **외부에서 인터셉터 설치 + Selenium 팝업 1회 트리거** 필요
- 한 번 body가 캡처되면 나머지 수천 건은 `fetch()` 직접 호출

### 4.3 왜 01:00 일일 수집에서 Direct API가 작동하지 않는가

`run_scheduler.py`의 `detail_fetch_wrapper()`:
```
ProductDetailBatchCollector.collect_all()
  → _try_direct_api() 호출
    → DirectPopupFetcher(driver).capture_template()
      → window.__popupCaptures 읽기
      → 결과: 0건 (인터셉터가 설치된 적 없음)
  → Selenium 폴백
```

**문제**: 일일 배치에서는 인터셉터 설치 없이 `capture_template()`을 호출하므로 항상 실패한다.

**해결방안**: `collect_all()` 내부에서 첫 1건만 인터셉터 설치 + Selenium 수집 → 나머지 Direct API 전환

---

## 5. SSV 프로토콜 파싱

### 5.1 SSV 형식

넥사크로는 **SSV (Separator-Separated Values)** 형식으로 데이터를 주고받는다.

```
구분자:
  RS (Record Separator) = \u001e (0x1E)
  US (Unit Separator)   = \u001f (0x1F)

구조:
  [헤더 레코드]RS[데이터 행 1]RS[데이터 행 2]RS...

헤더: _RowType_:S:0US컬럼명1:타입:길이US컬럼명2:타입:길이US...
데이터: 값1US값2US값3US...
```

### 5.2 파싱 핵심 함수 (direct_api_fetcher.py)

```python
def parse_ssv_dataset(ssv_text: str, dataset_marker: str) -> Optional[Dict]:
    """
    SSV 전체 텍스트에서 특정 데이터셋 추출

    dataset_marker: 해당 데이터셋에만 존재하는 컬럼명
      - 'ITEM_NM' → dsItem / dsItemDetail
      - 'ORD_ADAY' → dsItemDetailOrd
      - 'ORD_QTY' → dsOrderSale
    """
    records = ssv_text.split(RS)
    for i, record in enumerate(records):
        if '_RowType_' in record and dataset_marker in record:
            cols = [c.split(':')[0] for c in record.split(US)]
            rows = []
            for j in range(i + 1, len(records)):
                row = records[j].strip()
                if not row or '_RowType_' in row:
                    break
                rows.append(row.split(US))
            return {'columns': cols, 'rows': rows}
    return None
```

### 5.3 body 치환 패턴

캡처된 body에서 파라미터만 교체하여 다른 상품을 조회:

```javascript
function replaceParam(body, key, val) {
    var re = new RegExp(key + '=[^\\u001e]*');
    return body.replace(re, key + '=' + val);
}

// 사용
var newBody = replaceParam(capturedBody, 'strItemCd', '8809196620052');
newBody = replaceParam(newBody, 'strOrdYmd', '20260228');
```

---

## 6. JS Worker Pool 패턴 (병렬 요청)

`DirectPopupFetcher.fetch_items_batch()` 와 `DirectApiFetcher`에서 공통 사용하는
**브라우저 내 비동기 병렬 요청** 패턴:

```javascript
// 핵심 구조 (의사코드)
var results = [];
var idx = 0;      // 공유 인덱스 (atomic increment)

async function worker() {
    while (idx < barcodes.length) {
        var myIdx = idx++;
        var r = await fetchOne(barcodes[myIdx]);
        results.push(r);
        if (delayMs > 0) {
            await new Promise(ok => setTimeout(ok, delayMs));
        }
    }
}

// concurrency 개의 worker 동시 실행
var workers = [];
for (var w = 0; w < concurrency; w++) {
    workers.push(worker());
}
await Promise.all(workers);
return results;
```

### 파라미터 가이드

| 파라미터 | 권장값 | 설명 |
|----------|--------|------|
| `concurrency` | 5 | 동시 요청 수. 10 초과 시 서버 부하 우려 |
| `timeout_ms` | 8000~10000 | 개별 요청 타임아웃 |
| `delay_ms` | 50 | 요청 간 딜레이 (서버 보호) |
| `batch_size` | 500 | Python→JS 1회 전달 상품 수 (너무 크면 JS 메모리 이슈) |

---

## 7. DB 저장 시 주의사항

### 7.1 sell_price COALESCE 패턴

`bulk_update_from_popup()`의 sell_price 업데이트:

```sql
-- 기존 값이 NULL일 때만 새 값 적용
UPDATE product_details
SET sell_price = COALESCE(sell_price, ?), ...
WHERE item_cd = ?
```

- **의도**: 이미 매가가 있는 상품은 덮어쓰지 않음
- **강제 덮어쓰기 필요 시**: 별도 UPDATE 쿼리 사용 (`--force` 옵션)

### 7.2 large_cd 스키마 갭

`mid_categories` 테이블과 일부 `product_details` INSERT 경로에
`large_cd` 컬럼이 없을 수 있음:

```
오류: sqlite3.OperationalError: table mid_categories has no column named large_cd
원인: 스키마 마이그레이션 누락
영향: _save_to_db() 경로 실패 → product_details 신규 행 생성 불가
우회: save_results_to_db()에서 bulk_update_from_popup() 직접 호출 (mid_categories 건너뜀)
해결: ALTER TABLE mid_categories ADD COLUMN large_cd TEXT;
```

### 7.3 products 테이블 스키마

```sql
-- products 테이블 컬럼 (최소)
CREATE TABLE products (
    item_cd TEXT PRIMARY KEY,
    item_nm TEXT,
    mid_cd TEXT,
    created_at TEXT,
    updated_at TEXT
);
-- 주의: status, mid_nm 컬럼 없음!
```

---

## 8. 실전 스크립트: 전상품 일괄 수집

`scripts/fetch_all_details_direct.py` 사용:

```bash
# 전체 수집
python scripts/fetch_all_details_direct.py

# 50개만 테스트
python scripts/fetch_all_details_direct.py --max 50

# 대상만 확인 (실제 수집 안 함)
python scripts/fetch_all_details_direct.py --dry-run

# 기존 sell_price도 최신값으로 덮어쓰기
python scripts/fetch_all_details_direct.py --force

# 동시 요청 수 조정
python scripts/fetch_all_details_direct.py --concurrency 3 --batch-size 300
```

### 실행 플로우 요약

```
[1/6] 로그인 → SalesAnalyzer.do_login()
[2/6] 인터셉터 설치 → install_popup_interceptor()
[3/6] Selenium 1건 → ProductDetailBatchCollector._fetch_single_item()
[4/6] Direct API 배치 → DirectPopupFetcher.fetch_items_batch() × N배치
[5/6] 결과 집계
[6/6] 수집 전후 현황 비교
```

---

## 9. 새 API 전환 시 체크리스트

넥사크로 다른 화면도 같은 패턴으로 Direct API 전환 가능:

- [ ] **엔드포인트 확인**: 해당 화면의 XHR URL 패턴 (예: `/stbj070/`, `/stgj020/`)
- [ ] **인터셉터 필터**: URL 필터 조건 수정 (indexOf 패턴)
- [ ] **body 파라미터 확인**: 캡처된 body에서 어떤 파라미터를 치환해야 하는지
- [ ] **SSV dataset_marker**: 응답에서 어떤 컬럼명이 타겟 데이터셋을 식별하는지
- [ ] **세션/쿠키**: 로그인된 Selenium 브라우저 내 fetch()로 자동 공유 확인
- [ ] **Selenium 폴백**: API 실패 시 기존 방식 유지

### 적용 가능 화면 후보

| 화면 | 엔드포인트 | 현재 방식 | 전환 가능 여부 |
|------|-----------|----------|--------------|
| 발주준비 (STBJ030) | /stbj030/selSearch | ✅ Direct API 완료 | 완료 |
| 상품상세 팝업 | /stbjz00/selItemDetail* | ✅ Direct API 완료 | 완료 |
| **발주 저장** | **/stbjz00/saveOrd** | **✅ Direct API 완료** | **완료 (DirectApiSaver)** |
| 시간대별 매출 (STMB010) | /stmb010/* | ✅ Direct API 완료 | 완료 (HourlySalesCollector) |
| 매출 (STMB011) | 미확인 | Selenium | 계획 중 |
| 발주현황 (STBJ070) | 미확인 | Selenium | 미정 |
| 검수전표 (STGJ020) | 미확인 | Selenium | 미정 |

---

## 10. 교훈 정리

### 실패 사례에서 배운 것

1. **잘못된 API 엔드포인트 사용**: OrderPrepCollector(stbj030)에서 캡처한 템플릿으로 stbjz00 호출 시도 → 실패. API 체계가 다르다.

2. **홈화면 Quick Search 직접 구현 실패**: 커스텀 JS로 edt_pluSearch 직접 조작 → 팝업 미출현. 넥사크로 내부 이벤트 체인이 복잡하여 기존 코드(ProductDetailBatchCollector._fetch_single_item) 재사용이 안전하다.

3. **인터셉터 미설치 상태에서 capture_template() 호출**: window.__popupCaptures가 비어있어 항상 실패. 인터셉터는 **외부에서 명시적으로** 설치해야 한다.

4. **fetch() resp.ok 미확인** (2026-02-28 발견): HTTP 403/500 등 에러 응답도 text()로 읽어 SSV 파싱 시도 → 전체 실패. 반드시 `resp.ok` 체크 필요.

5. **prefetch 시점의 템플릿 부재** (2026-02-28 발견): Phase 2 시작 시 단품별 발주 페이지에 진입하지 않은 상태에서 ensure_template() 호출 → 항상 실패. Selenium 1건 검색으로 자동 캡처하도록 수정.

### 성공 패턴

1. **기존 Selenium 코드 재사용 + 인터셉터 추가**: 검증된 코드로 1건 처리하면서 인터셉터가 body를 캡처 → 가장 안정적
2. **배치 크기 500 + 동시 요청 5**: 서버 부하 없이 최적 처리량 (25.6개/초)
3. **save_results_to_db() 별도 구현**: BatchCollector._save_to_db() 경로의 large_cd 오류 우회
4. **배치 전 1건 프로브 검증**: `_validate_template()`으로 실제 API 호출 1건 테스트 후 배치 시작 → 전체 실패 방지

---

## 관련 파일

| 파일 | 역할 |
|------|------|
| `src/collectors/direct_popup_fetcher.py` | stbjz00 Direct API 읽기 모듈 |
| `src/collectors/direct_api_fetcher.py` | stbj030 Direct API 읽기 모듈 (SSV 파서 포함) |
| `src/order/direct_api_saver.py` | stbjz00 Direct API 쓰기 모듈 (발주 저장) |
| `src/collectors/product_detail_batch_collector.py` | 상품상세 일괄 수집기 (Direct API + Selenium) |
| `src/collectors/order_prep_collector.py` | 발주준비 수집기 (stbj030 사용, 자동 템플릿 캡처) |
| `src/order/order_executor.py` | 3단계 폴백 발주 실행기 |
| `scripts/fetch_all_details_direct.py` | 전상품 일괄 수집 1회성 스크립트 |

### 관련 문서

| 문서 | 설명 |
|------|------|
| `docs/collectors/direct-api-troubleshooting.md` | 운영 트러블슈팅 & 버그 수정 이력 |
| `docs/archive/2026-02/direct-api-prefetch/` | prefetch PDCA 문서 |
| `docs/archive/2026-02/direct-api-order/` | 발주 저장 PDCA 문서 |
