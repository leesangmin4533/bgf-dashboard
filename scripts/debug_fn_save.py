"""
fn_save 직접 호출 테스트 + row type 확인
1. dataset 채우기 (프리페치)
2. row type 확인 (N vs I vs U)
3. fn_save() 직접 호출 시도
4. gfn_transaction과 fn_save의 차이점 분석
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_fn_save")


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

        # XHR 인터셉터 설치
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
                    bodyPreview: body ? body.substring(0, 2000).replace(/[\\x1e]/g, '\\n[RS]').replace(/[\\x1f]/g, '|') : '',
                    timestamp: Date.now()
                });
            }
            return origSend.apply(this, arguments);
        };
        """)

        # 1. 프리페치
        logger.info("\n=== Step 1: 프리페치 ===")
        from src.order.direct_api_saver import PREFETCH_ITEMS_JS, POPULATE_DATASET_JS
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

        if not prefetch_data:
            logger.error("  프리페치 실패")
            return

        # 2. dataset 채우기
        logger.info("\n=== Step 2: dataset 채우기 ===")
        orders_json = json.dumps([{
            "item_cd": item_cd,
            "multiplier": 1,
            "store_cd": "46513",
            "ord_unit_qty": prefetch_data[item_cd].get('ORD_UNIT_QTY', ''),
            "fields": prefetch_data[item_cd]
        }])
        pop_result = driver.execute_script(POPULATE_DATASET_JS, orders_json, tomorrow)
        logger.info(f"  채우기 결과: {pop_result}")

        # 3. Row type + form 변수 확인
        logger.info("\n=== Step 3: row type + 폼 변수 확인 ===")
        diag = driver.execute_script("""
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

        // row type
        if (ds.getRowCount() > 0) {
            try { result.rowType0 = ds.getRowType(0); } catch(e) { result.rowType0 = 'err:' + e.message; }
            try { result.rowTypeName0 = ['NONE','NORMAL','INSERT','UNKNOWN','UPDATE'][ds.getRowType(0)] || 'unknown'; } catch(e) {}

            // 주요 필드 값
            result.ITEM_CD = String(ds.getColumn(0, 'ITEM_CD') || '');
            result.ORD_MUL_QTY = String(ds.getColumn(0, 'ORD_MUL_QTY') || '');
            result.ORD_YMD = String(ds.getColumn(0, 'ORD_YMD') || '');

            // ITEM_CHK 확인
            try { result.ITEM_CHK = String(ds.getColumn(0, 'ITEM_CHK') || 'null'); } catch(e) { result.ITEM_CHK = 'err'; }
        }

        // form 인스턴스 변수
        try { result.fv_PyunsuId = String(workForm.fv_PyunsuId); } catch(e) { result.fv_PyunsuId = 'err:' + e.message; }
        try { result.fv_OrdInputFlag = String(workForm.fv_OrdInputFlag); } catch(e) { result.fv_OrdInputFlag = 'err:' + e.message; }

        // nexacro.wrapQuote 테스트
        try {
            result.wrapQuote0 = nexacro.wrapQuote('0');
            result.wrapQuote04 = nexacro.wrapQuote('04');
        } catch(e) { result.wrapQuoteErr = e.message; }

        // gf_ModifiedDS 체크
        try {
            result.isModified = workForm.gf_ModifiedDS(ds);
        } catch(e) { result.isModifiedErr = e.message; }

        // dataset.getDeletedRowCount 등
        try {
            result.rowCount = ds.getRowCount();
            result.deletedRowCount = ds.getDeletedRowCount();
        } catch(e) {}

        // serializeSSV 확인 (inData 직렬화 결과 미리보기)
        try {
            var ssv = ds.saveSSV();
            if (ssv) {
                result.ssvLength = ssv.length;
                result.ssvPreview = ssv.substring(0, 300).replace(/[\\x1e]/g, '[RS]').replace(/[\\x1f]/g, '|');
            }
        } catch(e) { result.ssvErr = e.message; }

        return JSON.stringify(result);
        """)

        if diag:
            d = json.loads(diag)
            logger.info(f"  rowType[0]: {d.get('rowType0')} ({d.get('rowTypeName0', '?')})")
            logger.info(f"  ITEM_CD: {d.get('ITEM_CD')}")
            logger.info(f"  ORD_MUL_QTY: {d.get('ORD_MUL_QTY')}")
            logger.info(f"  ITEM_CHK: {d.get('ITEM_CHK')}")
            logger.info(f"  fv_PyunsuId: {d.get('fv_PyunsuId')}")
            logger.info(f"  fv_OrdInputFlag: {d.get('fv_OrdInputFlag')}")
            logger.info(f"  wrapQuote('0'): {d.get('wrapQuote0')}")
            logger.info(f"  wrapQuote('04'): {d.get('wrapQuote04')}")
            logger.info(f"  isModified: {d.get('isModified', d.get('isModifiedErr', '?'))}")
            logger.info(f"  rowCount: {d.get('rowCount')}, deletedRowCount: {d.get('deletedRowCount')}")
            if d.get('ssvLength'):
                logger.info(f"  SSV 길이: {d['ssvLength']}")
                logger.info(f"  SSV preview: {d.get('ssvPreview', '')}")

        # 4. row type을 U로 변경 + fn_save 직접 호출
        logger.info("\n=== Step 4: row type → U + fn_save 직접 호출 ===")

        # 결과 저장소 초기화
        driver.execute_script("""
        window._fnSaveResult = null;
        window._fnSaveDone = false;
        """)

        # row type 변경 + 콜백 가로채기 + fn_save 호출
        save_result = driver.execute_script("""
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

        // row type을 U (4)로 변경 (Updated)
        for (var ri = 0; ri < ds.getRowCount(); ri++) {
            try { ds.setRowType(ri, 4); } catch(e) {}
        }
        var newRowType = -1;
        try { newRowType = ds.getRowType(0); } catch(e) {}

        // isModified 재확인
        var isModNow = false;
        try { isModNow = workForm.gf_ModifiedDS(ds); } catch(e) {}

        // gfn_callback 가로채기
        var origGfnCb = workForm.gfn_callback;
        workForm.gfn_callback = function(svcId, errCd, errMsg) {
            window._fnSaveResult = {
                svcId: String(svcId || ''),
                errCd: String(errCd || ''),
                errMsg: String(errMsg || ''),
                callbackType: 'gfn_callback',
                success: (errCd === 0 || errCd === '0' || errCd === 'SYS000')
            };
            window._fnSaveDone = true;
            workForm.gfn_callback = origGfnCb;
            try { origGfnCb.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        };

        // fn_callback도 가로채기
        var origFnCb = workForm.fn_callback;
        workForm.fn_callback = function(svcId, errCd, errMsg) {
            if (!window._fnSaveResult) {
                window._fnSaveResult = {
                    svcId: String(svcId || ''),
                    errCd: String(errCd || ''),
                    errMsg: String(errMsg || ''),
                    callbackType: 'fn_callback',
                    success: (errCd === 0 || errCd === '0' || errCd === 'SYS000')
                };
            } else {
                window._fnSaveResult.fnCallbackAlso = true;
                window._fnSaveResult.fnErrCd = String(errCd || '');
            }
            window._fnSaveDone = true;
            workForm.fn_callback = origFnCb;
            try { origFnCb.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        };

        // fn_save 직접 호출!
        try {
            workForm.fn_save();
            return JSON.stringify({
                called: true,
                newRowType: newRowType,
                isModifiedAfterTypeChange: isModNow
            });
        } catch(e) {
            return JSON.stringify({error: e.message, newRowType: newRowType});
        }
        """)
        logger.info(f"  fn_save 호출: {save_result}")

        # 콜백 대기
        logger.info("\n=== Step 5: fn_save 콜백 대기 ===")
        for i in range(20):
            time.sleep(0.5)
            done = driver.execute_script("return window._fnSaveDone === true;")
            if done:
                result = driver.execute_script("return JSON.stringify(window._fnSaveResult);")
                if result:
                    r = json.loads(result)
                    logger.info(f"  콜백 수신: {r}")
                break
        else:
            logger.warning("  콜백 타임아웃 (10초)")
            # fn_save에서 gf_ModifiedDS 검증 실패 시 return하여 콜백 없을 수 있음
            logger.info("  → gf_ModifiedDS 검증에서 이미 return했을 가능성")

        # XHR body 확인
        logger.info("\n=== Step 6: XHR body 확인 ===")
        xhr_data = driver.execute_script("return JSON.stringify(window._capturedSaveRequests || []);")
        if xhr_data:
            requests = json.loads(xhr_data)
            logger.info(f"  캡처된 요청 수: {len(requests)}")
            for idx, req in enumerate(requests):
                logger.info(f"\n  --- 요청 [{idx}] ---")
                logger.info(f"  URL: {req.get('method')} {req.get('url')}")
                logger.info(f"  body 길이: {req.get('bodyLength')}")
                preview = req.get('bodyPreview', '')
                if preview:
                    for line in preview.split('[RS]'):
                        line = line.strip()
                        if line:
                            logger.info(f"    {line[:200]}")

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
