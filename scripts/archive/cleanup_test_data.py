"""
테스트 데이터 정리 스크립트

BGF 프로덕션 DB에서 테스트 데이터를 안전하게 삭제합니다.
- 테스트 패턴: item_cd GLOB '88[0-9][0-9][0-9]00001'
- 특정 수집 시각: collected_at = '2026-02-06 11:40:21'
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import shutil
from datetime import datetime
import argparse

from src.utils.logger import get_logger

logger = get_logger(__name__)

# DB 경로
DB_PATH = Path(__file__).parent.parent / "data" / "bgf_sales.db"
BACKUP_DIR = Path(__file__).parent.parent / "data" / "backup"


def create_backup() -> Path:
    """DB 백업 생성

    Returns:
        Path: 백업 파일 경로
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"bgf_sales_before_cleanup_{timestamp}.db"

    logger.info(f"DB 백업 생성 중: {backup_path}")
    shutil.copy2(DB_PATH, backup_path)

    backup_size_mb = backup_path.stat().st_size / 1024 / 1024
    logger.info(f"백업 완료: {backup_size_mb:.2f} MB")

    return backup_path


def analyze_test_data() -> dict:
    """테스트 데이터 분석

    Returns:
        dict: {total_test_records, by_pattern, by_timestamp, affected_dates}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 패턴 기반 테스트 데이터
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM daily_sales
            WHERE item_cd GLOB '88[0-9][0-9][0-9]00001'
        """)
        pattern_count = cursor.fetchone()['count']

        # 특정 수집 시각
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM daily_sales
            WHERE collected_at = '2026-02-06 11:40:21'
        """)
        timestamp_count = cursor.fetchone()['count']

        # 영향받는 날짜
        cursor.execute("""
            SELECT DISTINCT sales_date
            FROM daily_sales
            WHERE item_cd GLOB '88[0-9][0-9][0-9]00001'
            OR collected_at = '2026-02-06 11:40:21'
            ORDER BY sales_date
        """)
        affected_dates = [row['sales_date'] for row in cursor.fetchall()]

        # 총 레코드 수
        cursor.execute("SELECT COUNT(*) as count FROM daily_sales")
        total_records = cursor.fetchone()['count']

        return {
            'total_records': total_records,
            'pattern_count': pattern_count,
            'timestamp_count': timestamp_count,
            'affected_dates': affected_dates,
            'test_total': max(pattern_count, timestamp_count)
        }
    finally:
        conn.close()


def delete_test_data(dry_run: bool = True) -> dict:
    """테스트 데이터 삭제

    Args:
        dry_run: True면 실제 삭제 없이 미리보기만

    Returns:
        dict: {deleted_count, remaining_count}
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if dry_run:
            logger.info("[DRY-RUN 모드] 실제 삭제 없이 미리보기")

            # 삭제 예정 샘플 조회
            cursor.execute("""
                SELECT sales_date, item_cd, mid_cd, sale_qty, collected_at
                FROM daily_sales
                WHERE collected_at = '2026-02-06 11:40:21'
                LIMIT 10
            """)

            logger.info("삭제 예정 샘플 (최대 10건):")
            for row in cursor.fetchall():
                logger.info(f"  {row}")

            return {'deleted_count': 0, 'dry_run': True}

        # 실제 삭제
        logger.info("테스트 데이터 삭제 시작...")

        # 특정 수집 시각으로 삭제 (가장 확실한 방법)
        cursor.execute("""
            DELETE FROM daily_sales
            WHERE collected_at = '2026-02-06 11:40:21'
        """)
        deleted_count = cursor.rowcount

        conn.commit()

        # 검증: 남은 레코드 수
        cursor.execute("SELECT COUNT(*) as count FROM daily_sales")
        remaining_count = cursor.fetchone()[0]

        logger.info(f"삭제 완료: {deleted_count}개 레코드")
        logger.info(f"남은 레코드: {remaining_count}개")

        return {
            'deleted_count': deleted_count,
            'remaining_count': remaining_count,
            'dry_run': False
        }
    finally:
        conn.close()


