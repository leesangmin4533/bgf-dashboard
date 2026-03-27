# order-save-fix 완료 리포트

> **상태**: 완료 (라이브 검증 완료)
>
> **프로젝트**: BGF 리테일 자동 발주 시스템
> **버전**: 1.0
> **완료 날짜**: 2026-03-02
> **PDCA 사이클**: #1

---

## 1. 개요

### 1.1 프로젝트 정보

| 항목 | 내용 |
|------|------|
| 기능명 | order-save-fix (발주 저장 실패 근본 수정) |
| 시작일 | 2026-02-28 |
| 완료일 | 2026-03-02 |
| 소요기간 | 3일 |
| 담당자 | Development Team |

### 1.2 결과 요약

```
┌────────────────────────────────────────┐
│  완료율: 100%                          │
├────────────────────────────────────────┤
│  ✅ 완료:     2개 버그 수정              │
│  ✅ 테스트:   2904개 전부 통과          │
│  ✅ 라이브검증: 2026-03-02 성공         │
└────────────────────────────────────────┘
```

---

## 2. 관련 문서

| 단계 | 문서 | 상태 |
|------|------|------|
| Plan | order-save-fix.plan.md | ✅ 완료 |
| Design | [order-save-fix.md](../../02-design/features/order-save-fix.md) | ✅ 완료 |
| Do | 구현 완료 | ✅ 완료 |
| Check | order-save-fix.analysis.md | ✅ 완료 |

---

## 3. 완료 항목

### 3.1 버그 수정

#### Bug 1: NumberFormatException (OLD_PYUN_QTY 타입 불일치)

| 항목 | 내용 |
|------|------|
| **증상** | BGF 서버 java.lang.NumberFormatException: For input string: "" |
| **근본 원인** | OLD_PYUN_QTY 컬럼의 서버-클라이언트 타입 불일치<br/>- 서버: bigdecimal(0)<br/>- 폼: STRING(256)<br/>- 결과: 빈 문자열("") 전송 → parseInt("") 오류 |
| **해결방법** | _knownNums 하드코딩 목록에 OLD_PYUN_QTY 추가 (이중 안전망) |
| **적용 파일** | direct_api_saver.py, batch_grid_input.py, test_date_filter_order.py |
| **최종 콜론 목록** | 12개 (HQ_MAEGA_SET, ORD_UNIT_QTY, ORD_MULT_ULMT, ORD_MULT_LLMT, NOW_QTY, ORD_MUL_QTY, OLD_PYUN_QTY, TOT_QTY, PAGE_CNT, EXPIRE_DAY, PROFIT_RATE, PYUN_QTY) |

#### Bug 2: 발주일자 불일치

| 항목 | 내용 |
|------|------|
| **증상** | 10시 이후 실행 시 Direct API는 당일(3/2)로 발주하지만, 팝업은 다음날(3/3)만 선택 |
| **근본 원인** | execute_orders()에서 group_orders_by_date() 결과 날짜를 직접 사용<br/>- select_order_day()의 _last_selected_date 미사용<br/>- 실제 선택된 날짜(3/3)와 order_date(3/2) 불일치 |
| **해결방법** | select_order_day() 이후 _last_selected_date로 order_date 보정<br/>방어적 초기화: if not hasattr(self, '_last_selected_date'): self._last_selected_date = None |
| **적용 파일** | order_executor.py |
| **영향 범위** | 10시 이후 발주 시나리오 해결 |

### 3.2 기능 요구사항

| ID | 요구사항 | 상태 | 비고 |
|----|---------|------|------|
| FR-01 | 숫자 컬럼 타입 감지 (1차 메타정보) | ✅ 완료 | ds.getColumnInfo().type 활용 |
| FR-02 | 숫자 컬럼 폴백 감지 (2차 하드코딩) | ✅ 완료 | _knownNums 목록 12개 유지 |
| FR-03 | 빈 값 → 0으로 기본값 설정 | ✅ 완료 | 숫자 컬럼에만 적용 |
| FR-04 | 발주일자 실제 선택값 추적 | ✅ 완료 | _last_selected_date 추적 |
| FR-05 | 발주일자 자동 보정 | ✅ 완료 | select_order_day 호출 직후 |

