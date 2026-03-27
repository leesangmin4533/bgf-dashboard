"""
CallItemDetailPopup API 엔드포인트 캡처 스크립트

로그인 → 팝업 닫기 → 인터셉터 설치 → 바코드 입력 → 팝업 트리거 → API 캡처

사용법:
    python scripts/test_popup_api_capture.py --barcode 8801234567890
    python scripts/test_popup_api_capture.py --barcode 8801234567890 --count 1
"""

import sys
import io
import json
import time
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime

# Windows CP949 콘솔 -> UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.utils.popup_manager import close_all_popups
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────
# XHR 인터셉터 (body + response 캡처)
# ──────────────────────────────────────────────────────────────────
INTERCEPTOR_JS = r"""
(function() {
    if (window.__popupCaptures) return 'already';
    window.__popupCaptures = [];

    // XHR 인터셉터
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this.__method = method;
        this.__url = url;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var xhr = this;
        var bodyStr = '';
        try { bodyStr = (body || '').toString(); } catch(e) {}
        xhr.addEventListener('load', function() {
            try {
                var resp = '';
                try { resp = xhr.responseText || ''; } catch(e) {}
                window.__popupCaptures.push({
                    type: 'xhr',
                    method: xhr.__method || '',
                    url: xhr.__url || '',
                    status: xhr.status,
                    bodyLen: bodyStr.length,
                    bodyPreview: bodyStr.substring(0, 8000),
                    respLen: resp.length,
                    respPreview: resp.substring(0, 5000),
                    ts: new Date().toISOString()
                });
            } catch(e) {}
        });
        return origSend.apply(this, arguments);
    };

    // fetch 인터셉터
    var origFetch = window.fetch;
    if (origFetch) {
        window.fetch = function(input, init) {
            var url = typeof input === 'string' ? input : (input && input.url) || '';
            var body = '';
            try { body = (init && init.body) ? init.body.toString() : ''; } catch(e) {}
            var entry = {
                type: 'fetch',
                url: url,
                method: (init && init.method) || 'GET',
                bodyLen: body.length,
                bodyPreview: body.substring(0, 8000),
                ts: new Date().toISOString()
            };
            return origFetch.apply(this, arguments).then(function(resp) {
                var cloned = resp.clone();
                cloned.text().then(function(text) {
                    entry.status = resp.status;
                    entry.respLen = text.length;
                    entry.respPreview = text.substring(0, 5000);
                    window.__popupCaptures.push(entry);
                }).catch(function(){});
                return resp;
            });
        };
    }

    return 'ok';
})()
"""


def get_captures(driver):
    """캡처된 요청 목록 조회"""
    try:
        return driver.execute_script("return window.__popupCaptures || []")
    except Exception:
        return []


def clear_captures(driver):
    """캡처 목록 초기화"""
    driver.execute_script("window.__popupCaptures = []")


def print_captures(captures, title=""):
    """캡처된 요청 출력"""
    print(f"\n{'='*70}")
    print(f" {title}")
    print(f"{'='*70}")
    print(f" 총 {len(captures)}건 캡처됨\n")

    for i, cap in enumerate(captures):
        cap_type = cap.get('type', '?')
        ts = cap.get('ts', '')

        print(f"  [{i+1}] {cap_type.upper()} {cap.get('method', '')} {cap.get('url', '')} @ {ts}")
        print(f"      status   : {cap.get('status', '')}")
        print(f"      body     : {cap.get('bodyLen', 0)} bytes")
        if cap.get('bodyPreview'):
            preview = cap['bodyPreview'][:500]
            print(f"      bodyPrev : {preview}")
        print(f"      response : {cap.get('respLen', 0)} bytes")
        if cap.get('respPreview'):
            resp_preview = cap['respPreview'][:500]
            print(f"      respPrev : {resp_preview}")
        print()


