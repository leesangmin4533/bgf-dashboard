# BGF 리테일 API 엔드포인트 분석 (2026-02-27)

## 캡처 방법

XHR 인터셉터(`XMLHttpRequest.prototype.open/send` 오버라이드)를 설치한 뒤
각 화면에 진입하여 **자동 호출되는 API**를 캡처.

- 스크립트: `scripts/explore_priority_screens.py`
- 결과 파일: `data/bgf_priority_screens.json`

---

## 캡처 완료: Tier 1 화면 (5개)

### 1. STJK010_M0 — 현재고 조회

| 항목 | 값 |
|------|---|
| **엔드포인트** | `POST /stjk010/selLarge` |
| **body 크기** | 639자 (SSV) |
| **response 크기** | 598자 (SSV) |
| **response Dataset** | `ds_ListT1` |
| **response 컬럼** | `LARGE_CD:string(2)`, `LARGE_NM:string(30)`, `STO...` (truncated) |

**분석**: 화면 진입 시 **대분류 목록만** 자동 로드됨.
실제 상품별 재고 데이터는 대분류 선택 후 **추가 API 호출** 필요 → `/stjk010/selSearch` 추정.

**Direct API 활용**:
- 실시간 재고 수량 조회 가능 여부는 추가 캡처 필요
- 기존 `realtime_inventory` 테이블과 비교하여 정합성 확인 가능

---

### 2. STJK030_M0 — 일자별 재고추이

| 항목 | 값 |
|------|---|
| **엔드포인트** | `POST /stjkz10/selLarge` ⚠️ stjk030이 아닌 **stjkz10** |
| **body 크기** | 598자 (SSV) |
| **response 크기** | 486자 (SSV) |
| **response Dataset** | `dsList` |
| **response 컬럼** | `LARGE_CD:string(2)`, `LARGE_NM:string(30)`, `PAGE_C...` (truncated) |

**분석**: STJK010과 유사하게 대분류 목록만 자동 로드.
⚠️ URL 패턴 주의: 화면 ID(`STJK030`)와 API 경로(`/stjkz10/`)가 다름.

**Direct API 활용**:
- 날짜 범위 지정 시 일별 재고 추이 데이터 제공 가능
- inventory_batches 대비 BGF 공식 재고 데이터로 교차 검증 용도

---

### 3. STGJ300_M0 — 입고예정 내역 조회

| 항목 | 값 |
|------|---|
| **엔드포인트 1** | `POST /stgj300/searchAisPlanYmdList` |
| **body 크기** | 578자 (SSV) |
| **response 크기** | 199자 (SSV) |
| **response Dataset** | `dsAcpYmd` |
| **response 컬럼** | `STORE_CD:string(5)`, `ORD_YMD:string(8)`, `VIEW_Y...` (truncated) |
| **엔드포인트 2** | `POST /stgj300/searchChitListPopup` |
| **body 크기** | 585자 (SSV) |
| **response 크기** | 610자 (SSV) |
| **response Dataset** | `dsListPopup` |
| **response 컬럼** | `NAP_PLAN_YMD:string(8)`, `DGFW_YMD:string(8)`, ... (truncated) |

**분석**: 화면 진입 시 **2개 API 자동 호출** — 날짜 목록 + 전표 목록.
- `dsAcpYmd`: 입고 예정 날짜 목록 (발주일 → 입고예정일)
- `dsListPopup`: 전표별 입고 예정 상세 (납입예정일 NAP_PLAN_YMD, 도착예정일 DGFW_YMD)

**Direct API 활용** ⭐ 높음:
- 발주 후 입고 예정 내역을 직접 조회 → 리드타임 분석 자동화
- `receiving-delay-analysis` 기능과 직접 연동 가능
- 기존 `ReceivingCollector`(STGJ010)와 상호보완: STGJ010=확정입고, STGJ300=예정입고

---

### 4. STMB010_M0 — 시간대별 매출 정보

| 항목 | 값 |
|------|---|
| **엔드포인트** | `POST /stmb010/selDay` |
| **body 크기** | 1116자 (SSV) |
| **response 크기** | 1765자 (SSV) |
| **response Dataset** | `dsList` |
| **response 컬럼** | `HMS:string(2)`, `AMT:bigdecimal(0)`, `CNT:bigdecima...` (truncated) |

**분석**: 화면 진입 시 **당일 시간대별 매출** 자동 로드.
- `HMS`: 시간대 (00~23)
- `AMT`: 매출액
- `CNT`: 판매 건수
- body가 1116자로 큰 편 → 다양한 파라미터 포함

