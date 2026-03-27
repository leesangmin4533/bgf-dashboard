# BGF 점포시스템 사이트 아키텍처 레퍼런스

> **탐구일**: 2026-03-08
> **대상**: https://store.bgfretail.com (점포관리 시스템)
> **플랫폼**: 넥사크로17 (Nexacro Platform 17)
> **점포**: (46513) 이천호반베르디움점

---

## 1. 넥사크로 앱 구조

### 1.1 앱 기본 정보

```
Application: App_Desktop
├── MainFrame: mainframe
│   └── HFrameSet00
│       ├── LoginFrame (ChildFrame) — 로그인 폼
│       ├── LeftFrame (ChildFrame) — 좌측 메뉴 패널
│       └── VFrameSet00
│           ├── TopFrame (ChildFrame) — 상단바 (메뉴/날씨/알림)
│           └── FrameSet
│               └── WorkFrame (ChildFrame) — 메인 콘텐츠 영역
│                   └── {MENU_ID} (동적 ChildFrame) — 각 화면
```

### 1.2 화면 내부 중첩 구조 (공통 패턴)

```
{MENU_ID}.form
├── sta_bg (Static) — 배경
├── div_cmmbtn (Div) — 공통 버튼 바 (조회/저장/종료)
├── btn_leftmenu (Button) — 좌측 메뉴 토글
└── div_workForm (Div) — 실제 작업 영역
    └── div_work_01 (Div) — [일부 화면만] 추가 중첩
        └── form
            ├── Dataset들 (objects)
            └── Grid/Div/Button 등 (components)
```

**주의**: 데이터셋 위치가 화면마다 다름
- STBJ030: `div_workForm.form.div_work_01.form` (2단 중첩)
- STMB010: `div_workForm.form` (1단 중첩)
- 탐색 시 재귀적 scan 필요

### 1.3 JavaScript 접근 경로

```javascript
// 앱 객체
const app = nexacro.getApplication();

// TopFrame (전역 함수/데이터)
const topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;

// 특정 화면 접근
const fs = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
const screen = fs.STBJ030_M0; // 메뉴 ID로 접근

// 메뉴 열기/닫기
topForm.gfn_openMenuId('STBJ030_M0');
topForm.gfn_formClose('STBJ030_M0');
```

---

## 2. 전역 데이터셋 (Application 레벨)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|----------|------|------|------|
| **GDS_MENU** | 270 | MENU_ID, MENU_NM, URL, PRG_NM, UPPER_MENU_ID, FOLDER_YN, FUNC1~12, FUN1_TXT~12 | 전체 메뉴 트리 |
| **GDS_JUMPO** | 1 | STORE_CD, STORE_NM, SLC_CD, LOC_CD, STORE_OWNER_NM 등 41컬럼 | 점포 정보 |
| **GDS_COMMON** | 1 | CUR_YEAR, CUR_DATE, CUR_TIME 등 | 시스템 공통 변수 |
| **GDS_MSG** | 317 | CODE, MSG_CONT, MSG_TY, MSG_BTTN_TY | 시스템 메시지 |
| **GDS_JJMENU** | 15 | (메뉴 구조) | 점주관리 메뉴 |
| **GDS_MYMENU** | ? | MENU_ID, MENU_NM, URL | 나의 메뉴 (즐겨찾기) |
| **GDS_OPENMENU** | 0 | MENU_ID, MENU_NM, URL | 열린 메뉴 추적 |
| **GDS_ITEMINFO** | 0 | (동적) | 상품 정보 임시 |
| **GDS_CMMCODE** | 0 | (동적) | 공통코드 |

### 2.1 GDS_JUMPO 주요 컬럼

