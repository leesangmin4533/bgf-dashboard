"""
daily_sales 과거 데이터 백필 스크립트

사용:
    # 47863 매장 3/3~3/4 재수집
    python scripts/backfill_daily_sales.py --store 47863 --start-date 2026-03-03 --end-date 2026-03-04

    # 특정 매장, 최근 7일
    python scripts/backfill_daily_sales.py --store 46513 --days 7

    # 기본 매장, 어제만
    python scripts/backfill_daily_sales.py --days 1
"""

import argparse
import sys
import os
from datetime import datetime, timedelta

# 프로젝트 루트 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="daily_sales 과거 데이터 백필"
    )
    parser.add_argument(
        "--store", type=str, required=True,
        help="매장 코드 (예: 47863)"
    )
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="시작일 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, default=None,
        help="종료일 (YYYY-MM-DD, 기본: 어제)"
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="백필 기간 (--start-date 미지정 시 오늘-days부터)"
    )
    args = parser.parse_args()

    store_id = args.store

    # 날짜 범위 계산
    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    else:
        end_date = datetime.now() - timedelta(days=1)

    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    elif args.days:
        start_date = datetime.now() - timedelta(days=args.days)
    else:
        parser.error("--start-date 또는 --days 중 하나를 지정하세요")
        return

    # 날짜 리스트 생성
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    if not dates:
        logger.error("수집할 날짜가 없습니다")
        return

    logger.info(f"[Backfill] Store: {store_id}")
    logger.info(f"[Backfill] 기간: {dates[0]} ~ {dates[-1]} ({len(dates)}일)")

    # DB 초기화
    from src.infrastructure.database.schema import init_db
    init_db()

    # SalesRepository 준비
    from src.infrastructure.database.repos.sales_repo import SalesRepository
    sales_repo = SalesRepository()

    # 저장 콜백
    def save_callback(date_str, data):
        stats = sales_repo.save_daily_sales(
            sales_data=data,
            sales_date=date_str,
            store_id=store_id,
        )
        return stats

    # SalesCollector로 다중 날짜 수집
    from src.collectors.sales_collector import SalesCollector
    collector = SalesCollector(store_id=store_id)

    try:
        logger.info("[Backfill] 수집 시작...")
        results = collector.collect_multiple_dates(dates, save_callback)

        # 결과 요약
        success_count = sum(1 for r in results.values() if r.get("success"))
        total_items = sum(r.get("item_count", 0) for r in results.values())

        logger.info("=" * 60)
        logger.info(f"[Backfill] 완료!")
        logger.info(f"  - 전체 일수: {len(dates)}")
        logger.info(f"  - 성공 일수: {success_count}")
        logger.info(f"  - 총 품목 수: {total_items}")

        for date_str, result in sorted(results.items()):
            status = "OK" if result.get("success") else "FAIL"
            items = result.get("item_count", 0)
            error = result.get("error", "")
            logger.info(f"  {date_str}: {status} ({items}건) {error}")

        logger.info("=" * 60)

    except KeyboardInterrupt:
        logger.info("[Backfill] 사용자 중단")
    except Exception as e:
        logger.error(f"[Backfill] 백필 실패: {e}")
        raise
    finally:
        collector.close()


if __name__ == "__main__":
    main()
