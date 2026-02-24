"""
전체 자동화 플로우 실행
1. BGF 로그인
2. 판매 데이터 수집
3. DB 현황 확인
4. 발주 실행 (DailyOrderFlow 위임)
"""

import sys
import io
from pathlib import Path

# Windows CP949 콘솔 -> UTF-8 래핑 (한글/특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 로그 정리 (30일 초과 삭제, 50MB 초과 잘라내기)
from src.utils.logger import cleanup_old_logs
cleanup_old_logs(max_age_days=30, max_file_mb=50)

import time
from datetime import datetime
from typing import Any, Dict

from src.sales_analyzer import SalesAnalyzer
from src.infrastructure.database.repos import SalesRepository
from src.collectors.sales_collector import SalesCollector
from src.settings.constants import DEFAULT_STORE_ID


def run_full_flow(
    dry_run: bool = True,
    collect_sales: bool = True,
    max_order_items: int = 5,
    min_order_qty: int = 1,
    store_id: str = DEFAULT_STORE_ID,
) -> Dict[str, Any]:
    """
    전체 플로우 실행

    로그인/수집은 스크립트 고유 로직이므로 직접 처리하고,
    핵심 발주 파이프라인은 DailyOrderFlow에 위임합니다.

    Args:
        dry_run: True면 실제 발주 안함 (테스트)
        collect_sales: 판매 데이터 수집 여부
        max_order_items: 발주할 최대 상품 수
        min_order_qty: 최소 발주량
        store_id: 점포 코드
    """
    print("\n" + "="*70)
    print("BGF 자동화 시스템 - 전체 플로우")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    analyzer = None
    start_time = time.time()

    try:
        # ============================================================
        # 1단계: BGF 로그인
        # ============================================================
        print("\n" + "="*70)
        print("[1단계] BGF 시스템 로그인")
        print("="*70)

        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return {"success": False, "step": "login", "message": "login failed"}

        print("[OK] 로그인 성공")

        # 팝업 닫기
        analyzer.close_popup()
        time.sleep(2)

        # ============================================================
        # 2단계: 판매 데이터 수집 (스크립트 고유 — 누락일 백필)
        # ============================================================
        if collect_sales:
            print("\n" + "="*70)
            print("[2단계] 판매 데이터 수집")
            print("="*70)

            # 판매현황 메뉴로 이동
            print("\n[이동] 판매현황 메뉴...")
            if not analyzer.navigate_to_sales_menu():
                print("[WARN] 메뉴 이동 실패, 수집 건너뜀")
            else:
                repo = SalesRepository(store_id=store_id)

                # 과거 30일 중 누락된 날짜 확인
                missing_dates = repo.get_missing_dates(days=30)
                print(f"\n[확인] 과거 30일 중 누락된 날짜: {len(missing_dates)}개")

                if missing_dates:
                    print(f"  누락 날짜: {missing_dates[:5]}{'...' if len(missing_dates) > 5 else ''}")

                    # 날짜 순(오래된 것부터)으로 수집
                    for date_str in missing_dates:
                        # YYYY-MM-DD -> YYYYMMDD 변환
                        target_date = date_str.replace("-", "")
                        print(f"\n[수집] {target_date} (누락 데이터)")

                        try:
                            data = analyzer.collect_all_mid_category_data(target_date)
                            if data:
                                print(f"  -> 수집 완료: {len(data)}건")
                                stats = repo.save_daily_sales(data, date_str)
                                print(f"  -> DB 저장: 신규 {stats.get('new', 0)}건, 업데이트 {stats.get('updated', 0)}건")
                            else:
                                print(f"  -> 데이터 없음")
                        except Exception as e:
                            print(f"  -> 수집 실패: {e}")

                        time.sleep(0.5)  # 요청 간 딜레이
                else:
                    print("  [OK] 과거 30일 데이터 모두 수집됨")

                # 오늘 데이터 수집 (YYYYMMDD 형식)
                target_date = datetime.now().strftime("%Y%m%d")
                print(f"\n[수집] {target_date} (오늘)")

                try:
                    data = analyzer.collect_all_mid_category_data(target_date)
                    if data:
                        print(f"  -> 수집 완료: {len(data)}건")

                        # DB 저장
                        sales_date = datetime.now().strftime("%Y-%m-%d")
                        stats = repo.save_daily_sales(data, sales_date)
                        print(f"  -> DB 저장: 신규 {stats.get('new', 0)}건, 업데이트 {stats.get('updated', 0)}건")
                except Exception as e:
                    print(f"  -> 수집 실패: {e}")

            # 매출분석 탭 닫기 (발주 화면 진입 전 정리)
            print("\n[정리] 매출분석 탭 닫기...")
            sales_collector = SalesCollector(analyzer)
            sales_collector.close_sales_menu()

            time.sleep(1)
        else:
            print("\n[2단계] 판매 데이터 수집 건너뜀")

        # ============================================================
        # 3단계: DB 현황 확인
        # ============================================================
        print("\n" + "="*70)
        print("[3단계] DB 현황 확인")
        print("="*70)

        sales_repo = SalesRepository(store_id=store_id)
        stats = sales_repo.get_stats_summary()

        print(f"  총 상품 수: {stats.get('total_products', 0)}개")
        print(f"  총 카테고리: {stats.get('total_categories', 0)}개")
        print(f"  수집된 일수: {stats.get('total_days', 0)}일")
        print(f"  기간: {stats.get('first_date', 'N/A')} ~ {stats.get('last_date', 'N/A')}")

        # ============================================================
        # 4단계: 발주 실행 (DailyOrderFlow 위임)
        # ============================================================
        print("\n" + "="*70)
        print(f"[4단계] 발주 실행 ({'테스트 모드' if dry_run else '실제 발주'})")
        print("="*70)

        if dry_run:
            print("\n[INFO] dry_run=True -> 실제 발주 없이 시뮬레이션만 수행")

        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id(store_id)
        flow = DailyOrderFlow(
            store_ctx=ctx,
            driver=analyzer.driver,
            use_improved_predictor=True,
        )
        flow_result = flow.run_auto_order(
            dry_run=dry_run,
            min_order_qty=min_order_qty,
            max_items=max_order_items if max_order_items > 0 else None,
            prefetch_pending=True,
            max_pending_items=100,
            collect_fail_reasons=True,
        )

        # ============================================================
        # 결과 요약
        # ============================================================
        elapsed = time.time() - start_time

        exec_result = flow_result.get("execution", {})
        fail_reasons = flow_result.get("fail_reasons")

        print("\n" + "="*70)
        print("전체 플로우 완료")
        print("="*70)
        print(f"소요 시간: {elapsed:.1f}초")
        print(f"발주 성공: {flow_result.get('success_count', 0)}건")
        print(f"발주 실패: {flow_result.get('fail_count', 0)}건")
        if fail_reasons:
            print(f"실패 사유 확인: {fail_reasons.get('checked', 0)}건")
        print(f"모드: {'테스트 (dry_run)' if dry_run else '실제 발주'}")

        if exec_result.get('grouped_by_date'):
            print("\n[발주 일정]")
            for date, count in exec_result.get('grouped_by_date', {}).items():
                print(f"  {date}: {count}개 상품")

        return {
            "success": flow_result.get("success", False),
            "elapsed": elapsed,
            "success_count": flow_result.get("success_count", 0),
            "fail_count": flow_result.get("fail_count", 0),
            "dry_run": dry_run
        }

    except Exception as e:
        print(f"\n[ERROR] 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e)}

    finally:
        if analyzer:
            try:
                input("\n브라우저를 닫으려면 Enter를 누르세요...")
            except EOFError:
                pass  # 비대화형 모드에서는 바로 종료
            analyzer.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BGF 전체 자동화 플로우")
    parser.add_argument("--run", action="store_true", help="실제 발주 실행")
    parser.add_argument("--no-collect", action="store_true", help="판매 데이터 수집 건너뜀")
    parser.add_argument("--max-items", "-n", type=int, default=5, help="최대 발주 상품 수")
    parser.add_argument("--min-qty", "-q", type=int, default=1, help="최소 발주량")
    parser.add_argument("--store-id", type=str, default=DEFAULT_STORE_ID, help="점포 코드 (예: 46513)")

    args = parser.parse_args()

    run_full_flow(
        dry_run=not args.run,
        collect_sales=not args.no_collect,
        max_order_items=args.max_items,
        min_order_qty=args.min_qty,
        store_id=args.store_id,
    )
