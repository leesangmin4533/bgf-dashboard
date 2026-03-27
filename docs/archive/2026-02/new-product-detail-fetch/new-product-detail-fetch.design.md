# Design: new-product-detail-fetch (상품상세 일괄수집)

## 1. 수정 파일 목록

| # | 파일 | 유형 | 내용 |
|---|------|------|------|
| 1 | `scripts/discover_popup_columns.py` | **신규** | Phase 0: 팝업 전체 컬럼 덤프 스크립트 |
| 2 | `src/collectors/product_detail_batch_collector.py` | **신규** | Phase 1: 일괄 수집기 |
| 3 | `src/infrastructure/database/repos/product_detail_repo.py` | 수정 | `get_items_needing_detail_fetch()` + `bulk_update_from_popup()` 추가 |
| 4 | `src/settings/timing.py` | 수정 | BD_* 타이밍 상수 추가 |
| 5 | `run_scheduler.py` | 수정 | 01:00 스케줄 + CLI 옵션 + wrapper 함수 |
| 6 | `tests/test_product_detail_batch_collector.py` | **신규** | 테스트 |

---

## 2. Phase 0: discover_popup_columns.py (디스커버리)

### 2-1. 파일 구조

**파일**: `scripts/discover_popup_columns.py`

```python
"""
CallItemDetailPopup 데이터셋 컬럼 전수 조사

BGF 사이트 로그인 → 홈 바코드 입력 → 팝업 열기 → 컬럼 덤프 → JSON 저장

Usage:
    python scripts/discover_popup_columns.py --item-cd 8801234567890
    python scripts/discover_popup_columns.py --item-cd 8801234567890 --output columns.json
"""
```

### 2-2. 핵심 JS: 데이터셋 전체 컬럼 덤프

팝업 오픈 후 실행할 JS (FailReasonCollector의 _extract_stop_reason 패턴 기반):

```javascript
var popupId = arguments[0];

function getPopupForm() {
    try {
        var app = nexacro.getApplication();
        var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
        if (wf[popupId] && wf[popupId].form) return wf[popupId].form;
        if (wf.popupframes && wf.popupframes[popupId]) return wf.popupframes[popupId].form;
        if (wf.form && wf.form[popupId]) return wf.form[popupId].form;
    } catch(e) {}
    return null;
}

function dumpDataset(ds, dsName) {
    var result = {name: dsName, colCount: 0, rowCount: 0, columns: []};
    if (!ds || typeof ds.getRowCount !== 'function') return result;
    result.rowCount = ds.getRowCount();
    var cc = ds.colcount || 0;
    result.colCount = cc;
    for (var i = 0; i < cc; i++) {
        try {
            var colId = ds.getColID(i);
            var val = (result.rowCount > 0) ? ds.getColumn(0, colId) : null;
            // Decimal 타입 처리
            if (val && typeof val === 'object' && val.hi !== undefined) {
                val = String(val.hi);
            }
            result.columns.push({
                index: i,
                name: colId,
                value: (val != null) ? String(val) : null,
                type: typeof val
            });
        } catch(e) {
            result.columns.push({index: i, name: '?', value: null, error: e.toString()});
        }
    }
    return result;
}

var popupForm = getPopupForm();
if (!popupForm) return {success: false, message: 'popup not found'};

var datasets = {};

// 1. dsItemDetail
try { datasets.dsItemDetail = dumpDataset(popupForm.dsItemDetail, 'dsItemDetail'); } catch(e) {}

// 2. dsItemDetailOrd
try { datasets.dsItemDetailOrd = dumpDataset(popupForm.dsItemDetailOrd, 'dsItemDetailOrd'); } catch(e) {}

// 3. popupForm.objects에서 추가 데이터셋 탐색
try {
    var objs = popupForm.objects;
    if (objs) {
        for (var key in objs) {
            if (objs[key] && typeof objs[key].getRowCount === 'function' && !datasets[key]) {
                datasets[key] = dumpDataset(objs[key], key);
            }
        }
    }
} catch(e) {}

// 4. UI 컴포넌트 텍스트 (divInfo, divInfo01 등)
var uiTexts = {};
try {
    var divs = ['divInfo', 'divInfo01', 'divInfo02', 'divDetail'];
    for (var d = 0; d < divs.length; d++) {
        var div = popupForm[divs[d]];
        if (div && div.form) {
            var comps = div.form.components || div.form.objects;
            if (comps) {
                for (var c in comps) {
                    try {
                        var comp = comps[c];
                        var txt = comp.text || comp.value || '';
                        if (txt) uiTexts[divs[d] + '.' + c] = String(txt);
                    } catch(e2) {}
                }
            }
        }
    }
} catch(e) {}

return {success: true, datasets: datasets, uiTexts: uiTexts};
```

