# BGF 사이트 미수집 데이터 소스 분석

> **분석일**: 2026-03-08
> **목적**: 현재 수집하지 않는 BGF 사이트 데이터 중 예측/발주에 활용 가능한 것 식별

---

## 1. 현재 수집 현황

### 1.1 수집 중인 데이터 (Collector별)

| Collector | 소스 화면 | 수집 데이터 | API 엔드포인트 |
|-----------|----------|------------|--------------|
| OrderPrepCollector | STBJ030 | dsGeneralGrid (55컬럼), dsItem, dsOrderSale | `/stbj030/selSearch` |
| DirectApiFetcher | STBJ030 | selSearch SSV 파싱 | `/stbj030/selSearch` |
| DirectPopupFetcher | STBJZ00 팝업 | dsItemDetail(98), dsItemDetailOrd(30), dsOrderSale(90일) | `/stbjz00/selItemDetail*` |
| SalesCollector | STMB010 | 시간대별 매출 (HMS, AMT, CNT) | `/stmb010/selDay` |
| HourlySalesCollector | STMB010 | 시간대별 매출 | `/stmb010/selDay` |
| HourlySalesDetailCollector | STMB010 | 시간대별 상품 상세 | `/stmb010/selPrdT3` |
| ReceivingCollector | STGJ010 | 센터 매입(입고) 데이터 | `/stgj010/selSearch` |
| DirectReceivingFetcher | STGJ010 | Direct API 입고 | `/stgj010/selSearch` |
| WasteSlipCollector | STGJ020 | 폐기 전표 | `/stgj020/selSearch` |
| DirectWasteSlipFetcher | STGJ020 | Direct API 폐기전표 | `/stgj020/selSearch` |
| OrderStatusCollector | STBJ070 | 발주현황, 발주단위 | `/stbj070/selListData` |
| DirectOrderStatusFetcher | STBJ070 | Direct API 발주현황 | `/stbj070/selListData` |
| WeatherCollector | TopFrame | ds_weatherTomorrow (날씨 예보) | (TopFrame 직접) |
| ProductInfoCollector | STBJ030 팝업 | 상품 상세 정보 | `/stbjz00/selItemDetailSearch` |
| ProductDetailBatchCollector | STBJ030 팝업 | 배치 상품 상세 | `/stbjz00/selItemDetailSearch` |
| PromotionCollector | STBJ030 | 행사 정보 (MONTH_EVT 등) | `/stbj030/selSearch` |
| FailReasonCollector | STBJ030 | 입고실패 사유 | (발주 과정 중) |
| HistoricalDataCollector | 복합 | 히스토리 수집 | (복합) |
| NewProductCollector | STGJ010 | 신규 상품 감지 | (입고 과정 중) |

### 1.2 수집 중인 데이터 요약

- **발주**: 단품별 발주 55컬럼 + 팝업 98컬럼 + 90일 이력
- **매출**: 시간대별 매출 + 상품별 시간대 상세
- **입고**: 센터 매입 내역
- **폐기**: 통합 전표 (헤더 레벨)
- **날씨**: 내일 예보 (기온/강수/날씨유형)
- **발주현황**: 발주 내역 + 발주단위

---

## 2. 미수집 데이터 (활용 가치 순)

### 2.1 ★★★ 높은 활용 가치

#### A. STMB011_M0 — 중분류별 매출 구성비
- **API**: `/stmb011/selSearch`
- **데이터**: MID_CD, MID_NM, CUR_AMT(당기매출), CUR_CNT(당기건수), PRE_AMT(전기매출), PRE_CNT(전기건수), CUR_RATE, PRE_RATE
- **활용**: 카테고리 수준 수요 트렌드 비교 (전기 대비 당기), 카테고리별 성장/하락 추적
- **현재 부재**: daily_sales에서 개별 집계로 추정 중 → 서버 공식 값 수집 가능

#### B. STMB340_M0 — 요일별 상품 매출분석
- **API**: `/stmb340/selSearch`
- **데이터**: ITEM_CD, ITEM_NM, MON_QTY~SUN_QTY, MON_AMT~SUN_AMT
- **활용**: 요일계수 DB화의 정확한 서버 기준값, 개별 상품의 요일 패턴 확인
- **현재 부재**: `get_food_weekday_coefficient()`에서 daily_sales 4주 평균으로 자체 계산 중

#### C. STBJ490_M0 — 품절상품현황
- **API**: `/stbj490/selMainSearch`
- **데이터**: ITEM_CD, ITEM_NM, MID_NM, STOCK_QTY, LAST_SALE_YMD, AVG_SALE_QTY
- **활용**: 실시간 품절 감지, 긴급 보충 발주 트리거, 품절 빈도 분석
- **현재 부재**: realtime_inventory.stock_qty=0으로 간접 감지 중 (정확도 낮음)

