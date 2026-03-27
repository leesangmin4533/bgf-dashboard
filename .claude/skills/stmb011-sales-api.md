# 중분류별 매출 구성비 Direct API 스킬 (STMB011)

## When to Use

- 중분류별 매출 데이터를 날짜별로 수집할 때
- 신규 점포 판매 데이터를 백필(backfill)할 때
- daily_sales 테이블에 과거 데이터를 일괄 적재할 때
- 매출 트렌드 분석용 원시 데이터가 필요할 때

## Common Pitfalls

- ❌ Selenium UI 조작(그리드 클릭)으로 중분류별 상세 수집 → 느림, 팝업/탭 관리 필요
- ✅ Direct API(`/stmb011/selSearch` + `/stmb011/selDetailSearch`)로 fetch() 직접 호출

- ❌ 템플릿 없이 body를 직접 구성 → 세션 파라미터 누락으로 401/500 에러
- ✅ XHR 인터셉터로 실제 요청 body 캡처 → 날짜/중분류만 교체하여 재사용

- ❌ 로그인 없이 API 호출 → 인증 실패
- ✅ Selenium 로그인 필수 (세션 쿠키 공유 방식)

- ❌ strFromYmd/strToYmd에 서로 다른 날짜 → 기간 합산 데이터 (일별 분리 불가)
- ✅ 반드시 strFromYmd == strToYmd (동일 날짜) → 일별 정확한 데이터

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| selSearch 빈 응답 | 세션 만료 | 재로그인 후 재시도 |
| selDetailSearch 0건 반환 | 해당 중분류에 당일 판매 없음 | 정상 (SALE_QTY=0인 상품 미노출) |
| 템플릿 캡처 실패 | STMB011 화면 미진입 상태 | navigate_to_sales_menu() 후 조회 1회 실행 |
| "strGubun" 관련 에러 | selSearch/selDetailSearch 혼동 | selSearch에만 strGubun=0 포함, detail에는 없음 |
| 30개 이상 중분류 상세조회 느림 | 동시 요청 과다 | concurrency=5 + delay_ms=30으로 서버 보호 |

---

## 화면 정보

| 항목 | 값 |
|------|---|
| **메뉴 경로** | 매출분석 > 유형별 분석 > 중분류별 매출 구성비 |
| **프레임 ID** | STMB011_M0 |
| **프레임 경로** | `app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB011_M0.form` |
| **수집기** | `DirectSalesFetcher` (`src/collectors/direct_sales_fetcher.py`) |
| **백필 스크립트** | `tools/backfill_sales.py` |

---

## API 엔드포인트

### 1. selSearch — 중분류 목록 조회

| 항목 | 값 |
|------|---|
| **URL** | `POST /stmb011/selSearch` |
| **Content-Type** | `text/plain;charset=UTF-8` |
| **요청 형식** | SSV (넥사크로 고유 프로토콜) |
| **응답 형식** | SSV |

#### 핵심 파라미터 (body 내)

| 파라미터 | 설명 | 예시 |
|----------|------|------|
| `strFromYmd` | 시작일 (YYYYMMDD) | `20260304` |
| `strToYmd` | 종료일 (YYYYMMDD) | `20260304` |
| `strStoreCd` | 점포코드 | `46513` |
| `strPreStoreList` | 이전 점포 목록 | `('190623')` |
| `strGubun` | 조회구분 (0=영수금액) | `0` |
| `SS_STORE_CD` | 세션 점포코드 | `46513` |

#### 응답 Dataset: `dsList`

| 컬럼명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| MID_CD | STRING(256) | 중분류코드 | `001` |
| MID_NM | STRING(256) | 중분류명 | `도시락` |
| SALE_QTY | INT(256) | 판매수량 합계 | `5` |
| SALE_AMT | BIGDECIMAL(256) | 매출금액 합계 | `21968` |
| RATE | BIGDECIMAL(256) | 구성비(%) | `1.8` |

---

### 2. selDetailSearch — 중분류별 상품 상세 조회

| 항목 | 값 |
|------|---|
| **URL** | `POST /stmb011/selDetailSearch` |
| **Content-Type** | `text/plain;charset=UTF-8` |
| **요청 형식** | SSV |
| **응답 형식** | SSV |

#### 핵심 파라미터 (body 내)

