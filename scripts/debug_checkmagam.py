"""
fn_checkMagam + fn_postInit + form_onload 코드 확인 + checkMagam 호출 테스트
핵심: lvOrdYmd가 비어있으면 발주 불가 → checkMagam으로 발주일 설정 필요
"""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.sales_analyzer import SalesAnalyzer
from src.order.order_executor import OrderExecutor
from src.utils.logger import get_logger

logger = get_logger("debug_magam")

# workForm 접근 경로
WF_PATH = "app.mainframe.all.HFrameSet00.all.VFrameSet00.all.FrameSet.all.STBJ030_M0.form.div_workForm.form.div_work_01.form"


def main():
    analyzer = SalesAnalyzer()
    try:
        analyzer.setup_driver()
        analyzer.connect()
        analyzer.do_login()
        logger.info("로그인 성공")

        driver = analyzer.driver
        executor = OrderExecutor(driver)
        if not executor.navigate_to_single_order():
            logger.error("메뉴 이동 실패")
            return
        logger.info("메뉴 이동 성공")
        time.sleep(3)

        # 1. fn_checkMagam + fn_postInit + form_onload 코드
        logger.info("\n=== 1. 주요 함수 코드 ===")
        code_result = driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var stbj = app.mainframe.all.HFrameSet00.all.VFrameSet00.all.FrameSet.all.STBJ030_M0;
            var wf = stbj.form.div_workForm.form.div_work_01.form;
            var result = {};

            var funcNames = [
                'fn_checkMagam', 'fn_postInit', 'form_onload', 'fn_afterFormOnload',
                'fn_initBalju', 'fn_addNewRecord', 'fn_postSave', 'fn_postSaveClose',
                'fn_selSearch', 'fn_postSelSearch', 'messageCallSave'
            ];
            for (var i = 0; i < funcNames.length; i++) {
                if (wf[funcNames[i]]) {
                    result[funcNames[i]] = wf[funcNames[i]].toString();
                }
            }

            // dsCheckMagam 상태
            if (wf.dsCheckMagam) {
                result.dsCheckMagam_rows = wf.dsCheckMagam.getRowCount();
                result.dsCheckMagam_cols = wf.dsCheckMagam.getColCount();
                if (wf.dsCheckMagam.getColCount() > 0) {
                    var cols = [];
                    for (var c = 0; c < wf.dsCheckMagam.getColCount(); c++) {
                        cols.push(wf.dsCheckMagam.getColID(c));
                    }
                    result.dsCheckMagam_columns = cols;
                }
                if (wf.dsCheckMagam.getRowCount() > 0) {
                    var data = {};
                    for (var c2 = 0; c2 < wf.dsCheckMagam.getColCount(); c2++) {
                        data[wf.dsCheckMagam.getColID(c2)] = wf.dsCheckMagam.getColumn(0, wf.dsCheckMagam.getColID(c2));
                    }
                    result.dsCheckMagam_data = data;
                }
            } else {
                result.dsCheckMagam = 'NOT FOUND';
            }

            // GV_ORD_YMD
            try {
                result.GV_ORD_YMD = String(app.getVariable('GV_ORD_YMD') || '');
            } catch(e) { result.GV_ORD_YMD_error = e.message; }

            // GV_RCV_ID
            try {
                result.GV_RCV_ID = String(app.getVariable('GV_RCV_ID') || '');
            } catch(e) {}

            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        if code_result:
            cr = json.loads(code_result)
            for k, v in sorted(cr.items()):
                if isinstance(v, str) and len(v) > 100:
                    logger.info(f"\n  === {k} ===")
                    # 줄바꿈 포맷팅
                    code = v.replace(';', ';\n  ').replace('{', '{\n  ').replace('}', '\n  }')
                    for line in code.split('\n'):
                        logger.info(f"  {line.rstrip()}")
                elif isinstance(v, (dict, list)):
                    logger.info(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    logger.info(f"  {k}: {v}")

        # 2. fn_checkMagam 직접 호출 테스트
        logger.info("\n=== 2. fn_checkMagam 호출 테스트 ===")
        magam_result = driver.execute_script("""
        return new Promise(function(resolve) {
            try {
                var app = nexacro.getApplication();
                var stbj = app.mainframe.all.HFrameSet00.all.VFrameSet00.all.FrameSet.all.STBJ030_M0;
                var wf = stbj.form.div_workForm.form.div_work_01.form;

                // 콜백 오버라이드
                var origCallback = wf.fn_callback;
                var callbackCalled = false;

                wf.fn_callback = function(svcID, errorCode, errorMsg) {
                    callbackCalled = true;
                    var result = {
                        svcID: svcID,
                        errorCode: String(errorCode),
                        errorMsg: String(errorMsg)
                    };

                    // dsCheckMagam 확인
                    if (wf.dsCheckMagam && wf.dsCheckMagam.getRowCount() > 0) {
                        result.ORD_YMD = wf.dsCheckMagam.getColumn(0, 'ORD_YMD');
                        result.dsCheckMagam_rows = wf.dsCheckMagam.getRowCount();
                        // 모든 컬럼 덤프
                        var data = {};
                        for (var c = 0; c < wf.dsCheckMagam.getColCount(); c++) {
                            data[wf.dsCheckMagam.getColID(c)] = String(wf.dsCheckMagam.getColumn(0, wf.dsCheckMagam.getColID(c)) || '');
                        }
                        result.dsCheckMagam_full = data;
                    }

                    result.lvOrdYmd_after = String(wf.lvOrdYmd || '');
                    result.GV_ORD_YMD_after = String(app.getVariable('GV_ORD_YMD') || '');

                    // 원래 콜백 복원
                    wf.fn_callback = origCallback;

                    // 원래 콜백도 실행 (부수효과 포함)
                    try {
                        origCallback.call(wf, svcID, errorCode, errorMsg);
                    } catch(e) {
                        result.origCallback_error = e.message;
                    }

                    resolve(JSON.stringify(result));
                };

                // fn_checkMagam 호출
                wf.fn_checkMagam();

                // 타임아웃
                setTimeout(function() {
                    if (!callbackCalled) {
                        wf.fn_callback = origCallback;
                        resolve(JSON.stringify({error: 'checkMagam callback timeout 15s'}));
                    }
                }, 15000);
            } catch(e) { resolve(JSON.stringify({error: e.message})); }
        });
        """)
        if magam_result:
            mr = json.loads(magam_result)
            for k, v in sorted(mr.items()):
                if isinstance(v, dict):
                    logger.info(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    logger.info(f"  {k}: {v}")

        # 3. checkMagam 후 lvOrdYmd 확인 + save 테스트 (1건)
        logger.info("\n=== 3. checkMagam 후 상태 확인 ===")
        post_state = driver.execute_script("""
        try {
            var app = nexacro.getApplication();
            var stbj = app.mainframe.all.HFrameSet00.all.VFrameSet00.all.FrameSet.all.STBJ030_M0;
            var wf = stbj.form.div_workForm.form.div_work_01.form;
            return JSON.stringify({
                lvOrdYmd: String(wf.lvOrdYmd || ''),
                GV_ORD_YMD: String(app.getVariable('GV_ORD_YMD') || ''),
                fv_PyunsuId: String(wf.fv_PyunsuId || ''),
                fv_OrdInputFlag: String(wf.fv_OrdInputFlag || ''),
                dsGeneralGrid_rows: wf.dsGeneralGrid.getRowCount(),
                dsCheckMagam_rows: wf.dsCheckMagam ? wf.dsCheckMagam.getRowCount() : -1
            });
        } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        if post_state:
            ps = json.loads(post_state)
            for k, v in sorted(ps.items()):
                logger.info(f"  {k}: {v}")

    except Exception as e:
        logger.error(f"오류: {e}", exc_info=True)
    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass
        logger.info("완료")


if __name__ == "__main__":
    main()
