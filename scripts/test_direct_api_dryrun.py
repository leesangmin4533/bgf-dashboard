"""
Direct API 발주 전략 1 (gfn_transaction) dry-run 테스트

사용법:
    python scripts/test_direct_api_dryrun.py                    # dry-run (실제 저장 안함)
    python scripts/test_direct_api_dryrun.py --live              # 실제 저장 (1개 상품)
    python scripts/test_direct_api_dryrun.py --item 8801045571416  # 특정 상품

테스트 항목:
    1. 넥사크로 폼 탐색 (STBJ030_M0)
    2. dataset 바인딩 객체 확인
    3. gfn_transaction 함수 존재 확인
    4. dataset 채우기 (setColumn)
    5. dry-run: body 생성만 / live: 실제 gfn_transaction 호출
"""

import json
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger

logger = get_logger("direct_api_dryrun")


def driver_exec_json(driver, js, *args) -> dict:
    """execute_script + JSON.stringify 결과 파싱 헬퍼"""
    try:
        r = driver.execute_script(js, *args)
        return json.loads(r) if r else {'error': 'null_return'}
    except Exception as e:
        return {'error': f'py_error: {e}'}


def run_diagnostic(driver) -> dict:
    """넥사크로 환경 진단 — 폼, dataset, gfn_transaction 존재 확인"""
    # Step-by-step 확인 (한번에 하면 None 반환 이슈 방지)
    diag = {}

    # 1단계: nexacro 존재 여부
    try:
        r = driver.execute_script("return typeof nexacro;")
        diag['nexacro_typeof'] = r
    except Exception as e:
        diag['nexacro_typeof'] = f'error: {e}'

    # 2단계: _FIND_ORDER_FORM_JS 동일 패턴으로 폼 탐색
    # 주의: nexacro 객체 직접 반환 시 circular reference 발생 → 순수값만 반환
    try:
        r = driver.execute_script("""
            var result = {ready: false, stbjForm: false, reason: ''};
            try {
                var app = nexacro.getApplication();
                if (!app) { result.reason = 'no_app'; return JSON.stringify(result); }

                var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                if (!frameSet) { result.reason = 'no_frameSet'; return JSON.stringify(result); }

                var stbjForm = null;
                try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}

                if (!stbjForm || !stbjForm.div_workForm) {
                    var keys = Object.keys(frameSet);
                    for (var ki = 0; ki < keys.length; ki++) {
                        try {
                            var f = frameSet[keys[ki]];
                            if (f && f.form && f.form.div_workForm &&
                                f.form.div_workForm.form.div_work_01 &&
                                f.form.div_workForm.form.div_work_01.form.gdList) {
                                stbjForm = f.form;
                                break;
                            }
                        } catch(e) {}
                    }
                }

                if (!stbjForm) { result.reason = 'no_stbjForm'; return JSON.stringify(result); }
                result.stbjForm = String(stbjForm.name || 'found');

                var workForm = null;
                try { workForm = stbjForm.div_workForm.form.div_work_01.form; } catch(e) {}
                if (!workForm || !workForm.gdList) {
                    result.reason = 'no_gdList';
                    return JSON.stringify(result);
                }

                // _binddataset = Dataset 객체 자체 (문자열 아님)
                var ds = workForm.gdList._binddataset;
                if (!ds) { try { ds = workForm.gdList._binddataset_obj; } catch(e) {} }
                var dsName = 'unknown';
                try { dsName = String(ds._id || ds.name || ds._name || 'dataset'); } catch(e) {}
                result.dsName = dsName;

                var colCount = 0;
                var cols = [];
                try {
                    if (ds) {
                        colCount = ds.getColCount();
                        for (var i = 0; i < Math.min(colCount, 15); i++) {
                            cols.push(String(ds.getColID(i)));
                        }
                        result.rowCount = ds.getRowCount();
                    }
                } catch(e) {}
                result.colCount = colCount;
                result.cols_sample = cols;

                // gfn_transaction 탐색
                var gfnScope = 'none';
                try {
                    if (typeof stbjForm.gfn_transaction === 'function') gfnScope = 'form';
                    else if (typeof gfn_transaction === 'function') gfnScope = 'global';
                } catch(e) {}
                result.gfnScope = gfnScope;
                result.ready = true;

            } catch(e) {
                result.reason = 'exception: ' + String(e.message || e);
            }
            return JSON.stringify(result);
        """)
        diag['form_check'] = json.loads(r) if r else {'ready': False, 'reason': 'null_return'}
    except Exception as e:
        diag['form_check'] = {'ready': False, 'reason': f'py_error: {e}'}

    return diag


