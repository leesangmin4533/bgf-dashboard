# Design: 자동발주제외 (Auto-Order Exclusion)

> Plan 참조: `docs/01-plan/features/auto-order-exclude.plan.md`

---

## 1. 설계 개요

발주 현황 조회(STBJ070_M0) 화면에서 "자동" 라디오 버튼을 클릭하여 자동발주 상품 목록을 수집하고, `auto_order.py`의 `get_recommendations()`에서 해당 상품을 제외하는 필터를 추가한다.

### 변경 범위

| # | 파일 | 변경 유형 | 설명 |
|---|------|----------|------|
| 1 | `src/collectors/order_status_collector.py` | 수정 | `click_auto_radio()`, `collect_auto_order_items()`, `close_menu()` 추가 |
| 2 | `src/order/auto_order.py` | 수정 | `_auto_order_items` 필드, `load_auto_order_items()`, 제외 필터 |
| 3 | `src/config/timing.py` | 수정 | 타이밍 상수 2개 추가 |

DB 테이블 추가 없음 (사이트 실시간 조회 방식, 캐시 불필요).

---

## 2. 넥사크로 "자동" 라디오 버튼 클릭 설계

### 2-1. DOM 구조 분석

넥사크로 Radio 컴포넌트의 DOM 패턴:

```html
<!-- 넥사크로 Radio 그룹 (일반적 구조) -->
<div id="...rdo_OrdType...">           ← Radio 컴포넌트 루트
  <div>                                 ← 아이템 컨테이너
    <img class="nexaiconitem" src="...rdo_WF_Radio_S.png">  ← 라디오 아이콘
    <span>수동</span>                   ← 라벨 텍스트
  </div>
  <div>
    <img class="nexaiconitem" src="...rdo_WF_Radio_S.png">  ← 라디오 아이콘
    <span>자동</span>                   ← 라벨 텍스트 ★ 클릭 대상
  </div>
  <div>
    <img class="nexaiconitem" src="...rdo_WF_Radio_S.png">
    <span>전체</span>
  </div>
</div>
```

### 2-2. 클릭 전략 (2단계 폴백)

넥사크로 라디오 버튼은 `<img>` 직접 클릭으로는 동작하지 않을 수 있다. "자동" 텍스트를 찾아 그 **부모** 또는 **인접한 img**를 클릭하는 방식 사용.

**Strategy A (기본)**: "자동" 텍스트 요소의 부모 div 클릭

```javascript
// STBJ070_M0 화면 내에서 "자동" 텍스트를 가진 라디오 아이템 찾기
const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STBJ070_M0;
const frameEl = frame._getElementNative?.() || document.querySelector('[id*="STBJ070_M0"]');

// 1. 프레임 내 모든 텍스트 요소에서 "자동" 찾기
const allTexts = frameEl.querySelectorAll('[class*="nexacontentsitem"], span, div');
for (const el of allTexts) {
    if (el.textContent.trim() === '자동' && el.offsetParent !== null) {
        // 부모 요소(라디오 아이템 컨테이너) 클릭
        const target = el.parentElement || el;
        clickElement(target);
        return {success: true, method: 'text_parent'};
    }
}
```

**Strategy B (폴백)**: nexaiconitem img 요소 인덱스 기반 클릭

```javascript
// 라디오 아이콘 이미지 중 2번째(인덱스1) = "자동"
const radioImgs = frameEl.querySelectorAll('img.nexaiconitem[src*="rdo_WF_Radio"]');
if (radioImgs.length >= 2) {
    clickElement(radioImgs[1]);  // 0=수동, 1=자동, 2=전체
    return {success: true, method: 'img_index'};
}
```

**Strategy C (최종 폴백)**: 넥사크로 API 직접 호출

