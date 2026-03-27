"""
gfn_transaction 전체 소스코드 확인 + 올바른 호출 경로 탐색
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_gfn_source")


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

        # 1. gfn_transaction 전체 소스코드
        logger.info("\n=== gfn_transaction 소스코드 ===")
        src = driver.execute_script("""
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
        return String(stbjForm.gfn_transaction);
        """)
        if src:
            logger.info(f"  길이: {len(src)}")
            # 500자씩 출력
            for i in range(0, len(src), 500):
                logger.info(f"  [{i}] {src[i:i+500]}")

        # 2. 저장 버튼 핸들러 찾기
        logger.info("\n=== 저장 버튼 관련 함수 ===")
        btn_info = driver.execute_script("""
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
        var result = {};

        // stbjForm에서 save/저장 관련 함수 찾기
        var saveFns = [];
        var allKeys = Object.keys(stbjForm);
        for (var ki = 0; ki < allKeys.length; ki++) {
            var k = allKeys[ki];
            if (k.toLowerCase().indexOf('save') >= 0 ||
                k.toLowerCase().indexOf('btn') >= 0 ||
                k.toLowerCase().indexOf('fn_') === 0) {
                var typ = typeof stbjForm[k];
                saveFns.push(k + ':' + typ);
            }
        }
        result.stbjForm_saveFns = saveFns;

        // workForm에서도
        var wfSaveFns = [];
        var wfKeys = Object.keys(workForm);
        for (var ki2 = 0; ki2 < wfKeys.length; ki2++) {
            var k2 = wfKeys[ki2];
            if (k2.toLowerCase().indexOf('save') >= 0 ||
                k2.toLowerCase().indexOf('btn') >= 0 ||
                k2.toLowerCase().indexOf('fn_') === 0) {
                var typ2 = typeof workForm[k2];
                wfSaveFns.push(k2 + ':' + typ2);
            }
        }
        result.workForm_saveFns = wfSaveFns;

        // fn_save 소스
        try {
            if (typeof stbjForm.fn_save === 'function') {
                result.fn_save_src = String(stbjForm.fn_save).substring(0, 1000);
            }
        } catch(e) {}

        // gfn_transaction 호출 경로: workForm에도 있는지
        result.workForm_hasGfnTx = typeof workForm.gfn_transaction;
        result.stbjForm_hasGfnTx = typeof stbjForm.gfn_transaction;

        // nexacro.Form.prototype.transaction 확인
        result.hasFormTransaction = typeof (nexacro.Form && nexacro.Form.prototype && nexacro.Form.prototype.transaction);

        // workForm.transaction 직접 확인
        try {
            result.workForm_hasTransaction = typeof workForm.transaction;
        } catch(e) { result.workForm_hasTransaction = 'err'; }

        return JSON.stringify(result);
        """)
        if btn_info:
            bi = json.loads(btn_info)
            logger.info(f"  stbjForm save functions: {bi.get('stbjForm_saveFns', [])}")
            logger.info(f"  workForm save functions: {bi.get('workForm_saveFns', [])}")
            logger.info(f"  workForm gfn_transaction: {bi.get('workForm_hasGfnTx')}")
            logger.info(f"  stbjForm gfn_transaction: {bi.get('stbjForm_hasGfnTx')}")
            logger.info(f"  Form.prototype.transaction: {bi.get('hasFormTransaction')}")
            logger.info(f"  workForm.transaction: {bi.get('workForm_hasTransaction')}")
            if bi.get('fn_save_src'):
                logger.info(f"  fn_save 소스:\n{bi['fn_save_src']}")

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
