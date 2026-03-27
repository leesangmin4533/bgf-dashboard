# Direct API Order Design Document

> Feature: `direct-api-order`
> Phase: Do (completed)
> Created: 2026-02-28

## 1. Overview

BGF 리테일 자동 발주 시스템의 발주 저장 단계를 Direct API 호출로 최적화합니다.
기존 Selenium UI 조작(상품당 3.3초) 대신 API 1회 호출로 50개 상품을 일괄 저장(~10초 예상).

## 2. Architecture

### 2.1 3-Level Fallback Chain

```
OrderExecutor.execute_orders()
  ├─ Level 1: DirectApiOrderSaver  (Direct API, ~10초/50개)
  │    ├─ Strategy 1: dataset 채우기 + gfn_transaction JS 호출
  │    └─ Strategy 2: SSV body 수동 구성 + fetch() 직접 호출
  ├─ Level 2: BatchGridInputter   (Hybrid 배치, ~25초/50개)
  │    └─ dataset.setColumn() + confirm_order() 버튼
  └─ Level 3: Selenium fallback   (기존 방식, ~170초/50개)
       └─ 개별 상품 UI 조작
```

### 2.2 Key Components

| Component | File | Role |
|-----------|------|------|
| DirectApiOrderSaver | `src/order/direct_api_saver.py` | 2-tier Direct API 저장 |
| BatchGridInputter | `src/order/batch_grid_input.py` | 넥사크로 dataset 배치 입력 |
| OrderExecutor (통합) | `src/order/order_executor.py` | 3-level 폴백 오케스트레이션 |
| capture_save_api.py | `scripts/capture_save_api.py` | Save API 캡처 스크립트 |
| save_api_template.json | `captures/save_api_template.json` | 검증된 API 구조 템플릿 |

### 2.3 Feature Flags (constants.py)

| Flag | Default | Description |
|------|---------|-------------|
| DIRECT_API_ORDER_ENABLED | True | Direct API 발주 저장 활성화 |
| BATCH_GRID_INPUT_ENABLED | True | Hybrid 배치 그리드 활성화 |
| DIRECT_API_ORDER_MAX_BATCH | 50 | 1회 최대 상품 수 |
| DIRECT_API_ORDER_VERIFY | True | 저장 후 검증 활성화 |

### 2.4 Timing Constants (timing.py)

| Constant | Value | Description |
|----------|-------|-------------|
| DIRECT_API_SAVE_TIMEOUT_MS | 15000 | 저장 API 타임아웃 (ms) |
| DIRECT_API_VERIFY_WAIT | 2.0 | 저장 후 검증 대기 (초) |
| BATCH_GRID_POPULATE_WAIT | 1.0 | 배치 입력 안정화 대기 (초) |
| BATCH_GRID_SAVE_WAIT | 3.0 | 배치 저장 응답 대기 (초) |
| BATCH_GRID_ROW_DELAY_MS | 10 | 행 추가 간 딜레이 (ms) |

## 3. DirectApiOrderSaver Detail

### 3.1 SaveResult dataclass
- success: bool
- saved_count: int
- failed_items: List[str]
- elapsed_ms: float
- method: str ('direct_api' | 'direct_api_transaction' | 'direct_api_fetch')
- message: str
- response_preview: str

### 3.2 Strategy 1: gfn_transaction

1. 넥사크로 폼 탐색 (STBJ030_M0 → 폴백 동적 탐색)
2. dataset 바인딩 객체 획득 (gdList._binddataset)
3. clearData() + addRow() + setColumn() 으로 상품 데이터 채우기
   - **RowType = I (Insert)** — U (Update) 아님, applyChange() 불필요 (라이브 검증 2026-02-28)
   - **PYUN_QTY = 배수** (핵심), ORD_MUL_QTY는 빈값
   - TOT_QTY = PYUN_QTY × ORD_UNIT_QTY
4. dsSaveChk dataset: **빈 데이터셋** (컬럼 헤더만 전송, 데이터 행 없음)
5. workForm.gfn_transaction('savOrd', 'saveOrd', 'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk', 'gds_ErrMsg=gds_ErrMsg', strArg, 'fn_callback')
   - svcURL = 'saveOrd' (폼이 `/stbjz00/` 자동 prefix)
   - inDS에 `:U` 필터 없음 (라이브 캡처 확인)
   - outDS = 'gds_ErrMsg=gds_ErrMsg'
6. fn_callBack에서 errCd 확인 (0 = 성공, 99999 = 발주시간 제한)
7. Promise로 비동기 콜백 대기 (timeout 포함)

### 3.3 Strategy 2: fetch() 폴백

1. has_template 확인 (캡처 파일 또는 인터셉터에서 로드)
2. build_ssv_body()로 SSV body 생성
   - 템플릿 있으면: _replace_items_in_template() (컬럼 순서 보존)
   - 없으면: 기본 SSV 구성 (5컬럼 축약)
