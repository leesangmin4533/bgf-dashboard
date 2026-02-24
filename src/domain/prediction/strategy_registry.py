"""
StrategyRegistry — mid_cd에 따라 적절한 CategoryStrategy를 반환

improved_predictor.py의 15개 if/elif 체인을 대체합니다.
"""

from typing import List, Optional

from src.domain.prediction.base_strategy import CategoryStrategy
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StrategyRegistry:
    """카테고리 전략 레지스트리

    Usage:
        registry = StrategyRegistry()
        strategy = registry.get_strategy("049")  # → BeerStrategy
        strategy = registry.get_strategy("999")  # → DefaultStrategy
    """

    def __init__(self):
        self._strategies: List[CategoryStrategy] = []
        self._default: Optional[CategoryStrategy] = None
        self._cache: dict = {}  # mid_cd → strategy 캐시

    def register(self, strategy: CategoryStrategy) -> None:
        """전략 등록"""
        self._strategies.append(strategy)

    def set_default(self, strategy: CategoryStrategy) -> None:
        """기본 전략 설정"""
        self._default = strategy

    def get_strategy(self, mid_cd: str) -> CategoryStrategy:
        """mid_cd에 매칭되는 전략 반환

        Args:
            mid_cd: 중분류 코드

        Returns:
            매칭되는 CategoryStrategy (없으면 DefaultStrategy)
        """
        # 캐시 확인
        if mid_cd in self._cache:
            return self._cache[mid_cd]

        # 순서대로 매칭 시도
        for strategy in self._strategies:
            if strategy.matches(mid_cd):
                self._cache[mid_cd] = strategy
                return strategy

        # 기본 전략
        if self._default:
            self._cache[mid_cd] = self._default
            return self._default

        raise ValueError(f"mid_cd '{mid_cd}'에 매칭되는 전략이 없습니다")

    def list_strategies(self) -> List[str]:
        """등록된 전략 이름 목록"""
        names = [s.name for s in self._strategies]
        if self._default:
            names.append(f"{self._default.name} (default)")
        return names


def create_default_registry() -> StrategyRegistry:
    """기본 레지스트리 생성 (모든 카테고리 전략 등록)

    15개 카테고리 전략을 등록하고 DefaultStrategy를 폴백으로 설정합니다.
    등록 순서는 improved_predictor.py의 기존 if/elif 체인과 동일합니다.

    Returns:
        모든 전략이 등록된 StrategyRegistry
    """
    from src.domain.prediction.strategies import (
        RamenStrategy,
        TobaccoStrategy,
        BeerStrategy,
        SojuStrategy,
        FoodStrategy,
        PerishableStrategy,
        BeverageStrategy,
        FrozenIceStrategy,
        InstantMealStrategy,
        DessertStrategy,
        SnackConfectionStrategy,
        AlcoholGeneralStrategy,
        DailyNecessityStrategy,
        GeneralMerchandiseStrategy,
        DefaultStrategy,
    )

    registry = StrategyRegistry()

    # 기존 if/elif 체인 순서 유지 (우선순위)
    registry.register(RamenStrategy())           # 9-1
    registry.register(TobaccoStrategy())         # 9-2
    registry.register(BeerStrategy())            # 9-3
    registry.register(SojuStrategy())            # 9-4
    registry.register(FoodStrategy())            # 9-5
    registry.register(PerishableStrategy())      # 9-6
    registry.register(BeverageStrategy())        # 9-7
    registry.register(FrozenIceStrategy())       # 9-8
    registry.register(InstantMealStrategy())     # 9-9
    registry.register(DessertStrategy())         # 9-10
    registry.register(SnackConfectionStrategy()) # 9-11
    registry.register(AlcoholGeneralStrategy())  # 9-12
    registry.register(DailyNecessityStrategy())  # 9-13
    registry.register(GeneralMerchandiseStrategy())  # 9-14

    # 폴백 (모든 mid_cd에 매칭)
    registry.set_default(DefaultStrategy())      # 9-15

    logger.info(f"카테고리 전략 레지스트리 생성: {len(registry._strategies)}개 등록")
    return registry