| 컬럼 | 값 예시 | 설명 |
|------|---------|------|
| STORE_CD | 46513 | 점포 코드 |
| STORE_NM | 이천호반베르디움점 | 점포명 |
| SLC_CD | (코드) | SLC 코드 |
| LOC_CD | (코드) | 지역 코드 |
| ADM_SECT_CD | (코드) | 행정구역 코드 (날씨 조회용) |
| STORE_POS_QTY | (수량) | POS 수량 |
| SV_EMP_NO | (번호) | 담당 SV 사번 |
| FC_CD | (코드) | FC 코드 |
| OPEN_YMD | (날짜) | 개점일 |
| USER_GRP_ID | 0001 | 사용자 그룹 |
| MULTI_STORE_TYPE | (타입) | 복수점 타입 |
| DELI_PICK_STORE_YN | (Y/N) | 배달/픽업 가능 여부 |

---

## 3. TopFrame 데이터셋

| 데이터셋 | 행수 | 주요 컬럼 | 설명 |
|----------|------|----------|------|
| **ds_weatherTomorrow** | 0~1 | WEATHER_YMD, WEATHER_CD_NM, VIEW_WEATHER_CD_NM, HIGHEST_TMPT, LOWEST_TMPT, AVG_TMPT, RAIN_RATE, RAIN_QTY, RAIN_TY_NM | 내일 날씨 예보 |
| **ds_weatherToday** | 5 | TIME_ZN_ID, VIEW_WEATHER_CD_NM, TIME_ZN_TMPT | 오늘 시간별 날씨 |
| **ds_alarmTalkList** | ? | (알림톡 목록) | 알림Talk 데이터 |
| **ds_prodRank** | ? | (상품 랭킹) | +1 상품 랭킹 |
| **ds_newProdRank** | ? | (신상품 랭킹) | 신상품 랭킹 |
| **ds_oneProdRank** | ? | (원플원 랭킹) | 1+1 상품 랭킹 |
| **ds_newsEvent** | ? | (뉴스/이벤트) | 주간안내/신상 등 |
| **ds_topMenu** | 11 | (GDS_MENU와 동일 구조) | 상위 메뉴 |
| **ds_smsInfo** | ? | (SMS 정보) | SMS 알림 |
| **Dataset00** | 4 | notice | 공지사항 텍스트 |
| **Dataset01** | 3 | weather | 날씨 표시용 |
| **ds_output** | 0 | TS_ID, SEQ, TOTAL_TIME, TRAN_TIME, CHECK_TIME | 성능 측정 |

---

## 4. 메뉴 트리 (270개 메뉴, 216개 페이지)

### 4.1 최상위 메뉴 (11개)

| 메뉴 ID | 메뉴명 | 하위 페이지 수 |
|---------|--------|--------------|
| STBJ000_M0 | 발주 | 37 |
| STJS000_M0 | 정산 | 13 |
| STGJ000_M0 | 검수전표 | 12 |
| STMB000_M0 | 매출분석 | 28 |
| STJK000_M0 | 재고 | 12 |
| STCM000_M0 | 커뮤니케이션 | 23 |
| STJJ000_M0 | 점주관리 | 38 |
| STMS000_M0 | 마스터 | 16 |
| STAP00001 | 신청업무 | 22 |
| STON001_M0 | 온라인(APP) | 11 |
| STSE001_M0 | 나의 온라인점포 | 4 |

### 4.2 발주 메뉴 상세 (37개)

