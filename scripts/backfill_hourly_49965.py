#!/usr/bin/env python3
"""
49965 매장 hourly_sales_detail 소급 수집 + bootstrap

hourly 데이터가 0건인 49965 매장에 대해:
1. BGF 로그인 → selPrdT3 Direct API로 과거 데이터 수집
2. 수집 완료 후 소진율 곡선 bootstrap 실행

Usage:
    python scripts/backfill_hourly_49965.py                    # 60일 소급
    python scripts/backfill_hourly_49965.py --days 90          # 90일 소급
    python scripts/backfill_hourly_49965.py --bootstrap-only   # 수집 건너뛰고 bootstrap만
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.logger import get_logger

logger = get_logger("backfill_49965")

STORE_ID = "49965"


def run_backfill(days: int = 60):
    """Phase 1: hourly_sales_detail 소급 수집"""
    from src.sales_analyzer import SalesAnalyzer

    print(f"\n{'='*60}")
    print(f"  [Phase 1] 매장 {STORE_ID} hourly_sales_detail 소급 수집")
    print(f"  기간: 최근 {days}일")
    print(f"{'='*60}")

    # 1. BGF 로그인
    print("\n  [1/3] BGF 로그인 중...")
    analyzer = SalesAnalyzer(store_id=STORE_ID)
    analyzer.setup_driver()
    analyzer.connect()

    try:
        analyzer.do_login()
        print("  로그인 성공")
        time.sleep(2)

        # 2. 매출분석 메뉴 진입 (selPrdT3 템플릿 캡처용)
        print("  [2/3] 매출분석 화면 진입 중...")
        try:
            analyzer.go_to_menu("매출분석", "시간대별 매출 정보")
            time.sleep(3)
        except Exception as e:
            logger.warning(f"메뉴 이동 실패 (Direct API는 계속 시도): {e}")

        # 3. backfill 실행
        print(f"  [3/3] {days}일 소급 수집 시작...")
        from src.application.services.hourly_detail_service import HourlyDetailService

        service = HourlyDetailService(store_id=STORE_ID, driver=analyzer.driver)
        result = service.backfill(days=days, driver=analyzer.driver)

        print(f"\n  수집 완료: {result}")
        return result

    except Exception as e:
        print(f"\n  수집 실패: {e}")
        logger.error(f"backfill 실패: {e}", exc_info=True)
        return {"error": str(e)}

    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass


def run_bootstrap():
    """Phase 2: 소진율 곡선 bootstrap"""
    print(f"\n{'='*60}")
    print(f"  [Phase 2] 매장 {STORE_ID} 소진율 곡선 bootstrap")
    print(f"{'='*60}")

    from src.application.services.food_depletion_service import FoodDepletionService
    service = FoodDepletionService(store_id=STORE_ID)
    result = service.bootstrap_from_history(lookback_days=90)
    print(f"\n  bootstrap 완료: {result}")
    return result


def check_status():
    """현재 상태 확인"""
    import sqlite3
    conn = sqlite3.connect(f'{ROOT}/data/stores/{STORE_ID}.db')

    r1 = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT sales_date) FROM hourly_sales_detail"
    ).fetchone()
    print(f"\n  hourly_sales_detail: {r1[0]}행, {r1[1]}일")

    try:
        r2 = conn.execute("""
            SELECT COUNT(DISTINCT item_cd), MAX(sample_count),
                   ROUND(AVG(sample_count), 1)
            FROM food_popularity_curve
        """).fetchone()
        print(f"  food_popularity_curve: {r2[0]}상품, max_sample={r2[1]}, avg={r2[2]}")
    except Exception:
        print("  food_popularity_curve: 테이블 없음")

    conn.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description="49965 매장 hourly 소급 수집")
    parser.add_argument("--days", type=int, default=60, help="소급 기간")
    parser.add_argument("--bootstrap-only", action="store_true",
                        help="수집 건너뛰고 bootstrap만")
    parser.add_argument("--status", action="store_true", help="현재 상태만 확인")
    args = parser.parse_args()

    print(f"매장 {STORE_ID} hourly 소급 수집 + bootstrap")

    if args.status:
        check_status()
        return

    if not args.bootstrap_only:
        backfill_result = run_backfill(days=args.days)
        if "error" in backfill_result:
            print("\n수집 실패. bootstrap 건너뜀.")
            return

    check_status()
    run_bootstrap()
    check_status()

    print(f"\n{'='*60}")
    print(f"  전체 완료!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