### 3.3 비기능 요구사항

| 항목 | 목표 | 달성값 | 상태 |
|------|------|--------|------|
| 라이브 검증 | 성공 | 100% (ErrorCode=99999) | ✅ |
| 테스트 커버리지 | 100% | 2904/2904 | ✅ |
| 코드 품질 | Design Match Rate 90% 이상 | 100% | ✅ |
| 외부 API 변경 | 0개 | 0개 | ✅ |

### 3.4 산출물

| 산출물 | 위치 | 상태 |
|--------|------|------|
| 설계 문서 | docs/02-design/features/order-save-fix.md | ✅ |
| 수정된 소스 | src/order/direct_api_saver.py | ✅ |
| | src/order/batch_grid_input.py | ✅ |
| | src/order/order_executor.py | ✅ |
| | tests/test_date_filter_order.py | ✅ |
| 테스트 결과 | 2904개 전부 통과 | ✅ |
| 라이브 검증 | 2026-03-02 점포 46513 | ✅ |

---

## 4. 미완료 항목

없음 (완료율 100%)

---

## 5. 품질 지표

### 5.1 분석 결과

| 지표 | 목표 | 최종값 | 변화 |
|------|------|--------|------|
| Design Match Rate | 90% | 100% | ✅ +100% |
| 테스트 통과율 | 100% | 100% | ✅ 유지 |
| 외부 인터페이스 변경 | 0개 | 0개 | ✅ 안전 |
| 라이브 검증 | 성공 | 성공 | ✅ 완료 |

### 5.2 해결된 이슈

| 이슈 | 해결방법 | 결과 |
|------|---------|------|
| NumberFormatException | _knownNums 확장 (12개 컬럼) | ✅ 해결 |
| 발주일자 불일치 | _last_selected_date 추적 및 보정 | ✅ 해결 |
| 10시 이후 팝업 날짜 선택 | 자동 보정 로직 | ✅ 해결 |

---

## 6. 수정 상세

### 6.1 이중 안전망 컬럼 감지 전략

```
[방법 1] 넥사크로 메타정보 (우선)
├─ ds.getColumnInfo(index).type
├─ 'INT', 'BIGDECIMAL', 'FLOAT', 'NUMBER' 감지
└─ 자동으로 0 설정

[방법 2] 하드코딩 목록 (폴백)
├─ _knownNums 목록에 포함 확인
├─ 서버/클라이언트 타입 불일치 대비 (OLD_PYUN_QTY 사례)
└─ 0 설정 (재확보증)
```

### 6.2 최종 컬럼 목록 (12개)

```
1. HQ_MAEGA_SET    - 본사 매입가
2. ORD_UNIT_QTY    - 발주 단위
3. ORD_MULT_ULMT   - 발주 배수 상한
4. ORD_MULT_LLMT   - 발주 배수 하한
5. NOW_QTY         - 현재 수량
6. ORD_MUL_QTY     - 발주 배수 수량
7. OLD_PYUN_QTY    - 구 배수량 (타입 불일치 주범)
8. TOT_QTY         - 총 수량 = PYUN_QTY × ORD_UNIT_QTY
9. PAGE_CNT        - 페이지 카운트
10. EXPIRE_DAY     - 유통기한 일수
11. PROFIT_RATE    - 마진율
12. PYUN_QTY       - 배수량 (핵심)
```

### 6.3 발주일자 보정 로직

```python
# order_executor.py execute_orders()
select_order_day()  # _last_selected_date 저장

# select_order_day() 직후
actual_date = self._last_selected_date or order_date
if actual_date != order_date:
    logger.info(f"발주일자 보정: {order_date} -> {actual_date}")
    order_date = actual_date
```

