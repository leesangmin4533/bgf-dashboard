"""
Direct API 발주 라이브 테스트 스크립트

실제 BGF 사이트에서 Direct API 발주 저장이 작동하는지 검증합니다.

안전장치:
    - 기본 모드는 --dry-run (실제 저장 안 함)
    - --live 플래그를 명시적으로 지정해야만 실제 저장 실행
    - 상품 수 제한: 기본 1개, 최대 5개
    - 각 단계별 상세 로그 출력
    - 발주 가능 시간 사전 체크
    - 실패 시 자동 Selenium 폴백 없음 (Direct API만 테스트)

사용법:
    # 1단계: 발주 가능 여부만 확인
    python scripts/test_direct_api_live.py --check-only

    # 2단계: 프리페치만 테스트 (저장 안 함)
    python scripts/test_direct_api_live.py --dry-run

    # 3단계: 실제 1개 상품 라이브 저장
    python scripts/test_direct_api_live.py --live --items 1

    # 특정 상품으로 테스트
    python scripts/test_direct_api_live.py --live --item-cd 8801043036016 --qty 1
"""

import json
import sys
import io
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows CP949 콘솔 → UTF-8 래핑
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger

logger = get_logger("test_direct_api_live")


# =====================================================================
# 상수
# =====================================================================
MAX_LIVE_ITEMS = 5  # 라이브 테스트 최대 상품 수 (안전장치)


# =====================================================================
# 단계별 테스트 함수
# =====================================================================

def step_login(store_id: str = None):
    """[Step 1] BGF 사이트 로그인"""
    print("\n" + "=" * 60)
    print("[Step 1] BGF 사이트 로그인")
    print("=" * 60)

    from src.sales_analyzer import SalesAnalyzer
    sa_kwargs = {}
    if store_id:
        sa_kwargs["store_id"] = store_id
    analyzer = SalesAnalyzer(**sa_kwargs)
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        raise RuntimeError("로그인 실패")

    print("[OK] 로그인 성공")
    time.sleep(2)
    return analyzer


def step_navigate(driver):
    """[Step 2] 단품별 발주 메뉴 이동"""
    print("\n" + "=" * 60)
    print("[Step 2] 단품별 발주 메뉴로 이동")
    print("=" * 60)

    from src.order.order_executor import OrderExecutor
    executor = OrderExecutor(driver)

    if not executor.navigate_to_single_order():
        raise RuntimeError("단품별 발주 메뉴 이동 실패")

    print("[OK] 단품별 발주 화면 진입")
    time.sleep(2)
    return executor


def step_check_availability(driver) -> Dict[str, Any]:
    """[Step 3] 발주 가능 여부 확인"""
    print("\n" + "=" * 60)
    print("[Step 3] 발주 가능 여부 확인")
    print("=" * 60)

    from src.order.direct_api_saver import DirectApiOrderSaver
    saver = DirectApiOrderSaver(driver)
    result = saver.check_order_availability()

    print(f"  발주 가능: {result.get('available', '알 수 없음')}")
    print(f"  ordYn: {result.get('ordYn', '')}")
    print(f"  ordClose: {result.get('ordClose', '')}")
    print(f"  매장코드: {result.get('storeCd', '')}")
    print(f"  브라우저 시간: {result.get('browserTime', '')}")

    # 폼 변수 상세
    work_vars = result.get('workFormVars', {})
    if work_vars:
        ord_vars = {k: v for k, v in work_vars.items() if 'ord' in k.lower() or 'fv_' in k.lower()}
        if ord_vars:
            print(f"  발주 관련 변수:")
            for k, v in sorted(ord_vars.items()):
                print(f"    {k}: {v}")

    if result.get('error'):
        print(f"  [WARN] 에러: {result['error']}")

    if not result.get('available', True):
        print("\n  [!!] 발주 불가 상태입니다.")
        print("  → 발주 가능 시간에 다시 시도하세요.")

    return result


def step_find_form(driver) -> Dict[str, Any]:
    """[Step 4] 넥사크로 폼/그리드 탐색"""
    print("\n" + "=" * 60)
    print("[Step 4] 넥사크로 폼/그리드 탐색")
    print("=" * 60)

    from src.order.direct_api_saver import _FIND_ORDER_FORM_JS
    result = driver.execute_script(_FIND_ORDER_FORM_JS)

    if not result:
        print("  [FAIL] 발주 폼을 찾을 수 없음")
        raise RuntimeError("발주 폼 탐색 실패")

    print(f"  폼 ID: {result.get('formId', 'N/A')}")
    print(f"  그리드: {result.get('hasGrid', False)}")
    print(f"  데이터셋: {result.get('dsName', 'N/A')}")
    print("[OK] 폼 구조 확인 완료")
    return result