| 파라미터 | 설명 | 예시 |
|----------|------|------|
| `strFromYmd` | 시작일 (YYYYMMDD) | `20260304` |
| `strToYmd` | 종료일 (YYYYMMDD) | `20260304` |
| `strMidCd` | 중분류코드 | `001` |
| `strStoreCd` | 점포코드 | `46513` |
| `strPreStoreList` | 이전 점포 목록 | `('190623')` |

> **참고**: selSearch와 달리 `strGubun` 파라미터 없음, `strMidCd` 추가

#### 응답 Dataset: `dsDetail`

| 컬럼명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| ORD_YMD | string(0) | 발주일 | (빈 값) |
| ITEM_CD | string(13) | 상품코드 (바코드) | `8809196617779` |
| ITEM_NM | string(36) | 상품명 | `도)동원리챔앤참치김치` |
| SALE_QTY | bigdecimal(0) | 판매수량 | `1` |
| ORD_QTY | bigdecimal(0) | 발주수량 | `1` |
| BUY_QTY | bigdecimal(0) | 매입(입고)수량 | `1` |
| DISUSE_QTY | bigdecimal(0) | 폐기수량 | `0` |
| STOCK_QTY | bigdecimal(0) | 현재고수량 | `0` |

---

## SSV 프로토콜 구조

넥사크로 고유 직렬화 형식. 구분자:

| 기호 | 코드 | 역할 |
|------|------|------|
| RS | `\x1e` (U+001E) | 레코드 구분 (파라미터 간) |
| US | `\x1f` (U+001F) | 필드 구분 (컬럼 간) |

### 요청 body 구조 예시

```
SSV:utf-8{RS}
GV_USERFLAG=HOME{RS}
SS_STORE_CD=46513{RS}
SS_USER_NO=46513{RS}
strFromYmd=20260304{RS}
strToYmd=20260304{RS}
strStoreCd=46513{RS}
strGubun=0{RS}                     ← selSearch 전용
GV_MENU_ID=0001,STMB011_M0{RS}
Dataset:dsList{RS}
_RowType_{US}MID_CD:STRING(256){US}MID_NM:STRING(256){US}SALE_QTY:INT(256){US}SALE_AMT:BIGDECIMAL(256){US}RATE:BIGDECIMAL(256){RS}
{RS}
```

### 응답 body 구조 예시

```
SSV:UTF-8{RS}
ErrorCode:string=0{RS}
ErrorMsg:string={RS}
Dataset:dsDetail{RS}
_RowType_{US}ITEM_CD:string(13){US}ITEM_NM:string(36){US}SALE_QTY:bigdecimal(0)...{RS}
N{US}8809196617779{US}도)동원리챔앤참치김치{US}1{US}1{US}1{US}0{US}0{RS}
N{US}8800279679004{US}도)압도적뉴한돈김치제육{US}1{US}1{US}1{US}0{US}0{RS}
```

- 첫 행: `_RowType_`로 시작하는 컬럼 정의
- 데이터 행: `N` (Normal), `I` (Insert), `U` (Update) 등 RowType 시작

---

## 수집 플로우 (2단계)

```
날짜 D에 대해:

[Step 1] selSearch(D)
  → 중분류 목록 [{MID_CD: "001", MID_NM: "도시락", SALE_QTY: 5, ...}, ...]
  → 약 30개 중분류

[Step 2] selDetailSearch(D, mid_cd) × 30회 (병렬 concurrency=5)
  → 상품별 상세 [{ITEM_CD, ITEM_NM, SALE_QTY, ORD_QTY, BUY_QTY, DISUSE_QTY, STOCK_QTY}, ...]
  → 약 200~500개 상품

합산: 1일당 약 31회 API 호출, 2~3초 소요
```

---

## 날짜 교체 방법

기존 body에서 정규식으로 파라미터 값만 교체:

```python
import re

def replace_ssv_param(body: str, param_name: str, new_value: str) -> str:
    """SSV body에서 파라미터 값 교체"""
    pattern = param_name + r'=[^\x1e]*'
    replacement = f'{param_name}={new_value}'
    return re.sub(pattern, replacement, body)

# 날짜 교체
body = replace_ssv_param(template, 'strFromYmd', '20260301')
body = replace_ssv_param(body, 'strToYmd', '20260301')

# 중분류 교체 (selDetailSearch)
body = replace_ssv_param(body, 'strMidCd', '015')
```

---

## 템플릿 캡처 방법

### XHR 인터셉터 설치 (Selenium)