def run_populate_test(driver, item_cd: str, date_str: str) -> dict:
    """dataset 채우기 테스트 (저장하지 않음) — JSON.stringify로 반환"""
    try:
        result_str = driver.execute_script("""
        var itemCd = arguments[0];
        var dateStr = arguments[1];
        var r = {};
        try {
            var app = nexacro.getApplication();
            var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
            var stbjForm = null;
            try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}

            if (!stbjForm || !stbjForm.div_workForm) {
                var keys = Object.keys(frameSet);
                for (var ki = 0; ki < keys.length; ki++) {
                    try {
                        var f = frameSet[keys[ki]];
                        if (f && f.form && f.form.div_workForm &&
                            f.form.div_workForm.form.div_work_01 &&
                            f.form.div_workForm.form.div_work_01.form.gdList) {
                            stbjForm = f.form;
                            break;
                        }
                    } catch(e) {}
                }
            }
            if (!stbjForm) return JSON.stringify({error: 'form_not_found'});

            var workForm = stbjForm.div_workForm.form.div_work_01.form;
            if (!workForm || !workForm.gdList) return JSON.stringify({error: 'grid_not_found'});

            var ds = workForm.gdList._binddataset;
            if (!ds) { try { ds = workForm.gdList._binddataset_obj; } catch(e) {} }
            if (!ds) return JSON.stringify({error: 'dataset_not_found'});
            var dsName = 'unknown';
            try { dsName = String(ds._id || ds.name || ds._name || 'dataset'); } catch(e) {}

            var beforeRows = ds.getRowCount();

            // 새 행 추가 + 값 설정
            var row = ds.addRow();
            if (row < 0) return JSON.stringify({error: 'addRow_failed'});

            ds.setColumn(row, 'ITEM_CD', itemCd);
            ds.setColumn(row, 'ORD_MUL_QTY', 1);
            ds.setColumn(row, 'ORD_YMD', dateStr);

            // 검증: 값 읽기 (String으로 변환)
            var vItemCd = String(ds.getColumn(row, 'ITEM_CD') || '');
            var vQty = String(ds.getColumn(row, 'ORD_MUL_QTY') || '');
            var vYmd = String(ds.getColumn(row, 'ORD_YMD') || '');

            var afterRows = ds.getRowCount();

            // 원복
            ds.deleteRow(row);
            var restoredRows = ds.getRowCount();

            // 컬럼 존재 확인
            var hasItemCd = ds.getColIndex('ITEM_CD') >= 0;
            var hasOrdMulQty = ds.getColIndex('ORD_MUL_QTY') >= 0;
            var hasOrdYmd = ds.getColIndex('ORD_YMD') >= 0;
            var hasStoreCd = ds.getColIndex('STORE_CD') >= 0;
            var hasOrdUnitQty = ds.getColIndex('ORD_UNIT_QTY') >= 0;

            r = {
                success: true,
                dataset_name: dsName,
                before_rows: beforeRows,
                after_add_rows: afterRows,
                restored_rows: restoredRows,
                verify_item_cd: vItemCd,
                verify_qty: vQty,
                verify_ymd: vYmd,
                has_ITEM_CD: hasItemCd,
                has_ORD_MUL_QTY: hasOrdMulQty,
                has_ORD_YMD: hasOrdYmd,
                has_STORE_CD: hasStoreCd,
                has_ORD_UNIT_QTY: hasOrdUnitQty,
            };
        } catch(e) {
            r = {error: String(e.message || e)};
        }
        return JSON.stringify(r);
        """, item_cd, date_str)
        return json.loads(result_str) if result_str else {'error': 'null_return'}
    except Exception as e:
        return {'error': f'py_error: {e}'}


def run_live_save(driver, item_cd: str, date_str: str, timeout_ms: int = 15000) -> dict:
    """실제 gfn_transaction 호출로 저장 (1개 상품)"""
    from src.order.direct_api_saver import DirectApiOrderSaver

    saver = DirectApiOrderSaver(driver, timeout_ms=timeout_ms)

    # 캡처 파일에서 템플릿 로드
    import os
    capture_files = [
        'captures/save_api_capture_valid.json',
        'captures/save_api_capture.json',
        'bgf_auto/captures/save_api_capture_valid.json',
        'bgf_auto/captures/save_api_capture.json',
    ]
    for fpath in capture_files:
        if os.path.exists(fpath):
            if saver.set_template_from_file(fpath):
                logger.info(f"  템플릿 로드: {fpath}")
                break

    # 인터셉터 설치 (템플릿 없을 때)
    if not saver.has_template:
        saver.install_interceptor()
        saver.capture_save_template()

    orders = [{
        'item_cd': item_cd,
        'multiplier': 1,
        'order_unit_qty': 1,
    }]

    logger.info(f"  save_orders() 호출: item={item_cd}, date={date_str}")
    result = saver.save_orders(orders, date_str)

    return {
        'success': result.success,
        'saved_count': result.saved_count,
        'method': result.method,
        'message': result.message,
        'elapsed_ms': result.elapsed_ms,
        'response_preview': result.response_preview[:500] if result.response_preview else '',
    }