| 하위 그룹 | 메뉴 ID | 메뉴명 | 비고 |
|-----------|---------|--------|------|
| **오늘의 메뉴** | STCM120_M0_@ | 신상품 사전 안내 | |
| **기능별 발주** | STBJ400_M0 | 통합 발주 | |
| | STBJ430_M0 | 진열집기순 발주 | |
| | STBJ120_M0 | 본부 월 행사 발주 | |
| | STBJ150_M0 | 장려금/광고비 대상 발주 | |
| | STBJ160_M0 | 원가 DC상품 발주 | |
| | STBJ310_M0 | 판매실적 발주 | |
| | STBJ010_M0 | 냉장배송 발주 | |
| | STBJ021_M0 | 신상품 발주 | |
| | STBJ030_M0 | 단품별 발주 | ★ 주 사용 |
| | STBJ170_M0 | 원/매가 인상상품 발주 | |
| | STBJ040_M0 | 예약발주 | |
| | STBJ240_M0 | 예약발주(추가발주) | |
| | STCM040_M0 | 무료택배 | |
| | STBJ180_M0 | 발주정지 대체 발주 | |
| | STBJ230_M0 | 미도입매출상위/동네추천 발주 | |
| | STBJ450_M0 | 상품채우기발주 | |
| | STBJ470_M0 | 미도입 상생지원(신상품) 발주 | |
| **시간별 발주** | STBJ200_M0 | 5분 발주 | |
| | STBJ210_M0 | 30분 발주 | |
| | STBJ220_M0 | 1시간이상 발주 | |
| | STBJ190_M0 | 대체일 발주 | |
| **발주정보** | STBJ070_M0 | 발주 현황 조회 | ★ 수집 중 |
| | STBJ071_M0 | 행사상품 발주조회 | |
| | STBJ080_M0 | 상품별 발주 카렌더 | |
| | STBJ330_M0 | 발주정지상품조회 | |
| | STBJ460_M0 | 상생지원(신상품) 발주조회 | |
| **발주가이드** | STBJ490_M0 | 품절상품현황 | |
| | STBJ500_M0 | 신상품 랭킹 | |
| | STBJ510_M0 | +1상품 랭킹 | |
| | STBJ520_M0 | 원가DC 랭킹 | |
| | STBJ530_M0 | 원가DC 종료 상품 발주 | |
| **스마트발주** | STBJ540_M0 | 스마트 발주 | |
| | STBJ550_M0 | 일괄 스마트 발주 | |
| | STBJ560_M0 | 스마트발주 상품관리 | |

### 4.3 매출분석 메뉴 (28개)

| 메뉴 ID | 메뉴명 |
|---------|--------|
| STMB010_M0 | 시간대별 매출 정보 |
| STMB350_M0 | 매출조회차트 |
| STMB011_M0 | 중분류별 매출 구성비 |
| STMB251_M0 | 단골고객 주요정보 |
| STMB300_M0 | 예약상품 매출분석 |
| STMB310_M0 | 배달/픽업 일자별 매출분석 |
| STMB320_M0 | CU키핑쿠폰현황 |
| STMB330_M0 | 상품별 매출 분석 |
| STMB340_M0 | 요일별 상품 매출분석 |
| STMB360_M0 | 알뜰폰 매출분석 |
| ... | (나머지 18개) |

### 4.4 재고 메뉴 (12개)

| 메뉴 ID | 메뉴명 |
|---------|--------|
| STJK010_M0 | 재고현황 |
| STJK030_M0 | 상품별재고현황 |
| STJK051_M0 | 유통기한관리 |
| STJK060_M0 | 진열맵 관리 |
| STJK070_M0 | 재고이동 |
| ... | (나머지) |

---

## 5. API 엔드포인트 패턴

### 5.1 URL 규칙

```
https://store.bgfretail.com/{menuIdLower}/{serviceName}
```

- `menuIdLower`: 메뉴 ID의 소문자 변환 (STBJ030_M0 → stbj030)
- `serviceName`: 조회=selSearch/selDay 등, 저장=saveOrd 등

### 5.2 캡처된 API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| **발주 관련** | | |
| `/stbj030/selSearch` | POST | 단품별 발주 상품 조회 |
| `/stbjz00/selItemDetailSearch` | POST | 상품 상세 팝업 |
| `/stbjz00/selItemDetailOrd` | POST | 상품 발주 이력 |
| `/stbjz00/selItemDetailSale` | POST | 상품 90일 판매 이력 |
| `/stbjz00/saveOrd` | POST | 발주 저장 |
| `/stbj070/selListData` | POST | 발주 현황 조회 |
| `/stbj150/selMainSearch` | POST | 장려금/광고비 대상 |
| `/stbj490/selMainSearch` | POST | 품절상품 현황 |
| `/stbj500/mainSelSearch` | POST | 신상품 랭킹 |
| `/stbj510/mainSelSearch` | POST | +1상품 랭킹 |
| **매출분석 관련** | | |
| `/stmb010/selDay` | POST | 시간대별 매출 |
| `/stmb010/selPrdT3` | POST | 시간대별 상품 상세 |
| **정산 관련** | | |
| `/stjs010/selSearch` | POST | 정산 조회 |
| **검수 관련** | | |
| `/stgj010/selSearch` | POST | 센터 매입 조회 |
| `/stgj020/selSearch` | POST | 통합 전표 조회 |
| **재고 관련** | | |
| `/stjk010/selSearch` | POST | 재고 현황 |
| `/stjk030/selSearch` | POST | 상품별 재고 |
| **공통** | | |
| `/main/selMainNotice` | POST | 메인 공지사항 |
| `/main/getMainTranction` | POST | 메인 트랜잭션 |
| `/main/getCommBoxDate` | POST | 공통 날짜 박스 |
| `/stco010/selComCd` | POST | 공통코드 조회 |
| `/stjj190/selOptionList` | POST | 옵션 목록 |
| `/stjj620/search` | POST | 점주관리 검색 |
| `/search/selNoticeTotalSearch` | POST | 통합 검색 |
| `/searchEngine/selBestSearch` | POST | 베스트 검색 |

