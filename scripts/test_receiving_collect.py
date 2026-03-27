"""
입고 수집 로직 디버깅 테스트 스크립트

46704 점포의 입고 수집(Phase 1.1) 로직만 단독 실행하여
수집 결과를 상세 로깅하고, DB 재고와의 차이를 검증한다.

Usage:
    python scripts/test_receiving_collect.py --store 46704
    python scripts/test_receiving_collect.py --store 46704 --days 3
"""

import os
import sys
import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트 설정
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from src.sales_analyzer import SalesAnalyzer
from src.collectors.receiving_collector import ReceivingCollector
from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository
from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger("test_receiving")


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def step1_check_db_before(store_id: str) -> dict:
    """수집 전 DB 상태 확인"""
    print_section("STEP 1: 수집 전 DB 상태 확인")

    inv_repo = RealtimeInventoryRepository(store_id=store_id)
    conn = inv_repo._get_conn()

    try:
        cursor = conn.cursor()

        # 전체 상품 수
        cursor.execute(
            "SELECT COUNT(*) FROM realtime_inventory WHERE store_id = ?",
            (store_id,)
        )
        total_items = cursor.fetchone()[0]

        # queried_at 분포
        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        cursor.execute("""
            SELECT
                CASE
                    WHEN queried_at IS NULL THEN 'NULL'
                    WHEN substr(queried_at, 1, 10) = ? THEN 'TODAY'
                    WHEN substr(queried_at, 1, 10) = ? THEN 'YESTERDAY'
                    ELSE 'OLDER'
                END as period,
                COUNT(*) as cnt
            FROM realtime_inventory
            WHERE store_id = ?
            GROUP BY period
        """, (today_str, yesterday_str, store_id))

        queried_dist = {row[0]: row[1] for row in cursor.fetchall()}

        # stock_qty > 0 인 상품 수
        cursor.execute(
            "SELECT COUNT(*) FROM realtime_inventory WHERE store_id = ? AND stock_qty > 0",
            (store_id,)
        )
        has_stock = cursor.fetchone()[0]

        print(f"  총 상품 수: {total_items}")
        print(f"  재고 있는 상품: {has_stock}")
        print(f"  queried_at 분포:")
        for period, cnt in sorted(queried_dist.items()):
            print(f"    {period}: {cnt}개")

        return {
            "total_items": total_items,
            "has_stock": has_stock,
            "queried_dist": queried_dist,
        }
    finally:
        conn.close()


def step2_collect_receiving(driver, store_id: str, days: int = 1) -> dict:
    """입고 데이터 수집 (메뉴 이동 + 수집)"""
    print_section("STEP 2: 입고 데이터 수집")

    recv_collector = ReceivingCollector(driver=driver, store_id=store_id)

    # 메뉴 이동
    print("  센터매입 조회/확정 메뉴 이동 중...")
    if not recv_collector.navigate_to_receiving_menu():
        print("  [ERROR] 메뉴 이동 실패!")
        return {"error": "menu_navigation_failed"}

    print("  메뉴 이동 성공")

    # 날짜 목록 확인
    available_dates = recv_collector.get_available_dates()
    print(f"\n  사용 가능한 입고일: {len(available_dates)}개")
    for d in available_dates:
        dgfw = d.get('DGFW_YMD', '')
        view = d.get('VIEW_YMD', '')
        res = d.get('RES_GUBN', '')
        print(f"    {dgfw} ({view}) RES_GUBN={res}")

    # 수집할 날짜 결정
    today = datetime.now()
    collect_dates = []
    for i in range(days):
        dt = today - timedelta(days=i)
        collect_dates.append(dt.strftime("%Y%m%d"))

    print(f"\n  수집 대상 날짜: {collect_dates}")

    # 날짜별 수집 (collect_and_save 호출하지 않고, 수집만 먼저)
    all_results = {}
    for date_str in collect_dates:
        print(f"\n  --- {date_str} 수집 ---")

        # 1) 데이터만 먼저 수집 (DB 저장 전)
        raw_data = recv_collector.collect_receiving_data(date_str)
        print(f"  수집된 레코드: {len(raw_data)}건")

        if raw_data:
            # 상품별 요약
            recv_items = {}
            pending_items = {}
            for record in raw_data:
                item_cd = record.get('item_cd', '')
                item_nm = record.get('item_nm', '')
                recv_qty = recv_collector._to_int(record.get('receiving_qty', 0))
                plan_qty = recv_collector._to_int(record.get('plan_qty', 0))

                if recv_qty > 0:
                    if item_cd not in recv_items:
                        recv_items[item_cd] = {"nm": item_nm, "qty": 0}
                    recv_items[item_cd]["qty"] += recv_qty
                elif plan_qty > 0:
                    if item_cd not in pending_items:
                        pending_items[item_cd] = {"nm": item_nm, "qty": 0}
                    pending_items[item_cd]["qty"] += plan_qty

            print(f"  검수 확정 상품: {len(recv_items)}개 (총 {sum(v['qty'] for v in recv_items.values())}개)")
            print(f"  검수 미확정(미입고) 상품: {len(pending_items)}개 (총 {sum(v['qty'] for v in pending_items.values())}개)")

            # 상위 10개 표시
            if recv_items:
                print(f"\n  [검수 확정 상위 10개]")
                sorted_recv = sorted(recv_items.items(), key=lambda x: x[1]["qty"], reverse=True)
                for item_cd, info in sorted_recv[:10]:
                    print(f"    {item_cd} | {info['nm'][:20]:20s} | +{info['qty']}")

            if pending_items:
                print(f"\n  [검수 미확정(미입고) 상위 10개]")
                sorted_pend = sorted(pending_items.items(), key=lambda x: x[1]["qty"], reverse=True)
                for item_cd, info in sorted_pend[:10]:
                    print(f"    {item_cd} | {info['nm'][:20]:20s} | 예정 {info['qty']}")

        all_results[date_str] = {
            "raw_data": raw_data,
            "recv_count": len(raw_data),
        }

    # 메뉴 닫기
    try:
        recv_collector.close_receiving_menu()
    except Exception:
        pass

    return all_results


