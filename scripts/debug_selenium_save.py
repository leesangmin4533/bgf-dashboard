"""
기존 Selenium 방식으로 1건 발주 + 저장 테스트
서버가 현재 발주를 허용하는지 확인
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_selenium_save")


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
            if (this._captureUrl &&
                (this._captureUrl.indexOf('saveOrd') >= 0 ||
                 this._captureUrl.indexOf('selSearch') >= 0)) {
                window._capturedSaveRequests.push({
                    url: this._captureUrl,
                    method: this._captureMethod,
                    bodyLength: body ? body.length : 0,
                    timestamp: Date.now()
                });
            }
            return origSend.apply(this, arguments);
        };
        """)

        # gfn_callback 가로채기
        driver.execute_script("""
        window._lastSaveCallback = null;

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

        // gfn_callback 감시
        var origGfn = workForm.gfn_callback;
        workForm.gfn_callback = function(svcId, errCd, errMsg) {
            window._lastSaveCallback = {
                svcId: String(svcId || ''),
                errCd: String(errCd || ''),
                errMsg: String(errMsg || ''),
                time: Date.now()
            };
            workForm.gfn_callback = origGfn;
            return origGfn.call(workForm, svcId, errCd, errMsg);
        };
        """)

        # 1. 기존 방식으로 상품 입력
        logger.info("\n=== Step 1: 기존 Selenium 방식으로 상품 입력 ===")
        try:
            # OrderExecutor의 _input_product_code_optimized 직접 호출
            success = executor._input_product_code_optimized(item_cd, 0)
            logger.info(f"  상품 입력 결과: {success}")
        except Exception as e:
            logger.error(f"  상품 입력 에러: {e}")
            return

        time.sleep(2)

        # dataset 상태 확인
        ds_state = driver.execute_script("""
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
        var result = {rowCount: ds.getRowCount()};
        if (ds.getRowCount() > 0) {
            var lastRow = ds.getRowCount() - 1;
            result.lastRowType = ds.getRowType(lastRow);
            result.ITEM_CD = String(ds.getColumn(lastRow, 'ITEM_CD') || '');
            result.ITEM_NM = String(ds.getColumn(lastRow, 'ITEM_NM') || '');
            result.ORD_MUL_QTY = String(ds.getColumn(lastRow, 'ORD_MUL_QTY') || '');
            result.ORD_YMD = String(ds.getColumn(lastRow, 'ORD_YMD') || '');
            result.ITEM_CHK = String(ds.getColumn(lastRow, 'ITEM_CHK') || '');
            result.PYUN_QTY = String(ds.getColumn(lastRow, 'PYUN_QTY') || '');

            // 설정/NULL 컬럼 수
            var setCols = 0, nullCols = 0;
            for (var ci = 0; ci < ds.getColCount(); ci++) {
                var cid = ds.getColID(ci);
                var val = ds.getColumn(lastRow, cid);
                if (val !== null && val !== undefined && val !== '') setCols++;
                else nullCols++;
            }
            result.setCount = setCols;
            result.nullCount = nullCols;
        }
        return JSON.stringify(result);
        """)
        if ds_state:
            s = json.loads(ds_state)
            logger.info(f"  행 수: {s.get('rowCount')}")
            logger.info(f"  lastRowType: {s.get('lastRowType')} (1=N, 2=I, 4=U)")
            logger.info(f"  ITEM_CD: {s.get('ITEM_CD')}")
            logger.info(f"  ITEM_NM: {s.get('ITEM_NM')}")
            logger.info(f"  ORD_MUL_QTY: {s.get('ORD_MUL_QTY')}")
            logger.info(f"  ORD_YMD: {s.get('ORD_YMD')}")
            logger.info(f"  ITEM_CHK: {s.get('ITEM_CHK')}")
            logger.info(f"  PYUN_QTY: {s.get('PYUN_QTY')}")
            logger.info(f"  설정/NULL: {s.get('setCount')}/{s.get('nullCount')}")

        # 2. UI 저장 버튼 클릭 (기존 confirm_order 방식)
        logger.info("\n=== Step 2: UI 저장 버튼 클릭 ===")
        try:
            # fn_save 직접 호출 (저장 버튼 클릭과 동일)
            save_call_result = driver.execute_script("""
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
            try {
                workForm.fn_save();
                return 'called';
            } catch(e) {
                return 'error: ' + e.message;
            }
            """)
            logger.info(f"  fn_save 결과: {save_call_result}")
        except Exception as e:
            logger.error(f"  fn_save 에러: {e}")

        # 콜백 대기
        time.sleep(5)

        # 콜백 결과
        cb_result = driver.execute_script("return JSON.stringify(window._lastSaveCallback || null);")
        if cb_result and cb_result != 'null':
            cb = json.loads(cb_result)
            logger.info(f"  콜백: errCd={cb.get('errCd')}, errMsg={cb.get('errMsg')}")
            logger.info(f"  svcId: {cb.get('svcId')}")
        else:
            logger.warning("  콜백 수신 안됨 (gf_ModifiedDS 검증에서 리턴했을 수 있음)")

        # XHR 요청 확인
        xhr_data = driver.execute_script("return JSON.stringify(window._capturedSaveRequests || []);")
        if xhr_data:
            requests = json.loads(xhr_data)
            logger.info(f"\n  캡처된 XHR 요청: {len(requests)}개")
            for req in requests:
                logger.info(f"    {req.get('method')} {req.get('url')} (body={req.get('bodyLength')})")

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