def dismiss_login_popups(driver, max_attempts=5):
    """로그인 후 차단 팝업 모두 닫기

    1) popup_manager.close_all_popups (Selenium 물리 클릭)
    2) XPath로 '닫기', '확인' 버튼 클릭
    3) Alert 처리
    """
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoAlertPresentException

    for attempt in range(max_attempts):
        closed = 0

        # 1) popup_manager
        try:
            n = close_all_popups(driver, silent=True)
            closed += n
        except Exception:
            pass

        # 2) XPath 패턴
        xpaths = [
            "//*[text()='닫기']",
            "//*[text()='확인']",
            "//*[@id[contains(., 'btn_close')]]",
        ]
        for xpath in xpaths:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for elem in elements:
                    if elem.is_displayed():
                        try:
                            elem.click()
                            closed += 1
                            time.sleep(0.3)
                        except Exception:
                            pass
            except Exception:
                pass

        # 3) Alert
        try:
            from selenium.webdriver.common.alert import Alert
            alert = Alert(driver)
            alert.accept()
            closed += 1
        except (NoAlertPresentException, Exception):
            pass

        if closed == 0:
            break  # 더 이상 닫을 것 없음
        print(f"  팝업/알림 {closed}개 닫음 (attempt {attempt+1})")
        time.sleep(0.5)


def trigger_popup_barcode(driver, item_cd):
    """방법 1: 바코드 입력 → Enter → Quick Search → 팝업

    프로덕션 product_detail_batch_collector 동일 패턴.
    """
    # 바코드 입력 (3단계 폴백)
    barcode_dom_id = (
        "mainframe.HFrameSet00.VFrameSet00.FrameSet."
        "WorkFrame.form.edt_pluSearch:input"
    )
    result = driver.execute_script("""
        var barcode = arguments[0];
        var domId = arguments[1];
        // 1차: nexacro
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            if (wf && wf.form && wf.form.edt_pluSearch) {
                wf.form.edt_pluSearch.set_value(barcode);
                return {success: true, method: 'nexacro'};
            }
        } catch(e) {}
        // 2차: DOM ID
        try {
            var el = document.getElementById(domId);
            if (el) {
                el.value = barcode;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return {success: true, method: 'dom_id'};
            }
        } catch(e) {}
        // 3차: querySelector
        try {
            var inputs = document.querySelectorAll(
                '[id*="edt_pluSearch"] input, [id*="edt_pluSearch"]');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].offsetParent !== null) {
                    inputs[i].value = barcode;
                    inputs[i].dispatchEvent(new Event('input', {bubbles: true}));
                    return {success: true, method: 'querySelector'};
                }
            }
        } catch(e) {}
        return {success: false};
    """, item_cd, barcode_dom_id)

    if not (result and result.get("success")):
        print(f"  [FAIL] 바코드 입력 실패")
        return False
    print(f"  바코드 입력 OK ({result.get('method')})")
    time.sleep(0.3)

    # Enter 키 트리거 (2단계 폴백)
    enter_result = driver.execute_script("""
        var domId = arguments[0];
        // 1차: nexacro event
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            var edt = wf.form.edt_pluSearch;
            if (edt && typeof edt.on_fire_onkeyup === 'function') {
                var evt = new nexacro.KeyEventInfo(
                    edt, 'onkeyup', 13, false, false, false, false);
                edt.on_fire_onkeyup(edt, evt);
                return {success: true, method: 'nexacro_event'};
            }
        } catch(e) {}
        // 2차: DOM keyevent
        try {
            var el = document.getElementById(domId);
            if (!el) {
                var els = document.querySelectorAll(
                    '[id*="edt_pluSearch"] input');
                el = els.length > 0 ? els[0] : null;
            }
            if (el) {
                el.dispatchEvent(new KeyboardEvent('keyup', {
                    key: 'Enter', code: 'Enter',
                    keyCode: 13, which: 13, bubbles: true
                }));
                return {success: true, method: 'dom_keyevent'};
            }
        } catch(e) {}
        return {success: false};
    """, barcode_dom_id)

    print(f"  Enter 트리거: {enter_result}")
    time.sleep(0.5)

    # Quick Search 첫 항목 클릭 (2단계 폴백)
    qs_result = driver.execute_script("""
        // 1차: nexacro grid
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            if (wf && wf.form) {
                var grids = ['grd_quickSearch', 'grd_pluSearch', 'Grid00'];
                for (var i = 0; i < grids.length; i++) {
                    var g = wf.form[grids[i]];
                    if (g && g._currow !== undefined) {
                        g.set_focusrow(0);
                        if (typeof g.on_fire_oncellclick === 'function') {
                            g.on_fire_oncellclick(g, 0, 0);
                            return {success: true, method: grids[i]};
                        }
                    }
                }
            }
        } catch(e) {}
        // 2차: DOM cell 클릭
        try {
            var cells = document.querySelectorAll(
                '[id*="WorkFrame"][id*="quickSearch"] [id*="body"] [id*="cell_"],' +
                '[id*="WorkFrame"][id*="pluSearch"] [id*="body"] [id*="cell_"],' +
                '[id*="WorkFrame"][id*="Quick"] [id*="cell_"]');
            if (cells.length > 0) {
                var el = cells[0];
                el.scrollIntoView({block: 'center'});
                var r = el.getBoundingClientRect();
                var o = {bubbles: true, cancelable: true, view: window,
                         clientX: r.left + r.width/2,
                         clientY: r.top + r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return {success: true, method: 'dom_cell'};
            }
        } catch(e) {}
        return {success: false};
    """)
    print(f"  Quick Search: {qs_result}")
    time.sleep(0.5)

    # 팝업 대기 (최대 10초)
    return wait_for_popup(driver)


