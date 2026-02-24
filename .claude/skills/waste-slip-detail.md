# 폐기 전표 상세 품목 추출 스킬

## When to Use

- 통합 전표 조회에서 전표별 상세 품목(item-level) 데이터를 추출할 때
- 폐기 전표의 개별 상품코드, 수량, 원가, 매가 정보가 필요할 때
- 넥사크로 팝업(STGJ020_P1) 내 데이터셋에 접근해야 할 때
- WasteSlipCollector의 헤더 수집 이후 상세 데이터를 보강할 때
- dsGsTmp 캐싱 문제로 팝업 데이터가 반복되는 문제를 해결할 때

## Common Pitfalls

- :x: 그리드 더블클릭으로 팝업 열기 시도 -> ActionChains 더블클릭은 nexacro 팝업을 트리거하지 못함
- :white_check_mark: `gfn_openPopup()` JavaScript 직접 호출로 팝업 열기

- :x: 팝업을 열고 바로 데이터 추출 -> 첫 번째 전표 데이터만 반복 반환
- :white_check_mark: 팝업 오픈 후 dsGsTmp 강제 갱신 + fn_selSearch() 재호출

- :x: 팝업 close()만 호출하고 새로 열기 -> 팝업 프레임이 재사용되어 dsGsTmp 캐싱됨
- :white_check_mark: close() + destroy() 후 새로 열기, 그래도 dsGsTmp 수동 갱신 필수

- :x: `fn_moveDetailPage()` 직접 호출 -> 이벤트는 발생하지만 팝업이 열리지 않음 (headless 환경)
- :white_check_mark: dsGs 설정 후 `gfn_openPopup()` 직접 호출

- :x: dsListType0 데이터셋만 확인 -> 폐기 품목은 dsListType1에 있음
- :white_check_mark: dsListType0~4 전체 순회하여 데이터 추출

- :x: oncelldblclick 핸들러를 `_userhandler` 속성으로 조회 -> null 반환
- :white_check_mark: `wf.gdList_oncelldblclick` 이름 패턴으로 직접 함수 찾기

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| 모든 전표에서 동일한 품목 데이터 반환 | dsGsTmp가 첫 번째 전표값으로 고정 | 팝업 오픈 후 dsGsTmp.setColumn()으로 gvVar 갱신 + fn_selSearch() 재호출 |
| 팝업이 열리지 않음 | gfn_openPopup 호출 전 dsGs 미설정 | dsList.set_rowposition() + dsGs gvVar 설정 후 gfn_openPopup 호출 |
| nexacro.getPopupFrames()가 빈 배열 | 팝업 열기 후 대기 부족 | time.sleep(3) 이상 대기 |
| dsListType1이 비어있음 | fn_selSearch 서버 응답 미도착 | fn_selSearch() 후 time.sleep(3) 대기 |
| items에 error 키가 있음 | 팝업 form 접근 실패 | popupframes 배열의 마지막 요소 사용 (popupframes[popupframes.length-1]) |
| gvVar07이 빈 문자열 | dsGsTmp 갱신 시 잘못된 값 전달 | Python f-string에서 chit_no 변수가 올바른지 확인 |

---

## 화면 구조

### 통합 전표 조회 (STGJ020_M0)

```
프레임: STGJ020_M0
메뉴: 검수전표 > 통합 전표 조회
전표구분 폐기 CODE: "10"
```

### 상세 팝업 (STGJ020_P1)

```
fn_moveDetailPage() -> gfn_openPopup() 호출
  - CHIT_ID = "04" (폐기) -> STGJ020_P1.xfdl
  - CHIT_ID = "09"/"10"   -> STGJ020_P2.xfdl
```

### 팝업 데이터 흐름

