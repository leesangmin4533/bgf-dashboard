"""
WasteReportFlow -- 폐기 리포트 생성 플로우

폐기 추적 리포트를 생성합니다.
기존 run_scheduler.py의 waste_report_wrapper를 통합합니다.
"""

from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class WasteReportFlow:
    """폐기 리포트 플로우

    Usage:
        flow = WasteReportFlow(store_ctx=ctx)
        result = flow.run()
    """

    def __init__(self, store_ctx=None):
        self.store_ctx = store_ctx

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def run(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """폐기 리포트 생성

        Args:
            target_date: 기준 날짜 (기본: 오늘)

        Returns:
            {success: bool, report_path: str, ...}
        """
        try:
            from src.analysis.waste_report import generate_waste_report

            path = generate_waste_report(
                target_date=target_date,
                store_id=self.store_id,
            )
            if path:
                logger.info(f"WasteReport 완료: store={self.store_id}, path={path}")
                return {"success": True, "report_path": str(path)}
            else:
                logger.error(f"WasteReport 생성 실패: store={self.store_id}")
                return {"success": False, "error": "리포트 생성 실패"}
        except Exception as e:
            logger.error(f"WasteReport 실패: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