3. fetch(endpoint, {method: 'POST', body: ssv_body}) 호출
4. 응답에서 ErrorCode:string=0 확인

### 3.4 SSV Protocol (라이브 검증 2026-02-28)

- 구분자: RS(\u001e) 줄 구분, US(\u001f) 컬럼 구분, ETX(\u0003) 종료
- 컬럼 타입 형식: `COLNAME:TYPE(SIZE)` (예: `STORE_CD:STRING(256)`, `NOW_QTY:INT(256)`)
- SSV body 구조 (34줄, 2122바이트):
  - 줄 0: `SSV:utf-8`
  - 줄 1~20: 세션 변수 20개 (`key=value` 형식, RS 구분)
  - 줄 21~25: 파라미터 5개 (strPyunsuId, strOrdInputFlag, GV_MENU_ID, GV_USERFLAG, GV_CHANNELTYPE)
  - 줄 26: `Dataset:dsGeneralGrid`
  - 줄 27: 컬럼 헤더 (55개 컬럼, US 구분)
  - 줄 28: 데이터 행 (RowType=I, US 구분 값)
  - 줄 29: 빈 줄 (dsGeneralGrid 종료)
  - 줄 30: `Dataset:dsSaveChk`
  - 줄 31: 컬럼 헤더 (6개 컬럼)
  - 줄 32~33: 빈 줄 (데이터 없음)
- dsGeneralGrid 핵심 컬럼: STORE_CD, ORD_YMD, ITEM_CD, **PYUN_QTY** (배수), TOT_QTY, ORD_UNIT_QTY
- dsSaveChk: 6컬럼 헤더만 (ITEM_CD, ITEM_NM, MID_NM, ORD_YMD, ORD_MUL_QTY, ORD_INPUT_NM) — **데이터 행 없음**
- 성공 응답: ErrorCode:string=0 또는 ErrorCode:string=99999 (gds_ErrMsg TYPE=NORMAL = 정상 처리)
- 실패 응답: ErrorCode 값 + gds_ErrMsg TYPE≠NORMAL 또는 에러 메시지 존재 시

### 3.5 Template Loading (3가지)

1. 캡처 파일 로드: set_template_from_file(path)
   - save_gfn_transactions[] 또는 save_xhr_requests[] 또는 endpoint+gfn_transaction 구조
2. 런타임 인터셉터: install_interceptor() → capture_save_template()
   - gfn_transaction 오버라이드 + XHR 캡처
3. 인라인 (없을 때): 기본 SSV 구성

### 3.6 Verification (verify_save)

- 저장 후 그리드 dataset을 읽어 item_cd + ord_qty 매칭
- matched/mismatched/missing 분류
- DIRECT_API_ORDER_VERIFY=False면 스킵

## 4. BatchGridInputter Detail

### 4.1 Flow
1. check_grid_ready() — dataset 존재 + 컬럼 확인
2. populate_grid(orders) — addRow() + setColumn(ITEM_CD, ORD_QTY) + enableredraw
3. input_batch(orders, date, confirm_fn) — populate + save
4. _confirm_save() — 저장 버튼 DOM/넥사크로 탐색 + Alert 처리

### 4.2 Grid State API
- check_grid_ready() → {ready, dsName, rowCount, columns}
- read_grid_state() → [{ITEM_CD, ORD_QTY, ...}]
- clear_grid() → bool

## 5. OrderExecutor Integration

### 5.1 execute_orders() 3-Level 흐름

```python
for order_date, items in grouped_orders.items():
    # Level 1: Direct API
    if DIRECT_API_ORDER_ENABLED and not dry_run:
        api_result = self._try_direct_api_save(items, order_date)
        if api_result.success: continue

    # Level 2: Batch Grid
    if BATCH_GRID_INPUT_ENABLED and not dry_run and len(items) >= 3:
        batch_result = self._try_batch_grid_input(items, order_date)
        if batch_result.success: continue

    # Level 3: Selenium fallback
    # ... 기존 개별 입력 로직
```

### 5.2 _try_direct_api_save()
- DirectApiOrderSaver 인스턴스 생성
- 캡처 파일 4곳 탐색 (captures/save_api_capture_valid.json 우선)
- 템플릿 없으면 인터셉터 → 실시간 캡처
- save_orders() → verify_save()

### 5.3 _try_batch_grid_input()
- BatchGridInputter 인스턴스 생성
- check_grid_ready() 확인
- input_batch(items, date, confirm_fn=self.confirm_order) — confirm_order 재사용

## 6. Capture Script

