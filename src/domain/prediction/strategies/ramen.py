"""라면 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.ramen import (
    is_ramen_category,
    get_safety_stock_with_ramen_pattern,
)


class RamenStrategy(CategoryStrategy):
    """라면/면류(028,029) 전략 — 회전율 기반 안전재고 + 상한선"""

    @property
    def name(self) -> str:
        return "ramen"

    def matches(self, mid_cd: str) -> bool:
        return is_ramen_category(mid_cd)

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
        # 라면 모듈은 current_stock/pending_qty 미사용
        return get_safety_stock_with_ramen_pattern(
            mid_cd, daily_avg, expiration_days, item_cd,
            store_id=store_id,
        )
