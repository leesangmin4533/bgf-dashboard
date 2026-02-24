# 넥사크로 웹 스크래핑 스킬

## When to Use

- 넥사크로(Nexacro) 기반 웹사이트에서 데이터를 스크래핑할 때
- 일반 Selenium 선택자(CSS, XPath)가 작동하지 않을 때
- BGF 스토어 시스템에서 판매/재고/발주 데이터를 수집할 때
- 넥사크로 그리드(grid), 데이터셋(dataset), 팝업 등을 조작할 때

## Common Pitfalls

- ❌ `find_element_by_css_selector()` 사용 → 넥사크로 DOM은 동적 생성되어 작동 안함
- ✅ `driver.execute_script()`로 `nexacro.getApplication()` 직접 접근

- ❌ Python f-string 안에서 JavaScript 중괄호 `{}` 그대로 사용 → SyntaxError
- ✅ f-string 내 JavaScript에서 `{{`, `}}`로 이스케이프

- ❌ 데이터 로딩 대기 없이 바로 값 조회 → `undefined` 또는 빈 값
- ✅ `time.sleep(1~2)` 후 데이터셋 조회

- ❌ `offsetParent` 체크 없이 요소 클릭 → 숨겨진 요소에 이벤트 발생
- ✅ `el.offsetParent !== null` 으로 가시성 확인 후 클릭

- ❌ nexacro 데이터셋에서 큰 숫자 값을 바로 사용 → `{hi, lo}` 객체 반환됨
- ✅ `getVal()` 헬퍼로 `val.hi` 변환 처리

- ❌ **JavaScript로 팝업 닫기** → 프레임 구조 문제로 잘못된 버튼 클릭하거나 찾지 못함
- ✅ **Selenium XPath로 팝업 닫기** → 프레임 무관하게 작동, 자동 재시도

- ❌ 전역 텍스트 검색으로 "닫기" 버튼 찾기 → 다른 프레임의 버튼 클릭
- ✅ `is_displayed()` 체크 + 여러 XPath 패턴으로 정확한 버튼 찾기

- ❌ 팝업 처리 없이 데이터 수집 → 1~2개만 수집되고 중단
- ✅ 화면 이동 직후 팝업 닫기 → 전체 데이터 수집 가능

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| "Cannot read property of undefined" | 프레임 ID 오타 또는 화면 미로딩 | `bgf-screen-map.md`에서 정확한 FRAME_ID 확인, `time.sleep()` 추가 |
| Alert 창이 뜨면서 스크립트 멈춤 | 상품 없음/발주불가 알림 | `try: driver.switch_to.alert` 로 alert 처리 |
| 그리드 데이터가 비어있음 | 상품코드 입력 후 대기 부족 | Enter 후 `time.sleep(1.5)` 이상 대기 |
| 클릭 이벤트가 작동 안함 | 단순 `.click()` 사용 | `mousedown` → `mouseup` → `click` 순서로 디스패치 |
| 팝업이 닫히지 않음 (기본) | 상품검색 팝업 자동 생성 | `popupframe` / `PopupFrame` 모두 검색하여 닫기 |
| **팝업이 전혀 닫히지 않음 (고급)** | WorkFrame에 렌더링된 독립 팝업 | **Selenium XPath 방법 사용** (고급 팝업 처리 섹션 참조) |
| "element click intercepted" | 다른 요소가 팝업 버튼 가림 | Selenium XPath로 여러 요소 순회 시도 |
| 잘못된 프레임의 닫기 버튼 클릭 | 전역 검색으로 다른 화면 버튼 발견 | Selenium XPath + `is_displayed()` 체크 |
| 메뉴 이동 후 화면이 안 바뀜 | 이전 탭 미닫힘 | `close_menu()` 호출 후 새 메뉴 이동 |
| 입력값이 기존 값과 겹침 | 기존 값 미삭제 | `Ctrl+A → Delete` 후 새 값 입력 |
| 데이터 수집 시 1~2개만 수집됨 | 차단 팝업이 화면 가림 | 화면 이동 직후 팝업 닫기 (`close_popups_selenium()`) |

---

## 핵심 규칙

- 일반 Selenium 선택자(CSS, XPath) 사용 불가 - 넥사크로 DOM은 동적 생성
- 반드시 `driver.execute_script()`로 넥사크로 객체 직접 접근
- Alert 창은 `driver.switch_to.alert`로 처리
- 팝업/메뉴 전환 후 반드시 `time.sleep()` 대기
- f-string 내 JavaScript에서 중괄호는 `{{`, `}}`로 이스케이프

## 기본 접근 패턴

```javascript
const app = nexacro.getApplication();
const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{FRAME_ID}.form;
const workForm = form.div_workForm.form.div_work_01.form;
```