```javascript
window._salesFetcherCaptures = [];
var origOpen = XMLHttpRequest.prototype.open;
var origSend = XMLHttpRequest.prototype.send;

XMLHttpRequest.prototype.open = function(m, u) {
    this._sfUrl = u;
    return origOpen.apply(this, arguments);
};
XMLHttpRequest.prototype.send = function(body) {
    if (body && this._sfUrl && this._sfUrl.includes('stmb011')) {
        window._salesFetcherCaptures.push({
            url: this._sfUrl,
            body: body
        });
    }
    return origSend.apply(this, arguments);
};
```

### 캡처 순서

1. 인터셉터 설치
2. STMB011 화면에서 **조회** 버튼 클릭 → `selSearch` 템플릿 캡처
3. 좌측 중분류 행 클릭 → `selDetailSearch` 템플릿 캡처
4. `window._salesFetcherCaptures`에서 body 추출

---

## DB 저장 매핑 (daily_sales)

| API 응답 컬럼 | daily_sales 컬럼 | 비고 |
|---------------|-----------------|------|
| ITEM_CD | item_cd | PK의 일부 (UNIQUE: sales_date + item_cd) |
| MID_CD | mid_cd | 중분류코드 |
| SALE_QTY | sale_qty | 판매수량 |
| ORD_QTY | ord_qty | 발주수량 |
| BUY_QTY | buy_qty | 매입(입고)수량 |
| DISUSE_QTY | disuse_qty | 폐기수량 |
| STOCK_QTY | stock_qty | 현재고수량 |
| (파라미터) strFromYmd | sales_date | YYYY-MM-DD 변환 |

추가 저장:
- `products` 테이블 (common.db): ITEM_CD, ITEM_NM, MID_CD 마스터 UPSERT
- `mid_categories` 테이블 (common.db): MID_CD, MID_NM 마스터 UPSERT

---

## 관련 파일

| 파일 | 역할 |
|------|------|
| `src/collectors/direct_sales_fetcher.py` | DirectSalesFetcher 클래스 (selSearch/selDetailSearch API 호출) |
| `src/collectors/sales_collector.py` | SalesCollector (Selenium 기반 수집, Direct API 폴백) |
| `src/sales_analyzer.py` | SalesAnalyzer (로그인, 메뉴 이동, 넥사크로 UI 조작) |
| `src/infrastructure/database/repos/sales_repo.py` | SalesRepository.save_daily_sales() |
| `src/collectors/direct_api_fetcher.py` | parse_ssv_dataset(), ssv_row_to_dict() SSV 파서 |
| `tools/backfill_sales.py` | 신규 점포 백필 스크립트 (날짜 범위 일괄 수집) |

---

## 사용 예시

### CLI (백필 스크립트)

```bash
cd bgf_auto

# 최근 60일 백필
python tools/backfill_sales.py --store_id 47863 --days 60

# 특정 기간
python tools/backfill_sales.py --store_id 47863 --from 2026-01-01 --to 2026-03-04

# dry-run (수집만, 저장 안 함)
python tools/backfill_sales.py --store_id 47863 --days 30 --dry-run

# 이미 수집된 날짜 건너뛰기
python tools/backfill_sales.py --store_id 47863 --days 90 --skip-existing
```

### 코드 (DirectSalesFetcher 직접 사용)

```python
from src.collectors.direct_sales_fetcher import DirectSalesFetcher

fetcher = DirectSalesFetcher(driver, concurrency=5, timeout_ms=8000)
fetcher.install_interceptor()
# ... (STMB011 화면에서 조회 1회 트리거) ...
fetcher.capture_templates_from_interceptor()

# 날짜별 수집
items = fetcher.collect_all("20260304")
# → [{MID_CD, MID_NM, ITEM_CD, ITEM_NM, SALE_QTY, ORD_QTY, BUY_QTY, DISUSE_QTY, STOCK_QTY}, ...]
```

---

## 검증 기록

- **검증일**: 2026-03-04
- **검증 방법**: Chrome 확장(Claude in Chrome)으로 STMB011 화면 접속 → XHR 인터셉터 캡처
- **selSearch 응답**: 30개 중분류 (001~099), 1357 bytes
- **selDetailSearch 응답**: 5개 상품 (001 도시락), 467 bytes
- **strFromYmd=strToYmd 확인**: 동일 날짜 사용 시 일별 정확 데이터
- **strGubun=0**: 영수금액 기준 (매출금액 기준은 1)