### 5.3 gfn_transaction 함수 시그니처

```javascript
gfn_transaction(strSvcId, strSvcUrl, inData, outData, strArg, callBackFnc, isAsync, nTimeout)
```

| 파라미터 | 설명 | 예시 |
|---------|------|------|
| strSvcId | 서비스 식별자 | 'selSearch', 'save' |
| strSvcUrl | 엔드포인트 경로 | 'stbj030/selSearch' |
| inData | 입력 데이터셋 매핑 | 'dsCond=dsCond' |
| outData | 출력 데이터셋 매핑 | 'dsGeneralGrid=dsGeneralGrid dsItem=dsItem' |
| strArg | 추가 인자 | '' |
| callBackFnc | 콜백 함수명 | 'fn_callback' |
| isAsync | 비동기 여부 | true |
| nTimeout | 타임아웃(ms) | 30000 |

---

## 6. 전역 함수 목록 (gfn_*)

### 6.1 서비스 관련

| 함수명 | 파라미터 | 설명 |
|--------|---------|------|
| gfn_transaction | (strSvcId, strSvcUrl, inData, outData, strArg, callBackFnc, isAsync, nTimeout) | API 호출 핵심 |
| gfn_callback | (svcId, errCode, errMsg) | 서비스 콜백 |
| gfn_setCommonCode | (param) | 공통코드 설정 |
| gfn_setComboHead | (param) | 콤보 헤더 설정 |

### 6.2 메뉴/네비게이션

| 함수명 | 파라미터 | 설명 |
|--------|---------|------|
| gfn_openMenuId | (psMenuId, paObjList) | 메뉴 ID로 화면 열기 |
| gfn_OpenMenu | (psMenuId, psMenuNm, psMenuPage, psMenuPath, paObjList) | 상세 메뉴 열기 |
| gfn_OpenMenuRow | (pnRow, paObjList) | 메뉴 행 인덱스로 열기 |
| gfn_goPage | (psMenuId, paObjList) | 페이지 이동 |
| gfn_formClose | (sFrameID) | 화면 닫기 |
| gfn_formBeforeClose | (sFrameID) | 닫기 전 처리 |
| gfn_getFormObj | (psMenuId) | 화면 form 객체 반환 |
| gfn_callFormFuction | (psMenuId, psFucntionName) | 화면 함수 호출 |

### 6.3 다이얼로그/팝업

| 함수명 | 파라미터 | 설명 |
|--------|---------|------|
| gfn_alert | (obj, msg) | 알림 다이얼로그 |
| gfn_alertD | (obj, msg) | 디버그 알림 |
| gfn_confirm | (obj, msg) | 확인 다이얼로그 |
| gfn_confirmD | (obj, msg) | 디버그 확인 |
| gfn_popupOpen | (obj, args) | 팝업 열기 |
| gfn_popupClose | (obj, args) | 팝업 닫기 |
| gfn_popupCallback | (id, val) | 팝업 콜백 |

### 6.4 유틸리티

