"""음료류 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.beverage import (
    is_beverage_category,
    get_safety_stock_with_beverage_pattern,
)


class BeverageStrategy(CategoryStrategy):
    """음료류 전략 -- 계절성 + 온도 기반"""

    @property
    def name(self) -> str:
        return "beverage"

    def matches(self, mid_cd: str) -> bool:
        return is_beverage_category(mid_cd)

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
        return get_safety_stock_with_beverage_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