def step_get_test_items(
    driver,
    executor,
    item_cd: str = None,
    qty: int = 1,
    num_items: int = 1,
    store_id: str = None,
) -> List[Dict[str, Any]]:
    """[Step 5] 테스트 상품 선택"""
    print("\n" + "=" * 60)
    print("[Step 5] 테스트 상품 선택")
    print("=" * 60)

    items = []

    if item_cd:
        # 특정 상품 지정
        items = [{
            'item_cd': item_cd,
            'final_order_qty': qty,
            'multiplier': qty,
            'order_unit_qty': 1,
        }]
        print(f"  지정 상품: {item_cd}, 수량: {qty}")
    else:
        # DB에서 발주 추천 상품 가져오기
        print(f"  DB에서 발주 추천 상품 {num_items}개 조회...")
        try:
            from src.order.auto_order import AutoOrderSystem
            system = AutoOrderSystem(
                driver=driver,
                use_improved_predictor=True,
                store_id=store_id,
            )
            system.load_unavailable_from_db()
            system.load_cut_items_from_db()
            system.load_inventory_cache_from_db()

            recs = system.get_recommendations(min_order_qty=1, max_items=num_items * 3)
            system.close()

            if recs:
                # 발주량이 작은 것부터 선택 (안전)
                recs.sort(key=lambda x: x.get('final_order_qty', 0))
                items = recs[:num_items]
                print(f"  → {len(items)}개 상품 선택됨:")
                for i, item in enumerate(items):
                    print(
                        f"    [{i+1}] {item.get('item_cd')} "
                        f"{(item.get('item_nm') or '')[:20]} "
                        f"발주={item.get('final_order_qty', 0)}개"
                    )
            else:
                print("  [WARN] 발주 추천 상품 없음")
        except Exception as e:
            print(f"  [ERROR] 추천 목록 생성 실패: {e}")

    if not items:
        print("  [FAIL] 테스트 상품 없음. --item-cd 옵션으로 직접 지정하세요.")
        return []

    return items


def step_prefetch(driver, items: List[Dict], date_str: str) -> Dict[str, Dict]:
    """[Step 6] selSearch 프리페치 테스트"""
    print("\n" + "=" * 60)
    print("[Step 6] selSearch 프리페치 테스트")
    print("=" * 60)

    from src.order.direct_api_saver import DirectApiOrderSaver
    saver = DirectApiOrderSaver(driver)

    item_codes = [str(item.get('item_cd', '')) for item in items]
    print(f"  대상: {len(item_codes)}개 상품")
    print(f"  발주일: {date_str}")

    start = time.time()
    details = saver._prefetch_item_details(item_codes, date_str)
    elapsed = (time.time() - start) * 1000

    print(f"  결과: {len(details)}/{len(item_codes)}건 성공 ({elapsed:.0f}ms)")

    for item_cd, fields in details.items():
        item_nm = fields.get('ITEM_NM', 'N/A')
        store_cd = fields.get('STORE_CD', 'N/A')
        ord_unit = fields.get('ORD_UNIT_QTY', 'N/A')
        ord_aday = fields.get('ORD_ADAY', 'N/A')
        print(
            f"    {item_cd}: {item_nm[:20]} | "
            f"매장={store_cd} | 입수={ord_unit} | "
            f"발주가능요일={ord_aday} | 필드={len(fields)}개"
        )

    if not details:
        print("  [WARN] 프리페치 실패 — 템플릿이 없을 수 있음")
        print("  → Selenium 1건 검색으로 자동 캡처 시도...")

    return details


def step_populate_test(driver, items: List[Dict], date_str: str, prefetch_data: Dict) -> Dict:
    """[Step 7] dataset 채우기 테스트 (dry-run)"""
    print("\n" + "=" * 60)
    print("[Step 7] dataset 채우기 테스트")
    print("=" * 60)

    from src.order.direct_api_saver import POPULATE_DATASET_JS, DirectApiOrderSaver

    saver = DirectApiOrderSaver(driver)

    orders_json = json.dumps([
        {
            'item_cd': str(item.get('item_cd', '')),
            'multiplier': saver._calc_multiplier(item),
            'ord_unit_qty': item.get('order_unit_qty', 1) or 1,
            'store_cd': item.get('store_cd', ''),
            'fields': prefetch_data.get(str(item.get('item_cd', '')), {}),
        }
        for item in items
    ], ensure_ascii=False)

    start = time.time()
    result_str = driver.execute_script(POPULATE_DATASET_JS, orders_json, date_str)
    elapsed = (time.time() - start) * 1000

    if not result_str:
        print("  [FAIL] dataset 채우기 응답 없음")
        return {'error': 'no_response'}

    result = json.loads(result_str)
    print(f"  결과: {json.dumps(result, ensure_ascii=False)}")
    print(f"  소요시간: {elapsed:.0f}ms")

    if result.get('error'):
        print(f"  [FAIL] 에러: {result['error']}")
    else:
        print(f"  [OK] {result.get('added', 0)}건 추가됨")
        print(f"    dataset 행 수: {result.get('dsRowCount', 0)}")
        print(f"    행당 평균 필드: {result.get('avgFieldsPerRow', 0)}")
        print(f"    RowType[0]: {result.get('rowType0', 'N/A')}")
        # RowType 4 = Updated (U)가 정상
        if result.get('rowType0') == 4:
            print("    → RowType=4 (Updated) — 정상! saveOrd가 이 행을 전송합니다.")
        elif result.get('rowType0') == 2:
            print("    → RowType=2 (Normal) — 주의: applyChange 후 변경이 필요합니다.")
        elif result.get('rowType0') == 1:
            print("    → RowType=1 (Inserted) — applyChange가 호출되지 않았습니다.")

    return result


