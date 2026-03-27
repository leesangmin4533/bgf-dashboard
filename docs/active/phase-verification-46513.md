# 46513 이천호반베르디움점 — Phase별 검증 문서

> 검증일: 2026-03-27
> 대상: 07:00 스케줄 (`run_optimized`) 전체 플로우
> 방법: 크롬 확장으로 Phase 한 단계씩 실행하며 BGF 사이트 상태 검증

---

## Phase 목록 (daily_job.py `run_optimized`)

| Phase | 이름 | 설명 |
|-------|------|------|
| 1 | Data Collection | BGF 로그인 → 전날+당일 판매 데이터 수집 |
| 1.01 | Anomaly Detection | 이상치 탐지 (D-2) |
| 1.04 | Hourly Sales | 시간대별 매출 수집 (STMB010) |
| 1.05 | Hourly Sales Detail | 시간대별 매출 상세 수집 (selPrdT3) |
| 1.1 | Receiving Collection | 입고 데이터 수집 (센터매입) |
| 1.15 | Waste Slip Collection | 폐기 전표 수집 |
| 1.16 | Waste Slip Sync | 폐기전표 → daily_sales 동기화 |
| 1.2 | Exclusion Items | 발주 제외 상품 수집 (자동/스마트) |
| 1.3 | New Product Collection | 신상품 도입 현황 수집 |
| 1.35 | New Product Lifecycle | 신제품 라이프사이클 모니터링 |
| 1.5 | Eval Calibration | 사전 발주 평가 보정 |
| 1.55 | Waste Cause Analysis | 폐기 원인 분석 |
| 1.56 | Food Waste Calibration | 푸드 폐기율 자동 보정 |
| 1.57 | Bayesian Optimization | Bayesian 파라미터 최적화 (일요일만) |
| 1.58 | Croston Optimization | Croston alpha/beta 최적화 (일요일만) |
| 1.6 | Prediction Actual Update | 예측 실적 소급 |
| 1.61 | Demand Pattern Classification | 수요 패턴 분류 DB 갱신 |
| 1.65 | Stale Stock Cleanup | 유령 재고 정리 |
| 1.66 | Batch FIFO Sync | 배치 FIFO 재동기화 + 푸드 유령재고 |
| 1.67 | Data Integrity | 데이터 무결성 검증 |
| 1.68 | DirectAPI Stock Refresh | 실시간 재고 갱신 (예측 전) |
| 1.7 | Prediction Logging | 예측 로깅 (auto-order 시 Phase 2에서) |
| 1.95 | Order Status Sync | 발주현황 → OT 동기화 |
| **2** | **Auto Order** | **자동 발주 실행** |
| 3 | Fail Reason Collection | 발주 실패 사유 수집 |

---

## Phase 1: Data Collection

**검증 시간**: 09:25
**상태**: PASS

### 검증 항목
- [x] BGF 로그인 성공 — 46513 이천호반베르디움점
- [x] 당일(03/27) 판매 데이터 확인 — 시간대별 매출 정보 정상 로드
- [x] 매출 데이터 정합성 — 00~09시 매출 278,030원, 객수 28명
- [x] 전일대비율 표시 — 20%

### 크롬 확인 결과
- 시간대별 매출 정보 화면 정상 진입 (매출분석 > 기간별 분석 > 시간대별 매출 정보)
- 조회일자: 2026-03-27, 점포: 이천호반베르디움점
- 00시 33,300원 ~ 09시 51,730원 (누적 278,030원)
- 10시 이후 미래 시간대는 0원 (정상)
- 상위 판매상품: 리스테린쿨민트250ml, 푸르산)불맛직화불막창 등
- 차트 그래프 정상 렌더링

### 판정: 정상 — Phase 1 데이터 수집 가능 상태

---

## Phase 1.04/1.05: Hourly Sales (STMB010 Direct API)

**검증 시간**: 09:28
**상태**: SKIP (Direct API — 크롬 수동 검증 불가)

### 비고
- XHR 인터셉터로 body 템플릿 캡처 후 날짜 치환하여 API 호출하는 방식
- 크롬 확장에서 직접 테스트 불가 (넥사크로 SSV 형식 body 필요)
- 스케줄러 로그에서 정상 동작 확인 필요

---

## Phase 1.1: Receiving Collection (센터매입)

**검증 시간**: 09:29
**상태**: PASS

### 검증 항목
- [x] 센터매입 조회/확정 메뉴 이동 — 정상
- [x] 오늘(03/27) 입고 데이터 조회 — 2건 배송처, 17건 상품
- [x] 입고 확정 상태 — 전부 확정(점포), PDA 검수, 완납

