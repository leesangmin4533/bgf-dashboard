"""
입고 데이터 추적 완정성 테스트 스크립트

이 스크립트는 다음을 확인합니다:
1. receiving_history에는 있지만 order_tracking/inventory_batches에 없는 항목 (누락)
2. products 테이블과 receiving_history의 mid_cd 불일치
3. 유통기한 계산이 정확한지 확인
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


def check_missing_batches() -> None:
    """receiving_history에는 있지만 배치가 생성되지 않은 항목 확인"""
    logger.info("=" * 60)
    logger.info("1. 누락된 배치 확인")
    logger.info("=" * 60)

    repo = BaseRepository()
    conn = repo._get_conn()
    cursor = conn.cursor()

    try:
        # 최근 7일 데이터 확인
        cursor.execute("""
            SELECT
                rh.receiving_date,
                rh.item_cd,
                rh.item_nm,
                rh.mid_cd,
                rh.receiving_qty,
                CASE
                    WHEN ot.id IS NOT NULL THEN 'order_tracking'
                    WHEN ib.id IS NOT NULL THEN 'inventory_batches'
                    ELSE 'MISSING'
                END as batch_status
            FROM receiving_history rh
            LEFT JOIN order_tracking ot
                ON rh.item_cd = ot.item_cd
                AND DATE(rh.receiving_date) = DATE(ot.arrival_time)
            LEFT JOIN inventory_batches ib
                ON rh.item_cd = ib.item_cd
                AND rh.receiving_date = ib.receiving_date
            WHERE rh.receiving_date >= DATE('now', '-7 days')
            ORDER BY rh.receiving_date DESC, rh.item_cd
        """)

        rows = cursor.fetchall()

        total_count = len(rows)
        missing_count = sum(1 for row in rows if row[5] == 'MISSING')
        tracking_count = sum(1 for row in rows if row[5] == 'order_tracking')
        batches_count = sum(1 for row in rows if row[5] == 'inventory_batches')

        logger.info(f"총 입고 건수: {total_count}건")
        logger.info(f"  - order_tracking: {tracking_count}건")
        logger.info(f"  - inventory_batches: {batches_count}건")
        logger.info(f"  - 누락: {missing_count}건")

        if missing_count > 0:
            logger.warning(f"\n누락된 {missing_count}건:")
            for row in rows:
                if row[5] == 'MISSING':
                    logger.warning(
                        f"  - {row[0]} | {row[1]} | {row[2]} | "
                        f"mid_cd={row[3]} | qty={row[4]}"
                    )

        tracking_rate = ((total_count - missing_count) / total_count * 100) if total_count > 0 else 0
        logger.info(f"\n추적율: {tracking_rate:.1f}%")

    finally:
        conn.close()


def check_mid_cd_mismatch() -> None:
    """products 테이블과 receiving_history의 mid_cd 불일치 확인"""
    logger.info("\n" + "=" * 60)
    logger.info("2. 중분류 코드 불일치 확인")
    logger.info("=" * 60)

    repo = BaseRepository()
    conn = repo._get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                rh.item_cd,
                rh.item_nm,
                rh.mid_cd as rh_mid_cd,
                p.mid_cd as p_mid_cd,
                COUNT(*) as count
            FROM receiving_history rh
            JOIN products p ON rh.item_cd = p.item_cd
            WHERE rh.receiving_date >= DATE('now', '-7 days')
              AND (rh.mid_cd != p.mid_cd OR rh.mid_cd = '' OR rh.mid_cd IS NULL)
            GROUP BY rh.item_cd, rh.item_nm, rh.mid_cd, p.mid_cd
            ORDER BY count DESC
        """)

        rows = cursor.fetchall()

        if rows:
            logger.warning(f"중분류 코드 불일치: {len(rows)}개 상품")
            for row in rows:
                logger.warning(
                    f"  - {row[0]} | {row[1]} | "
                    f"receiving={row[2] or '(빈값)'} vs products={row[3]} | {row[4]}건"
                )
        else:
            logger.info("중분류 코드 불일치 없음")

    finally:
        conn.close()


def check_expiry_accuracy() -> None:
    """유통기한 계산 정확성 확인"""
    logger.info("\n" + "=" * 60)
    logger.info("3. 유통기한 계산 정확성 확인")
    logger.info("=" * 60)

    repo = BaseRepository()
    conn = repo._get_conn()
    cursor = conn.cursor()

    try:
        # order_tracking의 유통기한 확인 (푸드류)
        cursor.execute("""
            SELECT
                ot.item_cd,
                ot.item_nm,
                ot.mid_cd,
                ot.arrival_time,
                ot.expiry_time,
                JULIANDAY(ot.expiry_time) - JULIANDAY(ot.arrival_time) as days_diff
            FROM order_tracking ot
            WHERE ot.arrival_time >= DATE('now', '-7 days')
            ORDER BY ot.arrival_time DESC
            LIMIT 10
        """)

        rows = cursor.fetchall()

        if rows:
            logger.info("order_tracking 유통기한 샘플 (푸드류):")
            for row in rows:
                logger.info(
                    f"  - {row[1]} ({row[2]}) | "
                    f"입고: {row[3]} | 만료: {row[4]} | {row[5]:.1f}일"
                )
        else:
            logger.info("order_tracking 데이터 없음")

        # inventory_batches의 유통기한 확인 (비푸드류)
        cursor.execute("""
            SELECT
                ib.item_cd,
                ib.item_nm,
                ib.mid_cd,
                ib.receiving_date,
                ib.expiry_date,
                ib.expiration_days
            FROM inventory_batches ib
            WHERE ib.receiving_date >= DATE('now', '-7 days')
            ORDER BY ib.receiving_date DESC
            LIMIT 10
        """)

        rows = cursor.fetchall()

        if rows:
            logger.info("\ninventory_batches 유통기한 샘플 (비푸드류):")
            for row in rows:
                logger.info(
                    f"  - {row[1]} ({row[2]}) | "
                    f"입고: {row[3]} | 만료: {row[4]} | {row[5]}일"
                )
        else:
            logger.info("inventory_batches 데이터 없음")

    finally:
        conn.close()


def check_recent_collection_stats() -> None:
    """최근 수집 통계"""
    logger.info("\n" + "=" * 60)
    logger.info("4. 최근 수집 통계")
    logger.info("=" * 60)

    repo = BaseRepository()
    conn = repo._get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                receiving_date,
                COUNT(*) as total_items,
                SUM(receiving_qty) as total_qty,
                COUNT(DISTINCT mid_cd) as category_count
            FROM receiving_history
            WHERE receiving_date >= DATE('now', '-7 days')
            GROUP BY receiving_date
            ORDER BY receiving_date DESC
        """)

        rows = cursor.fetchall()

        if rows:
            logger.info("날짜별 수집 현황:")
            for row in rows:
                logger.info(
                    f"  - {row[0]}: {row[1]}개 상품, {row[2]}개 수량, "
                    f"{row[3]}개 카테고리"
                )
        else:
            logger.info("최근 7일 수집 데이터 없음")

    finally:
        conn.close()


def main():
    """전체 테스트 실행"""
    logger.info("입고 데이터 추적 완정성 테스트 시작")
    logger.info(f"테스트 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        check_missing_batches()
        check_mid_cd_mismatch()
        check_expiry_accuracy()
        check_recent_collection_stats()

        logger.info("\n" + "=" * 60)
        logger.info("테스트 완료")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"테스트 실패: {e}", exc_info=True)


if __name__ == "__main__":
    main()
