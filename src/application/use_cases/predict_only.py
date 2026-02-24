"""
PredictOnlyFlow -- 예측 전용 플로우 (웹 API / 미리보기)

발주 실행 없이 예측 결과만 반환합니다.
"""

from typing import Optional, List, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PredictOnlyFlow:
    """예측 전용 플로우

    Usage:
        flow = PredictOnlyFlow(store_ctx=ctx)
        predictions = flow.run(min_order_qty=0)
    """

    def __init__(self, store_ctx=None):
        self.store_ctx = store_ctx

    def run(
        self,
        min_order_qty: int = 0,
        categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """예측 실행

        Args:
            min_order_qty: 최소 발주 수량 (0=전체)
            categories: 특정 카테고리만 (None=전체)

        Returns:
            예측 결과 리스트
        """
        from src.application.services.prediction_service import PredictionService

        service = PredictionService(store_ctx=self.store_ctx)
        predictions = service.predict_all(
            min_order_qty=min_order_qty,
            categories=categories,
        )
        logger.info(
            f"PredictOnly 완료: {len(predictions)}개 상품"
            f" (store={self.store_ctx.store_id if self.store_ctx else 'default'})"
        )
        return predictions
