"""
Smart Order Override 라이브 테스트 스크립트

1. BGF 로그인
2. 발주현황조회 > 스마트 탭 → 스마트발주 상품 목록 수집 (DB 갱신)
3. 수집된 스마트 상품을 예측 파이프라인에 주입
4. 단품별발주로 실제 발주 실행 (스마트→수동 전환)

Usage:
    # dry-run (발주 시뮬레이션만)
    python scripts/test_smart_order_override.py

    # 실제 발주 (--run 플래그 필수)
    python scripts/test_smart_order_override.py --run

    # 최대 N개만 발주
    python scripts/test_smart_order_override.py --run --max-items 3
"""

import sys
import io
import time
import argparse
from pathlib import Path
from datetime import datetime

# Windows CP949 콘솔 -> UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.logger import get_logger
logger = get_logger("smart_override_test")


def run_test(dry_run: bool = True, max_items: int = 0):
    """스마트 오버라이드 라이브 테스트 실행"""

    from src.sales_analyzer import SalesAnalyzer
    from src.settings.constants import DEFAULT_STORE_ID, SMART_OVERRIDE_MIN_QTY
    from src.infrastructure.database.repos import (
        SmartOrderItemRepository, AppSettingsRepository,
    )
    from src.collectors.order_status_collector import OrderStatusCollector

    store_id = DEFAULT_STORE_ID
    analyzer = None
    start_time = time.time()

    print("\n" + "=" * 70)
    print("Smart Order Override 라이브 테스트")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"모드: {'DRY-RUN (시뮬레이션)' if dry_run else '실제 발주'}")
    print(f"MAX_ITEMS: {max_items if max_items > 0 else '무제한'}")
    print(f"SMART_OVERRIDE_MIN_QTY: {SMART_OVERRIDE_MIN_QTY}")
    print("=" * 70)

    try:
        # ==============================================================
        # Step 1: BGF 로그인
        # ==============================================================
        print("\n[Step 1] BGF 시스템 로그인")
        print("-" * 50)

        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return {"success": False, "step": "login"}

        print("[OK] 로그인 성공")
        analyzer.close_popup()
        time.sleep(2)

        driver = analyzer.driver

        # ==============================================================
        # Step 2: 발주현황조회 > 스마트 탭 → DB 갱신
        # ==============================================================
        print("\n[Step 2] 발주현황조회 > 스마트발주 상품 수집")
        print("-" * 50)

        collector = OrderStatusCollector(driver, store_id=store_id)
        try:
            if not collector.navigate_to_order_status_menu():
                print("[ERROR] 발주현황조회 메뉴 이동 실패")
                return {"success": False, "step": "navigate"}

            print("[OK] 발주현황조회 메뉴 진입 성공")

            # 스마트 탭 수집
            smart_detail = collector.collect_smart_order_items_detail()
            if smart_detail is None:
                print("[ERROR] 스마트발주 상품 조회 실패")
                return {"success": False, "step": "smart_collect"}

            # DB 갱신
            smart_repo = SmartOrderItemRepository(store_id=store_id)
            saved = smart_repo.refresh(smart_detail, store_id=store_id)
            print(f"[OK] 스마트발주 상품 수집: {len(smart_detail)}개 → DB {saved}건 갱신")

            # mid_cd 분포
            mid_dist = {}
            for item in smart_detail:
                mc = item.get("mid_cd", "???")
                mid_dist[mc] = mid_dist.get(mc, 0) + 1
            print(f"     mid_cd 분포: {dict(sorted(mid_dist.items(), key=lambda x: -x[1]))}")

        finally:
            collector.close_menu()
            time.sleep(1)

        # ==============================================================
        # Step 3: SMART_ORDER_OVERRIDE 활성화 확인
        # ==============================================================
        print("\n[Step 3] 설정 확인")
        print("-" * 50)

        settings_repo = AppSettingsRepository(store_id=store_id)
        override_enabled = settings_repo.get("SMART_ORDER_OVERRIDE", False)
        print(f"  SMART_ORDER_OVERRIDE = {override_enabled}")

        if not override_enabled:
            print("[WARN] SMART_ORDER_OVERRIDE가 비활성화 상태입니다. 활성화합니다.")
            settings_repo.set("SMART_ORDER_OVERRIDE", True)
            print("[OK] SMART_ORDER_OVERRIDE = True 설정 완료")

        # ==============================================================
        # Step 4: 예측 + 발주 실행 (DailyOrderFlow 경유)
        # ==============================================================
        print("\n[Step 4] 예측 + 발주 실행")
        print("-" * 50)

        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id(store_id)
        flow = DailyOrderFlow(
            store_ctx=ctx,
            driver=driver,
            use_improved_predictor=True,
        )

        # 3/3 날짜 발주
        target_date = datetime.now().strftime("%Y-%m-%d")

        flow_result = flow.run_auto_order(
            dry_run=dry_run,
            min_order_qty=0,  # qty=0도 허용
            max_items=max_items if max_items > 0 else None,
            prefetch_pending=True,
            max_pending_items=200,
            collect_fail_reasons=True,
            skip_exclusion_fetch=True,  # Step 2에서 이미 수집했으므로 건너뜀
        )

        # ==============================================================
        # Step 5: 결과 분석
        # ==============================================================
        elapsed = time.time() - start_time

        print("\n" + "=" * 70)
        print("결과 요약")
        print("=" * 70)
        print(f"소요 시간: {elapsed:.1f}초")
        print(f"모드: {'DRY-RUN' if dry_run else '실제 발주'}")

        exec_result = flow_result.get("execution", flow_result)
        success_count = exec_result.get("success_count", flow_result.get("success_count", 0))
        fail_count = exec_result.get("fail_count", flow_result.get("fail_count", 0))

        print(f"발주 성공: {success_count}건")
        print(f"발주 실패: {fail_count}건")

        # smart_override 항목 분석
        order_list = exec_result.get("order_list", [])
        if order_list:
            so_items = [i for i in order_list if i.get("smart_override")]
            so_positive = [i for i in so_items if i.get("final_order_qty", 0) > 0]
            so_zero = [i for i in so_items if i.get("final_order_qty", 0) == 0]

            print(f"\n[SmartOverride 상품]")
            print(f"  전체: {len(so_items)}개")
            print(f"  예측 qty > 0: {len(so_positive)}개")
            print(f"  전환용 qty = 0: {len(so_zero)}개")

            if so_positive:
                print(f"\n  [예측 발주 상위 5개]")
                so_positive.sort(key=lambda x: x.get("final_order_qty", 0), reverse=True)
                for i, item in enumerate(so_positive[:5]):
                    print(f"    {i+1}. {item.get('item_nm', '')} | qty={item['final_order_qty']} | {item['item_cd']}")

            if so_zero:
                print(f"\n  [전환용 상위 5개]")
                for i, item in enumerate(so_zero[:5]):
                    print(f"    {i+1}. {item.get('item_nm', '')} | qty=0 | {item['item_cd']}")

        # 발주 실행 결과
        results = exec_result.get("results", [])
        if results:
            so_results = [r for r in results if r.get("smart_override")]
            if so_results:
                so_ok = [r for r in so_results if r.get("success")]
                so_fail = [r for r in so_results if not r.get("success")]
                print(f"\n[SmartOverride 발주 실행 결과]")
                print(f"  성공: {len(so_ok)}건")
                print(f"  실패: {len(so_fail)}건")
                if so_fail:
                    for r in so_fail[:5]:
                        print(f"    실패: {r.get('item_cd')} - {r.get('error', 'unknown')}")

        return {
            "success": True,
            "elapsed": elapsed,
            "dry_run": dry_run,
            "smart_items_collected": len(smart_detail),
            "success_count": success_count,
            "fail_count": fail_count,
        }

    except Exception as e:
        logger.error(f"테스트 실패: {e}", exc_info=True)
        print(f"\n[ERROR] {e}")
        return {"success": False, "error": str(e)}

    finally:
        if analyzer:
            try:
                analyzer.driver.quit()
                print("\n[정리] 브라우저 종료")
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart Order Override 라이브 테스트")
    parser.add_argument("--run", action="store_true", help="실제 발주 실행 (없으면 dry-run)")
    parser.add_argument("--max-items", type=int, default=0, help="최대 발주 상품 수 (0=무제한)")
    args = parser.parse_args()

    result = run_test(dry_run=not args.run, max_items=args.max_items)

    print("\n" + "=" * 70)
    if result.get("success"):
        print("테스트 완료!")
    else:
        print(f"테스트 실패: {result.get('error', result.get('step', 'unknown'))}")
    print("=" * 70)
