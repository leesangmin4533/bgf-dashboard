"""소멸성 상품 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.perishable import (
    is_perishable_category,
    get_safety_stock_with_perishable_pattern,
)


class PerishableStrategy(CategoryStrategy):
    """소멸성 상품(떡/과일/요구르트) 전략 -- 초단기 유통기한 + 온도 민감"""

    @property
    def name(self) -> str:
        return "perishable"

    def matches(self, mid_cd: str) -> bool:
        return is_perishable_category(mid_cd)

    def calculate_safety_stock(
        self,
        mid_cd: str,
        daily_avg: float,
        expiration_days: int,
        item_cd: str,
        current_stock: int,
        pending_qty: int,
        store_id: str = "",
        **kwargs,
    ) -> Tuple[float, Optional[Any]]:
        return get_safety_stock_with_perishable_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