- `{FRAME_ID}`는 화면별 고유 ID (예: `STBJ030_M0`, `STAJ001_M0`)
- `bgf-screen-map.md` 참조

## 데이터셋 조회

```javascript
// 값 추출 헬퍼 (nexacro 특수 객체 처리 포함)
function getVal(ds, row, col) {
    let val = ds.getColumn(row, col);
    // nexacro는 큰 숫자를 {hi, lo} 객체로 반환할 수 있음
    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
    return val;
}

// 컬럼명으로 접근
getVal(wf.dsItem, 0, 'ITEM_CD');
getVal(wf.dsItem, 0, 'NOW_QTY');

// 컬럼 인덱스로 접근 (그리드 바인딩 데이터셋)
const ds = wf.gdList._binddataset;
ds.getColumn(row, ds.getColID(11));  // 인덱스 11번 컬럼

// 행 수 확인
ds.getRowCount();

// 현재 행 위치 설정
ds.set_rowposition(targetRow);
```

## 그리드 클릭 이벤트

```javascript
// 셀 요소 찾기
const cellId = 'cell_' + rowIdx + '_' + colIdx;
const cell = document.querySelector('[id*="gdList"][id*="' + cellId + '"]');

// 클릭 이벤트 디스패치
const r = cell.getBoundingClientRect();
const opts = {
    bubbles: true, cancelable: true, view: window,
    clientX: r.left + r.width/2, clientY: r.top + r.height/2
};
cell.dispatchEvent(new MouseEvent('mousedown', opts));
cell.dispatchEvent(new MouseEvent('mouseup', opts));
cell.dispatchEvent(new MouseEvent('click', opts));

// 더블클릭
cell.dispatchEvent(new MouseEvent('dblclick', {...opts, detail: 2}));
```

## 메뉴 이동

```javascript
// 상단 메뉴 클릭 (텍스트 기반 탐색)
const menuItems = document.querySelectorAll('[id*="div_topMenu"] [id*=":text"]');
for (const el of menuItems) {
    const text = (el.innerText || '').trim();
    if (text === '발주' && el.offsetParent !== null) {
        // 클릭 이벤트 디스패치
    }
}
```

## 입력 처리

```python
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# 기존 값 지우고 새 값 입력
actions = ActionChains(driver)
actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
actions.send_keys(Keys.DELETE)
actions.send_keys(item_cd)
actions.perform()
time.sleep(0.3)

# Enter 키로 검색 실행
actions = ActionChains(driver)
actions.send_keys(Keys.ENTER)
actions.perform()
time.sleep(1.5)  # 데이터 로딩 대기
```

## Alert 처리

```python
try:
    alert = driver.switch_to.alert
    alert_text = alert.text
    alert.accept()
    if '없' in alert_text or '불가' in alert_text:
        print(f"[ALERT] {alert_text}")
        return None
except:
    pass  # Alert 없으면 정상
```

## 팝업 닫기 (기본)

```javascript
// 열린 팝업 프레임 닫기 (간단한 경우)
const popupFrames = document.querySelectorAll('[id*="popupframe"], [id*="PopupFrame"]');
for (const popup of popupFrames) {
    if (popup.offsetParent !== null) {
        const closeBtn = popup.querySelector('[id*="btn_close"], [id*="Close"]');
        if (closeBtn) closeBtn.click();
    }
}
```

## 고급 팝업 처리 (Advanced Popup Handling)

### 문제 상황

넥사크로 팝업은 복잡한 프레임 구조로 인해 일반적인 JavaScript 접근이 실패할 수 있습니다:

1. **WorkFrame에 렌더링**: 팝업이 작업 중인 프레임(예: STGJ010_M0) 외부의 독립 프레임에 생성
2. **여러 프레임의 "닫기" 버튼**: 전역 검색 시 다른 화면의 닫기 버튼을 잘못 클릭
3. **Element Click Intercepted**: 팝업 오버레이나 다른 요소가 클릭 차단

### 프레임 구조 예시

```
nexacro.getApplication()
├── mainframe.HFrameSet00.VFrameSet00.FrameSet
│   ├── STGJ010_M0 (센터매입 화면) ← 작업 중인 프레임
│   └── WorkFrame (독립 팝업 컨테이너)
│       └── STZZ120_P0 (팝업 화면)
│           └── btn_close (닫기 버튼) ← 여기에 있음!
```

### 실패하는 접근 방법들

#### ❌ 방법 1: 전역 JavaScript 검색
```javascript
// 문제: 다른 프레임의 닫기 버튼을 클릭할 수 있음
const allElements = document.querySelectorAll('*');
for (const elem of allElements) {
    if (elem.textContent.trim() === '닫기') {
        elem.click();  // 잘못된 프레임의 버튼 클릭 가능!
    }
}
```