```javascript
// 넥사크로 Radio 컴포넌트의 set_value() 메서드
const wf = frame.form.div_workForm.form.div_work.form;
// 라디오 컴포넌트 이름 패턴: rdo_OrdType, rdoType, rdo_searchType 등
const radioNames = ['rdo_OrdType', 'rdoType', 'rdo_searchType', 'Radio00'];
for (const name of radioNames) {
    const radio = wf[name];
    if (radio && radio.set_value) {
        radio.set_value('1');  // 0=수동, 1=자동, 2=전체 (추정)
        radio.on_fire_onitemchanged?.(radio, {});
        return {success: true, method: 'api_set_value', component: name};
    }
}
```

### 2-3. 클릭 후 데이터 갱신 확인

라디오 버튼 클릭 후 dsResult가 자동 갱신되는지, 별도 조회 버튼 필요한지 확인 필요.

```javascript
// 클릭 후 dsResult 행 수 변화 감지 (최대 5초 대기)
function waitForData(wf, maxWait) {
    const start = Date.now();
    return new Promise(resolve => {
        const check = () => {
            const ds = wf.dsResult;
            if (ds && ds.getRowCount() > 0) {
                resolve({success: true, rows: ds.getRowCount()});
                return;
            }
            if (Date.now() - start > maxWait) {
                resolve({success: false, rows: 0});
                return;
            }
            setTimeout(check, 500);
        };
        check();
    });
}
```

Python 측에서는 동기적으로 폴링:

```python
for attempt in range(6):  # 최대 3초 (0.5초 × 6)
    time.sleep(0.5)
    row_count = self._get_ds_result_row_count()
    if row_count > 0:
        break
```

---

## 3. OrderStatusCollector 확장 설계

### 3-1. 신규 메서드

#### `click_auto_radio(self) -> bool`

```python
def click_auto_radio(self) -> bool:
    """
    "자동" 라디오 버튼 클릭

    3단계 폴백 전략:
    1. "자동" 텍스트 부모 클릭
    2. nexaiconitem img 인덱스 클릭
    3. 넥사크로 Radio API set_value()

    Returns:
        클릭 성공 여부
    """
```

**JavaScript 통합 스크립트** (3단계를 하나의 execute_script로):

```python
result = self.driver.execute_script(JS_CLICK_HELPER + f"""
    try {{
        const app = nexacro.getApplication();
        const frame = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{self.FRAME_ID};
        const wf = frame.form.{self.DS_PATH};

        // Strategy A: "자동" 텍스트 찾아 부모 클릭
        const frameDoc = frame._getElementNative?.()
            || document.querySelector('[id*="{self.FRAME_ID}"]');
        if (frameDoc) {{
            const texts = frameDoc.querySelectorAll('span, div, [class*="nexacontentsitem"]');
            for (const el of texts) {{
                if (el.textContent.trim() === '자동' && el.offsetParent !== null) {{
                    clickElement(el.parentElement || el);
                    return {{success: true, method: 'text_parent'}};
                }}
            }}
        }}

        // Strategy B: img 인덱스
        const imgs = document.querySelectorAll(
            '[id*="{self.FRAME_ID}"] img.nexaiconitem[src*="rdo_WF_Radio"]'
        );
        if (imgs.length >= 2) {{
            clickElement(imgs[1]);
            return {{success: true, method: 'img_index'}};
        }}

        // Strategy C: 넥사크로 API
        const names = ['rdo_OrdType', 'rdoType', 'rdo_searchType', 'Radio00'];
        for (const name of names) {{
            const radio = wf[name];
            if (radio && radio.set_value) {{
                radio.set_value('1');
                if (radio.on_fire_onitemchanged) {{
                    radio.on_fire_onitemchanged(radio, {{}});
                }}
                return {{success: true, method: 'api', component: name}};
            }}
        }}

        return {{success: false, error: 'radio not found'}};
    }} catch(e) {{
        return {{success: false, error: e.message}};
    }}
""")
```

#### `collect_auto_order_items(self) -> Set[str]`

