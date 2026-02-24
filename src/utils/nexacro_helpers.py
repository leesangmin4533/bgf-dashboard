"""
넥사크로 UI 조작 헬퍼
- 공통 JavaScript 함수 제공
- MouseEvent 시뮬레이션
- 메뉴/서브메뉴 클릭
"""

from typing import Any, Optional


# 공통 MouseEvent 클릭 시뮬레이션 JavaScript
JS_CLICK_HELPER = """
function clickElement(el) {
    if (!el) return false;
    el.scrollIntoView({block: 'center'});
    const r = el.getBoundingClientRect();
    const o = {
        bubbles: true, cancelable: true, view: window,
        clientX: r.left + r.width/2, clientY: r.top + r.height/2
    };
    el.dispatchEvent(new MouseEvent('mousedown', o));
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.dispatchEvent(new MouseEvent('click', o));
    return true;
}
"""


def click_menu_by_text(driver: Any, menu_text: str) -> bool:
    """
    상단 메뉴에서 텍스트로 메뉴 클릭

    Args:
        driver: Selenium WebDriver
        menu_text: 메뉴 텍스트 (예: '발주', '매출분석')

    Returns:
        클릭 성공 여부
    """
    result = driver.execute_script(JS_CLICK_HELPER + """
        const menus = document.querySelectorAll('[id*="div_topMenu"] [id*=":icontext"]');
        for (const el of menus) {
            const text = (el.innerText || '').trim();
            if (text === arguments[0]) {
                clickElement(el);
                return {success: true, text: text};
            }
        }
        return {success: false};
    """, menu_text)
    return bool(result and result.get('success'))


def click_submenu_by_text(driver: Any, submenu_text: str) -> bool:
    """
    서브메뉴에서 텍스트로 클릭

    Args:
        driver: Selenium WebDriver
        submenu_text: 서브메뉴 텍스트 (예: '단품별 발주', '발주 현황 조회')

    Returns:
        클릭 성공 여부
    """
    result = driver.execute_script(JS_CLICK_HELPER + """
        const submenus = document.querySelectorAll('[id*="pdiv_topMenu"] [id*=":text"]');
        for (const el of submenus) {
            const text = (el.innerText || '').trim();
            if (text.includes(arguments[0]) && el.offsetParent !== null) {
                clickElement(el);
                return {success: true, text: text};
            }
        }
        return {success: false};
    """, submenu_text)
    return bool(result and result.get('success'))


def wait_for_frame(driver: Any, frame_id: str, max_wait: int = 10, interval: float = 0.5) -> bool:
    """
    넥사크로 프레임 로딩 대기

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID (예: 'STBJ030_M0')
        max_wait: 최대 대기 횟수
        interval: 대기 간격 (초)

    Returns:
        로딩 완료 여부
    """
    import time
    for _ in range(max_wait):
        frame_check = driver.execute_script("""
            try {
                const app = nexacro.getApplication();
                const frameSet = app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet;
                if (frameSet?.[arguments[0]]?.form) {
                    return {loaded: true};
                }
                return {loaded: false};
            } catch(e) {
                return {error: e.message};
            }
        """, frame_id)
        if frame_check and frame_check.get('loaded'):
            return True
        time.sleep(interval)
    return False


def close_tab_by_frame_id(driver: Any, frame_id: str) -> bool:
    """
    프레임 ID로 탭 닫기 (nexacro MdiFrame 탭)

    넥사크로 MdiFrame의 tab_openList에서 해당 프레임 탭의 extrabutton(X)을
    클릭하여 닫는다. extrabutton 클릭이 nexacro 이벤트 핸들러를 트리거하여
    탭 버튼 + 프레임 콘텐츠를 모두 정리한다.

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID (예: 'STBJ070_M0', 'STBJ030_M0')

    Returns:
        닫기 성공 여부
    """
    result = driver.execute_script(JS_CLICK_HELPER + """
        var fid = arguments[0];

        // 1차: DOM에서 프레임의 tab_openList를 찾고 해당 탭의 extrabutton 클릭
        // extrabutton 클릭이 nexacro 닫기 이벤트를 트리거하여 콘텐츠까지 정리
        try {
            var frameEl = document.querySelector('[id*="tab_openList"][id$=".' + fid + '"]');
            if (frameEl) {
                var tabListId = frameEl.id.substring(0, frameEl.id.lastIndexOf('.' + fid));
                var extras = document.querySelectorAll(
                    '[id^="' + tabListId + '.tabbutton_"][id$=".extrabutton"]'
                );
                for (var k = 0; k < extras.length; k++) {
                    if (extras[k].offsetParent !== null) {
                        clickElement(extras[k]);
                        return {success: true, method: 'extrabutton', id: extras[k].id};
                    }
                }
            }
        } catch(e) {}

        // 2차: 모든 tab_openList에서 frame_id를 포함하는 탭의 extrabutton 스캔
        try {
            var allExtras = document.querySelectorAll(
                '[id*="tab_openList"][id*=".extrabutton"]:not([id*=":"])'
            );
            for (var m = 0; m < allExtras.length; m++) {
                var ex = allExtras[m];
                var parts = ex.id.split('.tabbutton_');
                if (parts.length < 2) continue;
                var listPath = parts[0];
                var check = document.querySelector('[id="' + listPath + '.' + fid + '"]');
                if (check && ex.offsetParent !== null) {
                    clickElement(ex);
                    return {success: true, method: 'extrabutton_scan', id: ex.id};
                }
            }
        } catch(e) {}

        // 3차: nexacro API - fn_tabClose 이벤트 시뮬레이션
        try {
            var mdi = nexacro.getApplication().mainframe.HFrameSet00.VFrameSet00.MdiFrame;
            if (mdi && mdi.form) {
                // fn_tabClose가 있으면 호출 (사이트 자체 닫기 함수)
                if (typeof mdi.form.fn_tabClose === 'function') {
                    mdi.form.fn_tabClose(fid);
                    return {success: true, method: 'fn_tabClose'};
                }
                // gfn_closeFrame이 있으면 호출
                var app = nexacro.getApplication();
                if (typeof app.gfn_closeFrame === 'function') {
                    app.gfn_closeFrame(fid);
                    return {success: true, method: 'gfn_closeFrame'};
                }
            }
        } catch(e) {}

        return {success: false};
    """, frame_id)
    return bool(result and result.get('success'))


def get_dataset_value(driver: Any, frame_id: str, ds_path: str, dataset_name: str, row: int, col: str) -> Any:
    """
    넥사크로 데이터셋에서 값 조회

    Args:
        driver: Selenium WebDriver
        frame_id: 프레임 ID
        ds_path: 데이터셋 접근 경로
        dataset_name: 데이터셋 이름
        row: 행 인덱스
        col: 컬럼명

    Returns:
        데이터셋 값 또는 None
    """
    result = driver.execute_script("""
        try {
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet[arguments[0]].form;
            const dsPathParts = arguments[1].split('.');
            let wf = form;
            for (const p of dsPathParts) { wf = wf?.[p]; }
            const ds = wf?.[arguments[2]];
            if (!ds) return null;
            let val = ds.getColumn(arguments[3], arguments[4]);
            if (val && typeof val === 'object' && val.hi !== undefined) {
                val = val.hi;
            }
            return val;
        } catch(e) {
            return null;
        }
    """, frame_id, ds_path, dataset_name, row, col)
    return result


def navigate_menu(driver: Any, menu_text: str, submenu_text: str, frame_id: str) -> bool:
    """
    메뉴 → 서브메뉴 클릭 → 프레임 로딩 대기 복합 함수

    Args:
        driver: Selenium WebDriver
        menu_text: 상단 메뉴 텍스트 (예: '발주', '검수전표')
        submenu_text: 서브메뉴 텍스트 (예: '단품별 발주', '센터매입')
        frame_id: 대기할 프레임 ID (예: 'STBJ030_M0')

    Returns:
        메뉴 이동 성공 여부
    """
    import time
    from src.settings.timing import (
        MENU_CLICK_DELAY,
        SUBMENU_CLICK_DELAY,
        FRAME_LOAD_MAX_CHECKS,
        FRAME_LOAD_INTERVAL,
    )

    # 1. 메뉴 클릭
    if not click_menu_by_text(driver, menu_text):
        return False

    time.sleep(MENU_CLICK_DELAY)

    # 2. 서브메뉴 클릭
    if not click_submenu_by_text(driver, submenu_text):
        return False

    time.sleep(SUBMENU_CLICK_DELAY)

    # 3. 프레임 로딩 대기
    return wait_for_frame(driver, frame_id, FRAME_LOAD_MAX_CHECKS, FRAME_LOAD_INTERVAL)
