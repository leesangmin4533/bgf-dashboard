"""잡화/비식품 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.general_merchandise import (
    is_general_merchandise_category,
    get_safety_stock_with_general_merchandise_pattern,
)


class GeneralMerchandiseStrategy(CategoryStrategy):
    """잡화/비식품 전략 -- 유통기한 없음 + 저회전"""

    @property
    def name(self) -> str:
        return "general_merchandise"

    def matches(self, mid_cd: str) -> bool:
        return is_general_merchandise_category(mid_cd)

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
        return get_safety_stock_with_general_merchandise_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