#### ❌ 방법 2: 현재 프레임 내부만 검색
```javascript
// 문제: 팝업이 WorkFrame에 있어서 찾지 못함
const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STGJ010_M0;
const frameContainer = document.querySelector('[id*="STGJ010_M0"]');
const closeBtn = frameContainer.querySelector('[id*="btn_close"]');
// null 반환 - 팝업이 이 프레임 밖에 있음!
```

### ✅ 해결 방법: Selenium Native XPath

JavaScript 복잡도를 완전히 우회하고 Selenium의 네이티브 요소 찾기 사용:

```python
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

def close_popups_selenium(driver) -> dict:
    """Selenium XPath로 팝업 닫기 (권장 방법)"""
    debug_info = {
        "method": "selenium_native",
        "found_elements": [],
        "attempts": []
    }
    closed = 0

    # 여러 XPath 패턴으로 "닫기" 버튼 찾기
    xpaths = [
        "//*[text()='닫기']",                          # 정확히 "닫기"인 요소
        "//button[contains(text(), '닫기')]",         # 버튼 중 "닫기" 포함
        "//div[contains(text(), '닫기')]",            # div 중 "닫기" 포함
        "//*[text()='×']",                             # X 버튼
        "//*[@id[contains(., 'btn_close')]]",         # ID에 btn_close 포함
        "//*[@class[contains(., 'btn_close')]]"       # class에 btn_close 포함
    ]

    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            debug_info["found_elements"].append({
                "xpath": xpath,
                "count": len(elements)
            })

            for elem in elements:
                # 실제로 보이는 요소만 클릭
                if elem.is_displayed():
                    try:
                        elem.click()
                        closed += 1
                        debug_info["attempts"].append(f"SUCCESS_{elem.tag_name}")
                        # 첫 번째 성공 시 즉시 종료
                        return {"success": True, "closed": closed, "debug": debug_info}
                    except Exception as e:
                        # Element click intercepted 등의 에러
                        debug_info["attempts"].append(f"click_failed_{str(e)[:50]}")
                        continue  # 다음 요소 시도

        except NoSuchElementException:
            continue

    return {"success": True, "closed": closed, "debug": debug_info}
```

### 사용 예시

```python
# 화면 이동 후 팝업 닫기
logger.info("팝업 닫기 시도...")
popup_result = close_popups_selenium(driver)

if popup_result.get('success'):
    closed_count = popup_result.get('closed', 0)
    if closed_count > 0:
        logger.info(f"[OK] 팝업 닫기 성공: {closed_count}개")
        time.sleep(2.0)  # 팝업 닫힌 후 안정화 대기
    else:
        logger.warning("[WARNING] 닫기 버튼을 찾지 못함")
```

### 장점

1. **프레임 무관**: 어떤 프레임에 팝업이 있든 찾을 수 있음
2. **JavaScript 우회**: Nexacro 복잡도와 무관하게 작동
3. **가시성 체크**: `is_displayed()`로 실제 보이는 요소만 클릭
4. **재시도 로직**: Click intercepted 시 다음 요소 자동 시도
5. **디버그 정보**: 어떤 XPath가 매칭되었는지 추적 가능

### 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| "element click intercepted" | 다른 요소가 버튼 위에 오버레이됨 | 여러 요소를 순회하며 시도 (코드 내장) |
| 여러 개 발견되지만 클릭 안됨 | 숨겨진 요소들 포함 | `is_displayed()` 체크 (코드 내장) |
| 닫기 버튼을 찾지 못함 | 버튼 텍스트가 "닫기"가 아님 | XPath에 다른 패턴 추가 (예: "Close", "확인") |
| 팝업이 다시 나타남 | 연속된 팝업들 | 여러 번 호출하거나 루프로 처리 |

### 대안: Nexacro API (고급)

특정 팝업을 알고 있는 경우 Nexacro API 직접 호출:

```python
script = """
    try {
        const app = nexacro.getApplication();
        // 활성 팝업 목록 가져오기
        const popupIds = Object.keys(app.popupFrames || {});

        for (const popupId of popupIds) {
            const popup = app.popupFrames[popupId];
            if (popup && popup.visible !== false) {
                app.closePopup(popupId);
            }
        }

        return {success: true, closed: popupIds.length};
    } catch(e) {
        return {error: e.message};
    }
"""
driver.execute_script(script)
```

**주의**: 이 방법은 `app.popupFrames`가 존재하고 접근 가능한 경우에만 작동합니다.

## 탭 닫기

```javascript
// 특정 화면 탭 닫기
const closeBtn = document.querySelector('[id*="{FRAME_ID}"][id*="btn_topClose"]');
if (closeBtn && closeBtn.offsetParent !== null) {
    closeBtn.click();
}
```

