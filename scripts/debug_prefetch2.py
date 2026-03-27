"""
selSearch 응답 파싱 디버깅 — PREFETCH_ITEMS_JS 전체 흐름 테스트
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger
from src.collectors.direct_api_fetcher import extract_dsitem_all_columns, parse_ssv_dataset

logger = get_logger("debug_prefetch2")


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
        item_cd = "8801045571416"

        # 1. selSearch 직접 호출하여 전체 응답 텍스트 가져오기
        logger.info("\n=== Step 1: selSearch 응답 전체 텍스트 ===")
        raw_text = driver.execute_script("""
        var itemCd = arguments[0];
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

        var resp = await fetch('/stbj030/selSearch', {
            method: 'POST',
            headers: {'Content-Type': 'text/plain;charset=UTF-8'},
            body: body,
            signal: AbortSignal.timeout(10000)
        });
        return await resp.text();
        """, item_cd)

        if raw_text:
            logger.info(f"  응답 길이: {len(raw_text)}")
            # RS로 분리
            records = raw_text.split('\x1e')
            logger.info(f"  레코드 수: {len(records)}")
            for i, rec in enumerate(records):
                clean = rec.replace('\x1f', ' | ')
                logger.info(f"  [{i}] {clean[:200]}")

            # 2. extract_dsitem_all_columns 호출
            logger.info("\n=== Step 2: extract_dsitem_all_columns 파싱 ===")
            fields = extract_dsitem_all_columns(raw_text)
            if fields:
                logger.info(f"  성공: {len(fields)}개 필드")
                for k, v in sorted(fields.items()):
                    logger.info(f"    {k}: {v}")
            else:
                logger.warning("  실패: None 반환")

                # parse_ssv_dataset 직접 호출
                logger.info("\n  parse_ssv_dataset 디버깅:")
                ds = parse_ssv_dataset(raw_text, 'ITEM_NM')
                if ds:
                    logger.info(f"    columns: {ds.get('columns', [])[:10]}")
                    logger.info(f"    rows 수: {len(ds.get('rows', []))}")
                    if ds['rows']:
                        logger.info(f"    첫 행: {ds['rows'][0][:10]}")
                else:
                    logger.warning("    parse_ssv_dataset도 None 반환")

            # 3. PREFETCH_ITEMS_JS 전체 흐름 테스트
            logger.info("\n=== Step 3: PREFETCH_ITEMS_JS 전체 흐름 ===")
            from src.order.direct_api_saver import PREFETCH_ITEMS_JS
            prefetch_raw = driver.execute_script(PREFETCH_ITEMS_JS, [item_cd], 10000)
            logger.info(f"  raw type: {type(prefetch_raw)}")
            if prefetch_raw:
                logger.info(f"  raw len: {len(str(prefetch_raw))}")
                logger.info(f"  raw preview: {str(prefetch_raw)[:500]}")
                try:
                    data = json.loads(prefetch_raw) if isinstance(prefetch_raw, str) else prefetch_raw
                    logger.info(f"  parsed type: {type(data)}")
                    if isinstance(data, list):
                        for entry in data:
                            logger.info(f"    entry keys: {list(entry.keys()) if isinstance(entry, dict) else 'not dict'}")
                            if isinstance(entry, dict):
                                logger.info(f"    ok: {entry.get('ok')}, itemCd: {entry.get('itemCd')}")
                                logger.info(f"    error: {entry.get('error', '(none)')}")
                                if entry.get('text'):
                                    logger.info(f"    text len: {len(entry['text'])}")
                                    logger.info(f"    text preview: {entry['text'][:200].replace(chr(0x1e), '|').replace(chr(0x1f), ',')}")
                    elif isinstance(data, dict):
                        logger.info(f"  dict: {data}")
                except Exception as e:
                    logger.error(f"  파싱 에러: {e}")
            else:
                logger.warning(f"  raw is None/empty: {repr(prefetch_raw)}")

        else:
            logger.error("  응답 None")

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
