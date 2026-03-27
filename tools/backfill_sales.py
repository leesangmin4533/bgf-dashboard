"""
신규 점포 판매 데이터 백필 스크립트
====================================
중분류별 매출 구성비(STMB011) Direct API를 이용하여
지정 기간의 판매 데이터를 일괄 수집 → DB 저장합니다.

사용법:
    cd bgf_auto

    # 최근 60일 수집
    python tools/backfill_sales.py --store_id 47863 --days 60

    # 특정 기간 수집
    python tools/backfill_sales.py --store_id 47863 --from 2026-01-01 --to 2026-03-04

    # dry-run (수집만 하고 저장 안 함)
    python tools/backfill_sales.py --store_id 47863 --days 30 --dry-run

    # 기존 데이터가 있는 날짜 건너뛰기
    python tools/backfill_sales.py --store_id 47863 --days 90 --skip-existing
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src.utils.logger import get_logger
from src.settings.constants import DEFAULT_STORE_ID

logger = get_logger("backfill_sales")

# ─────────────────────────────────────────────
# 날짜 유틸
# ─────────────────────────────────────────────

def generate_date_range(start_date: str, end_date: str) -> list:
    """YYYY-MM-DD 형식의 날짜 리스트 생성 (최신→과거 순)"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    current = end
    while current >= start:
        dates.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return dates


