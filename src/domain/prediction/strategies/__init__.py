"""
카테고리별 예측 전략 모듈

15개 카테고리 Strategy 클래스를 제공합니다.
각 Strategy는 기존 카테고리 모듈(src/prediction/categories/)의 함수를 래핑합니다.
알고리즘은 불변 -- 구조적 래퍼일 뿐입니다.

Usage:
    from src.domain.prediction.strategies import BeerStrategy, DefaultStrategy
    from src.domain.prediction.strategy_registry import create_default_registry

    registry = create_default_registry()
    strategy = registry.get_strategy("049")  # → BeerStrategy
"""

# --- 주류 ---
from .beer import BeerStrategy
from .soju import SojuStrategy
from .alcohol_general import AlcoholGeneralStrategy

# --- 담배 ---
from .tobacco import TobaccoStrategy

# --- 식품 (유통기한 민감) ---
from .food import FoodStrategy
from .perishable import PerishableStrategy
from .dessert import DessertStrategy
from .instant_meal import InstantMealStrategy

# --- 라면/면류 ---
from .ramen import RamenStrategy

# --- 음료/냉동 ---
from .beverage import BeverageStrategy
from .frozen_ice import FrozenIceStrategy

# --- 과자/간식/생활용품/잡화 ---
from .snack_confection import SnackConfectionStrategy
from .daily_necessity import DailyNecessityStrategy
from .general_merchandise import GeneralMerchandiseStrategy

# --- 기본 (폴백) ---
from .default import DefaultStrategy

__all__ = [
    "BeerStrategy",
    "SojuStrategy",
    "AlcoholGeneralStrategy",
    "TobaccoStrategy",
    "FoodStrategy",
    "PerishableStrategy",
    "DessertStrategy",
    "InstantMealStrategy",
    "RamenStrategy",
    "BeverageStrategy",
    "FrozenIceStrategy",
    "SnackConfectionStrategy",
    "DailyNecessityStrategy",
    "GeneralMerchandiseStrategy",
    "DefaultStrategy",
]
