# Design: order-save-fix (발주 저장 실패 근본 수정)

> **라이브 검증 완료**: 2026-03-02, ErrorCode=99999(정상), 저장+취소 모두 확인

## 1. 아키텍처 개요

### 현행 3-tier 발주 저장 흐름
```
execute_orders()
  ├─ Level 1: DirectApiOrderSaver._save_via_transaction()
  │    ├─ Phase 0: selSearch 프리페치 (17/125 성공)
  │    ├─ Phase 1: POPULATE_DATASET_JS (dataset 채우기)
  │    │    ├─ 3a: 프리페치 필드 설정 (성공 항목만)
  │    │    ├─ 3b: 핵심 필드 오버라이드 (ITEM_CD, PYUN_QTY, TOT_QTY, ORD_YMD, ORD_UNIT_QTY)
  │    │    └─ 3c: 숫자 컬럼 빈값→0 기본값 (이중 안전망)
  │    └─ Phase 2: CALL_GFN_TRANSACTION_JS (gfn_transaction 호출)
  │
  ├─ Level 2: BatchGridInputter.input_batch()
  │    ├─ populate_grid() → PYUN_QTY + 필수 필드 + 숫자 기본값
  │    └─ _confirm_save() (저장 버튼 클릭)
  │
  └─ Level 3: Selenium 개별 입력 (input_product 반복)
```

## 2. 근본 원인 분석 (라이브 검증 2026-03-02)

### 2.1 Bug 1: NumberFormatException — OLD_PYUN_QTY 타입 불일치

**증상**: BGF 서버가 `java.lang.NumberFormatException: For input string: ""`로 저장 거부

**근본 원인**: `OLD_PYUN_QTY` 컬럼의 서버-클라이언트 타입 불일치
- **서버(selSearch 응답)**: `OLD_PYUN_QTY:bigdecimal(0)` — 숫자 타입으로 정의
- **넥사크로 폼(dsGeneralGrid)**: `OLD_PYUN_QTY:STRING(256)` — 문자열 타입으로 정의
- **결과**: `getColumnInfo().type` → `'STRING'` 반환 → 숫자 감지 실패 → 빈 문자열("") 전송
- **서버 동작**: `parseInt("")` 호출 시 `NumberFormatException` 발생

**수정**: `_knownNums` 하드코딩 목록에 `OLD_PYUN_QTY` 추가 (3개 파일)

### 2.2 Bug 2: 발주일자 불일치

**증상**: 10시 이후 실행 시 Direct API가 당일(3/2)로 발주하지만, 팝업은 다음날(3/3)만 선택 가능

**근본 원인**: `execute_orders()`에서 `group_orders_by_date()` 결과의 날짜를 그대로 사용
- `select_order_day()`가 `_last_selected_date`에 실제 선택된 날짜를 저장하지만 미사용

**수정**: `select_order_day()` 호출 후 `_last_selected_date`로 `order_date` 보정

## 3. 수정 상세

### 3.1 Fix 1: 숫자 컬럼 기본값 — `_knownNums` 확장 (3개 파일)

**최종 목록** (12개 컬럼):
```
HQ_MAEGA_SET, ORD_UNIT_QTY, ORD_MULT_ULMT, ORD_MULT_LLMT,
NOW_QTY, ORD_MUL_QTY, OLD_PYUN_QTY, TOT_QTY,
PAGE_CNT, EXPIRE_DAY, PROFIT_RATE, PYUN_QTY
```

**수정 파일 3개**:

| 파일 | 위치 | 변경 |
|------|------|------|
| `direct_api_saver.py` | POPULATE_DATASET_JS `_knownNums` (line ~497) | +`OLD_PYUN_QTY` |
| `direct_api_saver.py` | `_replace_items_in_template()` `_KNOWN_NUMERIC` (line ~1396) | +`OLD_PYUN_QTY`, +`PYUN_QTY` |
| `batch_grid_input.py` | populate_grid JS `_knownNums` (line ~220) | +`OLD_PYUN_QTY` |

**이중 안전망 로직** (JS):
```
FOR 각 행(row)의 모든 컬럼(col):
  IF 값이 null/undefined/'' THEN:
    1차: getColumnInfo(ci).type → INT/BIGDECIMAL/FLOAT/NUMBER → 0 설정
    2차: _knownNums 목록에 포함 → 0 설정 (타입 불일치 대비)
```

