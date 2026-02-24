"""
PredictionService -- 예측 서비스

ImprovedPredictor를 래핑하여 일관된 인터페이스를 제공합니다.
Strategy Registry를 통해 카테고리별 예측을 수행합니다.
"""

from dataclasses import asdict
from typing import Optional, List, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PredictionService:
    """예측 서비스

    ImprovedPredictor를 래핑하여 StoreContext 기반 통합 인터페이스를 제공합니다.

    Usage:
        from src.settings.store_context import StoreContext
        ctx = StoreContext.from_store_id("46513")
        service = PredictionService(store_ctx=ctx)
        predictions = service.predict_all(min_order_qty=1)
    """

    def __init__(self, store_ctx=None):
        """초기화

        Args:
            store_ctx: StoreContext 인스턴스 (None이면 기본 매장)
        """
        self.store_ctx = store_ctx
        self._predictor = None

    def _get_predictor(self):
        """ImprovedPredictor lazy 초기화"""
        if self._predictor is None:
            from src.prediction.improved_predictor import ImprovedPredictor

            store_id = self.store_ctx.store_id if self.store_ctx else None
            self._predictor = ImprovedPredictor(store_id=store_id)
        return self._predictor

    def predict_all(
        self,
        min_order_qty: int = 1,
        categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """모든 관리 대상 상품 예측

        ImprovedPredictor.get_order_candidates()를 호출하여
        PredictionResult를 dict로 변환하여 반환합니다.
        하위 레이어(order_filter 등)가 dict 기반이므로 asdict() 변환 필수.

        Args:
            min_order_qty: 최소 발주 수량 (이하 제외)
            categories: 특정 카테고리만 (None=전체)

        Returns:
            예측 결과 딕셔너리 리스트
        """
        predictor = self._get_predictor()
        try:
            results = predictor.get_order_candidates(min_order_qty=min_order_qty)
            if categories:
                results = [r for r in results if r.mid_cd in categories]
            # PredictionResult → dict 변환 (order_filter 등 하위 레이어 호환)
            dict_results = [asdict(r) for r in results]
            logger.info(
                f"예측 완료: {len(dict_results)}개 상품"
                f" (store={self.store_ctx.store_id if self.store_ctx else 'default'})"
            )
            return dict_results
        except Exception as e:
            logger.error(f"예측 실패: {e}")
            raise

    def predict_single(self, item_cd: str) -> Optional[Dict[str, Any]]:
        """단일 상품 예측

        Args:
            item_cd: 상품 코드

        Returns:
            예측 결과 딕셔너리 (없으면 None)
        """
        results = self.predict_all(min_order_qty=0)
        for r in results:
            if r.get("item_cd") == item_cd:
                return r
        return None
