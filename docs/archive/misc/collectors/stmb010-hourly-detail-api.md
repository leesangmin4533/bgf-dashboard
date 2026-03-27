# STMB010 시간대별 매출 상세 (selPrdT3) 기술문서

> 작성일: 2026-03-07
> 범위: BGF STMB010 화면 구조, selPrdT3 Direct API, 데이터 수집/정합성
> 관련 코드: `src/collectors/hourly_sales_detail_collector.py`, `src/application/services/hourly_detail_service.py`

---

## 1. 화면 구조

### 1.1 프레임 경로

```
nexacro.getApplication()
  .mainframe.HFrameSet00.VFrameSet00.FrameSet
    .STMB010_M0                              ← 메인 프레임
      .form.div_workForm.form                ← workForm (wf)
        .dsListMain                          ← 24행 Dataset (시간대별 매출 요약)
        .div2.form.gdList                    ← 오전 그리드 (0~11시)
        .div2.form.gdList2                   ← 오후 그리드 (12~23시)
      .STMB010_P0                            ← 팝업 프레임
        .form.div_tab_btn_03_onclick()       ← 상품구성비 탭 핸들러
```

**주의**: 그리드가 `div2` 내부에 있음. `wf.gdList`가 아니라 `wf.div2.form.gdList`.

### 1.2 Dataset 구조

| Dataset | 위치 | 내용 |
|---------|------|------|
| `dsListMain` | workForm | 24행(0~23시), 컬럼: HMS, SALE_QTY, RECT_AMT 등 |
| `dsList` | 팝업/API 응답 | 품목별 상세, 컬럼: ITEM_CD, ITEM_NM, SALE_QTY 등 |

### 1.3 더블클릭 핸들러

```javascript
// 올바른 방법: workForm 레벨 핸들러 직접 호출
wf.gdList_oncelldblclick(grid, evt);   // 0~11시
wf.gdList2_oncelldblclick(grid, evt);  // 12~23시

// 잘못된 방법 (작동 안 함):
grid.oncelldblclick.fireEvent(grid, evt);  // ✗
grid.set_currentrow(row);                   // ✗
```

**행 선택**: `ds.set_rowposition(targetRow)` 사용 (`grid.set_currentrow()` 아님)

---

## 2. API 사양

### 2.1 selPrdT3 vs selDay 비교

| 항목 | selDay (시간대 요약) | selPrdT3 (품목 상세) |
|------|---------------------|---------------------|
| 엔드포인트 | `/stmb010/selDay` | `/stmb010/selPrdT3` |
| 날짜 파라미터 | `calFromDay`, `calToDay` | `strYmd` |
| 시간 파라미터 | 없음 (전 시간 조회) | `strTime=XX` (2자리) |
| 추가 파라미터 | `strGubun`, `strSaleGubun` | `strPosNo=`, `strchkGubun=0` |
| 메뉴 ID | 없음 | `GV_MENU_ID=0001,STMB010_M0` |
| 채널 | 없음 | `GV_CHANNELTYPE=HOME` |
| Dataset 정의 | 불필요 | **필수** (body 끝에 첨부) |
| 응답 | dsListMain (24행) | dsList (품목 N행) |

### 2.2 selPrdT3 요청 body 구조 (실제 캡처, 763자)

```
SSV:utf-8
{세션변수들...}           ← selDay와 동일한 SS_* 변수
strYmd=20260307          ← 조회 날짜 (YYYYMMDD)
strPosNo=                ← POS번호 (빈값=전체)
strTime=10               ← 시간대 (00~23, 2자리)
strchkGubun=0            ← 구분값
strStoreCd=46513         ← 매장코드
GV_MENU_ID=0001,STMB010_M0
GV_USERFLAG=HOME
GV_CHANNELTYPE=HOME
Dataset:dsList␟_RowType_␟ITEM_CD:STRING(256)␟ITEM_NM:STRING(256)␟...
```

> `␟` = Unit Separator (0x1F), `␞` = Record Separator (0x1E)

### 2.3 Dataset 정의 (body 끝에 필수 첨부)

