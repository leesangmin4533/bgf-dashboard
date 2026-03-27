# STBJ030 단품별 발주 화면 전체 데이터셋 참조

> **생성일**: 2026-03-08
> **덤프 스크립트**: `scripts/dump_selsearch_columns.py`, `scripts/explore_ordersale_avg.py`
> **덤프 원본**: `captures/selsearch_columns_20260308_212955.json`
> **라이브 검증**: 2026-03-08 21:29 (Phase 1 nexacro dump 성공)

---

## 1. 데이터셋 전체 목록 (7개)

STBJ030_M0 단품별 발주 화면에 존재하는 모든 nexacro dataset:

| 데이터셋 | 컬럼 수 | 바인딩 Grid | 설명 | SSV 출처 |
|----------|:-------:|------------|------|----------|
| **dsGeneralGrid** | 55 | gdList | 발주 메인 그리드 | selSearch |
| **dsItem** | 55 | (= dsGeneralGrid) | dsGeneralGrid와 동일 | selSearch |
| **dsOrderSale** | 8 | - | 일별 발주/입고/판매/폐기 이력 | selSearch |
| **dsOrderSaleBind** | 92 | grd_summ | dsOrderSale 피벗 (DAY1~91 + AVG) | 클라이언트 생성 |
| **dsSaveChk** | 6 | - | 발주 저장 확인용 | selSearch |
| **dsWeek** | 1 | - | 발주일(ORD_YMD) 목록 | selSearch |
| **dsCheckMagam** | 4~5 | - | 발주 마감 시간 | selSearch |

### 접근 제한

- **발주 시간대에만 조회 가능** (예: 10:00~11:00, dsCheckMagam으로 확인)
- 시간대 밖에서 상품 검색 시 Alert: "발주 가능한 상품이 없습니다."
- Alert 발생 시 dsOrderSale, dsOrderSaleBind 등 데이터 미적재

---

## 2. dsGeneralGrid / dsItem (55컬럼)

발주 메인 그리드. 두 데이터셋은 동일 구조.

| # | 컬럼명 | 설명 | 타입 | 발주 시 핵심 |
|---|--------|------|------|:--------:|
| 0 | STORE_CD | 매장코드 | STRING | |
| 1 | ORD_YMD | 발주일자 | STRING | O |
| 2 | PRE_STORE_CD | 이전 매장코드 | STRING | |
| 3 | JIP_ITEM_CD | 입고상품코드 | STRING | |
| 4 | RET_PSS_ID | 반품가능여부 | STRING | |
| 5 | ITEM_NM | 상품명 | STRING | O |
| 6 | PITEM_ID | 필수등급 (A/B/C) | STRING | |
| 7 | PITEM_ID_CSS | 필수등급 CSS | STRING | |
| 8 | PITEM_ID_NM | 필수등급명 (MUST/...) | STRING | |
| 9 | PROFIT_RATE | 이익률 | STRING | |
| 10 | HQ_MAEGA_SET | 본사 매가 | STRING | |
| 11 | ORD_UNIT | 발주단위 코드 | STRING | |
| 12 | **ORD_UNIT_QTY** | **발주단위수량 (입수)** | STRING | O |
| 13 | **ITEM_CD** | **상품코드** | STRING | O |
| 14 | PYUN_ID | 편수 ID | STRING | |
| 15 | **PYUN_QTY** | **발주배수** | STRING | O |
| 16 | ORD_TURN_HMS | 발주차수시간 | STRING | |
| 17 | ABSENCE | 결품 | STRING | |
| 18 | ORD_MULT_ULMT | 발주배수 상한 | STRING | |
| 19 | ORD_MULT_LLMT | 발주배수 하한 | STRING | |
| 20 | CT_ITEM_YN | CT 상품여부 | STRING | |
| 21 | CUT_ITEM_YN | CUT 상품여부 | STRING | |
| 22 | **NOW_QTY** | **현재고** | STRING | O |
| 23 | **ORD_MUL_QTY** | **발주수량 (=배수×입수)** | STRING | O |
| 24 | OLD_PYUN_QTY | 기존 배수 | STRING | O |
| 25 | TOT_QTY | 합계수량 | STRING | |
| 26 | CURDAY | 현재날짜 | STRING | |
| 27 | CURTIME | 현재시간 | STRING | |
| 28 | MID_NM | 중분류명 | STRING | |
| 29 | PRE_BKCOLOR | 배경색 | STRING | |
| 30 | IMG_CHK | 이미지 체크 | STRING | |
| 31 | IMG_URL | 이미지 URL | STRING | |
| 32 | PAGE_CNT | 페이지 수 | STRING | |
| 33 | EXPIRE_DAY | 유통기한 일수 | STRING | |
| 34 | MONTH_EVT | 당월 행사 | STRING | |
| 35 | NEXT_MONTH_EVT | 차월 행사 | STRING | |
| 36 | RT_GB | RT 구분 | STRING | |
| 37 | RT_GB_CSS | RT 구분 CSS | STRING | |
| 38 | EVT_DC_YN | 행사할인 여부 | STRING | |
| 39 | EVT_DC_CSS | 행사할인 CSS | STRING | |
| 40 | RB_YN | RB 여부 | STRING | |
| 41 | RB_CSS | RB CSS | STRING | |
| 42 | WMCHG_YN | 가격변경 여부 | STRING | |
| 43 | WMCHG_CSS | 가격변경 CSS | STRING | |
| 44 | STOP_PLAN_YN | 발주정지 예정 | STRING | |
| 45 | STOP_PLAN_CSS | 발주정지 CSS | STRING | |
| 46 | EVT_DC_YMD | 행사할인 일자 | STRING | |
| 47 | EVT_DC_RATE | 행사할인율 | STRING | |
| 48 | RB_AMT | RB 금액 | STRING | |
| 49 | RB_CON | RB 조건 | STRING | |
| 50 | RB_YMD | RB 일자 | STRING | |
| 51 | RT_YMD | RT 일자 | STRING | |
| 52 | STOP_PLAN_YMD | 발주정지 일자 | STRING | |
| 53 | ITEM_CHK | 상품 체크 | STRING | |
| 54 | NAP_NEXTORD | 납품다음발주 | STRING | |

