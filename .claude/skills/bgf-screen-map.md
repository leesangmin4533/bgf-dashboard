# BGF 화면 매핑 스킬

## When to Use

- BGF 스토어 시스템의 특정 화면에 접근해야 할 때
- 데이터셋 이름, 컬럼명, 그리드 인덱스를 확인할 때
- 새로운 화면(프레임)을 스크래핑해야 할 때
- DOM 요소 ID 패턴을 찾아야 할 때 (팝업, 탭, 메뉴 등)

## Common Pitfalls

- ❌ 프레임 ID를 추측하여 사용 → "Cannot read property" 오류
- ✅ 이 문서의 프레임 ID 테이블에서 정확한 ID 확인

- ❌ `dsOrderSale.ORD_QTY`를 실제 개수로 사용 → 배수 단위임
- ✅ 미입고 개수 = (`ORD_QTY` × 입수) - `BUY_QTY`

- ❌ gdList 컬럼을 컬럼명으로 접근 → 행사 컬럼은 이름 없음
- ✅ `ds.getColID(11)`, `ds.getColID(12)`로 인덱스 접근

- ❌ 화면 진입 후 바로 데이터 조회 → 날짜 선택 팝업이 먼저 뜸
- ✅ 날짜 선택 팝업 처리 후 데이터 조회

- ❌ `wf` 변수를 화면마다 동일하게 사용 → 화면별 경로가 다름
- ✅ 각 화면의 정확한 접근 경로 확인 (STBJ030_M0 ≠ STAJ001_M0)

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| "FrameSet.XXX is undefined" | 프레임 ID 오타 또는 해당 화면 미열림 | 프레임 ID 테이블에서 정확한 ID 확인 |
| 데이터셋 행 수가 0 | 상품코드 입력/검색 미완료 | 상품코드 입력 → Enter → 1.5초 대기 후 조회 |
| gdList 컬럼 11/12가 빈 값 | 행사 없는 상품 | 빈 값은 정상 (행사 미진행 상품) |
| dsItem 값이 이전 상품 것 | 새 상품 검색 후 대기 부족 | `time.sleep(1.5)` 이상 대기 |
| 날짜 선택 팝업이 안 닫힘 | Button44 클릭 누락 | 날짜 행 더블클릭 후 "선택" 버튼 클릭 |
| 상품 상세 팝업에서 유통기한 못 읽음 | DOM ID 패턴 불일치 | `[id*="CallItemDetailPopup"][id*="stExpireNm"]` 패턴 사용 |

---

## 화면별 프레임 ID

| 메뉴 경로 | 프레임 ID | 용도 | 수집기 |
|-----------|-----------|------|--------|
| 매출분석 > 시간대별 매출 | STAJ001_M0 | 판매 데이터 수집 | SalesCollector |
| 발주 > 단품별 발주 | STBJ030_M0 | 미입고/행사 조회, 발주 실행 | OrderPrepCollector, OrderExecutor |
| 발주 > 카테고리 발주 | STBJ010_M0 | 카테고리별 발주 | (미사용) |
| 검수전표 > 센터매입 조회 | (별도) | 입고 데이터 수집 | ReceivingCollector |

## 공통 접근 경로

```javascript
const app = nexacro.getApplication();
// 모든 화면의 공통 경로
const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID};
const form = frame.form;
```

---

## 단품별 발주 화면 (STBJ030_M0)

### 접근 경로

```javascript
const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STBJ030_M0.form;
const wf = form.div_workForm.form.div_work_01.form;
```

### 데이터셋

| 데이터셋 | 접근 | 용도 |
|----------|------|------|
| dsItem | `wf.dsItem` | 상품 정보 (코드, 명칭, 현재고, 입수, 유통기한) |
| dsOrderSale | `wf.dsOrderSale` | 날짜별 발주/입고/판매/폐기 이력 |
| dsWeek | `wf.dsWeek` | 주간 날짜 목록 (ORD_YMD) |
| gdList 바인딩 | `wf.gdList._binddataset` | 그리드에 표시되는 데이터 |

### dsItem 컬럼

| 컬럼명 | 설명 | 예시 |
|--------|------|------|
| ITEM_CD | 상품코드 | 8801234567890 |
| ITEM_NM | 상품명 | CU)우유900ml |
| NOW_QTY | 현재재고 | 5 |
| ORD_UNIT_QTY | 입수 (1배수당 개수) | 1 |
| EXPIRE_DAY | 유통기한 (일) | 3 |

### dsOrderSale 컬럼

| 컬럼명 | 설명 |
|--------|------|
| ORD_YMD | 날짜 (YYYY-MM-DD) |
| ITEM_CD | 상품코드 |
| ORD_QTY | 발주수량 (배수 단위) |
| BUY_QTY | 입고수량 (실제 개수) |
| SALE_QTY | 판매수량 |
| DISUSE_QTY | 폐기수량 |

> **주의**: `ORD_QTY`는 배수, `BUY_QTY`는 실제 개수
> 미입고 개수 = (`ORD_QTY` x 입수) - `BUY_QTY`

### 그리드 컬럼 (gdList)