**Direct API 활용** ⭐ 최고:
- 시간대별 판매 패턴 분석에 직접 활용
- 기존 `STMB011` (중분류 매출 구성비)와 다른 API — 시간축 vs 카테고리축
- 푸드 배송차수 수요 비율(`DELIVERY_TIME_DEMAND_RATIO`)의 실제 데이터 검증

---

### 5. STCM130_M0 — 상품 유통기한 관리

| 항목 | 값 |
|------|---|
| **엔드포인트** | `POST /stcm130/search` |
| **body 크기** | 721자 (SSV) |
| **response 크기** | 2027자 (SSV) |
| **response Dataset** | `dsList` |
| **response 컬럼** | `LARGE_NM:string(30)`, `MID_NM:string(30)`, `EXPIRE_...` (truncated) |

**분석**: 화면 진입 시 **유통기한 관리 대상 상품 목록** 자동 로드.
- `LARGE_NM`, `MID_NM`: 대/중분류명
- `EXPIRE_...`: 유통기한 관련 필드 (EXPIRE_DAY, EXPIRE_DATE 추정)
- response 2027자로 상당한 데이터량

**Direct API 활용** ⭐ 높음:
- `product_details.expiration_days` 대비 BGF 공식 유통기한 교차 검증
- 유통기한 TTL 시스템의 `FOOD_EXPIRY_FALLBACK` 보정값 자동화
- `FoodWasteRateCalibrator`의 정확도 향상

---

## 캡처 실패: Tier 2-3 화면 (7개)

페이지 리프레시 후 **로그인 세션 불일치**로 네비게이션 실패.

| 화면 | 추정 엔드포인트 | 상태 |
|------|--------------|------|
| STBJ330_M0 (발주정지) | `/stbj330/selSearch` | ❌ 미캡처 |
| STBJ490_M0 (품절현황) | `/stbj490/selSearch` | ❌ 미캡처 |
| STBJ080_M0 (발주카렌더) | `/stbj080/selSearch` | ❌ 미캡처 |
| STBJ030_M0 (단품발주) ★기존 | `/stbj030/selSearch` | ✅ 이미 구현 |
| STMB011_M0 (중분류매출) ★기존 | 미확인 | 📋 계획 중 (plan 존재) |
| STGJ010_M0 (센터매입) ★기존 | 미확인 | ✅ Selenium 구현 |
| STBJ070_M0 (발주현황) ★기존 | 미확인 | ✅ Selenium 구현 |

**재캡처 방안**: 페이지 리프레시 대신 **탭 전체 닫기 + 재로그인** 후 이어서 탐색.

---

## 공통 SSV Body 구조

캡처된 모든 API의 body는 동일한 SSV 헤더를 공유:

```
SSV:utf-8
GV_USERFLAG=HOME
_xm_webid_1_={세션ID}
_xm_tid_1_={트랜잭션ID}
SS_STORE_CD={매장코드}       ← 고정값 (예: 46513)
SS_PRE_STORE_CD={이전매장코드}
SS_STORE_NM={매장명}         ← Base64 인코딩
SS_SLC_CD=
SS_LOC_CD=02
SS_A...                      ← 이후 파라미터는 화면별 상이
```

### 치환 필요 파라미터

| 파라미터 | 설명 | 치환 필요 |
|----------|------|----------|
| `_xm_tid_1_` | 트랜잭션 ID | 매 요청마다 변경 |
| `SS_STORE_CD` | 매장 코드 | 매장 전환 시 |
| 날짜 파라미터 | 화면별 상이 | 조회 조건에 따라 |
| 상품/카테고리 코드 | 화면별 상이 | 조회 대상에 따라 |

---

## 공통 API (모든 화면 진입 시)

| API | 설명 | 호출 시점 |
|-----|------|----------|
| `/log/saveAccLog` | 접근 로그 저장 | 매 화면 진입 |
| `/search/selNoticeTotalSearch` | 통합 공지사항 검색 | 매 화면 진입 |
| `/stbjz00/selCheckStore` | 매장 상태 확인 | 발주 화면 진입 |
| `/stbjz00/selOrdDayList` | 발주 가능일 목록 | 발주 화면 진입 |
| `/stbjz00/selBaljuTime` | 발주 마감 시간 | 일부 발주 화면 |

---

## 화면 xfdl 로딩 패턴

화면 진입 시 먼저 xfdl(넥사크로 폼 정의) 파일을 로드:
```
https://store.bgfretail.com/websrc/deploy/{PREFIX}/{SCREEN_ID}.xfdl.js?nexaversion=...
```