def get_existing_dates(store_id: str) -> set:
    """이미 수집된 날짜 목록 조회"""
    from src.infrastructure.database.connection import DBRouter
    try:
        conn = DBRouter.get_store_connection(store_id)
        rows = conn.execute(
            "SELECT DISTINCT sales_date FROM daily_sales"
        ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


# ─────────────────────────────────────────────
# 수집 엔진
# ─────────────────────────────────────────────

class SalesBackfiller:
    """Direct API 기반 판매 데이터 백필러"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.analyzer = None
        self.fetcher = None
        self._logged_in = False

    def login(self) -> bool:
        """Selenium 로그인 + Direct API 준비"""
        from src.sales_analyzer import SalesAnalyzer
        from src.collectors.direct_sales_fetcher import DirectSalesFetcher

        logger.info(f"[{self.store_id}] Selenium 로그인 시작...")
        self.analyzer = SalesAnalyzer(store_id=self.store_id)
        self.analyzer.setup_driver()
        self.analyzer.connect()

        if not self.analyzer.do_login():
            logger.error("로그인 실패")
            return False

        self.analyzer.close_popup()
        logger.info("로그인 성공")

        # 매출분석 메뉴 진입 (템플릿 캡처용)
        logger.info("매출분석 메뉴 진입...")
        if not self.analyzer.navigate_to_sales_menu():
            logger.error("매출분석 메뉴 이동 실패")
            return False

        # Direct API fetcher 초기화
        self.fetcher = DirectSalesFetcher(
            self.analyzer.driver, concurrency=5, timeout_ms=8000
        )

        # 인터셉터 설치 + 한번 조회해서 템플릿 캡처
        self.fetcher.install_interceptor()
        time.sleep(1)

        # 오늘 날짜로 한번 조회 (넥사크로 UI 트리거)
        today = datetime.now().strftime("%Y%m%d")
        self.analyzer.collect_all_mid_category_data(today)
        time.sleep(1)

        # 인터셉터에서 템플릿 캡처
        self.fetcher.capture_templates_from_interceptor()

        # selDetailSearch 템플릿이 없으면 selSearch에서 빌드 (항상 체크)
        if self.fetcher._search_template and not self.fetcher._detail_template:
            logger.info("selDetailSearch 템플릿을 selSearch에서 빌드")
            self.fetcher._detail_template = (
                self.fetcher._build_detail_template_from_search(
                    self.fetcher._search_template
                )
            )

        if not self.fetcher._search_template:
            logger.error("selSearch 템플릿을 확보하지 못했습니다")
            return False

        logger.info("Direct API 준비 완료")
        self._logged_in = True
        return True

    def collect_date(self, date_str: str) -> list:
        """단일 날짜 수집 (Direct API)

        Args:
            date_str: YYYY-MM-DD 형식

        Returns:
            [{ITEM_CD, ITEM_NM, MID_CD, MID_NM, SALE_QTY, ORD_QTY, BUY_QTY, DISUSE_QTY, STOCK_QTY}, ...]
        """
        if not self._logged_in:
            raise RuntimeError("login() 먼저 호출하세요")

        date_yyyymmdd = date_str.replace("-", "")
        items = self.fetcher.collect_all(date_yyyymmdd)
        return items or []

    def save_date(self, date_str: str, data: list) -> dict:
        """단일 날짜 데이터 DB 저장"""
        from src.infrastructure.database.repos.sales_repo import SalesRepository

        repo = SalesRepository(store_id=self.store_id)
        stats = repo.save_daily_sales(
            sales_data=data,
            sales_date=date_str,
            store_id=self.store_id,
            enable_validation=False,
        )
        return stats

    def close(self):
        """브라우저 종료"""
        if self.analyzer and self.analyzer.driver:
            try:
                self.analyzer.driver.quit()
            except Exception:
                pass


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="신규 점포 판매 데이터 백필")
    parser.add_argument("--store_id", required=True, help="점포 코드")
    parser.add_argument("--days", type=int, default=0, help="최근 N일 수집")
    parser.add_argument("--from", dest="from_date", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="수집만 하고 저장 안 함")
    parser.add_argument("--skip-existing", action="store_true", help="이미 수집된 날짜 건너뛰기")
    parser.add_argument("--delay", type=float, default=1.0, help="날짜 간 딜레이(초)")
    args = parser.parse_args()

    # 날짜 범위 결정
    if args.days > 0:
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    elif args.from_date and args.to_date:
        start_date = args.from_date
        end_date = args.to_date
    else:
        print("--days N 또는 --from/--to 를 지정하세요")
        sys.exit(1)

    dates = generate_date_range(start_date, end_date)

    # 기존 데이터 필터
    if args.skip_existing:
        existing = get_existing_dates(args.store_id)
        before = len(dates)
        dates = [d for d in dates if d not in existing]
        skipped = before - len(dates)
        if skipped > 0:
            logger.info(f"이미 수집된 {skipped}일 건너뜀")

    if not dates:
        print("수집할 날짜가 없습니다.")
        return

    print(f"\n  수집 대상: {args.store_id}")
    print(f"  기간: {dates[-1]} ~ {dates[0]} ({len(dates)}일)")
    print(f"  모드: {'DRY-RUN (저장 안 함)' if args.dry_run else 'LIVE (DB 저장)'}\n")

    backfiller = SalesBackfiller(args.store_id)
    try:
        if not backfiller.login():
            print("로그인 실패. .env에 BGF 인증정보가 설정되어 있는지 확인하세요.")
            sys.exit(1)

        total_items = 0
        success_days = 0
        fail_days = 0

        for i, date_str in enumerate(dates):
            prefix = f"[{i+1}/{len(dates)}] {date_str}"
            try:
                data = backfiller.collect_date(date_str)
                count = len(data)

                if count == 0:
                    logger.warning(f"{prefix} 데이터 없음 (휴무일?)")
                    continue

                if not args.dry_run:
                    stats = backfiller.save_date(date_str, data)
                    logger.info(
                        f"{prefix} {count}건 저장 "
                        f"(new={stats['new']}, updated={stats['updated']})"
                    )
                else:
                    logger.info(f"{prefix} {count}건 수집 (dry-run)")

                total_items += count
                success_days += 1

            except Exception as e:
                logger.error(f"{prefix} 실패: {e}")
                fail_days += 1

            # 서버 부하 방지
            if i < len(dates) - 1:
                time.sleep(args.delay)

        # 결과 요약
        print(f"\n{'='*50}")
        print(f"  백필 완료: {args.store_id}")
        print(f"  성공: {success_days}일, 실패: {fail_days}일")
        print(f"  총 상품 데이터: {total_items}건")
        if not args.dry_run:
            print(f"  저장 위치: data/stores/{args.store_id}.db")
        print(f"{'='*50}\n")

    finally:
        backfiller.close()


if __name__ == "__main__":
    main()