def trigger_popup_fn(driver, item_cd):
    """방법 2: fn_ItemDetail 직접 호출 (promotion_collector 패턴)"""
    result = driver.execute_script("""
        var itemCd = arguments[0];
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            var form = wf.form;
            if (form.fn_ItemDetail) {
                form.fn_ItemDetail(itemCd);
                return {success: true, method: 'fn_ItemDetail'};
            }
            if (form.gfn_ItemDetail) {
                form.gfn_ItemDetail(itemCd);
                return {success: true, method: 'gfn_ItemDetail'};
            }
        } catch(e) {
            return {success: false, error: e.message};
        }
        return {success: false, error: 'no function found'};
    """, item_cd)

    print(f"  fn_ItemDetail: {result}")
    if not (result and result.get("success")):
        return False

    time.sleep(1.0)
    return wait_for_popup(driver)


def trigger_popup_selenium(driver, item_cd):
    """방법 3: Selenium ActionChains 실제 키보드/클릭 (최후 수단)

    넥사크로 교훈: JS DOM 조작 불가 → 실제 키보드 이벤트만 동작
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains

    barcode_dom_id = (
        "mainframe.HFrameSet00.VFrameSet00.FrameSet."
        "WorkFrame.form.edt_pluSearch:input"
    )

    try:
        el = driver.find_element(By.ID, barcode_dom_id)
    except Exception:
        # querySelector 폴백
        try:
            el = driver.find_element(
                By.CSS_SELECTOR,
                '[id*="edt_pluSearch"] input, [id*="edt_pluSearch"]'
            )
        except Exception:
            print("  [FAIL] 바코드 입력창 없음")
            return False

    if not el.is_displayed():
        print("  [FAIL] 바코드 입력창 비표시")
        return False

    # ActionChains로 실제 클릭 → 클리어 → 타이핑 → Enter
    actions = ActionChains(driver)
    actions.click(el)
    actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
    actions.send_keys(item_cd)
    actions.send_keys(Keys.ENTER)
    actions.perform()
    print(f"  ActionChains 바코드+Enter 완료")

    time.sleep(1.0)

    # Quick Search 드롭다운 DOM 셀 찾아서 실제 클릭
    try:
        cells = driver.find_elements(
            By.CSS_SELECTOR,
            '[id*="WorkFrame"][id*="quick"] [id*="cell_"],'
            '[id*="WorkFrame"][id*="Quick"] [id*="cell_"],'
            '[id*="WorkFrame"][id*="plu"] [id*="cell_"]'
        )
        for cell in cells:
            if cell.is_displayed():
                ActionChains(driver).click(cell).perform()
                print(f"  Quick Search DOM 셀 클릭 OK")
                break
    except Exception as e:
        print(f"  Quick Search 셀 클릭 실패: {e}")

    time.sleep(1.0)
    return wait_for_popup(driver)


def wait_for_popup(driver, max_checks=20, interval=0.5):
    """CallItemDetailPopup 출현 폴링"""
    for i in range(max_checks):
        found = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                if (wf['CallItemDetailPopup'] && wf['CallItemDetailPopup'].form) return true;
                if (wf.popupframes && wf.popupframes['CallItemDetailPopup']) return true;
                if (wf.form && wf.form['CallItemDetailPopup']) return true;
            } catch(e) {}
            // DOM 폴백
            var el = document.querySelector('[id$="CallItemDetailPopup.form"]');
            if (el && el.offsetParent !== null) return true;
            return false;
        """)
        if found:
            print(f"  [OK] CallItemDetailPopup 출현 ({(i+1)*interval:.1f}초)")
            return True
        time.sleep(interval)

    print(f"  [WARN] CallItemDetailPopup 미출현 ({max_checks*interval:.0f}초 대기)")
    return False