```
1. 부모 화면: dsList에서 행 선택 -> dsGs에 gvVar 파라미터 설정
2. gfn_openPopup("STGJ020_P1", "GJ::STGJ020_P1.xfdl", oArg) 호출
3. 팝업 fn_afterFormOnload():
   - dsGsTmp.copyData(this.getOwnerFrame().dsArg)  <- 여기서 dsGs 복사
   - fn_setGridFormat()
   - fn_setCommonCode()
   - fn_setComponentValue()
   - fn_selSearch()  <- 서버 트랜잭션 호출 (품목 데이터 로딩)
4. 서버 응답 -> dsListType0~4에 품목 데이터 저장
```

### gvVar 매핑 (dsGs / dsGsTmp)

| gvVar | 컬럼 | 설명 | 예시 |
|-------|------|------|------|
| gvVar04 | CHIT_ID | 전표구분 코드 | "04" (폐기) |
| gvVar05 | CHIT_ID_NM | 전표구분명 | "폐기" |
| gvVar06 | NAP_PLAN_YMD | 납품예정일 | "20260218" |
| gvVar07 | CHIT_NO | 전표번호 | "10044420011" |
| gvVar08 | CENTER_NM | 배송센터명 | "CU물류" |
| gvVar09 | CHIT_ID_NO | 전표구분번호 | "10044420011" |
| gvVar10 | LSTORE_NM | 좌측매장명 | "" |
| gvVar11 | RSTORE_NM | 우측매장명 | "" |
| gvVar12 | LARGE_CD | 대분류코드 | "" |
| gvVar13 | CHIT_FLAG | 전표상태 | "1" |
| gvVar14 | RET_CHIT_NO | 반품전표번호 | "" |
| gvVar15 | MAEIP_CHIT_NO | 매입전표번호 | "" |
| gvVar18 | CHIT_YMD | 전표일자 | "20260218" |
| gvVar20 | - | "Y" 고정 | "Y" |

### 품목 데이터셋 (dsListType1)

| 컬럼 | 설명 | 예시 |
|------|------|------|
| CHIT_NO | 전표번호 | "10044420011" |
| CHIT_SEQ | 전표순번 | 1, 2, 3... |
| ITEM_CD | 상품코드 | "8801234567890" |
| ITEM_NM | 상품명 | "뉴전주비빔소불고기" |
| LARGE_CD | 대분류코드 | "001" |
| LARGE_NM | 대분류명 | "도시락" |
| QTY | 수량 | 1 |
| WONGA_PRICE | 원가단가 | 3089 |
| WONGA_AMT | 원가합계 | 3089 |
| MAEGA_PRICE | 매가단가 | 5500 |
| MAEGA_AMT | 매가합계 | 5500 |
| CUST_NM | 거래처명 | "OO식품" |
| CENTER_NM | 배송센터명 | "CU물류" |

---

## 핵심 구현 패턴

### 전체 흐름 (5단계)

```python
for idx in range(len(slip_list)):
    slip = slip_list[idx]
    # 1) dsGs 파라미터 설정
    # 2) 기존 팝업 닫기 (close + destroy)
    # 3) 팝업 열기 (gfn_openPopup)
    # 4) dsGsTmp 강제 갱신 + fn_selSearch 재호출
    # 5) 데이터 추출 (dsListType0~4)
```

### 1단계: dsGs 파라미터 설정

```javascript
// f-string으로 Python 변수 주입 ({{ }} 이스케이프 필수)
var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
var wf = form.div_workForm.form;
wf.dsList.set_rowposition({idx});

var fields = ['CHIT_ID','CHIT_ID_NM','NAP_PLAN_YMD','CHIT_NO','CENTER_NM',
              'CHIT_ID_NO','LSTORE_NM','RSTORE_NM','LARGE_CD','CHIT_FLAG',
              'RET_CHIT_NO','MAEIP_CHIT_NO','CHIT_YMD'];
var gvNums = ['04','05','06','07','08','09','10','11','12','13','14','15','18'];

for (var i = 0; i < fields.length; i++) {
    var val = String(wf.dsList.getColumn(idx, fields[i]) || '');
    wf['gvVar' + gvNums[i]] = val;
    wf.dsGs.setColumn(0, 'gvVar' + gvNums[i], val);
}
wf.gvVar20 = "Y";
```