```
Dataset:dsList␟_RowType_␟
ITEM_CD:STRING(256)␟
ITEM_NM:STRING(256)␟
SALE_QTY:INT(256)␟
RECT_AMT:BIGDECIMAL(256)␟
RATE:BIGDECIMAL(256)␟
MONTH_EVT:STRING(256)␟
G_RECT_AMT:BIGDECIMAL(256)␟
ORD_ITEM:STRING(256)
```

### 2.4 응답 (SSV)

```
SSV:utf-8
Dataset:dsList
_RowType_␟ITEM_CD␟ITEM_NM␟SALE_QTY␟RECT_AMT␟RATE␟MONTH_EVT␟G_RECT_AMT␟ORD_ITEM
N␟8801858011024␟카스캔500ml␟4␟17600␟26.42␟2+1␟17600␟8801858011024
N␟8801045521077␟오뚜기)열라면␟2␟1800␟2.70␟␟1800␟8801045521077
...
```

| 컬럼 | 타입 | 설명 |
|------|------|------|
| ITEM_CD | STRING | 바코드 (13자리) |
| ITEM_NM | STRING | 상품명 |
| SALE_QTY | INT | 판매수량 |
| RECT_AMT | BIGDECIMAL | 매출금액 |
| RATE | BIGDECIMAL | 구성비(%) |
| MONTH_EVT | STRING | 행사 (1+1, 2+1 등) |
| G_RECT_AMT | BIGDECIMAL | 총매출금액 |
| ORD_ITEM | STRING | 발주상품코드 |

---

## 3. 템플릿 확보 전략

selPrdT3 호출에는 세션 변수가 포함된 body가 필요. 3단계 전략:

### 3.1 1차: UI 흐름으로 캡처

```
XHR 인터셉터 설치
  → STMB010 메뉴 진입
  → dsListMain 로딩 대기 (getRowCount() > 0)
  → 00시 더블클릭 (ds.set_rowposition + wf.gdList_oncelldblclick)
  → 팝업(STMB010_P0) 확인
  → 상품구성비 탭 클릭 (div_tab_btn_03_onclick)
  → XHR 캡처 확인 (url에 'selPrdT3' 포함)
  → 팝업 닫기
```

### 3.2 2차: selDay body → selPrdT3 body 변환

selDay body에서 세션 변수를 추출하고 비즈니스 파라미터만 교체:

```python
# 제거할 selDay 전용 파라미터
STRIP = ['strGubun=', 'strSaleGubun=', 'calFromDay=', 'calToDay=', 'strPreStoreCd=']

# 추가할 selPrdT3 파라미터
ADD = ['strYmd=YYYYMMDD', 'strPosNo=', 'strTime=00',
       'strchkGubun=0', 'strStoreCd=XXXXX',
       'GV_MENU_ID=0001,STMB010_M0', 'GV_USERFLAG=HOME',
       'GV_CHANNELTYPE=HOME', PRDT3_DS_DEFINITION]
```

### 3.3 3차: JavaScript로 직접 구성

쿠키/세션 변수에서 직접 body 빌드 (최후 폴백).

---

## 4. 파라미터 치환

```python
def _replace_params(template, date_str, hour):
    body = template
    body = re.sub(r'(strYmd=)\d{8}', rf'\g<1>{date_str}', body)
    body = re.sub(r'(calFromDay=)\d{8}', rf'\g<1>{date_str}', body)
    body = re.sub(r'(calToDay=)\d{8}', rf'\g<1>{date_str}', body)
    body = re.sub(r'(strTime=)\d{2}', rf'\g<1>{hour:02d}', body)
    return body
```

**주의**: `strTime`이 핵심. `strHms`는 selPrdT3에서 사용하지 않음.

---

## 5. 수집 아키텍처

### 5.1 컴포넌트 구조