### 2-3. 플로우

```python
def main(item_cd: str, output_path: str = None):
    # 1. 로그인 (SalesAnalyzer.login() 재활용)
    # 2. edt_pluSearch에 바코드 입력 (FailReasonCollector 패턴)
    # 3. Enter → 팝업 대기 (FR_POPUP_MAX_CHECKS 폴링)
    # 4. 전체 컬럼 덤프 JS 실행
    # 5. 팝업 닫기
    # 6. 결과 출력 + JSON 저장
    # 7. 드라이버 종료
```

### 2-4. 출력 형식

```json
{
  "item_cd": "8801234567890",
  "timestamp": "2026-02-26T22:00:00",
  "datasets": {
    "dsItemDetail": {
      "name": "dsItemDetail",
      "colCount": 25,
      "rowCount": 1,
      "columns": [
        {"index": 0, "name": "ITEM_CD", "value": "8801234567890", "type": "string"},
        {"index": 1, "name": "ITEM_NM", "value": "CU)매콤불닭도시락", "type": "string"},
        {"index": 2, "name": "MID_CD", "value": "001", "type": "string"},
        ...
      ]
    },
    "dsItemDetailOrd": { ... }
  },
  "uiTexts": {
    "divInfo.stItemNm": "CU)매콤불닭도시락",
    "divInfo01.stStopReason": "",
    ...
  }
}
```

### 2-5. Phase 0 결과 분석 기준

**사용자에게 보고할 항목:**

```
✅ 발견됨:
  - MID_CD → products.mid_cd 업데이트 가능
  - EXPIRE_DAY → product_details.expiration_days
  - ORD_ADAY → product_details.orderable_day
  - ...

❌ 미발견:
  - sell_price 관련 컬럼 없음 → 수집 범위에서 제외
  - margin_rate 관련 컬럼 없음 → 수집 범위에서 제외
  - ...

⚠️ 추가 발견:
  - [예상 못한 유용한 컬럼들]
```

---

## 3. Phase 1: product_detail_batch_collector.py (일괄 수집기)

### 3-1. 클래스 설계

```python
"""
상품 상세 정보 일괄 수집기

- 정보 미비 상품을 대상으로 CallItemDetailPopup에서 상세 정보 수집
- FailReasonCollector의 바코드입력/팝업대기/닫기 패턴 재활용
- common.db products + product_details 업데이트

실행:
    매일 01:00 스케줄 (run_scheduler.py)
    python run_scheduler.py --fetch-detail

플로우:
    1. 수집 대상 선별 (DB 조회)
    2. BGF 로그인 + 발주 화면 진입
    3. 상품별: 바코드입력 → 팝업 → 추출 → DB저장 → 닫기
    4. 진행상황 로깅 + 에러 건너뛰기
"""
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository
from src.settings.timing import (
    BD_BARCODE_INPUT_WAIT, BD_POPUP_MAX_CHECKS, BD_POPUP_CHECK_INTERVAL,
    BD_DATA_LOAD_MAX_CHECKS, BD_DATA_LOAD_CHECK_INTERVAL,
    BD_POPUP_CLOSE_WAIT, BD_BETWEEN_ITEMS, BD_MAX_ITEMS_PER_RUN
)
from src.settings.ui_config import FAIL_REASON_UI
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ProductDetailBatchCollector:
    """상품 상세 정보 일괄 수집기"""

    def __init__(self, driver: Any, store_id: Optional[str] = None):
        self.driver = driver
        self.store_id = store_id
        self._detail_repo = ProductDetailRepository()
        self._stats = {"total": 0, "success": 0, "skip": 0, "fail": 0}
```

### 3-2. 수집 대상 선별 메서드

