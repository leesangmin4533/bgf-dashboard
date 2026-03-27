"""
Direct Sales API 실제 운영 테스트

로그인 → STMB011 이동 → Direct API로 매출 수집 → Selenium 결과와 비교

사용법:
    python scripts/test_direct_sales_live.py --date 20260226
"""

import sys
import io
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta

if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.sales_analyzer import SalesAnalyzer
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Direct Sales API 실제 테스트")
    parser.add_argument('--date', '-d', type=str, default=None, help='조회 날짜 (YYYYMMDD)')
    args = parser.parse_args()

    target_date = args.date
    if not target_date:
        yesterday = datetime.now() - timedelta(days=1)
        target_date = yesterday.strftime('%Y%m%d')

    print("=" * 70)
    print(f"Direct Sales API 실제 테스트 - {target_date}")
    print("=" * 70)

    analyzer = None
    try:
        # 1. 로그인
        print("\n[1/3] 로그인...")
        analyzer = SalesAnalyzer()
        analyzer.setup_driver()
        analyzer.connect()
        if not analyzer.do_login():
            print("[ERROR] 로그인 실패")
            return
        print("[OK] 로그인 성공")

        # 2. 메뉴 이동
        print("\n[2/3] 매출분석 메뉴 이동...")
        if not analyzer.navigate_to_sales_menu():
            print("[ERROR] 메뉴 이동 실패")
            return
        print("[OK] STMB011_M0 화면 로딩됨")
        time.sleep(1)

        # 3. collect_all_mid_category_data (Direct API 자동 시도)
        print(f"\n[3/3] 매출 데이터 수집 (Direct API 우선)...")
        start = time.time()
        data = analyzer.collect_all_mid_category_data(target_date)
        elapsed = time.time() - start

        print(f"\n{'=' * 70}")
        print(f" 수집 결과")
        print(f"{'=' * 70}")
        print(f"  총 상품: {len(data)}개")
        print(f"  소요시간: {elapsed:.1f}초")

        if data:
            # 중분류별 요약
            mid_summary = {}
            for item in data:
                mid_cd = item.get('MID_CD', '?')
                mid_nm = item.get('MID_NM', '?')
                key = f"[{mid_cd}] {mid_nm}"
                if key not in mid_summary:
                    mid_summary[key] = {'count': 0, 'sale_qty': 0}
                mid_summary[key]['count'] += 1
                mid_summary[key]['sale_qty'] += item.get('SALE_QTY', 0)

            print(f"\n  중분류별 요약 ({len(mid_summary)}개):")
            for key, v in sorted(mid_summary.items()):
                print(f"    {key}: {v['count']}상품, 판매 {v['sale_qty']}개")

            # 처음 5개 상품 샘플
            print(f"\n  상품 샘플 (처음 5개):")
            for item in data[:5]:
                print(f"    {item.get('ITEM_CD', '')} | {item.get('ITEM_NM', '')} "
                      f"| 판매:{item.get('SALE_QTY', 0)} "
                      f"| 발주:{item.get('ORD_QTY', 0)} "
                      f"| 입고:{item.get('BUY_QTY', 0)} "
                      f"| 폐기:{item.get('DISUSE_QTY', 0)} "
                      f"| 재고:{item.get('STOCK_QTY', 0)}")

        print(f"\n{'=' * 70}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if analyzer and analyzer.driver:
            analyzer.driver.quit()


if __name__ == "__main__":
    main()