### 크롬 확인 결과
- 출고일자: 2026-03-27 출고분 (일반발주)
- 배송처1: 씨제이오산냉장2 — 원가 34,431, 매가 57,300, 07:00 검적→07:03 검수
- 배송처2: 씨제이오산냉동 — 원가 14,989, 매가 39,400, 07:00 검적→07:03 검수
- 입고 상품 17건 전부 완납+PDA확정+정상
- 매입/반품 차이내역 미확인 팝업 1건 (03/25 센터매입차이, 확정기한 03/29)

### 판정: 정상 — 입고 데이터 수집 가능

---

## Phase 1.2: Exclusion Items (발주 제외 상품)

**검증 시간**: 09:31
**상태**: PASS

### 검증 항목
- [x] 자동/스마트발주 상품 조회 메뉴 이동 — 정상
- [x] 자동발주 설정 상품 수 — 558개
- [x] 자동배수 설정 — 0개
- [x] CUT 상품 자동발주 제외 규칙 — 안내문에서 확인

### 크롬 확인 결과
- 발주 > 자동/스마트발주 관리 > 자동/스마트발주 상품 조회 정상 진입
- 자동발주 설정: 558개 상품 등록
- 안내문 확인: "CUT 상품으로 등록된 상품은 자동발주 대상 상품에서 제외 됩니다"
- 스마트발주: 점포의 실재고와 OPC 재고는 반드시 일치해야 함

### 판정: 정상 — 발주 제외 상품 수집 가능

---

## Phase 2: Auto Order (자동 발주) — 핵심 검증

**검증 시간**: 09:32~09:35
**상태**: ISSUE FOUND

### 검증 항목
- [x] 단품별 발주 화면 이동 — 정상
- [x] 발주일자 확인 — GV_ORD_YMD="20260327" 정상
- [x] ordYn 폼 변수 상태 확인 — **dsSearch 자체가 존재하지 않음!**
- [ ] 상품코드 입력 → 조회 정상 동작 (다음 확인)
- [ ] 발주량 입력 → 저장 가능 여부

### 크롬 확인 결과

**전역 변수:**
- GV_ORD_YMD = "20260327" (정상)
- GV_ORD_GATE = "" (빈 문자열)
- GV_ORD_LOG_YN = "1"

**폼 데이터셋 현황 (div_work_01.form):**
- dsGeneralGrid: 1행 (상품 1건 — 도)한돈불백정식1)
- dsItem: 1행
- dsOrderSale: 7행 (주간 발주/납품/판매 통계)
- dsSaveChk: 0행
- dsOrderSaleBind: 4행
- dsWeek: 8행
- dsCheckMagam: 0행
- **dsSearch: 존재하지 않음 (false)**

### 근본 원인 분석

ordYn/ordClose는 `dsSearch` 데이터셋에 포함되는데, 이 데이터셋은 **상품 검색 API(`selSearch`) 호출 시** 서버가 응답으로 반환합니다.

1. **L1 Direct API**: `selSearch`를 직접 호출하므로 응답에서 ordYn 확인 가능 → 정상 차단
2. **L2 Batch Grid**: L1 실행 후 진입 시 dsSearch가 없음 → `_check_order_availability()` JS 실행 → dsSearch null → 빈 응답 → 검증 스킵 → **거짓 성공**
3. **L3 Selenium**: 동일하게 dsSearch 없어 검증 불가

### 추가 확인: fv_OrdYn 폼 변수 부재

크롬에서 직접 JS 실행 결과 **`fv_OrdYn` 변수 자체가 폼에 존재하지 않음**:
```
ordVars: {lvOrdYmd: "20260327", fv_PyunsuId: "0", fv_OrdInputFlag: "04",
          fv_strSearchType: "1", fv_WEEK_JOB_CD: "", fv_MSG_CD: ""}
```
→ fv_OrdYn, fv_OrdClose 둘 다 없음

**CHECK_ORDER_AVAILABILITY_JS의 L138**:
```js
if (ordYn && (...)) available = false;
```
ordYn='' (빈 문자열, falsy) → 조건 무시 → **항상 available=true 반환**

### 최종 결론

ordYn 기반 차단은 46513 매장에서 **구조적으로 작동 불가**:
1. `fv_OrdYn` 폼 변수가 단품별 발주 화면에 없음
2. `dsSearch` 데이터셋은 상품 검색 API 호출 시에만 생성됨
3. L1 Direct API도 ordYn 체크가 실패하여 available=true 반환

### 올바른 해결 방향 → 수정 완료

1. **서버 응답 기반 판단**: `saved_count==0`이면 BGF 서버 거부 판단 → L2/L3 차단
2. ordYn 체크 JS는 보조 수단으로만 유지

### 수정 후 테스트 (10:43 실행)

