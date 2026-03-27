# CallItemDetailPopup 전체 구조 참조

> **생성일**: 2026-02-27
> **덤프 스크립트**: `scripts/dump_popup_columns.py`
> **덤프 원본**: `data/popup_column_dump.json`
> **대상 상품**: 육개장사발면(032), 3XL베이컨햄마요김치(002), 팔리아멘트아쿠아(072)

---

## 1. 데이터셋 목록

| 데이터셋 | 설명 | 컬럼 수 | 비고 |
|----------|------|:-------:|------|
| **dsItemDetail** | 상품 상세 (메인) | **98** | 핵심 데이터셋 |
| **dsItemDetailOrd** | 발주 정보 | **30** | 발주요일/리드타임/현재고 |
| dsCheckMagam | 마감 시간 | 5 | 발주 마감 시간 |
| dsOrderSale | 발주/납품/판매/폐기 이력 | 8 | grd_summ 그리드 데이터 |
| dsOrderSaleBind | 일별 이력 바인딩 | 92 | DAY1~DAY91 + AVG |
| dsSaveChk | 저장 확인 | 6 | 발주 저장 시 사용 |
| dsWeek | 주간 발주일 | 1 | ORD_YMD 목록 |

---

## 2. dsItemDetail 전체 컬럼 (98개)

### 2.1 기본 정보

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 0 | STORE_CD | STRING | 46513 | 매장코드 | - |
| 1 | **ITEM_CD** | STRING | 8801043015653 | 상품코드 | O |
| 2 | **ITEM_NM** | STRING | 농심)육개장사발면 | 상품명 | O |
| 3 | ITEM_SNM | STRING | 농심)육개장사발면 | 상품 약칭 | - |
| 6 | PLU_ID | STRING | 00 | PLU ID | - |

### 2.2 분류 정보 (핵심)

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 20 | **LARGE_CD** | STRING | 20 / 10 / 45 | **대분류 코드** | - |
| 21 | **MID_CD** | STRING | 032 / 002 / 072 | **중분류 코드** | O |
| 22 | **SMALL_CD** | STRING | 055 / 010 / 220 | **소분류 코드** | - |
| 23 | **LARGE_NM** | STRING | 가공식품 / 간편식사 / 담배 | **대분류 명칭** | - |
| 24 | **MID_NM** | STRING | 라면류 / 주먹밥 / 담배 | **중분류 명칭** | - |
| 25 | **SMALL_NM** | STRING | 용기면 / 삼각김밥 / 외산담배 | **소분류 명칭** | - |
| 26 | **CLASS_NM** | STRING | 가공식품 > 라면류 > 용기면 | **전체 분류 경로** | - |

> **UI 매핑**: `divInfo > stClass` = CLASS_NM 값 표시
> 예: "간편식사 > 주먹밥 > 삼각김밥", "담배 > 담배 > 외산담배"

### 2.3 행사/이벤트

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 4 | EVT01 | STRING | (빈값) | 행사 정보 텍스트 | - |
| 5 | EVT01_MOBI | STRING | (빈값) | 모바일 행사 | - |
| 93 | EVT_DC_YMD | STRING | | 행사 할인 일자 | - |
| 94 | EVT_DC_PROFIT_RATE | STRING | | 행사 할인 이익률 | - |

### 2.4 규격/물류

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 7 | ITEM_SPEC | STRING | 75G / 161G / 28G | 규격 | - |
| 27 | ITEM_SW | BIGDECIMAL | 130 | 가로(mm) | - |
| 28 | ITEM_SL | BIGDECIMAL | 50 | 세로(mm) | - |
| 29 | ITEM_SH | BIGDECIMAL | 130 | 높이(mm) | - |
| 30 | ITEM_SWLH | STRING | 130 * 50 * 130 | 가로*세로*높이 | - |
| 31 | ITEM_G | BIGDECIMAL | 75 | 중량(g) | - |
| 91 | TOT_DISP_SEQ | STRING | 203050 | 전체 진열 순서 | - |