def step3_simulate_stock_update(store_id: str, all_results: dict) -> dict:
    """update_stock_from_receiving 로직을 시뮬레이션하여 스킵되는 상품 확인"""
    print_section("STEP 3: 재고 갱신 시뮬레이션 (실제 DB 변경 없음)")

    inv_repo = RealtimeInventoryRepository(store_id=store_id)
    today_str = datetime.now().strftime("%Y-%m-%d")

    total_stats = {
        "would_update": 0,
        "skipped_fresh": 0,
        "skipped_no_data": 0,
        "skipped_items": [],
        "update_items": [],
    }

    for date_str, result in all_results.items():
        raw_data = result.get("raw_data", [])
        if not raw_data:
            continue

        print(f"\n  --- {date_str} 시뮬레이션 ---")

        # 상품별 입고수량 합산 (receiving_collector와 동일한 로직)
        item_receiving_qty = {}
        for record in raw_data:
            item_cd = record.get('item_cd')
            if not item_cd:
                continue
            recv_qty = ReceivingCollector._to_int(record.get('receiving_qty', 0))
            if recv_qty > 0:
                item_receiving_qty[item_cd] = item_receiving_qty.get(item_cd, 0) + recv_qty

        print(f"  입고 확정 상품: {len(item_receiving_qty)}개")

        for item_cd, total_recv_qty in item_receiving_qty.items():
            current = inv_repo.get(item_cd, store_id=store_id)

            if not current:
                total_stats["skipped_no_data"] += 1
                continue

            # ★ queried_at 스킵 로직 (원본 코드와 동일)
            queried_at = current.get("queried_at", "")
            current_stock = current.get("stock_qty", 0)

            if queried_at and queried_at[:10] == today_str:
                total_stats["skipped_fresh"] += 1
                total_stats["skipped_items"].append({
                    "item_cd": item_cd,
                    "item_nm": current.get("item_nm", ""),
                    "current_stock": current_stock,
                    "recv_qty": total_recv_qty,
                    "queried_at": queried_at,
                    "reason": "queried_at == today",
                })
            else:
                new_stock = current_stock + total_recv_qty
                total_stats["would_update"] += 1
                total_stats["update_items"].append({
                    "item_cd": item_cd,
                    "item_nm": current.get("item_nm", ""),
                    "current_stock": current_stock,
                    "new_stock": new_stock,
                    "recv_qty": total_recv_qty,
                    "queried_at": queried_at,
                })

    print(f"\n  === 시뮬레이션 결과 ===")
    print(f"  갱신 예정: {total_stats['would_update']}건")
    print(f"  스킵 (queried_at==today): {total_stats['skipped_fresh']}건")
    print(f"  스킵 (DB 미등록): {total_stats['skipped_no_data']}건")

    if total_stats["skipped_items"]:
        print(f"\n  [스킵된 상품 상세 (상위 20개)]")
        for item in total_stats["skipped_items"][:20]:
            print(f"    {item['item_cd']} | {item['item_nm'][:20]:20s} | "
                  f"현재재고={item['current_stock']} | 입고={item['recv_qty']} | "
                  f"queried_at={item['queried_at']}")

    if total_stats["update_items"]:
        print(f"\n  [갱신 예정 상품 상세 (상위 20개)]")
        for item in total_stats["update_items"][:20]:
            print(f"    {item['item_cd']} | {item['item_nm'][:20]:20s} | "
                  f"재고 {item['current_stock']} → {item['new_stock']} (+{item['recv_qty']}) | "
                  f"queried_at={item['queried_at']}")

    return total_stats