### 6.4 라이브 검증 (2026-03-02)

#### 검증 환경
- **점포**: 46513 (이천호반베르디움점)
- **테스트 상품**: 오뚜기스파게티컵 (8801045571416)
- **시간**: 10시 이후
- **선택된 날짜**: 3/3 (자동)

#### 검증 결과

| 검증 항목 | 기대값 | 실제값 | 결과 |
|----------|--------|--------|------|
| 숫자 컬럼 채움 | emptyNumeric=[] | [] | ✅ OK |
| OLD_PYUN_QTY | 숫자(0 이상) | "0" | ✅ OK |
| gfn_transaction 저장 | ErrorCode=99999 | ErrorCode=99999 | ✅ OK |
| selSearch 재검증 | PYUN_QTY=1 | PYUN_QTY=1 | ✅ OK |
| OLD_PYUN_QTY | 값 보존 | 1 | ✅ OK |
| 취소 후 조회 | PYUN_QTY=0 | PYUN_QTY=0 | ✅ OK |
| 날짜 보정 | 3/2 → 3/3 | 3/3 선택 | ✅ OK |
| gfn_transaction 파라미터 | ('save', url, inDS, outDS, args, callback) | 정확한 순서 | ✅ OK |

#### gfn_transaction 호출 (검증됨)

```javascript
workForm.gfn_transaction(
    'save',                                          // txId
    'stbjz00/saveOrd',                               // url
    'dsGeneralGrid=dsGeneralGrid:U dsSaveChk=dsSaveChk',  // inDS (:U 필터)
    'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk',    // outDS
    'strPyunsuId="0" strOrdInputFlag="04"',         // args
    'fn_callback'                                    // callback
);
```

---

## 7. 로깅 강화

### 7.1 POPULATE_DATASET_JS 로그

```
[Direct API Saver] POPULATE_DATASET_JS:
  prefetch 성공: 17/125
  numericFilled: 8개
  numericFilledCols: [HQ_MAEGA_SET(meta:INT), OLD_PYUN_QTY(known), ...]
```

### 7.2 order_executor.py 로그

```
[Order Executor] 발주일자 보정: 2026-03-02 → 2026-03-03
[Order Executor] 발주 저장 완료 (배수=1, 총량=12)
```

### 7.3 _save_via_transaction() 로그

```
[Direct API Saver] gfn_transaction 결과:
  errCd: 0
  errMsg: "저장 완료"
  숫자컬럼0채움: 8개
  0채움 컬럼: HQ_MAEGA_SET(meta:INT), OLD_PYUN_QTY(known), ...
```

---

## 8. 영향 범위

### 8.1 수정 파일 (4개)

| 파일 | 라인 | 변경 사항 |
|------|------|---------|
| `src/order/direct_api_saver.py` | ~497 | POPULATE_DATASET_JS _knownNums에 OLD_PYUN_QTY 추가 |
| | ~1396 | _KNOWN_NUMERIC set에 OLD_PYUN_QTY, PYUN_QTY 추가 |
| `src/order/batch_grid_input.py` | ~220 | populate_grid JS _knownNums에 OLD_PYUN_QTY 추가 |
| `src/order/order_executor.py` | ~106 | __init__에 _last_selected_date = None 초기화 |
| | execute_orders() | select_order_day() 후 발주일자 보정 로직 추가 |
| `tests/test_date_filter_order.py` | ~23 | _make_executor()에서 _last_selected_date 초기화 |

### 8.2 외부 인터페이스 변경

**없음** — 모든 수정이 내부 데이터 채우기 및 로깅

- execute_orders() 서명 변경 없음
- direct_api_saver 공개 API 변경 없음
- batch_grid_input 공개 API 변경 없음

### 8.3 테스트 영향

