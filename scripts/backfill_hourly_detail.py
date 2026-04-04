"""
시간대별 매출 상세(품목별) 과거 데이터 백필 스크립트

BGF selPrdT3 Direct API를 사용하여 과거 날짜의 시간대별 품목 판매 데이터를 수집합니다.

구조:
  BGF 로그인 -> STMB010 진입 -> selPrdT3 템플릿 캡처(또는 세션변수 폴백)
  -> 날짜별 24시간 순회 -> hourly_sales_detail 테이블 저장

사용:
    # 기본 6개월 백필
    python scripts/backfill_hourly_detail.py --store 46513

    # 30일만 백필
    python scripts/backfill_hourly_detail.py --store 49965 --days 30

    # 특정 기간 지정
    python scripts/backfill_hourly_detail.py --store 46513 --start-date 2026-01-01 --end-date 2026-01-31

    # 수집 후 소진율 곡선 bootstrap 포함
    python scripts/backfill_hourly_detail.py --store 49965 --days 60 --bootstrap

    # 현재 상태만 확인
    python scripts/backfill_hourly_detail.py --store 49965 --status

    # bootstrap만 실행 (수집 건너뛰기)
    python scripts/backfill_hourly_detail.py --store 49965 --bootstrap-only

API 참조:
  - 엔드포인트: /stmb010/selPrdT3
  - 요청: SSV body (strYmd=YYYYMMDD, strTime=HH, strStoreCd=매장코드)
  - 응답: Dataset:dsList (ITEM_CD, ITEM_NM, SALE_QTY, RECT_AMT, RATE, ...)
  - 하루 약 15초 소요, 60일 = 약 15분

트러블슈팅:
  - "API응답 성공이지만 품목 0건":
    strStoreCd가 빈 값 -> 쿠키 SS_STORE_CD 우선 사용으로 해결 (2026-04-04 수정)
  - "selPrdT3 캡처 0건":
    팝업 열기 실패. 폴백(세션변수 직접 구성)이 자동 동작
"""

import argparse
import sys
import os

# 프로젝트 루트 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _print_status(store_id: str):
    """수집 현황 출력"""
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'stores', f'{store_id}.db')
    if not os.path.exists(db_path):
        logger.info(f"  DB 없음: {db_path}")
        return
    conn = sqlite3.connect(db_path)
    try:
        r1 = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT sales_date), "
            "MIN(sales_date), MAX(sales_date) FROM hourly_sales_detail"
        ).fetchone()
        logger.info(
            f"[Status] hourly_sales_detail: {r1[0]}행, "
            f"{r1[1]}일 ({r1[2]} ~ {r1[3]})"
        )
        try:
            r2 = conn.execute("""
                SELECT COUNT(DISTINCT item_cd), MAX(sample_count),
                       ROUND(AVG(sample_count), 1),
                       SUM(CASE WHEN sample_count >= 20 THEN 1 ELSE 0 END)
                FROM (SELECT item_cd, MAX(sample_count) as sample_count
                      FROM food_popularity_curve GROUP BY item_cd)
            """).fetchone()
            logger.info(
                f"[Status] food_popularity_curve: {r2[0]}상품, "
                f"max_sample={r2[1]}, avg={r2[2]}, "
                f"sample>=20={r2[3]}개"
            )
        except Exception:
            logger.info("[Status] food_popularity_curve: 테이블 없음")
    finally:
        conn.close()


def _run_bootstrap(store_id: str, lookback_days: int = 90):
    """소진율 곡선 bootstrap"""
    logger.info(f"[Bootstrap] 소진율 곡선 초기화 시작 (store={store_id})...")
    from src.application.services.food_depletion_service import FoodDepletionService
    service = FoodDepletionService(store_id=store_id)
    result = service.bootstrap_from_history(lookback_days=lookback_days)
    logger.info(f"[Bootstrap] 완료: {result}")
    return result


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
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="수집 후 소진율 곡선 bootstrap 실행"
    )
    parser.add_argument(
        "--bootstrap-only", action="store_true",
        help="수집 건너뛰고 bootstrap만 실행"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="현재 수집 상태만 확인"
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

    # --status: 현재 상태만 확인
    if args.status:
        _print_status(store_id)
        return

    # --bootstrap-only: 수집 건너뛰고 bootstrap만
    if args.bootstrap_only:
        _run_bootstrap(store_id, lookback_days=max(args.days, 90))
        _print_status(store_id)
        return

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

        # --bootstrap: 수집 후 소진율 곡선 bootstrap
        if args.bootstrap:
            _run_bootstrap(store_id, lookback_days=max(args.days, 90))

        _print_status(store_id)

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
