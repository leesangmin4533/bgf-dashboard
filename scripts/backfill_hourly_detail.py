"""
시간대별 매출 상세(품목별) 과거 데이터 백필 스크립트

사용:
    # 기본 6개월 백필
    python scripts/backfill_hourly_detail.py

    # 30일만 백필
    python scripts/backfill_hourly_detail.py --days 30

    # 특정 시작일부터
    python scripts/backfill_hourly_detail.py --start-date 2025-12-01

    # 특정 기간 지정 (시작일 ~ 종료일)
    python scripts/backfill_hourly_detail.py --start-date 2026-01-01 --end-date 2026-01-31

    # 특정 매장
    python scripts/backfill_hourly_detail.py --store 46513 --days 90
"""

import argparse
import sys
import os

# 프로젝트 루트 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="시간대별 매출 상세 과거 데이터 백필"
    )
    parser.add_argument(
        "--days", type=int, default=180,
        help="백필 기간 (기본: 180일 = 6개월)"
    )
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="시작일 (YYYY-MM-DD, 기본: 오늘-days)"
    )
    parser.add_argument(
        "--end-date", type=str, default=None,
        help="종료일 (YYYY-MM-DD, 기본: 어제)"
    )
    parser.add_argument(
        "--store", type=str, default=None,
        help="매장 코드 (기본: 기본 매장)"
    )
    parser.add_argument(
        "--delay-hour", type=float, default=0.3,
        help="시간대 간 딜레이 초 (기본: 0.3)"
    )
    parser.add_argument(
        "--delay-day", type=float, default=2.0,
        help="날짜 간 딜레이 초 (기본: 2.0)"
    )
    args = parser.parse_args()

    # DB 초기화
    from src.infrastructure.database.schema import init_db
    init_db()

    # 매장 ID
    if args.store:
        store_id = args.store
    else:
        from config import DEFAULT_STORE
        store_id = DEFAULT_STORE.get("store_id", "46513")

    range_desc = f"Days: {args.days}"
    if args.start_date:
        range_desc = f"Start: {args.start_date}"
    if args.end_date:
        range_desc += f", End: {args.end_date}"
    logger.info(f"[Backfill] Store: {store_id}, {range_desc}")
    logger.info(f"[Backfill] Delay: {args.delay_hour}s/hour, {args.delay_day}s/day")

    # 드라이버 준비 (로그인 필요)
    from src.sales_analyzer import SalesAnalyzer
    analyzer = SalesAnalyzer(store_id=store_id)

    try:
        logger.info("[Backfill] BGF 사이트 로그인 중...")
        analyzer.setup_driver()
        analyzer.connect()
        if not analyzer.do_login():
            logger.error("[Backfill] 로그인 실패")
            return
        driver = analyzer.driver

        if not driver:
            logger.error("[Backfill] 드라이버 연결 실패")
            return

        # 로그인 후 팝업 닫기 + 메인 페이지 로딩 대기
        import time as _time
        _time.sleep(2)
        analyzer.close_popup()
        _time.sleep(1)
        logger.info("[Backfill] 메인 페이지 로딩 대기...")
        for _wait_i in range(20):
            try:
                _ready = driver.execute_script("""
                    try {
                        var app = nexacro.getApplication();
                        var top = app.mainframe.HFrameSet00.VFrameSet00.TopFrame;
                        return top && top.form ? true : false;
                    } catch(e) { return false; }
                """)
                if _ready:
                    logger.info("[Backfill] 메인 페이지 로딩 완료")
                    break
            except Exception:
                pass
            _time.sleep(0.5)
        else:
            logger.warning("[Backfill] 메인 페이지 로딩 타임아웃 — 계속 진행")

        # 백필 실행
        from src.application.services.hourly_detail_service import (
            HourlyDetailService,
        )
        service = HourlyDetailService(store_id=store_id, driver=driver)

        logger.info(f"[Backfill] 백필 시작: {args.days}일")
        stats = service.backfill(
            days=args.days,
            driver=driver,
            start_date=args.start_date,
            end_date=args.end_date,
        )

        logger.info("=" * 60)
        logger.info(f"[Backfill] 완료!")
        logger.info(f"  - 처리 일수: {stats['total_days']}")
        logger.info(f"  - 성공 일수: {stats['success_days']}")
        logger.info(f"  - 총 품목 수: {stats['total_items']}")
        logger.info("=" * 60)

    except KeyboardInterrupt:
        logger.info("[Backfill] 사용자 중단")
    except Exception as e:
        logger.error(f"[Backfill] 백필 실패: {e}")
        raise
    finally:
        try:
            analyzer.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