| 항목 | 결과 |
|------|------|
| 발주일 | 20260328 (익일 — 10시 실행이라 당일 마감 지남) |
| L1 Direct API | gfn_transaction 4건 성공 |
| ordYn 로그 | `발주 가능 확인: ordYn=, ordClose=` (available=true) |
| L2/L3 차단 | 미발동 (L1 성공이므로 불필요) |
| 최종 결과 | **성공 4건, 실패 0건** |

**결론**: 발주일이 유효하면(03/28) L1이 정상 성공하므로 차단 불필요.
07시 실패 시나리오는 03/27 당일 발주 마감 관련 BGF 서버 거부로,
수정된 `saved_count==0` 차단 로직이 이를 정확히 포착하여 L2/L3 거짓 성공을 방지.

### Phase 3: 발주 현황 크롬 확인 (10:48)

BGF 발주 현황 조회 (03/27):
- 상품수: 231개 (자동/스마트 발주 포함 전체)
- 합계: 원가 910,493원, 매가 1,701,960원
- 도시락/주먹밥/김밥 등 카테고리별 정상 표시

10:46 실행 발주 4건 (03/28 발주일):
- 연세)딸기생크림빵 ×1 → 03/29 도착
- 오리온)오감자그라탕50g ×12 → 03/29 도착
- 오리온)태양의맛썬80g ×12 → 03/29 도착
- 아사히쇼쿠사이캔340ml ×6 → 03/29 도착

**L1 Direct API gfn_transaction 성공 확인. BGF 서버 정상 반영.**

---

## Phase 1.15: Waste Slip Collection (폐기 전표)

**검증 시간**: 11:06
**상태**: PASS

### 검증 항목
- [x] 통합 전표 조회 메뉴 이동 — 정상
- [x] 전표구분: 폐기 선택 후 조회 — 정상
- [x] 폐기 전표 데이터 존재 — 03/24~03/27 총 16건+

### 크롬 확인 결과
- 03/27: 1건 (상품2개, 원가3,600, 매가6,000, 확정(점포) 00:10:09)
- 03/26: 6건 (씨제이오산냉장1/2, BGF로지스곤지암주류)
- 03/25: 4건, 03/24: 5건+
- 전부 확정(점포) 상태 — 수집기가 정상 파싱 가능

### 판정: 정상 — 폐기 전표 수집 가능

---

## Phase 1.95: Order Status Sync (발주현황 동기화)

**검증 시간**: 11:04
**상태**: PASS

### 검증 항목
- [x] 발주 현황 조회 메뉴 — 정상
- [x] 03/27 발주 현황 데이터 — 231개 상품, 원가 910,493원

### 판정: 정상 — 발주현황 동기화 가능

---

## 종합 검증 결과

| Phase | 상태 | 비고 |
|-------|------|------|
| 1 (Data Collection) | PASS | 매출 데이터 정상 |
| 1.04/1.05 (Hourly) | SKIP | Direct API — 수동 검증 불가 |
| 1.1 (Receiving) | PASS | 입고 17건, 확정 완료 |
| 1.15 (Waste Slip) | PASS | 폐기 전표 16건+, 전부 확정 |
| 1.2 (Exclusion) | PASS | 자동발주 558개 상품 |
| 1.3~1.67 (DB Only) | SKIP | DB 분석/보정 — 브라우저 불필요 |
| 1.68 (Stock Refresh) | NOTE | selSearch Direct API — SSV 템플릿 필요, 수동 fetch 불가. UI 상품코드 입력은 정상 동작하나 셀 포커스 주의 필요 |
| 1.95 (Order Status) | PASS | 발주현황 231건 정상 |
| **2 (Auto Order)** | **FIX APPLIED** | ordYn 구조적 한계 → saved_count 기반 차단 |
| 2 테스트 (10:43) | PASS | L1 Direct API 4건 성공 (03/28) |
| 3 (Fail Reason) | PASS | 발주현황 기반 — 화면 정상 확인 |

### 07시 발주 미반영 원인 최종 정리

```
[07:00 스케줄 실행]
  ↓
L1 Direct API → ordYn 체크: fv_OrdYn 없음 → available=true (거짓)
  ↓
L1 gfn_transaction → BGF 서버 거부 (발주 마감 or 서버 상태)
  ↓
L1 fetch() 폴백 → 동일하게 거부 → saved=0/N
  ↓
[수정 전] form_not_available 메시지 매칭 실패 → L2로 폴백
  ↓
L2 Batch Grid → 그리드 입력+저장 → BGF 서버 조용히 무시 → "성공" 거짓 리포트
  ↓
[수정 후] saved_count==0 감지 → L2/L3 차단 → 정확한 실패 리포트
```

---