### 6.1 scripts/capture_save_api.py
- 이중 인터셉터: gfn_transaction 오버라이드 + XHR POST 캡처
- 캡처 결과를 captures/save_api_capture_valid.json에 저장
- 사용법: `python scripts/capture_save_api.py --item 8801045571416`

## 7. Test Coverage

| Test File | Count | Coverage |
|-----------|-------|----------|
| test_direct_api_saver.py | 20 | DirectApiOrderSaver 전체 |
| test_order_executor_direct_api.py | 12 | OrderExecutor 3-level 통합 |
| test_batch_grid_input.py | 10 | BatchGridInputter 전체 |
| **Total** | **42** | |

### 7.1 Key Test Scenarios
- 2-tier 전략 (transaction 성공/실패 → fetch 폴백)
- SSV body 생성 + 템플릿 교체
- 날짜 형식 정규화 (YYYY-MM-DD → YYYYMMDD)
- max_batch 초과 거부
- dry_run 모드
- 3-level 폴백 체인 (L1→L2→L3)
- 캡처 파일 로드 + 인터셉터 캡처
- 검증 (verify_save) 성공/실패/스킵

## 8. Multiplier Calculation

```python
@staticmethod
def _calc_multiplier(order: Dict) -> int:
    multiplier = order.get('multiplier', 0)
    if multiplier and multiplier > 0:
        return int(multiplier)
    qty = order.get('final_order_qty', 0)
    unit = order.get('order_unit_qty', 1) or 1
    return max(1, (qty + unit - 1) // unit)
```

## 9. Error Handling

- 모듈 미설치: ImportError 캐치 → None 반환 → 다음 Level
- 폼 탐색 실패: form_not_found → SaveResult(success=False)
- gfn_transaction 타임아웃: Promise timeout → error 반환
- fetch 실패: HTTP 에러 → SaveResult(success=False)
- 예외: Exception catch → SaveResult(success=False, message)

## 10. 라이브 테스트 검증 (2026-02-28)

### 10.1 테스트 환경
- 매장: 46513
- 테스트 상품: 8801116012176 (LIL액상카트리지)
- 도구: Claude in Chrome 확장 + MCP JavaScript 실행

### 10.2 핵심 발견사항

| 항목 | 설계 가정 | 라이브 실제값 | 조치 |
|------|---------|-------------|------|
| RowType | U (Update, RowType=4) | **I (Insert, RowType=2)** | 코드 수정 완료 |
| 배수 컬럼 | ORD_MUL_QTY | **PYUN_QTY** (ORD_MUL_QTY는 빈값) | 코드 수정 완료 |
| dsSaveChk | 데이터 병행 채우기 | **빈 데이터셋** (컬럼 헤더만) | 코드 수정 완료 |
| inDS 필터 | dsGeneralGrid=dsGeneralGrid:U | **:U 없음** | 코드 수정 완료 |
| svcURL | stbjz00/saveOrd | **saveOrd** (폼이 prefix 자동 추가) | 코드 수정 완료 |
| svcURL (2차 수정) | saveOrd | **stbjz00/saveOrd** (gfn_transaction이 svc:: 자동추가) | 코드 수정 완료 |
| 컬럼 타입 | STRING:256 | **STRING(256)** | 코드 수정 완료 |
| nexacro 접근 | app | **nexacro.getApplication()** | 코드 수정 완료 |

### 10.3 테스트 결과

#### 단건 테스트 (1개 상품)
- API 호출: 성공 (HTTP 200)
- 서버 응답: ErrorCode=99999, gds_ErrMsg TYPE=NORMAL — **정상 처리** (그리드 초기화 확인)
- ErrorCode=99999는 발주시간 제한이 아니라 정상 응답 코드임 (2차 검증)
- SSV body 캡처: 2122바이트, 34줄, 55컬럼 완전 캡처
- 캡처 파일: `captures/saveOrd_live_capture_20260228.json`

#### 50개 일괄 테스트 (최대 배치)
- Body: **10,130 bytes** (50개 상품, 발주수량 251개)
- HTTP Status: **200 OK**
- ErrorCode: **99999 (정상)**
- 소요시간: **~12.7초** (기존 Selenium ~170초 → **93% 단축**)
- 그리드 초기화 확인

#### 10개 상품 테스트
- Body: **3,530 bytes** (10개 상품, 발주수량 37개)
- HTTP Status: **200 OK**
- ErrorCode: **99999 (정상)**
- 그리드 초기화 확인

### 10.4 URL 해석 메커니즘 (2차 발견)

gfn_transaction은 svcURL 파라미터에 `svc::` prefix를 자동 추가:

| 입력 svcURL | gfn_transaction 변환 | 실제 URL | 결과 |
|-------------|---------------------|----------|------|
| `saveOrd` | `svc::saveOrd` | `/saveOrd` | **404** |
| `stbjz00/saveOrd` | `svc::stbjz00/saveOrd` | `/stbjz00/saveOrd` | **200 OK** |

