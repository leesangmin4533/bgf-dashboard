"""일반(기본) 카테고리 전략"""
from typing import Optional, Tuple, Any, Dict
from src.domain.prediction.base_strategy import CategoryStrategy
from src.prediction.categories.default import (
    get_safety_stock_days,
    get_weekday_coefficient as default_get_weekday_coefficient,
)


class DefaultStrategy(CategoryStrategy):
    """기본 카테고리 전략 -- 전용 핸들러 없는 모든 카테고리의 폴백

    유통기한 기반 안전재고 일수 + 요일 계수를 적용합니다.
    matches()는 항상 True를 반환하므로 레지스트리의 마지막(default)으로 등록해야 합니다.
    """

    @property
    def name(self) -> str:
        return "default"

    def matches(self, mid_cd: str) -> bool:
        # 모든 mid_cd에 매칭 (폴백 전략)
        return True

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
        # default 모듈은 (mid_cd, daily_avg, expiration_days)만 사용
        safety_days = get_safety_stock_days(mid_cd, daily_avg, expiration_days)
        safety_stock = daily_avg * safety_days
        return safety_stock, None

    def get_weekday_coefficient(self, weekday: int) -> Optional[float]:
        # default.py의 get_weekday_coefficient는 (mid_cd, weekday) 시그니처이므로
        # 여기서는 None 반환 (호출자가 mid_cd로 직접 조회해야 함)
        return None