### 2.5 발주/단위

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 8 | SALE_UNIT | BIGDECIMAL | 1 | 판매 단위 | - |
| 9 | ORD_MULT_ULMT | BIGDECIMAL | 99 | 발주배수 상한 | - |
| 10 | ORD_MULT_LLMT | BIGDECIMAL | 1 | 발주배수 하한 | - |
| 11 | SALE_UNIT_QTY | BIGDECIMAL | 1 | 판매단위 수량 | - |
| 32 | ORD_UNIT | STRING | 01 / 02 | 발주단위 코드 | - |
| 33 | **ORD_UNIT_NM** | STRING | 낱개 / 묶음 | 발주단위 명칭 | O |
| 34 | **CASE_UNIT_QTY** | BIGDECIMAL | 20 / 50 / 500 | CASE 입수 | O |
| 35 | **ORD_UNIT_QTY** | BIGDECIMAL | 1 / 10 | 발주 입수 | O |

### 2.6 유통기한

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 36 | **EXPIRE_DAY** | BIGDECIMAL | 117 / 1 / 260 | **유통기한(일)** | O |
| 37 | EXPIRE_ID | STRING | 0 | 유통기한 구분 ID | - |
| 38 | EXPIRE_NM | STRING | 117 / 1일 / 260 | 유통기한 명칭 | - |

### 2.7 가격/원가

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 59 | **ITEM_WONGA** | BIGDECIMAL | 580 / 985 / 3709.09 | **원가** | - |
| 60 | **HQ_MAEGA_SET** | BIGDECIMAL | 1100 / 1700 / 4500 | **본부 매가** | - |
| 61 | STORE_MAEGA_SET | BIGDECIMAL | (빈값) | 점포 매가 설정 | - |
| 62 | STORE_BASE_MAEGA_SET | BIGDECIMAL | 1100 / 1700 / 4500 | 점포 기본매가 | - |
| 63 | **ITEM_MAEGA** | BIGDECIMAL | 1100 / 1700 / 4500 | **현재 매가** | - |
| 64 | OD_MAEGA | BIGDECIMAL | 1100 / 1700 / 4500 | OD 매가 | - |
| 65 | **PROFIT_RATE** | BIGDECIMAL | 37.64 / 36.26 / 9.33 | **이익률(%)** | - |
| 75 | FIX_WONGA | BIGDECIMAL | (빈값) | 고정 원가 | - |

### 2.8 발주 가능/정지 상태

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 39 | HIGHLOW_ID | STRING | 0 | 고저가 구분 | - |
| 40 | PITEM_ID | STRING | 7 / 1 | 상품 등급 ID | - |
| 41 | PITEM_ID_NM | STRING | 결품주의 / (빈값) | 상품 등급명 | - |
| 44 | BORD_STOP_YN | STRING | 0 | 본부 발주정지 여부 | - |
| 55 | ORD_PSS_ID | STRING | 0 | 발주가능 ID | - |
| 56 | **ORD_PSS_ID_NM** | STRING | 가능 / 불가 | **발주가능 상태** | O |
| 57 | ORD_PSS_SYMD | STRING | 20240115 | 발주가능 시작일 | - |
| 58 | CUT_ITEM_YN | STRING | 0 | CUT 상품 여부 | - |
| 69 | **ORD_STOP_SYMD** | STRING | (빈값) | **발주정지 시작일** | O |
| 70 | ORD_STOP_EYMD | STRING | (빈값) | 발주정지 종료일 | - |
| 71 | SALE_STOP_ID | STRING | 0 | 판매정지 ID | - |
| 85 | ORD_STOP_PLAN_YMD | STRING | 2025-11-17 | 정지예정일 | - |
| 86 | REASON_ID | STRING | (빈값) | 정지 사유 ID | - |
| 87 | CHG_ITEM_CD | STRING | (빈값) | 대체상품 코드 | - |
| 92 | DISUSE_GB | STRING | 가능 | 폐기 가능 구분 | - |

### 2.9 거래처/물류

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 47 | FUR_CD | STRING | 31 / 71 | 배송처 코드 | - |
| 48 | **CUST_CD** | STRING | 4218800 / 3423800 | **거래처 코드** | - |
| 49 | **CUST_NM** | STRING | (주)농심 / (주)한국필립모리스 | **거래처명** | - |
| 50 | **CENTER_NM** | STRING | BGF로지스용인상온1 / 씨제이오산냉장1 | **배송센터명** | - |