수정: `SAVE_SVC_URL = 'stbjz00/saveOrd'`

### 10.5 7시 스케줄러 라이브 실행 결과 (2026-02-28 13:17)

매장 46513, `--now --store 46513` 실행:

| 날짜 | 상품수 | 방법 | 결과 | 소요시간 | prefetch |
|------|--------|------|------|----------|----------|
| 2026-03-01 | 55개 | Batch Grid (폴백) | 성공 55/55 | 5,729ms | N/A |
| 2026-03-02 | 29개 | **Direct API** | 성공 29/29 검증통과 | **1,575ms** | 29/29 |
| 2026-03-03 | 17개 | **Direct API** | 성공 17/17 검증통과 | **1,101ms** | 17/17 |
| 2026-03-01 | 1개(DB교정) | **Direct API** | 성공 1/1 검증통과 | **645ms** | 1/1 |

- 03-01 (55개): `batch size 55 exceeds max 50` → Batch Grid 자동 폴백
- 총 결과: **102건 발주 성공, 0건 실패**

### 10.6 테스트 vs 라이브 플로우 차이점

#### 테스트 (Chrome 확장 수동, prefetch 없음)
```
1. 그리드에 상품코드+배수만 직접 입력 (POPULATE_DATASET_JS)
2. prefetch 없이 gfn_transaction 호출
3. 그리드에 상품명/가격 등 미표시 상태로 저장
4. 서버가 ITEM_CD + PYUN_QTY + TOT_QTY 최소 필드로 처리
```

#### 라이브 스케줄러 (selSearch prefetch 포함)
```
1. navigate_to_single_order() → 날짜 선택 → 폼 완전 로드
2. Phase 0: selSearch API prefetch → 상품별 전체 55개 컬럼 조회
   - GET /stbj030/selSearch 를 상품코드별 fetch (동시 5개)
   - 응답: SSV 형식, dsItem 전체 필드 (ITEM_NM, MAEGA_AMT, PROFIT_RATE 등)
3. Phase 1: POPULATE_DATASET_JS
   - prefetch 필드로 ALL columns 먼저 설정 (3a: fields loop)
   - 핵심 필드 오버라이드 (3b: ITEM_CD, PYUN_QTY, TOT_QTY, ORD_YMD)
   → 그리드에 상품명/가격/이익율 등 전체 정보 표시됨
4. Phase 2: gfn_transaction 호출 → 전체 필드 포함 SSV body 전송
5. 검증: 서버 응답 후 그리드 상태 확인 (29/29, 17/17 일치)
```

#### 핵심 차이 요약

| 항목 | 테스트 | 라이브 |
|------|--------|--------|
| selSearch prefetch | 없음 | 있음 (상품별 전체 필드) |
| 그리드 표시 | 상품코드만 | 상품명/가격/이익율 전체 |
| SSV body 컬럼 수 | 핵심 5~6개만 값 있음 | 전체 55개 컬럼 값 채움 |
| 서버 처리 | 최소 필드로 처리 가능 | 완전한 데이터 전달 (안전) |
| 폼 상태 | 이미 열린 상태 가정 | navigate + 날짜선택 후 실행 |

**결론**: 라이브에서는 prefetch가 그리드에 상품 정보를 완전히 채운 후 gfn_transaction을 호출하므로, 서버에 전달되는 SSV body에 모든 필드가 포함됨. 테스트에서는 최소 필드만 있었지만 서버가 수용했음 (ErrorCode=99999). 실제 발주시간(07:00~)에는 전체 필드가 있어야 안전.

### 10.7 배치 분할 설계 (v2)

50개 초과 시 서브배치로 분할하여 Direct API 유지:

```
execute_orders([55개], date)
  └─ _try_direct_api_save([55개])
       └─ save_orders([55개])
            ├─ chunk 1: [50개] → prefetch → populate → gfn_transaction → 검증
            ├─ (그리드 초기화 대기)
            └─ chunk 2: [5개] → prefetch → populate → gfn_transaction → 검증
```

- 청크당 최대 50개 (DIRECT_API_ORDER_MAX_BATCH)
- 각 청크는 독립적 3단계 (prefetch → populate → gfn_transaction)
- 청크 간 2초 대기 (그리드 초기화 + 서버 처리 시간)
- 하나라도 실패하면 나머지는 Level 2로 폴백

### 10.8 남은 검증
- [x] 복수 상품 일괄 저장 테스트 (50개, 10개 성공)
- [ ] 발주시간대(07:00~) 실제 발주 반영 확인
- [x] 7시 발주 로직 통합 완료
- [x] 라이브 스케줄러 실행 검증 (29건, 17건, 1건 Direct API 성공)
- [x] 배치 분할 설계 (50개 초과 처리)
