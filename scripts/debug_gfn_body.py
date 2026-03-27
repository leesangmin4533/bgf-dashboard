"""
gfn_transaction이 서버에 보내는 XHR body를 캡처하여 분석합니다.
NullPointerException 원인을 찾기 위해 실제 전송 데이터를 확인합니다.
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_gfn_body")


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

        # 1. XHR body 캡처 인터셉터 설치
        logger.info("\n=== Step 1: XHR body 캡처 인터셉터 설치 ===")
        driver.execute_script("""
        window._capturedSaveBody = null;
        window._capturedSaveUrl = null;
        window._capturedSaveResp = null;
        var origSend = XMLHttpRequest.prototype.send;
        var origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._debugUrl = url;
            this._debugMethod = method;
            return origOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function(body) {
            var url = this._debugUrl || '';
            if (url.indexOf('saveOrd') >= 0 || url.indexOf('Save') >= 0) {
                window._capturedSaveUrl = url;
                window._capturedSaveBody = typeof body === 'string' ? body : '';
                // 응답도 캡처
                var xhr = this;
                var origHandler = xhr.onreadystatechange;
                xhr.onreadystatechange = function() {
                    if (xhr.readyState === 4) {
                        window._capturedSaveResp = {
                            status: xhr.status,
                            text: xhr.responseText ? xhr.responseText.substring(0, 2000) : ''
                        };
                    }
                    if (origHandler) origHandler.apply(this, arguments);
                };
            }
            return origSend.apply(this, arguments);
        };
        """)
        logger.info("  XHR 인터셉터 설치됨")

        # 2. dataset 존재 확인 (dsGeneralGrid + dsSaveChk)
        logger.info("\n=== Step 2: dataset 존재 확인 ===")
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

        var r = {};

        // dsGeneralGrid
        var ds = workForm.gdList._binddataset;
        r.dsGeneralGrid = {
            exists: !!ds,
            type: typeof ds,
            colCount: ds ? ds.getColCount() : 0,
            rowCount: ds ? ds.getRowCount() : 0,
        };

        // dsSaveChk 탐색 — 다양한 경로
        r.dsSaveChk_paths = {};

        // 경로 1: workForm 직접
        try {
            var d1 = workForm.dsSaveChk;
            r.dsSaveChk_paths['workForm.dsSaveChk'] = d1 ? {type: typeof d1, hasAddRow: typeof d1.addRow} : 'null';
        } catch(e) { r.dsSaveChk_paths['workForm.dsSaveChk'] = 'err: ' + e.message; }

        // 경로 2: stbjForm 직접
        try {
            var d2 = stbjForm.dsSaveChk;
            r.dsSaveChk_paths['stbjForm.dsSaveChk'] = d2 ? {type: typeof d2, hasAddRow: typeof d2.addRow} : 'null';
        } catch(e) { r.dsSaveChk_paths['stbjForm.dsSaveChk'] = 'err: ' + e.message; }

        // 경로 3: 부모 폼
        try {
            var parent = stbjForm.div_workForm.form;
            var d3 = parent.dsSaveChk;
            r.dsSaveChk_paths['div_workForm.form.dsSaveChk'] = d3 ? {type: typeof d3, hasAddRow: typeof d3.addRow} : 'null';
        } catch(e) { r.dsSaveChk_paths['div_workForm.form.dsSaveChk'] = 'err: ' + e.message; }

        // 경로 4: Object.keys에서 'ds' 포함 항목 탐색
        try {
            var dsKeys = [];
            var allKeys = Object.keys(stbjForm);
            for (var ki = 0; ki < allKeys.length; ki++) {
                if (allKeys[ki].toLowerCase().indexOf('ds') === 0) {
                    var v = stbjForm[allKeys[ki]];
                    dsKeys.push(allKeys[ki] + ':' + typeof v);
                }
            }
            r.stbjForm_dsKeys = dsKeys;
        } catch(e) { r.stbjForm_dsKeys = ['err: ' + e.message]; }

        // 경로 5: workForm keys
        try {
            var wfDsKeys = [];
            var wfKeys = Object.keys(workForm);
            for (var ki2 = 0; ki2 < wfKeys.length; ki2++) {
                if (wfKeys[ki2].toLowerCase().indexOf('ds') === 0) {
                    var v2 = workForm[wfKeys[ki2]];
                    wfDsKeys.push(wfKeys[ki2] + ':' + typeof v2);
                }
            }
            r.workForm_dsKeys = wfDsKeys;
        } catch(e) { r.workForm_dsKeys = ['err: ' + e.message]; }

        // 경로 6: 폼의 all_dataset
        try {
            var datasets = [];
            if (stbjForm._datasets) {
                for (var dk in stbjForm._datasets) {
                    datasets.push(dk);
                }
            }
            r.stbjForm_datasets = datasets;
        } catch(e) { r.stbjForm_datasets = ['err: ' + e.message]; }

        return JSON.stringify(r);
        """)
        if ds_check:
            dc = json.loads(ds_check)
            logger.info(f"  dsGeneralGrid: {dc.get('dsGeneralGrid')}")
            logger.info(f"  dsSaveChk paths:")
            for k, v in dc.get('dsSaveChk_paths', {}).items():
                logger.info(f"    {k}: {v}")
            logger.info(f"  stbjForm_dsKeys: {dc.get('stbjForm_dsKeys', [])}")
            logger.info(f"  workForm_dsKeys: {dc.get('workForm_dsKeys', [])}")
            logger.info(f"  stbjForm_datasets: {dc.get('stbjForm_datasets', [])}")

        # 3. selSearch 프리페치 + dataset 채우기
        logger.info("\n=== Step 3: selSearch 프리페치 + dataset 채우기 ===")
        from src.order.direct_api_saver import DirectApiOrderSaver, PREFETCH_ITEMS_JS, POPULATE_DATASET_JS
        from src.collectors.direct_api_fetcher import extract_dsitem_all_columns

        # 프리페치
        prefetch_raw = driver.execute_script(PREFETCH_ITEMS_JS, [item_cd], 10000, tomorrow)
        if prefetch_raw:
            data = json.loads(prefetch_raw) if isinstance(prefetch_raw, str) else prefetch_raw
            if isinstance(data, list) and len(data) > 0:
                entry = data[0]
                if entry.get('ok') and entry.get('text'):
                    fields = extract_dsitem_all_columns(entry['text'])
                    if fields:
                        logger.info(f"  프리페치 성공: {len(fields)}개 필드")
                        # ORD_YMD 확인
                        logger.info(f"  selSearch ORD_YMD: {fields.get('ORD_YMD')}")
                        logger.info(f"  우리가 전달할 date: {tomorrow}")
                    else:
                        logger.error(f"  프리페치 파싱 실패")
                        fields = {}
                else:
                    logger.error(f"  프리페치 에러: {entry.get('error')}")
                    fields = {}
            else:
                logger.error(f"  프리페치: {data}")
                fields = {}
        else:
            fields = {}

        # dataset 채우기 (POPULATE_DATASET_JS)
        orders_json = json.dumps([{
            'item_cd': item_cd,
            'multiplier': 1,
            'ord_unit_qty': 1,
            'store_cd': '',
            'fields': fields,
        }], ensure_ascii=False)

        populate_result = driver.execute_script(POPULATE_DATASET_JS, orders_json, tomorrow)
        logger.info(f"  POPULATE 결과: {populate_result}")

        # 4. dataset 값 덤프
        logger.info("\n=== Step 4: dataset 값 덤프 ===")
        dump = driver.execute_script("""
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

        var result = {rowCount: ds.getRowCount(), columns: {}};
        if (ds.getRowCount() > 0) {
            for (var ci = 0; ci < ds.getColCount(); ci++) {
                var colId = ds.getColID(ci);
                var val = ds.getColumn(0, colId);
                result.columns[colId] = val == null ? 'NULL' : String(val);
            }
        }

        // dsSaveChk 값도 확인
        result.dsSaveChk = {exists: false};
        try {
            // dsSaveChk은 gfn_transaction의 inDS에서 참조됨
            // 이름으로 직접 접근 시도
            var chk = stbjForm.dsSaveChk || workForm.dsSaveChk;
            if (!chk) {
                // div_workForm 레벨
                chk = stbjForm.div_workForm.form.dsSaveChk;
            }
            if (chk && typeof chk.getRowCount === 'function') {
                result.dsSaveChk = {
                    exists: true,
                    rowCount: chk.getRowCount(),
                    colCount: chk.getColCount(),
                };
                if (chk.getRowCount() > 0) {
                    var chkCols = {};
                    for (var cci = 0; cci < chk.getColCount(); cci++) {
                        var ccId = chk.getColID(cci);
                        chkCols[ccId] = String(chk.getColumn(0, ccId) || '');
                    }
                    result.dsSaveChk.row0 = chkCols;
                }
            }
        } catch(e) {
            result.dsSaveChk = {error: e.message};
        }

        return JSON.stringify(result);
        """)
        if dump:
            d = json.loads(dump)
            logger.info(f"  dsGeneralGrid rowCount: {d.get('rowCount')}")
            cols = d.get('columns', {})
            # 중요 컬럼들
            key_cols = ['STORE_CD', 'ORD_YMD', 'ITEM_CD', 'ITEM_NM', 'ORD_MUL_QTY',
                       'ORD_UNIT_QTY', 'PITEM_ID', 'MID_NM', 'PROFIT_RATE', 'HQ_MAEGA_SET',
                       'JIP_ITEM_CD', 'PRE_STORE_CD', 'ORD_UNIT', 'ORD_TURN_HMS',
                       'ORD_MULT_ULMT', 'ORD_MULT_LLMT', 'CT_ITEM_YN', 'CUT_ITEM_YN']
            for kc in key_cols:
                logger.info(f"    {kc}: {cols.get(kc, '(not in dump)')}")
            # NULL인 컬럼들
            null_cols = [k for k, v in cols.items() if v == 'NULL']
            logger.info(f"  NULL 컬럼 ({len(null_cols)}개): {null_cols}")
            # dsSaveChk
            logger.info(f"  dsSaveChk: {d.get('dsSaveChk')}")

        # 5. gfn_transaction 호출 (Alert 자동 수락)
        logger.info("\n=== Step 5: gfn_transaction 호출 + XHR body 캡처 ===")
        # Alert 자동 수락 설정
        driver.execute_script("""
        window._origAlert = window.alert;
        window._lastAlert = null;
        window.alert = function(msg) {
            window._lastAlert = String(msg);
        };
        """)

        from src.order.direct_api_saver import CALL_GFN_TRANSACTION_JS
        tx_result = driver.execute_script(CALL_GFN_TRANSACTION_JS, 1)
        logger.info(f"  gfn_transaction 호출 결과: {tx_result}")

        # 3초 대기 (서버 응답 + XHR 캡처)
        time.sleep(5)

        # 6. 캡처된 데이터 확인
        logger.info("\n=== Step 6: 캡처된 XHR body ===")
        captured = driver.execute_script("""
        return JSON.stringify({
            url: window._capturedSaveUrl || 'none',
            bodyLength: window._capturedSaveBody ? window._capturedSaveBody.length : 0,
            bodyPreview: window._capturedSaveBody ? window._capturedSaveBody.substring(0, 2000).replace(/[\x1e\x1f]/g, '|') : 'none',
            resp: window._capturedSaveResp || {text: 'none'},
            lastAlert: window._lastAlert || 'none',
        });
        """)
        if captured:
            c = json.loads(captured)
            logger.info(f"  URL: {c.get('url')}")
            logger.info(f"  body length: {c.get('bodyLength')}")
            logger.info(f"  lastAlert: {c.get('lastAlert')}")
            logger.info(f"  resp: {json.dumps(c.get('resp', {}), ensure_ascii=False)[:300]}")
            body_preview = c.get('bodyPreview', '')
            # 레코드별 분리
            records = body_preview.split('|')
            logger.info(f"  body records (| separated): {len(records)}")
            for i, rec in enumerate(records[:30]):
                logger.info(f"    [{i}] {rec[:150]}")

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
