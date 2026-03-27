"""
Save API 캡처 스크립트 - 발주 저장 시 gfn_transaction + XHR 이중 캡처

사용법:
    python scripts/capture_save_api.py [--headless] [--output captures/save_api.json]

설명:
    1. BGF 사이트 로그인
    2. 단품별 발주 메뉴 이동
    3. gfn_transaction + XHR 인터셉터 설치
    4. 사용자가 상품 입력 + 저장 버튼 클릭
    5. 캡처된 API 정보를 JSON 파일로 저장

결과물:
    - endpoint URL
    - request body (SSV 형식)
    - response preview
    - gfn_transaction 파라미터 (txId, svcURL, inDS, outDS)
"""

import json
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger

logger = get_logger("capture_save_api")


# =====================================================================
# 이중 인터셉터 JavaScript
# =====================================================================
INTERCEPTOR_JS = """
(function() {
    // 이미 설치된 경우 건너뜀
    if (window._saveApiInterceptorInstalled) {
        console.log('[SaveCapture] 인터셉터 이미 설치됨');
        return {status: 'already_installed', captures: window._saveCaptures?.length || 0};
    }

    window._saveCaptures = [];
    window._saveXhrCaptures = [];

    // ── 1) gfn_transaction 오버라이드 ──
    try {
        if (typeof gfn_transaction === 'function') {
            window._origGfnTransaction = gfn_transaction;
            window.gfn_transaction = function(txId, svcURL, inDS, outDS, args, callback, isAsync) {
                var capture = {
                    type: 'gfn_transaction',
                    txId: txId || '',
                    svcURL: svcURL || '',
                    inDS: inDS || '',
                    outDS: outDS || '',
                    args: args || '',
                    timestamp: new Date().toISOString()
                };
                window._saveCaptures.push(capture);
                console.log('[SaveCapture] gfn_transaction:', txId, svcURL);
                return window._origGfnTransaction.apply(this, arguments);
            };
            console.log('[SaveCapture] gfn_transaction 오버라이드 성공');
        } else {
            console.warn('[SaveCapture] gfn_transaction 함수를 찾을 수 없음');
        }
    } catch(e) {
        console.error('[SaveCapture] gfn_transaction 오버라이드 실패:', e);
    }

    // ── 2) XMLHttpRequest body + response 캡처 ──
    try {
        var origOpen = XMLHttpRequest.prototype.open;
        var origSend = XMLHttpRequest.prototype.send;

        XMLHttpRequest.prototype.open = function(method, url) {
            this._captureUrl = url;
            this._captureMethod = method;
            return origOpen.apply(this, arguments);
        };

        XMLHttpRequest.prototype.send = function(body) {
            var self = this;
            var captureUrl = this._captureUrl || '';

            // 모든 POST 요청 캡처 (selSearch 외 저장 요청도 포함)
            if (this._captureMethod === 'POST' && body) {
                var capture = {
                    type: 'xhr',
                    url: captureUrl,
                    method: 'POST',
                    body: typeof body === 'string' ? body.substring(0, 5000) : String(body).substring(0, 5000),
                    bodyLength: typeof body === 'string' ? body.length : 0,
                    timestamp: new Date().toISOString(),
                    status: null,
                    responsePreview: null
                };

                // 응답도 캡처
                this.addEventListener('load', function() {
                    try {
                        capture.status = self.status;
                        capture.responsePreview = (self.responseText || '').substring(0, 2000);
                        capture.responseLength = (self.responseText || '').length;
                    } catch(e) {
                        capture.responseError = e.message;
                    }
                });

                window._saveXhrCaptures.push(capture);
                console.log('[SaveCapture] XHR POST:', captureUrl, 'body:', (body || '').substring(0, 100));
            }

            return origSend.apply(this, arguments);
        };

        console.log('[SaveCapture] XHR 인터셉터 설치 성공');
    } catch(e) {
        console.error('[SaveCapture] XHR 인터셉터 실패:', e);
    }

    // ── 3) fetch() 오버라이드 ──
    try {
        var origFetch = window.fetch;
        window.fetch = function(url, opts) {
            if (opts && opts.method === 'POST' && opts.body) {
                var capture = {
                    type: 'fetch',
                    url: typeof url === 'string' ? url : url.url || '',
                    method: 'POST',
                    body: typeof opts.body === 'string' ? opts.body.substring(0, 5000) : '',
                    bodyLength: typeof opts.body === 'string' ? opts.body.length : 0,
                    timestamp: new Date().toISOString()
                };
                window._saveXhrCaptures.push(capture);
                console.log('[SaveCapture] fetch POST:', capture.url);
            }
            return origFetch.apply(this, arguments);
        };
        console.log('[SaveCapture] fetch 인터셉터 설치 성공');
    } catch(e) {
        console.error('[SaveCapture] fetch 인터셉터 실패:', e);
    }

    window._saveApiInterceptorInstalled = true;
    return {status: 'installed', gfn: typeof window._origGfnTransaction === 'function'};
})();
"""