```
HourlyDetailService (서비스 레이어)
  ├── HourlySalesDetailCollector (수집)
  │     ├── collect_hour(date, hour)    → 단일 시간대 수집
  │     ├── collect_all_hours(date)     → 24시간 전체 수집
  │     ├── _ensure_template_prdt3()   → 템플릿 확보 (3단계)
  │     ├── _replace_params()          → 파라미터 치환
  │     └── _parse_prdt3_response()    → SSV 파싱
  ├── HourlySalesDetailRepository (저장)
  │     ├── save_detail(date, hour, items)
  │     ├── save_raw(date, hour, ssv)
  │     └── get_collected_hours(date)
  └── backfill(days)                   → 과거 일괄 수집
```

### 5.2 수집 흐름

```
1. 템플릿 확보 (1회)
2. 날짜별 루프:
   a. preflight: selDay 호출 (서버 상태 워밍업)
   b. 시간대별 루프 (0~23시):
      - 이미 수집된 시간 스킵
      - _replace_params(template, date, hour)
      - fetch(DETAIL_ENDPOINT, body) → SSV 응답
      - parse → save_detail + save_raw
   c. 2초 대기 (날짜 간)
```

### 5.3 성공/실패 판정

```python
result = collector.collect_hour(date, hour)

if isinstance(result, tuple):
    items, ssv = result    # 성공 (빈 리스트도 성공 — 해당 시간 매출 0)
else:
    # result = []  → API 실패
```

**핵심**: `items=[]`(빈 리스트)는 "해당 시간에 매출 없음"이므로 **성공**. `if items:` 체크 금지.

---

## 6. DB 스키마

### hourly_sales_detail (stores/{store_id}.db)

```sql
CREATE TABLE hourly_sales_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sales_date TEXT NOT NULL,     -- YYYY-MM-DD
    hour INTEGER NOT NULL,        -- 0~23
    item_cd TEXT NOT NULL,        -- 바코드
    item_nm TEXT,                 -- 상품명
    sale_qty INTEGER DEFAULT 0,   -- 판매수량
    sale_amt REAL DEFAULT 0,      -- 판매금액 (G_RECT_AMT)
    receipt_amt REAL DEFAULT 0,   -- 매출금액 (RECT_AMT)
    rate REAL DEFAULT 0,          -- 구성비(%)
    month_evt TEXT DEFAULT '',    -- 행사
    ord_item TEXT DEFAULT '',     -- 발주상품코드
    collected_at TEXT NOT NULL,   -- 수집시각
    UNIQUE(sales_date, hour, item_cd)
);
```

### raw_hourly_sales_detail (stores/{store_id}.db)

```sql
CREATE TABLE raw_hourly_sales_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sales_date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    ssv_response TEXT,           -- 원본 SSV 응답 전문
    collected_at TEXT NOT NULL,
    UNIQUE(sales_date, hour)
);
```

---

## 7. 데이터 정합성

### 7.1 daily_sales와의 관계

| 항목 | hourly_sales_detail (HSD) | daily_sales (DS) |
|------|--------------------------|------------------|
| 수집 소스 | STMB010 상품구성비 탭 | STMB010 일별 매출 |
| 단위 | 시간대×상품 | 일×상품 |
| 바코드 | **묶음 바코드 포함** | **낱개 바코드만** |
| 커버리지 | 시간대별 상위 매출 품목 | 전체 품목 |

### 7.2 알려진 차이 원인

| 원인 | 설명 | 처리 |
|------|------|------|
| **묶음 바코드** | 카스캔500ml*4, 신라면5입 등 | 낱개 바코드로 변환 + 수량×배수 |
| **보루(카톤)** | 담배 보루 = 10갑 묶음 | 낱개 바코드로 변환 + 수량×10 |
| **공병 반납** | 주류공병100/130 (음수 수량) | **삭제** |
| **재활용봉투** | 친환경봉투판매용, 배달전용봉투 | **삭제** |
| **상위 품목 한정** | BGF가 시간대별 상위 매출만 노출 | 포착률 ~96% (정상 한계) |

### 7.3 묶음→낱개 변환 패턴

```python
# 상품명에서 배수 파싱
'카스캔500ml*4'        → multiplier=4, base='카스캔500ml'
'신라면5입'             → multiplier=5, base='신라면'
'더원블루1mg보루'        → multiplier=10, base='더원블루1mg'
'하이네켄캔500m'        → multiplier=1, base='하이네켄캔500ml' (잘린 이름 복원)
```