def step_live_save(driver, items: List[Dict], date_str: str) -> Dict:
    """[Step 8] 실제 발주 저장 (--live 모드)"""
    print("\n" + "=" * 60)
    print("[Step 8] Direct API 실제 발주 저장")
    print("=" * 60)
    print(f"  [!!] 실제 발주를 실행합니다!")
    print(f"  상품 수: {len(items)}")
    print(f"  발주일: {date_str}")
    for item in items:
        print(
            f"    {item.get('item_cd')} "
            f"{(item.get('item_nm') or '')[:20]} "
            f"발주={item.get('final_order_qty', 0)}개"
        )

    from src.order.direct_api_saver import DirectApiOrderSaver
    saver = DirectApiOrderSaver(driver)

    # 전략 1: gfn_transaction
    start = time.time()
    result = saver._save_via_transaction(items, date_str)
    elapsed = (time.time() - start) * 1000

    if result:
        print(f"\n  결과:")
        print(f"    성공: {result.success}")
        print(f"    저장 건수: {result.saved_count}")
        print(f"    메서드: {result.method}")
        print(f"    메시지: {result.message}")
        print(f"    소요시간: {elapsed:.0f}ms")
        if result.response_preview:
            print(f"    응답 미리보기: {result.response_preview[:200]}")

        if result.success:
            print("\n  [OK] Direct API 발주 저장 성공!")

            # 검증
            print("\n  [검증] 저장된 데이터 확인...")
            verify = saver.verify_save(items)
            print(f"    검증 결과: {json.dumps(verify, ensure_ascii=False, indent=2)}")
        else:
            print(f"\n  [FAIL] 저장 실패: {result.message}")

        return {
            'success': result.success,
            'saved_count': result.saved_count,
            'method': result.method,
            'message': result.message,
            'elapsed_ms': elapsed,
        }
    else:
        print("  [FAIL] gfn_transaction 전략 실패 (None 반환)")
        return {'success': False, 'message': 'transaction returned None'}


def step_cleanup(driver, populate_result: Dict):
    """[Step 9] 그리드 정리 (dry-run 후 복원)"""
    print("\n" + "=" * 60)
    print("[Step 9] 그리드 데이터 정리")
    print("=" * 60)

    if populate_result and populate_result.get('added', 0) > 0:
        try:
            result = driver.execute_script("""
                try {
                    var app = nexacro.getApplication();
                    var frameSet = app.mainframe.HFrameSet00.VFrameSet00.FrameSet;
                    var stbjForm = null;
                    var fsKeys = Object.keys(frameSet);
                    for (var ki = 0; ki < fsKeys.length; ki++) {
                        try {
                            var f = frameSet[fsKeys[ki]];
                            if (f && f.form && f.form.div_workForm &&
                                f.form.div_workForm.form.div_work_01 &&
                                f.form.div_workForm.form.div_work_01.form.gdList) {
                                stbjForm = f.form;
                                break;
                            }
                        } catch(e) {}
                    }
                    if (!stbjForm) return 'form_not_found';
                    var workForm = stbjForm.div_workForm.form.div_work_01.form;
                    var ds = workForm.gdList._binddataset;
                    if (ds && typeof ds.clearData === 'function') {
                        var before = ds.getRowCount();
                        ds.clearData();
                        return 'cleared_' + before + '_rows';
                    }
                    return 'no_dataset';
                } catch(e) {
                    return 'error: ' + e.message;
                }
            """)
            print(f"  정리 결과: {result}")
        except Exception as e:
            print(f"  정리 실패 (무시 가능): {e}")
    else:
        print("  정리 불필요 (데이터 없음)")


