"""
4-6순위 컬렉터 API 캡처 스크립트

receiving_collector (STGJ010_M0), waste_slip_collector (STGJ020_M0),
order_status_collector (STBJ070_M0)의 실제 API 엔드포인트를 캡처합니다.

사용법:
    cd bgf_auto
    python scripts/test_collectors_api_capture.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.utils.nexacro_helpers import navigate_menu, close_tab_by_frame_id
from src.settings.ui_config import FRAME_IDS, MENU_TEXT, SUBMENU_TEXT

# ═══════════════════════════════════════════════════════════════
# XHR/fetch 인터셉터 JS
# ═══════════════════════════════════════════════════════════════

INTERCEPTOR_JS = r"""
(function() {
    if (window.__collectorCaptures) return 'already_installed';

    window.__collectorCaptures = [];

    // 1) fetch 인터셉터
    var origFetch = window.fetch;
    window.fetch = function(url, opts) {
        var entry = {
            type: 'fetch',
            url: String(url),
            method: (opts && opts.method) || 'GET',
            bodyPreview: '',
            timestamp: new Date().toISOString()
        };
        if (opts && opts.body) {
            entry.bodyPreview = String(opts.body).substring(0, 3000);
        }

        var p = origFetch.apply(this, arguments);
        p.then(function(resp) {
            return resp.clone().text().then(function(text) {
                entry.status = resp.status;
                entry.responsePreview = text.substring(0, 2000);
                entry.responseLength = text.length;
            });
        }).catch(function() {});

        window.__collectorCaptures.push(entry);
        return p;
    };

    // 2) XHR 인터셉터
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this._capUrl = String(url);
        this._capMethod = method;
        return origOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        var self = this;
        var entry = {
            type: 'xhr',
            url: self._capUrl || '',
            method: self._capMethod || 'POST',
            bodyPreview: body ? String(body).substring(0, 3000) : '',
            timestamp: new Date().toISOString()
        };

        self.addEventListener('load', function() {
            entry.status = self.status;
            entry.responsePreview = (self.responseText || '').substring(0, 2000);
            entry.responseLength = (self.responseText || '').length;
        });

        window.__collectorCaptures.push(entry);
        return origSend.apply(this, arguments);
    };

    return 'installed';
})();
"""


def get_captures(driver, clear=False):
    """브라우저에서 캡처된 요청 가져오기"""
    result = driver.execute_script("""
        var caps = window.__collectorCaptures || [];
        if (arguments[0]) window.__collectorCaptures = [];
        return caps;
    """, clear)
    return result or []


def print_captures(captures, label=""):
    """캡처된 요청 출력"""
    if label:
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}")

    if not captures:
        print("  (캡처된 요청 없음)")
        return

    for i, cap in enumerate(captures):
        url = cap.get('url', '')
        # 정적 리소스 필터링
        if any(ext in url for ext in ['.js', '.css', '.png', '.gif', '.ico', '.woff']):
            continue
        print(f"\n  [{i+1}] {cap.get('method', '?')} {url}")
        print(f"      status: {cap.get('status', '?')}, "
              f"response: {cap.get('responseLength', '?')} bytes")
        body = cap.get('bodyPreview', '')
        if body:
            print(f"      body: {body[:120]}...")
        resp = cap.get('responsePreview', '')
        if resp:
            print(f"      resp: {resp[:120]}...")


def dismiss_login_popups(driver):
    """로그인 후 모달 팝업 닫기"""
    from src.utils.popup_manager import close_all_popups
    closed = close_all_popups(driver)
    if closed > 0:
        print(f"  popup_manager: {closed}개 닫음")
        time.sleep(1)
        return

    # XPath 폴백
    from selenium.webdriver.common.by import By
    xpaths = ["//*[text()='닫기']", "//button[contains(text(),'닫기')]"]
    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            for elem in elements:
                if elem.is_displayed():
                    elem.click()
                    print(f"  XPath 닫기 클릭: {xpath}")
                    time.sleep(1)
                    return
        except Exception:
            continue


# ═══════════════════════════════════════════════════════════════
# 4순위: receiving_collector (STGJ010_M0)
# ═══════════════════════════════════════════════════════════════

def capture_receiving(driver):
    """센터매입 조회/확정 API 캡처"""
    print("\n" + "=" * 70)
    print("  4순위: receiving_collector (STGJ010_M0)")
    print("=" * 70)

    frame_id = FRAME_IDS["RECEIVING"]

    # 메뉴 이동
    print("\n[1] 메뉴 이동...")
    success = navigate_menu(
        driver,
        MENU_TEXT["RECEIVING"],
        SUBMENU_TEXT["RECEIVING_CENTER"],
        frame_id,
    )
    if not success:
        print("  ❌ 메뉴 이동 실패")
        return []
    print("  ✅ 메뉴 이동 성공")
    time.sleep(3)

    # 팝업 닫기
    dismiss_login_popups(driver)

    # 인터셉터 설치 (메뉴 이동 후)
    print("\n[2] 인터셉터 설치...")
    driver.execute_script(INTERCEPTOR_JS)
    # 이전 캡처 클리어
    get_captures(driver, clear=True)
    time.sleep(0.5)

    # 날짜 목록 조회
    print("\n[3] 날짜 목록 조회 (dsAcpYmd)...")
    dates = driver.execute_script(f"""
        try {{
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            var wf = form.div_workForm && form.div_workForm.form;
            if (!wf || !wf.dsAcpYmd) return {{error: 'dsAcpYmd not found'}};

            var ds = wf.dsAcpYmd;
            var rows = [];
            for (var i = 0; i < ds.getRowCount(); i++) {{
                rows.push({{
                    idx: i,
                    DGFW_YMD: ds.getColumn(i, 'DGFW_YMD'),
                    VIEW_YMD: ds.getColumn(i, 'VIEW_YMD'),
                }});
            }}
            return {{dates: rows}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  날짜 목록: {dates}")

    # 첫 번째 날짜 선택 → API 캡처
    if dates and dates.get('dates') and len(dates['dates']) > 0:
        first_date = dates['dates'][0]
        dgfw_ymd = first_date.get('DGFW_YMD', '')
        idx = first_date.get('idx', 0)
        print(f"\n[4] 날짜 선택: {dgfw_ymd} (idx={idx})...")

        select_result = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
                var wf = form.div_workForm && form.div_workForm.form;
                var cb = wf.div1 && wf.div1.form
                    && wf.div1.form.divSearch && wf.div1.form.divSearch.form
                    && wf.div1.form.divSearch.form.cbAcpYmd;
                if (!cb) return {{error: 'cbAcpYmd not found'}};

                cb.set_index({idx});

                if (typeof wf.div1_divSearch_cbAcpYmd_onitemchanged === 'function') {{
                    wf.div1_divSearch_cbAcpYmd_onitemchanged(cb, {{
                        postindex: {idx}
                    }});
                    return {{success: true, method: 'onitemchanged'}};
                }}
                return {{success: true, method: 'set_index_only'}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)
        print(f"  선택 결과: {select_result}")
        time.sleep(4)

        # dsListPopup 로딩 확인
        popup_check = driver.execute_script(f"""
            try {{
                var form = nexacro.getApplication()
                    .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
                var wf = form.div_workForm && form.div_workForm.form;
                var ds = wf && wf.dsListPopup;
                if (!ds) return {{error: 'dsListPopup not found'}};
                return {{rowCount: ds.getRowCount()}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)
        print(f"  dsListPopup: {popup_check}")

        # 전표 선택 (rowposition 변경) → dsList 로딩 캡처
        if popup_check and popup_check.get('rowCount', 0) > 0:
            print(f"\n[5] 전표 선택 (row 0)...")
            row_result = driver.execute_script(f"""
                try {{
                    var form = nexacro.getApplication()
                        .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
                    var wf = form.div_workForm && form.div_workForm.form;
                    var ds = wf.dsListPopup;
                    ds.set_rowposition(0);
                    return {{success: true}};
                }} catch(e) {{
                    return {{error: e.message}};
                }}
            """)
            print(f"  행 선택 결과: {row_result}")
            time.sleep(3)

    # 캡처 수집
    caps = get_captures(driver, clear=True)
    print_captures(caps, "Receiving API Captures")

    # 탭 닫기
    try:
        close_tab_by_frame_id(driver, frame_id)
        time.sleep(1)
    except Exception:
        pass

    return caps


# ═══════════════════════════════════════════════════════════════
# 5순위: waste_slip_collector (STGJ020_M0)
# ═══════════════════════════════════════════════════════════════

def capture_waste_slip(driver):
    """통합 전표 조회 API 캡처"""
    print("\n" + "=" * 70)
    print("  5순위: waste_slip_collector (STGJ020_M0)")
    print("=" * 70)

    frame_id = FRAME_IDS.get("WASTE_SLIP", "STGJ020_M0")
    today = datetime.now().strftime("%Y%m%d")
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    # 메뉴 이동
    print("\n[1] 메뉴 이동...")
    success = navigate_menu(
        driver,
        MENU_TEXT["RECEIVING"],
        SUBMENU_TEXT["WASTE_SLIP"],
        frame_id,
    )
    if not success:
        print("  ❌ 메뉴 이동 실패")
        return []
    print("  ✅ 메뉴 이동 성공")
    time.sleep(3)

    # 인터셉터 재설치 (탭 전환)
    print("\n[2] 인터셉터 설치...")
    driver.execute_script(INTERCEPTOR_JS)
    get_captures(driver, clear=True)
    time.sleep(0.5)

    # 날짜 + 필터 설정 → F10 검색
    print(f"\n[3] 날짜/필터 설정: {yesterday}~{today}, 폐기(10)...")
    filter_result = driver.execute_script("""
        var fid = arguments[0];
        var fromDt = arguments[1];
        var toDt = arguments[2];
        try {
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var wf = form.div_workForm.form;

            // 전표구분 콤보 = 폐기 (CODE=10)
            var dsChitDiv = wf.dsChitDiv;
            var targetIdx = -1;
            for (var r = 0; r < dsChitDiv.getRowCount(); r++) {
                if (dsChitDiv.getColumn(r, 'CODE') === '10') {
                    targetIdx = r;
                    break;
                }
            }
            if (targetIdx < 0) return {error: 'CODE 10 not found'};

            var cb = wf.div2.form.divSearch.form.cbChitDiv;
            cb.set_index(targetIdx);

            // dsSearch 설정
            var dsSearch = wf.dsSearch;
            dsSearch.setColumn(0, 'strChitDiv', '10');
            dsSearch.setColumn(0, 'strFromDt', fromDt);
            dsSearch.setColumn(0, 'strToDt', toDt);

            return {success: true, comboText: cb.text};
        } catch(e) {
            return {error: e.message};
        }
    """, frame_id, yesterday, today)
    print(f"  필터 결과: {filter_result}")

    # F10 검색
    print("\n[4] F10 조회 실행...")
    search_result = driver.execute_script("""
        var fid = arguments[0];
        try {
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            if (typeof form.fn_commBtn_10 === 'function') {
                form.fn_commBtn_10();
                return {method: 'fn_commBtn_10', success: true};
            }
            var cmmbtn = form.div_cmmbtn;
            if (cmmbtn && cmmbtn.form && cmmbtn.form.F_10) {
                cmmbtn.form.F_10.click();
                return {method: 'F_10', success: true};
            }
            return {error: 'no search method'};
        } catch(e) {
            return {error: e.message};
        }
    """, frame_id)
    print(f"  검색 결과: {search_result}")
    time.sleep(4)

    # dsList 확인
    ds_check = driver.execute_script("""
        var fid = arguments[0];
        try {
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet[fid].form;
            var ds = form.div_workForm.form.dsList;
            if (!ds) return {error: 'dsList not found'};
            return {rowCount: ds.getRowCount()};
        } catch(e) {
            return {error: e.message};
        }
    """, frame_id)
    print(f"  dsList: {ds_check}")

    # 캡처 수집
    caps = get_captures(driver, clear=True)
    print_captures(caps, "Waste Slip API Captures")

    # 탭 닫기
    try:
        close_tab_by_frame_id(driver, frame_id)
        time.sleep(1)
    except Exception:
        pass

    return caps


# ═══════════════════════════════════════════════════════════════
# 6순위: order_status_collector (STBJ070_M0)
# ═══════════════════════════════════════════════════════════════

def capture_order_status(driver):
    """발주 현황 조회 API 캡처"""
    print("\n" + "=" * 70)
    print("  6순위: order_status_collector (STBJ070_M0)")
    print("=" * 70)

    frame_id = FRAME_IDS["ORDER_STATUS"]

    # 인터셉터 미리 설치 (메뉴 이동 시 API도 캡처하기 위해)
    print("\n[1] 인터셉터 설치 (메뉴 이동 전)...")
    driver.execute_script(INTERCEPTOR_JS)
    get_captures(driver, clear=True)

    # 메뉴 이동
    print("\n[2] 메뉴 이동...")
    from src.utils.nexacro_helpers import click_menu_by_text, click_submenu_by_text, wait_for_frame
    if not click_menu_by_text(driver, '발주'):
        print("  ❌ 발주 메뉴 클릭 실패")
        return []
    time.sleep(1)
    if not click_submenu_by_text(driver, '발주 현황 조회'):
        print("  ❌ 서브메뉴 클릭 실패")
        return []
    time.sleep(2)
    if not wait_for_frame(driver, frame_id):
        print("  ❌ 프레임 로딩 실패")
        return []
    print("  ✅ 메뉴 이동 성공")
    time.sleep(3)

    # 초기 로드 시 캡처된 것 수집
    init_caps = get_captures(driver, clear=True)
    print_captures(init_caps, "Order Status - Initial Load Captures")

    # dsResult 확인
    ds_check = driver.execute_script(f"""
        try {{
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            var wf = form.div_workForm.form.div_work.form;
            var res = {{}};
            if (wf.dsResult) res.dsResult = wf.dsResult.getRowCount();
            if (wf.dsOrderSale) res.dsOrderSale = wf.dsOrderSale.getRowCount();
            if (wf.dsWeek) res.dsWeek = wf.dsWeek.getRowCount();
            return res;
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"\n  데이터셋 상태: {ds_check}")

    # 라디오 클릭 (일반=1) → 캡처
    print("\n[3] 일반 라디오 클릭...")
    radio_result = driver.execute_script(f"""
        try {{
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            var wf = form.div_workForm.form.div_work.form;
            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio && radio.set_value) {{
                radio.set_value('1');
                if (radio.on_fire_onitemchanged) {{
                    radio.on_fire_onitemchanged(radio, {{}});
                }}
                return {{success: true, method: 'api'}};
            }}
            return {{error: 'rdGubun not found'}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)
    print(f"  라디오 결과: {radio_result}")
    time.sleep(3)

    # 라디오 변경 후 캡처
    radio_caps = get_captures(driver, clear=True)
    print_captures(radio_caps, "Order Status - Radio Change Captures")

    # "전체" 라디오로 복원
    print("\n[4] 전체 라디오 복원...")
    driver.execute_script(f"""
        try {{
            var form = nexacro.getApplication()
                .mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            var wf = form.div_workForm.form.div_work.form;
            var radio = wf.Div21 && wf.Div21.form && wf.Div21.form.rdGubun;
            if (radio) {{
                radio.set_value('0');
                if (radio.on_fire_onitemchanged) {{
                    radio.on_fire_onitemchanged(radio, {{}});
                }}
            }}
        }} catch(e) {{}}
    """)
    time.sleep(2)

    all_radio_caps = get_captures(driver, clear=True)

    # 탭 닫기
    try:
        close_tab_by_frame_id(driver, frame_id)
        time.sleep(1)
    except Exception:
        pass

    return init_caps + radio_caps + all_radio_caps


# ═══════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  4-6순위 컬렉터 API 캡처 시작")
    print("=" * 70)

    # 로그인
    print("\n[Login] SalesAnalyzer 로그인...")
    analyzer = SalesAnalyzer()
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        print("  ❌ 로그인 실패")
        return

    print("  ✅ 로그인 성공")
    time.sleep(3)

    driver = analyzer.driver

    # 팝업 닫기
    print("\n[Popup] 로그인 팝업 처리...")
    dismiss_login_popups(driver)
    time.sleep(1)

    # 인터셉터 설치
    print("\n[Interceptor] XHR/fetch 인터셉터 설치...")
    result = driver.execute_script(INTERCEPTOR_JS)
    print(f"  결과: {result}")

    all_captures = {}

    # 4순위: receiving
    try:
        caps = capture_receiving(driver)
        all_captures['receiving'] = caps
    except Exception as e:
        print(f"\n  ❌ receiving 캡처 실패: {e}")
        import traceback
        traceback.print_exc()
        all_captures['receiving'] = []

    # 5순위: waste_slip
    try:
        caps = capture_waste_slip(driver)
        all_captures['waste_slip'] = caps
    except Exception as e:
        print(f"\n  ❌ waste_slip 캡처 실패: {e}")
        import traceback
        traceback.print_exc()
        all_captures['waste_slip'] = []

    # 6순위: order_status
    try:
        caps = capture_order_status(driver)
        all_captures['order_status'] = caps
    except Exception as e:
        print(f"\n  ❌ order_status 캡처 실패: {e}")
        import traceback
        traceback.print_exc()
        all_captures['order_status'] = []

    # JSON 저장
    output_path = Path(__file__).parent.parent / "data" / "captures" / "collectors_api_capture.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 직렬화 가능하도록 정리
    serializable = {}
    for key, caps_list in all_captures.items():
        serializable[key] = []
        for cap in caps_list:
            if isinstance(cap, dict):
                serializable[key].append({
                    k: str(v)[:2000] if isinstance(v, str) else v
                    for k, v in cap.items()
                })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 캡처 결과 저장: {output_path}")

    # 요약
    print("\n" + "=" * 70)
    print("  캡처 요약")
    print("=" * 70)
    for key, caps_list in all_captures.items():
        api_caps = [c for c in caps_list if isinstance(c, dict)
                    and not any(ext in c.get('url', '') for ext in ['.js', '.css', '.png'])]
        print(f"  {key}: {len(api_caps)}건")

    print("\n완료! 브라우저를 닫으려면 Enter...")
    try:
        input()
    except EOFError:
        pass

    try:
        driver.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