```python
def collect_auto_order_items(self) -> Set[str]:
    """
    자동발주 상품코드 목록 수집

    Flow:
    1. 메뉴 이동 (navigate_to_order_status_menu)
    2. "자동" 라디오 클릭 (click_auto_radio)
    3. 데이터 갱신 대기
    4. dsResult에서 ITEM_CD 추출

    Returns:
        자동발주 상품코드 set
    """
```

핵심 JS (dsResult에서 ITEM_CD 추출):

```python
result = self.driver.execute_script(f"""
    try {{
        const app = nexacro.getApplication();
        const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
            .{self.FRAME_ID}.form.{self.DS_PATH};
        const ds = wf?.dsResult;
        if (!ds) return {{error: 'dsResult not found'}};

        const items = [];
        for (let i = 0; i < ds.getRowCount(); i++) {{
            let cd = ds.getColumn(i, 'ITEM_CD');
            if (cd && typeof cd === 'object' && cd.hi !== undefined) cd = cd.hi;
            if (cd) items.push(String(cd));
        }}
        return {{items: items, total: ds.getRowCount()}};
    }} catch(e) {{
        return {{error: e.message}};
    }}
""")
```

#### `close_menu(self) -> None`

```python
def close_menu(self) -> None:
    """발주 현황 조회 메뉴 탭 닫기"""
    try:
        close_tab_by_frame_id(self.driver, self.FRAME_ID)
        time.sleep(ORDER_STATUS_MENU_CLOSE_WAIT)
    except Exception as e:
        logger.warning(f"발주 현황 조회 탭 닫기 실패: {e}")
```

---

## 4. AutoOrderSystem 확장 설계

### 4-1. 필드 추가

`__init__()` (line ~105 부근):

```python
# 기존
self._cut_items: set = set()

# ★ 신규 추가
self._auto_order_items: set = set()  # 자동발주 상품 (BGF 본부 관리)
```

### 4-2. 신규 메서드: `load_auto_order_items()`

```python
def load_auto_order_items(self) -> None:
    """
    발주 현황 조회 > 자동 탭에서 자동발주 상품 목록 조회

    사이트 접속하여 실시간 조회. 실패 시 빈 set 유지 (발주 진행).
    """
    if not self.driver:
        logger.info("드라이버 없음 — 자동발주 상품 조회 스킵")
        return

    try:
        from src.collectors.order_status_collector import OrderStatusCollector

        collector = OrderStatusCollector(self.driver)

        # 메뉴 이동
        if not collector.navigate_to_order_status_menu():
            logger.warning("발주 현황 조회 메뉴 이동 실패 — 자동발주 제외 스킵")
            return

        # 자동 탭 상품 수집
        auto_items = collector.collect_auto_order_items()

        if auto_items:
            self._auto_order_items = auto_items
            logger.info(f"자동발주 상품 {len(auto_items)}개 로드됨 (발주 제외 예정)")
        else:
            logger.info("자동발주 상품 없음")

        # 메뉴 탭 닫기
        collector.close_menu()

    except Exception as e:
        logger.warning(f"자동발주 상품 조회 실패 (발주 진행): {e}")
```

### 4-3. `execute()` 흐름 변경

현재 흐름 (line 592~597):

```python
# 기존
self.load_unavailable_from_db()
self.load_cut_items_from_db()
# ... prefetch / get_recommendations
```

변경 후:

```python
self.load_unavailable_from_db()
self.load_cut_items_from_db()
self.load_auto_order_items()       # ★ 신규 (사이트 조회)
# ... prefetch / get_recommendations
```

### 4-4. `get_recommendations()` 필터 추가

`_cut_items` 제외 블록 바로 뒤 (line 465 이후)에 동일 패턴 추가:

```python
# 자동발주 상품 제외 (BGF 본부 관리)
if self._auto_order_items:
    before_count = len(order_list)
    order_list = [item for item in order_list
                  if item["item_cd"] not in self._auto_order_items]
    excluded = before_count - len(order_list)
    if excluded > 0:
        logger.info(f"자동발주(본부관리) {excluded}개 상품 제외")
```

> 기존 예측기 분기(line 499~505)에도 동일하게 추가.

---

## 5. 타이밍 상수 추가

**파일**: `src/config/timing.py`

```python
# 발주 현황 조회
ORDER_STATUS_RADIO_CLICK_WAIT = 2.0    # 라디오 버튼 클릭 후 데이터 갱신 대기
ORDER_STATUS_MENU_CLOSE_WAIT = 1.0     # 메뉴 탭 닫기 후 대기
```

---

## 6. execute() 전체 흐름도 (변경 후)

```
execute()
  │
  ├─ load_unavailable_from_db()        # DB에서 미취급 상품 로드
  ├─ load_cut_items_from_db()          # DB에서 발주중지 상품 로드
  │
  ├─ load_auto_order_items()           # ★ 신규
  │   ├─ navigate_to_order_status_menu()  (발주 > 발주 현황 조회)
  │   ├─ click_auto_radio()               ("자동" 라디오 클릭)
  │   ├─ collect_auto_order_items()       (dsResult → ITEM_CD set)
  │   └─ close_menu()                     (탭 닫기)
  │
  ├─ get_recommendations()
  │   ├─ improved_predictor.get_order_candidates()
  │   ├─ _unavailable_items 제외
  │   ├─ _cut_items 제외
  │   ├─ _auto_order_items 제외        # ★ 신규 필터
  │   └─ food_daily_cap 적용
  │
  ├─ prefetch_pending_quantities()
  ├─ _apply_pending_and_stock_to_order_list()
  ├─ _ensure_clean_screen_state()
  └─ executor.execute_orders()
```

---

## 7. 에러 핸들링 정책

| 실패 시나리오 | 동작 |
|-------------|------|
| 메뉴 이동 실패 | warning 로그, `_auto_order_items` 빈 set → 발주 진행 |
| 라디오 클릭 실패 (3단계 모두) | warning 로그, 빈 set → 발주 진행 |
| dsResult 데이터 없음 (자동발주 0건) | info 로그, 정상 → 전체 상품 발주 |
| 메뉴 닫기 실패 | warning 로그, `_ensure_clean_screen_state()`에서 재정리 |

핵심 원칙: **자동발주 조회 실패 시에도 발주는 항상 진행한다** (기존 동작 유지).

---

## 8. 구현 순서

```
Step 1: src/config/timing.py          — 상수 2개 추가
Step 2: src/collectors/order_status_collector.py
        — click_auto_radio()
        — collect_auto_order_items()
        — close_menu()
Step 3: src/order/auto_order.py
        — _auto_order_items 필드
        — load_auto_order_items()
        — get_recommendations() 필터 (2곳)
        — execute() 호출 추가
Step 4: 검증 (--preview 모드)
```

---

## 9. 검증 방법

### 9-1. 단위 검증

```bash
# 1. Python 구문 검증
python -m py_compile src/collectors/order_status_collector.py
python -m py_compile src/order/auto_order.py

# 2. 자동발주 조회 단독 테스트 (사이트 로그인 필요)
python -c "
from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_status_collector import OrderStatusCollector

sa = SalesAnalyzer()
sa.login()
collector = OrderStatusCollector(sa.driver)
collector.navigate_to_order_status_menu()
auto_items = collector.collect_auto_order_items()
print(f'자동발주 상품: {len(auto_items)}개')
for cd in list(auto_items)[:10]:
    print(f'  {cd}')
collector.close_menu()
"
```

### 9-2. 통합 검증

```bash
# 기존 preview에서 자동발주 상품이 제외되는지 확인
python scripts/run_auto_order.py --preview
# 로그에서 "자동발주(본부관리) N개 상품 제외" 메시지 확인
```
