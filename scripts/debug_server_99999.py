"""
ErrorCode=99999 원인 진단
1. HTTP 응답 코드 + 전체 응답 확인
2. 폼 변수 전체 덤프 (발주 가능 여부 확인)
3. 다른 날짜 테스트
4. 서버 에러 상세 확인
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_99999")


def main():
    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer()

    try:
        analyzer.setup_driver()
        analyzer.connect()
        analyzer.do_login()
        logger.info("로그인 성공")

        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor(analyzer.driver)
        if not executor.navigate_to_single_order():
            logger.error("메뉴 이동 실패")
            return
        time.sleep(3)

        driver = analyzer.driver

        # 1. 폼 변수 전체 덤프
        logger.info("\n=== Step 1: 폼 변수 덤프 ===")
        form_vars = driver.execute_script("""
        var app = nexacro.getApplication();
        var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
        var stbjForm = null;
        try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
        if (!stbjForm || !stbjForm.div_workForm) {
            var fKeys = Object.keys(frameSet);
            for (var fki = 0; fki < fKeys.length; fki++) {
                try {
                    var ff = frameSet[fKeys[fki]];
                    if (ff && ff.form && ff.form.div_workForm &&
                        ff.form.div_workForm.form.div_work_01 &&
                        ff.form.div_workForm.form.div_work_01.form.gdList) {
                        stbjForm = ff.form; break;
                    }
                } catch(e) {}
            }
        }
        var workForm = stbjForm.div_workForm.form.div_work_01.form;
        var result = {};

        // fv_ 시작하는 폼 변수
        var allKeys = Object.keys(workForm);
        var fvVars = {};
        for (var ki = 0; ki < allKeys.length; ki++) {
            var k = allKeys[ki];
            if (k.indexOf('fv_') === 0) {
                try { fvVars[k] = String(workForm[k]); } catch(e) { fvVars[k] = 'err'; }
            }
        }
        result.fvVars = fvVars;

        // 발주 관련 변수 (stbjForm에도)
        var sfKeys = Object.keys(stbjForm);
        var sfFvVars = {};
        for (var si = 0; si < sfKeys.length; si++) {
            var sk = sfKeys[si];
            if (sk.indexOf('fv_') === 0 || sk.indexOf('fn_') === 0 ||
                sk.toLowerCase().indexOf('ord') >= 0 || sk.toLowerCase().indexOf('pyun') >= 0) {
                var typ = typeof stbjForm[sk];
                if (typ !== 'function') {
                    try { sfFvVars[sk] = String(stbjForm[sk]); } catch(e) {}
                }
            }
        }
        result.stbjFormVars = sfFvVars;

        // app 레벨 변수 중 발주 관련
        var appVarNames = ['GV_USERFLAG', 'SS_STORE_CD', 'SS_USER_NO'];
        var appVars = {};
        for (var vi = 0; vi < appVarNames.length; vi++) {
            try { appVars[appVarNames[vi]] = String(app.getVariable(appVarNames[vi]) || ''); } catch(e) {}
        }
        result.appVars = appVars;

        return JSON.stringify(result);
        """)

        if form_vars:
            fv = json.loads(form_vars)
            logger.info("  workForm fv_ 변수:")
            for k, v in sorted(fv.get('fvVars', {}).items()):
                logger.info(f"    {k}: {v}")
            logger.info("\n  stbjForm 발주 관련 변수:")
            for k, v in sorted(fv.get('stbjFormVars', {}).items()):
                logger.info(f"    {k}: {v}")

        # 2. fetch()로 직접 saveOrd 호출 — HTTP 응답 상세 확인
        logger.info("\n=== Step 2: fetch() 직접 saveOrd — HTTP 상세 ===")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
        item_cd = "8801045571416"

        from src.order.direct_api_saver import PREFETCH_ITEMS_JS
        from src.collectors.direct_api_fetcher import extract_dsitem_all_columns

        prefetch_raw = driver.execute_script(PREFETCH_ITEMS_JS, [item_cd], 10000, tomorrow)
        fields = {}
        if prefetch_raw:
            results = json.loads(prefetch_raw) if isinstance(prefetch_raw, str) else prefetch_raw
            for entry in results:
                if entry.get('ok') and entry.get('text'):
                    f = extract_dsitem_all_columns(entry['text'])
                    if f:
                        fields = f

        if fields:
            # dataset 채우기 + U type
            fields_json = json.dumps(fields)
            driver.execute_script("""
            var fieldsJson = arguments[0];
            var dateStr = arguments[1];
            var itemCd = arguments[2];

            var app = nexacro.getApplication();
            var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var stbjForm = null;
            try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
            if (!stbjForm || !stbjForm.div_workForm) {
                var fKeys = Object.keys(frameSet);
                for (var fki = 0; fki < fKeys.length; fki++) {
                    try {
                        var ff = frameSet[fKeys[fki]];
                        if (ff && ff.form && ff.form.div_workForm &&
                            ff.form.div_workForm.form.div_work_01 &&
                            ff.form.div_workForm.form.div_work_01.form.gdList) {
                            stbjForm = ff.form; break;
                        }
                    } catch(e) {}
                }
            }
            var workForm = stbjForm.div_workForm.form.div_work_01.form;
            var ds = workForm.gdList._binddataset;
            var fields = JSON.parse(fieldsJson);
            ds.clearData();
            var row = ds.addRow();
            for (var cn in fields) {
                if (fields.hasOwnProperty(cn) && fields[cn] !== '' && fields[cn] != null) {
                    try { ds.setColumn(row, cn, fields[cn]); } catch(e) {}
                }
            }
            ds.setColumn(row, 'ITEM_CD', itemCd);
            ds.setColumn(row, 'ORD_MUL_QTY', 0);
            ds.setColumn(row, 'ORD_YMD', dateStr);
            ds.applyChange();
            ds.setColumn(0, 'ORD_MUL_QTY', 1);
            """, fields_json, tomorrow, item_cd)

            # 직접 transaction() 호출하여 응답 상세 캡처
            http_result = driver.execute_script("""
            return new Promise(function(resolve) {
                var app = nexacro.getApplication();
                var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var stbjForm = null;
                try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
                if (!stbjForm || !stbjForm.div_workForm) {
                    var fKeys = Object.keys(frameSet);
                    for (var fki = 0; fki < fKeys.length; fki++) {
                        try {
                            var ff = frameSet[fKeys[fki]];
                            if (ff && ff.form && ff.form.div_workForm &&
                                ff.form.div_workForm.form.div_work_01 &&
                                ff.form.div_workForm.form.div_work_01.form.gdList) {
                                stbjForm = ff.form; break;
                            }
                        } catch(e) {}
                    }
                }
                var workForm = stbjForm.div_workForm.form.div_work_01.form;
                var ds = workForm.gdList._binddataset;

                // SSV body 수동 구성
                var RS = String.fromCharCode(0x1e);
                var US = String.fromCharCode(0x1f);

                // 세션 변수 (gfn_transaction이 자동으로 추가하는 부분)
                var svNames = [
                    'GV_USERFLAG', '_xm_webid_1_', '_xm_tid_1_',
                    'SS_STORE_CD', 'SS_PRE_STORE_CD', 'SS_STORE_NM',
                    'SS_SLC_CD', 'SS_LOC_CD', 'SS_ADM_SECT_CD',
                    'SS_STORE_OWNER_NM', 'SS_STORE_POS_QTY', 'SS_STORE_IP',
                    'SS_SV_EMP_NO', 'SS_SSTORE_ID', 'SS_RCV_ID',
                    'SS_FC_CD', 'SS_USER_GRP_ID', 'SS_USER_NO',
                    'SS_SGGD_CD', 'SS_LOGIN_USER_NO'
                ];
                var parts = ['SSV:utf-8'];
                for (var si = 0; si < svNames.length; si++) {
                    var sv = '';
                    try { sv = String(app.getVariable(svNames[si]) || ''); } catch(e) {}
                    parts.push(svNames[si] + '=' + sv);
                }
                parts.push('strPyunsuId=' + String(workForm.fv_PyunsuId || '0'));
                parts.push('strOrdInputFlag=' + String(workForm.fv_OrdInputFlag || '04'));
                parts.push('GV_MENU_ID=0001,STBJ030_M0');
                parts.push('GV_USERFLAG=HOME');
                parts.push('GV_CHANNELTYPE=HOME');

                // dsGeneralGrid 직렬화 (U 행만)
                parts.push('Dataset:dsGeneralGrid');
                var colDefs = ['_RowType_'];
                for (var ci = 0; ci < ds.getColCount(); ci++) {
                    colDefs.push(ds.getColID(ci) + ':STRING(256)');
                }
                parts.push(colDefs.join(US));

                // U 행
                for (var ri = 0; ri < ds.getRowCount(); ri++) {
                    if (ds.getRowType(ri) === 4) { // U type only
                        var vals = ['U'];
                        for (var ci2 = 0; ci2 < ds.getColCount(); ci2++) {
                            var v = ds.getColumn(ri, ds.getColID(ci2));
                            vals.push(v === null || v === undefined ? '' : String(v));
                        }
                        parts.push(vals.join(US));
                    }
                }
                parts.push(''); // 빈 행 (dsGeneralGrid 종료)

                // dsSaveChk (비어있음)
                parts.push('Dataset:dsSaveChk');
                parts.push('_RowType_' + US + 'ITEM_CD:STRING(256)' + US + 'ITEM_NM:STRING(256)' + US + 'MID_NM:STRING(256)' + US + 'ORD_YMD:STRING(256)' + US + 'ORD_MUL_QTY:INT(256)' + US + 'ORD_INPUT_NM:STRING(256)');
                parts.push('');
                parts.push('');

                var body = parts.join(RS);

                fetch('/stbjz00/saveOrd', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(10000)
                }).then(function(resp) {
                    return resp.text().then(function(text) {
                        resolve(JSON.stringify({
                            status: resp.status,
                            statusText: resp.statusText,
                            bodyLength: text.length,
                            body: text.substring(0, 500).replace(/[\\x1e]/g, '[RS]').replace(/[\\x1f]/g, '|'),
                            headers: {
                                contentType: resp.headers.get('Content-Type') || '',
                                server: resp.headers.get('Server') || ''
                            }
                        }));
                    });
                }).catch(function(e) {
                    resolve(JSON.stringify({error: e.message}));
                });
            });
            """)

            if http_result:
                hr = json.loads(http_result)
                logger.info(f"  HTTP status: {hr.get('status')} {hr.get('statusText')}")
                logger.info(f"  응답 길이: {hr.get('bodyLength')}")
                logger.info(f"  응답 body: {hr.get('body')}")
                logger.info(f"  Content-Type: {hr.get('headers', {}).get('contentType')}")

        # 3. 발주가능 상태 확인 — selOrdYn 엔드포인트 테스트
        logger.info("\n=== Step 3: 발주가능 상태 확인 ===")
        ord_yn = driver.execute_script("""
        var RS = String.fromCharCode(0x1e);
        var US = String.fromCharCode(0x1f);
        var app = nexacro.getApplication();
        var parts = ['SSV:utf-8'];
        var svNames = [
            'GV_USERFLAG', '_xm_webid_1_', '_xm_tid_1_',
            'SS_STORE_CD', 'SS_PRE_STORE_CD', 'SS_STORE_NM',
            'SS_SLC_CD', 'SS_LOC_CD', 'SS_ADM_SECT_CD',
            'SS_STORE_OWNER_NM', 'SS_STORE_POS_QTY', 'SS_STORE_IP',
            'SS_SV_EMP_NO', 'SS_SSTORE_ID', 'SS_RCV_ID',
            'SS_FC_CD', 'SS_USER_GRP_ID', 'SS_USER_NO',
            'SS_SGGD_CD', 'SS_LOGIN_USER_NO'
        ];
        for (var si = 0; si < svNames.length; si++) {
            var sv = '';
            try { sv = String(app.getVariable(svNames[si]) || ''); } catch(e) {}
            parts.push(svNames[si] + '=' + sv);
        }
        parts.push('GV_MENU_ID=0001,STBJ030_M0');
        parts.push('GV_USERFLAG=HOME');
        parts.push('GV_CHANNELTYPE=HOME');
        var body = parts.join(RS);

        // 발주가능 확인 엔드포인트들 시도
        var endpoints = [
            '/stbj030/selOrdYn',
            '/stbj030/selOrdInfo',
            '/stbj030/selInit'
        ];

        var results = [];
        var done = 0;
        var total = endpoints.length;

        return new Promise(function(resolve) {
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
                                body: text.substring(0, 300).replace(/[\\x1e]/g, '[RS]').replace(/[\\x1f]/g, '|')
                            });
                            done++;
                            if (done === total) resolve(JSON.stringify(results));
                        });
                    }).catch(function(e) {
                        results.push({endpoint: ep, error: e.message});
                        done++;
                        if (done === total) resolve(JSON.stringify(results));
                    });
                })(endpoints[ei]);
            }
        });
        """)

        if ord_yn:
            eps = json.loads(ord_yn)
            for ep in eps:
                logger.info(f"\n  {ep.get('endpoint')}:")
                if ep.get('error'):
                    logger.info(f"    에러: {ep['error']}")
                else:
                    logger.info(f"    status: {ep.get('status')}")
                    logger.info(f"    body: {ep.get('body')}")

    except Exception as e:
        logger.error(f"오류: {e}", exc_info=True)
    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass

    logger.info("디버깅 완료")


if __name__ == "__main__":
    main()
