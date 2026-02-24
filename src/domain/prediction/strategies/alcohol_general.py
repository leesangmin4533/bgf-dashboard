"""일반주류 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.alcohol_general import (
    is_alcohol_general_category,
    get_safety_stock_with_alcohol_general_pattern,
)


class AlcoholGeneralStrategy(CategoryStrategy):
    """일반주류(양주/와인) 전략 — 요일 패턴"""

    @property
    def name(self) -> str:
        return "alcohol_general"

    def matches(self, mid_cd: str) -> bool:
        return is_alcohol_general_category(mid_cd)

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
        return get_safety_stock_with_alcohol_general_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )
