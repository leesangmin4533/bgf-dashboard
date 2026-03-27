"""STMB010 올바른 그리드 경로로 더블클릭 → 팝업 → selPrdT3 캡처"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils.logger import get_logger
from src.infrastructure.database.schema import init_db

logger = get_logger(__name__)
RS = '\u001e'


def main():
    init_db()

    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer(store_id='46513')

    try:
        analyzer.setup_driver()
        analyzer.connect()
        if not analyzer.do_login():
            logger.error("로그인 실패")
            return
        driver = analyzer.driver
        time.sleep(2)
        analyzer.close_popup()
        time.sleep(1)

        # 인터셉터
        driver.execute_script("""
            if (!window.__dbgCaptures) window.__dbgCaptures = [];
            if (!window.__dbgInterceptorInstalled) {
                window.__dbgInterceptorInstalled = true;
                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url) {
                    this.__captureUrl = url;
                    return origOpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function(body) {
                    var url = this.__captureUrl || '';
                    if (url.indexOf('stmb010') >= 0) {
                        window.__dbgCaptures.push({
                            url: url, body: body ? String(body) : ''
                        });
                    }
                    return origSend.apply(this, arguments);
                };
            }
        """)

        # STMB010 이동
        driver.execute_script("""
            function clickById(id) {
                var el = document.getElementById(id);
                if (!el) return false;
                var r = el.getBoundingClientRect();
                var o = {bubbles:true, cancelable:true, view:window,
                    clientX: r.left+r.width/2, clientY: r.top+r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return true;
            }
            clickById(
                'mainframe.HFrameSet00.VFrameSet00.TopFrame.form'
                + '.div_topMenu.form.STMB000_M0:icontext'
            );
        """)
        time.sleep(1.5)
        driver.execute_script("""
            function clickById(id) {
                var el = document.getElementById(id);
                if (!el) return false;
                var r = el.getBoundingClientRect();
                var o = {bubbles:true, cancelable:true, view:window,
                    clientX: r.left+r.width/2, clientY: r.top+r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return true;
            }
            clickById(
                'mainframe.HFrameSet00.VFrameSet00.TopFrame.form'
                + '.pdiv_topMenu_STMB000_M0.form.STMB010_M0:text'
            );
        """)
        time.sleep(8)

        # 캡처 초기화
        driver.execute_script("window.__dbgCaptures = [];")

        # 올바른 그리드 경로: wf.div2.form.gdList (0-11시)
        logger.info("=== 그리드 더블클릭 (wf.div2.form.gdList) ===")
        dblclick = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                var wf = f.form.div_workForm.form;
                var ds = wf.dsListMain;

                // 10시 행 찾기
                var targetRow = -1;
                for (var r = 0; r < ds.getRowCount(); r++) {
                    if (parseInt(ds.getColumn(r, 'HMS')) === 10) {
                        targetRow = r;
                        break;
                    }
                }
                if (targetRow < 0) return 'HOUR_10_NOT_FOUND';

                // rowposition 설정
                ds.set_rowposition(targetRow);

                // gdList (div2 내부)
                var grid = wf.div2.form.gdList;
                if (!grid) return 'gdList_NOT_FOUND_IN_DIV2';

                // 이벤트 발생
                grid.set_currentrow(targetRow);
                var evt = {
                    cell: 0, col: 0, row: targetRow,
                    clientx: 100, clienty: 100
                };

                // 방법 1: 그리드의 oncelldblclick 이벤트
                if (grid.oncelldblclick && grid.oncelldblclick.fireEvent) {
                    grid.oncelldblclick.fireEvent(grid, evt);
                    return 'FIRED_gdList_dblclick_row=' + targetRow;
                }

                // 방법 2: workForm의 핸들러 함수 직접 호출
                if (wf.gdList_oncelldblclick) {
                    wf.gdList_oncelldblclick(grid, evt);
                    return 'CALLED_wf_handler_row=' + targetRow;
                }

                return 'NO_DBLCLICK_HANDLER';
            } catch(e) {
                return 'ERROR: ' + e.message;
            }
        """)
        logger.info(f"더블클릭: {dblclick}")
        time.sleep(3)

        # 팝업 확인
        popup = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                var p0 = f.STMB010_P0;
                if (!p0) return 'NO_POPUP';
                if (!p0.form) return 'POPUP_NO_FORM';

                // 팝업 내부 탭 정보
                var comps = [];
                for (var k in p0.form) {
                    var obj = p0.form[k];
                    if (obj && typeof obj === 'object' && obj.name) {
                        var tn = obj._type_name || '';
                        if (k.indexOf('tab') >= 0 || k.indexOf('btn') >= 0 ||
                            k.indexOf('div') >= 0 || tn === 'Grid' || tn === 'Dataset') {
                            comps.push(k + ':' + tn);
                        }
                    }
                }
                return {status: 'POPUP_OPEN', components: comps};
            } catch(e) { return {error: e.message}; }
        """)
        logger.info(f"팝업: {popup}")

        if isinstance(popup, dict) and popup.get('status') == 'POPUP_OPEN':
            logger.info("팝업 열림! 상품구성비 탭 검색 중...")

            # 탭 버튼 찾기
            tab_info = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;
                    var tabs = [];
                    for (var k in p0.form) {
                        if (typeof p0.form[k] === 'function' &&
                            k.indexOf('btn') >= 0 && k.indexOf('onclick') >= 0) {
                            tabs.push(k);
                        }
                    }
                    // div_tab 관련
                    for (var k2 in p0.form) {
                        if (k2.indexOf('div_tab') >= 0 || k2.indexOf('tab') >= 0) {
                            tabs.push(k2 + ':' + typeof p0.form[k2]);
                        }
                    }
                    return tabs;
                } catch(e) { return {error: e.message}; }
            """)
            logger.info(f"탭 관련: {tab_info}")

            # 상품구성비 탭 클릭 시도
            tab_click = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;

                    // 방법 1: div_tab_btn_03_onclick
                    if (typeof p0.form.div_tab_btn_03_onclick === 'function') {
                        p0.form.div_tab_btn_03_onclick();
                        return 'CALLED_div_tab_btn_03_onclick';
                    }

                    // 방법 2: 탭 관련 함수 검색
                    for (var k in p0.form) {
                        if (typeof p0.form[k] === 'function' &&
                            (k.indexOf('tab_03') >= 0 || k.indexOf('T3') >= 0 ||
                             k.indexOf('prd') >= 0)) {
                            return 'FOUND_' + k;
                        }
                    }

                    return 'NO_TAB_HANDLER';
                } catch(e) { return {error: e.message}; }
            """)
            logger.info(f"탭 클릭: {tab_click}")
            time.sleep(3)

            # 캡처 확인
            captures = driver.execute_script("return window.__dbgCaptures || []")
            logger.info(f"캡처된 XHR: {len(captures)}건")
            for i, cap in enumerate(captures):
                url = cap.get('url', '')
                body = cap.get('body', '')
                logger.info(f"  [{i}] URL: {url}")
                logger.info(f"  [{i}] Body ({len(body)}자): {body[:800]}")

                if 'selPrdT3' in url:
                    logger.info("=" * 60)
                    logger.info("!!! selPrdT3 body 캡처 성공 !!!")
                    # 파라미터 분석
                    parts = body.split(RS)
                    logger.info(f"파라미터 ({len(parts)}개):")
                    for p in parts:
                        logger.info(f"  - {p[:200]}")

                    # 직접 호출 테스트
                    logger.info("=== 캡처된 body로 직접 selPrdT3 호출 ===")
                    resp = driver.execute_async_script("""
                        var callback = arguments[arguments.length - 1];
                        fetch('https://store.bgfretail.com/stmb010/selPrdT3', {
                            method: 'POST',
                            headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                            body: arguments[0]
                        })
                        .then(function(r) {
                            return r.text().then(function(t) {
                                return {status: r.status, len: t.length,
                                        preview: t.substring(0, 500)};
                            });
                        })
                        .then(function(d) { callback(d); })
                        .catch(function(e) { callback({error: e.message}); });
                    """, body)
                    logger.info(
                        f"직접 호출 응답: status={resp.get('status')}, "
                        f"len={resp.get('len')}"
                    )
                    logger.info(f"  preview: {resp.get('preview', '')}")

            # 팝업 닫기
            driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;
                    if (p0 && p0.close) p0.close();
                } catch(e) {}
            """)
        else:
            logger.warning("팝업 미열림!")

            # 핸들러 직접 호출 시도
            logger.info("workForm 핸들러 직접 호출...")
            handler_result = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                    var wf = f.form.div_workForm.form;
                    var ds = wf.dsListMain;
                    var grid = wf.div2.form.gdList;

                    // rowposition 설정
                    var targetRow = 10;
                    ds.set_rowposition(targetRow);

                    // gdList_oncelldblclick 직접 호출
                    if (typeof wf.gdList_oncelldblclick === 'function') {
                        var evt = {cell: 0, col: 0, row: targetRow,
                            clientx: 100, clienty: 100};
                        wf.gdList_oncelldblclick(grid, evt);
                        return 'DIRECT_HANDLER_CALLED';
                    }
                    return 'NO_HANDLER';
                } catch(e) { return 'ERROR: ' + e.message; }
            """)
            logger.info(f"핸들러 호출: {handler_result}")
            time.sleep(3)

            # 다시 팝업 확인
            popup2 = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                    var p0 = f.STMB010_P0;
                    return p0 && p0.form ? 'POPUP_OPEN' : 'NO_POPUP';
                } catch(e) { return 'ERROR: ' + e.message; }
            """)
            logger.info(f"핸들러 후 팝업: {popup2}")

            if popup2 == 'POPUP_OPEN':
                # 상품구성비 탭 클릭
                driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var p0 = app.mainframe.HFrameSet00.VFrameSet00
                            .FrameSet.STMB010_M0.STMB010_P0;
                        if (p0.form.div_tab_btn_03_onclick) {
                            p0.form.div_tab_btn_03_onclick();
                        }
                    } catch(e) {}
                """)
                time.sleep(3)

                captures = driver.execute_script(
                    "return window.__dbgCaptures || []"
                )
                logger.info(f"캡처: {len(captures)}건")
                for i, cap in enumerate(captures):
                    url = cap.get('url', '')
                    body = cap.get('body', '')
                    logger.info(f"  [{i}] URL: {url}")
                    logger.info(f"  [{i}] Body ({len(body)}자): {body[:800]}")

    except KeyboardInterrupt:
        logger.info("중단")
    except Exception as e:
        logger.error(f"에러: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            analyzer.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
