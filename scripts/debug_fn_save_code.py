"""
fn_save 소스코드 분석 — 올바른 프레임 경로 사용
경로: mainframe → HFrameSet00 → VFrameSet00 → FrameSet → STBJ030_M0
"""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.sales_analyzer import SalesAnalyzer
from src.order.order_executor import OrderExecutor
from src.utils.logger import get_logger

logger = get_logger("debug_fnsave")


def main():
    analyzer = SalesAnalyzer()
    try:
        analyzer.setup_driver()
        analyzer.connect()
        analyzer.do_login()
        logger.info("로그인 성공")

        driver = analyzer.driver
        executor = OrderExecutor(driver)
        if not executor.navigate_to_single_order():
            logger.error("메뉴 이동 실패")
            return
        logger.info("메뉴 이동 성공")
        time.sleep(3)

        # STBJ030_M0 접근 + workForm 찾기
        logger.info("\n=== 1. STBJ030_M0 → workForm 접근 ===")
        result = driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var result = {};

            // 올바른 경로
            var stbj = app.mainframe.all.HFrameSet00.all.VFrameSet00.all.FrameSet.all.STBJ030_M0;
            if (!stbj) {
                result.error = 'STBJ030_M0 not found';
                return JSON.stringify(result);
            }
            result.stbj_found = true;
            result.stbj_has_form = !!stbj.form;

            if (stbj.form) {
                // stbjForm 레벨 컴포넌트
                var comps = [];
                if (stbj.form.components) {
                    for (var i = 0; i < stbj.form.components.length; i++) {
                        comps.push(stbj.form.components[i].name || 'unnamed');
                    }
                }
                result.stbj_comps = comps;

                // div_work_01 접근
                var wf = null;
                if (stbj.form.div_work_01) {
                    wf = stbj.form.div_work_01.form;
                    result.workForm_path = 'stbj.form.div_work_01.form';
                }
                // 또는 div_workForm → div_work_01
                if (!wf && stbj.form.div_workForm) {
                    result.has_div_workForm = true;
                    if (stbj.form.div_workForm.form && stbj.form.div_workForm.form.div_work_01) {
                        wf = stbj.form.div_workForm.form.div_work_01.form;
                        result.workForm_path = 'stbj.form.div_workForm.form.div_work_01.form';
                    }
                }

                if (wf) {
                    result.workForm_found = true;
                    result.dsGeneralGrid_rows = wf.dsGeneralGrid ? wf.dsGeneralGrid.getRowCount() : -1;
                    result.dsGeneralGrid_cols = wf.dsGeneralGrid ? wf.dsGeneralGrid.getColCount() : -1;

                    // fv_ 변수
                    var fvVars = {};
                    for (var k in wf) {
                        if ((k.indexOf('fv_') === 0 || k.indexOf('lv') === 0) && typeof wf[k] !== 'function') {
                            fvVars[k] = String(wf[k] || '');
                        }
                    }
                    result.fvVars = fvVars;

                    // fn_save 코드
                    if (wf.fn_save) {
                        result.fn_save = wf.fn_save.toString();
                    } else {
                        result.fn_save = 'NOT FOUND';
                    }

                    // fn_callback 코드
                    if (wf.fn_callback) {
                        result.fn_callback = wf.fn_callback.toString();
                    }

                    // gfn_callback 코드
                    if (wf.gfn_callback) {
                        result.gfn_callback = wf.gfn_callback.toString().substring(0, 2000);
                    }

                    // 발주 관련 함수 목록
                    var funcs = [];
                    for (var fk in wf) {
                        if (typeof wf[fk] === 'function') {
                            var fl = fk.toLowerCase();
                            if (fl.indexOf('ord') >= 0 || fl.indexOf('save') >= 0 ||
                                fl.indexOf('init') >= 0 || fl.indexOf('load') >= 0 ||
                                fl.indexOf('close') >= 0 || fl.indexOf('day') >= 0 ||
                                fl.indexOf('check') >= 0 || fl.indexOf('search') >= 0 ||
                                fl.indexOf('pyunsu') >= 0 || fl.indexOf('validate') >= 0) {
                                funcs.push(fk);
                            }
                        }
                    }
                    result.ordFuncs = funcs;
                } else {
                    result.workForm_found = false;
                }
            }

            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message, stack: e.stack}); }
        """)
        if result:
            r = json.loads(result)
            for k, v in sorted(r.items()):
                if k in ('fn_save', 'fn_callback', 'gfn_callback'):
                    logger.info(f"\n  === {k} ===")
                    for line in str(v).split('\n'):
                        logger.info(f"  {line.rstrip()}")
                elif isinstance(v, (dict, list)):
                    logger.info(f"  {k}:")
                    if isinstance(v, dict):
                        for fk, fv in sorted(v.items()):
                            logger.info(f"    {fk}: '{fv}'")
                    else:
                        for item in v:
                            logger.info(f"    - {item}")
                else:
                    logger.info(f"  {k}: {v}")

        # 2. gfn_transaction으로 selOrdDayList 호출
        logger.info("\n=== 2. gfn_transaction selOrdDayList 호출 ===")
        gfn_result = driver.execute_script("""
        return new Promise(function(resolve) {
            try {
                var app = nexacro.getApplication();
                var stbj = app.mainframe.all.HFrameSet00.all.VFrameSet00.all.FrameSet.all.STBJ030_M0;
                var wf = stbj.form.div_work_01 ? stbj.form.div_work_01.form : null;

                if (!wf) {
                    resolve(JSON.stringify({error: 'workForm not found at stbj.form.div_work_01'}));
                    return;
                }

                // 콜백
                var cbName = '_dbg_cb_' + Date.now();
                wf[cbName] = function(svcId, errCd, errMsg) {
                    var result = {svcId: svcId, errCd: String(errCd), errMsg: String(errMsg)};

                    // dsResult 확인
                    try {
                        var ds = wf.dsResult || stbj.form.dsResult;
                        if (ds) {
                            result.dsResult_rows = ds.getRowCount();
                            if (ds.getRowCount() > 0) {
                                var rows = [];
                                for (var r = 0; r < ds.getRowCount(); r++) {
                                    var row = {};
                                    for (var c = 0; c < ds.getColCount(); c++) {
                                        row[ds.getColID(c)] = String(ds.getColumn(r, ds.getColID(c)) || '');
                                    }
                                    rows.push(row);
                                }
                                result.dsResult_data = rows;
                            }
                        }
                    } catch(e) { result.dsResult_error = e.message; }

                    // dsTmpDate 확인
                    try {
                        var dt = wf.dsTmpDate || stbj.form.dsTmpDate;
                        if (dt && dt.getRowCount() > 0) {
                            result.dsTmpDate = {};
                            for (var tc = 0; tc < dt.getColCount(); tc++) {
                                result.dsTmpDate[dt.getColID(tc)] = String(dt.getColumn(0, dt.getColID(tc)) || '');
                            }
                        }
                    } catch(e) {}

                    delete wf[cbName];
                    resolve(JSON.stringify(result));
                };

                // gfn_transaction 호출
                wf.gfn_transaction(
                    'selOrdDayList',
                    'stbjz00/selOrdDayList',
                    '',
                    'dsResult=dsResult dsTmpDate=dsTmpDate',
                    '',
                    cbName
                );

                setTimeout(function() {
                    if (wf[cbName]) {
                        delete wf[cbName];
                        resolve(JSON.stringify({error: 'timeout 15s'}));
                    }
                }, 15000);
            } catch(e) { resolve(JSON.stringify({error: e.message})); }
        });
        """)
        if gfn_result:
            gr = json.loads(gfn_result)
            for k, v in sorted(gr.items()):
                if isinstance(v, (dict, list)):
                    logger.info(f"  {k}: {json.dumps(v, ensure_ascii=False, indent=2)}")
                else:
                    logger.info(f"  {k}: {v}")

    except Exception as e:
        logger.error(f"오류: {e}", exc_info=True)
    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass
        logger.info("완료")


if __name__ == "__main__":
    main()
