"""
잘못된 배치 데이터 정리 스크립트

푸드류가 inventory_batches에 잘못 들어간 경우 정리
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.repository import BaseRepository
from src.config.constants import FOOD_CATEGORIES
from src.utils.logger import get_logger

logger = get_logger(__name__)


def cleanup_misplaced_batches() -> None:
    """푸드류가 inventory_batches에 잘못 들어간 데이터 확인 및 정리"""

    repo = BaseRepository()
    conn = repo._get_conn()
    cursor = conn.cursor()

    try:
        # 푸드류가 inventory_batches에 있는지 확인
        cursor.execute("""
            SELECT id, item_cd, item_nm, mid_cd, receiving_date, expiration_days
            FROM inventory_batches
            WHERE mid_cd IN ({})
            ORDER BY receiving_date DESC
        """.format(','.join('?' * len(FOOD_CATEGORIES))), FOOD_CATEGORIES)

        misplaced = cursor.fetchall()

        if not misplaced:
            logger.info("✓ 잘못 분류된 배치 없음")
            return

        logger.warning(f"⚠ 잘못 분류된 배치 발견: {len(misplaced)}건")

        for row in misplaced:
            logger.warning(
                f"  - ID {row[0]}: {row[2]} ({row[3]}) | "
                f"입고: {row[4]} | 유통기한: {row[5]}일"
            )

        # 사용자 확인
        response = input(f"\n{len(misplaced)}건의 잘못된 배치를 삭제하시겠습니까? (yes/no): ")

        if response.lower() == 'yes':
            cursor.execute("""
                DELETE FROM inventory_batches
                WHERE mid_cd IN ({})
            """.format(','.join('?' * len(FOOD_CATEGORIES))), FOOD_CATEGORIES)

            conn.commit()
            logger.info(f"✓ {len(misplaced)}건 삭제 완료")
        else:
            logger.info("취소됨")

    finally:
        conn.close()


def check_old_order_tracking() -> None:
    """오래된 order_tracking 데이터 확인"""

    repo = BaseRepository()
    conn = repo._get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT COUNT(*) as cnt,
                   MIN(order_date) as oldest,
                   MAX(order_date) as newest
            FROM order_tracking
        """)

        row = cursor.fetchone()

        if row and row[0] > 0:
            logger.info(f"\norder_tracking 통계:")
            logger.info(f"  - 총 {row[0]}건")
            logger.info(f"  - 가장 오래된 발주: {row[1]}")
            logger.info(f"  - 가장 최근 발주: {row[2]}")
        else:
            logger.info("\norder_tracking 데이터 없음")

    finally:
        conn.close()


def main():
    """메인 실행"""
    logger.info("배치 데이터 정리 시작\n")

    cleanup_misplaced_batches()
    check_old_order_tracking()

    logger.info("\n정리 완료")


if __name__ == "__main__":
    main()
