"""즉석식품 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.instant_meal import (
    is_instant_meal_category,
    get_safety_stock_with_instant_meal_pattern,
)


class InstantMealStrategy(CategoryStrategy):
    """즉석식품 전략 -- 유통기한 + 온장고 공간"""

    @property
    def name(self) -> str:
        return "instant_meal"

    def matches(self, mid_cd: str) -> bool:
        return is_instant_meal_category(mid_cd)

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
        return get_safety_stock_with_instant_meal_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
