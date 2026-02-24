"""
InventoryService -- 재고 통합 조회 서비스

재고, 입고, 배치 정보를 Repository를 통해 통합 조회합니다.
"""

from typing import Optional, Dict, Any, List
from src.utils.logger import get_logger

logger = get_logger(__name__)


class InventoryService:
    """재고 통합 조회 서비스

    Usage:
        service = InventoryService(store_ctx=ctx)
        stock = service.get_current_stock("1234567890")
        pending = service.get_pending_qty("1234567890")
        summary = service.get_stock_summary()
    """

    def __init__(self, store_ctx=None):
        """초기화

        Args:
            store_ctx: StoreContext 인스턴스
        """
        self.store_ctx = store_ctx
        self._inventory_repo = None
        self._batch_repo = None
        self._tracking_repo = None

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def _get_inventory_repo(self):
        if self._inventory_repo is None:
            from src.infrastructure.database.repos import RealtimeInventoryRepository
            self._inventory_repo = RealtimeInventoryRepository(store_id=self.store_id)
        return self._inventory_repo

    def _get_batch_repo(self):
        if self._batch_repo is None:
            from src.infrastructure.database.repos import InventoryBatchRepository
            self._batch_repo = InventoryBatchRepository(store_id=self.store_id)
        return self._batch_repo

    def _get_tracking_repo(self):
        if self._tracking_repo is None:
            from src.infrastructure.database.repos import OrderTrackingRepository
            self._tracking_repo = OrderTrackingRepository(store_id=self.store_id)
        return self._tracking_repo

    def get_current_stock(self, item_cd: str) -> int:
        """현재 재고 조회

        Args:
            item_cd: 상품 코드

        Returns:
            재고 수량 (없으면 0)
        """
        repo = self._get_inventory_repo()
        result = repo.get_by_item(item_cd, store_id=self.store_id)
        return result.get("stock_qty", 0) if result else 0

    def get_pending_qty(self, item_cd: str) -> int:
        """미입고 수량 조회

        Args:
            item_cd: 상품 코드

        Returns:
            미입고 수량 (없으면 0)
        """
        repo = self._get_tracking_repo()
        return repo.get_pending_qty_sum(item_cd, store_id=self.store_id)

    def get_stock_summary(self) -> Dict[str, Any]:
        """재고 현황 요약

        Returns:
            {summary, cut_count, unavailable_count}
        """
        repo = self._get_inventory_repo()
        summary = repo.get_summary(store_id=self.store_id)
        cut_items = repo.get_cut_items(store_id=self.store_id)
        unavailable = repo.get_unavailable_items(store_id=self.store_id)
        return {
            "summary": summary,
            "cut_count": len(cut_items),
            "unavailable_count": len(unavailable),
        }

    def get_expiring_items(self, days: int = 3) -> List[Dict[str, Any]]:
        """유통기한 임박 항목 조회

        Args:
            days: 임박 기준 일수

        Returns:
            임박 항목 리스트
        """
        repo = self._get_batch_repo()
        return repo.get_expiring_soon(days=days, store_id=self.store_id)

    def get_unavailable_items(self) -> set:
        """미취급 상품 코드 set"""
        repo = self._get_inventory_repo()
        return set(repo.get_unavailable_items(store_id=self.store_id))

    def get_cut_items(self) -> set:
        """CUT 상품 코드 set"""
        repo = self._get_inventory_repo()
        return set(repo.get_cut_items(store_id=self.store_id))
