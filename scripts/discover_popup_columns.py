"""
CallItemDetailPopup 데이터셋 컬럼 전수 조사

BGF 사이트 로그인 -> 홈 바코드 입력 -> 팝업 열기 -> 컬럼 덤프 -> JSON 저장

목적:
    1. dsItemDetail + dsItemDetailOrd 의 **모든 컬럼명** 실제 덤프
    2. UI 컴포넌트 텍스트 전수 조사
    3. Phase 0 결과 → Plan/Design 업데이트

사용법:
    cd bgf_auto
    python scripts/discover_popup_columns.py --item-cd 8801234567890
    python scripts/discover_popup_columns.py --item-cd 8801234567890 --output columns.json
"""

import sys
import io
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

# Windows CP949 콘솔 -> UTF-8 래핑
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sales_analyzer import SalesAnalyzer
from src.settings.constants import DEFAULT_STORE_ID
from src.settings.timing import (
    FR_BARCODE_INPUT_WAIT, FR_POPUP_MAX_CHECKS, FR_POPUP_CHECK_INTERVAL,
    FR_DATA_LOAD_MAX_CHECKS, FR_DATA_LOAD_CHECK_INTERVAL,
)
from src.settings.ui_config import FAIL_REASON_UI


# ── 전체 컬럼 덤프 JS ──

DUMP_COLUMNS_JS = """
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

// 1. dsItemDetail (메인)
try { datasets.dsItemDetail = dumpDataset(popupForm.dsItemDetail, 'dsItemDetail'); } catch(e) {}

// 2. dsItemDetailOrd (발주 정보)
try { datasets.dsItemDetailOrd = dumpDataset(popupForm.dsItemDetailOrd, 'dsItemDetailOrd'); } catch(e) {}

// 3. popupForm 내 추가 데이터셋 탐색 (unknown ds)
try {
    var knownDs = {'dsItemDetail':1, 'dsItemDetailOrd':1};
    for (var key in popupForm) {
        try {
            var obj = popupForm[key];
            if (obj && typeof obj.getRowCount === 'function' && !knownDs[key]) {
                datasets[key] = dumpDataset(obj, key);
            }
        } catch(e2) {}
    }
} catch(e) {}

// 4. UI 컴포넌트 텍스트 (divInfo, divInfo01, divInfo02, divDetail 등)
var uiTexts = {};
try {
    var divs = ['divInfo', 'divInfo01', 'divInfo02', 'divDetail', 'divOrd'];
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
            // form 직접 접근도 시도
            for (var fk in div.form) {
                try {
                    var fc = div.form[fk];
                    if (fc && (fc.text || fc.value) && typeof fc !== 'function') {
                        var ftxt = fc.text || fc.value || '';
                        if (ftxt && !uiTexts[divs[d] + '.' + fk]) {
                            uiTexts[divs[d] + '.' + fk] = String(ftxt);
                        }
                    }
                } catch(e3) {}
            }
        }
    }
} catch(e) {}

return {success: true, datasets: datasets, uiTexts: uiTexts};
"""


def _input_barcode(driver, item_cd: str) -> bool:
    """edt_pluSearch에 바코드 입력 (4단계 폴백)"""
    result = driver.execute_script("""
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


def _trigger_search(driver) -> bool:
    """Enter 키 이벤트 발생 → 팝업 트리거"""
    result = driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            var edt = wf.form.edt_pluSearch;
            if (edt) {
                // nexacro KeyEvent
                var keyEvt = new nexacro.KeyEventInfo(edt, 'onkeyup', 13, false, false, false);
                edt.on_fire_user_onkeyup(keyEvt);
                return {success: true, method: 'nexacro_key'};
            }
        } catch(e) {}
        // fallback: DOM keyup
        try {
            var domId = 'mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame.form.edt_pluSearch:input';
            var el = document.getElementById(domId);
            if (el) {
                el.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', keyCode: 13, bubbles: true}));
                return {success: true, method: 'dom_keyup'};
            }
        } catch(e) {}
        return {success: false};
    """)
    return result and result.get("success", False)