#### D. STBJ500_M0 / STBJ510_M0 — 신상품/+1 랭킹
- **API**: `/stbj500/mainSelSearch`, `/stbj510/mainSelSearch`
- **데이터**: RANK_NO, ITEM_CD, ITEM_NM, SALE_QTY, SALE_AMT, ORD_YN
- **활용**: 전국/입지별 신상품 판매 추세 → 신제품 초기발주량 보정
- **현재 부재**: 유사상품 mid_cd 중위값으로 추정 (new-product-lifecycle)

#### E. STBJ080_M0 — 상품별 발주 카렌더
- **API**: `/stbj080/selSearch`
- **데이터**: ITEM_CD, ITEM_NM, DAY1~DAY31 (월간 일별 발주량)
- **활용**: 발주 패턴 분석, 발주 주기 자동 감지, 과소/과다 발주 히스토리
- **현재 부재**: order_tracking에서 자체 기록만 관리

#### F. STMB330_M0 — 상품별 매출 분석
- **API**: `/stmb330/selSearch`
- **데이터**: ITEM_CD, ITEM_NM, MID_NM, SALE_QTY, SALE_AMT, PROFIT, PROFIT_RATE, RANK
- **활용**: 상품별 수익성 분석, 이익률 기반 발주 우선순위
- **현재 부재**: PROFIT_RATE 서버값 미수집 (dsGeneralGrid에 있지만 활용 부족)

### 2.2 ★★ 중간 활용 가치

#### G. STMB350_M0 — 매출조회차트
- **데이터**: 일별/주별/월별 매출 추이 차트 데이터
- **활용**: 트렌드 시각화, 장기 수요 패턴

#### H. STBJ150_M0 — 장려금/광고비 대상 발주
- **API**: `/stbj150/selMainSearch`
- **데이터**: ITEM_CD, JANG_AMT(장려금액), JANG_COND(장려금조건), JANG_PERIOD(장려금기간)
- **활용**: 장려금 최적화 발주 (손익 개선), 장려금 조건 미충족 방지
- **현재 부재**: 대시보드에 "장려금 달성률 19%" 표시되지만 개별 상품 조건 미수집

#### I. STBJ330_M0 — 발주정지 상품조회
- **API**: `/stbj330/selSearch`
- **데이터**: ITEM_CD, CUT_START_YMD, CUT_END_YMD, CUT_REASON
- **활용**: CUT 상품 정확한 기간 파악, food-cut-replacement 대체 보충 개선
- **현재 부재**: 발주 시점에 CUT_ITEM_YN으로만 감지

#### J. STBJ520_M0 — 원가DC 랭킹
- **API**: `/stbj520/selSearch`
- **데이터**: ITEM_CD, DC_RATE, DC_PERIOD, SALE_QTY
- **활용**: 할인 상품 수요 증가 예측, 할인 종료 후 수요 감소 대비

#### K. STMB310_M0 — 배달/픽업 매출분석
- **데이터**: YMD, DELI_AMT, DELI_CNT, PICK_AMT, PICK_CNT
- **활용**: 배달/픽업 채널 수요 분리 예측

#### L. STMB251_M0 — 단골고객 주요정보
- **데이터**: MEM_TYPE, MEM_CNT, VISIT_CNT, AVG_AMT
- **활용**: 고객 세그먼트별 수요 특성

### 2.3 ★ 참고/보조 가치

#### M. STJK010_M0 / STJK030_M0 — 재고현황
- **활용**: 대분류/상품별 재고 금액 → 재고 회전율 계산
- **현재**: realtime_inventory로 개별 재고 관리 중

#### N. STBJ400_M0 — 통합 발주
- **활용**: 카테고리 단위 발주 화면 → 발주 흐름 자동화 확장

#### O. STBJ071_M0 — 행사상품 발주조회
- **활용**: 행사 발주 이력 별도 추적

#### P. STJS010_M0 — 정산
- **활용**: 일별 정산 데이터 → 매출/송금 차이 추적

---

## 3. 수집 우선순위 로드맵

### Phase 1 (즉시 적용 가능 — Direct API)

| 순위 | 소스 | 엔드포인트 | 예상 난이도 | 효과 |
|------|------|-----------|-----------|------|
| 1 | 품절상품현황 | `/stbj490/selMainSearch` | 낮음 | 실시간 품절 감지 |
| 2 | 중분류별 매출 구성비 | `/stmb011/selSearch` | 낮음 | 카테고리 트렌드 |
| 3 | 신상품 랭킹 | `/stbj500/mainSelSearch` | 낮음 | 초기발주 개선 |
| 4 | +1 랭킹 | `/stbj510/mainSelSearch` | 낮음 | 행사 수요 예측 |

### Phase 2 (중기 — 분석 파이프라인 연동)