| 함수명 | 파라미터 | 설명 |
|--------|---------|------|
| gfn_addDate | (date, days) | 날짜 가산 |
| gfn_addMonth | (date, months) | 월 가산 |
| gfn_dateTime | () | 현재 일시 |
| gfn_dateToStr | (date) | 날짜→문자열 |
| gfn_strToDate | (str) | 문자열→날짜 |
| gfn_allTrim | (str) | 양쪽 트림 |
| gfn_appendComma | (num) | 숫자 콤마 표시 |
| gfn_decode | (args...) | 디코드 함수 |
| gfn_isNull | (val) | null 체크 |
| gfn_length | (str) | 문자열 길이 |
| gfn_getByteLength | (str) | 바이트 길이 |
| gfn_lpad | (str, len, pad) | 좌측 패딩 |
| gfn_dsIsUpdated | (ds) | 데이터셋 변경 여부 |

### 6.5 엑셀/파일

| 함수명 | 파라미터 | 설명 |
|--------|---------|------|
| gfn_excelExport | (args) | 엑셀 내보내기 |
| gfn_excelExport2 | (args) | 엑셀 내보내기 v2 |
| gfn_excelExportSheet | (args) | 시트별 내보내기 |
| gfn_excelImport | (args) | 엑셀 가져오기 |
| gfn_fileDownload | (args) | 파일 다운로드 |
| gfn_fileUpload | (args) | 파일 업로드 |

### 6.6 그리드/UI

| 함수명 | 파라미터 | 설명 |
|--------|---------|------|
| gfn_ckGrdObj | (grid) | 그리드 객체 확인 |
| gfn_gridOnHeadClick | (obj, e) | 그리드 헤더 클릭 정렬 |
| gfn_clearSortMark | (grid) | 정렬 마크 초기화 |
| gfn_findGridText | (grid, text) | 그리드 텍스트 검색 |
| gfn_createPopupMenu | (obj, args) | 팝업 메뉴 생성 |
| gfn_createTooltip | (obj, args) | 툴팁 생성 |
| gfn_chkboxChrome | (obj, args) | 크롬 체크박스 처리 |
| gfn_btnEnableController | (obj, args) | 버튼 활성화 제어 |

---

## 7. 화면별 데이터셋 상세

### 7.1 STBJ030_M0 (단품별 발주) — ★ 주 사용

| 데이터셋 | 행수 | 컬럼수 | 주요 컬럼 | 설명 |
|---------|------|--------|----------|------|
| dsGeneralGrid | 0~N | 55 | STORE_CD, ORD_YMD, ITEM_CD, ITEM_NM, PYUN_QTY, ORD_UNIT_QTY, NOW_QTY, PROFIT_RATE, HQ_MAEGA_SET 등 | 메인 발주 그리드 |
| dsItem | 0~1 | 55 | (dsGeneralGrid와 동일 구조) | 검색된 상품 상세 |
| dsOrderSale | 0~N | 8 | SALE_DATE, ORD_QTY, IN_QTY, SALE_QTY, DISUSE_QTY 등 | 90일 판매 이력 |
| dsOrderSaleBind | 0~4 | 92 | DAY1~DAY91, AVG | 판매 이력 피벗 (발주/입고/판매/폐기 × 91일) |
| dsSaveChk | 0~1 | 6 | SAVE_CHK, ERR_MSG 등 | 저장 결과 |
| dsWeek | 0~1 | 1 | (발주일) | 발주 요일 |
| dsCheckMagam | 0~8 | 4 | ORD_YMD, S_ORD_TURN_HMS, E_ORD_TURN_HMS, SAVE_TIME_YN | 발주 마감 시간 |