```python
def get_items_to_fetch(self, limit: int = None) -> List[str]:
    """정보 미비 상품 코드 목록 반환

    대상 기준:
    1. product_details.fetched_at IS NULL (BGF 사이트 미조회)
    2. product_details.expiration_days IS NULL (유통기한 누락)
    3. product_details.orderable_day = '일월화수목금토' (기본값 그대로)
    4. products.mid_cd IN ('999', '') (카테고리 미분류)

    Args:
        limit: 최대 개수 (기본: BD_MAX_ITEMS_PER_RUN = 200)

    Returns:
        수집이 필요한 item_cd 리스트
    """
    max_items = limit or BD_MAX_ITEMS_PER_RUN
    return self._detail_repo.get_items_needing_detail_fetch(max_items)
```

### 3-3. 메인 수집 플로우

```python
def collect_all(self, item_codes: List[str] = None) -> Dict[str, int]:
    """일괄 수집 실행

    Args:
        item_codes: 수집할 상품 코드 (None이면 자동 선별)

    Returns:
        {"total": N, "success": N, "skip": N, "fail": N}
    """
    if item_codes is None:
        item_codes = self.get_items_to_fetch()

    if not item_codes:
        logger.info("[BatchDetail] 수집 대상 없음")
        return self._stats

    self._stats = {"total": len(item_codes), "success": 0, "skip": 0, "fail": 0}
    logger.info(f"[BatchDetail] 수집 시작: {len(item_codes)}개 상품")

    for i, item_cd in enumerate(item_codes):
        try:
            if (i + 1) % 10 == 0 or i == 0:
                logger.info(
                    f"[BatchDetail] 진행: {i+1}/{len(item_codes)} "
                    f"(성공={self._stats['success']}, 실패={self._stats['fail']})"
                )

            result = self._fetch_single_item(item_cd)

            if result is None:
                self._stats["fail"] += 1
            elif result == "skip":
                self._stats["skip"] += 1
            else:
                self._save_to_db(item_cd, result)
                self._stats["success"] += 1

        except Exception as e:
            logger.warning(f"[BatchDetail] {item_cd} 오류: {e}")
            self._stats["fail"] += 1

        # 상품 간 대기
        if i < len(item_codes) - 1:
            time.sleep(BD_BETWEEN_ITEMS)

    logger.info(
        f"[BatchDetail] 완료: "
        f"전체={self._stats['total']}, 성공={self._stats['success']}, "
        f"스킵={self._stats['skip']}, 실패={self._stats['fail']}"
    )
    return self._stats
```

### 3-4. 단일 상품 수집 (_fetch_single_item)

```python
def _fetch_single_item(self, item_cd: str) -> Optional[Dict[str, Any]]:
    """단일 상품 팝업 조회

    FailReasonCollector 패턴 재활용:
    바코드입력 → Enter/클릭 → 팝업대기(폴링) → 데이터추출 → 팝업닫기

    Returns:
        추출된 데이터 dict, 스킵이면 "skip", 실패면 None
    """
    popup_id = FAIL_REASON_UI["POPUP_ID"]  # "CallItemDetailPopup"

    # 1. 바코드 입력
    if not self._input_barcode(item_cd):
        return None

    time.sleep(BD_BARCODE_INPUT_WAIT)

    # 2. Enter → Quick Search 드롭다운 클릭
    if not self._trigger_search(item_cd):
        return None

    # 3. 팝업 대기 (폴링)
    if not self._wait_for_popup(popup_id):
        logger.warning(f"[BatchDetail] {item_cd} 팝업 미출현")
        return None

    # 4. 데이터 로딩 대기
    if not self._wait_for_data_load(popup_id):
        logger.warning(f"[BatchDetail] {item_cd} 데이터 로딩 타임아웃")
        self._close_popup(popup_id)
        return None

    # 5. 데이터 추출
    data = self._extract_detail(item_cd, popup_id)

    # 6. 팝업 닫기
    self._close_popup(popup_id)
    time.sleep(BD_POPUP_CLOSE_WAIT)

    return data
```

### 3-5. 바코드 입력 (_input_barcode)

FailReasonCollector `_input_barcode()` 와 동일한 4단계 폴백:

```python
def _input_barcode(self, item_cd: str) -> bool:
    """edt_pluSearch에 바코드 입력 (4단계 폴백)

    Level 1: nexacro component set_value
    Level 2: DOM ID direct value
    Level 3: querySelector
    Level 4: ActionChains (키보드)
    """
    result = self.driver.execute_script("""
        var barcode = arguments[0];
        // Level 1: Nexacro
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            if (wf.form.edt_pluSearch) {
                wf.form.edt_pluSearch.set_value(barcode);
                return {success: true, method: 'nexacro'};
            }
        } catch(e) {}
        // Level 2: DOM ID
        try {
            var domId = 'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.form.edt_pluSearch:input';
            var el = document.getElementById(domId);
            if (el) {
                el.value = barcode;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return {success: true, method: 'dom_id'};
            }
        } catch(e) {}
        // Level 3: querySelector
        try {
            var inputs = document.querySelectorAll('[id*="edt_pluSearch"] input, [id*="edt_pluSearch"]:not(div)');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].offsetParent !== null) {
                    inputs[i].value = barcode;
                    inputs[i].dispatchEvent(new Event('input', {bubbles: true}));
                    return {success: true, method: 'querySelector'};
                }
            }
        } catch(e) {}
        return {success: false};
    """, item_cd)
    return result and result.get("success", False)
```

### 3-6. Enter/클릭 트리거 + 팝업 대기

```python
def _trigger_search(self, item_cd: str) -> bool:
    """Enter 키 이벤트 → Quick Search 드롭다운 첫 항목 클릭"""
    # FailReasonCollector _trigger_search_and_click 패턴 동일
    # nexacro KeyEventInfo → DOM keyup → ActionChains 폴백
    ...

def _wait_for_popup(self, popup_id: str) -> bool:
    """CallItemDetailPopup 출현 폴링 (BD_POPUP_MAX_CHECKS회)"""
    # FailReasonCollector _wait_for_popup 패턴 동일
    for _ in range(BD_POPUP_MAX_CHECKS):
        result = self.driver.execute_script("""
            var pid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf[pid] && wf[pid].form) return {found: true};
                if (wf.popupframes && wf.popupframes[pid]) return {found: true};
                if (wf.form && wf.form[pid]) return {found: true};
            } catch(e) {}
            var formEl = document.querySelector('[id$="' + pid + '.form"]');
            if (formEl && formEl.offsetParent !== null) return {found: true};
            return {found: false};
        """, popup_id)
        if result and result.get("found"):
            return True
        time.sleep(BD_POPUP_CHECK_INTERVAL)
    return False

def _wait_for_data_load(self, popup_id: str) -> bool:
    """데이터 로딩 폴링 (dsItemDetail 행 존재 확인)"""
    for _ in range(BD_DATA_LOAD_MAX_CHECKS):
        result = self.driver.execute_script("""
            var pid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                var popup = wf[pid] || (wf.popupframes && wf.popupframes[pid])
                         || (wf.form && wf.form[pid]);
                if (popup && popup.form && popup.form.dsItemDetail) {
                    if (popup.form.dsItemDetail.getRowCount() > 0) return {loaded: true};
                }
            } catch(e) {}
            return {loaded: false};
        """, popup_id)
        if result and result.get("loaded"):
            return True
        time.sleep(BD_DATA_LOAD_CHECK_INTERVAL)
    return False
```

### 3-7. 데이터 추출 (_extract_detail) ★ 핵심