def get_captures(driver) -> dict:
    """브라우저에서 캡처된 데이터 수집"""
    try:
        result = driver.execute_script("""
            return {
                gfn_captures: window._saveCaptures || [],
                xhr_captures: window._saveXhrCaptures || [],
                interceptor_installed: !!window._saveApiInterceptorInstalled
            };
        """)
        return result or {"gfn_captures": [], "xhr_captures": [], "interceptor_installed": False}
    except Exception as e:
        logger.error(f"캡처 데이터 수집 실패: {e}")
        return {"gfn_captures": [], "xhr_captures": [], "interceptor_installed": False}


def clear_captures(driver) -> None:
    """캡처 데이터 초기화"""
    try:
        driver.execute_script("""
            window._saveCaptures = [];
            window._saveXhrCaptures = [];
        """)
    except Exception as e:
        logger.warning(f"캡처 초기화 실패: {e}")


def filter_save_captures(captures: dict) -> dict:
    """저장 관련 캡처만 필터링 (selSearch 제외)"""
    gfn = captures.get("gfn_captures", [])
    xhr = captures.get("xhr_captures", [])

    # gfn_transaction: 'sel'로 시작하지 않는 것 = 저장 요청 가능성
    save_gfn = [c for c in gfn if not c.get("svcURL", "").startswith("sel")]
    # XHR: selSearch 외 요청
    save_xhr = [c for c in xhr if "selSearch" not in c.get("url", "")]

    return {
        "save_gfn_transactions": save_gfn,
        "save_xhr_requests": save_xhr,
        "all_gfn_transactions": gfn,
        "all_xhr_requests": xhr,
    }


def save_captures_to_file(captures: dict, output_path: str) -> None:
    """캡처 결과를 JSON 파일로 저장"""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "captured_at": datetime.now().isoformat(),
        "summary": {
            "total_gfn": len(captures.get("all_gfn_transactions", [])),
            "total_xhr": len(captures.get("all_xhr_requests", [])),
            "save_gfn": len(captures.get("save_gfn_transactions", [])),
            "save_xhr": len(captures.get("save_xhr_requests", [])),
        },
        **captures,
    }

    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"캡처 결과 저장: {output}")
    logger.info(f"  gfn_transaction: {data['summary']['save_gfn']}건 (전체 {data['summary']['total_gfn']}건)")
    logger.info(f"  XHR 요청: {data['summary']['save_xhr']}건 (전체 {data['summary']['total_xhr']}건)")


