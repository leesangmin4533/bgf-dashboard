"""
컷상품 검증 스크립트

DB에 저장된 CUT 상품을 실제 BGF 발주 화면에서 재확인하여
CUT 상태가 일치하는지 검증합니다.

사용법:
    python scripts/verify_cut_items.py          # DB CUT 상품 검증
    python scripts/verify_cut_items.py --all     # 최근 조회 상품 전체 CUT 확인
    python scripts/verify_cut_items.py --item 8809256674643  # 특정 상품 확인
"""

import sys
import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime

# 프로젝트 루트와 src 폴더를 path에 추가
project_root = Path(__file__).parent.parent
src_root = project_root / "src"
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(src_root))
os.chdir(str(src_root))

from sales_analyzer import SalesAnalyzer
from collectors.order_prep_collector import OrderPrepCollector
from db.repository import RealtimeInventoryRepository


def get_cut_items_from_db():
    """DB에서 CUT 상품 목록 조회"""
    repo = RealtimeInventoryRepository()
    cut_items = repo.get_cut_items_detail()
    # get_cut_items_detail()은 is_cut_item 필드를 반환하지 않으므로 명시 추가
    for item in cut_items:
        item['is_cut_item'] = 1
    return cut_items


def get_recent_items_from_db(limit=20):
    """DB에서 최근 조회된 상품 목록"""
    db_path = project_root / "data" / "bgf_sales.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT item_cd, item_nm, is_cut_item, queried_at "
        "FROM realtime_inventory ORDER BY queried_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def verify_cut_on_bgf(driver, item_codes):
    """
    실제 BGF 발주 화면에서 CUT 여부 확인

    Returns:
        {item_cd: {'item_nm': str, 'is_cut_item': bool, 'bgf_raw': str, 'success': bool}}
    """
    collector = OrderPrepCollector(driver, save_to_db=False)
    results = {}

    # 메뉴 이동
    print("\n[이동] 단품별 발주 화면...")
    if not collector.navigate_to_menu():
        print("[ERROR] 메뉴 이동 실패")
        return results

    # 날짜 선택
    if not collector.select_order_date():
        print("[ERROR] 날짜 선택 실패")
        return results

    print(f"\n[검증] {len(item_codes)}개 상품 CUT 여부 확인 시작\n")
    print(f"{'상품코드':<16} {'상품명':<30} {'CUT여부':<10} {'상태'}")
    print("-" * 76)

    for i, item_cd in enumerate(item_codes):
        result = collector.query_item_order_history(item_cd)

        if result and result.get('success', False):
            is_cut = result.get('is_cut_item', False)
            item_nm = result.get('item_nm', '알 수 없음')
            cut_text = "발주중지" if is_cut else "정상"

            results[item_cd] = {
                'item_nm': item_nm,
                'is_cut_item': is_cut,
                'current_stock': result.get('current_stock', 0),
                'pending_qty': result.get('pending_qty', 0),
                'success': True
            }

            print(f"{item_cd:<16} {item_nm:<30} {'[CUT]' if is_cut else '정상':<10} 확인완료")
        else:
            results[item_cd] = {
                'item_nm': '',
                'is_cut_item': None,
                'success': False
            }
            print(f"{item_cd:<16} {'???':<30} {'???':<10} 조회실패")

        time.sleep(0.5)

    # 메뉴 닫기
    collector.close_menu()

    return results


def print_verification_report(db_items, bgf_results):
    """DB vs BGF 비교 리포트 출력"""
    print("\n" + "=" * 76)
    print("검증 결과 리포트")
    print("=" * 76)

    match_count = 0
    mismatch_count = 0
    fail_count = 0

    print(f"\n{'상품코드':<16} {'DB상태':<10} {'BGF상태':<10} {'일치여부':<10} {'비고'}")
    print("-" * 76)

    for db_item in db_items:
        item_cd = db_item.get('item_cd')
        db_cut = bool(db_item.get('is_cut_item', 0))
        bgf = bgf_results.get(item_cd, {})

        if not bgf.get('success'):
            print(f"{item_cd:<16} {'CUT' if db_cut else '정상':<10} {'조회실패':<10} {'???':<10}")
            fail_count += 1
            continue

        bgf_cut = bgf.get('is_cut_item', False)

        if db_cut == bgf_cut:
            match_text = "일치"
            match_count += 1
        else:
            match_text = "불일치"
            mismatch_count += 1

        note = bgf.get('item_nm', '')
        print(f"{item_cd:<16} {'CUT' if db_cut else '정상':<10} {'CUT' if bgf_cut else '정상':<10} {match_text:<10} {note}")

    print("-" * 76)
    print(f"합계: 일치 {match_count}건, 불일치 {mismatch_count}건, 조회실패 {fail_count}건")

    if mismatch_count > 0:
        print("\n[경고] DB와 BGF 화면의 CUT 상태가 다른 상품이 있습니다!")
        print("       DB를 업데이트하려면 발주 플로우를 다시 실행하세요.")
    elif fail_count == 0:
        print("\n[OK] 모든 CUT 상품이 BGF 화면과 일치합니다.")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="컷상품 검증")
    parser.add_argument("--all", action="store_true", help="최근 조회 상품 전체 확인")
    parser.add_argument("--item", type=str, help="특정 상품코드 확인")
    parser.add_argument("--limit", type=int, default=20, help="--all 시 확인할 상품 수")
    args = parser.parse_args()

    # 1. DB에서 확인 대상 조회
    print("=" * 76)
    print("BGF 컷상품 검증 스크립트")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 76)

    if args.item:
        # 특정 상품
        item_codes = [args.item]
        db_items = [{'item_cd': args.item, 'is_cut_item': '?'}]
        print(f"\n[대상] 특정 상품: {args.item}")
    elif args.all:
        # 최근 전체
        db_items = get_recent_items_from_db(limit=args.limit)
        item_codes = [item['item_cd'] for item in db_items]
        cut_count = sum(1 for item in db_items if item.get('is_cut_item'))
        print(f"\n[대상] 최근 조회 상품 {len(db_items)}개 (DB CUT: {cut_count}개)")
    else:
        # CUT 상품만
        db_items = get_cut_items_from_db()
        if not db_items:
            print("\n[INFO] DB에 CUT 상품이 없습니다.")
            return
        item_codes = [item['item_cd'] for item in db_items]
        print(f"\n[대상] DB CUT 상품 {len(db_items)}개")

    for item in db_items[:10]:
        print(f"  - {item.get('item_cd')} {item.get('item_nm', '')}")

    # 2. BGF 로그인
    print("\n[로그인] BGF 시스템 접속...")
    analyzer = SalesAnalyzer()

    try:
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return

        print("[OK] 로그인 성공")
        analyzer.close_popup()
        time.sleep(2)

        # 3. 실제 화면에서 CUT 확인
        bgf_results = verify_cut_on_bgf(analyzer.driver, item_codes)

        # 4. 비교 리포트
        print_verification_report(db_items, bgf_results)

    except Exception as e:
        print(f"\n[ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()

    finally:
        try:
            input("\n브라우저를 닫으려면 Enter를 누르세요...")
        except EOFError:
            pass
        analyzer.close()


if __name__ == "__main__":
    main()