```python
def _extract_detail(self, item_cd: str, popup_id: str) -> Optional[Dict[str, Any]]:
    """CallItemDetailPopup에서 상세 정보 추출

    dsItemDetail + dsItemDetailOrd 모두 조회.
    Phase 0에서 확인된 컬럼만 추출.
    """
    result = self.driver.execute_script("""
        var popupId = arguments[0];
        var itemCd = arguments[1];

        function getPopupForm() {
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf[popupId] && wf[popupId].form) return wf[popupId].form;
                if (wf.popupframes && wf.popupframes[popupId]) return wf.popupframes[popupId].form;
                if (wf.form && wf.form[popupId]) return wf.form[popupId].form;
            } catch(e) {}
            return null;
        }

        function dsVal(ds, row, col) {
            try {
                var v = ds.getColumn(row, col);
                if (v && typeof v === 'object' && v.hi !== undefined) return String(v.hi);
                return v ? String(v) : null;
            } catch(e) { return null; }
        }

        var popupForm = getPopupForm();
        if (!popupForm) return null;

        var d1 = popupForm.dsItemDetail;
        var d2 = popupForm.dsItemDetailOrd || d1;
        if (!d1 || d1.getRowCount() <= 0) return null;

        var r = {
            item_cd: dsVal(d1, 0, 'ITEM_CD') || dsVal(d1, 0, 'PLU_CD') || itemCd,
            item_nm: dsVal(d1, 0, 'ITEM_NM'),

            // ★ 카테고리 (Phase 0에서 컬럼명 확정 후 활성화)
            // 후보: MID_CD, MCLS_CD, M_CATE_CD
            mid_cd: dsVal(d1, 0, 'MID_CD') || dsVal(d1, 0, 'MCLS_CD') || null,

            // 유통기한
            expiration_days: parseInt(dsVal(d1, 0, 'EXPIRE_DAY')) || null,

            // 발주 정보
            orderable_day: dsVal(d2, 0, 'ORD_ADAY') || dsVal(d1, 0, 'ORD_ADAY') || null,
            orderable_status: dsVal(d1, 0, 'ORD_PSS_ID_NM')
                           || dsVal(d2, 0, 'ORD_PSS_CHK_NM') || null,
            order_unit_qty: parseInt(dsVal(d1, 0, 'ORD_UNIT_QTY')
                           || dsVal(d2, 0, 'ORD_UNIT_QTY')) || null,
            order_unit_name: dsVal(d1, 0, 'ORD_UNIT_NM') || null,
            case_unit_qty: parseInt(dsVal(d1, 0, 'CASE_UNIT_QTY')) || null,

            // 가격 (Phase 0에서 컬럼명 확정 후 활성화)
            // 후보: SELL_PRC, MAEGA_AMT
            sell_price: parseInt(dsVal(d1, 0, 'SELL_PRC')
                      || dsVal(d1, 0, 'MAEGA_AMT')) || null,

            // 정지 사유
            order_stop_date: dsVal(d1, 0, 'ORD_STOP_YMD')
                          || dsVal(d1, 0, 'ORD_STOP_DT') || null
        };

        return r;
    """, popup_id, item_cd)

    if result and result.get("item_nm"):
        logger.info(
            f"[BatchDetail] {item_cd}: {result.get('item_nm')}, "
            f"mid_cd={result.get('mid_cd')}, "
            f"expire={result.get('expiration_days')}, "
            f"day={result.get('orderable_day')}"
        )
        return result
    return None
```

### 3-8. DB 저장 (_save_to_db)

```python
def _save_to_db(self, item_cd: str, data: Dict[str, Any]) -> None:
    """추출 데이터를 common.db에 저장

    1. products.mid_cd 업데이트 (현재 '999' 또는 '' 인 경우만)
    2. product_details 부분 업데이트 (NULL/기본값인 필드만)
    """
    now = datetime.now().isoformat()

    # 1. products.mid_cd 업데이트
    mid_cd = data.get("mid_cd")
    if mid_cd and mid_cd.strip():
        self._detail_repo.update_product_mid_cd(item_cd, mid_cd)

    # 2. product_details 부분 업데이트
    self._detail_repo.bulk_update_from_popup(item_cd, {
        "item_nm": data.get("item_nm"),
        "expiration_days": data.get("expiration_days"),
        "orderable_day": data.get("orderable_day"),
        "orderable_status": data.get("orderable_status"),
        "order_unit_qty": data.get("order_unit_qty"),
        "order_unit_name": data.get("order_unit_name"),
        "case_unit_qty": data.get("case_unit_qty"),
        "sell_price": data.get("sell_price"),
        "fetched_at": now,
    })
```

### 3-9. 팝업 닫기 (_close_popup)

```python
def _close_popup(self, popup_id: str) -> None:
    """CallItemDetailPopup 닫기 (FailReasonCollector 패턴 동일)"""
    self.driver.execute_script("""
        var popupId = arguments[0];
        // 1차: nexacro btn_close
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            var popup = wf[popupId]
                     || (wf.popupframes && wf.popupframes[popupId])
                     || (wf.form && wf.form[popupId]);
            if (popup && popup.form && popup.form.btn_close) {
                popup.form.btn_close.click();
                return;
            }
        } catch(e) {}
        // 2차: DOM btn_close
        try {
            var btnId = 'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.'
                      + popupId + '.form.btn_close';
            var el = document.getElementById(btnId);
            if (el && el.offsetParent !== null) {
                var r = el.getBoundingClientRect();
                var o = {bubbles:true, cancelable:true, view:window,
                         clientX:r.left+r.width/2, clientY:r.top+r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return;
            }
        } catch(e) {}
        // 3차: querySelector
        try {
            var btns = document.querySelectorAll(
                '[id*="' + popupId + '"][id*="btn_close"]');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].offsetParent !== null) {
                    btns[i].click();
                    return;
                }
            }
        } catch(e) {}
    """, popup_id)
```

---