def step4_actual_collect_and_save(driver, store_id: str, days: int = 1) -> dict:
    """실제 collect_and_save 실행하여 결과 확인"""
    print_section("STEP 4: 실제 수집+저장 실행 (collect_and_save)")

    recv_collector = ReceivingCollector(driver=driver, store_id=store_id)

    # 메뉴 이동
    print("  센터매입 조회/확정 메뉴 이동 중...")
    if not recv_collector.navigate_to_receiving_menu():
        print("  [ERROR] 메뉴 이동 실패!")
        return {"error": "menu_navigation_failed"}

    today = datetime.now()
    all_stats = {}

    for i in range(days):
        dt = today - timedelta(days=i)
        date_str = dt.strftime("%Y%m%d")
        date_display = dt.strftime("%Y-%m-%d")

        print(f"\n  --- {date_display} ({date_str}) collect_and_save ---")

        stats = recv_collector.collect_and_save(date_str)
        all_stats[date_str] = stats

        print(f"  결과: {stats}")
        print(f"    총 수집: {stats.get('total', 0)}건")
        print(f"    신규: {stats.get('new', 0)}건")
        print(f"    업데이트: {stats.get('updated', 0)}건")
        print(f"    재고 갱신: {stats.get('stock_updated', 0)}건")
        print(f"    재고 스킵: {stats.get('stock_skipped', 0)}건")
        print(f"    배치 생성: {stats.get('batches_created', 0)}건")

    try:
        recv_collector.close_receiving_menu()
    except Exception:
        pass

    return all_stats