# =====================================================================
# 메인 실행
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Direct API 발주 라이브 테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  발주 가능 확인만:   python scripts/test_direct_api_live.py --check-only
  프리페치 테스트:    python scripts/test_direct_api_live.py --dry-run
  실제 1개 저장:     python scripts/test_direct_api_live.py --live --items 1
  특정 상품 저장:    python scripts/test_direct_api_live.py --live --item-cd 8801043036016 --qty 1
        """,
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="발주 가능 여부만 확인 (로그인 → 메뉴이동 → 가능여부 확인)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="dry-run 모드 (기본값, 실제 저장 안 함)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="실제 발주 저장 실행 (주의: 실제 발주됨!)",
    )
    parser.add_argument(
        "--items",
        type=int,
        default=1,
        help="테스트 상품 수 (기본: 1, 최대: 5)",
    )
    parser.add_argument(
        "--item-cd",
        type=str,
        default=None,
        help="특정 상품코드로 테스트",
    )
    parser.add_argument(
        "--qty",
        type=int,
        default=1,
        help="발주 수량 (기본: 1)",
    )
    parser.add_argument(
        "--store-id",
        type=str,
        default=None,
        help="매장 ID",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="발주일 (YYYYMMDD 또는 YYYY-MM-DD, 기본: 내일)",
    )

    args = parser.parse_args()

    # 안전장치: --live 시 items 제한
    num_items = min(args.items, MAX_LIVE_ITEMS)

    # 발주일 결정
    if args.date:
        date_str = args.date.replace('-', '')
    else:
        tomorrow = datetime.now() + timedelta(days=1)
        date_str = tomorrow.strftime('%Y%m%d')

    is_live = args.live
    mode_str = "LIVE (실제 저장)" if is_live else "DRY-RUN (저장 안 함)"

    print("\n" + "#" * 60)
    print("#  Direct API 발주 라이브 테스트")
    print(f"#  모드: {mode_str}")
    print(f"#  상품 수: {num_items}")
    print(f"#  발주일: {date_str}")
    print(f"#  시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" * 60)

    analyzer = None
    try:
        # Step 1: 로그인
        analyzer = step_login(args.store_id)
        driver = analyzer.driver

        # Step 2: 메뉴 이동
        executor = step_navigate(driver)

        # Step 3: 발주 가능 여부 확인
        avail = step_check_availability(driver)

        if args.check_only:
            print("\n" + "=" * 60)
            print("[완료] --check-only 모드: 발주 가능 여부 확인 완료")
            print("=" * 60)
            return

        if not avail.get('available', True):
            print("\n[!!] 발주 불가 상태입니다.")
            if not is_live:
                print("  → dry-run 모드이므로 계속 진행합니다.")
            else:
                print("  → 발주 가능 시간에 다시 시도하세요.")
                return

        # Step 4: 폼 탐색
        form_info = step_find_form(driver)

        # Step 5: 테스트 상품 선택
        items = step_get_test_items(
            driver, executor,
            item_cd=args.item_cd,
            qty=args.qty,
            num_items=num_items,
            store_id=args.store_id,
        )
        if not items:
            return

        # Step 6: 프리페치
        prefetch_data = step_prefetch(driver, items, date_str)

        # Step 7: dataset 채우기 테스트
        populate_result = step_populate_test(driver, items, date_str, prefetch_data)

        if is_live:
            # Step 8: 실제 저장 — 먼저 그리드 초기화 후 재시도
            # (populate_test에서 채운 데이터는 RowType 확인용이었으므로)
            step_cleanup(driver, populate_result)
            time.sleep(1)

            save_result = step_live_save(driver, items, date_str)

            print("\n" + "=" * 60)
            print("[결과 요약]")
            print(f"  성공: {save_result.get('success')}")
            print(f"  저장 건수: {save_result.get('saved_count', 0)}")
            print(f"  소요시간: {save_result.get('elapsed_ms', 0):.0f}ms")
            print(f"  메시지: {save_result.get('message', '')}")
            print("=" * 60)
        else:
            # dry-run: 정리만
            step_cleanup(driver, populate_result)

            print("\n" + "=" * 60)
            print("[DRY-RUN 완료]")
            print(f"  프리페치: {len(prefetch_data)}/{len(items)}건 성공")
            print(f"  dataset 채우기: {populate_result.get('added', 0)}건")
            print(f"  RowType: {populate_result.get('rowType0', 'N/A')}")
            if populate_result.get('error'):
                print(f"  에러: {populate_result['error']}")
            else:
                print("  [OK] 모든 사전 검증 통과")
                print("  → --live 플래그로 실제 저장을 테스트하세요.")
            print("=" * 60)

    except KeyboardInterrupt:
        print("\n[중단] 사용자 중단")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if analyzer:
            try:
                analyzer.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
