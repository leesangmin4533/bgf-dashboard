"""
실패사유 수집기 라이브 테스트
- BGF 사이트 로그인 후 반복 실패 상품 목록의 정지사유를 실제 조회
- _wait_for_data_loaded() 패치 효과 검증
"""

import sys
import io
import os
import sqlite3
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 프로젝트 루트
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src.sales_analyzer import SalesAnalyzer
from src.collectors.fail_reason_collector import FailReasonCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_test_items(store_id: str, min_count: int = 3, limit: int = 20):
    """반복 실패 상품 목록 조회"""
    db_path = ROOT / f"data/stores/{store_id}.db"
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute('''
        SELECT item_cd, item_nm, stop_reason, COUNT(*) as cnt
        FROM order_fail_reasons
        GROUP BY item_cd
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ?
    ''', (min_count, limit)).fetchall()
    conn.close()
    return rows


def main():
    store_id = "46513"

    # 1. 테스트 대상 상품 조회
    items = get_test_items(store_id, min_count=3, limit=20)
    print(f"\n=== 테스트 대상: {len(items)}개 상품 (매장 {store_id}) ===\n")

    # 2. BGF 사이트 로그인
    print("[1/3] BGF 사이트 로그인...")
    analyzer = SalesAnalyzer(store_id=store_id)
    analyzer.setup_driver()
    analyzer.connect()  # BASE_URL 접속 + 넥사크로 로딩 대기

    try:
        if not analyzer.do_login():
            print("로그인 실패!")
            return

        print("로그인 성공!\n")

        # 3. 홈 프레임 이동 대기
        time.sleep(3)

        # 4. FailReasonCollector 초기화
        collector = FailReasonCollector(
            driver=analyzer.driver,
            store_id=store_id
        )

        # 5. 각 상품 조회
        print("[2/3] 상품별 정지사유 조회 시작...\n")
        print(f"{'#':>3}  {'상품코드':<16} {'기존사유':<15} {'새로조회':<15} {'상태':<8} {'상품명'}")
        print("-" * 90)

        results = []
        success = 0
        improved = 0

        for idx, (item_cd, item_nm, old_reason, cnt) in enumerate(items):
            old_reason = old_reason or "(없음)"

            try:
                result = collector.lookup_stop_reason(item_cd)

                if result:
                    new_reason = result.get("stop_reason") or "(빈값)"
                    new_status = result.get("orderable_status") or "-"

                    # 개선 여부 판단
                    if old_reason == "알수없음" and new_reason != "알수없음" and new_reason != "(빈값)":
                        status = "FIXED"
                        improved += 1
                    elif new_reason == "알수없음" or new_reason == "(빈값)":
                        status = "STILL"
                    else:
                        status = "OK"

                    success += 1
                else:
                    new_reason = "조회실패"
                    new_status = "-"
                    status = "FAIL"

                nm = (item_nm or "")[:20]
                print(f"{idx+1:>3}  {item_cd:<16} {old_reason:<15} {new_reason:<15} {status:<8} {nm}")

                results.append({
                    "item_cd": item_cd,
                    "old_reason": old_reason,
                    "new_reason": new_reason,
                    "status": status,
                })

            except Exception as e:
                print(f"{idx+1:>3}  {item_cd:<16} {old_reason:<15} {'ERROR':<15} {'ERR':<8} {str(e)[:30]}")

            # 상품 간 대기
            time.sleep(1.0)

        # 6. 결과 요약
        print(f"\n[3/3] 결과 요약")
        print(f"  조회 성공: {success}/{len(items)}")
        print(f"  개선됨 (알수없음 -> 실제사유): {improved}")
        still_unknown = sum(1 for r in results if r["status"] == "STILL")
        print(f"  여전히 알수없음: {still_unknown}")
        print(f"  기존 정상: {sum(1 for r in results if r['status'] == 'OK')}")
        print(f"  조회 실패: {sum(1 for r in results if r['status'] == 'FAIL')}")

    finally:
        try:
            analyzer.driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
