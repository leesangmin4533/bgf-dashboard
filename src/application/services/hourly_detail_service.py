"""
HourlyDetailService -- 시간대별 매출 상세(품목별) 수집 서비스

Collector + Repository를 연결하여 수집 → 저장 파이프라인을 관리합니다.

수집 전략:
- 매시 10분에 직전 시간대 수집 (13:10 → 12시 데이터)
- 55분마다 heartbeat로 세션 유지
- 실패 시간대 재시도 큐
- 백필 모드: 과거 6개월 일괄 수집
"""

import time as _time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.collectors.hourly_sales_detail_collector import HourlySalesDetailCollector
from src.infrastructure.database.repos.hourly_sales_detail_repo import (
    HourlySalesDetailRepository,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HourlyDetailService:
    """시간대별 매출 상세 수집 서비스"""

    def __init__(self, store_id: str, driver: Any = None):
        self.store_id = store_id
        self.repo = HourlySalesDetailRepository(store_id=store_id)
        self.repo.ensure_table()
        self._collector: Optional[HourlySalesDetailCollector] = None
        self._driver = driver

    def _get_collector(self, driver: Any = None) -> HourlySalesDetailCollector:
        """Collector 인스턴스 (lazy init)"""
        drv = driver or self._driver
        if not self._collector:
            self._collector = HourlySalesDetailCollector(driver=drv)
        elif drv:
            self._collector.driver = drv
        return self._collector

    def collect_previous_hour(self, driver: Any = None) -> int:
        """직전 시간대 수집 (매시 10분 호출용)

        예: 13:10에 호출 → 12시 데이터 수집

        Returns:
            수집된 품목 수 (0이면 실패/데이터없음)
        """
        now = datetime.now()
        target_hour = now.hour - 1
        target_date = now.strftime('%Y-%m-%d')

        if target_hour < 0:
            # 자정 직후 → 어제 23시
            target_hour = 23
            target_date = (now - timedelta(days=1)).strftime('%Y-%m-%d')

        return self.collect_hour(target_date, target_hour, driver)

    def collect_hour(self, date_str: str, hour: int,
                     driver: Any = None) -> int:
        """특정 날짜+시간대 수집 → DB 저장

        Returns:
            수집된 품목 수
        """
        collector = self._get_collector(driver)
        result = collector.collect_hour(date_str.replace('-', ''), hour)

        if isinstance(result, tuple):
            items, ssv_text = result
        else:
            items, ssv_text = result, None

        if not items:
            return 0

        # raw 저장
        if ssv_text:
            self.repo.save_raw(
                date_str, hour, ssv_text, len(items)
            )

        # 가공 저장
        saved = self.repo.save_detail(date_str, hour, items)
        return saved

    def collect_all_hours(self, date_str: str,
                          driver: Any = None) -> Dict[str, int]:
        """특정 날짜 전체 시간대 수집 (미수집분만)

        Returns:
            {'collected': N, 'items': N, 'failed': N}
        """
        collector = self._get_collector(driver)

        # 이미 수집된 시간대 건너뛰기
        collected_hours = self.repo.get_collected_hours(date_str)
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')

        if date_str == today:
            target_hours = [
                h for h in range(0, now.hour)
                if h not in collected_hours
            ]
        else:
            target_hours = [
                h for h in range(24)
                if h not in collected_hours
            ]

        if not target_hours:
            logger.debug(f"[HDS] {date_str}: 이미 전체 수집 완료")
            return {'collected': 0, 'items': 0, 'failed': 0}

        results = collector.collect_all_hours(
            date_str.replace('-', ''), hours=target_hours, delay=0.5
        )

        total_items = 0
        saved_hours = 0
        for hour, items in results.items():
            if items:
                self.repo.save_detail(date_str, hour, items)
                total_items += len(items)
                saved_hours += 1

        # results에 포함된 시간대 = 유효한 API 응답 (빈 응답 포함)
        api_success = len(results)
        api_failed = len(target_hours) - api_success

        logger.info(
            f"[HDS] {date_str}: API응답 {api_success}/{len(target_hours)}, "
            f"품목있음 {saved_hours}, {total_items}품목, 실패 {api_failed}"
        )

        return {
            'collected': api_success,
            'items': total_items,
            'failed': api_failed,
        }

    def retry_failed(self, driver: Any = None) -> int:
        """실패 큐 재시도"""
        collector = self._get_collector(driver)
        return collector.retry_failed()

    def backfill(self, days: int = 180, driver: Any = None,
                 start_date: str = None, end_date: str = None) -> Dict:
        """과거 데이터 일괄 백필

        서비스 레이어에서 날짜별 순회하여 수집 → DB 저장까지 처리합니다.

        Args:
            days: 백필 기간 (기본 180일 = 6개월)
            driver: Selenium 드라이버
            start_date: 시작일 (YYYY-MM-DD, None=오늘-days)
            end_date: 종료일 (YYYY-MM-DD, None=어제)

        Returns:
            {'total_days': N, 'success_days': N, 'total_items': N}
        """
        collector = self._get_collector(driver)

        if end_date:
            end_dt = datetime.strptime(end_date.replace('-', ''), '%Y%m%d')
        else:
            end_dt = datetime.now() - timedelta(days=1)
        if start_date:
            begin = datetime.strptime(
                start_date.replace('-', ''), '%Y%m%d'
            )
        else:
            begin = end_dt - timedelta(days=days)

        stats = {'total_days': 0, 'success_days': 0, 'total_items': 0}
        current = begin

        logger.info(
            f"[HDS] 백필 시작: {begin:%Y-%m-%d} ~ {end_dt:%Y-%m-%d} "
            f"({(end_dt - begin).days + 1}일)"
        )

        while current <= end_dt:
            date_str = current.strftime('%Y-%m-%d')
            stats['total_days'] += 1

            # Preflight: selDay 호출로 서버 상태 워밍업
            self._preflight_selday(collector, date_str)

            # collect_all_hours → 수집 + DB 저장
            result = self.collect_all_hours(date_str, driver)
            items = result.get('items', 0)
            api_success = result.get('collected', 0)
            api_failed = result.get('failed', 0)

            stats['total_items'] += items
            if api_success > 0 or api_failed == 0:
                # API가 응답했거나, 이미 전체 수집 완료
                stats['success_days'] += 1

            current += timedelta(days=1)
            _time.sleep(2.0)

            if stats['total_days'] % 10 == 0:
                logger.info(
                    f"[HDS] 백필 진행: {stats['total_days']}일 처리, "
                    f"{stats['total_items']}건 수집"
                )

        logger.info(
            f"[HDS] 백필 완료: "
            f"{stats['success_days']}/{stats['total_days']}일, "
            f"총 {stats['total_items']}건"
        )
        return stats

    def _preflight_selday(self, collector, date_str: str) -> None:
        """selDay 호출로 서버 상태 워밍업 (selPrdT3 전 필수)"""
        from src.collectors.hourly_sales_detail_collector import (
            HEARTBEAT_ENDPOINT,
        )
        template = collector._template_selday or collector._template_prdt3
        if not template or not collector.driver:
            return
        try:
            body = collector._replace_date_only(
                template, date_str.replace('-', '')
            )
            collector._call_api(HEARTBEAT_ENDPOINT, body)
        except Exception:
            pass

    def send_heartbeat(self, driver: Any = None) -> bool:
        """세션 유지 heartbeat"""
        collector = self._get_collector(driver)
        return collector.send_heartbeat()

    def get_failed_hours(self, date_str: str) -> List[int]:
        """수집 실패 시간대 목록"""
        return self.repo.get_failed_hours(date_str)