## 실전 예제: 센터매입 화면 데이터 수집

### 시나리오

센터매입 조회/확정 화면(STGJ010_M0)에서 전표 목록과 상품 데이터 수집:
1. 화면 접속 시 "매입/반품 차이내역 미확인 안내" 팝업 차단
2. 전표 목록(dsListPopup) 조회
3. 각 전표 선택하여 상품 목록(dsList) 수집

### 구현

```python
def analyze_receiving_screen(driver, frame_id: str = "STGJ010_M0"):
    """센터매입 화면 데이터 수집"""

    # 1. 화면 이동 후 팝업 닫기
    logger.info("화면 이동 완료")
    time.sleep(2.0)  # 화면 로딩 대기

    # Selenium XPath로 팝업 닫기
    logger.info("팝업 닫기 시도...")
    popup_result = close_popups_selenium(driver)
    if popup_result.get('closed', 0) > 0:
        logger.info(f"[OK] 팝업 {popup_result['closed']}개 닫음")
        time.sleep(2.0)  # 팝업 닫힌 후 안정화

    # 2. 전표 목록 조회 (dsListPopup)
    script = f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            if (!wf || !wf.dsListPopup) {{
                return {{error: 'dsListPopup not found'}};
            }}

            const ds = wf.dsListPopup;
            const chits = [];

            for (let i = 0; i < ds.getRowCount(); i++) {{
                chits.push({{
                    ROW_INDEX: i,
                    CHIT_NO: ds.getColumn(i, 'CHIT_NO'),
                    CENTER_NM: ds.getColumn(i, 'CENTER_NM'),
                    DGFW_YMD: ds.getColumn(i, 'DGFW_YMD')
                }});
            }}

            return {{success: true, chits: chits}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """

    chit_result = driver.execute_script(script)
    chits = chit_result.get('chits', [])
    logger.info(f"전표 {len(chits)}개 발견")

    # 3. 각 전표의 상품 수집
    all_items = []

    for i, chit in enumerate(chits):
        # 전표 선택 (set_rowposition)
        select_script = f"""
            const wf = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form.div_workForm?.form;
            wf.dsListPopup.set_rowposition({i});
            return {{success: true}};
        """
        driver.execute_script(select_script)
        time.sleep(2.0)  # dsList 로딩 대기

        # 상품 목록 조회 (dsList)
        items_script = f"""
            try {{
                const wf = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form.div_workForm?.form;
                const ds = wf.dsList;
                const items = [];

                function getVal(ds, row, col) {{
                    let val = ds.getColumn(row, col);
                    if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                    return val;
                }}

                for (let j = 0; j < ds.getRowCount(); j++) {{
                    items.push({{
                        ITEM_CD: ds.getColumn(j, 'ITEM_CD'),
                        ITEM_NM: ds.getColumn(j, 'ITEM_NM'),
                        ORD_QTY: getVal(ds, j, 'ORD_QTY'),
                        NAP_QTY: getVal(ds, j, 'NAP_QTY')
                    }});
                }}

                return {{success: true, items: items}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """

        items_result = driver.execute_script(items_script)
        items = items_result.get('items', [])
        logger.info(f"  [{i+1}/{len(chits)}] 전표 {chit['CHIT_NO']}: {len(items)}개 상품")

        all_items.extend(items)

    logger.info(f"총 {len(all_items)}개 상품 수집 완료")
    return {"chits": chits, "items": all_items}
```

### 실행 결과

```
팝업 닫기 시도...
[OK] 팝업 1개 닫음
전표 3개 발견
  [1/3] 전표 67061900501: 18개 상품
  [2/3] 전표 67061910601: 5개 상품
  [3/3] 전표 37156260801: 1개 상품
총 24개 상품 수집 완료
```

### 핵심 포인트

1. **팝업 먼저 처리**: 데이터 수집 전에 반드시 팝업 닫기
2. **충분한 대기**: 각 전표 선택 후 `time.sleep(2.0)` 필수
3. **Decimal 변환**: `getVal()` 헬퍼로 `{hi, lo}` 객체 처리
4. **에러 핸들링**: JavaScript에서 `try-catch`로 예외 처리

## 주의사항

- `offsetParent !== null` 체크로 가시성 확인 (숨겨진 요소 클릭 방지)
- 데이터 로딩 후 반드시 대기 (`time.sleep(1~2)`)
- 그리드 행 추가 후 `set_rowposition()` 호출 필수
- 에디터 활성화: `grid.setFocus()` → `grid.setCellPos(col)` → `grid.showEditor(true)`
- **팝업 처리는 Selenium XPath 방법 우선 사용** (JavaScript 방법은 프레임 구조에 취약)
