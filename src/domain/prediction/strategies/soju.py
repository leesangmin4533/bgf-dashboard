"""소주 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.soju import (
    is_soju_category,
    get_safety_stock_with_soju_pattern,
    SOJU_WEEKDAY_COEF,
    SOJU_SAFETY_CONFIG,
)


class SojuStrategy(CategoryStrategy):
    """소주(050) 전략 — 요일 패턴 + 상한선"""

    @property
    def name(self) -> str:
        return "soju"

    def matches(self, mid_cd: str) -> bool:
        return is_soju_category(mid_cd)

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
        return get_safety_stock_with_soju_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )

    def get_weekday_coefficient(self, weekday: int) -> Optional[float]:
        return SOJU_WEEKDAY_COEF.get(weekday)

    def get_max_stock(
        self, mid_cd: str, daily_avg: float,
        expiration_days: int, **kwargs,
    ) -> Optional[int]:
        if SOJU_SAFETY_CONFIG.get("max_stock_enabled"):
            max_days = SOJU_SAFETY_CONFIG.get("max_stock_days", 7)
            return int(daily_avg * max_days)
        return None