### 2단계: 기존 팝업 닫기

```javascript
var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
if (popupframes) {
    for (var i = popupframes.length - 1; i >= 0; i--) {
        try { popupframes[i].close(); popupframes[i].destroy(); } catch(e) {}
    }
}
```

### 3단계: 팝업 열기

```javascript
var form = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet['{FRAME_ID}'].form;
var wf = form.div_workForm.form;
var oArg = {};
oArg.dsArg = wf.dsGs;
oArg.strStoreCd = wf.strStoreCd;
oArg.strStoreNm = wf.strStoreNm;
wf.gfn_openPopup("STGJ020_P1", "GJ::STGJ020_P1.xfdl", oArg, "fn_popupCallback", {});
```

> time.sleep(3) 필수 - 팝업 렌더링 + fn_afterFormOnload 실행 대기

### 4단계: dsGsTmp 강제 갱신 + fn_selSearch (핵심!)

```javascript
var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
if (!popupframes || popupframes.length === 0) return;
var popup = popupframes[popupframes.length - 1];
var pf = popup.form;

// dsGsTmp 갱신 (현재 전표의 값으로)
pf.dsGsTmp.setColumn(0, 'gvVar04', '{chit_id}');
pf.dsGsTmp.setColumn(0, 'gvVar05', '{chit_id_nm}');
pf.dsGsTmp.setColumn(0, 'gvVar06', '{nap_ymd}');
pf.dsGsTmp.setColumn(0, 'gvVar07', '{chit_no}');      // <- 핵심: 전표번호
pf.dsGsTmp.setColumn(0, 'gvVar08', '{center_nm}');
pf.dsGsTmp.setColumn(0, 'gvVar09', '{chit_id_no}');
pf.dsGsTmp.setColumn(0, 'gvVar13', '{chit_flag}');
pf.dsGsTmp.setColumn(0, 'gvVar18', '{ymd}');           // <- 핵심: 전표일자

// 이전 데이터 클리어
for (var t = 0; t <= 4; t++) {
    var ds = pf['dsListType' + t];
    if (ds && ds.clearData) ds.clearData();
}

// 서버 트랜잭션 재호출
if (typeof pf.fn_selSearch === 'function') {
    pf.fn_selSearch();
}
```

> time.sleep(3) 필수 - 서버 응답 대기

### 5단계: 데이터 추출

```javascript
var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
var popup = popupframes[popupframes.length - 1];
var pf = popup.form;
var result = {items: []};

// dsGsTmp gvVar07 확인 (디버깅용)
result.dsGsTmp_gvVar07 = String(pf.dsGsTmp.getColumn(0, 'gvVar07') || '');

// dsListType0~4 순회
for (var t = 0; t <= 4; t++) {
    var ds = pf['dsListType' + t];
    if (ds && ds.getRowCount && ds.getRowCount() > 0) {
        var cc = ds.getColCount();
        var cols = [];
        for (var c = 0; c < cc; c++) cols.push(ds.getColID(c));

        for (var r = 0; r < ds.getRowCount(); r++) {
            var row = {_dsType: t};
            for (var c = 0; c < cols.length; c++) {
                var val = ds.getColumn(r, cols[c]);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                row[cols[c]] = val;
            }
            result.items.push(row);
        }
    }
}
return result;
```

---

## 팝업 접근 패턴

### nexacro.getPopupFrames()

```javascript
// 팝업 프레임 배열 가져오기
var popupframes = nexacro.getPopupFrames(nexacro.getApplication().mainframe);
// 마지막 열린 팝업 (가장 최근)
var popup = popupframes[popupframes.length - 1];
var popupForm = popup.form;
```

### 팝업 내 데이터셋 목록

| 데이터셋 | 용도 |
|----------|------|
| dsGsTmp | 부모에서 전달받은 파라미터 (gvVar04~18) |
| dsSearch | 서버 트랜잭션 검색 조건 |
| dsListType0 | 품목 데이터 (타입 0) |
| dsListType1 | 품목 데이터 (타입 1) - 폐기 전표 품목은 여기 |
| dsListType2~4 | 품목 데이터 (기타 타입) |
| dsRetReason | 반품 사유 코드 |

