"""
Historical Data Collector - 과거 데이터 수집 모듈

점포별 과거 데이터를 자동으로 수집하는 범용 모듈
- 신규 점포 추가 시 초기 데이터 수집
- 누락된 날짜 자동 탐지 및 보충
- 배치 단위 수집으로 안정성 확보
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import time

from src.collectors.sales_collector import SalesCollector
from src.infrastructure.database.repos import SalesRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HistoricalDataCollector:
    """과거 데이터 수집기 (점포별 범용 모듈)"""

    def __init__(self, store_id: str, store_name: Optional[str] = None):
        """
        Args:
            store_id: 점포 ID (예: "46704")
            store_name: 점포명 (선택, 로그 표시용)
        """
        if not store_id:
            raise ValueError("store_id는 필수입니다")

        self.store_id = store_id
        self.store_name = store_name or store_id
        self.repo = SalesRepository(store_id=self.store_id)

    def get_collection_plan(
        self,
        months: int = 6,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        수집 계획 수립 (실제 수집은 하지 않음)

        Args:
            months: 수집할 개월 수
            force: True면 이미 수집된 날짜도 포함

        Returns:
            수집 계획 정보
            {
                'total_dates': 전체 날짜 수,
                'collected_dates': 이미 수집된 날짜 수,
                'missing_dates': 미수집 날짜 목록,
                'date_range': {'start': ..., 'end': ...},
                'estimated_time_minutes': 예상 소요 시간(분)
            }
        """
        # 1. 날짜 범위 생성
        end_date = datetime.now().date() - timedelta(days=1)  # 어제까지
        start_date = end_date - timedelta(days=30 * months)

        all_dates = []
        current = start_date
        while current <= end_date:
            all_dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        # 2. 이미 수집된 날짜 확인 (점포별로 구분)
        if force:
            missing_dates = all_dates
            collected_count = 0
        else:
            collected = set(self.repo.get_collected_dates(days=365, store_id=self.store_id))
            missing_dates = [d for d in all_dates if d not in collected]
            collected_count = len(all_dates) - len(missing_dates)

        # 3. 예상 소요 시간 (날짜당 약 2분)
        estimated_time = len(missing_dates) * 2

        return {
            'total_dates': len(all_dates),
            'collected_dates': collected_count,
            'missing_dates': missing_dates,
            'missing_count': len(missing_dates),
            'date_range': {
                'start': start_date.strftime("%Y-%m-%d"),
                'end': end_date.strftime("%Y-%m-%d")
            },
            'estimated_time_minutes': estimated_time,
            'force': force
        }

    def collect_historical_data(
        self,
        months: int = 6,
        batch_size: int = 30,
        force: bool = False,
        auto_confirm: bool = False
    ) -> Dict[str, Any]:
        """
        과거 데이터 수집 실행

        Args:
            months: 수집할 개월 수 (기본: 6개월)
            batch_size: 배치 크기 (기본: 30일)
            force: True면 이미 수집된 날짜도 재수집
            auto_confirm: True면 사용자 확인 없이 자동 진행

        Returns:
            수집 결과
            {
                'success': True/False,
                'total_dates': 전체 날짜 수,
                'success_count': 성공한 날짜 수,
                'failed_count': 실패한 날짜 수,
                'elapsed_minutes': 소요 시간(분),
                'failed_dates': 실패한 날짜 목록
            }
        """
        logger.info("=" * 80)
        logger.info(f"과거 데이터 수집 - {self.store_name} ({self.store_id})")
        logger.info("=" * 80)

        start_time = time.time()

        # 1. 수집 계획 수립
        plan = self.get_collection_plan(months=months, force=force)

        logger.info(f"\n[수집 계획]")
        logger.info(f"  점포: {self.store_name} ({self.store_id})")
        logger.info(f"  기간: {plan['date_range']['start']} ~ {plan['date_range']['end']}")
        logger.info(f"  전체: {plan['total_dates']}일")
        logger.info(f"  이미 수집됨: {plan['collected_dates']}일")
        logger.info(f"  수집 대상: {plan['missing_count']}일")
        logger.info(f"  예상 시간: 약 {plan['estimated_time_minutes']:.1f}분")
        logger.info(f"  강제 재수집: {'예' if force else '아니오'}")

        # 수집할 날짜가 없으면 종료
        if plan['missing_count'] == 0:
            logger.info("\n[OK] 모든 데이터가 이미 수집되었습니다!")
            return {
                'success': True,
                'total_dates': plan['total_dates'],
                'success_count': 0,
                'failed_count': 0,
                'elapsed_minutes': 0,
                'message': 'Already collected'
            }

        # 미수집 날짜 샘플 출력
        if plan['missing_count'] > 0:
            sample = plan['missing_dates'][:5]
            if plan['missing_count'] > 5:
                logger.info(f"  누락 날짜 샘플: {sample}... 외 {plan['missing_count'] - 5}일")
            else:
                logger.info(f"  누락 날짜: {plan['missing_dates']}")

        # 2. 사용자 확인 (auto_confirm이 False일 때만)
        if not auto_confirm:
            try:
                print(f"\n계속하시겠습니까? (y/n): ", end="")
                user_input = input().strip().lower()
                if user_input != 'y':
                    logger.info("사용자가 취소했습니다.")
                    return {
                        'success': False,
                        'message': 'Cancelled by user'
                    }
            except (EOFError, KeyboardInterrupt):
                logger.info("\n자동 진행 모드로 계속합니다...")

        # 3. 배치 단위로 수집
        logger.info(f"\n[데이터 수집 시작]")

        result = self._collect_in_batches(
            dates=plan['missing_dates'],
            batch_size=batch_size
        )

        # 4. 결과 요약
        elapsed = time.time() - start_time
        elapsed_minutes = elapsed / 60

        logger.info("\n" + "=" * 80)
        logger.info("수집 완료")
        logger.info("=" * 80)
        logger.info(f"점포: {self.store_name} ({self.store_id})")
        logger.info(f"총 날짜: {plan['missing_count']}일")
        logger.info(f"성공: {result['success_count']}일")
        logger.info(f"실패: {result['failed_count']}일")
        logger.info(f"성공률: {result['success_rate']:.1f}%")
        logger.info(f"소요 시간: {elapsed_minutes:.1f}분")

        if result['failed_dates']:
            logger.warning(f"\n실패한 날짜 ({len(result['failed_dates'])}일):")
            for date in result['failed_dates'][:10]:
                logger.warning(f"  - {date}")
            if len(result['failed_dates']) > 10:
                logger.warning(f"  ... 외 {len(result['failed_dates']) - 10}일")

        return {
            'success': result['failed_count'] == 0,
            'total_dates': plan['missing_count'],
            'success_count': result['success_count'],
            'failed_count': result['failed_count'],
            'success_rate': result['success_rate'],
            'elapsed_minutes': elapsed_minutes,
            'failed_dates': result['failed_dates']
        }

    def _collect_in_batches(
        self,
        dates: List[str],
        batch_size: int
    ) -> Dict[str, Any]:
        """
        배치 단위로 데이터 수집 (내부 메서드)

        Args:
            dates: 수집할 날짜 리스트
            batch_size: 배치 크기

        Returns:
            수집 결과
        """
        total_dates = len(dates)
        total_success = 0
        total_failed = 0
        failed_dates = []

        # 배치로 나누기
        batches = [dates[i:i+batch_size] for i in range(0, len(dates), batch_size)]

        logger.info(f"총 {len(batches)}개 배치로 수집 (배치당 최대 {batch_size}일)")
        logger.info("=" * 80)

        for batch_idx, batch_dates in enumerate(batches, 1):
            logger.info(f"\n[배치 {batch_idx}/{len(batches)}] {len(batch_dates)}일 수집 중...")
            logger.info(f"날짜 범위: {batch_dates[0]} ~ {batch_dates[-1]}")

            collector = SalesCollector(store_id=self.store_id)

            try:
                # 다중 날짜 수집
                results = collector.collect_multiple_dates(
                    dates=batch_dates,
                    save_callback=self._save_batch_data
                )

                # 결과 집계
                batch_success = sum(1 for r in results.values() if r.get('success'))
                batch_failed = len(batch_dates) - batch_success

                total_success += batch_success
                total_failed += batch_failed

                # 실패한 날짜 기록
                for date, result in results.items():
                    if not result.get('success'):
                        failed_dates.append(date)

                logger.info(f"배치 {batch_idx} 완료: 성공 {batch_success}, 실패 {batch_failed}")

                # 배치 간 휴식 (마지막 배치 제외)
                if batch_idx < len(batches):
                    logger.info("다음 배치 전 30초 대기...")
                    time.sleep(30)

            except Exception as e:
                logger.error(f"배치 {batch_idx} 실패: {e}")
                total_failed += len(batch_dates)
                failed_dates.extend(batch_dates)

            finally:
                collector.close()

        success_rate = (total_success / total_dates * 100) if total_dates > 0 else 0

        return {
            'success_count': total_success,
            'failed_count': total_failed,
            'success_rate': success_rate,
            'failed_dates': failed_dates
        }

    def _save_batch_data(self, date_str: str, data: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        수집된 데이터를 DB에 저장 (콜백)

        Args:
            date_str: 날짜 (YYYY-MM-DD)
            data: 수집된 데이터

        Returns:
            저장 통계
        """
        # 데이터 키를 대문자로 변환
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

        stats = self.repo.save_daily_sales(
            sales_data=formatted_data,
            sales_date=date_str,
            store_id=self.store_id,
            collected_at=datetime.now().isoformat()
        )

        return stats

    def get_collection_status(self, days: int = 30) -> Dict[str, Any]:
        """
        최근 N일 수집 현황 조회

        Args:
            days: 조회할 일수

        Returns:
            수집 현황
        """
        collected = self.repo.get_collected_dates(days=days)
        missing = self.repo.get_missing_dates(days=days)

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days - 1)

        return {
            'store_id': self.store_id,
            'store_name': self.store_name,
            'period': {
                'start': start_date.strftime("%Y-%m-%d"),
                'end': end_date.strftime("%Y-%m-%d"),
                'days': days
            },
            'collected_dates': collected,
            'collected_count': len(collected),
            'missing_dates': missing,
            'missing_count': len(missing),
            'completion_rate': (len(collected) / days * 100) if days > 0 else 0
        }


def main():
    """CLI 인터페이스"""
    import argparse

    parser = argparse.ArgumentParser(
        description="점포별 과거 데이터 수집 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예제:
  # 46704 점포 6개월 데이터 수집
  python -m src.collectors.historical_data_collector --store-id 46704 --months 6

  # 강제 재수집
  python -m src.collectors.historical_data_collector --store-id 46704 --force

  # 현황만 확인
  python -m src.collectors.historical_data_collector --store-id 46704 --status-only

  # 자동 진행
  python -m src.collectors.historical_data_collector --store-id 46704 --yes
        """
    )

    parser.add_argument("--store-id", required=True, help="점포 ID (예: 46704)")
    parser.add_argument("--store-name", help="점포명 (선택, 로그 표시용)")
    parser.add_argument("--months", type=int, default=6, help="수집할 개월 수 (기본: 6)")
    parser.add_argument("--batch-size", type=int, default=30, help="배치 크기 (기본: 30)")
    parser.add_argument("--force", "-f", action="store_true", help="이미 수집된 날짜도 재수집")
    parser.add_argument("--yes", "-y", action="store_true", help="사용자 확인 없이 자동 진행")
    parser.add_argument("--status-only", action="store_true", help="현황만 확인하고 수집하지 않음")
    parser.add_argument("--status-days", type=int, default=30, help="현황 조회 일수 (기본: 30)")

    args = parser.parse_args()

    # HistoricalDataCollector 초기화
    collector = HistoricalDataCollector(
        store_id=args.store_id,
        store_name=args.store_name
    )

    # 현황만 조회
    if args.status_only:
        status = collector.get_collection_status(days=args.status_days)

        print("=" * 80)
        print(f"수집 현황 - {status['store_name']} ({status['store_id']})")
        print("=" * 80)
        print(f"\n기간: {status['period']['start']} ~ {status['period']['end']} ({status['period']['days']}일)")
        print(f"수집 완료: {status['collected_count']}일 ({status['completion_rate']:.1f}%)")
        print(f"미수집: {status['missing_count']}일")

        if status['missing_dates']:
            print(f"\n미수집 날짜:")
            for date in status['missing_dates'][:10]:
                print(f"  - {date}")
            if len(status['missing_dates']) > 10:
                print(f"  ... 외 {len(status['missing_dates']) - 10}일")

        return

    # 데이터 수집 실행
    result = collector.collect_historical_data(
        months=args.months,
        batch_size=args.batch_size,
        force=args.force,
        auto_confirm=args.yes
    )

    # 종료 코드
    sys.exit(0 if result.get('success') else 1)


if __name__ == "__main__":
    main()