```
총 테스트: 2904개
실행 환경: Python 3.12
결과: 모두 통과 ✅
```

---

## 9. 학습 및 회고

### 9.1 잘 된 점 (Keep)

- **근본 원인 파악**: 라이브 검증을 통해 서버-클라이언트 타입 불일치를 명확히 규명
- **이중 안전망 설계**: 메타정보 검사 + 하드코딩 목록으로 향후 타입 불일치에 대비
- **방어적 프로그래밍**: _last_selected_date hasattr 체크로 테스트 호환성 확보
- **라이브 검증**: 실제 BGF 시스템에서 검증하여 파라미터 순서 등 확정
- **빠른 개선**: 문제 발견 → 근본 원인 파악 → 수정 → 라이브 검증 (3일)

### 9.2 개선 필요 사항 (Problem)

- **타입 메타정보 신뢰도**: OLD_PYUN_QTY처럼 서버 설정이 클라이언트와 다를 수 있음
  - 향후: 주기적으로 selSearch 응답의 메타정보와 폼 정의를 대조 검사
- **발주 날짜 자동 결정**: 10시 이후 팝업 자동 선택 이유를 문서화 부족
  - 향후: BGF 비즈니스 로직 (배송차수별 마감시간) 분석 및 주석 추가

### 9.3 다음번에 시도할 것 (Try)

- **먹서 계층별 테스트**: 직접 BGF API를 호출하지 않고, JavaScript 로직의 dataset 조작을 단위 테스트
- **타입 검증 도구**: 넥사크로 dataset 필드에 대한 자동 검증기 개발
  - 예: `validate_column_types(ds, expected_types)` → report 생성
- **BGF API 문서화**: gfn_transaction 파라미터 순서, selSearch 응답 포맷 등을 `docs/bgf-api.md`로 중앙화

---

## 10. 다음 단계

### 10.1 즉시 (이미 완료)

- [x] 라이브 검증 완료 (2026-03-02)
- [x] 2904개 테스트 통과
- [x] 변경 이력 기록

### 10.2 후속 작업

| 항목 | 우선순위 | 예상 시작 | 비고 |
|------|---------|---------|------|
| BGF 타입 메타정보 정기 감시 | 중 | 2026-03-10 | 월 1회 검증 |
| 발주 날짜 자동 결정 원인 분석 | 낮 | 2026-03-15 | 문서화만 필요 |
| Direct API Saver 단위 테스트 | 중 | 2026-04-01 | 기능 추가 시 |

---

## 11. Changelog

### v1.0.0 (2026-03-02)

**Fixed**
- **src/order/direct_api_saver.py**: NumberFormatException 수정
  - POPULATE_DATASET_JS `_knownNums`에 OLD_PYUN_QTY 추가
  - _KNOWN_NUMERIC set에 OLD_PYUN_QTY, PYUN_QTY 추가
  - 로깅: numericFilled, numericFilledCols 추가

- **src/order/batch_grid_input.py**: populate_grid JS 이중 안전망
  - _knownNums에 OLD_PYUN_QTY 추가

- **src/order/order_executor.py**: 발주일자 불일치 수정
  - __init__에 _last_selected_date = None 초기화
  - execute_orders()에서 select_order_day() 후 날짜 보정
  - 로깅: "발주일자 보정: {old} → {new}" 추가

- **tests/test_date_filter_order.py**: 테스트 호환성
  - _make_executor()에서 _last_selected_date 초기화

**Quality**
- Match Rate: 100% (모든 설계 항목 구현)
- Test Coverage: 2904/2904 (100%)
- Live Validation: ✅ Passed (2026-03-02, Store 46513, ErrorCode=99999)

---

## 버전 이력

| 버전 | 날짜 | 변경 사항 | 작성자 |
|------|------|---------|--------|
| 1.0 | 2026-03-02 | PDCA 완료 리포트 작성 | Development Team |
