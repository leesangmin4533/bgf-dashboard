"""
WeeklyReportFlow -- 주간 리포트 생성 플로우

주간 트렌드 리포트를 생성합니다.
기존 run_scheduler.py의 weekly_report_wrapper를 통합합니다.
"""

from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WeeklyReportFlow:
    """주간 리포트 플로우

    Usage:
        flow = WeeklyReportFlow(store_ctx=ctx)
        result = flow.run()
    """

    def __init__(self, store_ctx=None):
        self.store_ctx = store_ctx

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def run(self) -> Dict[str, Any]:
        """주간 리포트 생성

        Returns:
            {success: bool, result: {...}, ...}
        """
        try:
            from src.analysis.trend_report import ReportScheduler

            scheduler = ReportScheduler(store_id=self.store_id)
            result = scheduler.send_weekly_report()
            logger.info(f"WeeklyReport 완료: store={self.store_id}")
            return {"success": result.get("success", False), "result": result}
        except Exception as e:
            logger.error(f"WeeklyReport 실패: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
