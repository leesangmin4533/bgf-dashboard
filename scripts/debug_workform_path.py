"""
workForm 접근 경로 확인 + gfn_transaction으로 selOrdDayList 호출 + fn_save 코드 분석
"""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_wf")


def main():
    from src.sales_analyzer import SalesAnalyzer
    from src.order.order_executor import OrderExecutor

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

        # 1. Frame 구조 탐색
        logger.info("\n=== 1. Frame 구조 탐색 ===")
        frame_result = driver.execute_script(r"""
        try {
            var app = nexacro.getApplication();
            var result = {};

            // mainframe 정보
            result.mainframe_name = app.mainframe ? app.mainframe.name : 'null';
            result.mainframe_type = app.mainframe ? app.mainframe.constructor.name : 'null';

            // childframe 탐색
            var mf = app.mainframe;
            if (mf) {
                // 여러 접근 패턴 시도
                var paths = {};

                // 패턴 A: mf.childframe
                try { paths['mf.childframe'] = mf.childframe ? 'exists' : 'null'; } catch(e) { paths['mf.childframe'] = 'error: ' + e.message; }

                // 패턴 B: mf._childframes
                try { paths['mf._childframes'] = mf._childframes ? mf._childframes.length : 'null'; } catch(e) { paths['mf._childframes'] = 'error: ' + e.message; }

                // 패턴 C: mf.frame
                try { paths['mf.frame'] = mf.frame ? 'exists' : 'null'; } catch(e) { paths['mf.frame'] = 'error: ' + e.message; }

                // 패턴 D: mf.all 프레임
                try {
                    if (mf.all) {
                        var frameNames = [];
                        for (var k in mf.all) {
                            if (typeof mf.all[k] !== 'function') frameNames.push(k);
                        }
                        paths['mf.all_keys'] = frameNames.slice(0, 20);
                    }
                } catch(e) { paths['mf.all'] = 'error: ' + e.message; }

                // 패턴 E: 직접 이름 접근
                var childNames = ['childframe', 'frame_Work', 'work', 'stbjForm', 'STBJ030_M0'];
                for (var i = 0; i < childNames.length; i++) {
                    try {
                        var obj = mf[childNames[i]];
                        if (obj) paths['mf.' + childNames[i]] = obj.name || obj.id || 'exists';
                    } catch(e) {}
                }

                result.paths = paths;
            }

            // ChildFrame 체인 탐색
            try {
                var cf = mf.childframe || mf._childframes && mf._childframes[0];
                if (cf) {
                    result.cf_name = cf.name || 'unnamed';
                    result.cf_has_form = cf.form ? true : false;
                    if (cf.form) {
                        var formComps = [];
                        if (cf.form.components) {
                            for (var ci = 0; ci < cf.form.components.length; ci++) {
                                var c = cf.form.components[ci];
                                formComps.push((c.name || c.id || 'unknown') + '(' + (c.constructor.name || '') + ')');
                            }
                        }
                        result.cf_form_components = formComps;
                    }
                }
            } catch(e) { result.cf_chain_error = e.message; }

            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        if frame_result:
            fr = json.loads(frame_result)
            for k, v in sorted(fr.items()):
                if isinstance(v, (dict, list)):
                    logger.info(f"  {k}:")
                    if isinstance(v, dict):
                        for fk, fv in sorted(v.items()):
                            logger.info(f"    {fk}: {fv}")
                    else:
                        for item in v:
                            logger.info(f"    - {item}")
                else:
                    logger.info(f"  {k}: {v}")

        # 2. 올바른 workForm 경로 찾기
        logger.info("\n=== 2. workForm 경로 찾기 ===")
        wf_path = driver.execute_script(r"""
        try {
            var app = nexacro.getApplication();
            var result = {};

            // 방법 1: stbjForm 전역 접근
            try {
                var stbjForm = null;
                // nexacro._findObj 시도
                if (typeof nexacro._findObj === 'function') {
                    stbjForm = nexacro._findObj('stbjForm');
                    result['nexacro._findObj'] = stbjForm ? 'found' : 'null';
                }
            } catch(e) { result['_findObj_error'] = e.message; }

            // 방법 2: 폼 이름으로 전역 검색
            try {
                var foundWork = null;
                function searchForWorkForm(obj, path, depth) {
                    if (depth > 8 || !obj) return;
                    if (obj.name === 'div_work_01' || obj.id === 'div_work_01') {
                        result['found_div_work_01'] = path;
                        if (obj.form && obj.form.dsGeneralGrid) {
                            result['dsGeneralGrid_at'] = path;
                            foundWork = obj.form;
                        }
                    }
                    if (obj.name === 'STBJ030_T0' || obj.id === 'STBJ030_T0') {
                        result['found_STBJ030_T0'] = path;
                    }
                    // traverse children
                    try {
                        if (obj.form && obj.form.components) {
                            for (var i = 0; i < obj.form.components.length; i++) {
                                var c = obj.form.components[i];
                                searchForWorkForm(c, path + '.' + (c.name || 'c' + i), depth + 1);
                            }
                        }
                    } catch(e) {}
                    try {
                        if (obj._childframes) {
                            for (var j = 0; j < obj._childframes.length; j++) {
                                var cf = obj._childframes[j];
                                searchForWorkForm(cf, path + '._cf[' + j + '](' + (cf.name || '') + ')', depth + 1);
                            }
                        }
                    } catch(e) {}
                }
                searchForWorkForm(app.mainframe, 'mainframe', 0);

                if (foundWork) {
                    result.workForm_found = true;
                    result.dsGeneralGrid_rows = foundWork.dsGeneralGrid ? foundWork.dsGeneralGrid.getRowCount() : -1;
                    result.fv_OrdInputFlag = String(foundWork.fv_OrdInputFlag || '');
                    result.fv_PyunsuId = String(foundWork.fv_PyunsuId || '');
                    result.fv_OrdYn = String(foundWork.fv_OrdYn || '');
                    result.lvOrdYmd = String(foundWork.lvOrdYmd || '');

                    // 모든 fv_ 변수
                    var fvVars = {};
                    for (var k in foundWork) {
                        if ((k.indexOf('fv_') === 0 || k.indexOf('lv') === 0) && typeof foundWork[k] !== 'function') {
                            fvVars[k] = String(foundWork[k] || '');
                        }
                    }
                    result.fvVars = fvVars;

                    // fn_save 코드
                    if (foundWork.fn_save) {
                        result.fn_save = foundWork.fn_save.toString().substring(0, 3000);
                    }

                    // fn_callback 코드
                    if (foundWork.fn_callback) {
                        result.fn_callback = foundWork.fn_callback.toString().substring(0, 2000);
                    }

                    // 발주 관련 함수 목록
                    var funcs = [];
                    for (var fk in foundWork) {
                        if (typeof foundWork[fk] === 'function') {
                            var fl = fk.toLowerCase();
                            if (fl.indexOf('ord') >= 0 || fl.indexOf('save') >= 0 || fl.indexOf('init') >= 0 ||
                                fl.indexOf('load') >= 0 || fl.indexOf('close') >= 0 || fl.indexOf('day') >= 0 ||
                                fl.indexOf('search') >= 0 || fl.indexOf('check') >= 0) {
                                funcs.push(fk);
                            }
                        }
                    }
                    result.ordRelatedFunctions = funcs;
                }
            } catch(e) { result.search_error = e.message; }

            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        if wf_path:
            wp = json.loads(wf_path)
            for k, v in sorted(wp.items()):
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

        # 3. gfn_transaction으로 selOrdDayList 호출 (정상 채널)
        logger.info("\n=== 3. gfn_transaction으로 selOrdDayList 호출 ===")
        gfn_result = driver.execute_script(r"""
        return new Promise(function(resolve) {
            try {
                var app = nexacro.getApplication();

                // workForm 찾기
                var wf = null;
                function findWF(obj, depth) {
                    if (depth > 8 || !obj) return;
                    if (obj.name === 'div_work_01' && obj.form && obj.form.dsGeneralGrid) {
                        wf = obj.form;
                        return;
                    }
                    try {
                        if (obj.form && obj.form.components) {
                            for (var i = 0; i < obj.form.components.length && !wf; i++) {
                                findWF(obj.form.components[i], depth + 1);
                            }
                        }
                    } catch(e) {}
                    try {
                        if (obj._childframes) {
                            for (var j = 0; j < obj._childframes.length && !wf; j++) {
                                findWF(obj._childframes[j], depth + 1);
                            }
                        }
                    } catch(e) {}
                }
                findWF(app.mainframe, 0);

                if (!wf) {
                    resolve(JSON.stringify({error: 'workForm not found'}));
                    return;
                }

                // stbjForm 레벨 (workForm의 parent)
                var stbjForm = null;
                try {
                    // workForm.parent chain
                    stbjForm = wf.parent.parent.parent; // div_work_01 -> div_workForm -> stbjForm
                } catch(e) {}

                // 콜백 설정
                var callbackName = '_debug_ordday_cb_' + Date.now();
                wf[callbackName] = function(svcId, errCd, errMsg) {
                    var result = {svcId: svcId, errCd: errCd, errMsg: errMsg};

                    // dsResult 확인 (stbjForm 또는 workForm 레벨)
                    try {
                        var dsResult = wf.dsResult || (stbjForm && stbjForm.form && stbjForm.form.dsResult);
                        if (dsResult) {
                            result.dsResult_rows = dsResult.getRowCount();
                            if (dsResult.getRowCount() > 0) {
                                var rows = [];
                                for (var r = 0; r < Math.min(dsResult.getRowCount(), 10); r++) {
                                    var row = {};
                                    for (var c = 0; c < dsResult.getColCount(); c++) {
                                        row[dsResult.getColID(c)] = dsResult.getColumn(r, dsResult.getColID(c));
                                    }
                                    rows.push(row);
                                }
                                result.dsResult_data = rows;
                            }
                        } else {
                            result.dsResult = 'not found';
                        }
                    } catch(e) { result.dsResult_error = e.message; }

                    // dsTmpDate 확인
                    try {
                        var dsTmpDate = wf.dsTmpDate || (stbjForm && stbjForm.form && stbjForm.form.dsTmpDate);
                        if (dsTmpDate && dsTmpDate.getRowCount() > 0) {
                            result.dsTmpDate = {};
                            for (var tc = 0; tc < dsTmpDate.getColCount(); tc++) {
                                result.dsTmpDate[dsTmpDate.getColID(tc)] = dsTmpDate.getColumn(0, dsTmpDate.getColID(tc));
                            }
                        }
                    } catch(e) {}

                    delete wf[callbackName];
                    resolve(JSON.stringify(result));
                };

                // gfn_transaction 호출 — selOrdDayList
                wf.gfn_transaction(
                    'selOrdDayList',
                    'stbjz00/selOrdDayList',
                    '',
                    'dsResult=dsResult dsTmpDate=dsTmpDate',
                    '',
                    callbackName
                );

                // 타임아웃
                setTimeout(function() {
                    if (wf[callbackName]) {
                        delete wf[callbackName];
                        resolve(JSON.stringify({error: 'timeout'}));
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