def main():
    parser = argparse.ArgumentParser(description="BGF 발주 저장 API 캡처")
    parser.add_argument("--output", "-o", default="captures/save_api_capture.json", help="출력 파일 경로")
    parser.add_argument("--auto", action="store_true", help="자동 모드 (상품 1개 입력 후 저장)")
    parser.add_argument("--item-cd", default="8801043036016", help="자동 모드 테스트 상품코드")
    parser.add_argument("--store-id", default=None, help="매장 ID (기본: DEFAULT_STORE_ID)")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("BGF 발주 저장 API 캡처 시작")
    logger.info("=" * 60)

    # 1. SalesAnalyzer 재사용 (로그인)
    from src.sales_analyzer import SalesAnalyzer
    sa_kwargs = {}
    if args.store_id:
        sa_kwargs["store_id"] = args.store_id
    analyzer = SalesAnalyzer(**sa_kwargs)

    try:
        logger.info("크롬 드라이버 초기화...")
        analyzer.setup_driver()
        logger.info("BGF 사이트 접속...")
        analyzer.connect()
        logger.info("로그인 중...")
        analyzer.do_login()
        logger.info("로그인 성공")

        # 2. 단품별 발주 메뉴 이동
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor(analyzer.driver)

        logger.info("단품별 발주 메뉴로 이동...")
        if not executor.navigate_to_single_order():
            logger.error("단품별 발주 메뉴 이동 실패")
            return

        time.sleep(2)

        # 3. 인터셉터 설치
        logger.info("이중 인터셉터 설치 중...")
        install_result = analyzer.driver.execute_script(INTERCEPTOR_JS)
        logger.info(f"인터셉터 설치 결과: {install_result}")

        # 캡처 초기화
        clear_captures(analyzer.driver)

        if args.auto:
            # 자동 모드: 상품 1개 입력 -> 저장
            logger.info(f"자동 모드: 상품 {args.item_cd} 입력 -> 저장")

            # 상품 입력
            input_result = executor.input_product(args.item_cd, 1)
            logger.info(f"상품 입력 결과: {input_result}")

            time.sleep(1)

            # 저장 전 캡처 확인 (입력 시 발생하는 selSearch 등)
            pre_save = get_captures(analyzer.driver)
            logger.info(f"저장 전 캡처: gfn={len(pre_save['gfn_captures'])}건, xhr={len(pre_save['xhr_captures'])}건")

            # 캡처 초기화 (저장 요청만 캡처하기 위해)
            clear_captures(analyzer.driver)

            # 저장 실행
            logger.info("저장 버튼 클릭...")
            save_result = executor.confirm_order()
            logger.info(f"저장 결과: {save_result}")

            time.sleep(2)

            # 저장 후 캡처
            post_save = get_captures(analyzer.driver)
            filtered = filter_save_captures(post_save)

            logger.info(f"저장 후 캡처: gfn={len(filtered['save_gfn_transactions'])}건, xhr={len(filtered['save_xhr_requests'])}건")

            # 결과 저장
            save_captures_to_file({
                **filtered,
                "pre_save_captures": {
                    "gfn": pre_save["gfn_captures"],
                    "xhr": pre_save["xhr_captures"],
                },
            }, args.output)

        else:
            # 수동 모드: 사용자가 직접 상품 입력 + 저장
            logger.info("=" * 60)
            logger.info("수동 캡처 모드")
            logger.info("  1. 브라우저에서 상품을 입력하세요")
            logger.info("  2. 저장 버튼을 클릭하세요")
            logger.info("  3. 완료 후 Enter를 눌러 캡처 결과를 저장합니다")
            logger.info("=" * 60)

            input("상품 입력 + 저장 완료 후 Enter 키를 누르세요...")

            # 캡처 수집
            captures = get_captures(analyzer.driver)
            filtered = filter_save_captures(captures)

            logger.info(f"캡처 결과:")
            logger.info(f"  gfn_transaction (저장): {len(filtered['save_gfn_transactions'])}건")
            logger.info(f"  XHR (저장): {len(filtered['save_xhr_requests'])}건")

            # 상세 출력
            for i, cap in enumerate(filtered["save_gfn_transactions"]):
                logger.info(f"  [gfn #{i+1}] txId={cap.get('txId')}, svc={cap.get('svcURL')}")
                logger.info(f"    inDS={cap.get('inDS', '')[:200]}")
                logger.info(f"    outDS={cap.get('outDS', '')[:200]}")

            for i, cap in enumerate(filtered["save_xhr_requests"]):
                logger.info(f"  [xhr #{i+1}] {cap.get('method')} {cap.get('url')}")
                logger.info(f"    body({cap.get('bodyLength', 0)}): {cap.get('body', '')[:200]}")
                if cap.get("responsePreview"):
                    logger.info(f"    response({cap.get('responseLength', 0)}): {cap.get('responsePreview', '')[:200]}")

            save_captures_to_file(filtered, args.output)

    except KeyboardInterrupt:
        logger.info("사용자 중단")
    except Exception as e:
        logger.error(f"오류 발생: {e}", exc_info=True)
    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass

    logger.info("캡처 스크립트 완료")


if __name__ == "__main__":
    main()
