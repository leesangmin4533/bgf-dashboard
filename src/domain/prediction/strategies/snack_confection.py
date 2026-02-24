"""과자/간식 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.snack_confection import (
    is_snack_confection_category,
    get_safety_stock_with_snack_confection_pattern,
)


class SnackConfectionStrategy(CategoryStrategy):
    """과자/간식 전략 -- 장기 유통기한 + 진열 공간"""

    @property
    def name(self) -> str:
        return "snack_confection"

    def matches(self, mid_cd: str) -> bool:
        return is_snack_confection_category(mid_cd)

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
        return get_safety_stock_with_snack_confection_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
