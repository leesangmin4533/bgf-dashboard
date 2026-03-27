"""
selSearch 프리페치 메커니즘 디버깅 스크립트

폴백 템플릿 구성이 실제로 어떤 결과를 내는지 확인합니다.
"""

import json
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger

logger = get_logger("debug_prefetch")


def main():
    logger.info("selSearch 프리페치 디버깅 시작")

    # 1. 로그인
    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer()

    try:
        analyzer.setup_driver()
        analyzer.connect()
        analyzer.do_login()
        logger.info("로그인 성공")

        # 2. 메뉴 이동
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor(analyzer.driver)
        if not executor.navigate_to_single_order():
            logger.error("메뉴 이동 실패")
            return
        logger.info("메뉴 이동 성공")
        time.sleep(3)

        driver = analyzer.driver
        item_cd = "8801045571416"

        # 3. 템플릿 구성 테스트 (JS 직접 실행)
        logger.info("\n=== Step 1: 폴백 템플릿 구성 테스트 ===")
        template_result = driver.execute_script("""
        var RS = String.fromCharCode(0x1e);
        var US = String.fromCharCode(0x1f);
        var result = {step: '', error: ''};
        try {
            var app = nexacro.getApplication();
            if (!app) { result.error = 'no_app'; return JSON.stringify(result); }
            result.step = 'app_ok';

            var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null;
            if (!stbjForm || !stbjForm.div_workForm) {
                var fKeys = Object.keys(frameSet);
                for (var fki = 0; fki < fKeys.length; fki++) {
                    try {
                        var ff = frameSet[fKeys[fki]];
                        if (ff && ff.form && ff.form.div_workForm &&
                            ff.form.div_workForm.form.div_work_01 &&
                            ff.form.div_workForm.form.div_work_01.form.gdList) {
                            stbjForm = ff.form;
                            break;
                        }
                    } catch(e) {}
                }
            }
            if (!stbjForm) { result.error = 'no_form'; return JSON.stringify(result); }
            result.step = 'form_ok';

            var workForm = stbjForm.div_workForm.form.div_work_01.form;
            var ds = workForm.gdList._binddataset;
            if (!ds || typeof ds.getColCount !== 'function') {
                ds = workForm.gdList._binddataset_obj;
            }
            if (!ds) { result.error = 'no_dataset'; return JSON.stringify(result); }
            result.step = 'dataset_ok';
            result.colCount = ds.getColCount();

            // 세션 변수 읽기
            var svNames = [
                'GV_USERFLAG', '_xm_webid_1_', '_xm_tid_1_',
                'SS_STORE_CD', 'SS_PRE_STORE_CD', 'SS_STORE_NM',
                'SS_SLC_CD', 'SS_LOC_CD', 'SS_ADM_SECT_CD',
                'SS_STORE_OWNER_NM', 'SS_STORE_POS_QTY', 'SS_STORE_IP',
                'SS_SV_EMP_NO', 'SS_SSTORE_ID', 'SS_RCV_ID',
                'SS_FC_CD', 'SS_USER_GRP_ID', 'SS_USER_NO',
                'SS_SGGD_CD', 'SS_LOGIN_USER_NO'
            ];
            var sessionVars = {};
            for (var si = 0; si < svNames.length; si++) {
                var sv = '';
                try { sv = String(app.getVariable(svNames[si]) || ''); } catch(e) { sv = 'ERR:' + e.message; }
                sessionVars[svNames[si]] = sv;
            }
            result.sessionVars = sessionVars;

            // 템플릿 구성
            var parts = ['SSV:utf-8'];
            for (var si2 = 0; si2 < svNames.length; si2++) {
                parts.push(svNames[si2] + '=' + (sessionVars[svNames[si2]] || ''));
            }
            parts.push('strOrdYmd=');
            parts.push('strItemCd=__PLACEHOLDER__');
            parts.push('strSearchType=1');
            parts.push('WEEK_JOB_CD=');
            parts.push('MSG_CD=');
            parts.push('GV_MENU_ID=0001,STBJ030_M0');
            parts.push('GV_USERFLAG=HOME');
            parts.push('GV_CHANNELTYPE=HOME');

            // Dataset:dsItem column definitions
            var colDefs = ['_RowType_'];
            var colNames = [];
            for (var ci = 0; ci < ds.getColCount(); ci++) {
                var cid = ds.getColID(ci);
                colNames.push(cid);
                var ctype = 'STRING(256)';
                var intCols = ['HQ_MAEGA_SET','ORD_UNIT_QTY','ORD_MULT_ULMT','ORD_MULT_LLMT',
                               'NOW_QTY','ORD_MUL_QTY','TOT_QTY','PAGE_CNT','EXPIRE_DAY'];
                var decCols = ['PROFIT_RATE'];
                if (intCols.indexOf(cid) >= 0) ctype = 'INT(256)';
                if (decCols.indexOf(cid) >= 0) ctype = 'BIGDECIMAL(256)';
                colDefs.push(cid + ':' + ctype);
            }
            parts.push('Dataset:dsItem');
            parts.push(colDefs.join(US));
            parts.push('');

            var template = parts.join(RS);
            result.step = 'template_built';
            result.templateLength = template.length;
            result.templatePartsCount = parts.length;
            result.colNames = colNames;

            // 실제 요청 body 구성 (strItemCd 교체)
            var itemCd = arguments[0];
            var body = template.replace(/strItemCd=[^\x1e]*/, 'strItemCd=' + itemCd);
            result.bodyLength = body.length;
            result.bodyPreview = body.substring(0, 200).replace(/[\x1e\x1f]/g, '|');

            // 4. 실제 fetch 호출
            result.step = 'fetching';

        } catch(e) {
            result.error = e.message;
        }
        return JSON.stringify(result);
        """, item_cd)

        if template_result:
            tr = json.loads(template_result)
            logger.info(f"  step: {tr.get('step')}")
            logger.info(f"  colCount: {tr.get('colCount')}")
            logger.info(f"  templateLength: {tr.get('templateLength')}")
            logger.info(f"  bodyLength: {tr.get('bodyLength')}")
            logger.info(f"  bodyPreview: {tr.get('bodyPreview', '')[:300]}")
            if tr.get('error'):
                logger.error(f"  error: {tr['error']}")
            sv = tr.get('sessionVars', {})
            logger.info(f"  SS_STORE_CD: {sv.get('SS_STORE_CD')}")
            logger.info(f"  _xm_webid_1_: {sv.get('_xm_webid_1_', '(empty)')[:20]}")
            logger.info(f"  _xm_tid_1_: {sv.get('_xm_tid_1_', '(empty)')[:20]}")
        else:
            logger.error("  null 반환")

        # 4. 실제 fetch 호출 테스트
        logger.info("\n=== Step 2: selSearch fetch 테스트 ===")
        fetch_result = driver.execute_script("""
        var itemCd = arguments[0];
        var RS = String.fromCharCode(0x1e);
        var US = String.fromCharCode(0x1f);

        // 템플릿 구성 (Step 1과 동일)
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
                        stbjForm = ff.form;
                        break;
                    }
                } catch(e) {}
            }
        }
        var workForm = stbjForm.div_workForm.form.div_work_01.form;
        var ds = workForm.gdList._binddataset;

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
        parts.push('strOrdYmd=');
        parts.push('strItemCd=' + itemCd);
        parts.push('strSearchType=1');
        parts.push('WEEK_JOB_CD=');
        parts.push('MSG_CD=');
        parts.push('GV_MENU_ID=0001,STBJ030_M0');
        parts.push('GV_USERFLAG=HOME');
        parts.push('GV_CHANNELTYPE=HOME');

        var colDefs = ['_RowType_'];
        for (var ci = 0; ci < ds.getColCount(); ci++) {
            var cid = ds.getColID(ci);
            var ctype = 'STRING(256)';
            var intCols = ['HQ_MAEGA_SET','ORD_UNIT_QTY','ORD_MULT_ULMT','ORD_MULT_LLMT',
                           'NOW_QTY','ORD_MUL_QTY','TOT_QTY','PAGE_CNT','EXPIRE_DAY'];
            var decCols = ['PROFIT_RATE'];
            if (intCols.indexOf(cid) >= 0) ctype = 'INT(256)';
            if (decCols.indexOf(cid) >= 0) ctype = 'BIGDECIMAL(256)';
            colDefs.push(cid + ':' + ctype);
        }
        parts.push('Dataset:dsItem');
        parts.push(colDefs.join(US));
        parts.push('');

        var body = parts.join(RS);

        // fetch 호출
        try {
            var resp = await fetch('/stbj030/selSearch', {
                method: 'POST',
                headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                body: body,
                signal: AbortSignal.timeout(10000)
            });
            var text = await resp.text();
            return JSON.stringify({
                ok: resp.ok,
                status: resp.status,
                textLength: text.length,
                textPreview: text.substring(0, 500).replace(/[\x1e\x1f]/g, '|'),
                hasErrorCode: text.indexOf('ErrorCode') >= 0,
                errorCode: text.match(/ErrorCode:string=(\d+)/) ? text.match(/ErrorCode:string=(\d+)/)[1] : 'not_found',
                hasDsItem: text.indexOf('Dataset:dsItem') >= 0,
                hasItemNm: text.indexOf('ITEM_NM') >= 0,
            });
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
        """, item_cd)

        if fetch_result:
            fr = json.loads(fetch_result)
            logger.info(f"  ok: {fr.get('ok')}")
            logger.info(f"  status: {fr.get('status')}")
            logger.info(f"  textLength: {fr.get('textLength')}")
            logger.info(f"  errorCode: {fr.get('errorCode')}")
            logger.info(f"  hasDsItem: {fr.get('hasDsItem')}")
            logger.info(f"  hasItemNm: {fr.get('hasItemNm')}")
            logger.info(f"  textPreview: {fr.get('textPreview', '')[:400]}")
            if fr.get('error'):
                logger.error(f"  error: {fr['error']}")
        else:
            logger.error("  null 반환")

        # 5. 비교: 실제 캡처된 요청과 비교
        logger.info("\n=== Step 3: 캡처 파일의 selSearch 요청과 비교 ===")
        import os
        capture_file = 'captures/save_api_capture_valid.json'
        if os.path.exists(capture_file):
            with open(capture_file, 'r', encoding='utf-8') as f:
                capture = json.load(f)
            if 'captured_selSearch_requests' in capture:
                for req in capture['captured_selSearch_requests']:
                    body = req.get('body', '')
                    logger.info(f"  캡처된 body 길이: {len(body)}")
                    # 파싱
                    parts = body.split('\x1e')
                    logger.info(f"  캡처된 parts 수: {len(parts)}")
                    for p in parts[:10]:
                        clean = p.replace('\x1f', '|')
                        logger.info(f"    {clean[:100]}")
            else:
                logger.info("  captured_selSearch_requests 없음")
        else:
            logger.info(f"  {capture_file} 없음")

    except KeyboardInterrupt:
        logger.info("사용자 중단")
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
