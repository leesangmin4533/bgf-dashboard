# 팝업 닫기 개선 v2 - 넥사크로 API 방식

## 문제 분석

### 이전 문제
- ✅ 로그는 출력됨: "팝업 N개 닫음"
- ❌ 실제로 팝업이 닫히지 않음
- **원인**: DOM querySelector로 HTML 요소를 찾았지만, 넥사크로 팝업은 JavaScript 프레임 객체

### 근본 원인
BGF는 **넥사크로(Nexacro) 프레임워크** 기반:
- 팝업은 HTML DOM 요소가 아닌 넥사크로 Frame 객체
- `document.querySelector()`로 찾을 수 없음
- 넥사크로 API(`nexacro.getApplication()`)로 접근해야 함

---

## 해결 방법

### 1. 넥사크로 API 기반 팝업 닫기

**수정 파일**: `src/utils/popup_manager.py::close_all_popups()`

#### 변경 전 (DOM 방식)
```javascript
// ❌ DOM querySelector - 넥사크로 프레임 찾지 못함
const popups = document.querySelectorAll('[id*="Popup"]');
for (const popup of popups) {
    const closeBtn = popup.querySelector('[id*="btn_close"]');
    if (closeBtn) closeBtn.click();
}
```

#### 변경 후 (넥사크로 API 방식)
```javascript
// ✅ 넥사크로 API로 프레임 탐색
const app = nexacro.getApplication();
const allFrames = app.mainframe.all;

for (let i = 0; i < allFrames.length; i++) {
    const frame = allFrames[i];
    const frameId = frame.id || frame.name || '';

    // 팝업 프레임 필터링
    if (frameId.includes('Popup') || frameId.includes('CallItem')) {
        if (frame.visible !== false) {
            // 넥사크로 API로 닫기
            if (typeof app.gfn_closeFrame === 'function') {
                app.gfn_closeFrame(frameId);
                closedCount++;
            }
        }
    }
}
```

### 2. 폴백 메커니즘 추가

넥사크로 API 실패 시 DOM 방식으로 자동 전환:

```javascript
// 1차 시도: 넥사크로 API
if (closedCount === 0) {
    // 2차 시도: DOM querySelector (폴백)
    const popupFrames = document.querySelectorAll('[id*="popupframe"]');
    // ...
}
```

### 3. 진단 기능 추가

**발견된 팝업 로깅**:
```python
if found_popups and not silent:
    logger.debug(f"발견된 팝업 프레임: {', '.join(found_popups[:5])}")

if closed_count == 0 and found_popups:
    logger.warning(f"팝업 {len(found_popups)}개 발견했으나 닫기 실패")
```

---

## 테스트 방법

### 1. 진단 스크립트 실행

```bash
cd bgf_auto
python test_popup_diagnosis.py
```

**출력 예시**:
```
[1] 넥사크로 프레임 (23개)
  - STBJ030_M0 [VISIBLE] (FrameSet)
  - CallItemDetailPopup [VISIBLE] (ChildFrame)  ← 팝업!
  - STAJ001_M0 [VISIBLE] (FrameSet)

[2] DOM 팝업 요소 (5개)
  - popupframe_12345 (selector: [id*="popupframe"])

[3] 에러
  (없음)

[4] 닫아야 할 팝업 (1개)
  * CallItemDetailPopup
```

### 2. 실제 발주 플로우 테스트

```bash
python scripts/run_auto_order.py --categories 001,002 --max-items 3
```

**확인 포인트**:
1. "발견된 팝업 프레임: ..." 로그 출력
2. "팝업 N개 닫음" 로그의 N이 0이 아님
3. 실제 화면에서 팝업이 사라짐
4. 프로세스가 끝까지 진행됨

---

## 변경 파일 목록

### 수정
- `src/utils/popup_manager.py`
  - `close_all_popups()`: 넥사크로 API 방식으로 재작성
  - 진단 로깅 추가
  - 폴백 메커니즘 추가

### 신규
- `test_popup_diagnosis.py`: 팝업 진단 및 테스트 도구

### 문서
- `POPUP_FIX_V2.md`: 이 파일

---

## 기대 효과

### Before
```
2026-02-05 11:49:33 | INFO | 팝업 0개 닫음
→ 실제로는 팝업이 열려있음 (JavaScript가 찾지 못함)
```

### After
```
2026-02-05 11:49:33 | DEBUG | 발견된 팝업 프레임: CallItemDetailPopup, CallItemPopup
2026-02-05 11:49:33 | INFO | 팝업 2개 닫음
→ 넥사크로 API로 실제 팝업을 찾아서 닫음
```

---

## 참고 자료

- `.claude/skills/nexacro-scraping.md` (라인 155-166): 팝업 닫기 가이드
- `src/utils/nexacro_helpers.py` (라인 175-179): `gfn_closeFrame` 사용 예시

---

## 트러블슈팅

### "넥사크로API에러" 로그가 보이는 경우

**원인**: `nexacro.getApplication()` 접근 실패
**해결**: 페이지 로딩 대기 후 재시도

```python
time.sleep(1)
close_all_popups(driver)
```

### 팝업을 찾았지만 닫기 실패

**로그**:
```
WARNING | 팝업 1개 발견했으나 닫기 실패: CallItemPopup (API실패)
```

**원인**: `gfn_closeFrame()` 함수가 정의되지 않음
**해결**: DOM 폴백이 자동 실행되지만, 수동으로 닫아야 할 수 있음

### DOM 팝업만 보이고 넥사크로 프레임이 없음

**원인**: 로그인 전이거나 잘못된 페이지
**해결**: `SalesAnalyzer.login()` 후 메뉴 이동 확인

---

## Next Steps

1. `test_popup_diagnosis.py` 실행하여 팝업 구조 확인
2. 실제 발주 플로우에서 팝업 닫기 동작 확인
3. 로그에서 "발견된 팝업 프레임" 메시지 확인
4. 필요시 추가 팝업 패턴을 코드에 추가

---

## 코드 설명

### 넥사크로 프레임 탐색
```javascript
const app = nexacro.getApplication();  // 넥사크로 앱 객체
const allFrames = app.mainframe.all;   // 모든 프레임 배열

for (let i = 0; i < allFrames.length; i++) {
    const frame = allFrames[i];
    // frame.id: 프레임 ID (예: "CallItemPopup")
    // frame.visible: 가시성 (true/false)
    // frame.name: 프레임 이름
}
```

### 팝업 필터링
```javascript
if (frameId.includes('Popup') ||      // 일반 팝업
    frameId.includes('popup') ||      // 소문자 변형
    frameId.includes('CallItem') ||   // 상품 조회 팝업
    frameId.includes('Modal')) {      // 모달 다이얼로그
    // ...
}
```

### 팝업 닫기
```javascript
// 방법 1: gfn_closeFrame (추천)
app.gfn_closeFrame(frameId);

// 방법 2: closePopup 메서드
const owner = frame.getOwnerFrame();
if (owner && owner.form) {
    owner.form.closePopup(frameId);
}
```
