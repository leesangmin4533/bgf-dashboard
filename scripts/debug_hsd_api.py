"""
HSD API 디버그 스크립트 v2
- 로그인 → STMB010 그리드 로딩 대기 → 실제 UI 흐름 (더블클릭→팝업→상품구성비)
- 캡처된 실제 selPrdT3 body vs 수동 구성 body 비교
- selDay preflight 후 selPrdT3 호출 테스트
"""
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

        # 인터셉터 설치
        driver.execute_script("""
            if (!window.__dbgCaptures) {
                window.__dbgCaptures = [];
            }
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
        logger.info("인터셉터 설치 완료")

        # 매출분석 메뉴 클릭 (DOM MouseEvent)
        result = driver.execute_script("""
            function clickById(id) {
                var el = document.getElementById(id);
                if (!el || el.offsetParent === null) return 'NOT_FOUND: ' + id;
                el.scrollIntoView({block: 'center', inline: 'center'});
                var r = el.getBoundingClientRect();
                var o = {bubbles:true, cancelable:true, view:window,
                    clientX: r.left+r.width/2, clientY: r.top+r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return 'CLICKED: ' + id;
            }
            return clickById(
                'mainframe.HFrameSet00.VFrameSet00.TopFrame.form'
                + '.div_topMenu.form.STMB000_M0:icontext'
            );
        """)
        logger.info(f"매출분석 메뉴: {result}")
        time.sleep(1.5)

        result2 = driver.execute_script("""
            function clickById(id) {
                var el = document.getElementById(id);
                if (!el || el.offsetParent === null) return 'NOT_FOUND: ' + id;
                el.scrollIntoView({block: 'center', inline: 'center'});
                var r = el.getBoundingClientRect();
                var o = {bubbles:true, cancelable:true, view:window,
                    clientX: r.left+r.width/2, clientY: r.top+r.height/2};
                el.dispatchEvent(new MouseEvent('mousedown', o));
                el.dispatchEvent(new MouseEvent('mouseup', o));
                el.dispatchEvent(new MouseEvent('click', o));
                return 'CLICKED: ' + id;
            }
            return clickById(
                'mainframe.HFrameSet00.VFrameSet00.TopFrame.form'
                + '.pdiv_topMenu_STMB000_M0.form.STMB010_M0:text'
            );
        """)
        logger.info(f"시간대별 매출 서브메뉴: {result2}")

        # 그리드 로딩 대기 (최대 30초)
        logger.info("STMB010 그리드 로딩 대기...")
        grid_ready = False
        for i in range(60):
            status = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                    if (!f) return {level: 0, msg: 'FRAME_NOT_FOUND'};
                    if (!f.form) return {level: 0, msg: 'FORM_NOT_LOADED'};
                    var wf = f.form.div_workForm;
                    if (!wf || !wf.form) return {level: 1, msg: 'WORKFORM_NOT_LOADED'};
                    var g = wf.form.gdList;
                    if (!g) return {level: 2, msg: 'GRID_NOT_LOADED'};
                    var ds = wf.form.dsListMain;
                    var rows = ds ? ds.getRowCount() : 0;
                    return {level: 3, msg: 'READY', rows: rows};
                } catch(e) { return {level: -1, msg: 'ERROR: ' + e.message}; }
            """)
            if i % 5 == 0:
                logger.info(f"  [{i*0.5:.0f}초] {status}")
            if status and status.get('level') == 3 and status.get('rows', 0) > 0:
                grid_ready = True
                logger.info(f"그리드 로딩 완료: {status}")
                break
            time.sleep(0.5)

        if not grid_ready:
            logger.warning("그리드 로딩 타임아웃")

        # 캡처 확인
        captures = driver.execute_script("return window.__dbgCaptures || []")
        logger.info(f"캡처된 XHR: {len(captures)}건")
        for i, cap in enumerate(captures):
            url = cap.get('url', '')
            body = cap.get('body', '')
            logger.info(f"  [{i}] URL: {url}")
            logger.info(f"  [{i}] Body ({len(body)}자): {body[:500]}")

        # selDay 템플릿 추출
        selday_body = None
        for cap in captures:
            if 'selDay' in cap.get('url', '') and cap.get('body'):
                selday_body = cap['body']
                break

        if not selday_body:
            logger.error("selDay 템플릿 캡처 실패")
            return

        logger.info(f"selDay body: {selday_body[:300]}")

        # === 테스트 1: selDay 호출 (확인용) ===
        logger.info("=" * 60)
        logger.info("=== 테스트 1: selDay 호출 ===")
        resp1 = driver.execute_async_script("""
            var callback = arguments[arguments.length - 1];
            fetch('https://store.bgfretail.com/stmb010/selDay', {
                method: 'POST',
                headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                body: arguments[0]
            })
            .then(function(r) {
                return r.text().then(function(t) {
                    return {status: r.status, len: t.length, preview: t.substring(0, 300)};
                });
            })
            .then(function(d) { callback(d); })
            .catch(function(e) { callback({error: e.message}); });
        """, selday_body)
        logger.info(f"selDay 응답: status={resp1.get('status')}, len={resp1.get('len')}")
        logger.info(f"  preview: {resp1.get('preview', '')[:300]}")

        if grid_ready:
            # === 테스트 2: 실제 UI 더블클릭 → 팝업 → 상품구성비 탭 ===
            logger.info("=" * 60)
            logger.info("=== 테스트 2: 실제 UI 흐름 (더블클릭 → 팝업 → 상품구성비) ===")

            # 캡처 초기화
            driver.execute_script("window.__dbgCaptures = [];")

            # 10시 데이터 행 더블클릭
            dblclick_result = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var frame = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0;
                    var wf = frame.form.div_workForm.form;
                    var ds = wf.dsListMain;
                    var grid = wf.gdList;

                    // 10시 행 찾기
                    var targetRow = -1;
                    for (var r = 0; r < ds.getRowCount(); r++) {
                        var hms = ds.getColumn(r, 'HMS');
                        if (parseInt(hms) === 10) {
                            targetRow = r;
                            break;
                        }
                    }

                    if (targetRow < 0) return 'HOUR_10_NOT_FOUND';

                    // 해당 행 선택 + 더블클릭 이벤트
                    grid.set_currentrow(targetRow);

                    var evt = {
                        cell: 0, col: 0, row: targetRow,
                        clientx: 100, clienty: 100
                    };

                    if (grid.oncelldblclick && grid.oncelldblclick.fireEvent) {
                        grid.oncelldblclick.fireEvent(grid, evt);
                        return 'DBLCLICKED_ROW_' + targetRow;
                    }
                    return 'NO_DBLCLICK_EVENT';
                } catch(e) { return 'ERROR: ' + e.message; }
            """)
            logger.info(f"더블클릭 결과: {dblclick_result}")
            time.sleep(3)

            # 팝업 확인
            popup_status = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;
                    if (!p0) return 'NO_POPUP';
                    if (!p0.form) return 'POPUP_NO_FORM';
                    return 'POPUP_OPEN';
                } catch(e) { return 'ERROR: ' + e.message; }
            """)
            logger.info(f"팝업 상태: {popup_status}")

            if popup_status == 'POPUP_OPEN':
                # 상품구성비 탭 클릭
                tab_result = driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var p0 = app.mainframe.HFrameSet00.VFrameSet00
                            .FrameSet.STMB010_M0.STMB010_P0;
                        if (p0.form.div_tab_btn_03_onclick) {
                            p0.form.div_tab_btn_03_onclick();
                            return 'TAB_CLICKED';
                        }
                        return 'NO_TAB_HANDLER';
                    } catch(e) { return 'ERROR: ' + e.message; }
                """)
                logger.info(f"상품구성비 탭: {tab_result}")
                time.sleep(3)

                # 캡처 확인 - 실제 selPrdT3 body
                captures2 = driver.execute_script(
                    "return window.__dbgCaptures || []"
                )
                logger.info(f"UI 조작 후 캡처: {len(captures2)}건")

                real_prdt3_body = None
                for i, cap in enumerate(captures2):
                    url = cap.get('url', '')
                    body = cap.get('body', '')
                    logger.info(f"  [{i}] URL: {url}")
                    logger.info(f"  [{i}] Body ({len(body)}자): {body[:500]}")
                    if 'selPrdT3' in url and body:
                        real_prdt3_body = body

                if real_prdt3_body:
                    logger.info("=" * 60)
                    logger.info("=== 실제 selPrdT3 body 캡처 성공! ===")
                    logger.info(f"실제 body ({len(real_prdt3_body)}자):")
                    logger.info(f"  {real_prdt3_body[:500]}")

                    # 파라미터 비교
                    real_parts = real_prdt3_body.split(RS)
                    logger.info(f"실제 body 파라미터 ({len(real_parts)}개):")
                    for p in real_parts:
                        logger.info(f"  - {p[:100]}")

                    # 수동 body와 비교
                    manual = selday_body + RS + 'strHms=10'
                    manual_parts = manual.split(RS)
                    logger.info(f"수동 body 파라미터 ({len(manual_parts)}개):")
                    for p in manual_parts:
                        logger.info(f"  - {p[:100]}")

                    # 차이점 분석
                    real_keys = set()
                    manual_keys = set()
                    for p in real_parts:
                        if '=' in p:
                            real_keys.add(p.split('=')[0])
                    for p in manual_parts:
                        if '=' in p:
                            manual_keys.add(p.split('=')[0])

                    only_real = real_keys - manual_keys
                    only_manual = manual_keys - real_keys
                    if only_real:
                        logger.info(f"실제에만 있는 파라미터: {only_real}")
                    if only_manual:
                        logger.info(f"수동에만 있는 파라미터: {only_manual}")

                    # 실제 body로 selPrdT3 호출
                    logger.info("=== 테스트 2b: 실제 body로 selPrdT3 호출 ===")
                    resp_real = driver.execute_async_script("""
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
                    """, real_prdt3_body)
                    logger.info(
                        f"실제 body 응답: status={resp_real.get('status')}, "
                        f"len={resp_real.get('len')}"
                    )
                    logger.info(f"  preview: {resp_real.get('preview', '')}")

                # 팝업 닫기
                driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var p0 = app.mainframe.HFrameSet00.VFrameSet00
                            .FrameSet.STMB010_M0.STMB010_P0;
                        if (p0 && p0.close) p0.close();
                    } catch(e) {}
                """)

        # === 테스트 3: selDay body + strHms 로 selPrdT3 호출 ===
        logger.info("=" * 60)
        logger.info("=== 테스트 3: selDay body + strHms → selPrdT3 ===")
        prdt3_body = selday_body + RS + 'strHms=10'
        resp3 = driver.execute_async_script("""
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
        """, prdt3_body)
        logger.info(
            f"selDay+strHms 응답: status={resp3.get('status')}, "
            f"len={resp3.get('len')}"
        )
        logger.info(f"  preview: {resp3.get('preview', '')}")

        # === 테스트 4: selDay preflight → selPrdT3 호출 ===
        logger.info("=" * 60)
        logger.info("=== 테스트 4: selDay preflight → selPrdT3 ===")
        # 먼저 selDay 호출
        driver.execute_async_script("""
            var callback = arguments[arguments.length - 1];
            fetch('https://store.bgfretail.com/stmb010/selDay', {
                method: 'POST',
                headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                body: arguments[0]
            })
            .then(function(r) { return r.text(); })
            .then(function(t) { callback('OK:' + t.length); })
            .catch(function(e) { callback('ERR:' + e.message); });
        """, selday_body)
        time.sleep(1)
        # 그 다음 selPrdT3 호출
        resp4 = driver.execute_async_script("""
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
        """, prdt3_body)
        logger.info(
            f"preflight후 응답: status={resp4.get('status')}, "
            f"len={resp4.get('len')}"
        )
        logger.info(f"  preview: {resp4.get('preview', '')}")

    except KeyboardInterrupt:
        logger.info("사용자 중단")
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
