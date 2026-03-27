"""
발주 폼 초기화 분석 — selOrdDayList 응답 + fn_save 코드 + workForm 변수 확인
"""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_init")


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

        # 1. selOrdDayList 전체 응답
        logger.info("\n=== 1. selOrdDayList 전체 응답 ===")
        ord_day_js = r"""
        return new Promise(function(resolve) {
            var RS = String.fromCharCode(0x1e);
            var cookies = {};
            document.cookie.split(';').forEach(function(c) {
                var p = c.trim().split('=');
                if (p.length >= 2) cookies[p[0]] = p.slice(1).join('=');
            });

            var parts = ['SSV:utf-8'];
            parts.push('GV_USERFLAG=' + (cookies.GV_USERFLAG || 'HOME'));
            var ssVars = ['SS_STORE_CD', 'SS_PRE_STORE_CD', 'SS_STORE_NM', 'SS_SLC_CD',
                          'SS_LOC_CD', 'SS_ADM_SECT_CD', 'SS_STORE_OWNER_NM', 'SS_STORE_POS_QTY',
                          'SS_STORE_IP', 'SS_SV_EMP_NO', 'SS_USER_NO', 'SS_LOGIN_USER_NO',
                          'SS_FC_CD', 'SS_USER_GRP_ID', 'SS_SERVER_TIME'];
            for (var i = 0; i < ssVars.length; i++) {
                parts.push(ssVars[i] + '=' + (cookies[ssVars[i]] || ''));
            }
            parts.push('GV_MENU_ID=0001,STBJ030_M0');
            var body = parts.join(RS);

            fetch('/stbjz00/selOrdDayList', {
                method: 'POST',
                headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                body: body,
                signal: AbortSignal.timeout(10000)
            }).then(function(resp) {
                return resp.text().then(function(text) {
                    var lines = text.split(RS);
                    var parsed = [];
                    for (var j = 0; j < lines.length; j++) {
                        parsed.push(lines[j].replace(/[\x1f]/g, ' | '));
                    }
                    resolve(JSON.stringify({status: resp.status, rawLength: text.length, lines: parsed}));
                });
            }).catch(function(e) { resolve(JSON.stringify({error: e.message})); });
        });
        """
        result = driver.execute_script(ord_day_js)
        if result:
            odr = json.loads(result)
            if odr.get('error'):
                logger.info(f"  에러: {odr['error']}")
            else:
                logger.info(f"  Status: {odr['status']}, Length: {odr['rawLength']}")
                for i, line in enumerate(odr.get('lines', [])):
                    logger.info(f"  [{i}] {line}")

        # 2. workForm 변수 상태
        logger.info("\n=== 2. workForm 변수 상태 ===")
        wf_js = r"""
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.childframe.form.div_workForm.form.div_work_01.form;
            var result = {};

            if (wf) {
                result.workForm_found = true;
                result.dsGeneralGrid_rows = wf.dsGeneralGrid ? wf.dsGeneralGrid.getRowCount() : -1;
                result.dsGeneralGrid_cols = wf.dsGeneralGrid ? wf.dsGeneralGrid.getColCount() : -1;

                // 모든 fv_ / lv 변수
                var fvVars = {};
                for (var k in wf) {
                    if ((k.indexOf('fv_') === 0 || k.indexOf('lv') === 0) && typeof wf[k] !== 'function') {
                        fvVars[k] = String(wf[k] || '');
                    }
                }
                result.fvVars = fvVars;

                // dsSaveChk 상태
                if (wf.dsSaveChk) {
                    result.dsSaveChk_rows = wf.dsSaveChk.getRowCount();
                    if (wf.dsSaveChk.getRowCount() > 0) {
                        var saveCols = {};
                        for (var ci = 0; ci < wf.dsSaveChk.getColCount(); ci++) {
                            var cn = wf.dsSaveChk.getColID(ci);
                            saveCols[cn] = wf.dsSaveChk.getColumn(0, cn);
                        }
                        result.dsSaveChk_data = saveCols;
                    }
                }
            }

            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message}); }
        """
        result = driver.execute_script(wf_js)
        if result:
            wf = json.loads(result)
            for k, v in sorted(wf.items()):
                if k == 'fvVars' and isinstance(v, dict):
                    logger.info(f"  fvVars:")
                    for fk, fv in sorted(v.items()):
                        logger.info(f"    {fk}: '{fv}'")
                elif isinstance(v, dict):
                    logger.info(f"  {k}:")
                    for fk, fv in sorted(v.items()):
                        logger.info(f"    {fk}: {fv}")
                else:
                    logger.info(f"  {k}: {v}")

        # 3. fn_save + fn_callback + init 함수 코드
        logger.info("\n=== 3. 발주 관련 함수 목록 + 코드 ===")
        fn_js = r"""
        try {
            var app = nexacro.getApplication();
            var wf = app.mainframe.childframe.form.div_workForm.form.div_work_01.form;
            var result = {};

            // fn_save
            if (wf.fn_save) result.fn_save = wf.fn_save.toString().substring(0, 3000);
            // fn_callback
            if (wf.fn_callback) result.fn_callback = wf.fn_callback.toString().substring(0, 3000);
            // init/onload 함수
            var initNames = ['STBJ030_T0_onload', 'fn_init', 'fn_onload', 'div_work_01_onload', 'form_onload'];
            for (var i = 0; i < initNames.length; i++) {
                if (wf[initNames[i]]) result[initNames[i]] = wf[initNames[i]].toString().substring(0, 2000);
            }

            // 발주 관련 함수 이름 목록
            var ordFuncs = [];
            for (var k in wf) {
                if (typeof wf[k] === 'function') {
                    var kl = k.toLowerCase();
                    if (kl.indexOf('ord') >= 0 || kl.indexOf('save') >= 0 || kl.indexOf('init') >= 0 || kl.indexOf('load') >= 0 || kl.indexOf('close') >= 0) {
                        ordFuncs.push(k);
                    }
                }
            }
            result.ordRelatedFunctions = ordFuncs;

            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message}); }
        """
        result = driver.execute_script(fn_js)
        if result:
            fc = json.loads(result)
            for k, v in sorted(fc.items()):
                if isinstance(v, str) and len(v) > 100:
                    logger.info(f"\n  === {k} ===")
                    # 줄 단위로 출력
                    for line in v.split('\n'):
                        logger.info(f"  {line.rstrip()}")
                elif isinstance(v, list):
                    logger.info(f"  {k}: {v}")
                else:
                    logger.info(f"  {k}: {v}")

        # 4. 발주 마감 직접 호출 테스트 (stbjz00/selOrdYn 등)
        logger.info("\n=== 4. stbjz00 경로 발주 관련 엔드포인트 ===")
        ep_js = r"""
        return new Promise(function(resolve) {
            var RS = String.fromCharCode(0x1e);
            var cookies = {};
            document.cookie.split(';').forEach(function(c) {
                var p = c.trim().split('=');
                if (p.length >= 2) cookies[p[0]] = p.slice(1).join('=');
            });

            var parts = ['SSV:utf-8'];
            parts.push('GV_USERFLAG=' + (cookies.GV_USERFLAG || 'HOME'));
            var ssVars = ['SS_STORE_CD', 'SS_USER_NO', 'SS_LOC_CD', 'SS_FC_CD', 'SS_SLC_CD', 'SS_ADM_SECT_CD'];
            for (var i = 0; i < ssVars.length; i++) {
                parts.push(ssVars[i] + '=' + (cookies[ssVars[i]] || ''));
            }
            parts.push('GV_MENU_ID=0001,STBJ030_M0');
            var body = parts.join(RS);

            var endpoints = [
                '/stbjz00/selOrdYn',
                '/stbjz00/selOrdClose',
                '/stbjz00/selOrdInfo',
                '/stbjz00/selInit',
                '/stbjz00/selOrdDayList'
            ];

            var results = [];
            var done = 0;
            for (var ei = 0; ei < endpoints.length; ei++) {
                (function(ep) {
                    fetch(ep, {
                        method: 'POST',
                        headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                        body: body,
                        signal: AbortSignal.timeout(5000)
                    }).then(function(resp) {
                        return resp.text().then(function(text) {
                            results.push({
                                endpoint: ep,
                                status: resp.status,
                                bodyLength: text.length,
                                body: text.substring(0, 800).replace(/[\x1e]/g, '\n[RS]').replace(/[\x1f]/g, ' | ')
                            });
                            done++;
                            if (done === endpoints.length) resolve(JSON.stringify(results));
                        });
                    }).catch(function(e) {
                        results.push({endpoint: ep, error: e.message});
                        done++;
                        if (done === endpoints.length) resolve(JSON.stringify(results));
                    });
                })(endpoints[ei]);
            }
        });
        """
        result = driver.execute_script(ep_js)
        if result:
            eps = json.loads(result)
            for ep in eps:
                logger.info(f"\n  {ep.get('endpoint')}:")
                if ep.get('error'):
                    logger.info(f"    에러: {ep['error']}")
                else:
                    logger.info(f"    status: {ep.get('status')}")
                    logger.info(f"    length: {ep.get('bodyLength')}")
                    logger.info(f"    body:\n{ep.get('body', '')}")

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
