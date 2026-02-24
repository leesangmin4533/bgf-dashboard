"""
자동/스마트 발주 목록 수집 E2E 테스트

실제 BGF 사이트 로그인 → 발주현황조회 → 자동/스마트 탭 수집 → DB 저장 → API 확인
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.order_status_collector import OrderStatusCollector
from src.db.repository import AutoOrderItemRepository, SmartOrderItemRepository


def main():
    print("=" * 60)
    print("자동/스마트 발주 목록 수집 테스트")
    print("=" * 60)

    # 1. 드라이버 설정 + 로그인
    print("\n[1/5] BGF 사이트 로그인...")
    analyzer = SalesAnalyzer()
    analyzer.setup_driver()
    analyzer.connect()

    if not analyzer.do_login():
        print("로그인 실패!")
        analyzer.driver.quit()
        return

    print("로그인 성공!")
    time.sleep(2)

    # 2. 발주현황조회 메뉴 이동
    print("\n[2/5] 발주현황조회 메뉴 이동...")
    collector = OrderStatusCollector(analyzer.driver)

    if not collector.navigate_to_order_status_menu():
        print("메뉴 이동 실패!")
        analyzer.driver.quit()
        return

    print("메뉴 이동 성공!")
    time.sleep(1)

    # 3. 자동발주 상품 수집
    print("\n[3/5] 자동발주 상품 수집...")
    auto_detail = collector.collect_auto_order_items_detail()
    print(f"  수집 결과: {len(auto_detail)}건")
    if auto_detail:
        for i, item in enumerate(auto_detail[:5]):
            print(f"  [{i+1}] {item['item_cd']} | {item['item_nm']} | 중분류: {item['mid_cd']}")
        if len(auto_detail) > 5:
            print(f"  ... 외 {len(auto_detail) - 5}건")

    # DB 캐시 갱신
    auto_repo = AutoOrderItemRepository()
    saved = auto_repo.refresh(auto_detail)
    print(f"  DB 저장: {saved}건")

    # 4. 스마트발주 상품 수집
    print("\n[4/5] 스마트발주 상품 수집...")
    smart_detail = collector.collect_smart_order_items_detail()
    print(f"  수집 결과: {len(smart_detail)}건")
    if smart_detail:
        for i, item in enumerate(smart_detail[:5]):
            print(f"  [{i+1}] {item['item_cd']} | {item['item_nm']} | 중분류: {item['mid_cd']}")
        if len(smart_detail) > 5:
            print(f"  ... 외 {len(smart_detail) - 5}건")

    smart_repo = SmartOrderItemRepository()
    saved = smart_repo.refresh(smart_detail)
    print(f"  DB 저장: {saved}건")

    # 메뉴 닫기
    collector.close_menu()

    # 5. API 응답 검증 (DB에서 직접 읽기)
    print("\n[5/5] DB → API 응답 검증...")
    auto_from_db = auto_repo.get_all_detail()
    smart_from_db = smart_repo.get_all_detail()

    print(f"\n  자동발주 DB 캐시: {len(auto_from_db)}건")
    print(f"  스마트발주 DB 캐시: {len(smart_from_db)}건")

    # 수집 결과와 DB 일치 확인
    auto_ok = len(auto_from_db) == len(auto_detail)
    smart_ok = len(smart_from_db) == len(smart_detail)

    if auto_detail:
        site_codes = {item["item_cd"] for item in auto_detail}
        db_codes = {item["item_cd"] for item in auto_from_db}
        auto_ok = site_codes == db_codes
        if not auto_ok:
            print(f"  [불일치] 사이트: {len(site_codes)}건 vs DB: {len(db_codes)}건")
            diff = site_codes - db_codes
            if diff:
                print(f"    DB 누락: {diff}")

    if smart_detail:
        site_codes = {item["item_cd"] for item in smart_detail}
        db_codes = {item["item_cd"] for item in smart_from_db}
        smart_ok = site_codes == db_codes
        if not smart_ok:
            print(f"  [불일치] 사이트: {len(site_codes)}건 vs DB: {len(db_codes)}건")

    # 상세 필드 검증
    fields_ok = True
    if auto_from_db:
        item = auto_from_db[0]
        required = ["item_cd", "item_nm", "mid_cd"]
        missing = [f for f in required if f not in item or not item[f]]
        if missing:
            print(f"  [경고] 자동발주 첫 번째 항목 누락 필드: {missing}")
            fields_ok = False
        else:
            print(f"  자동발주 필드 검증 OK: item_cd={item['item_cd']}, item_nm={item['item_nm']}, mid_cd={item['mid_cd']}")

    if smart_from_db:
        item = smart_from_db[0]
        required = ["item_cd", "item_nm", "mid_cd"]
        missing = [f for f in required if f not in item or not item[f]]
        if missing:
            print(f"  [경고] 스마트발주 첫 번째 항목 누락 필드: {missing}")
            fields_ok = False
        else:
            print(f"  스마트발주 필드 검증 OK: item_cd={item['item_cd']}, item_nm={item['item_nm']}, mid_cd={item['mid_cd']}")

    # 결과 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    print(f"  자동발주 수집: {len(auto_detail)}건 → DB 저장 {'OK' if auto_ok else 'FAIL'}")
    print(f"  스마트발주 수집: {len(smart_detail)}건 → DB 저장 {'OK' if smart_ok else 'FAIL'}")
    print(f"  상세 필드 (item_cd/item_nm/mid_cd): {'OK' if fields_ok else 'FAIL'}")
    print(f"  전체: {'PASS' if (auto_ok and smart_ok and fields_ok) else 'FAIL'}")

    # 드라이버 종료
    analyzer.driver.quit()
    print("\n드라이버 종료 완료")


if __name__ == "__main__":
    main()