### 3.2 Fix 2: 발주일자 보정 — `_last_selected_date` 활용

**위치**: `order_executor.py` `execute_orders()`, `select_order_day()` 호출 직후

**로직**:
```python
# select_order_day()가 실제 선택한 날짜를 _last_selected_date에 저장
actual_date = self._last_selected_date or order_date
if actual_date != order_date:
    logger.info(f"발주일자 보정: {order_date} -> {actual_date}")
    order_date = actual_date
```

**방어적 초기화** (`__new__` 사용 테스트 대응):
```python
if not hasattr(self, '_last_selected_date'):
    self._last_selected_date = None
```

### 3.3 로깅 강화

**POPULATE_DATASET_JS** — 숫자 컬럼 채움 로그:
- `numericFilled`: 0으로 채운 숫자 컬럼 수
- `numericFilledCols`: 채운 컬럼명 목록 (디버깅용)

**order_executor.py** — 날짜 보정 로그:
- `발주일자 보정: {기존} -> {실제}` (차이 있을 때만)

**_save_via_transaction()** — 프리페치/저장 상세 로그:
- 프리페치 성공/전체 비율
- gfn_transaction 결과 (errCd, errMsg)
- 숫자 컬럼 채움 결과 (Python SSV 전략)

## 4. 컬럼 타입 판별 전략 (이중 안전망)

```
방법 1 (우선): 넥사크로 API
  ds.getColumnInfo(index) -> {type: 'INT'|'STRING'|'BIGDECIMAL'|...}
  -> INT, BIGDECIMAL, FLOAT, NUMBER -> 0 설정

방법 2 (폴백): 하드코딩 목록 (_knownNums)
  OLD_PYUN_QTY 같이 서버=bigdecimal, 클라이언트=STRING인 경우를 커버
  현재 12개 컬럼:
    HQ_MAEGA_SET, ORD_UNIT_QTY, ORD_MULT_ULMT, ORD_MULT_LLMT,
    NOW_QTY, ORD_MUL_QTY, OLD_PYUN_QTY, TOT_QTY,
    PAGE_CNT, EXPIRE_DAY, PROFIT_RATE, PYUN_QTY
```

## 5. 라이브 검증 결과 (2026-03-02)

### 검증 환경
- 점포: 46513 (이천호반베르디움점)
- 테스트 상품: 오뚜기스파게티컵 (8801045571416)
- 시간: 10시 이후 (3/3 자동 선택)

### 검증 항목

| 항목 | 결과 | 상세 |
|------|------|------|
| 숫자 컬럼 채움 | OK | `emptyNumeric: []` — 빈 숫자 컬럼 0개 |
| OLD_PYUN_QTY | OK | 값="0" (빈 문자열 아님) |
| gfn_transaction 저장 | OK | ErrorCode=99999, TYPE=NORMAL |
| selSearch 재검증 | OK | PYUN_QTY=1, OLD_PYUN_QTY=1, TOT_QTY=12 |
| PYUN_QTY=0 취소 | OK | 저장 후 selSearch: PYUN_QTY=0 |
| 날짜 보정 | OK | 3/2 → 3/3 자동 보정 |
| gfn_transaction 파라미터 순서 | OK | `(txId, url, inDS, outDS, args, callback)` |

### gfn_transaction 파라미터 (정확한 순서)
```javascript
workForm.gfn_transaction(
    'save',                                                      // txId
    'stbjz00/saveOrd',                                           // url
    'dsGeneralGrid=dsGeneralGrid:U dsSaveChk=dsSaveChk',        // inDS (:U 필터)
    'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk',          // outDS
    'strPyunsuId="0" strOrdInputFlag="04"',                     // args (따옴표 필수)
    'fn_callback'                                                // callback
);
```

## 6. 영향 범위

- `direct_api_saver.py`: POPULATE_DATASET_JS `_knownNums`, `_KNOWN_NUMERIC` set
- `batch_grid_input.py`: populate_grid JS `_knownNums`
- `order_executor.py`: `execute_orders()` 날짜 보정 로직, 방어적 초기화
- `test_date_filter_order.py`: `_make_executor()` `_last_selected_date` 초기화
- 테스트 영향: 2904개 전부 통과
- 외부 인터페이스 변경: 없음 (내부 데이터 채우기만 변경)