def verify_cleanup() -> dict:
    """정리 후 검증

    Returns:
        dict: {test_data_found, verification_passed}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 테스트 패턴 남은 것 확인
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM daily_sales
            WHERE item_cd GLOB '88[0-9][0-9][0-9]00001'
        """)
        test_pattern_count = cursor.fetchone()['count']

        # 특정 수집 시각 남은 것 확인
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM daily_sales
            WHERE collected_at = '2026-02-06 11:40:21'
        """)
        test_timestamp_count = cursor.fetchone()['count']

        # 전체 레코드 수
        cursor.execute("SELECT COUNT(*) as count FROM daily_sales")
        total_count = cursor.fetchone()['count']

        # 정상 데이터 샘플 조회 (2026-02-05 주먹밥)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM daily_sales
            WHERE sales_date = '2026-02-05'
            AND mid_cd = '002'
        """)
        sample_count = cursor.fetchone()['count']

        verification_passed = (test_pattern_count == 0 and test_timestamp_count == 0)

        return {
            'test_pattern_count': test_pattern_count,
            'test_timestamp_count': test_timestamp_count,
            'total_records': total_count,
            'sample_date_count': sample_count,
            'verification_passed': verification_passed
        }
    finally:
        conn.close()


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description="테스트 데이터 정리 스크립트")
    parser.add_argument("--dry-run", action="store_true", help="미리보기 모드 (실제 삭제 안함)")
    parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 자동 실행")
    parser.add_argument("--no-backup", action="store_true", help="백업 생성 건너뛰기")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("테스트 데이터 정리 스크립트")
    logger.info("=" * 80)

    # 1. 현재 상태 분석
    logger.info("\n[1단계] 테스트 데이터 분석")
    analysis = analyze_test_data()

    logger.info(f"전체 레코드: {analysis['total_records']:,}개")
    logger.info(f"테스트 데이터 (패턴): {analysis['pattern_count']:,}개")
    logger.info(f"테스트 데이터 (시각): {analysis['timestamp_count']:,}개")
    logger.info(f"영향받는 날짜: {len(analysis['affected_dates'])}일")

    if analysis['affected_dates']:
        sample_dates = analysis['affected_dates'][:5]
        if len(analysis['affected_dates']) > 5:
            logger.info(f"  샘플: {sample_dates}... (총 {len(analysis['affected_dates'])}일)")
        else:
            logger.info(f"  날짜: {analysis['affected_dates']}")

    if analysis['test_total'] == 0:
        logger.info("\n테스트 데이터가 없습니다!")
        return

    # Dry-run 모드
    if args.dry_run:
        logger.info("\n[2단계] Dry-run 모드 (미리보기)")
        delete_test_data(dry_run=True)
        logger.info(f"\n실제 삭제하려면 --yes 플래그를 추가하세요:")
        logger.info(f"  python {Path(__file__).name} --yes")
        return

    # 사용자 확인
    if not args.yes:
        logger.info(f"\n{analysis['test_total']:,}개 레코드를 삭제합니다.")
        logger.info(f"계속하시겠습니까? (y/n): ")
        try:
            response = input().strip().lower()
            if response != 'y':
                logger.info("취소되었습니다.")
                return
        except (EOFError, KeyboardInterrupt):
            logger.info("\n자동 진행...")

    # 2. 백업 생성
    if not args.no_backup:
        logger.info("\n[2단계] DB 백업 생성")
        backup_path = create_backup()
        logger.info(f"백업 경로: {backup_path}")
    else:
        logger.info("\n[2단계] 백업 건너뛰기 (--no-backup)")

    # 3. 삭제 실행
    logger.info("\n[3단계] 테스트 데이터 삭제")
    result = delete_test_data(dry_run=False)

    logger.info(f"삭제된 레코드: {result['deleted_count']:,}개")
    logger.info(f"남은 레코드: {result['remaining_count']:,}개")

    # 4. 검증
    logger.info("\n[4단계] 정리 후 검증")
    verification = verify_cleanup()

    logger.info(f"테스트 패턴 남은 것: {verification['test_pattern_count']}개")
    logger.info(f"테스트 시각 남은 것: {verification['test_timestamp_count']}개")
    logger.info(f"전체 레코드: {verification['total_records']:,}개")
    logger.info(f"샘플 확인 (2026-02-05 주먹밥): {verification['sample_date_count']}개")

    # 5. 결과
    logger.info("\n" + "=" * 80)
    if verification['verification_passed']:
        logger.info("정리 완료!")
        logger.info("모든 테스트 데이터가 성공적으로 삭제되었습니다.")
    else:
        logger.warning("경고: 테스트 데이터가 남아있습니다!")
        logger.warning(f"  패턴: {verification['test_pattern_count']}개")
        logger.warning(f"  시각: {verification['test_timestamp_count']}개")

    logger.info("=" * 80)

    if not args.no_backup:
        logger.info(f"\n백업 파일: {backup_path}")


if __name__ == "__main__":
    main()