def step5_verify_after(store_id: str, before_state: dict) -> dict:
    """수집 후 DB 변경 검증"""
    print_section("STEP 5: 수집 후 DB 변경 검증")

    inv_repo = RealtimeInventoryRepository(store_id=store_id)
    conn = inv_repo._get_conn()

    try:
        cursor = conn.cursor()

        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # queried_at 분포 (변경 후)
        cursor.execute("""
            SELECT
                CASE
                    WHEN queried_at IS NULL THEN 'NULL'
                    WHEN substr(queried_at, 1, 10) = ? THEN 'TODAY'
                    WHEN substr(queried_at, 1, 10) = ? THEN 'YESTERDAY'
                    ELSE 'OLDER'
                END as period,
                COUNT(*) as cnt
            FROM realtime_inventory
            WHERE store_id = ?
            GROUP BY period
        """, (today_str, yesterday_str, store_id))

        queried_dist_after = {row[0]: row[1] for row in cursor.fetchall()}
        queried_dist_before = before_state.get("queried_dist", {})

        print(f"  queried_at 분포 변화:")
        print(f"  {'기간':10s} | {'수집 전':>8s} | {'수집 후':>8s} | {'변화':>8s}")
        print(f"  {'-'*42}")
        for period in sorted(set(list(queried_dist_before.keys()) + list(queried_dist_after.keys()))):
            before = queried_dist_before.get(period, 0)
            after = queried_dist_after.get(period, 0)
            diff = after - before
            diff_str = f"+{diff}" if diff > 0 else str(diff)
            print(f"  {period:10s} | {before:>8d} | {after:>8d} | {diff_str:>8s}")

        # receiving_history에서 오늘 저장된 건수
        recv_repo = ReceivingRepository(store_id=store_id)
        recv_conn = recv_repo._get_conn()
        try:
            recv_cursor = recv_conn.cursor()
            recv_cursor.execute("""
                SELECT receiving_date, COUNT(*) as cnt,
                       SUM(CASE WHEN receiving_qty > 0 THEN 1 ELSE 0 END) as confirmed,
                       SUM(CASE WHEN receiving_qty = 0 THEN 1 ELSE 0 END) as pending
                FROM receiving_history
                WHERE store_id = ?
                  AND created_at >= ?
                GROUP BY receiving_date
                ORDER BY receiving_date DESC
            """, (store_id, today_str))

            print(f"\n  오늘 저장된 receiving_history:")
            print(f"  {'입고일':12s} | {'전체':>6s} | {'확정':>6s} | {'미확정':>6s}")
            print(f"  {'-'*42}")
            for row in recv_cursor.fetchall():
                print(f"  {row[0]:12s} | {row[1]:>6d} | {row[2]:>6d} | {row[3]:>6d}")
        finally:
            recv_conn.close()

        return {"queried_dist_after": queried_dist_after}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="입고 수집 로직 테스트")
    parser.add_argument("--store", default="46704", help="점포 코드")
    parser.add_argument("--days", type=int, default=1, help="수집 날짜 수 (오늘 포함, 기본 1)")
    parser.add_argument("--simulate-only", action="store_true",
                        help="시뮬레이션만 (실제 수집+저장 안함)")
    args = parser.parse_args()

    store_id = args.store
    days = args.days

    print(f"\n{'#'*60}")
    print(f"  입고 수집 로직 테스트")
    print(f"  점포: {store_id}")
    print(f"  수집 일수: {days}일 (오늘부터)")
    print(f"  시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    # STEP 1: 수집 전 DB 상태
    before_state = step1_check_db_before(store_id)

    # 로그인
    print_section("BGF 사이트 로그인")
    print(f"  SalesAnalyzer(store_id={store_id}) 초기화...")
    analyzer = SalesAnalyzer(store_id=store_id)
    analyzer.setup_driver()
    analyzer.connect()

    login_ok = analyzer.do_login()
    if not login_ok:
        print("  [ERROR] 로그인 실패!")
        analyzer.driver.quit()
        return

    print(f"  로그인 성공 (store_id={store_id})")
    time.sleep(2)

    driver = analyzer.driver

    try:
        # STEP 2: 데이터 수집 (raw)
        collect_results = step2_collect_receiving(driver, store_id, days)
        if "error" in collect_results:
            print(f"  수집 실패: {collect_results['error']}")
            return

        # STEP 3: 재고 갱신 시뮬레이션
        sim_stats = step3_simulate_stock_update(store_id, collect_results)

        if args.simulate_only:
            print_section("시뮬레이션 모드 - 실제 저장 건너뜀")
            return

        # STEP 4: 실제 수집+저장
        save_stats = step4_actual_collect_and_save(driver, store_id, days)

        # STEP 5: 검증
        step5_verify_after(store_id, before_state)

        # 최종 요약
        print_section("최종 요약")
        print(f"  시뮬레이션 결과:")
        print(f"    갱신 대상: {sim_stats['would_update']}건")
        print(f"    스킵 (queried_at==today): {sim_stats['skipped_fresh']}건")
        print(f"    스킵 (미등록): {sim_stats['skipped_no_data']}건")
        print(f"\n  실제 저장 결과:")
        for date_str, stats in save_stats.items():
            print(f"    {date_str}: 수집={stats.get('total', 0)}, "
                  f"재고갱신={stats.get('stock_updated', 0)}, "
                  f"스킵={stats.get('stock_skipped', 0)}")

        if sim_stats['skipped_fresh'] > 0:
            pct = sim_stats['skipped_fresh'] / (sim_stats['would_update'] + sim_stats['skipped_fresh'] + sim_stats['skipped_no_data']) * 100
            print(f"\n  ⚠ queried_at 스킵 비율: {pct:.1f}%")
            print(f"  → queried_at==today로 인해 {sim_stats['skipped_fresh']}건의 입고가 DB에 미반영됨")

    finally:
        print("\n  드라이버 종료...")
        driver.quit()
        print("  완료")


if __name__ == "__main__":
    main()
