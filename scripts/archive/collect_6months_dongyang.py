"""
46704 이천 동양점 6개월 중분류 매출 데이터 수집 스크립트

BGF 사이트에서 실제 매출 데이터를 자동 스크래핑하여 DB에 저장
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from typing import List, Dict, Any
import time
import argparse

from src.collectors.sales_collector import SalesCollector
from src.db.repository import SalesRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 설정
STORE_ID = "46704"  # 이천 동양점
MONTHS_TO_COLLECT = 6
BATCH_SIZE = 30  # 한 번에 수집할 날짜 수


def generate_date_list(months: int) -> List[str]:
    """
    수집할 날짜 리스트 생성 (오늘부터 과거로)

    Args:
        months: 수집할 개월 수

    Returns:
        날짜 리스트 (YYYY-MM-DD 형식, 과거→현재 순서)
    """
    end_date = datetime.now().date() - timedelta(days=1)  # 어제까지
    start_date = end_date - timedelta(days=30 * months)

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    logger.info(f"수집 대상: {start_date} ~ {end_date} ({len(dates)}일)")
    return dates


def filter_already_collected(dates: List[str], store_id: str) -> List[str]:
    """
    이미 수집된 날짜 제외 (기존 run_full_flow.py 로직 참조)

    Args:
        dates: 전체 날짜 리스트
        store_id: 점포 ID

    Returns:
        아직 수집되지 않은 날짜 리스트
    """
    repo = SalesRepository()

    # ★ 기존 로직: get_missing_dates() 사용
    # 전체 날짜 중에서 DB에 없는 날짜만 필터링
    all_dates_set = set(dates)

    # 수집된 날짜 목록 가져오기
    collected = set(repo.get_collected_dates(days=365))  # 1년치 조회

    # 미수집 날짜 필터링
    uncollected = [d for d in dates if d not in collected]

    logger.info(f"미수집 날짜: {len(uncollected)}일 / 전체 {len(dates)}일")

    # 미수집 날짜가 있으면 목록 일부 출력
    if uncollected:
        sample = uncollected[:5]
        if len(uncollected) > 5:
            logger.info(f"  누락 날짜 샘플: {sample}... (총 {len(uncollected)}일)")
        else:
            logger.info(f"  누락 날짜: {uncollected}")

    return uncollected


def save_batch_data(date_str: str, data: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    수집된 데이터를 DB에 저장

    Args:
        date_str: 날짜 (YYYY-MM-DD)
        data: 수집된 데이터

    Returns:
        저장 통계 {total, new, updated}
    """
    repo = SalesRepository()

    # SalesRepository.save_daily_sales() 사용
    # 데이터 키를 대문자로 변환 (ITEM_CD, MID_CD 등)
    formatted_data = []
    for item in data:
        formatted_item = {
            'ITEM_CD': item.get('item_cd') or item.get('ITEM_CD', ''),
            'ITEM_NM': item.get('item_nm') or item.get('ITEM_NM', ''),
            'MID_CD': item.get('mid_cd') or item.get('MID_CD', ''),
            'MID_NM': item.get('mid_nm') or item.get('MID_NM', ''),
            'SALE_QTY': item.get('sale_qty') or item.get('SALE_QTY', 0),
            'ORD_QTY': item.get('ord_qty') or item.get('ORD_QTY', 0),
            'BUY_QTY': item.get('buy_qty') or item.get('BUY_QTY', 0),
            'DISUSE_QTY': item.get('disuse_qty') or item.get('DISUSE_QTY', 0),
            'STOCK_QTY': item.get('stock_qty') or item.get('STOCK_QTY', 0),
        }
        formatted_data.append(formatted_item)

    stats = repo.save_daily_sales(
        sales_data=formatted_data,
        sales_date=date_str,
        store_id=STORE_ID,
        collected_at=datetime.now().isoformat()
    )

    return stats