## 4. product_detail_repo.py 변경 상세

### 4-1. get_items_needing_detail_fetch() 추가

```python
def get_items_needing_detail_fetch(self, limit: int = 200) -> List[str]:
    """상세 정보 수집이 필요한 상품 코드 목록

    대상 조건 (OR):
    1. product_details.fetched_at IS NULL (BGF 사이트 미조회)
    2. product_details.expiration_days IS NULL (유통기한 누락)
    3. product_details.orderable_day = '일월화수목금토' (기본값)
    4. products.mid_cd IN ('999', '') (카테고리 미분류)

    product_details에 아예 없는 products도 포함.

    Args:
        limit: 최대 반환 개수

    Returns:
        item_cd 리스트
    """
    conn = self._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT p.item_cd
            FROM products p
            LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
            WHERE pd.fetched_at IS NULL
               OR pd.expiration_days IS NULL
               OR pd.orderable_day = '일월화수목금토'
               OR p.mid_cd IN ('999', '')
            ORDER BY p.updated_at DESC
            LIMIT ?
        """, (limit,))
        return [row["item_cd"] for row in cursor.fetchall()]
    finally:
        conn.close()
```

### 4-2. bulk_update_from_popup() 추가

```python
def bulk_update_from_popup(self, item_cd: str, data: Dict[str, Any]) -> bool:
    """팝업에서 수집한 데이터로 product_details 부분 업데이트

    기존에 정확한 값이 있는 필드는 덮어쓰지 않음:
    - expiration_days: 기존 NULL인 경우만 갱신
    - orderable_day: 기존 '일월화수목금토'(기본값)인 경우만 갱신
    - orderable_status: 기존 NULL인 경우만 갱신
    - sell_price: 기존 NULL인 경우만 갱신
    - fetched_at: 항상 갱신

    Args:
        item_cd: 상품코드
        data: 팝업에서 추출된 데이터

    Returns:
        성공 여부
    """
    now = datetime.now().isoformat()
    conn = self._get_conn()
    try:
        # product_details에 행이 없으면 INSERT
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM product_details WHERE item_cd = ?",
            (item_cd,)
        )
        exists = cursor.fetchone()["cnt"] > 0

        if not exists:
            cursor.execute("""
                INSERT INTO product_details
                (item_cd, item_nm, expiration_days, orderable_day,
                 orderable_status, order_unit_name, order_unit_qty,
                 case_unit_qty, sell_price, fetched_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_cd, data.get("item_nm"),
                data.get("expiration_days"),
                data.get("orderable_day", "일월화수목금토"),
                data.get("orderable_status"),
                data.get("order_unit_name"),
                data.get("order_unit_qty", 1),
                data.get("case_unit_qty", 1),
                data.get("sell_price"),
                data.get("fetched_at", now),
                now, now,
            ))
        else:
            cursor.execute("""
                UPDATE product_details
                SET
                    expiration_days = COALESCE(?, expiration_days),
                    orderable_day = CASE
                        WHEN orderable_day IS NULL OR orderable_day = '일월화수목금토'
                        THEN COALESCE(?, orderable_day)
                        ELSE orderable_day END,
                    orderable_status = COALESCE(?, orderable_status),
                    order_unit_qty = CASE
                        WHEN ? IS NOT NULL AND ? > 0 THEN ?
                        ELSE order_unit_qty END,
                    order_unit_name = COALESCE(?, order_unit_name),
                    case_unit_qty = COALESCE(?, case_unit_qty),
                    sell_price = COALESCE(?, sell_price),
                    fetched_at = ?,
                    updated_at = ?
                WHERE item_cd = ?
            """, (
                data.get("expiration_days"),
                data.get("orderable_day"),
                data.get("orderable_status"),
                data.get("order_unit_qty"), data.get("order_unit_qty"), data.get("order_unit_qty"),
                data.get("order_unit_name"),
                data.get("case_unit_qty"),
                data.get("sell_price"),
                data.get("fetched_at", now),
                now,
                item_cd,
            ))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[BatchDetail] DB 저장 실패 {item_cd}: {e}")
        return False
    finally:
        conn.close()
```

### 4-3. update_product_mid_cd() 추가