### 미존재 확인된 컬럼 (2026-03-08 라이브 검증)

| 컬럼명 | 상태 | 비고 |
|--------|------|------|
| **SUGGEST_QTY** | **미존재** | 로드맵 문서에 "수집 전 검증 필요"로 기재됨 → 검증 결과 없음 |
| **DISPLAY_QTY** | **미존재** | 진열수량 컬럼 없음 |
| **RCOM_QTY** | **미존재** | 추천수량 컬럼 없음 |
| **AUTO_ORD_QTY** | **미존재** | 자동발주 추천 컬럼 없음 |

> 7개 데이터셋, 총 139개 고유 컬럼에서 검색. 추천/진열 관련 컬럼 전무.

---

## 3. dsOrderSale (8컬럼)

일별 발주/입고/판매/폐기 이력. 최대 91일.

| # | 컬럼명 | 타입 | 설명 |
|---|--------|------|------|
| 0 | ORD_YMD | STRING | 날짜 (YYYYMMDD) |
| 1 | JIP_ITEM_CD | STRING | 입고상품코드 |
| 2 | ITEM_CD | STRING | 상품코드 |
| 3 | **ORD_QTY** | INT | 발주수량 |
| 4 | **BUY_QTY** | INT | 입고수량 |
| 5 | **SALE_QTY** | INT | 판매수량 |
| 6 | **DISUSE_QTY** | INT | 폐기수량 |
| 7 | SUM_UNIT_ID | STRING | 합계단위 ID |

### 데이터 출처

- **selSearch (/stbj030/selSearch)**: SSV 응답에 포함되나, 발주 시간대 밖에서는 0행
- **selItemDetailSale (/stbjz00/selItemDetailSale)**: 팝업 상세에서 90일 이력 반환 (발주 시간 무관)
- **우리 DB**: `daily_sales` 테이블에 동일 데이터 저장 중 (sale_qty, ord_qty, buy_qty, disuse_qty)

---

## 4. dsOrderSaleBind (92컬럼) — AVG 분석 완료

### 구조

dsOrderSale의 **클라이언트 측 피벗** (서버에서 오는 것이 아님)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| DAY1 ~ DAY91 | STRING | 일별 수량 (DAY1 = 가장 최근) |
| **AVG** | BIGDECIMAL | 91일 평균 |

### 행 구성 (4행)

| Row | 라벨 | 내용 |
|:---:|------|------|
| 0 | 발주 | ORD_QTY 일별 추이 |
| 1 | 입고 | BUY_QTY 일별 추이 |
| 2 | **판매** | **SALE_QTY 일별 추이** |
| 3 | 폐기 | DISUSE_QTY 일별 추이 |

### AVG 분석 결과 (2026-03-08)

**AVG = SUM(DAY1~DAY91) / 91** (단순 산술평균)

#### 우리 DB 데이터로 시뮬레이션한 비교

| 상품 | mid | BGF AVG91 | 우리 WMA7 | 차이 | 판매일/전체일 |
|------|-----|:---------:|:---------:|:----:|:----------:|
| 삼)참치마요삼각1 | 002 | 1.01 | 1.36 | +34% | 78/78 |
| 삼)김치볶음밥참치마요1 | 002 | 0.75 | 1.39 | +86% | 60/61 |
| 도)정성가득제육한상1 | 001 | 0.51 | 1.50 | +190% | 43/46 |
| 도)한끼만족중화한상1 | 001 | 0.16 | 1.00 | +507% | 15/15 |
| 농심)육개장사발면 | 032 | 4.63 | 4.57 | -1% | 91/91 |

#### 결론