def _wait_for_popup(driver, popup_id: str) -> bool:
    """CallItemDetailPopup 출현 폴링"""
    for _ in range(FR_POPUP_MAX_CHECKS):
        result = driver.execute_script("""
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
        time.sleep(FR_POPUP_CHECK_INTERVAL)
    return False


def _wait_for_data(driver, popup_id: str) -> bool:
    """dsItemDetail 데이터 로딩 대기"""
    for _ in range(FR_DATA_LOAD_MAX_CHECKS):
        result = driver.execute_script("""
            var pid = arguments[0];
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                var popup = wf[pid]
                         || (wf.popupframes && wf.popupframes[pid])
                         || (wf.form && wf.form[pid]);
                if (popup && popup.form && popup.form.dsItemDetail) {
                    if (popup.form.dsItemDetail.getRowCount() > 0) return {loaded: true};
                }
            } catch(e) {}
            return {loaded: false};
        """, popup_id)
        if result and result.get("loaded"):
            return True
        time.sleep(FR_DATA_LOAD_CHECK_INTERVAL)
    return False


def _close_popup(driver, popup_id: str) -> None:
    """CallItemDetailPopup 닫기"""
    driver.execute_script("""
        var popupId = arguments[0];
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
        try {
            var btns = document.querySelectorAll('[id*="' + popupId + '"][id*="btn_close"]');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].offsetParent !== null) { btns[i].click(); return; }
            }
        } catch(e) {}
    """, popup_id)


def discover(item_cd: str, output_path: str = None, store_id: str = DEFAULT_STORE_ID) -> dict:
    """CallItemDetailPopup 전체 컬럼 덤프

    Args:
        item_cd: 바코드 (상품코드)
        output_path: JSON 출력 경로 (None이면 콘솔만)
        store_id: 매장 코드

    Returns:
        덤프 결과 dict
    """
    popup_id = FAIL_REASON_UI["POPUP_ID"]

    analyzer = SalesAnalyzer(store_id=store_id)
    result_data = {
        "item_cd": item_cd,
        "timestamp": datetime.now().isoformat(),
        "datasets": {},
        "uiTexts": {},
    }

    try:
        # 0. 로그인
        print("\n" + "=" * 70)
        print("  [0] BGF 사이트 로그인")
        print("=" * 70)

        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return result_data

        analyzer.close_popup()
        time.sleep(2)

        driver = analyzer.driver

        # 1. 바코드 입력
        print(f"\n  [1] 바코드 입력: {item_cd}")
        if not _input_barcode(driver, item_cd):
            # ActionChains 폴백
            print("    -> JS 입력 실패, ActionChains 시도")
            from selenium.webdriver.common.action_chains import ActionChains
            from selenium.webdriver.common.keys import Keys

            dom_id = FAIL_REASON_UI["BARCODE_INPUT_DOM"]
            el = driver.find_element("id", dom_id)
            el.clear()
            ActionChains(driver).click(el).send_keys(item_cd).perform()

        time.sleep(FR_BARCODE_INPUT_WAIT)

        # 2. Enter → 팝업 트리거
        print("  [2] Enter 키 전송 → 팝업 트리거")
        if not _trigger_search(driver):
            # ActionChains 폴백
            from selenium.webdriver.common.action_chains import ActionChains
            from selenium.webdriver.common.keys import Keys

            dom_id = FAIL_REASON_UI["BARCODE_INPUT_DOM"]
            el = driver.find_element("id", dom_id)
            ActionChains(driver).click(el).send_keys(Keys.ENTER).perform()

        time.sleep(1.0)

        # 3. 팝업 대기
        print(f"  [3] 팝업 대기 (최대 {FR_POPUP_MAX_CHECKS * FR_POPUP_CHECK_INTERVAL:.1f}초)...")
        if not _wait_for_popup(driver, popup_id):
            print(f"    [WARN] 팝업 미출현 - Quick Search 드롭다운 클릭 시도")
            # Quick Search 드롭다운 클릭 시도
            driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                    if (wf.form.btn_pluQuickSearch) wf.form.btn_pluQuickSearch.click();
                } catch(e) {}
            """)
            time.sleep(1.0)

            if not _wait_for_popup(driver, popup_id):
                print("    [ERROR] 팝업 최종 미출현")
                return result_data

        print("    -> 팝업 출현 확인!")

        # 4. 데이터 로딩 대기
        print("  [4] 데이터 로딩 대기...")
        if not _wait_for_data(driver, popup_id):
            print("    [WARN] 데이터 로딩 타임아웃 (비어있을 수 있음)")

        # 5. 전체 컬럼 덤프
        print("  [5] 데이터셋 전체 컬럼 덤프 실행")
        dump = driver.execute_script(DUMP_COLUMNS_JS, popup_id)

        if dump and dump.get("success"):
            result_data["datasets"] = dump.get("datasets", {})
            result_data["uiTexts"] = dump.get("uiTexts", {})
            print("    -> 덤프 성공!")
        else:
            print(f"    [ERROR] 덤프 실패: {dump}")

        # 6. 팝업 닫기
        _close_popup(driver, popup_id)
        time.sleep(0.5)

        # 7. 결과 출력
        print("\n" + "=" * 70)
        print("  결과 분석")
        print("=" * 70)

        for ds_name, ds_info in result_data["datasets"].items():
            cols = ds_info.get("columns", [])
            rows = ds_info.get("rowCount", 0)
            print(f"\n  [{ds_name}] ({len(cols)} columns, {rows} rows)")
            print("  " + "-" * 60)
            for col in cols:
                val_str = col.get("value", "None")
                if val_str and len(str(val_str)) > 50:
                    val_str = str(val_str)[:50] + "..."
                print(f"    {col['index']:3d}. {col['name']:25s} = {val_str}")

        if result_data["uiTexts"]:
            print(f"\n  [UI 텍스트] ({len(result_data['uiTexts'])} items)")
            print("  " + "-" * 60)
            for k, v in result_data["uiTexts"].items():
                v_str = str(v)[:50] + "..." if len(str(v)) > 50 else str(v)
                print(f"    {k:40s} = {v_str}")

        # 핵심 필드 확인
        print("\n" + "=" * 70)
        print("  핵심 필드 존재 여부")
        print("=" * 70)

        target_fields = {
            "MID_CD (중분류)": ["MID_CD", "MCLS_CD", "M_CATE_CD"],
            "SELL_PRC (판매가)": ["SELL_PRC", "SELL_PRICE", "MAEGA_AMT"],
            "COST_PRC (원가)": ["WONGA_AMT", "COST_PRC", "BUGA_AMT"],
            "MARGIN_RATE (이익률)": ["MARGIN_RATE", "IYUL"],
            "LEAD_TIME (리드타임)": ["LEAD_TIME", "NAIP_TERM"],
            "EXPIRE_DAY (유통기한)": ["EXPIRE_DAY"],
            "ORD_ADAY (발주가능요일)": ["ORD_ADAY"],
            "VENDOR (거래처)": ["CUST_CD", "CUST_NM"],
        }

        all_col_names = set()
        for ds_info in result_data["datasets"].values():
            for col in ds_info.get("columns", []):
                all_col_names.add(col["name"])

        for field_desc, candidates in target_fields.items():
            found = [c for c in candidates if c in all_col_names]
            if found:
                print(f"  OK  {field_desc}: {', '.join(found)}")
            else:
                print(f"  --  {field_desc}: 미발견 (후보: {', '.join(candidates)})")

        # 8. JSON 저장
        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(result_data, f, ensure_ascii=False, indent=2)
            print(f"\n  결과 저장: {out}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            analyzer.close()
        except Exception:
            pass

    return result_data


def main():
    parser = argparse.ArgumentParser(
        description="CallItemDetailPopup 데이터셋 컬럼 전수 조사"
    )
    parser.add_argument(
        "--item-cd", required=True,
        help="조사할 상품 바코드 (예: 8801234567890)"
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="결과 JSON 저장 경로 (기본: 콘솔만 출력)"
    )
    parser.add_argument(
        "--store-id", default=DEFAULT_STORE_ID,
        help=f"매장 코드 (기본: {DEFAULT_STORE_ID})"
    )
    args = parser.parse_args()

    discover(
        item_cd=args.item_cd,
        output_path=args.output,
        store_id=args.store_id,
    )


if __name__ == "__main__":
    main()
