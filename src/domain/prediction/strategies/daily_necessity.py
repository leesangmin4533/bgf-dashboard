"""생활용품 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.daily_necessity import (
    is_daily_necessity_category,
    get_safety_stock_with_daily_necessity_pattern,
)


class DailyNecessityStrategy(CategoryStrategy):
    """생활용품 전략 -- 장기 유통기한 + 안정적 수요"""

    @property
    def name(self) -> str:
        return "daily_necessity"

    def matches(self, mid_cd: str) -> bool:
        return is_daily_necessity_category(mid_cd)

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
        return get_safety_stock_with_daily_necessity_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