### 2.10 기타

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 12 | BTL_ITEM_CD | STRING | | 병 상품코드 | - |
| 13 | BOX_ITEM_CD | STRING | | 박스 상품코드 | - |
| 14 | UNIT_ITEM_CD | STRING | | 단위 상품코드 | - |
| 15 | DAY_DC_YN | STRING | 0000000 | 요일 할인 여부 | - |
| 16 | TOT_ITEM_SPEC | BIGDECIMAL | 0 | 전체 상품 규격 | - |
| 17 | DISP_UNIT | STRING | 0 | 진열 단위 | - |
| 18 | DISP_STD | STRING | | 진열 기준 | - |
| 19 | JIP_ITEM_CD | STRING | 8801043015653 | 입고 상품코드 | - |
| 42 | JIP_ITEM_NM | STRING | 농심)육개장사발면 | 입고 상품명 | - |
| 43 | ITEM_DESC | STRING | (상품설명) | 상품 설명 텍스트 | - |
| 45 | DEEM_TAX_ID | STRING | 0 | 간주과세 ID | - |
| 46 | SET_DJ_TY_ID | STRING | 0 | SET 유형 ID | - |
| 51 | MAEGA_CHG_EYMD | STRING | | 매가 변경 종료일 | - |
| 52 | PYUNSU_ID | STRING | 0 / 1 | 편수 ID | - |
| 53 | RET_PSS_ID | STRING | 0 | 반품가능 ID | - |
| 54 | RET_PSS_ID_NM | STRING | 불가 | 반품가능 명칭 | - |
| 66 | MAEGA_CHG_ID | STRING | 0 | 매가변경 ID | - |
| 67 | TAX_ID | STRING | 과세 | 과세 구분 | - |
| 68 | SSR_ITEM_AGREE_ID | STRING | | SSR 동의 ID | - |
| 72 | SALE_STOP_NM | STRING | 과세 | 판매정지 명칭 | - |
| 73 | CT_ITEM_YN | STRING | 0 | CT 상품 여부 | - |
| 74 | SUM_UNIT_ID | STRING | 0 | 합산 단위 ID | - |
| 76 | BTL_ID | STRING | 0 | 병 ID | - |
| 77 | DRINK_BTL_ID | BIGDECIMAL | 0 | 음료병 ID | - |
| 78 | IMG_CHK | STRING | 1 | 이미지 존재 여부 | - |
| 79 | IMAGE_DIR | STRING | m | 이미지 디렉토리 | - |
| 80 | IMG_URL | STRING | {item_cd}_M.jpg | 이미지 URL | - |
| 81 | WMCHG_YN | STRING | 0 | 원가변경 여부 | - |
| 82 | WMCHG_YMD | STRING | | 원가변경 일자 | - |
| 83 | DC_DESC | STRING | 비대상 | 할인 설명 | - |
| 84 | BONUS_DESC | STRING | 비대상 | 보너스 설명 | - |
| 88 | RET_SYMD | STRING | | 반품 시작일 | - |
| 89 | RET_EYMD | STRING | | 반품 종료일 | - |
| 90 | RET_YMD | STRING | | 반품 일자 | - |
| 95 | RB_AMT | STRING | | 리베이트 금액 | - |
| 96 | RB_CON | STRING | | 리베이트 조건 | - |
| 97 | RB_YMD | STRING | | 리베이트 일자 | - |

---

## 3. dsItemDetailOrd (30개)

