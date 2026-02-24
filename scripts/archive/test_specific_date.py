"""
특정 날짜 입고 데이터 수집 테스트
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.receiving_collector import ReceivingCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    """2026-02-05 입고 데이터 수집"""
    logger.info("=" * 60)
    logger.info("2026-02-05 입고 데이터 수집 테스트")
    logger.info("=" * 60)

    analyzer = SalesAnalyzer()

    try:
        # 드라이버 설정
        logger.info("크롬 드라이버 설정 중...")
        analyzer.setup_driver()

        # BGF 사이트 접속
        logger.info("BGF 사이트 접속 중...")
        analyzer.connect()

        # 로그인
        logger.info("로그인 중...")
        if not analyzer.do_login():
            logger.error("로그인 실패")
            return 1

        logger.info("로그인 성공")

        # 입고 데이터 수집
        collector = ReceivingCollector(analyzer.driver)

        # 메뉴 이동
        if not collector.navigate_to_receiving_menu():
            logger.error("메뉴 이동 실패")
            return 1

        # 2026-02-05 데이터 수집
        logger.info("2026-02-05 데이터 수집 중...")
        result = collector.collect_multiple_dates(dates=["20260205"])

        # 결과 출력
        if "20260205" in result:
            data = result["20260205"]
            if data.get('success'):
                stats = data.get('stats', {})
                logger.info("=" * 60)
                logger.info("수집 결과")
                logger.info("=" * 60)
                logger.info(f"총 레코드: {stats.get('total', 0)}건")
                logger.info(f"새로 추가: {stats.get('inserted', 0)}건")
                logger.info(f"업데이트: {stats.get('updated', 0)}건")
                logger.info("=" * 60)
            else:
                logger.error(f"수집 실패: {data.get('error')}")
        else:
            logger.error("결과 없음")

        return 0

    except Exception as e:
        logger.error(f"오류 발생: {e}", exc_info=True)
        return 1

    finally:
        try:
            analyzer.close()
            logger.info("브라우저 종료")
        except Exception as e:
            logger.debug(f"브라우저 종료 실패: {e}")


if __name__ == "__main__":
    exit(main())