```python
def update_product_mid_cd(self, item_cd: str, mid_cd: str) -> bool:
    """products 테이블의 mid_cd 업데이트 (추정값만 덮어쓰기)

    기존 mid_cd가 '999' 또는 '' 인 경우만 갱신.
    이미 정확한 카테고리가 있으면 건드리지 않음.

    Args:
        item_cd: 상품코드
        mid_cd: BGF 팝업에서 가져온 정확한 중분류코드

    Returns:
        성공 여부
    """
    conn = self._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE products
            SET mid_cd = ?, updated_at = ?
            WHERE item_cd = ?
            AND (mid_cd IN ('999', '') OR mid_cd IS NULL)
        """, (mid_cd, datetime.now().isoformat(), item_cd))
        conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info(f"[BatchDetail] products.mid_cd 갱신: {item_cd} → {mid_cd}")
        return updated
    except Exception as e:
        logger.error(f"[BatchDetail] mid_cd 갱신 실패 {item_cd}: {e}")
        return False
    finally:
        conn.close()
```

---

## 5. timing.py 변경 상세

기존 `FR_*` (FailReason) 블록 아래에 추가:

```python
# =====================================================================
# 상품 상세 일괄 수집 (product_detail_batch_collector)
# =====================================================================
BD_BARCODE_INPUT_WAIT = 0.3       # 바코드 입력 후 대기 (초)
BD_POPUP_MAX_CHECKS = 10          # 팝업 대기 최대 확인 횟수
BD_POPUP_CHECK_INTERVAL = 0.5     # 팝업 대기 확인 간격 (초)
BD_DATA_LOAD_MAX_CHECKS = 10      # 데이터 로딩 대기 최대 확인 횟수
BD_DATA_LOAD_CHECK_INTERVAL = 0.3  # 데이터 로딩 확인 간격 (초)
BD_POPUP_CLOSE_WAIT = 0.5         # 팝업 닫기 후 대기 (초)
BD_BETWEEN_ITEMS = 2.0            # 상품 간 처리 간격 (초, 서버 부하 방지)
BD_MAX_ITEMS_PER_RUN = 200        # 1회 실행 최대 상품 수
```

---

## 6. run_scheduler.py 변경 상세

### 6-1. wrapper 함수 추가

`order_unit_collect_wrapper()` 바로 아래에 추가:

```python
def detail_fetch_wrapper() -> None:
    """상품 상세 정보 일괄 수집 (매일 01:00)

    BGF 사이트 로그인 → CallItemDetailPopup 일괄 조회 →
    common.db products + product_details 갱신 → 로그아웃

    order_unit_collect_wrapper()와 동일한 패턴:
    BGF 계정 단일이므로 _run_task가 아닌 직접 실행.
    """
    logger.info("=" * 60)
    logger.info(f"Product detail batch fetch at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        from src.sales_analyzer import SalesAnalyzer
        from src.collectors.product_detail_batch_collector import ProductDetailBatchCollector

        sa = SalesAnalyzer()
        sa.login()
        time.sleep(2)

        collector = ProductDetailBatchCollector(
            driver=sa.driver,
            store_id=None,  # common.db 대상
        )

        stats = collector.collect_all()

        logger.info(f"[DetailFetch] 결과: {stats}")

        # 카카오 알림 (선택)
        if stats["success"] > 0:
            try:
                notifier = KakaoNotifier(rest_api_key=DEFAULT_REST_API_KEY)
                notifier.send_to_me(
                    f"상품상세 수집 완료: "
                    f"성공 {stats['success']}/{stats['total']}건"
                )
            except Exception:
                pass

        sa.quit()

    except Exception as e:
        logger.error(f"[DetailFetch] 실패: {e}", exc_info=True)
```

### 6-2. 스케줄 등록 (setup_schedule 내부)

`schedule.every().day.at("00:00").do(order_unit_collect_wrapper)` 바로 아래에:

```python
# 11-2. 상품 상세 일괄 수집 (매일 01:00)
schedule.every().day.at("01:00").do(detail_fetch_wrapper)
logger.info("[Schedule] Product detail batch fetch: 01:00")
```

### 6-3. CLI 옵션 추가

argparse에 추가:

```python
parser.add_argument("--fetch-detail", action="store_true",
                    help="상품 상세 정보 일괄 수집 즉시 실행")
```

main() 분기에 추가:

```python
elif args.fetch_detail:
    init_db()
    detail_fetch_wrapper()
```

---

## 7. 테스트 설계

### 7-1. 파일: `tests/test_product_detail_batch_collector.py`