| # | 컬럼명 | 타입 | 샘플값 | 설명 | 현재 사용 |
|---|--------|------|--------|------|:---------:|
| 0 | HQ_MAEGA_SET | STRING | 1100 | 본부매가 | - |
| 1 | ITEM_CD | STRING | 8801043015653 | 상품코드 | - |
| 2 | ITEM_NM | STRING | 농심)육개장사발면 | 상품명 | - |
| 3 | PYUN_ITEM_CD | STRING | None | 편의 상품코드 | - |
| 4 | ITEM_MAEGA | STRING | 1100 | 현재매가 | - |
| 5 | JIP_ITEM_CD | STRING | 8801043015653 | 입고상품코드 | - |
| 6 | **NEXT_ORD_YMD** | STRING | 20260228 | **차발주일** | - |
| 7 | **NOW_QTY** | STRING | 7 / 0 / 13 | **현재고** | - |
| 8 | OD_MAEGA | STRING | 1100 | OD매가 | - |
| 9 | **ORD_ADAY** | STRING | 월화수목금토 | **발주가능요일** | O |
| 10 | **ORD_LEADTIME** | STRING | 1 | **리드타임(일)** | - |
| 11 | ORD_MUL_QTY | STRING | (빈값) | 발주배수 | - |
| 12 | ORD_MULT_LLMT | STRING | 1 | 발주배수 하한 | - |
| 13 | ORD_MULT_ULMT | STRING | 99 | 발주배수 상한 | - |
| 14 | ORD_PSS_ID | STRING | None | 발주가능 ID | - |
| 15 | ORD_PSS_CHK | STRING | 0 | 발주가능 체크 | - |
| 16 | **ORD_PSS_CHK_NM** | STRING | 가능 | **발주가능 상태명** | O |
| 17 | ORD_PSS_SYMD | STRING | 20240115 | 발주가능 시작일 | - |
| 18 | ORD_STOP_EYMD | STRING | | 발주정지 종료일 | - |
| 19 | ORD_STOP_SYMD | STRING | | 발주정지 시작일 | - |
| 20 | ORD_TURN_HMS | STRING | 100000 | 발주 마감 시간 | - |
| 21 | **ORD_UNIT_QTY** | STRING | 1 / 10 | **발주입수** | O |
| 22 | ORD_YMD | STRING | 20260227 | 발주일 | - |
| 23 | ORD_YMDHMS | STRING | 20260227100000 | 발주 일시 | - |
| 24 | PYUN_ID | STRING | 0 | 편수 ID | - |
| 25 | PYUN_QTY | STRING | | 편수 수량 | - |
| 26 | PRE_STORE_CD | STRING | 190623 | 이전 매장코드 | - |
| 27 | STORE_BASE_MAEGA_SET | STRING | 1100 | 매장기본매가 | - |
| 28 | STORE_CD | STRING | 46513 | 매장코드 | - |
| 29 | STORE_MAEGA_SET | STRING | | 매장매가설정 | - |

---

## 4. UI 컴포넌트 구조

### divInfo (기본 정보)

| 컴포넌트 | 라벨 | 샘플값 | 매핑 컬럼 |
|----------|------|--------|-----------|
| stItemNm | 상품명칭 | 농심)육개장사발면 | ITEM_NM |
| stItemCd | 상품코드 | 8801043015653 | ITEM_CD |
| stTaxId | 과세구분 | 과세 | TAX_ID |
| stPitemId | 등급 | 결품주의 / (빈값) | PITEM_ID_NM |
| stPyunsuId | 회차구분 | 0 / 1 | PYUNSU_ID |
| stWDH | 가로*세로*높이 | 130 * 50 * 130 | ITEM_SWLH |
| **stClass** | **분류** | **간편식사 > 주먹밥 > 삼각김밥** | **CLASS_NM** |
| stWDH00 | 규격 | 75G | ITEM_SPEC |
| stCenterNm | 배송처 | BGF로지스용인상온1 | CENTER_NM |
| stCustNm | 거래처 | (주)농심 | CUST_NM |

### divInfo01 (발주정지)

| 컴포넌트 | 라벨 | 샘플값 | 매핑 컬럼 |
|----------|------|--------|-----------|
| stStopPlanYmd | 정지예정일 | 2025-11-17 ~ | ORD_STOP_PLAN_YMD |
| stStopReason | 정지사유 | 운영종료 | (DOM 텍스트) |
| stOrdStopYmd | 발주정지일 | None | ORD_STOP_SYMD |
| edReplItem | 대체상품 | 8800336392136 | CHG_ITEM_CD |

### divInfo02 (발주/가격)

