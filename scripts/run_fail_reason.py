"""
발주 실패 사유 조회 실행 스크립트

사용법:
    # 오늘 실패 상품 일괄 조회
    python scripts/run_fail_reason.py

    # 특정 날짜
    python scripts/run_fail_reason.py --date 2026-02-03

    # 단일 상품 조회
    python scripts/run_fail_reason.py --item 8801234567890

    # DB 조회만 (브라우저 없이)
    python scripts/run_fail_reason.py --db-only
"""

import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import get_logger
from src.infrastructure.database.repos import FailReasonRepository
from src.settings.constants import LOG_SEPARATOR_WIDE, DEFAULT_STORE_ID

logger = get_logger(__name__)


def print_fail_reasons(eval_date: str, store_id: str = DEFAULT_STORE_ID) -> None:
    """DB에 저장된 실패 사유 출력"""
    repo = FailReasonRepository(store_id=store_id)
    items = repo.get_fail_reasons_by_date(eval_date)

    print(f"\n{LOG_SEPARATOR_WIDE}")
    print(f"  발주 실패 사유 ({eval_date})")
    print(LOG_SEPARATOR_WIDE)

    if not items:
        print("  저장된 실패 사유 없음")
        print(LOG_SEPARATOR_WIDE)
        return

    print(f"  {'상품코드':<15} {'상품명':<20} {'정지사유':<15} {'발주상태':<10}")
    print(f"  {'-'*15} {'-'*20} {'-'*15} {'-'*10}")

    for item in items:
        print(
            f"  {item.get('item_cd', ''):<15} "
            f"{(item.get('item_nm') or '-'):<20} "
            f"{(item.get('stop_reason') or '-'):<15} "
            f"{(item.get('orderable_status') or '-'):<10}"
        )

    print(f"\n  총 {len(items)}건")
    print(LOG_SEPARATOR_WIDE)


def run_collect(eval_date: str, item_cd: str = None, max_items: int = 0) -> None:
    """브라우저로 실패 사유 수집"""
    from src.sales_analyzer import SalesAnalyzer
    from src.collectors.fail_reason_collector import FailReasonCollector

    import time

    print(f"\n브라우저 시작...")
    analyzer = SalesAnalyzer()

    try:
        analyzer.setup_driver()
        analyzer.connect()

        if not analyzer.do_login():
            print("로그인 실패")
            return

        print("로그인 완료")
        analyzer.close_popup()
        time.sleep(2)

        collector = FailReasonCollector(driver=analyzer.driver)

        if item_cd:
            # 단일 상품 조회
            print(f"\n상품 조회: {item_cd}")
            result = collector.lookup_stop_reason(item_cd)
            if result:
                print(f"  정지사유: {result.get('stop_reason', '-')}")
                print(f"  발주상태: {result.get('orderable_status', '-')}")
                print(f"  발주요일: {result.get('orderable_day', '-')}")
                print(f"  상품명:   {result.get('item_nm', '-')}")

                # DB에도 저장
                repo = FailReasonRepository(store_id=DEFAULT_STORE_ID)
                repo.save_fail_reason(
                    eval_date=eval_date,
                    item_cd=item_cd,
                    item_nm=result.get("item_nm"),
                    stop_reason=result.get("stop_reason"),
                    orderable_status=result.get("orderable_status"),
                    orderable_day=result.get("orderable_day"),
                )
                print("  DB 저장 완료")
            else:
                print("  조회 실패")
        else:
            # 전체 일괄 조회
            result = collector.collect_all(eval_date, max_items=max_items)
            print(f"\n조회 결과: {result['success']}성공 / {result['failed']}실패")
            print(f"DB 저장: {result['checked']}건")

    except Exception:
        logger.exception("실패 사유 수집 오류")
        print("\n오류 발생 (로그 확인)")
    finally:
        try:
            analyzer.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description='발주 실패 사유 조회')
    parser.add_argument('--date', type=str, default=None,
                        help='평가일 (YYYY-MM-DD, 기본: 오늘)')
    parser.add_argument('--item', type=str, default=None,
                        help='단일 상품코드 조회')
    parser.add_argument('--max-items', type=int, default=0,
                        help='최대 조회 건수 (0=전체)')
    parser.add_argument('--db-only', action='store_true',
                        help='DB 조회만 (브라우저 없이)')
    args = parser.parse_args()

    eval_date = args.date or datetime.now().strftime("%Y-%m-%d")

    if args.db_only:
        print_fail_reasons(eval_date)
    else:
        run_collect(eval_date, args.item, max_items=args.max_items)
        print_fail_reasons(eval_date)


if __name__ == "__main__":
    main()
