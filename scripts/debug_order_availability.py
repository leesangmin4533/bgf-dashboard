"""
발주 가능 여부 진단 + Direct API 저장 테스트

1. 발주 가능 상태 확인 (fv_OrdYn, fv_OrdClose 등)
2. HTTP 응답 상세 확인 (status, headers, body)
3. selOrdYn 엔드포인트 테스트
4. Direct API 저장 1건 테스트

사용법:
    python scripts/debug_order_availability.py
    python scripts/debug_order_availability.py --save-test   # 실제 저장 테스트 포함
"""
import json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.logger import get_logger

logger = get_logger("debug_order_avail")


def main():
    save_test = '--save-test' in sys.argv

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

        # ===== Step 1: 발주 가능 상태 확인 =====
        logger.info("\n=== Step 1: 발주 가능 상태 확인 ===")
        from src.order.direct_api_saver import CHECK_ORDER_AVAILABILITY_JS
        avail_result = driver.execute_script(CHECK_ORDER_AVAILABILITY_JS)
        if avail_result:
            avail = json.loads(avail_result)
            logger.info(f"  available: {avail.get('available')}")
            logger.info(f"  ordYn: '{avail.get('ordYn', '')}'")
            logger.info(f"  ordClose: '{avail.get('ordClose', '')}'")
            logger.info(f"  serverTime: '{avail.get('serverTime', '')}'")
            logger.info(f"  storeCd: '{avail.get('storeCd', '')}'")
            if avail.get('workFormVars'):
                logger.info(f"  workForm 발주변수:")
                for k, v in sorted(avail['workFormVars'].items()):
                    logger.info(f"    {k}: {v}")
            if avail.get('stbjFormVars'):
                logger.info(f"  stbjForm 발주변수:")
                for k, v in sorted(avail['stbjFormVars'].items()):
                    logger.info(f"    {k}: {v}")
        else:
            logger.warning("  응답 없음")

        # ===== Step 2: 서버 시간 및 세션 확인 =====
        logger.info("\n=== Step 2: 서버 시간 및 세션 변수 ===")
        session_info = driver.execute_script("""
        var app = nexacro.getApplication();
        var result = {};
        var varNames = [
            'GV_USERFLAG', 'SS_STORE_CD', 'SS_USER_NO', 'SS_STORE_NM',
            'SS_SLC_CD', 'SS_LOC_CD', 'SS_FC_CD', 'SS_SERVER_TIME',
            'SS_LOGIN_USER_NO', 'SS_USER_GRP_ID'
        ];
        for (var i = 0; i < varNames.length; i++) {
            try { result[varNames[i]] = String(app.getVariable(varNames[i]) || ''); } catch(e) {}
        }
        result.browserTime = new Date().toISOString();
        return JSON.stringify(result);
        """)
        if session_info:
            si = json.loads(session_info)
            for k, v in sorted(si.items()):
                logger.info(f"  {k}: {v}")

        # ===== Step 3: HTTP 응답 상세 ─ 빈 saveOrd 호출 =====
        logger.info("\n=== Step 3: 빈 saveOrd HTTP 응답 상세 ===")
        http_test = driver.execute_script("""
        return new Promise(function(resolve) {
            var RS = String.fromCharCode(0x1e);
            var app = nexacro.getApplication();
            var parts = ['SSV:utf-8'];
            parts.push('GV_USERFLAG=' + (app.getVariable('GV_USERFLAG') || ''));
            parts.push('SS_STORE_CD=' + (app.getVariable('SS_STORE_CD') || ''));
            parts.push('SS_USER_NO=' + (app.getVariable('SS_USER_NO') || ''));
            parts.push('GV_MENU_ID=0001,STBJ030_M0');
            parts.push('strPyunsuId=0');
            parts.push('strOrdInputFlag=04');
            var body = parts.join(RS);

            fetch('/stbjz00/saveOrd', {
                method: 'POST',
                headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                body: body,
                signal: AbortSignal.timeout(10000)
            }).then(function(resp) {
                return resp.text().then(function(text) {
                    resolve(JSON.stringify({
                        status: resp.status,
                        statusText: resp.statusText,
                        bodyLength: text.length,
                        bodyPreview: text.substring(0, 1000)
                            .replace(/[\\x1e]/g, '\\n[RS]')
                            .replace(/[\\x1f]/g, ' | '),
                        headers: {
                            contentType: resp.headers.get('Content-Type') || '',
                            server: resp.headers.get('Server') || ''
                        }
                    }));
                });
            }).catch(function(e) {
                resolve(JSON.stringify({error: e.message}));
            });
        });
        """)
        if http_test:
            ht = json.loads(http_test)
            if ht.get('error'):
                logger.info(f"  에러: {ht['error']}")
            else:
                logger.info(f"  HTTP status: {ht.get('status')} {ht.get('statusText')}")
                logger.info(f"  응답 길이: {ht.get('bodyLength')}")
                logger.info(f"  Content-Type: {ht.get('headers', {}).get('contentType')}")
                logger.info(f"  응답 body:\n{ht.get('bodyPreview', '')}")

        # ===== Step 4: selOrdYn 엔드포인트 테스트 =====
        logger.info("\n=== Step 4: 발주 관련 엔드포인트 테스트 ===")
        ep_test = driver.execute_script("""
        var RS = String.fromCharCode(0x1e);
        var app = nexacro.getApplication();
        var parts = ['SSV:utf-8'];
        var svNames = ['GV_USERFLAG', 'SS_STORE_CD', 'SS_USER_NO', 'SS_SLC_CD'];
        for (var i = 0; i < svNames.length; i++) {
            try { parts.push(svNames[i] + '=' + (app.getVariable(svNames[i]) || '')); } catch(e) {}
        }
        parts.push('GV_MENU_ID=0001,STBJ030_M0');
        var body = parts.join(RS);

        var endpoints = [
            '/stbj030/selOrdYn',
            '/stbj030/selOrdInfo',
            '/stbj030/selInit'
        ];

        var results = [];
        var done = 0;
        return new Promise(function(resolve) {
            for (var ei = 0; ei < endpoints.length; ei++) {
                (function(ep) {
                    fetch(ep, {
                        method: 'POST',
                        headers: {'Content-Type': 'text/plain;charset=UTF-8'},
                        body: body,
                        signal: AbortSignal.timeout(5000)
                    }).then(function(resp) {
                        return resp.text().then(function(text) {
                            results.push({
                                endpoint: ep,
                                status: resp.status,
                                bodyLength: text.length,
                                body: text.substring(0, 500)
                                    .replace(/[\\x1e]/g, '\\n[RS]')
                                    .replace(/[\\x1f]/g, ' | ')
                            });
                            done++;
                            if (done === endpoints.length) resolve(JSON.stringify(results));
                        });
                    }).catch(function(e) {
                        results.push({endpoint: ep, error: e.message});
                        done++;
                        if (done === endpoints.length) resolve(JSON.stringify(results));
                    });
                })(endpoints[ei]);
            }
        });
        """)
        if ep_test:
            eps = json.loads(ep_test)
            for ep in eps:
                logger.info(f"\n  {ep.get('endpoint')}:")
                if ep.get('error'):
                    logger.info(f"    에러: {ep['error']}")
                else:
                    logger.info(f"    status: {ep.get('status')}")
                    logger.info(f"    body:\n{ep.get('body', '')}")

        # ===== Step 5: Direct API 저장 테스트 (옵션) =====
        if save_test:
            logger.info("\n=== Step 5: Direct API 저장 1건 테스트 ===")
            from src.order.direct_api_saver import DirectApiOrderSaver
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
            item_cd = "8801045571416"

            saver = DirectApiOrderSaver(driver)
            orders = [{
                'item_cd': item_cd,
                'final_order_qty': 1,
                'order_unit_qty': 1,
                'multiplier': 1,
            }]

            result = saver.save_orders(orders, tomorrow)
            logger.info(f"  success: {result.success}")
            logger.info(f"  saved_count: {result.saved_count}")
            logger.info(f"  method: {result.method}")
            logger.info(f"  message: {result.message}")
            logger.info(f"  response_preview: {result.response_preview}")
        else:
            logger.info("\n=== Step 5: 저장 테스트 생략 (--save-test 옵션으로 활성화) ===")

    except Exception as e:
        logger.error(f"오류: {e}", exc_info=True)
    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass

    logger.info("진단 완료")


if __name__ == "__main__":
    main()