매칭 전략:
1. **정확 매칭**: products.item_nm == base_name
2. **퍼지 매칭**: 브랜드 접두사(농심/오뚜기/삼양 등) 제거 후 포함 비교

### 7.4 검증 수치 (2026-03-07 기준, 46513 매장)

| 지표 | 값 |
|------|---|
| 총 레코드 | 74,868건 |
| 수집 기간 | 2025-09-07 ~ 2026-03-06 (181일) |
| 고유 상품 | 3,561개 |
| **전체 포착률** | **96.2%** |
| **일별 일치율** | **99.0~100.0%** |

카테고리별 포착률 (2월 기준):

| 카테고리 | 포착률 |
|----------|--------|
| 신선식품 | 101.3% |
| 스낵/라면 | 100.7% |
| 담배 | 101.5% |
| 주류 | 101.3% |
| 음료 | 102.0% |
| 생활용품 | 80.1% ⚠️ |
| 서비스 | 41.0% ⚠️ |

> 생활용품/서비스는 다품종 소량판매 특성상 시간대별 상위 품목에서 누락되는 비율이 높음.

---

## 8. 실행 방법

### 8.1 일상 수집 (스케줄러)

```python
# run_scheduler.py에서 자동 등록
# 매시 10분: 직전 시간대 수집
# 55분마다: heartbeat (세션 유지)
```

### 8.2 과거 백필

```bash
# 6개월 백필 (기본)
python scripts/backfill_hourly_detail.py --store 46513

# 30일만
python scripts/backfill_hourly_detail.py --store 46513 --days 30

# 특정 기간
python scripts/backfill_hourly_detail.py --start-date 2026-01-01 --end-date 2026-01-31
```

### 8.3 CLI (run_scheduler.py)

```bash
python run_scheduler.py --backfill-hourly-detail 180 --store 46513
```

---

## 9. 트러블슈팅

### 9.1 "성공 0/24, 실패 24" — 빈 응답도 실패로 카운트

**원인**: `if items:` 체크 — 빈 리스트는 falsy
**수정**: `isinstance(result, tuple)` 로 성공 판정

### 9.2 selPrdT3 호출 시 빈 응답만 반환

**체크리스트**:
1. `strTime` 사용 여부 (`strHms` 아님)
2. body 끝에 Dataset 정의 포함 여부
3. `GV_MENU_ID=0001,STMB010_M0` 포함 여부
4. `GV_CHANNELTYPE=HOME` 포함 여부

### 9.3 더블클릭이 작동 안 함

**체크리스트**:
1. 그리드 경로: `wf.div2.form.gdList` (NOT `wf.gdList`)
2. 핸들러: `wf.gdList_oncelldblclick(grid, evt)` (NOT `grid.oncelldblclick.fireEvent`)
3. 행 선택: `ds.set_rowposition(row)` (NOT `grid.set_currentrow(row)`)

### 9.4 backfill이 DB에 저장 안 됨

**원인**: collector.backfill()은 repository 접근 불가
**수정**: service.backfill() → service.collect_all_hours() 루프 (repository 포함)

### 9.5 포착률 100% 초과

**원인**: 묶음 바코드(카스캔*4 등)가 HSD에 별도 집계
**수정**: 묶음→낱개 변환 + 수량×배수, 미매칭 잔여 삭제

---

## 10. 넥사크로 STMB010 메뉴 진입 방법

```javascript
// DOM MouseEvent로 서브메뉴 클릭
var el = document.querySelector(
    '#mainframe_HFrameSet00_VFrameSet00_TopFrame_form_div_menu_form_grd_menuAll_body_gridrow_4_cell_4_1_controltreeTextBoxElement'
);
var evt = new MouseEvent('mousedown', {bubbles: true, cancelable: true});
el.dispatchEvent(evt);
```

> 일반 `.click()`은 넥사크로에서 무시됨. 반드시 `MouseEvent('mousedown')` 사용.
