"""디저트 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.dessert import (
    is_dessert_category,
    get_safety_stock_with_dessert_pattern,
)


class DessertStrategy(CategoryStrategy):
    """디저트(014) 전략 -- 단기 유통기한 + 계절성"""

    @property
    def name(self) -> str:
        return "dessert"

    def matches(self, mid_cd: str) -> bool:
        return is_dessert_category(mid_cd)

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
        return get_safety_stock_with_dessert_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