def collect_in_batches(dates: List[str], batch_size: int = BATCH_SIZE) -> Dict[str, Any]:
    """
    배치 단위로 데이터 수집

    Args:
        dates: 수집할 날짜 리스트
        batch_size: 배치 크기

    Returns:
        수집 결과 통계
    """
    total_dates = len(dates)
    total_success = 0
    total_failed = 0

    # 배치로 나누기
    batches = [dates[i:i+batch_size] for i in range(0, len(dates), batch_size)]

    logger.info(f"총 {len(batches)}개 배치로 수집 시작 (배치당 최대 {batch_size}일)")
    logger.info("=" * 80)

    for batch_idx, batch_dates in enumerate(batches, 1):
        logger.info(f"\n[배치 {batch_idx}/{len(batches)}] {len(batch_dates)}일 수집 중...")
        logger.info(f"날짜 범위: {batch_dates[0]} ~ {batch_dates[-1]}")

        collector = SalesCollector(store_id=STORE_ID)

        try:
            # 다중 날짜 수집
            results = collector.collect_multiple_dates(
                dates=batch_dates,
                save_callback=save_batch_data
            )

            # 결과 집계
            batch_success = sum(1 for r in results.values() if r.get('success'))
            batch_failed = len(batch_dates) - batch_success

            total_success += batch_success
            total_failed += batch_failed

            logger.info(f"배치 {batch_idx} 완료: 성공 {batch_success}, 실패 {batch_failed}")

            # 배치 간 휴식 (마지막 배치 제외)
            if batch_idx < len(batches):
                logger.info("다음 배치 전 30초 대기...")
                time.sleep(30)

        except Exception as e:
            logger.error(f"배치 {batch_idx} 실패: {e}")
            total_failed += len(batch_dates)

        finally:
            collector.close()

    return {
        'total_dates': total_dates,
        'success': total_success,
        'failed': total_failed,
        'success_rate': (total_success / total_dates * 100) if total_dates > 0 else 0,
    }


def print_collection_summary(store_id: str):
    """
    수집 완료 후 통계 출력

    Args:
        store_id: 점포 ID
    """
    repo = SalesRepository()

    logger.info("\n" + "=" * 80)
    logger.info("📊 수집 완료 통계")
    logger.info("=" * 80)

    # 수집된 날짜 목록
    collected_dates = repo.get_collected_dates(days=365)
    logger.info(f"총 수집 날짜: {len(collected_dates)}일")

    if collected_dates:
        logger.info(f"최신 수집일: {max(collected_dates)}")
        logger.info(f"최초 수집일: {min(collected_dates)}")

    logger.info("\n✅ 데이터 수집이 완료되었습니다!")
    logger.info("=" * 80)


def main():
    """메인 실행 함수"""
    # 명령줄 인자 파싱
    parser = argparse.ArgumentParser(description="46704 이천 동양점 6개월 매출 데이터 수집")
    parser.add_argument("--yes", "-y", action="store_true", help="사용자 확인 없이 자동 진행")
    parser.add_argument("--dry-run", action="store_true", help="실제 수집 없이 계획만 출력")
    parser.add_argument("--force", "-f", action="store_true", help="이미 수집된 날짜도 다시 수집 (강제 재수집)")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info(f"46704 이천 동양점 - {MONTHS_TO_COLLECT}개월 매출 데이터 수집")
    logger.info("=" * 80)

    # 1. 날짜 리스트 생성
    logger.info("\n[1단계] 수집 날짜 목록 생성")
    all_dates = generate_date_list(MONTHS_TO_COLLECT)

    # 2. 이미 수집된 날짜 제외 (--force 옵션이 없을 때만)
    logger.info("\n[2단계] 미수집 날짜 확인")
    if args.force:
        logger.info("--force 옵션: 모든 날짜를 강제로 다시 수집합니다.")
        dates_to_collect = all_dates
    else:
        dates_to_collect = filter_already_collected(all_dates, STORE_ID)

    if not dates_to_collect:
        logger.info("✅ 모든 날짜가 이미 수집되었습니다!")
        print_collection_summary(STORE_ID)
        return

    logger.info(f"\n수집할 날짜: {len(dates_to_collect)}일")
    logger.info(f"예상 소요 시간: 약 {len(dates_to_collect) * 2 / 60:.1f}분")

    # Dry-run 모드
    if args.dry_run:
        logger.info("\n[Dry-run 모드] 실제 수집 없이 종료")
        logger.info(f"수집 대상 날짜: {dates_to_collect}")
        return

    # 사용자 확인 (--yes 플래그 없을 때만)
    if not args.yes:
        try:
            print("\n계속하시겠습니까? (y/n): ", end="")
            if input().strip().lower() != 'y':
                logger.info("취소되었습니다.")
                return
        except (EOFError, KeyboardInterrupt):
            logger.info("\n자동 진행 모드로 계속합니다...")
            # 자동 진행

    # 3. 데이터 수집
    logger.info("\n[3단계] 데이터 수집 시작")
    start_time = time.time()

    results = collect_in_batches(dates_to_collect, batch_size=BATCH_SIZE)

    elapsed = time.time() - start_time

    # 4. 결과 출력
    logger.info("\n" + "=" * 80)
    logger.info("📈 수집 결과")
    logger.info("=" * 80)
    logger.info(f"총 날짜: {results['total_dates']}일")
    logger.info(f"성공: {results['success']}일")
    logger.info(f"실패: {results['failed']}일")
    logger.info(f"성공률: {results['success_rate']:.1f}%")
    logger.info(f"소요 시간: {elapsed/60:.1f}분")

    # 5. 최종 통계
    print_collection_summary(STORE_ID)


if __name__ == "__main__":
    main()
