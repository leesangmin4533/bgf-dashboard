"""
gfn_transaction 호출 시 XHR body 캡처 + dataset 포함 여부 확인
workForm 수정 후 실제로 dataset이 전송되는지 검증
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_save_xhr")


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
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
        item_cd = "8801045571416"

        # 1. XHR 인터셉터 설치
        logger.info("\n=== Step 1: XHR 인터셉터 설치 ===")
        driver.execute_script("""
        window._capturedSaveRequests = [];
        var origOpen = XMLHttpRequest.prototype.open;
        var origSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._captureUrl = url;
            this._captureMethod = method;
            return origOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function(body) {
            if (this._captureUrl && this._captureUrl.indexOf('saveOrd') >= 0) {
                window._capturedSaveRequests.push({
                    url: this._captureUrl,
                    method: this._captureMethod,
                    bodyLength: body ? body.length : 0,
                    bodyPreview: body ? body.substring(0, 500) : '',
                    bodyFull: body || '',
                    timestamp: Date.now()
                });
            }
            return origSend.apply(this, arguments);
        };
        """)
        logger.info("  XHR 인터셉터 설치 완료")

        # 2. 프리페치
        logger.info("\n=== Step 2: selSearch 프리페치 ===")
        from src.order.direct_api_saver import DirectApiOrderSaver, PREFETCH_ITEMS_JS
        from src.collectors.direct_api_fetcher import extract_dsitem_all_columns

        prefetch_raw = driver.execute_script(PREFETCH_ITEMS_JS, [item_cd], 10000, tomorrow)
        prefetch_data = {}
        if prefetch_raw:
            results = json.loads(prefetch_raw) if isinstance(prefetch_raw, str) else prefetch_raw
            for entry in results:
                if entry.get('ok') and entry.get('text'):
                    fields = extract_dsitem_all_columns(entry['text'])
                    if fields:
                        prefetch_data[item_cd] = fields
                        logger.info(f"  프리페치 성공: {len(fields)}개 필드")
                    else:
                        logger.warning(f"  프리페치 파싱 실패")
                else:
                    logger.warning(f"  프리페치 응답 실패: {entry.get('error', 'unknown')}")

        if not prefetch_data:
            logger.error("  프리페치 데이터 없음, 종료")
            return

        # 3. dataset 채우기
        logger.info("\n=== Step 3: dataset 채우기 ===")
        from src.order.direct_api_saver import POPULATE_DATASET_JS
        orders_json = json.dumps([{
            "item_cd": item_cd,
            "multiplier": 1,
            "store_cd": "46513",
            "ord_unit_qty": prefetch_data[item_cd].get('ORD_UNIT_QTY', ''),
            "fields": prefetch_data[item_cd]
        }])
        pop_result = driver.execute_script(POPULATE_DATASET_JS, orders_json, tomorrow)
        if pop_result:
            pr = json.loads(pop_result)
            logger.info(f"  결과: {pr}")
        else:
            logger.error("  dataset 채우기 실패")
            return

        # 4. dataset 상태 확인 (gfn_transaction 호출 직전)
        logger.info("\n=== Step 4: dataset 상태 확인 ===")
        ds_check = driver.execute_script("""
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
        var result = {};

        // dsGeneralGrid 상태
        result.dsName = ds.name || 'unknown';
        result.rowCount = ds.getRowCount();
        result.colCount = ds.getColCount();

        // 첫 행 전체 데이터
        if (ds.getRowCount() > 0) {
            var rowData = {};
            var nullCols = [];
            for (var ci = 0; ci < ds.getColCount(); ci++) {
                var colId = ds.getColID(ci);
                var val = ds.getColumn(0, colId);
                if (val === null || val === undefined || val === '') {
                    nullCols.push(colId);
                } else {
                    rowData[colId] = String(val);
                }
            }
            result.rowData = rowData;
            result.nullCols = nullCols;
            result.setCount = Object.keys(rowData).length;
            result.nullCount = nullCols.length;
        }

        // dsSaveChk 상태
        try {
            var dsSaveChk = null;
            var wfKeys = Object.keys(workForm);
            for (var wi = 0; wi < wfKeys.length; wi++) {
                if (wfKeys[wi].toLowerCase().indexOf('savechk') >= 0 || wfKeys[wi] === 'dsSaveChk') {
                    if (workForm[wfKeys[wi]] && typeof workForm[wfKeys[wi]].addRow === 'function') {
                        dsSaveChk = workForm[wfKeys[wi]]; break;
                    }
                }
            }
            if (dsSaveChk) {
                result.saveChkRows = dsSaveChk.getRowCount();
                result.saveChkCols = dsSaveChk.getColCount();
                if (dsSaveChk.getRowCount() > 0) {
                    var chkData = {};
                    for (var si = 0; si < dsSaveChk.getColCount(); si++) {
                        var cid = dsSaveChk.getColID(si);
                        chkData[cid] = String(dsSaveChk.getColumn(0, cid) || '');
                    }
                    result.saveChkData = chkData;
                }
            }
        } catch(e) { result.saveChkError = e.message; }

        // gfn_callback 원본 소스 (일부)
        try {
            result.gfnCallbackSrc = String(workForm.gfn_callback).substring(0, 500);
        } catch(e) { result.gfnCallbackSrc = 'err: ' + e.message; }

        // fn_save 소스 (일부)
        try {
            result.fnSaveSrc = String(workForm.fn_save).substring(0, 800);
        } catch(e) { result.fnSaveSrc = 'err: ' + e.message; }

        return JSON.stringify(result);
        """)

        if ds_check:
            dc = json.loads(ds_check)
            logger.info(f"  dsGeneralGrid: {dc.get('rowCount')}행, {dc.get('colCount')}컬럼")
            logger.info(f"  설정된 컬럼: {dc.get('setCount')}개, NULL 컬럼: {dc.get('nullCount')}개")
            if dc.get('nullCols'):
                logger.info(f"  NULL 컬럼들: {dc['nullCols']}")
            if dc.get('rowData'):
                logger.info(f"  설정된 값 (주요):")
                for key in ['ITEM_CD', 'ORD_MUL_QTY', 'ORD_YMD', 'STORE_CD', 'ITEM_NM',
                           'ORD_UNIT_QTY', 'MID_NM', 'PROFIT_RATE', 'HQ_MAEGA_SET']:
                    if key in dc['rowData']:
                        logger.info(f"    {key}: {dc['rowData'][key]}")

            logger.info(f"  dsSaveChk: {dc.get('saveChkRows', '?')}행, {dc.get('saveChkCols', '?')}컬럼")
            if dc.get('saveChkData'):
                logger.info(f"  saveChkData: {dc['saveChkData']}")

            if dc.get('fnSaveSrc'):
                logger.info(f"\n  fn_save 소스:\n{dc['fnSaveSrc']}")

        # 5. gfn_transaction 호출 (workForm에서)
        logger.info("\n=== Step 5: gfn_transaction 호출 (workForm) ===")
        tx_result = driver.execute_script("""
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

        // 결과 저장소
        window._debugSaveResult = null;
        window._debugSaveDone = false;

        // gfn_callback 가로채기
        var origGfnCallback = workForm.gfn_callback;
        workForm.gfn_callback = function(svcId, errCd, errMsg) {
            window._debugSaveResult = {
                svcId: String(svcId || ''),
                errCd: String(errCd || ''),
                errMsg: String(errMsg || ''),
                callbackType: 'gfn_callback'
            };
            window._debugSaveDone = true;
            // 원본 복원
            workForm.gfn_callback = origGfnCallback;
            try { origGfnCallback.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        };

        // fn_callBack도 별도 감시
        var origFnCallBack = workForm.fn_callBack;
        workForm.fn_callBack = function(svcId, errCd, errMsg) {
            if (!window._debugSaveResult) {
                window._debugSaveResult = {
                    svcId: String(svcId || ''),
                    errCd: String(errCd || ''),
                    errMsg: String(errMsg || ''),
                    callbackType: 'fn_callBack'
                };
            } else {
                window._debugSaveResult.fnCallbackAlso = true;
                window._debugSaveResult.fnErrCd = String(errCd || '');
                window._debugSaveResult.fnErrMsg = String(errMsg || '');
            }
            window._debugSaveDone = true;
            workForm.fn_callBack = origFnCallBack;
            try { origFnCallBack.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        };

        try {
            workForm.gfn_transaction(
                'savOrd',
                'stbjz00/saveOrd',
                'dsGeneralGrid=dsGeneralGrid dsSaveChk=dsSaveChk',
                'gds_ErrMsg=gds_ErrMsg',
                'strPyunsuId=0 strOrdInputFlag=04',
                'fn_callBack'
            );
            return JSON.stringify({started: true});
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
        """)
        logger.info(f"  gfn_transaction 호출: {tx_result}")

        # 6. 콜백 대기 (최대 10초)
        logger.info("\n=== Step 6: 콜백 대기 ===")
        for i in range(20):
            time.sleep(0.5)
            done = driver.execute_script("return window._debugSaveDone === true;")
            if done:
                result = driver.execute_script("return JSON.stringify(window._debugSaveResult);")
                if result:
                    r = json.loads(result)
                    logger.info(f"  콜백 수신: {r}")
                break
        else:
            logger.warning("  콜백 타임아웃 (10초)")

        # 7. 캡처된 XHR body 확인
        logger.info("\n=== Step 7: 캡처된 saveOrd XHR body ===")
        xhr_data = driver.execute_script("return JSON.stringify(window._capturedSaveRequests || []);")
        if xhr_data:
            requests = json.loads(xhr_data)
            logger.info(f"  캡처된 요청 수: {len(requests)}")
            for idx, req in enumerate(requests):
                logger.info(f"\n  --- 요청 [{idx}] ---")
                logger.info(f"  URL: {req.get('method')} {req.get('url')}")
                logger.info(f"  body 길이: {req.get('bodyLength')}")

                body = req.get('bodyFull', '')
                if body:
                    # RS로 분리
                    parts = body.split('\x1e')
                    logger.info(f"  RS 분리 파트 수: {len(parts)}")
                    for pi, part in enumerate(parts):
                        clean = part.replace('\x1f', ' | ')
                        if len(clean) > 300:
                            logger.info(f"    [{pi}] {clean[:300]}...")
                        else:
                            logger.info(f"    [{pi}] {clean}")

                    # dataset 포함 여부
                    has_ds = any('dsGeneralGrid' in p for p in parts)
                    has_chk = any('dsSaveChk' in p for p in parts)
                    has_data_rows = len(parts) > 10
                    logger.info(f"\n  dsGeneralGrid 포함: {has_ds}")
                    logger.info(f"  dsSaveChk 포함: {has_chk}")
                    logger.info(f"  데이터 행 존재: {has_data_rows}")
        else:
            logger.warning("  XHR 캡처 없음")

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
