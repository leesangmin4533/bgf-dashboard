"""
Row type U 확보 — ORD_MUL_QTY=0으로 applyChange, 실제값으로 setColumn → U type
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_rowtype_u2")


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
        window._xhrCaptures = [];
        var origOpen = XMLHttpRequest.prototype.open;
        var origSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._captureUrl = url; this._captureMethod = method;
            return origOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function(body) {
            if (this._captureUrl && this._captureUrl.indexOf('saveOrd') >= 0) {
                // row type 확인
                var RS = String.fromCharCode(0x1e);
                var parts = body ? body.split(RS) : [];
                var rowTypeLine = '';
                for (var pi = 0; pi < parts.length; pi++) {
                    if (parts[pi].charAt(0) === 'U' || parts[pi].charAt(0) === 'I' || parts[pi].charAt(0) === 'N') {
                        if (pi > 5) { // 데이터 행
                            rowTypeLine = parts[pi].substring(0, 5);
                            break;
                        }
                    }
                }
                window._xhrCaptures.push({
                    bodyLength: body ? body.length : 0,
                    rowType: rowTypeLine,
                    timestamp: Date.now()
                });
            }
            return origSend.apply(this, arguments);
        };
        """)

        # 프리페치
        logger.info("\n=== 프리페치 ===")
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
                        logger.info(f"  {len(f)}개 필드")

        if not fields:
            logger.error("  프리페치 실패")
            return

        # dataset 채우기: addRow + setColumn(ORD_MUL_QTY=0) + applyChange + setColumn(ORD_MUL_QTY=1) → U type
        logger.info("\n=== dataset U type 생성 ===")
        fields_json = json.dumps(fields)
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

        // Step A: clearData + addRow + 모든 필드 설정 (ORD_MUL_QTY=0)
        ds.clearData();
        var row = ds.addRow();
        var colsSet = 0;
        for (var colName in fields) {
            if (fields.hasOwnProperty(colName) && fields[colName] !== '' && fields[colName] != null) {
                try { ds.setColumn(row, colName, fields[colName]); colsSet++; } catch(e) {}
            }
        }
        ds.setColumn(row, 'ITEM_CD', itemCd);
        ds.setColumn(row, 'ORD_MUL_QTY', 0);  // ★ 0으로 설정
        ds.setColumn(row, 'ORD_YMD', dateStr);
        ds.setColumn(row, 'STORE_CD', '46513');

        var t1 = ds.getRowType(0);  // 2=I

        // Step B: applyChange() → 모든 변경 커밋 (I→N)
        ds.applyChange();
        var t2 = ds.getRowType(0);  // 1=N

        // Step C: ORD_MUL_QTY를 실제값(1)으로 변경 → N→U
        ds.setColumn(0, 'ORD_MUL_QTY', 1);
        var t3 = ds.getRowType(0);  // 4=U (hopefully!)

        var isMod = false;
        try { isMod = workForm.gf_ModifiedDS(ds); } catch(e) {}

        return JSON.stringify({
            colsSet: colsSet,
            t1_afterAdd: t1,
            t2_afterApply: t2,
            t3_afterSet: t3,
            isModified: isMod,
            ORD_MUL_QTY: String(ds.getColumn(0, 'ORD_MUL_QTY')),
            rowCount: ds.getRowCount()
        });
        """, fields_json, tomorrow, item_cd)

        if result:
            r = json.loads(result)
            logger.info(f"  t1 after addRow: {r.get('t1_afterAdd')} (2=I)")
            logger.info(f"  t2 after applyChange: {r.get('t2_afterApply')} (1=N)")
            logger.info(f"  t3 after setColumn(MUL_QTY=1): {r.get('t3_afterSet')} (4=U)")
            logger.info(f"  isModified: {r.get('isModified')}")
            logger.info(f"  ORD_MUL_QTY: {r.get('ORD_MUL_QTY')}")

            if r.get('t3_afterSet') == 4 and r.get('isModified'):
                logger.info("  ★ U type + isModified=True 성공!")
            else:
                logger.warning(f"  ★ U type 실패: rowType={r.get('t3_afterSet')}, isModified={r.get('isModified')}")

        # fn_save 호출
        logger.info("\n=== fn_save 호출 ===")
        driver.execute_script("""
        window._utestResult = null;
        window._utestDone = false;
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

        var origGfn = workForm.gfn_callback;
        workForm.gfn_callback = function(svcId, errCd, errMsg) {
            window._utestResult = {
                errCd: String(errCd || ''),
                errMsg: String(errMsg || '')
            };
            window._utestDone = true;
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

        for i in range(20):
            time.sleep(0.5)
            done = driver.execute_script("return window._utestDone === true;")
            if done:
                cb = driver.execute_script("return JSON.stringify(window._utestResult);")
                if cb:
                    c = json.loads(cb)
                    logger.info(f"  ★ 콜백: errCd={c.get('errCd')}, errMsg={c.get('errMsg')}")
                    if c.get('errCd') in ['0', 'SYS000']:
                        logger.info("  ✅ 저장 성공!!")
                    else:
                        logger.warning(f"  ❌ 저장 실패: {c.get('errCd')}")
                break
        else:
            logger.warning("  콜백 타임아웃")

        # XHR row type 확인
        xhr = driver.execute_script("return JSON.stringify(window._xhrCaptures || []);")
        if xhr:
            xhrs = json.loads(xhr)
            logger.info(f"\n  XHR 요청: {len(xhrs)}개")
            for x in xhrs:
                logger.info(f"    body={x.get('bodyLength')}, rowType='{x.get('rowType')}'")

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
