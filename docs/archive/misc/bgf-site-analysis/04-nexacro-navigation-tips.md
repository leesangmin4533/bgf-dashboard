# 넥사크로 네비게이션 팁 (2026-02-27 탐색에서 습득)

## 핵심 교훈

### 1. nexacro app 접근

```javascript
// ❌ 잘못된 방법 (app is not defined 에러)
var app = app;

// ✅ 올바른 방법
var app = nexacro.getApplication();
```

### 2. TopFrame 데이터셋 접근

```javascript
var topForm = app.mainframe.HFrameSet00.VFrameSet00.TopFrame.form;

// ❌ objects 배열에서 typename으로 필터링 (typename이 undefined 반환)
topForm.objects[i].typename === 'Dataset'  // 항상 false

// ✅ 이름으로 직접 접근
var ds = topForm['ds_orgMenu'];
if (ds && typeof ds.getRowCount === 'function') {
    // Dataset임을 getRowCount 존재 여부로 판단 (duck typing)
}
```

### 3. DOM ID 기반 메뉴 클릭

```javascript
// 상위 메뉴 클릭 패턴
var TOP_PREFIX = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.div_topMenu.form.";
var topMenuId = TOP_PREFIX + "STMB000_M0:icontext";

// 서브 메뉴 클릭 패턴
var SUB_PREFIX = "mainframe.HFrameSet00.VFrameSet00.TopFrame.form.pdiv_topMenu_";
var subMenuId = SUB_PREFIX + "STMB000_M0.form.STMB011_M0:text";
```

### 4. MouseEvent 시뮬레이션 (필수)

```javascript
function clickById(id) {
    const el = document.getElementById(id);
    if (!el || el.offsetParent === null) return false;

    el.scrollIntoView({block: 'center', inline: 'center'});
    const r = el.getBoundingClientRect();
    const o = {
        bubbles: true,
        cancelable: true,
        view: window,
        clientX: r.left + r.width / 2,
        clientY: r.top + r.height / 2
    };

    // mousedown → mouseup → click 시퀀스 필수
    el.dispatchEvent(new MouseEvent('mousedown', o));
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.dispatchEvent(new MouseEvent('click', o));
    return true;
}
```

### 5. 서브메뉴 패널은 클릭으로 생성

```
❌ mouseover만으로는 서브메뉴 패널(pdiv_topMenu_*)이 DOM에 생성되지 않음
✅ 상위 메뉴를 실제 클릭해야 서브메뉴 패널이 생성됨
```

- 상위 메뉴 클릭 후 `pdiv_topMenu_{PARENT_ID}` 패널 내 요소 탐색
- 서브메뉴 요소 ID 패턴: `...form.{MENU_ID}:text` 또는 `...form.{MENU_ID}:icontext`

### 6. WorkFrame 화면 로딩 대기

```javascript
// WorkFrame이 로드될 때까지 폴링
for (var i = 0; i < 16; i++) {
    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
    if (wf && wf.form && wf.form.objects) break;
    // 0.5초 대기
}
```

### 7. 동시 열림 제한: 7개

```
넥사크로 앱에서 동시에 열 수 있는 화면(탭)은 최대 7개.
초과 시: alert("화면을 7개 이상 열 수 없습니다.")

대응:
- Selenium: try/except로 alert 처리 후 탭 닫기
- 탭 닫기 API: (미해결, nexacro 내부 함수 확인 필요)
```

### 8. XHR 인터셉터

```javascript
window.__xhrLog = [];
var oO = XMLHttpRequest.prototype.open;
var oS = XMLHttpRequest.prototype.send;

XMLHttpRequest.prototype.open = function(m, u) {
    this.__u = u;
    this.__m = m;
    return oO.apply(this, arguments);
};

XMLHttpRequest.prototype.send = function(b) {
    var u = this.__u || '';
    // /st, /ST, /ss, /SS 패턴만 캡처 (xfdl 제외)
    if (u && (u.indexOf('/st') >= 0 || u.indexOf('/ST') >= 0)) {
        window.__xhrLog.push({
            url: u,
            method: this.__m,
            bodyLen: b ? b.length : 0,
            body: b ? b.substring(0, 500) : '',
            ts: new Date().toISOString()
        });
    }
    return oS.apply(this, arguments);
};
```

### 9. ds_orgMenu 활용

전체 메뉴 트리가 `ds_orgMenu` (272행)에 저장:

```javascript
var ds = topForm['ds_orgMenu'];
for (var r = 0; r < ds.getRowCount(); r++) {
    var menuId = ds.getColumn(r, 'MENU_ID');
    var menuNm = ds.getColumn(r, 'MENU_NM');
    var url = ds.getColumn(r, 'URL');
    var folderYn = ds.getColumn(r, 'FOLDER_YN');
    // folderYn === '0' 이면 실제 화면 (리프 메뉴)
}
```

## 시행착오 기록

| 시도 | 결과 | 교훈 |
|------|------|------|
| v1: `app` 직접 접근 | `app is not defined` | `nexacro.getApplication()` 필수 |
| v2: nexacro Dataset API | Menu Dataset 못 찾음 | `objects[].typename` 이 undefined |
| v3: mouseover로 서브메뉴 | 패널 생성 안 됨 | 실제 click 필요 |
| v4: 전체 화면 순회 | 7개 제한 alert | 탭 관리 필요 |
| v5: close_all_tabs() | 탭 닫기 실패 | nexacro 탭 닫기 API 추가 조사 필요 |
| 최종: ds_orgMenu 직접 추출 | ✅ 272행 전체 성공 | 이름으로 직접 접근 |