def main():
    parser = argparse.ArgumentParser(description="Direct API 전략 1 dry-run 테스트")
    parser.add_argument("--live", action="store_true", help="실제 저장 실행 (기본: dry-run)")
    parser.add_argument("--item", default=None, help="테스트 상품코드")
    parser.add_argument("--store-id", default=None, help="매장 ID")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Direct API 전략 1 dry-run 테스트")
    logger.info(f"  모드: {'LIVE (실제 저장)' if args.live else 'DRY-RUN (진단만)'}")
    logger.info("=" * 60)

    # 내일 날짜 (발주일)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')

    # 1. BGF 로그인
    from src.sales_analyzer import SalesAnalyzer
    sa_kwargs = {}
    if args.store_id:
        sa_kwargs["store_id"] = args.store_id
    analyzer = SalesAnalyzer(**sa_kwargs)

    try:
        logger.info("\n[Step 1] 크롬 드라이버 + 로그인")
        analyzer.setup_driver()
        analyzer.connect()
        analyzer.do_login()
        logger.info("  로그인 성공")

        # 2. 단품별 발주 메뉴 이동
        logger.info("\n[Step 2] 단품별 발주 메뉴 이동")
        from src.order.order_executor import OrderExecutor
        executor = OrderExecutor(analyzer.driver)

        if not executor.navigate_to_single_order():
            logger.error("  ❌ 메뉴 이동 실패")
            return
        logger.info("  ✅ 메뉴 이동 성공")
        time.sleep(3)

        # 3. 넥사크로 환경 진단
        logger.info("\n[Step 3] 넥사크로 환경 진단")
        diag = run_diagnostic(analyzer.driver)
        logger.info(f"  nexacro typeof: {diag.get('nexacro_typeof')}")

        form_check = diag.get('form_check', {})
        if isinstance(form_check, str):
            logger.error(f"  ❌ 폼 탐색 에러: {form_check}")
            return

        logger.info(f"  form_check: {json.dumps(form_check, ensure_ascii=False, default=str)}")

        if not form_check or not form_check.get('ready'):
            logger.error(f"  ❌ 폼 준비 안됨: {form_check.get('reason', 'unknown')}")
            logger.info("  stbjForm 존재: " + str(form_check.get('stbjForm', False)))
            return

        logger.info(f"  ✅ stbjForm: {form_check.get('stbjForm')}")
        logger.info(f"  ✅ dataset: {form_check.get('dsName')} ({form_check.get('colCount')}컬럼, {form_check.get('rowCount')}행)")
        logger.info(f"  ✅ 컬럼 샘플: {form_check.get('cols_sample', [])}")
        logger.info(f"  gfn_transaction scope: {form_check.get('gfnScope')}")

        if form_check.get('gfnScope') == 'none':
            logger.warning("  ⚠️ gfn_transaction 없음 — 전략 2 (fetch) 폴백 필요")

        # 4. 상품코드 결정
        item_cd = args.item
        if not item_cd:
            # DB에서 발주 가능 상품 조회
            logger.info("\n[Step 4] DB에서 발주 가능 상품 조회")
            try:
                import sqlite3
                from src.settings.store_context import get_store_context
                ctx = get_store_context()
                common_db = ctx.common_db_path
                conn = sqlite3.connect(common_db)
                row = conn.execute("""
                    SELECT p.item_cd, p.item_nm
                    FROM products p
                    JOIN product_details pd ON p.item_cd = pd.item_cd
                    WHERE pd.orderable_status = '가능'
                    ORDER BY RANDOM()
                    LIMIT 1
                """).fetchone()
                conn.close()
                if row:
                    item_cd = row[0]
                    logger.info(f"  선택: {item_cd} ({row[1]})")
                else:
                    item_cd = "8801045571416"
                    logger.info(f"  DB 조회 실패, 기본값: {item_cd}")
            except Exception as e:
                item_cd = "8801045571416"
                logger.info(f"  DB 에러 ({e}), 기본값: {item_cd}")

        # 5. dataset 채우기 테스트 (항상 실행)
        logger.info(f"\n[Step 5] dataset 채우기 테스트 (item={item_cd}, date={tomorrow})")
        populate = run_populate_test(analyzer.driver, item_cd, tomorrow)

        if populate.get('error'):
            logger.error(f"  ❌ 채우기 실패: {populate['error']}")
            return

        logger.info(f"  ✅ addRow + setColumn 성공")
        logger.info(f"    verify ITEM_CD: {populate.get('verify_item_cd')}")
        logger.info(f"    verify ORD_MUL_QTY: {populate.get('verify_qty')}")
        logger.info(f"    verify ORD_YMD: {populate.get('verify_ymd')}")
        logger.info(f"    rows: {populate.get('before_rows')} → {populate.get('after_add_rows')} → {populate.get('restored_rows')} (원복)")
        logger.info(f"    key columns: ITEM_CD={populate.get('has_ITEM_CD')}, ORD_MUL_QTY={populate.get('has_ORD_MUL_QTY')}, ORD_YMD={populate.get('has_ORD_YMD')}, STORE_CD={populate.get('has_STORE_CD')}, ORD_UNIT_QTY={populate.get('has_ORD_UNIT_QTY')}")

        # 5.5. gfn_transaction URL resolution 진단
        logger.info("\n[Step 5.5] gfn_transaction URL resolution 진단")
        try:
            url_diag = driver_exec_json(analyzer.driver, """
            var r = {};
            try {
                var app = nexacro.getApplication();
                var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                var stbjForm = null;
                try { stbjForm = frameSet.STBJ030_M0 ? frameSet.STBJ030_M0.form : null; } catch(e) {}
                if (!stbjForm || !stbjForm.div_workForm) {
                    for (var ki = 0; ki < Object.keys(frameSet).length; ki++) {
                        try {
                            var f = frameSet[Object.keys(frameSet)[ki]];
                            if (f && f.form && f.form.div_workForm &&
                                f.form.div_workForm.form.div_work_01 &&
                                f.form.div_workForm.form.div_work_01.form.gdList) {
                                stbjForm = f.form;
                                break;
                            }
                        } catch(e) {}
                    }
                }
                if (!stbjForm) { r.error = 'no_form'; return JSON.stringify(r); }

                // gfn_transaction 관련 정보
                r.formName = String(stbjForm.name || '');
                r.gfn_type = typeof stbjForm.gfn_transaction;

                // 넥사크로 서비스 URL 설정
                try { r.serviceUrl = String(stbjForm._serviceurl || stbjForm.serviceurl || ''); } catch(e) { r.serviceUrl = 'err: ' + e.message; }
                try {
                    var appSvcUrl = app.services || {};
                    var svcKeys = [];
                    for (var k in appSvcUrl) { svcKeys.push(k + '=' + String(appSvcUrl[k] || '')); }
                    r.appServices = svcKeys.slice(0, 5);
                } catch(e) { r.appServices = ['err: ' + e.message]; }

                // grf_appSvcUrl() 직접 호출
                try {
                    if (typeof stbjForm.grf_appSvcUrl === 'function') {
                        r.grfAppSvcUrl = String(stbjForm.grf_appSvcUrl() || '');
                    } else {
                        r.grfAppSvcUrl = 'function not found';
                    }
                } catch(e) { r.grfAppSvcUrl = 'err: ' + e.message; }

                // nexacro 서비스 등록 정보
                try {
                    var svcList = [];
                    if (app._services) {
                        for (var sk in app._services) {
                            svcList.push(sk + '=' + String(app._services[sk]._url || app._services[sk] || ''));
                        }
                    }
                    r.appRegisteredServices = svcList.slice(0, 10);
                } catch(e) { r.appRegisteredServices = ['err: ' + e.message]; }

                // 서비스 URL 해석 시도 (nexacro._getServiceLocation)
                try {
                    if (typeof nexacro._getServiceLocation === 'function') {
                        r.resolvedUrl = String(nexacro._getServiceLocation('saveOrd', stbjForm) || '');
                    } else {
                        r.resolvedUrl = 'nexacro._getServiceLocation not found';
                    }
                } catch(e) { r.resolvedUrl = 'err: ' + e.message; }

                // gfn_transaction 소스 코드 일부 확인
                try {
                    var src = String(stbjForm.gfn_transaction);
                    r.gfn_src_preview = src.substring(0, 300);
                } catch(e) { r.gfn_src_preview = 'err: ' + e.message; }

                // XHR 인터셉터로 실제 URL 캡처 준비
                try {
                    window._lastXhrUrl = null;
                    if (!window._xhrUrlCapInstalled) {
                        var origOpen = XMLHttpRequest.prototype.open;
                        XMLHttpRequest.prototype.open = function(method, url) {
                            window._lastXhrUrl = method + ' ' + url;
                            return origOpen.apply(this, arguments);
                        };
                        window._xhrUrlCapInstalled = true;
                    }
                    r.xhrCapture = 'installed';
                } catch(e) { r.xhrCapture = 'err: ' + e.message; }

            } catch(e) {
                r.error = String(e.message || e);
            }
            return JSON.stringify(r);
            """)
            for k, v in url_diag.items():
                logger.info(f"    {k}: {v}")
        except Exception as e:
            logger.error(f"  URL 진단 에러: {e}")

        # 5.7. selSearch 프리페치 진단 (프리페치 가능 여부 확인)
        logger.info("\n[Step 5.7] selSearch 프리페치 진단")
        try:
            from src.order.direct_api_saver import DirectApiOrderSaver
            test_saver = DirectApiOrderSaver(analyzer.driver)
            prefetch_result = test_saver._prefetch_item_details([item_cd], tomorrow)
            if prefetch_result and item_cd in prefetch_result:
                fields = prefetch_result[item_cd]
                logger.info(f"  ✅ 프리페치 성공: {len(fields)}개 필드")
                key_fields = ['STORE_CD', 'ITEM_NM', 'PITEM_ID', 'ORD_UNIT',
                              'ORD_UNIT_QTY', 'MID_NM', 'PROFIT_RATE', 'HQ_MAEGA_SET']
                for kf in key_fields:
                    logger.info(f"    {kf}: {fields.get(kf, '(없음)')}")
            else:
                logger.warning(f"  ⚠️ 프리페치 실패 (template 없음?): {prefetch_result}")
                logger.info("  → Phase 1 prefetch가 먼저 실행되어야 selSearch 템플릿이 캡처됩니다")
        except Exception as e:
            logger.error(f"  프리페치 진단 에러: {e}")

        # 6. 실제 저장 (--live 옵션)
        if args.live:
            logger.info(f"\n[Step 6] LIVE 저장 실행 (item={item_cd}, qty=1)")
            live_result = run_live_save(analyzer.driver, item_cd, tomorrow)
            logger.info(f"  success: {live_result.get('success')}")
            logger.info(f"  method: {live_result.get('method')}")
            logger.info(f"  saved_count: {live_result.get('saved_count')}")
            logger.info(f"  elapsed_ms: {live_result.get('elapsed_ms', 0):.0f}")
            logger.info(f"  message: {live_result.get('message')}")
            if live_result.get('response_preview'):
                logger.info(f"  response: {live_result['response_preview'][:300]}")

            # XHR URL 확인
            try:
                last_xhr = analyzer.driver.execute_script("return window._lastXhrUrl || 'none';")
                logger.info(f"  lastXhrUrl: {last_xhr}")
            except Exception:
                pass

            if live_result.get('success'):
                logger.info("  ✅ LIVE 저장 성공!")
            else:
                logger.warning("  ❌ LIVE 저장 실패 — 응답 확인 필요")
        else:
            logger.info("\n[Step 6] dry-run 완료 (--live 플래그로 실제 저장 가능)")

        # 7. 결과 요약
        logger.info("\n" + "=" * 60)
        logger.info("테스트 결과 요약")
        logger.info("=" * 60)
        logger.info(f"  폼 탐색:       {'✅' if form_check.get('ready') else '❌'} ({form_check.get('stbjForm')})")
        logger.info(f"  dataset:       {'✅' if form_check.get('dsName') else '❌'} ({form_check.get('dsName')}, {form_check.get('colCount')}컬럼)")
        logger.info(f"  gfn_transaction: {form_check.get('gfnScope')}")
        logger.info(f"  setColumn:     {'✅' if not populate.get('error') else '❌'}")
        if args.live:
            logger.info(f"  LIVE 저장:     {'✅' if live_result.get('success') else '❌'}")

    except KeyboardInterrupt:
        logger.info("사용자 중단")
    except Exception as e:
        logger.error(f"오류: {e}", exc_info=True)
    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass

    logger.info("테스트 완료")


if __name__ == "__main__":
    main()
