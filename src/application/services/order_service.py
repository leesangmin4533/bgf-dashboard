"""
OrderService -- 주문 서비스

주문 목록 조립, 필터링, 수량 조정, 실행을 담당합니다.
auto_order.py의 AutoOrderSystem을 래핑합니다.
"""

from typing import Optional, List, Dict, Any, Set
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderService:
    """주문 서비스

    Usage:
        service = OrderService(store_ctx=ctx)
        filtered = service.filter_items(predictions)
        result = service.execute(filtered, driver=driver, dry_run=True)
    """

    def __init__(self, store_ctx=None):
        """초기화

        Args:
            store_ctx: StoreContext 인스턴스
        """
        self.store_ctx = store_ctx

    def filter_items(
        self,
        predictions: List[Dict[str, Any]],
        *,
        unavailable_items: Optional[Set[str]] = None,
        cut_items: Optional[Set[str]] = None,
        auto_order_items: Optional[Set[str]] = None,
        smart_order_items: Optional[Set[str]] = None,
        exclude_auto: bool = True,
        exclude_smart: bool = True,
    ) -> List[Dict[str, Any]]:
        """미취급/CUT/제외 항목 필터링

        domain.order.order_filter의 순수 함수를 사용합니다.

        Args:
            predictions: 예측 결과 리스트
            unavailable_items: 미취급 상품 set
            cut_items: CUT 상품 set
            auto_order_items: 자동발주 상품 set
            smart_order_items: 스마트발주 상품 set
            exclude_auto: 자동발주 제외 여부
            exclude_smart: 스마트발주 제외 여부

        Returns:
            필터링된 예측 결과 리스트
        """
        from src.domain.order.order_filter import apply_all_filters

        return apply_all_filters(
            predictions,
            unavailable_items=unavailable_items or set(),
            cut_items=cut_items or set(),
            auto_order_items=auto_order_items or set(),
            smart_order_items=smart_order_items or set(),
            exclude_auto=exclude_auto,
            exclude_smart=exclude_smart,
        )

    def adjust_quantities(
        self,
        items: List[Dict[str, Any]],
        *,
        pending_data: Optional[Dict[str, int]] = None,
        stock_data: Optional[Dict[str, int]] = None,
        min_order_qty: int = 1,
    ) -> List[Dict[str, Any]]:
        """발주 수량 조정 (미입고/재고 반영)

        Args:
            items: 발주 항목 리스트
            pending_data: {item_cd: pending_qty}
            stock_data: {item_cd: stock_qty}
            min_order_qty: 최소 발주량

        Returns:
            조정된 발주 항목 리스트
        """
        from src.domain.order.order_adjuster import adjust_for_pending_stock

        if not pending_data and not stock_data:
            return items

        adjusted = []
        for item in items:
            item_cd = item.get("item_cd", "")
            original_qty = item.get("final_order_qty", item.get("order_qty", 0))
            original_stock = item.get("current_stock", 0)
            original_pending = item.get("pending_receiving_qty", 0)

            new_stock = stock_data.get(item_cd, original_stock) if stock_data else original_stock
            new_pending = pending_data.get(item_cd, original_pending) if pending_data else original_pending

            new_qty = adjust_for_pending_stock(
                original_qty, original_stock, original_pending,
                new_stock, new_pending,
                min_order_qty=min_order_qty,
            )

            if new_qty > 0:
                adjusted_item = item.copy()
                adjusted_item["final_order_qty"] = new_qty
                adjusted_item["current_stock"] = new_stock
                adjusted_item["pending_receiving_qty"] = new_pending
                adjusted.append(adjusted_item)

        logger.info(f"수량 조정: {len(items)}개 -> {len(adjusted)}개")
        return adjusted

    def execute(
        self,
        items: List[Dict[str, Any]],
        *,
        driver=None,
        dry_run: bool = True,
        max_items: int = 0,
    ) -> Dict[str, Any]:
        """발주 실행

        Args:
            items: 발주 항목 리스트
            driver: Selenium WebDriver
            dry_run: True면 실제 실행하지 않음
            max_items: 최대 발주 수 (0=제한없음)

        Returns:
            실행 결과 딕셔너리
        """
        if dry_run:
            logger.info(f"[DRY-RUN] 발주 {len(items)}개 항목 (실행 안 함)")
            return {
                "success": True,
                "dry_run": True,
                "total": len(items),
                "success_count": len(items),
                "fail_count": 0,
            }

        if not driver:
            logger.warning("드라이버 없음: 발주 실행 불가")
            return {
                "success": False,
                "error": "드라이버 없음",
                "total": len(items),
            }

        # AutoOrderSystem을 통한 실제 실행은 기존 코드 위임
        from src.order.auto_order import AutoOrderSystem

        store_id = self.store_ctx.store_id if self.store_ctx else None
        auto_system = AutoOrderSystem(driver, store_id=store_id)
        return auto_system.execute_orders(
            items, max_items=max_items
        )