| 순위 | 소스 | 엔드포인트 | 효과 |
|------|------|-----------|------|
| 5 | 요일별 상품 매출 | `/stmb340/selSearch` | 요일계수 정밀화 |
| 6 | 상품별 발주 카렌더 | `/stbj080/selSearch` | 발주 패턴 분석 |
| 7 | 장려금 대상 | `/stbj150/selMainSearch` | 장려금 최적화 |
| 8 | 발주정지 상품 | `/stbj330/selSearch` | CUT 대체 개선 |

### Phase 3 (장기 — 고도화)

| 순위 | 소스 | 효과 |
|------|------|------|
| 9 | 상품별 매출 분석 | 수익성 기반 우선순위 |
| 10 | 원가DC 랭킹 | 할인 수요 예측 |
| 11 | 배달/픽업 매출 | 채널 분리 예측 |

---

## 4. Direct API 수집 구현 가이드

### 4.1 기본 패턴

모든 BGF API는 동일한 SSV(넥사크로) 프로토콜 사용:

```python
# 요청: POST https://store.bgfretail.com/{endpoint}
# Content-Type: application/x-www-form-urlencoded (SSV body)
# 응답: SSV 포맷 (RS/US 구분자)

import requests

def call_bgf_api(session, endpoint, in_datasets, out_dataset_names):
    """BGF Direct API 호출 범용 함수

    Args:
        session: requests.Session (쿠키 포함)
        endpoint: 'stbj490/selMainSearch'
        in_datasets: {'dsCond': {'col1': 'val1', ...}}
        out_dataset_names: ['dsList']
    """
    url = f'https://store.bgfretail.com/{endpoint}'
    body = build_ssv_request(in_datasets)
    resp = session.post(url, data=body)
    return parse_ssv_response(resp.text, out_dataset_names)
```

### 4.2 쿠키 추출 (Selenium → requests)

```python
# Selenium 세션에서 쿠키 복사
selenium_cookies = driver.get_cookies()
session = requests.Session()
for cookie in selenium_cookies:
    session.cookies.set(cookie['name'], cookie['value'])
```

### 4.3 SSV 프로토콜

```
RS = \x1e (Record Separator)  — 데이터셋 구분
US = \x1f (Unit Separator)    — 컬럼/값 구분

요청 body:
  SSV:{inDataset_name}{RS}{col1}{US}{col2}{RS}{val1}{US}{val2}

응답 body:
  SSV:{outDataset_name}{RS}{col1}{US}{col2}{US}...{RS}{row1_val1}{US}{row1_val2}...{RS}...
```

---

## 5. 참고: 넥사크로 JavaScript 디버깅 팁

### 5.1 화면 데이터셋 재귀 탐색

```javascript
function scanAllDatasets(menuId) {
  const fs = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet;
  const frame = fs[menuId];
  const allDS = [];
  function scan(form) {
    if (!form) return;
    if (form.objects) {
      for (let i = 0; i < form.objects.length; i++) {
        const obj = form.objects[i];
        if (obj._type_name === 'Dataset') {
          allDS.push(obj);
        }
      }
    }
    if (form.components) {
      for (let i = 0; i < form.components.length; i++) {
        const c = form.components[i];
        if (c._type_name === 'Div' && c.form) scan(c.form);
      }
    }
  }
  scan(frame.form);
  return allDS;
}
```

### 5.2 데이터셋 전체 덤프

```javascript
function dumpDataset(ds) {
  const cols = [];
  for (let c = 0; c < ds.getColCount(); c++) cols.push(ds.getColID(c));
  const rows = [];
  for (let r = 0; r < ds.rowcount; r++) {
    const row = {};
    cols.forEach(col => { row[col] = ds.getColumn(r, col); });
    rows.push(row);
  }
  return { id: ds.id, columns: cols, rows };
}
```

### 5.3 XHR 인터셉터

```javascript
window.__apiCalls = [];
const origXHR = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url, ...rest) {
  window.__apiCalls.push({ method, url, time: Date.now() });
  return origXHR.call(this, method, url, ...rest);
};
```

### 5.4 메뉴 배치 탐색 자동화

```javascript
// 메뉴 열기 → 대기 → 수집 → 닫기 → 반복
const queue = ['STMB010_M0', 'STMB011_M0', ...];
let results = {};
function processNext(idx) {
  if (idx >= queue.length) return;
  const topForm = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.TopFrame.form;
  if (idx > 0) topForm.gfn_formClose(queue[idx - 1]);
  topForm.gfn_openMenuId(queue[idx]);
  setTimeout(() => {
    results[queue[idx]] = scanAllDatasets(queue[idx]);
    processNext(idx + 1);
  }, 3000);
}
processNext(0);
```

---

## Version History

| 버전 | 날짜 | 변경 | 작성자 |
|------|------|------|--------|
| 1.0 | 2026-03-08 | 초기 작성 | Claude Code |
