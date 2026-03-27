"""STMB010 div1/div2 내부 그리드 구조 확인 + 실제 더블클릭 테스트"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils.logger import get_logger
from src.infrastructure.database.schema import init_db

logger = get_logger(__name__)


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

        # div1/div2 내부 구조 확인
        result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                var wf = f.form.div_workForm.form;
                var info = {};

                // div1 내부 컴포넌트
                var d1 = wf.div1;
                if (d1 && d1.form) {
                    var d1Comps = [];
                    for (var k in d1.form) {
                        var obj = d1.form[k];
                        if (obj && typeof obj === 'object' && obj.name) {
                            var tn = obj._type_name || '';
                            d1Comps.push(k + ':' + tn);
                        }
                    }
                    info.div1 = d1Comps;
                } else {
                    info.div1 = d1 ? 'NO_FORM' : 'NULL';
                }

                // div2 내부 컴포넌트
                var d2 = wf.div2;
                if (d2 && d2.form) {
                    var d2Comps = [];
                    for (var k in d2.form) {
                        var obj = d2.form[k];
                        if (obj && typeof obj === 'object' && obj.name) {
                            var tn = obj._type_name || '';
                            d2Comps.push(k + ':' + tn);
                        }
                    }
                    info.div2 = d2Comps;
                } else {
                    info.div2 = d2 ? 'NO_FORM' : 'NULL';
                }

                // dsListMain 데이터 샘플
                var ds = wf.dsListMain;
                if (ds) {
                    info.dsRows = ds.getRowCount();
                    var colNames = [];
                    for (var c = 0; c < ds.getColCount(); c++) {
                        colNames.push(ds.getColID(c));
                    }
                    info.dsCols = colNames;
                    // 10시 데이터
                    for (var r = 0; r < ds.getRowCount(); r++) {
                        if (parseInt(ds.getColumn(r, 'HMS')) === 10) {
                            var row = {};
                            for (var c2 = 0; c2 < ds.getColCount(); c2++) {
                                var cn = ds.getColID(c2);
                                row[cn] = ds.getColumn(r, cn);
                            }
                            info.row10 = row;
                            info.row10_idx = r;
                            break;
                        }
                    }
                }

                return info;
            } catch(e) {
                return {error: e.message};
            }
        """)
        logger.info("STMB010 div1/div2 구조:")
        if isinstance(result, dict):
            for k, v in result.items():
                if isinstance(v, list):
                    logger.info(f"  {k}: ({len(v)}개)")
                    for item in v:
                        logger.info(f"    - {item}")
                elif isinstance(v, dict):
                    logger.info(f"  {k}:")
                    for kk, vv in v.items():
                        logger.info(f"    {kk}: {vv}")
                else:
                    logger.info(f"  {k}: {v}")

        # 그리드 찾기 시도 (div1, div2 내부)
        grid_info = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                var wf = f.form.div_workForm.form;
                var result = {};

                // div1 내부에서 Grid 찾기
                var d1 = wf.div1;
                if (d1 && d1.form) {
                    for (var k in d1.form) {
                        var obj = d1.form[k];
                        if (obj && obj._type_name === 'Grid') {
                            result.div1Grid = k;
                            result.div1GridRows = obj._currow !== undefined ? obj._currow : -1;
                            // 이벤트 확인
                            result.div1GridDblClick = obj.oncelldblclick ? 'EXISTS' : 'NULL';
                        }
                    }
                }

                // div2 내부
                var d2 = wf.div2;
                if (d2 && d2.form) {
                    for (var k in d2.form) {
                        var obj = d2.form[k];
                        if (obj && obj._type_name === 'Grid') {
                            result.div2Grid = k;
                            result.div2GridDblClick = obj.oncelldblclick ? 'EXISTS' : 'NULL';
                        }
                    }
                }

                // workForm 직접 그리드 (gdListSaleRank)
                var gr = wf.gdListSaleRank;
                if (gr) {
                    result.gdListSaleRankDblClick = gr.oncelldblclick ? 'EXISTS' : 'NULL';
                }

                return result;
            } catch(e) {
                return {error: e.message};
            }
        """)
        logger.info(f"그리드 정보: {grid_info}")

        # 캡처 초기화
        driver.execute_script("window.__dbgCaptures = [];")

        # 더블클릭 이벤트 시뮬레이션 (그리드 경로 수정)
        logger.info("=== 더블클릭 시뮬레이션 (수정된 경로) ===")
        dblclick_result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                var wf = f.form.div_workForm.form;

                // div1에서 그리드 찾기
                var grid = null;
                var gridName = '';
                if (wf.div1 && wf.div1.form) {
                    for (var k in wf.div1.form) {
                        var obj = wf.div1.form[k];
                        if (obj && obj._type_name === 'Grid') {
                            grid = obj;
                            gridName = 'div1.' + k;
                            break;
                        }
                    }
                }

                if (!grid) {
                    // 직접 gdListSaleRank 사용
                    grid = wf.gdListSaleRank;
                    gridName = 'gdListSaleRank';
                }

                if (!grid) return 'NO_GRID_FOUND';

                // dsListMain에서 10시 행 찾기
                var ds = wf.dsListMain;
                var targetRow = -1;
                for (var r = 0; r < ds.getRowCount(); r++) {
                    if (parseInt(ds.getColumn(r, 'HMS')) === 10) {
                        targetRow = r;
                        break;
                    }
                }

                if (targetRow < 0) return 'HOUR_10_NOT_FOUND';

                // 현재 행 설정
                ds.set_rowposition(targetRow);

                // 더블클릭 이벤트
                var evt = {
                    cell: 0, col: 0, row: targetRow,
                    clientx: 100, clienty: 100
                };

                if (grid.oncelldblclick && grid.oncelldblclick.fireEvent) {
                    grid.oncelldblclick.fireEvent(grid, evt);
                    return 'DBLCLICKED_' + gridName + '_ROW_' + targetRow;
                }

                return 'NO_DBLCLICK_EVENT_ON_' + gridName;
            } catch(e) {
                return 'ERROR: ' + e.message;
            }
        """)
        logger.info(f"더블클릭: {dblclick_result}")
        time.sleep(3)

        # 팝업 확인
        popup_check = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                // 팝업 프레임 확인
                var names = [];
                for (var k in f) {
                    if (k.indexOf('P0') >= 0 || k.indexOf('P1') >= 0 || k.indexOf('popup') >= 0) {
                        names.push(k);
                    }
                }
                var p0 = f.STMB010_P0;
                return {
                    popupNames: names,
                    p0: p0 ? (p0.form ? 'OPEN' : 'NO_FORM') : 'NULL'
                };
            } catch(e) { return {error: e.message}; }
        """)
        logger.info(f"팝업 상태: {popup_check}")

        if popup_check.get('p0') == 'OPEN':
            logger.info("팝업 열림! 상품구성비 탭 클릭...")
            tab_result = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var p0 = app.mainframe.HFrameSet00.VFrameSet00
                        .FrameSet.STMB010_M0.STMB010_P0;

                    // 팝업 form 내부 컴포넌트
                    var comps = [];
                    for (var k in p0.form) {
                        var obj = p0.form[k];
                        if (obj && typeof obj === 'object' && obj.name) {
                            var tn = obj._type_name || '';
                            comps.push(k + ':' + tn);
                        }
                    }

                    // 상품구성비 탭 클릭 시도
                    var clicked = false;
                    if (p0.form.div_tab_btn_03_onclick) {
                        p0.form.div_tab_btn_03_onclick();
                        clicked = true;
                    }

                    return {
                        components: comps.slice(0, 20),
                        tabClicked: clicked
                    };
                } catch(e) { return {error: e.message}; }
            """)
            logger.info(f"탭 클릭 결과: {tab_result}")
            time.sleep(3)
        else:
            logger.info("팝업 미열림 - div_workForm의 이벤트 핸들러 직접 호출 시도")
            # form.div_workForm의 oncelldblclick 핸들러 확인
            handler_check = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                    var wf = f.form.div_workForm.form;

                    // 이벤트 핸들러 확인
                    var handlers = {};
                    for (var k in wf) {
                        if (typeof wf[k] === 'function' && k.indexOf('dblclick') >= 0) {
                            handlers[k] = 'function';
                        }
                        if (typeof wf[k] === 'function' && k.indexOf('popup') >= 0) {
                            handlers[k] = 'function';
                        }
                    }

                    // form 레벨 함수 확인
                    for (var k2 in f.form) {
                        if (typeof f.form[k2] === 'function' &&
                            (k2.indexOf('dblclick') >= 0 ||
                             k2.indexOf('popup') >= 0 ||
                             k2.indexOf('Detail') >= 0 ||
                             k2.indexOf('detail') >= 0)) {
                            handlers['form.' + k2] = 'function';
                        }
                    }

                    return handlers;
                } catch(e) { return {error: e.message}; }
            """)
            logger.info(f"핸들러: {handler_check}")

        # 캡처 확인
        captures = driver.execute_script("return window.__dbgCaptures || []")
        logger.info(f"캡처된 XHR: {len(captures)}건")
        for i, cap in enumerate(captures):
            url = cap.get('url', '')
            body = cap.get('body', '')
            logger.info(f"  [{i}] URL: {url}")
            logger.info(f"  [{i}] Body ({len(body)}자): {body[:500]}")

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
