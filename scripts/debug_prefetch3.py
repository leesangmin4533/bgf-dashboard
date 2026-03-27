"""
selSearch 응답 디버깅 — strOrdYmd 포함 + 다른 상품 시도
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_prefetch3")


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

        # 여러 상품 + strOrdYmd 포함/미포함 비교
        test_items = ["8801045571416", "8801043036016", "8809112345678"]

        for item_cd in test_items:
            logger.info(f"\n=== 상품: {item_cd} ===")
            for use_date in [False, True]:
                date_val = tomorrow if use_date else ''
                label = f"date={date_val}" if use_date else "date=(empty)"

                raw_text = driver.execute_script("""
                var itemCd = arguments[0];
                var dateVal = arguments[1];
                var RS = String.fromCharCode(0x1e);
                var US = String.fromCharCode(0x1f);

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
                parts.push('strOrdYmd=' + dateVal);
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

                var resp = await fetch('/stbj030/selSearch', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(10000)
                });
                var text = await resp.text();
                var records = text.split(RS);
                // 데이터 행 수 = 전체 레코드 - 헤더(SSV,ErrorCode,ErrorMsg,xm_tid,Dataset:dsItem,colDefs,빈행)
                var dataRows = 0;
                var lastNonEmpty = '';
                for (var ri = 0; ri < records.length; ri++) {
                    if (records[ri].length > 0 && ri > 5) {
                        dataRows++;
                        lastNonEmpty = records[ri].substring(0, 200);
                    }
                }
                return JSON.stringify({
                    status: resp.status,
                    length: text.length,
                    recordCount: records.length,
                    dataRows: dataRows,
                    lastNonEmpty: lastNonEmpty.replace(/[\x1f]/g, '|'),
                    errorCode: (text.match(/ErrorCode:string=(\\d+)/) || ['','?'])[1],
                });
                """, item_cd, date_val)

                if raw_text:
                    r = json.loads(raw_text)
                    logger.info(f"  {label}: status={r['status']}, len={r['length']}, records={r['recordCount']}, dataRows={r['dataRows']}, errCode={r['errorCode']}")
                    if r['dataRows'] > 0:
                        logger.info(f"    데이터: {r['lastNonEmpty'][:150]}")
                else:
                    logger.error(f"  {label}: None")

        # 실제 발주 가능한 상품 찾기 (DB에서)
        logger.info("\n=== DB에서 발주 가능 상품 3개 조회 ===")
        try:
            import sqlite3
            from src.settings.store_config import StoreConfig
            cfg = StoreConfig.load()
            common_db = cfg.common_db_path
            conn = sqlite3.connect(common_db)
            rows = conn.execute("""
                SELECT p.item_cd, p.item_nm
                FROM products p
                JOIN product_details pd ON p.item_cd = pd.item_cd
                WHERE pd.orderable_status = '가능'
                  AND pd.order_unit_qty >= 1
                ORDER BY RANDOM()
                LIMIT 3
            """).fetchall()
            conn.close()
            for row in rows:
                item_cd = row[0]
                logger.info(f"\n  DB 상품: {item_cd} ({row[1]})")
                raw_text = driver.execute_script("""
                var itemCd = arguments[0];
                var dateVal = arguments[1];
                var RS = String.fromCharCode(0x1e);
                var US = String.fromCharCode(0x1f);
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
                parts.push('strOrdYmd=' + dateVal);
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
                var resp = await fetch('/stbj030/selSearch', {
                    method: 'POST',
                    headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                    body: body,
                    signal: AbortSignal.timeout(10000)
                });
                var text = await resp.text();
                var records = text.split(RS);
                var dataRows = 0;
                var lastNonEmpty = '';
                for (var ri = 0; ri < records.length; ri++) {
                    if (records[ri].length > 0 && ri > 5) {
                        dataRows++;
                        lastNonEmpty = records[ri].substring(0, 300);
                    }
                }
                return JSON.stringify({
                    status: resp.status, length: text.length,
                    recordCount: records.length, dataRows: dataRows,
                    lastNonEmpty: lastNonEmpty.replace(/[\x1f]/g, '|'),
                });
                """, item_cd, tomorrow)
                if raw_text:
                    r = json.loads(raw_text)
                    logger.info(f"    len={r['length']}, dataRows={r['dataRows']}")
                    if r['dataRows'] > 0:
                        logger.info(f"    데이터: {r['lastNonEmpty'][:200]}")
        except Exception as e:
            logger.error(f"  DB 에러: {e}")

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