| 항목 | 평가 |
|------|------|
| **수집 가치** | **없음** — 우리 WMA가 상위 호환 |
| **과소평가 문제** | 91일 미만 데이터 시 심각 (0일 포함하여 평균 계산) |
| **반응성** | 매우 둔감 — 91일 기간이라 최근 트렌드 미반영 |
| **접근성** | 발주 시간대에만 조회 가능 (10:00~11:00) |
| **계산 복잡도** | 단순 산술평균 — 우리 파이프라인이 훨씬 정교 |
| **앙상블 활용** | 불필요 — 동일 원본 데이터(daily_sales)에서 더 나은 지표 계산 중 |

> **권장**: 로드맵에서 dsOrderSaleBind.AVG 수집은 제외.
> SUGGEST_QTY도 미존재 확인 → 수집 대상 제외.

---

## 5. dsSaveChk (6컬럼)

발주 저장 시 확인용 데이터셋.

| # | 컬럼명 | 설명 |
|---|--------|------|
| 0 | ITEM_CD | 상품코드 |
| 1 | ITEM_NM | 상품명 |
| 2 | MID_NM | 중분류명 |
| 3 | ORD_YMD | 발주일자 |
| 4 | ORD_MUL_QTY | 발주수량 |
| 5 | ORD_INPUT_NM | 입력구분명 |

> 발주 저장 API (saveOrd)에서 사용. `captures/save_api_template.json` 참조.

---

## 6. dsWeek (1컬럼)

| # | 컬럼명 | 설명 |
|---|--------|------|
| 0 | ORD_YMD | 발주 가능 일자 목록 |

---

## 7. dsCheckMagam (4~5컬럼)

| # | 컬럼명 | 샘플값 | 설명 |
|---|--------|--------|------|
| 0 | ORD_YMD | 20260227 | 발주일자 |
| 1 | S_ORD_TURN_HMS | 10:00 | 발주 시작 시간 |
| 2 | E_ORD_TURN_HMS | 11:00 | 발주 마감 시간 |
| 3 | SAVE_TIME_YN | Y | 저장가능 여부 |
| 4 | ORD_TURN_HMS | 100000 | 발주차수 시간코드 |

---

## 8. Grid 바인딩 매핑

| Grid | 바인딩 Dataset | 용도 |
|------|---------------|------|
| gdList | dsGeneralGrid (= dsItem) | 발주 메인 그리드 |
| grd_summ | dsOrderSaleBind | 판매/발주/입고 추이 그리드 |

---

## 9. API 엔드포인트 정리

| 엔드포인트 | 반환 데이터셋 | 발주시간 제한 | 용도 |
|-----------|-------------|:----------:|------|
| `/stbj030/selSearch` | dsItem, dsOrderSale, dsWeek, dsCheckMagam, dsSaveChk | **O** | 발주 메인 조회 |
| `/stbjz00/selItemDetailSearch` | dsItemDetail (98컬럼) | X | 상품 상세 팝업 |
| `/stbjz00/selItemDetailOrd` | dsItemDetailOrd (30컬럼) | X | 발주 정보 팝업 |
| `/stbjz00/selItemDetailSale` | dsOrderSale (90일 이력) | X | 판매 이력 팝업 |
| `/stbjz00/saveOrd` | gds_ErrMsg | **O** | 발주 저장 |

> selItemDetailSale은 발주 시간과 무관하게 90일 판매 이력 반환.
> 우리 시스템에서 daily_sales에 이미 동일 데이터 저장 중.

---

## 10. 검색 방식 참조

STBJ030에서 상품 검색하는 방식 (코드에서 사용):

| 방식 | 패턴 | 사용처 |
|------|------|--------|
| **Grid 셀 입력 + Enter** | gdList 마지막행 → 상품코드 입력 → Enter | `order_prep_collector.collect_for_item()` |
| **Direct API** | selSearch fetch() 직접 호출 (SSV) | `direct_api_fetcher.py` |
| ~~fn_search()~~ | ~~edtBarcode 입력 + fn_search()~~ | **비동기 문제로 자동화에 부적합** |

> **주의**: `fn_search()`는 넥사크로 비동기 트랜잭션을 트리거하므로
> Selenium 자동화에서 직접 호출하면 데이터가 로딩되지 않음.
> Grid 셀 입력 + Enter 방식 또는 Direct API 방식을 사용해야 함.

---

## 부록: 탐구 스크립트

| 스크립트 | 용도 | 사용법 |
|---------|------|--------|
| `scripts/dump_selsearch_columns.py` | 전체 데이터셋 컬럼 덤프 | `python scripts/dump_selsearch_columns.py --save` |
| `scripts/explore_ordersale_avg.py` | dsOrderSaleBind.AVG 라이브 탐구 | `python scripts/explore_ordersale_avg.py --save` |

> explore_ordersale_avg.py는 **발주 시간대(10:00~11:00)에 실행**해야 실제 AVG 값 확인 가능.
