"""
입고 데이터 수집 실행 스크립트

BGF 사이트에서 입고 데이터만 수집합니다.
"""

import sys
import io

# Windows CP949 콘솔 → UTF-8 래핑 (한글·특수문자 깨짐 방지)
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.receiving_collector import run_receiving_collection
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    """입고 데이터 수집 실행"""
    logger.info("=" * 60)
    logger.info("입고 데이터 수집 시작")
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
        logger.info("BGF 사이트 로그인 중...")
        if not analyzer.do_login():
            logger.error("로그인 실패")
            return 1

        logger.info("로그인 성공")

        # 입고 데이터 수집 (최근 7일)
        logger.info("입고 데이터 수집 중 (최근 7일)...")
        run_receiving_collection(
            driver=analyzer.driver,
            days=7,
            use_all_available=False
        )

        logger.info("=" * 60)
        logger.info("입고 데이터 수집 완료")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.error(f"오류 발생: {e}", exc_info=True)
        return 1

    finally:
        # 브라우저 종료
        try:
            analyzer.close()
            logger.info("브라우저 종료")
        except Exception as e:
            logger.debug(f"브라우저 종료 실패: {e}")


if __name__ == "__main__":
    exit(main())