### 7.2 STMB010_M0 (시간대별 매출)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | 20 | HMS, AMT, CNT, CNT_DANGA, RATE, PAGE_CNT | 시간대별 매출 (00~23시) |
| dsListSum | 1 | NSALE, RATE, AMT, CNT, CNT_DANGA, AVG_AMT, AVG_CNT, AVG_CNT_DANGA | 합계/평균 |
| dsListTemp | 24 | HMS, AMT, CNT, CNT_DANGA, RATE, AMT_SUM, CNT_SUM, PAGE_CNT | 차트용 전체 시간 |
| dsListChart | 24 | (dsListTemp와 동일) | 차트 데이터 |
| dsListMain | ? | HMS, AMTG, CNTG, CNT_DANGAG, AMTB, CNTB, CNT_DANGAB, AMT_RATE, CNT_RATE | **전년 비교** 데이터 |
| dsSaleRank | ? | RANK_NO, ITEM_NM, SALE_QTY, SALE_AMT, PROFIT_RATE | **매출 순위** |
| dsListSaleRank | ? | (매출순위 표시용) | |
| dsCond | 1 | SO_YMD, strGubn | 조회 조건 |

### 7.3 STMB011_M0 (중분류별 매출 구성비)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | MID_CD, MID_NM, CUR_AMT, CUR_CNT, PRE_AMT, PRE_CNT, CUR_RATE, PRE_RATE | **중분류별** 매출 비교 |
| dsListDetail | N | ITEM_CD, ITEM_NM, SALE_QTY, SALE_AMT | 상품 상세 |

### 7.4 STMB330_M0 (상품별 매출 분석)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM, MID_NM, SALE_QTY, SALE_AMT, PROFIT, PROFIT_RATE, RANK 등 | 상품별 매출 상세 |

### 7.5 STMB340_M0 (요일별 상품 매출분석)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM, MON_QTY~SUN_QTY, MON_AMT~SUN_AMT | **요일별** 상품 판매량 |

### 7.6 STMB251_M0 (단골고객 주요정보)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | MEM_TYPE, MEM_CNT, VISIT_CNT, AVG_AMT, TOT_AMT | 고객 유형별 분석 |

### 7.7 STMB310_M0 (배달/픽업 매출분석)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | YMD, DELI_AMT, DELI_CNT, PICK_AMT, PICK_CNT | 일자별 배달/픽업 매출 |

### 7.8 STJK010_M0 (재고현황)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| ds_ListT1 | 20 | LARGE_CD, LARGE_NM, STOCK_AMT, PAGE_CNT | **대분류별** 재고 금액 |
| ds_ListT2 | 0 | (탭 전환 시) | 중분류별 재고 |
| ds_ListT3 | 0 | (탭 전환 시) | 상품별 재고 |

### 7.9 STJK030_M0 (상품별 재고현황)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| ds_ListT1 | N | ITEM_CD, ITEM_NM, STOCK_QTY, STOCK_AMT, ORD_QTY, IN_QTY, SALE_QTY, DISUSE_QTY | 상품별 재고 상세 |

### 7.10 STGJ010_M0 (센터 매입 조회)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | IN_YMD, CHIT_NO, ITEM_CD, ITEM_NM, ORD_QTY, IN_QTY, WONGA, MAEGA | 매입 상세 |

### 7.11 STGJ020_M0 (통합 전표 조회)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | CHIT_YMD, CHIT_NO, CHIT_FLAG, CHIT_ID, CHIT_ID_NM, ITEM_CNT, CENTER_CD, WONGA_AMT, MAEGA_AMT | 전표 헤더 |
| dsChitDiv | N | CODE, NAME | 전표 구분 (폐기="10") |

### 7.12 STBJ070_M0 (발주 현황 조회)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsResult | N | ORD_YMD, ITEM_CD, ITEM_NM, ORD_QTY, ORD_UNIT_QTY, PYUN_QTY, ORD_AMT, IN_QTY, MID_NM 등 | 발주 내역 |

### 7.13 STBJ080_M0 (상품별 발주 카렌더)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM + DAY1~DAY31 | 월간 발주 카렌더 (일별 발주량) |

### 7.14 STBJ490_M0 (품절상품현황)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM, MID_NM, STOCK_QTY, LAST_SALE_YMD, AVG_SALE_QTY | 품절 상품 목록 |

### 7.15 STBJ500_M0 (신상품 랭킹)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | RANK_NO, ITEM_CD, ITEM_NM, SALE_QTY, SALE_AMT, ORD_YN | 신상품 판매 랭킹 |

