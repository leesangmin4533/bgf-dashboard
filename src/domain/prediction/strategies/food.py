"""푸드류 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.food import (
    is_food_category,
    get_safety_stock_with_food_pattern,
)


class FoodStrategy(CategoryStrategy):
    """푸드류(도시락/김밥/샌드위치/햄버거/빵) 전략 -- 유통기한 기반 + 폐기 피드백

    NOTE: food 모듈은 다른 시그니처를 가짐 (expiration_days 대신 disuse_rate).
    calculate_safety_stock에서 kwargs를 통해 disuse_rate를 전달받음.
    """

    @property
    def name(self) -> str:
        return "food"

    def matches(self, mid_cd: str) -> bool:
        return is_food_category(mid_cd)

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
        # food 모듈은 (mid_cd, daily_avg, item_cd, disuse_rate, store_id) 시그니처
        disuse_rate = kwargs.get("disuse_rate", None)
        return get_safety_stock_with_food_pattern(
            mid_cd, daily_avg, item_cd,
            disuse_rate=disuse_rate,
            store_id=store_id,
        )
