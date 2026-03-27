"""
BeverageDecisionFlow -- 음료 판단 Use Case (스케줄러에서 호출)

Usage:
    flow = BeverageDecisionFlow(store_id="46513")
    result = flow.run(target_categories=["A"])
"""

from typing import Dict, Any, List, Optional

from src.application.services.beverage_decision_service import BeverageDecisionService
from src.settings.constants import DEFAULT_STORE_ID
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BeverageDecisionFlow:
    """음료 발주 유지/정지 판단 플로우"""

    def __init__(self, store_id: str = "", **kwargs):
        self.store_id = store_id or DEFAULT_STORE_ID

    def run(
        self,
        target_categories: Optional[List[str]] = None,
        reference_date: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """판단 실행

        Args:
            target_categories: 대상 카테고리 (["A"], ["B"], ["C","D"] 등)
            reference_date: 기준일 (YYYY-MM-DD)
        """
        if not self.store_id:
            logger.error("[BeverageDecisionFlow] store_id 미지정")
            return {"error": "store_id required"}

        service = BeverageDecisionService(store_id=self.store_id)
        return service.run(
            target_categories=target_categories,
            reference_date=reference_date,
        )