### 7.16 STBJ150_M0 (장려금/광고비 대상)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM, JANG_AMT, JANG_COND, JANG_PERIOD, ORD_QTY | 장려금 대상 상품 |

### 7.17 STBJ330_M0 (발주정지 상품조회)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM, CUT_START_YMD, CUT_END_YMD, CUT_REASON | 발주정지 상품 |

### 7.18 STJS010_M0 (정산)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList1 | 7 | POS_ACT_YMD, TOTAL_REMIT_AMT, SEND_AMT, DIFF_AMT, REMIT_AMT, BUY_VAT | 일별 정산 |
| dsList2 | 12 | ACT_LIST_NM, SALE_AMT, VAT | 정산 항목별 상세 |

### 7.19 STMS010_M0 (상품 마스터)

| 데이터셋 | 행수 | 컬럼 | 설명 |
|---------|------|------|------|
| dsList | N | ITEM_CD, ITEM_NM, LARGE_CD, MID_CD, SMALL_CD, MAEGA, WONGA, ORD_UNIT_QTY 등 | 상품 마스터 |

---

## 8. 서비스 Prefix 매핑

xfdl URL에서 사용하는 prefix → 실제 폴더 매핑:

| Prefix | URL | 대상 |
|--------|-----|------|
| BJ | ./BJ/ | 발주 화면 |
| CM | ./CM/ | 커뮤니케이션 |
| CO | ./CO/ | 공통 |
| GJ | ./GJ/ | 검수전표 |
| JJ | ./JJ/ | 점주관리 |
| JK | ./JK/ | 재고 |
| JS | ./JS/ | 정산 |
| JT | ./JT/ | (점포 관련) |
| MB | ./MB/ | 매출분석 |
| MS | ./MS/ | 마스터 |
| SA | ./SA/ | (기타) |
| AP | ./AP/ | 신청업무 |
| ON | ./ON/ | 온라인 |
| SE | ./SE/ | 온라인점포 |
| svc | ./svc/ | 서비스 |
| lib | ./lib/ | 라이브러리 |

---

## 9. 홈 화면 위젯 (ds_widget)

| 위젯명 | 실행 방식 | URL/메뉴 |
|--------|----------|---------|
| 복수점 설정 | 팝업 | CO::STZZ130_P0 |
| 회계시스템 | 외부 링크 | http://etax.bgfretail.com:8080/uifc |
| 시스템운영 꿀 TIP | 메뉴 | STCM220_M0 |
| 배송차량 위치조회 | 팝업 | CO::STZZ150_P0 |
| PDA찾기 | 팝업 | CO::STZZ140_P0 |
| CU 신상 온에어 | 외부 링크 | (URL) |
| CU백과사전 | 외부 링크 | (URL) |
| 설문조사 | 메뉴 | STCM230_M0 |

---

## 10. 주요 발견사항

### 10.1 발주 시간 제한에 대한 참고사항

> **중요**: 발주 시간대(10:00~11:00)가 아닌 시간에도 서버가 데이터를 차단하는 것은 아님.
> dsCheckMagam의 S_ORD_TURN_HMS / E_ORD_TURN_HMS가 발주 가능 시간을 정의하며,
> SAVE_TIME_YN이 저장 허용 여부를 제어함.
> 조회(selSearch) 자체는 시간에 관계없이 동작 가능 — 올바른 접근 방식 사용 필요.

### 10.2 넥사크로 데이터셋 접근 시 주의사항

1. **fn_search() 비동기**: JavaScript에서 `fn_search()` 직접 호출 시 gfn_transaction이 비동기이므로 데이터 로드 전에 반환됨 → 콜백 또는 polling 필요
2. **중첩 구조 다양**: 화면마다 div 중첩 깊이가 다름 (1단 vs 2단)
3. **BigDecimal 타입**: 넥사크로의 BIGDECIMAL은 `{hi: N, lo: 0}` 객체로 반환됨 → hi 값 사용

---

## Version History

| 버전 | 날짜 | 변경 | 작성자 |
|------|------|------|--------|
| 1.0 | 2026-03-08 | 초기 작성 (Chrome 확장 라이브 탐구) | Claude Code |
