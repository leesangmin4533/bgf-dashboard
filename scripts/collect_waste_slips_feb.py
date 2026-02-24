"""
2월 1일~18일 폐기전표 수집 스크립트
두 매장(46513, 46704) 병렬 실행
"""
import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.collectors.sales_collector import SalesCollector
from src.collectors.waste_slip_collector import WasteSlipCollector


FROM_DATE = "20260201"
TO_DATE = "20260218"

STORE_IDS = ["46513", "46704"]


def collect_for_store(store_id: str) -> dict:
    """매장별 폐기전표 수집"""
    print(f"[{store_id}] 수집 시작: {FROM_DATE}~{TO_DATE}")

    try:
        # 1) 로그인
        print(f"[{store_id}] BGF 사이트 로그인 중...")
        collector = SalesCollector(store_id=store_id)
        collector._ensure_login()
        driver = collector.get_driver()

        if not driver:
            print(f"[{store_id}] ERROR: 드라이버 획득 실패")
            return {"store_id": store_id, "success": False, "error": "driver failed"}

        print(f"[{store_id}] 로그인 성공")

        # 2) 폐기전표 수집
        ws_collector = WasteSlipCollector(driver=driver, store_id=store_id)
        result = ws_collector.collect_waste_slips(
            from_date=FROM_DATE,
            to_date=TO_DATE,
            save_to_db=True,
        )

        print(f"[{store_id}] 수집 완료: {result}")
        result["store_id"] = store_id

        # 3) 브라우저 정리
        try:
            driver.quit()
        except Exception:
            pass

        return result

    except Exception as e:
        print(f"[{store_id}] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"store_id": store_id, "success": False, "error": str(e)}


def main():
    print(f"=== 폐기전표 수집: {FROM_DATE}~{TO_DATE} ===")
    print(f"대상 매장: {STORE_IDS}")
    print()

    threads = []
    results = {}

    for store_id in STORE_IDS:
        t = threading.Thread(
            target=lambda sid: results.__setitem__(sid, collect_for_store(sid)),
            args=(store_id,),
            name=f"waste-{store_id}",
        )
        threads.append(t)
        t.start()
        time.sleep(2)  # 브라우저 시작 간격

    # 완료 대기
    for t in threads:
        t.join(timeout=600)  # 최대 10분

    print()
    print("=" * 60)
    print("=== 수집 결과 ===")
    print("=" * 60)
    for store_id, result in results.items():
        success = result.get("success", False)
        count = result.get("count", 0)
        detail = result.get("detail_count", 0)
        saved = result.get("detail_saved", 0)
        print(f"  [{store_id}] success={success}, "
              f"전표={count}건, 품목={detail}건, 저장={saved}건")
        if result.get("date_summary"):
            for date_str, info in sorted(result["date_summary"].items()):
                print(f"    {date_str}: 전표={info.get('slip_count', 0)}, "
                      f"품목={info.get('item_count', 0)}, "
                      f"원가={info.get('wonga_total', 0):,.0f}")


if __name__ == "__main__":
    main()
