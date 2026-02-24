"""담배 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.tobacco import (
    is_tobacco_category,
    get_safety_stock_with_tobacco_pattern,
    TOBACCO_DYNAMIC_SAFETY_CONFIG,
)


class TobaccoStrategy(CategoryStrategy):
    """담배/전자담배(072,073) 전략 — 카톤 빈도 + 매진 빈도 + 공간 상한선"""

    @property
    def name(self) -> str:
        return "tobacco"

    def matches(self, mid_cd: str) -> bool:
        return is_tobacco_category(mid_cd)

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
        return get_safety_stock_with_tobacco_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            current_stock, pending_qty,
            store_id=store_id,
        )

    def get_max_stock(
        self, mid_cd: str, daily_avg: float,
        expiration_days: int, **kwargs,
    ) -> Optional[int]:
        return TOBACCO_DYNAMIC_SAFETY_CONFIG.get("max_stock", 30)