| PREFIX | 의미 | 예시 |
|--------|------|------|
| BJ | 발주 | BJ/STBJ400_M0.xfdl.js |
| JK | 재고 | JK/STJK010_M0.xfdl.js |
| JS | 정산 | JS/STJS010_M0.xfdl.js |
| GJ | 검수전표 | GJ/STGJ010_M0.xfdl.js |
| MB | 매출분석 | MB/STMB010_M0.xfdl.js |
| CM | 커뮤니케이션 | CM/STCM130_M0.xfdl.js |
| JJ | 점주관리 | JJ/STJJ160_M0.xfdl.js |
| MS | 마스터 | MS/STMS010_M0.xfdl.js |
| AP | 신청업무 | AP/STAP010_M0.xfdl.js |
| frame | 공통 프레임 | frame/workForm.xfdl.js, frame/cmmbtnForm.xfdl.js |

---

## Direct API 전환 우선순위

기존 `direct_api_fetcher.py`(SSV 프로토콜)와 동일 패턴으로 전환 가능:

### 즉시 전환 가능 (body 구조 캡처 완료)

| # | 화면 | API | 활용 | 효과 |
|---|------|-----|------|------|
| 1 | 시간대별 매출 | `/stmb010/selDay` | 시간대 판매 패턴 | 배송차수 수요비율 검증 |
| 2 | 유통기한 관리 | `/stcm130/search` | 유통기한 교차검증 | TTL/폐기율 정확도 향상 |
| 3 | 입고예정 내역 | `/stgj300/search*` | 리드타임 분석 | 입고지연 예측 |

### 추가 캡처 필요

| # | 화면 | 추정 API | 활용 | 비고 |
|---|------|---------|------|------|
| 4 | 현재고 상세 | `/stjk010/selSearch` | 실시간 재고 검증 | selLarge만 캡처됨 |
| 5 | 재고추이 상세 | `/stjkz10/selSearch` | 일별 재고 추이 | selLarge만 캡처됨 |
| 6 | 발주정지 | `/stbj330/selSearch` | 발주제외 자동화 | 미캡처 |
| 7 | 품절현황 | `/stbj490/selSearch` | 품절 대응 | 미캡처 |
| 8 | 중분류 매출 | 미확인 | Phase 1 매출 수집 | plan 작성 완료 |

### 전환 방법 (기존 패턴 재사용)

```python
# 1단계: 인터셉터 설치 + Selenium 1회 조작 → body 템플릿 캡처
# 2단계: body에서 날짜/코드 파라미터 치환
# 3단계: fetch() Direct API 호출 (병렬 5 workers)
# 4단계: SSV 응답 parse_ssv_dataset()로 파싱
# 5단계: Selenium 폴백 유지

# 참조: docs/collectors/nexacro-direct-api-pattern.md
# 코드: src/collectors/direct_api_fetcher.py (parse_ssv_dataset, ssv_row_to_dict)
```

---

## 홈 화면 데이터셋 (보너스 발견)

WorkFrame 탐색 중 홈 화면(fsms_Main_SC.xfdl)에서 발견된 94개 데이터셋 중 활용 가능한 것:

| 데이터셋 | 내용 | 행수 | 활용 |
|----------|------|------|------|
| `dsSoldOutItemList` | 품절 상품 | SO_CNT=20 | 품절 모니터링 |
| `dsNewItemList` | 신상품 랭킹 | ITEM_CD, RANK_GB, NOW_QTY | 신상품 감지 보완 |
| `dsPlusItemList` | +1상품 랭킹 | 유사 구조 | 연관 상품 분석 |
| `dsSubsidyList` | 상생지원 금액 | TOT_AMT=841700 | 운영 비용 추적 |
| `ds_AlarmTalk` | 업무 알림 | 17건 | 중요 알림 자동 감지 |
| `dsSaleRate` | 매출 현황 | RATE 필드 | 일일 매출 모니터링 |
| `dsMainNotice` | 공지사항 | 다수 | 운영 정보 |

※ 이 데이터셋들은 로그인 후 홈 화면에서 **별도 API 호출 없이** 접근 가능 (TopFrame/WorkFrame에 사전 로드됨).

---

## 참고사항

### 7화면 제한
넥사크로 앱에서 동시에 열 수 있는 화면은 최대 **7개**.
초과 시 `alert("화면을 7개 이상 열 수 없습니다.")` 발생.
→ 자동화 스크립트에서는 5개마다 처리 필요 (페이지 리프레시는 세션 불일치 유발).

### 화면 ID ≠ API 경로
일부 화면에서 화면 ID와 API URL이 불일치:
- `STJK030_M0` (화면) → `/stjkz10/selLarge` (API)
- `STBJ030_M0` (화면) → `/stbj030/selSearch` (API) ← 일치
→ 반드시 **실제 XHR 캡처**로 확인 필요.
