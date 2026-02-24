"""맥주 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.beer import (
    is_beer_category,
    get_safety_stock_with_beer_pattern,
    BEER_WEEKDAY_COEF,
    BEER_SAFETY_CONFIG,
)


class BeerStrategy(CategoryStrategy):
    """맥주(049) 전략 — 요일 패턴 + 냉장고 공간 상한선"""

    @property
    def name(self) -> str:
        return "beer"

    def matches(self, mid_cd: str) -> bool:
        return is_beer_category(mid_cd)

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
        return get_safety_stock_with_beer_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )

    def get_weekday_coefficient(self, weekday: int) -> Optional[float]:
        return BEER_WEEKDAY_COEF.get(weekday)

    def get_max_stock(
        self, mid_cd: str, daily_avg: float,
        expiration_days: int, **kwargs,
    ) -> Optional[int]:
        if BEER_SAFETY_CONFIG.get("max_stock_enabled"):
            max_days = BEER_SAFETY_CONFIG.get("max_stock_days", 7)
            return int(daily_avg * max_days)
        return None
