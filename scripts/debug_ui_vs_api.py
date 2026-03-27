"""
UI 입력 vs Direct API 입력 dataset 상태 비교
1. Selenium으로 상품코드 입력 → dataset 상태 캡처
2. Direct API 프리페치 입력 → dataset 상태 캡처
3. 차이점 분석
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_ui_vs_api")


def capture_dataset(driver, label):
    """현재 dsGeneralGrid 전체 상태 캡처"""
    raw = driver.execute_script("""
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
    var result = {
        rowCount: ds.getRowCount(),
        colCount: ds.getColCount()
    };

    // 모든 컬럼명
    var cols = [];
    for (var ci = 0; ci < ds.getColCount(); ci++) {
        cols.push(ds.getColID(ci));
    }
    result.columns = cols;

    // 각 행의 데이터
    var rows = [];
    for (var ri = 0; ri < ds.getRowCount(); ri++) {
        var row = {};
        row._rowType = ds.getRowType(ri);
        for (var ci2 = 0; ci2 < ds.getColCount(); ci2++) {
            var cid = ds.getColID(ci2);
            var val = ds.getColumn(ri, cid);
            row[cid] = (val === null || val === undefined) ? null : String(val);
        }
        rows.push(row);
    }
    result.rows = rows;

    return JSON.stringify(result);
    """)
    return json.loads(raw) if raw else None


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

        # ==================================
        # 방법 1: Selenium으로 UI 입력
        # ==================================
        logger.info("\n=== 방법 1: Selenium UI 입력 ===")

        # 기존 OrderExecutor 방식으로 UI 입력
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        # 셀 활성화 (기존 _activate_grid_cell 방식)
        driver.execute_script("""
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
        workForm.gdList.setFocus();
        """)
        time.sleep(0.5)

        # ITEM_CD 컬럼 인덱스 찾기
        col_idx = driver.execute_script("""
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
        for (var c = 0; c < ds.getColCount(); c++) {
            if (ds.getColID(c) === 'ITEM_CD') return c;
        }
        return 13;  // fallback
        """)
        logger.info(f"  ITEM_CD 컬럼 인덱스: {col_idx}")

        # 그리드 body 클릭 (셀 활성화) - 기존 방식처럼 DOM 요소 클릭
        try:
            from selenium.webdriver.common.by import By
            grid_divs = driver.find_elements(By.CSS_SELECTOR, "div[id*='gdList']")
            if grid_divs:
                # body div 찾기
                for div in grid_divs:
                    div_id = div.get_attribute("id") or ""
                    if "body" in div_id.lower():
                        ActionChains(driver).click(div).perform()
                        logger.info(f"  그리드 body 클릭: {div_id}")
                        break
                else:
                    ActionChains(driver).click(grid_divs[0]).perform()
                    logger.info(f"  그리드 div 클릭: {grid_divs[0].get_attribute('id')}")
        except Exception as e:
            logger.warning(f"  그리드 클릭 실패: {e}")

        time.sleep(0.5)

        # Tab으로 ITEM_CD 셀까지 이동 (여러번) 또는 직접 ActionChains 입력
        # 기존 OrderExecutor는 ActionChains로 직접 코드 입력
        actions = ActionChains(driver)
        actions.send_keys(item_cd)
        actions.send_keys(Keys.ENTER)
        actions.perform()
        logger.info(f"  상품코드 {item_cd} 입력 + Enter")

        # 서버 조회 대기
        time.sleep(3)

        # UI 입력 후 dataset 캡처
        ui_data = capture_dataset(driver, "UI")
        if ui_data and ui_data['rows']:
            logger.info(f"  행 수: {ui_data['rowCount']}")
            ui_row = ui_data['rows'][0]
            logger.info(f"  rowType: {ui_row.get('_rowType')} (1=Normal, 2=Insert, 4=Update)")
            logger.info(f"  ITEM_CD: {ui_row.get('ITEM_CD')}")
            logger.info(f"  ORD_MUL_QTY: {ui_row.get('ORD_MUL_QTY')}")
            logger.info(f"  ORD_YMD: {ui_row.get('ORD_YMD')}")
            logger.info(f"  PYUN_QTY: {ui_row.get('PYUN_QTY')}")
            logger.info(f"  ITEM_CHK: {ui_row.get('ITEM_CHK')}")

            # NULL이 아닌 필드 개수
            non_null = {k: v for k, v in ui_row.items() if v is not None and v != '' and k != '_rowType'}
            null_cols = [k for k, v in ui_row.items() if (v is None or v == '') and k != '_rowType']
            logger.info(f"  설정된 필드: {len(non_null)}개")
            logger.info(f"  NULL 필드: {len(null_cols)}개 — {null_cols}")
        else:
            logger.warning("  UI 입력 후 데이터 없음")
            # 혹시 행이 없으면 빈 행 추가된 상태일 수 있음
            logger.info(f"  raw: {json.dumps(ui_data, ensure_ascii=False)[:500]}")

        # dataset 초기화
        driver.execute_script("""
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
        workForm.gdList._binddataset.clearData();
        """)
        time.sleep(1)

        # ==================================
        # 방법 2: Direct API 프리페치 입력
        # ==================================
        logger.info("\n=== 방법 2: Direct API 프리페치 입력 ===")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')

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

        if prefetch_data:
            orders_json = json.dumps([{
                "item_cd": item_cd,
                "multiplier": 1,
                "store_cd": "46513",
                "ord_unit_qty": prefetch_data[item_cd].get('ORD_UNIT_QTY', ''),
                "fields": prefetch_data[item_cd]
            }])
            driver.execute_script(POPULATE_DATASET_JS, orders_json, tomorrow)

        api_data = capture_dataset(driver, "API")
        if api_data and api_data['rows']:
            api_row = api_data['rows'][0]
            logger.info(f"  rowType: {api_row.get('_rowType')} (1=Normal, 2=Insert, 4=Update)")
            logger.info(f"  ITEM_CD: {api_row.get('ITEM_CD')}")
            logger.info(f"  ORD_MUL_QTY: {api_row.get('ORD_MUL_QTY')}")
            logger.info(f"  ORD_YMD: {api_row.get('ORD_YMD')}")
            logger.info(f"  PYUN_QTY: {api_row.get('PYUN_QTY')}")
            logger.info(f"  ITEM_CHK: {api_row.get('ITEM_CHK')}")

            non_null_api = {k: v for k, v in api_row.items() if v is not None and v != '' and k != '_rowType'}
            null_cols_api = [k for k, v in api_row.items() if (v is None or v == '') and k != '_rowType']
            logger.info(f"  설정된 필드: {len(non_null_api)}개")
            logger.info(f"  NULL 필드: {len(null_cols_api)}개 — {null_cols_api}")

        # ==================================
        # 비교
        # ==================================
        if ui_data and ui_data['rows'] and api_data and api_data['rows']:
            logger.info("\n=== 비교: UI vs API ===")
            ui_row = ui_data['rows'][0]
            api_row = api_data['rows'][0]

            diffs = []
            for col in ui_data['columns']:
                ui_val = ui_row.get(col)
                api_val = api_row.get(col)
                if ui_val != api_val:
                    diffs.append({
                        'col': col,
                        'ui': ui_val,
                        'api': api_val
                    })

            logger.info(f"  차이 컬럼 수: {len(diffs)}")
            for d in diffs:
                logger.info(f"    {d['col']}: UI='{d['ui']}' vs API='{d['api']}'")

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
