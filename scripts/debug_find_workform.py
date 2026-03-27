"""
workForm 경로 찾기 — VFrameSet00 탐색
"""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.sales_analyzer import SalesAnalyzer
from src.order.order_executor import OrderExecutor
from src.utils.logger import get_logger

logger = get_logger("debug_findwf")

FIND_WORKFORM_JS = """
try {
    var app = nexacro.getApplication();
    var mf = app.mainframe;
    var result = {};

    // VFrameSet00 탐색
    try {
        var hfs = mf.all.HFrameSet00;
        var vfs = hfs.all.VFrameSet00;
        if (vfs && vfs.all) {
            var keys = [];
            for (var k in vfs.all) {
                if (k.charAt(0) !== '_') keys.push(k);
            }
            result.vfs_all = keys;

            // 각 자식 프레임 탐색
            for (var ki = 0; ki < keys.length; ki++) {
                var child = vfs.all[keys[ki]];
                if (child && child.all) {
                    var childKeys = [];
                    for (var ck in child.all) {
                        if (ck.charAt(0) !== '_') childKeys.push(ck);
                    }
                    result['vfs.' + keys[ki] + '.all'] = childKeys;
                }
                if (child && child.form) {
                    var compNames = [];
                    if (child.form.components) {
                        for (var ci = 0; ci < child.form.components.length; ci++) {
                            compNames.push(child.form.components[ci].name || 'unnamed');
                        }
                    }
                    result['vfs.' + keys[ki] + '.form.components'] = compNames;
                }
            }
        }
    } catch(e) { result.vfs_error = e.message; }

    // frame_Work 찾기 (보통 VFrameSet00 > frame_Work 또는 WorkFrame)
    try {
        var workFrame = null;
        var vfsAll = vfs.all;
        for (var wk in vfsAll) {
            if (wk.indexOf('Work') >= 0 || wk.indexOf('work') >= 0 || wk.indexOf('frame_W') >= 0) {
                result.workFrame_key = wk;
                workFrame = vfsAll[wk];
                break;
            }
        }
        if (workFrame && workFrame.form) {
            result.workFrame_form_comps = [];
            if (workFrame.form.components) {
                for (var wci = 0; wci < workFrame.form.components.length; wci++) {
                    var wc = workFrame.form.components[wci];
                    result.workFrame_form_comps.push((wc.name || 'unnamed') + '(' + (wc.constructor.name || '') + ')');
                }
            }
            // stbjForm / STBJ030_M0 찾기
            if (workFrame.form.div_workForm) {
                result.div_workForm_found = true;
                if (workFrame.form.div_workForm.form) {
                    result.div_workForm_comps = [];
                    var dwf = workFrame.form.div_workForm.form;
                    if (dwf.components) {
                        for (var di = 0; di < dwf.components.length; di++) {
                            result.div_workForm_comps.push(dwf.components[di].name || 'unnamed');
                        }
                    }
                    // div_work_01 확인
                    if (dwf.div_work_01) {
                        result.div_work_01_found = true;
                        var wform = dwf.div_work_01.form;
                        if (wform) {
                            result.dsGeneralGrid_exists = !!wform.dsGeneralGrid;
                            result.dsGeneralGrid_rows = wform.dsGeneralGrid ? wform.dsGeneralGrid.getRowCount() : -1;
                            result.fv_OrdInputFlag = String(wform.fv_OrdInputFlag || '');
                            result.fv_PyunsuId = String(wform.fv_PyunsuId || '');
                            result.fv_OrdYn = String(wform.fv_OrdYn || '');
                            result.lvOrdYmd = String(wform.lvOrdYmd || '');

                            // 모든 fv_ 변수
                            var fvVars = {};
                            for (var fk in wform) {
                                if ((fk.indexOf('fv_') === 0 || fk.indexOf('lv') === 0) && typeof wform[fk] !== 'function') {
                                    fvVars[fk] = String(wform[fk] || '');
                                }
                            }
                            result.fvVars = fvVars;

                            // fn_save 코드 (처음 3000자)
                            if (wform.fn_save) {
                                result.fn_save = wform.fn_save.toString().substring(0, 3000);
                            }

                            // fn_callback 코드
                            if (wform.fn_callback) {
                                result.fn_callback = wform.fn_callback.toString().substring(0, 2000);
                            }

                            // 관련 함수 목록
                            var funcs = [];
                            for (var ffk in wform) {
                                if (typeof wform[ffk] === 'function') {
                                    var fl = ffk.toLowerCase();
                                    if (fl.indexOf('ord') >= 0 || fl.indexOf('save') >= 0 || fl.indexOf('init') >= 0 || fl.indexOf('load') >= 0 || fl.indexOf('close') >= 0 || fl.indexOf('day') >= 0 || fl.indexOf('check') >= 0) {
                                        funcs.push(ffk);
                                    }
                                }
                            }
                            result.ordRelatedFuncs = funcs;

                            // workForm의 정확한 경로
                            result.workForm_path = 'app.mainframe.all.HFrameSet00.all.VFrameSet00.all.' + result.workFrame_key + '.form.div_workForm.form.div_work_01.form';
                        }
                    }
                }
            }
        }
    } catch(e) { result.workFrame_error = e.message; }

    return JSON.stringify(result);
} catch(e) { return JSON.stringify({error: e.message}); }
"""

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

        result = driver.execute_script(FIND_WORKFORM_JS)
        if result:
            r = json.loads(result)
            for k, v in sorted(r.items()):
                if k in ('fn_save', 'fn_callback'):
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