| 컴포넌트 | 라벨 | 샘플값 | 매핑 컬럼 |
|----------|------|--------|-----------|
| meItemMaegaSet | 현재매가 | 1,100 / 4,500 | ITEM_MAEGA |
| meWonga | 원가 | 580 / 3,709 | ITEM_WONGA |
| meProfitRate | 이익률 | 37.64 / 9.33 | PROFIT_RATE |
| stCaseUnitQty | CASE입수 | 20 / 500 | CASE_UNIT_QTY |
| stLeadTm | 납품소요 | 1 / None | ORD_LEADTIME |
| stOrdUnit | 발주단위 | 낱개 / 묶음 | ORD_UNIT_NM |
| stOrdUnitQty | 발주입수 | 1 / 10 | ORD_UNIT_QTY |
| stExpireNm | 유효기간 | 117 / 1 / 260 | EXPIRE_NM |
| edOrdMul | 발주배수 | None | ORD_MUL_QTY |
| meHQMaegaSet | 본부매가 | 1,100 / 4,500 | HQ_MAEGA_SET |
| meNowQty | 현재고 | 7 / 0 / 13 | NOW_QTY |
| meNextOrdYmd | 차발주일 | 2026-02-28 | NEXT_ORD_YMD |

### divInfo03 (버튼)

| 컴포넌트 | 설명 |
|----------|------|
| btNowQtyChg | 현재고 수정 |
| btAutoOrd | 자동/스마트발주 등록 |
| btn_ItemReport | 상품정보분석 보기 |

### form-level

| 컴포넌트 | 설명 |
|----------|------|
| btn_save | 저장 |
| btn_close | 닫기 |
| btn_x | X 닫기 |
| imgItemUrl | 상품 이미지 |
| taItemDesc | 상품 설명 텍스트 |
| stEvt01 | 행사 정보 텍스트 |
| btEvtDetail | 행사 상세 버튼 |
| stRebate | 리베이트 (비대상) |
| grd_summ | 발주/납품/판매/폐기 그리드 |
| btn_search | 조회 버튼 |

---

## 5. 분류 체계 정리

### 3단계 분류 구조

```
LARGE_CD (대분류코드) → LARGE_NM (대분류명)
  └── MID_CD (중분류코드) → MID_NM (중분류명)
       └── SMALL_CD (소분류코드) → SMALL_NM (소분류명)

CLASS_NM = "{LARGE_NM} > {MID_NM} > {SMALL_NM}"
```

### 실제 샘플

| 상품 | LARGE_CD | LARGE_NM | MID_CD | MID_NM | SMALL_CD | SMALL_NM | CLASS_NM |
|------|:--------:|----------|:------:|--------|:--------:|----------|----------|
| 육개장사발면 | 20 | 가공식품 | 032 | 라면류 | 055 | 용기면 | 가공식품 > 라면류 > 용기면 |
| 3XL베이컨햄마요김치 | 10 | 간편식사 | 002 | 주먹밥 | 010 | 삼각김밥 | 간편식사 > 주먹밥 > 삼각김밥 |
| 팔리아멘트아쿠아3mg | 45 | 담배 | 072 | 담배 | 220 | 외산담배 | 담배 > 담배 > 외산담배 |

### 현재 활용 현황

| 컬럼 | 현재 수집 | 현재 저장 | 비고 |
|------|:---------:|:---------:|------|
| LARGE_CD | X | X | 미수집 |
| LARGE_NM | X | X | 미수집 |
| MID_CD | O | products.mid_cd | batch collector |
| MID_NM | X | mid_categories.mid_nm | sales_collector에서만 저장 |
| SMALL_CD | X | X | 미수집 |
| SMALL_NM | X | X | 미수집 |
| CLASS_NM | X | X | 미수집 (UI stClass) |

---

## 6. 미활용 유용 컬럼

추후 활용 가능한 미수집 데이터:

| 컬럼 | 활용 시나리오 |
|------|-------------|
| LARGE_CD/NM, SMALL_CD/NM | 상세 카테고리 분류 |
| CLASS_NM | 전체 분류 경로 표시 |
| MID_NM | mid_categories 보충 |
| ITEM_WONGA / PROFIT_RATE | 원가/이익률 분석 |
| CUST_CD / CUST_NM | 거래처 분석 |
| CENTER_NM | 배송센터 분석 |
| NOW_QTY | 실시간 재고 크로스체크 |
| ORD_LEADTIME | 리드타임 정확화 |
| NEXT_ORD_YMD | 차발주일 확인 |
| PITEM_ID_NM | 결품주의 등 상품 등급 |
| ITEM_DESC | 상품 설명 (검색/분류용) |
| IMG_URL | 대시보드 상품 이미지 |
| ORD_STOP_PLAN_YMD | 정지예정 사전 알림 |
| CHG_ITEM_CD | 대체상품 자동 발주 |