| 인덱스 | 컬럼명 | 설명 | 비고 |
|--------|--------|------|------|
| 0 | (체크박스) | 선택 | |
| 1 | ITEM_CD / PLU_CD | 상품코드 | 입력 셀 |
| 2 | ITEM_NM | 상품명 | |
| 5 | EXPIRE_DAY | 유통기한 | |
| 6 | NOW_QTY | 현재고 | |
| 7 | ORD_UNIT_QTY | 입수 | |
| 11 | (당월행사) | 1+1, 2+1, 빈값 | `ds.getColID(11)` |
| 12 | (익월행사) | 1+1, 2+1, 빈값 | `ds.getColID(12)` |

### 상품 상태 플래그 (gdList 바인드 데이터셋)

상품명 셀(body[1])에 다음 cssclass 표현식 적용:
```javascript
expr:(CUT_ITEM_YN == '1') ? 'darkcyanColor' : (CT_ITEM_YN == '1') ? 'grid_colorBlue' : ''
```

| 컬럼명 | 타입 | 값 | 의미 | 화면 색상 |
|--------|------|:--:|------|----------|
| `CUT_ITEM_YN` | STRING | `'1'` | 발주중지 상품 | darkcyanColor (하늘색) |
| `CT_ITEM_YN` | STRING | `'1'` | 중점상품 | grid_colorBlue (파란색) |
| 둘 다 `'0'` | | | 일반 상품 | 검정색 (기본) |

**관련 보조 필드:**

| 컬럼명 | 설명 |
|--------|------|
| `PITEM_ID` | 중점상품 ID |
| `PITEM_ID_NM` | 중점상품 분류명 (예: "신상품") |
| `PITEM_ID_CSS` | 중점상품 CSS 클래스 |
| `STOP_PLAN_YN` | 발주중지 예정 여부 |
| `STOP_PLAN_YMD` | 발주중지 예정일 |
| `STOP_PLAN_CSS` | 발주중지 예정 CSS 클래스 |

**조회 코드:**
```javascript
if (wf.gdList && wf.gdList._binddataset) {
    const ds = wf.gdList._binddataset;
    const lastRow = ds.getRowCount() - 1;
    if (lastRow >= 0) {
        const cutItemYn = ds.getColumn(lastRow, 'CUT_ITEM_YN') || '0';
        const ctItemYn = ds.getColumn(lastRow, 'CT_ITEM_YN') || '0';
    }
}
```

> 검증 완료 (2026-01-30):
> - 나랑드사이다P500ml (8801097235014): `CUT_ITEM_YN=1` → 발주중지 OK
> - 한입두바이쫀득찰떡 (8807999035424): `CT_ITEM_YN=1` → 중점상품 OK
> - 컵누들우동소컵 (8801045571867): 둘 다 `0` → 일반 OK

### 행사 정보 조회 코드

```javascript
// gdList 그리드에서 행사 정보 조회
if (wf.gdList && wf.gdList._binddataset) {
    const ds = wf.gdList._binddataset;
    const lastRow = ds.getRowCount() - 1;
    if (lastRow >= 0) {
        const currentMonthPromo = ds.getColumn(lastRow, ds.getColID(11)) || '';
        const nextMonthPromo = ds.getColumn(lastRow, ds.getColID(12)) || '';
    }
}
```

### 상품코드 입력 → 조회 흐름

```
1. 마지막 행 셀 활성화 (cell_X_1 더블클릭)
2. 상품검색 팝업 닫기
3. input에 포커스
4. ActionChains: Ctrl+A → Delete → 상품코드 입력
5. Enter 키로 검색
6. 1.5초 대기 (데이터 로딩)
7. Alert 처리 (없는 상품/불가 등)
8. dsItem, dsOrderSale, gdList 데이터 조회
```

### 날짜 선택 팝업

```
- 화면 진입 시 날짜 선택 팝업 자동 표시
- fn_initBalju 내 grd_Result 그리드에서 행 더블클릭
- Button44 = "선택" 버튼
- 더블클릭으로 날짜 선택 후 "선택" 버튼 클릭
```

---

## 시간대별 매출 화면 (STAJ001_M0)

### 접근 경로

```javascript
const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STAJ001_M0.form;
```

### 데이터셋

| 데이터셋 | 용도 |
|----------|------|
| dsList | 판매 데이터 (중분류별 상품 목록) |

### 주요 컬럼

| 컬럼명 | 설명 |
|--------|------|
| ITEM_CD | 상품코드 |
| ITEM_NM | 상품명 |
| MID_CD | 중분류코드 |
| MID_NM | 중분류명 |
| SALE_QTY | 판매수량 |
| ORD_QTY | 발주수량 |
| BUY_QTY | 입고수량 |
| DISUSE_QTY | 폐기수량 |
| STOCK_QTY | 재고수량 |

---

## DOM 요소 ID 패턴

### 유통기한 읽기 (상품 상세 팝업)

```javascript
// CallItemDetailPopup 내 stExpireNm 요소
document.querySelector('[id*="CallItemDetailPopup"][id*="stExpireNm"][id*=":text"]');
```

### 탭 닫기 버튼

```javascript
// 화면별 닫기 버튼
document.querySelector('[id*="{FRAME_ID}"][id*="btn_topClose"]');
```

### 메뉴 텍스트 요소

```javascript
// 상단 메뉴 텍스트
document.querySelectorAll('[id*="div_topMenu"] [id*=":icontext"], [id*="div_topMenu"] [id*=":text"]');

// 서브 메뉴 텍스트
document.querySelectorAll('[id*="pdiv_topMenu"] [id*=":text"]');
```
