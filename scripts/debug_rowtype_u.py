"""
Row type I → U 변환 후 fn_save 테스트
1. addRow + setColumn (모든 필드)
2. applyChange() → I→N 변환
3. setColumn(ORD_MUL_QTY) → N→U 변환
4. fn_save 호출 → 99999 해결되는지 확인
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_rowtype_u")


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

        # XHR 인터셉터
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
                    bodyLength: body ? body.length : 0,
                    bodyFull: body || '',
                    timestamp: Date.now()
                });
            }
            return origSend.apply(this, arguments);
        };
        """)

        # 1. 프리페치
        logger.info("\n=== Step 1: 프리페치 ===")
        from src.order.direct_api_saver import PREFETCH_ITEMS_JS
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
                        logger.info(f"  성공: {len(fields)}개 필드")

        if not prefetch_data:
            logger.error("  프리페치 실패")
            return

        # 2. dataset 채우기 + applyChange + setColumn for U type
        logger.info("\n=== Step 2: dataset 채우기 + applyChange → U type ===")
        fields_json = json.dumps(prefetch_data[item_cd])
        result = driver.execute_script("""
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

        // Step A: clearData + addRow + setColumn (모든 필드)
        ds.clearData();
        var row = ds.addRow();
        var colsSet = 0;
        for (var colName in fields) {
            if (fields.hasOwnProperty(colName) && fields[colName] !== '' && fields[colName] != null) {
                try { ds.setColumn(row, colName, fields[colName]); colsSet++; } catch(e) {}
            }
        }
        ds.setColumn(row, 'ITEM_CD', itemCd);
        ds.setColumn(row, 'ORD_MUL_QTY', 1);
        ds.setColumn(row, 'ORD_YMD', dateStr);
        ds.setColumn(row, 'STORE_CD', '46513');

        var rowTypeAfterAdd = ds.getRowType(0);

        // Step B: applyChange() → I→N
        var hasApplyChange = typeof ds.applyChange === 'function';
        if (hasApplyChange) {
            ds.applyChange();
        }
        var rowTypeAfterApply = ds.getRowType(0);

        // Step C: setColumn(ORD_MUL_QTY) → N→U
        ds.setColumn(0, 'ORD_MUL_QTY', 1);
        var rowTypeAfterSet = ds.getRowType(0);

        // isModified 확인
        var isMod = false;
        try { isMod = workForm.gf_ModifiedDS(ds); } catch(e) {}

        return JSON.stringify({
            colsSet: colsSet,
            rowTypeAfterAdd: rowTypeAfterAdd,
            hasApplyChange: hasApplyChange,
            rowTypeAfterApply: rowTypeAfterApply,
            rowTypeAfterSet: rowTypeAfterSet,
            isModified: isMod,
            rowCount: ds.getRowCount()
        });
        """, fields_json, tomorrow, item_cd)

        if result:
            r = json.loads(result)
            logger.info(f"  컬럼 설정: {r.get('colsSet')}개")
            logger.info(f"  rowType after addRow: {r.get('rowTypeAfterAdd')} (2=I)")
            logger.info(f"  hasApplyChange: {r.get('hasApplyChange')}")
            logger.info(f"  rowType after applyChange: {r.get('rowTypeAfterApply')} (1=N)")
            logger.info(f"  rowType after setColumn: {r.get('rowTypeAfterSet')} (4=U)")
            logger.info(f"  isModified: {r.get('isModified')}")

        # 3. fn_save 호출
        logger.info("\n=== Step 3: fn_save 호출 ===")
        window_init = driver.execute_script("""
        window._rowTypeTestResult = null;
        window._rowTypeTestDone = false;
        """)

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

        // gfn_callback 가로채기
        var origGfn = workForm.gfn_callback;
        workForm.gfn_callback = function(svcId, errCd, errMsg) {
            window._rowTypeTestResult = {
                svcId: String(svcId || ''),
                errCd: String(errCd || ''),
                errMsg: String(errMsg || '')
            };
            window._rowTypeTestDone = true;
            workForm.gfn_callback = origGfn;
            try { origGfn.call(workForm, svcId, errCd, errMsg); } catch(e) {}
        };

        try {
            workForm.fn_save();
            return 'called';
        } catch(e) {
            return 'error: ' + e.message;
        }
        """)
        logger.info(f"  fn_save: {save_result}")

        # 콜백 대기
        for i in range(20):
            time.sleep(0.5)
            done = driver.execute_script("return window._rowTypeTestDone === true;")
            if done:
                cb = driver.execute_script("return JSON.stringify(window._rowTypeTestResult);")
                if cb:
                    c = json.loads(cb)
                    logger.info(f"  콜백: errCd={c.get('errCd')}, errMsg={c.get('errMsg')}")
                break
        else:
            logger.warning("  콜백 타임아웃 — gf_ModifiedDS에서 리턴했을 수 있음")

        # XHR body 확인 (row type 확인)
        logger.info("\n=== Step 4: XHR body row type 확인 ===")
        xhr_data = driver.execute_script("return JSON.stringify(window._capturedSaveRequests || []);")
        if xhr_data:
            requests = json.loads(xhr_data)
            logger.info(f"  캡처된 요청: {len(requests)}개")
            if requests:
                body = requests[-1].get('bodyFull', '')
                parts = body.split('\x1e')
                for pi, part in enumerate(parts):
                    if 'Dataset:dsGeneralGrid' in part or (pi > 0 and parts[pi-1] and 'dsGeneralGrid' in parts[pi-1]):
                        clean = part.replace('\x1f', '|')
                        if clean.startswith('U') or clean.startswith('I') or clean.startswith('N'):
                            logger.info(f"  데이터 행: {clean[:100]}")
                            logger.info(f"  Row type: {clean[0]} (I=Insert, U=Update, N=Normal)")

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
