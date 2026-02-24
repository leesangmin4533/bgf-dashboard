"""냉동/아이스크림 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.frozen_ice import (
    is_frozen_ice_category,
    get_safety_stock_with_frozen_ice_pattern,
)


class FrozenIceStrategy(CategoryStrategy):
    """냉동/아이스크림 전략 -- 계절성 + 냉동고 공간"""

    @property
    def name(self) -> str:
        return "frozen_ice"

    def matches(self, mid_cd: str) -> bool:
        return is_frozen_ice_category(mid_cd)

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
        return get_safety_stock_with_frozen_ice_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