def close_popup(driver):
    """CallItemDetailPopup 닫기 (3단계 폴백, 프로덕션 동일)"""
    driver.execute_script("""
        var popupId = 'CallItemDetailPopup';
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
        // 2차: DOM
        try {
            var btns = document.querySelectorAll('[id*="CallItemDetailPopup"][id*="btn_close"]');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].offsetParent !== null) {
                    btns[i].click();
                    return;
                }
            }
        } catch(e) {}
    """)
    time.sleep(0.5)


def get_sample_barcodes():
    """common.db에서 샘플 바코드 조회"""
    db_path = project_root / "data" / "common.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT p.item_cd FROM products p
            JOIN product_details pd ON p.item_cd = pd.item_cd
            WHERE p.item_cd IS NOT NULL AND p.item_cd != ''
            AND pd.orderable_status IS NOT NULL
            LIMIT 5
        """)
        codes = [row[0] for row in cursor.fetchall()]
        conn.close()
        return codes
    except Exception as e:
        print(f"  [WARN] DB 조회 실패: {e}")
        return []


def diagnose_home_screen(driver):
    """홈 화면 상태 진단"""
    info = driver.execute_script("""
        var result = {};
        try {
            var app = nexacro.getApplication();
            result.app = true;
            var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
            result.workFrame = !!wf;
            result.form = !!(wf && wf.form);
            if (wf && wf.form) {
                result.edt_pluSearch = !!wf.form.edt_pluSearch;
                result.fn_ItemDetail = typeof wf.form.fn_ItemDetail;
                result.gfn_ItemDetail = typeof wf.form.gfn_ItemDetail;
                // 열려있는 팝업 확인
                var popups = [];
                if (wf.popupframes) {
                    var keys = Object.keys(wf.popupframes);
                    popups = keys;
                }
                result.popupframes = popups;
                // form 메서드 목록 (함수인 것만, 앞 50개)
                var methods = [];
                for (var k in wf.form) {
                    if (typeof wf.form[k] === 'function') methods.push(k);
                }
                result.formMethods = methods.slice(0, 50);
                // form 객체 이름 중 'grd_' 포함
                var grids = [];
                for (var k in wf.form) {
                    if (k.indexOf('grd_') === 0 || k.indexOf('Grid') === 0) {
                        grids.push(k + ':' + typeof wf.form[k]);
                    }
                }
                result.grids = grids;
            }
        } catch(e) {
            result.error = e.message;
        }
        // 모달 오버레이 확인
        var overlays = document.querySelectorAll('.nexamodaloverlay, [class*="modal"]');
        result.overlayCount = overlays.length;
        var visibleOverlays = [];
        for (var i = 0; i < overlays.length; i++) {
            if (overlays[i].offsetParent !== null || overlays[i].style.display !== 'none') {
                visibleOverlays.push(overlays[i].className);
            }
        }
        result.visibleOverlays = visibleOverlays;
        return result;
    """)
    return info


def main():
    parser = argparse.ArgumentParser(description="CallItemDetailPopup API 캡처")
    parser.add_argument('--barcode', '-b', type=str, default=None,
                        help='테스트 바코드 (기본: DB에서 자동)')
    parser.add_argument('--count', '-c', type=int, default=1,
                        help='캡처할 팝업 수 (기본: 1)')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='결과 저장 경로')
    parser.add_argument('--method', '-m', type=str, default='all',
                        choices=['barcode', 'fn', 'selenium', 'all'],
                        help='팝업 트리거 방법 (기본: all = 순서대로 시도)')
    args = parser.parse_args()

    output_path = args.output or str(
        project_root / 'data' / 'captures' / 'popup_api_capture.json')

    print("=" * 70)
    print(" CallItemDetailPopup API 캡처 v2")
    print("=" * 70)

    analyzer = None
    all_captures = {
        'metadata': {
            'started_at': datetime.now().isoformat(),
        },
        'captures': [],
    }

    try:
        # ──── 1. 로그인 ────
        print("\n[1/5] SalesAnalyzer 초기화 + 로그인...")
        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return

        print("[OK] 로그인 성공")
        time.sleep(3)  # 홈 화면 로딩 대기

        # ──── 2. 로그인 후 팝업 닫기 ────
        print("\n[2/5] 로그인 후 팝업/오버레이 닫기...")
        dismiss_login_popups(analyzer.driver)
        time.sleep(1)

        # ──── 3. 홈 화면 진단 ────
        print("\n[3/5] 홈 화면 상태 진단...")
        diag = diagnose_home_screen(analyzer.driver)
        print(f"  app={diag.get('app')}, workFrame={diag.get('workFrame')}, "
              f"form={diag.get('form')}")
        print(f"  edt_pluSearch={diag.get('edt_pluSearch')}")
        print(f"  fn_ItemDetail={diag.get('fn_ItemDetail')}, "
              f"gfn_ItemDetail={diag.get('gfn_ItemDetail')}")
        print(f"  grids={diag.get('grids')}")
        print(f"  popupframes={diag.get('popupframes')}")
        print(f"  overlays={diag.get('overlayCount')} "
              f"(visible={diag.get('visibleOverlays')})")

        # ──── 4. 인터셉터 설치 ────
        print("\n[4/5] XHR/fetch 인터셉터 설치...")
        result = analyzer.driver.execute_script(INTERCEPTOR_JS)
        print(f"  인터셉터: {result}")

        # ──── 5. 바코드 준비 + 팝업 트리거 ────
        if args.barcode:
            barcodes = [args.barcode]
        else:
            barcodes = get_sample_barcodes()
            if not barcodes:
                print("[ERROR] 샘플 바코드 없음. --barcode 옵션 사용")
                return
            print(f"  DB에서 {len(barcodes)}개 바코드 로드")

        num_items = min(args.count, len(barcodes))
        print(f"\n[5/5] {num_items}개 상품 팝업 캡처...")

        for i, barcode in enumerate(barcodes[:num_items]):
            print(f"\n  {'─'*50}")
            print(f"  [{i+1}/{num_items}] 바코드: {barcode}")
            print(f"  {'─'*50}")

            clear_captures(analyzer.driver)
            popup_found = False

            # 방법별 시도
            if args.method in ('barcode', 'all'):
                print("\n  [방법1] 바코드 입력 → Enter → Quick Search")
                popup_found = trigger_popup_barcode(analyzer.driver, barcode)

            if not popup_found and args.method in ('fn', 'all'):
                print("\n  [방법2] fn_ItemDetail 직접 호출")
                popup_found = trigger_popup_fn(analyzer.driver, barcode)

            if not popup_found and args.method in ('selenium', 'all'):
                print("\n  [방법3] Selenium ActionChains")
                popup_found = trigger_popup_selenium(analyzer.driver, barcode)

            if popup_found:
                # 데이터 로딩 대기
                time.sleep(2.0)

                # 캡처 수집
                captures = get_captures(analyzer.driver)
                print(f"\n  캡처: {len(captures)}건")
                print_captures(captures, f"팝업 #{i+1} ({barcode})")
                all_captures['captures'].extend(captures)

                # 팝업 데이터도 추출 (확인용)
                popup_data = analyzer.driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.WorkFrame;
                        var popup = wf['CallItemDetailPopup']
                                 || (wf.popupframes && wf.popupframes['CallItemDetailPopup']);
                        if (popup && popup.form) {
                            var datasets = {};
                            // dsItemDetail
                            if (popup.form.dsItemDetail) {
                                var ds = popup.form.dsItemDetail;
                                var cols = [];
                                for (var c = 0; c < ds.getColCount(); c++) {
                                    cols.push(ds.getColID(c));
                                }
                                var rows = [];
                                for (var r = 0; r < ds.getRowCount(); r++) {
                                    var row = {};
                                    for (var c = 0; c < cols.length; c++) {
                                        var v = ds.getColumn(r, cols[c]);
                                        if (v && typeof v === 'object' && v.hi !== undefined) v = String(v.hi);
                                        row[cols[c]] = v;
                                    }
                                    rows.push(row);
                                }
                                datasets['dsItemDetail'] = {columns: cols, rows: rows};
                            }
                            // dsItemDetailOrd
                            if (popup.form.dsItemDetailOrd) {
                                var ds = popup.form.dsItemDetailOrd;
                                var cols = [];
                                for (var c = 0; c < ds.getColCount(); c++) {
                                    cols.push(ds.getColID(c));
                                }
                                var rows = [];
                                for (var r = 0; r < ds.getRowCount(); r++) {
                                    var row = {};
                                    for (var c = 0; c < cols.length; c++) {
                                        var v = ds.getColumn(r, cols[c]);
                                        if (v && typeof v === 'object' && v.hi !== undefined) v = String(v.hi);
                                        row[cols[c]] = v;
                                    }
                                    rows.push(row);
                                }
                                datasets['dsItemDetailOrd'] = {columns: cols, rows: rows};
                            }
                            return datasets;
                        }
                    } catch(e) { return {error: e.message}; }
                    return null;
                """)
                if popup_data:
                    print(f"\n  팝업 데이터셋:")
                    for ds_name, ds_info in popup_data.items():
                        if ds_name == 'error':
                            print(f"    error: {ds_info}")
                        else:
                            cols = ds_info.get('columns', [])
                            rows = ds_info.get('rows', [])
                            print(f"    {ds_name}: {len(cols)} cols, {len(rows)} rows")
                            print(f"    컬럼: {cols}")
                            if rows:
                                print(f"    첫 행: {rows[0]}")

                # 팝업 닫기
                close_popup(analyzer.driver)
                time.sleep(0.5)
            else:
                print(f"\n  [FAIL] 팝업 트리거 실패 - 모든 방법 시도 완료")
                # Alert 처리
                try:
                    from selenium.webdriver.common.alert import Alert
                    alert = Alert(analyzer.driver)
                    print(f"  [Alert] {alert.text}")
                    alert.accept()
                except Exception:
                    pass

        # ──── 결과 저장 ────
        all_captures['metadata']['completed_at'] = datetime.now().isoformat()
        all_captures['metadata']['total_captures'] = len(all_captures['captures'])

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_captures, f, ensure_ascii=False, indent=2)

        print(f"\n결과 저장: {output_path} ({len(all_captures['captures'])}건)")

        # 요약
        print(f"\n{'='*70}")
        print(" 캡처 요약")
        print(f"{'='*70}")

        urls = set()
        for cap in all_captures['captures']:
            url = cap.get('url', '')
            if url:
                urls.add(url)

        if urls:
            print(f"  발견된 엔드포인트:")
            for url in sorted(urls):
                print(f"    - {url}")
        else:
            print("  발견된 엔드포인트 없음")

        print(f"\n{'='*70}")

    except KeyboardInterrupt:
        print("\n[중단] 사용자 중단")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if analyzer and analyzer.driver:
            print("\n브라우저 종료...")
            try:
                analyzer.driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