```python
"""상품 상세 일괄 수집기 테스트"""

# --- 수집 대상 선별 ---
# 1. test_get_items_needing_fetch_empty: 모두 정상 → 빈 리스트
# 2. test_get_items_needing_fetch_null_fetched_at: fetched_at NULL → 포함
# 3. test_get_items_needing_fetch_null_expiry: expiration_days NULL → 포함
# 4. test_get_items_needing_fetch_default_orderable_day: '일월화수목금토' → 포함
# 5. test_get_items_needing_fetch_unknown_mid_cd: mid_cd='999' → 포함
# 6. test_get_items_needing_fetch_limit: limit 적용

# --- DB 저장 ---
# 7. test_bulk_update_from_popup_insert_new: 새 상품 INSERT
# 8. test_bulk_update_from_popup_update_null_only: NULL 필드만 갱신
# 9. test_bulk_update_from_popup_preserve_existing: 기존 값 보존
# 10. test_bulk_update_from_popup_orderable_day_overwrite: '일월화수목금토' → 실제값
# 11. test_bulk_update_from_popup_orderable_day_preserve: 기존 실제값 보존

# --- mid_cd 업데이트 ---
# 12. test_update_mid_cd_from_999: '999' → 실제값
# 13. test_update_mid_cd_preserve_existing: 기존 '001' 보존
# 14. test_update_mid_cd_from_empty: '' → 실제값

# --- 수집 통계 ---
# 15. test_collect_all_empty_list: 빈 리스트 → 수집 안함
# 16. test_stats_counting: success/fail/skip 카운트

# --- 엣지 케이스 ---
# 17. test_null_data_handling: 팝업에서 NULL 반환 시 안전 처리
# 18. test_decimal_type_handling: Decimal 객체 → 문자열 변환
```

### 7-2. 테스트 헬퍼

```python
def _create_test_db():
    """테스트용 common.db 생성 (products + product_details)"""
    ...

def _insert_product(conn, item_cd, mid_cd="999"):
    """products 테이블에 테스트 상품 추가"""
    ...

def _insert_detail(conn, item_cd, **kwargs):
    """product_details에 테스트 데이터 추가"""
    ...
```

---

## 8. 구현 순서

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 1 | BD_* 타이밍 상수 추가 | timing.py | 없음 |
| 2 | discover_popup_columns.py 작성 | scripts/ | 없음 |
| 3 | **Phase 0 실행 (사용자)** | - | 2 |
| 4 | _extract_detail JS 컬럼명 확정 | - | 3 결과 |
| 5 | get_items_needing_detail_fetch() 추가 | product_detail_repo.py | 없음 |
| 6 | bulk_update_from_popup() 추가 | product_detail_repo.py | 없음 |
| 7 | update_product_mid_cd() 추가 | product_detail_repo.py | 없음 |
| 8 | ProductDetailBatchCollector 작성 | collectors/ | 1, 5-7 |
| 9 | detail_fetch_wrapper + 스케줄 + CLI | run_scheduler.py | 8 |
| 10 | 테스트 작성 | tests/ | 5-8 |

> **Step 3은 사용자 개입 필요**: BGF 사이트에서 디스커버리 실행 후 결과 보고

---

## 9. Phase 0 → Phase 1 분기 시나리오

| 시나리오 | MID_CD | sell_price | 조치 |
|----------|:------:|:----------:|------|
| A (최선) | ✅ 있음 | ✅ 있음 | 전체 수집 |
| B (보통) | ✅ 있음 | ❌ 없음 | mid_cd + 기본정보만 수집 |
| C (보통) | ❌ 없음 | ✅ 있음 | 기본정보 + 가격만 수집, mid_cd는 대안 탐색 |
| D (최악) | ❌ 없음 | ❌ 없음 | 기본정보만 수집 (expiry/orderable_day/status) |

모든 시나리오에서 **expiration_days, orderable_day, orderable_status**는 확인 완료 (기존 코드에서 이미 추출 성공)이므로 최소 수집은 보장됨.

---

## 10. 하위 호환성

| 항목 | 영향 |
|------|------|
| ReceivingCollector | ❌ 변경 없음 |
| FailReasonCollector | ❌ 변경 없음 |
| ProductInfoCollector | ❌ 변경 없음 |
| 기존 스케줄 | ❌ 변경 없음 (01:00 신규 추가만) |
| DB 스키마 | ❌ 변경 없음 (기존 테이블/컬럼 활용) |
| 기존 테스트 | ❌ 영향 없음 |