### dsGsTmp 캐싱 문제 (Critical)

```
[문제]
fn_afterFormOnload()의 dsGsTmp.copyData(dsArg)는 팝업 최초 생성 시에만 실행됨.
이후 팝업을 닫았다 다시 열면 dsGsTmp가 갱신되지 않고 첫 번째 전표 데이터로 고정됨.

[원인]
nexacro 팝업은 close() 후에도 프레임이 메모리에 남아있어
새로 열 때 fn_afterFormOnload()가 재실행되지 않을 수 있음.

[해결]
팝업 열기 후 수동으로:
  1. dsGsTmp.setColumn() 으로 gvVar 값 갱신
  2. dsListType0~4 clearData()
  3. fn_selSearch() 재호출
```

---

## 실전 검증 결과

### 2026-02-18~19 폐기 전표 7건, 품목 10건

```
=== 2026-02-18 === 7건 | 원가 19,071원 | 매가 33,400원
  1. [8809899722116] 뉴전주비빔소불고기    x1 원가=3,089 매가=5,500 (도시락)
  2. [8801062895847] 통통이오란다쿠키슈     x1 원가=1,456 매가=2,500 (빵/디저트류)
  3. [8809899712100] 설향딸기크림빵         x1 원가=1,300 매가=2,500 (빵/디저트류)
  4. [8801155728830] 복숭아요거트300        x2 원가=3,836 매가=5,800 (유음료)
  5. [8801127000093] 초당순두부             x1 원가=2,392 매가=3,500 (반찬/델리)
  6. [8809899722215] 레드벨벳생크림빵       x1 원가=1,950 매가=3,600 (빵/디저트류)
  7. [8809899722239] 딸기생크림빵           x1 원가=1,950 매가=3,600 (빵/디저트류)

=== 2026-02-19 === 3건 | 원가 8,120원 | 매가 14,300원
  1. [8809899722161] 정성가득9첩한상        x1 원가=3,539 매가=6,500 (도시락)
  2. [8809899712070] 압도적제육김밥         x1 원가=2,400 매가=4,500 (김밥/주먹밥)
  3. [8801127071079] 비기오망고             x1 원가=2,181 매가=3,300 (유음료)

총 10건 | 원가 27,191원 | 매가 47,700원
```

---

## 관련 파일

| 파일 | 역할 |
|------|------|
| `scripts/fetch_waste_detail_v5.py` | 상세 품목 추출 검증 스크립트 (최종 동작 버전) |
| `src/collectors/waste_slip_collector.py` | 폐기 전표 헤더 수집기 (상세 품목 미포함, 보강 대상) |
| `src/infrastructure/database/repos/waste_slip_repo.py` | 폐기 전표 DB Repository |
| `src/application/services/waste_verification_service.py` | 폐기 검증 서비스 |
| `src/settings/ui_config.py` | FRAME_IDS["WASTE_SLIP"]="STGJ020_M0" 설정 |
| `src/utils/nexacro_helpers.py` | navigate_menu() 유틸리티 |

## 향후 통합 대상

WasteSlipCollector에 `_collect_detail_items()` 메서드 추가하여
헤더 수집 후 각 전표의 팝업을 열어 상세 품목까지 자동 수집하도록 확장 필요:

```python
def _collect_detail_items(self, slip_list: List[Dict]) -> List[Dict]:
    """각 전표의 상세 품목 데이터 수집 (팝업 기반)"""
    all_items = []
    for idx, slip in enumerate(slip_list):
        # 1) dsGs 설정
        # 2) 기존 팝업 닫기
        # 3) gfn_openPopup("STGJ020_P1", ...)
        # 4) dsGsTmp 강제 갱신 + fn_selSearch()
        # 5) dsListType0~4에서 품목 추출
        items = self._extract_popup_items(slip, idx)
        all_items.extend(items)
    return all_items
```
