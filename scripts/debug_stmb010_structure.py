"""STMB010 workForm 구조 조사"""
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

        # 매출분석 → 시간대별 매출
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

        # workForm 구조 조사
        result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                if (!f) return {error: 'FRAME_NOT_FOUND'};
                if (!f.form) return {error: 'FORM_NOT_LOADED'};

                var info = {};

                // form 직속 컴포넌트
                var formComps = [];
                for (var k in f.form) {
                    var obj = f.form[k];
                    if (obj && typeof obj === 'object' && obj.name) {
                        var typeName = obj._type_name || '';
                        if (!typeName && obj.constructor) typeName = obj.constructor.name || '';
                        formComps.push(k + ':' + typeName);
                    }
                }
                info.formComponents = formComps;

                // div_workForm 확인
                var wf = f.form.div_workForm;
                if (!wf) {
                    info.workForm = 'NOT_FOUND';
                } else if (!wf.form) {
                    info.workForm = 'NO_FORM';
                } else {
                    info.workForm = 'OK';
                    var wfComps = [];
                    for (var k2 in wf.form) {
                        var obj2 = wf.form[k2];
                        if (obj2 && typeof obj2 === 'object' && obj2.name) {
                            var tn2 = obj2._type_name || '';
                            if (!tn2 && obj2.constructor) tn2 = obj2.constructor.name || '';
                            wfComps.push(k2 + ':' + tn2);
                        }
                    }
                    info.workFormComponents = wfComps;

                    // 데이터셋 확인
                    var ds = wf.form.dsListMain;
                    info.dsListMain = ds ? 'rows=' + ds.getRowCount() : 'NOT_FOUND';
                }

                // fn_search 등 함수 확인
                info.fn_search = typeof f.form.fn_search;
                info.fn_onload = typeof f.form.fn_onload;

                return info;
            } catch(e) {
                return {error: e.message};
            }
        """)
        logger.info(f"STMB010 구조:")
        if isinstance(result, dict):
            for k, v in result.items():
                if isinstance(v, list):
                    logger.info(f"  {k}: ({len(v)}개)")
                    for item in v:
                        logger.info(f"    - {item}")
                else:
                    logger.info(f"  {k}: {v}")

        # fn_search 호출 시도
        logger.info("fn_search 호출 시도...")
        search_result = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                if (f.form.fn_search) {
                    f.form.fn_search();
                    return 'fn_search CALLED';
                }
                return 'fn_search NOT_FOUND';
            } catch(e) {
                return 'ERROR: ' + e.message;
            }
        """)
        logger.info(f"fn_search 결과: {search_result}")
        time.sleep(5)

        # fn_search 후 다시 확인
        result2 = driver.execute_script("""
            try {
                var app = nexacro.getApplication();
                var f = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.STMB010_M0;
                if (!f || !f.form) return {error: 'NO_FORM'};
                var wf = f.form.div_workForm;
                if (!wf || !wf.form) return {error: 'NO_WORKFORM'};

                var gd = wf.form.gdList;
                var gd2 = wf.form.gdList2;
                var ds = wf.form.dsListMain;

                return {
                    gdList: gd ? 'EXISTS' : 'NULL',
                    gdList2: gd2 ? 'EXISTS' : 'NULL',
                    dsListMain: ds ? 'rows=' + ds.getRowCount() : 'NULL'
                };
            } catch(e) {
                return {error: e.message};
            }
        """)
        logger.info(f"fn_search 후 상태: {result2}")

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
