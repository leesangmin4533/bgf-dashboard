"""
카테고리별 예측 모듈

각 카테고리별 동적 안전재고 로직을 분리하여 관리합니다.
- tobacco.py: 담배 (보루/전량소진 패턴, 상한선 30개)
- ramen.py: 라면 (회전율 기반, 상한선 5일)
- food.py: 푸드류 (유통기한 기반)
- beer.py: 맥주 (요일 패턴, 금/토 급증)
- soju.py: 소주 (요일 패턴, 금/토 급증)
- perishable.py: 소멸성 상품 (유통기한 기반, 요일계수 DB 학습)
- beverage.py: 음료류 (회전율 기반, 주말 증가, 요일계수 DB 학습)
- dessert.py: 디저트 (유통기한 기반 + 회전율 조정, 014 전용)
- snack_confection.py: 과자/간식 (회전율 기반, 안정적 수요, 015~020/029/030)
- alcohol_general.py: 일반주류 양주/와인 (주말 집중 패턴, 보수적 발주)
- frozen_ice.py: 냉동/아이스크림 (계절 가중치, 여름 품절방지/겨울 재고최소화)
- instant_meal.py: 즉석식품 (유통기한 그룹별 분기, fresh~long_life)
- daily_necessity.py: 생활용품 (결품 방지 최우선, 안정적 수요)
- general_merchandise.py: 잡화/비식품 (최소 재고 유지, 극소량 판매)
- default.py: 일반 상품 (기본 로직)
"""

from .tobacco import (
    is_tobacco_category,
    analyze_tobacco_pattern,
    calculate_tobacco_dynamic_safety,
    get_safety_stock_with_tobacco_pattern,
    TobaccoPatternResult,
    TOBACCO_DYNAMIC_SAFETY_CONFIG,
)

from .ramen import (
    is_ramen_category,
    analyze_ramen_pattern,
    calculate_ramen_dynamic_safety,
    get_safety_stock_with_ramen_pattern,
    RamenPatternResult,
    RAMEN_DYNAMIC_SAFETY_CONFIG,
)

from .food import (
    is_food_category,
    analyze_food_expiry_pattern,
    calculate_food_dynamic_safety,
    get_safety_stock_with_food_pattern,
    get_food_expiry_group,
    get_food_expiration_days,
    get_food_disuse_coefficient,
    get_dynamic_disuse_coefficient,
    get_delivery_waste_adjustment,
    calculate_delivery_gap_consumption,
    get_food_weekday_coefficient,
    get_food_weather_cross_coefficient,
    FoodExpiryResult,
    FOOD_CATEGORIES,
    FOOD_EXPIRY_SAFETY_CONFIG,
    FOOD_EXPIRY_FALLBACK,
    FOOD_DISUSE_COEFFICIENT,
    DELIVERY_WASTE_COEFFICIENT,
    DELIVERY_GAP_CONFIG,
    DELIVERY_TIME_DEMAND_RATIO,
    FOOD_WEATHER_CROSS_COEFFICIENTS,
    FOOD_ANALYSIS_DAYS,
)

from .beer import (
    is_beer_category,
    analyze_beer_pattern,
    calculate_beer_dynamic_safety,
    get_safety_stock_with_beer_pattern,
    get_beer_weekday_coef,
    get_beer_safety_days,
    BeerPatternResult,
    BEER_CATEGORIES,
    BEER_WEEKDAY_COEF,
    BEER_SAFETY_CONFIG,
)

from .soju import (
    is_soju_category,
    analyze_soju_pattern,
    calculate_soju_dynamic_safety,
    get_safety_stock_with_soju_pattern,
    get_soju_weekday_coef,
    get_soju_safety_days,
    SojuPatternResult,
    SOJU_CATEGORIES,
    SOJU_WEEKDAY_COEF,
    SOJU_SAFETY_CONFIG,
)

from .perishable import (
    is_perishable_category,
    analyze_perishable_pattern,
    calculate_perishable_dynamic_safety,
    get_safety_stock_with_perishable_pattern,
    PerishablePatternResult,
    PERISHABLE_CATEGORIES,
    PERISHABLE_EXPIRY_CONFIG,
    PERISHABLE_DYNAMIC_SAFETY_CONFIG,
    PERISHABLE_EXPIRY_FALLBACK,
)

from .beverage import (
    is_beverage_category,
    analyze_beverage_pattern,
    calculate_beverage_dynamic_safety,
    get_safety_stock_with_beverage_pattern,
    BeveragePatternResult,
    BEVERAGE_CATEGORIES,
    BEVERAGE_SAFETY_CONFIG,
    BEVERAGE_DYNAMIC_SAFETY_CONFIG,
)

from .dessert import (
    is_dessert_category,
    analyze_dessert_pattern,
    calculate_dessert_dynamic_safety,
    get_safety_stock_with_dessert_pattern,
    DessertPatternResult,
    DESSERT_TARGET_CATEGORIES,
    DESSERT_EXPIRY_SAFETY_CONFIG,
    DESSERT_TURNOVER_ADJUST,
)

from .snack_confection import (
    is_snack_confection_category,
    analyze_snack_confection_pattern,
    calculate_snack_confection_dynamic_safety,
    get_safety_stock_with_snack_confection_pattern,
    SnackConfectionPatternResult,
    SNACK_CONFECTION_TARGET_CATEGORIES,
    SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG,
    SNACK_SAFETY_CONFIG,
)

from .alcohol_general import (
    is_alcohol_general_category,
    analyze_alcohol_general_pattern,
    calculate_alcohol_general_dynamic_safety,
    get_safety_stock_with_alcohol_general_pattern,
    AlcoholGeneralPatternResult,
    ALCOHOL_GENERAL_TARGET_CATEGORIES,
    ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG,
    ALCOHOL_WEEKDAY_SAFETY,
)

from .frozen_ice import (
    is_frozen_ice_category,
    analyze_frozen_ice_pattern,
    calculate_frozen_ice_dynamic_safety,
    get_safety_stock_with_frozen_ice_pattern,
    FrozenIcePatternResult,
    FROZEN_ICE_CATEGORIES,
    FROZEN_ICE_WEEKDAY_COEF,
    FROZEN_SAFETY_CONFIG,
    FROZEN_ICE_DYNAMIC_SAFETY_CONFIG,
    DEFAULT_SEASONAL_COEF,
)

from .instant_meal import (
    is_instant_meal_category,
    analyze_instant_meal_pattern,
    calculate_instant_meal_dynamic_safety,
    get_safety_stock_with_instant_meal_pattern,
    InstantMealPatternResult,
    INSTANT_MEAL_CATEGORIES,
    INSTANT_MEAL_WEEKDAY_COEF,
    INSTANT_EXPIRY_GROUPS,
    INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG,
)

from .daily_necessity import (
    is_daily_necessity_category,
    analyze_daily_necessity_pattern,
    calculate_daily_necessity_dynamic_safety,
    get_safety_stock_with_daily_necessity_pattern,
    DailyNecessityPatternResult,
    DAILY_NECESSITY_CATEGORIES,
    DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG,
)

from .general_merchandise import (
    is_general_merchandise_category,
    analyze_general_merchandise_pattern,
    calculate_general_merchandise_dynamic_safety,
    get_safety_stock_with_general_merchandise_pattern,
    GeneralMerchandisePatternResult,
    GENERAL_MERCHANDISE_CATEGORIES,
    GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG,
)

from .default import (
    analyze_default_pattern,
    get_safety_stock_days,
    get_shelf_life_group,
    get_weekday_coefficient,
    DefaultPatternResult,
    SHELF_LIFE_CONFIG,
    WEEKDAY_COEFFICIENTS,
    DEFAULT_WEEKDAY_COEFFICIENTS,
    SAFETY_STOCK_MULTIPLIER,
    CATEGORY_FIXED_SAFETY_DAYS,
)

from .food_daily_cap import (
    apply_food_daily_cap,
    get_weekday_avg_sales,
    classify_items,
    select_items_with_cap,
    get_explore_failed_items,
    FOOD_DAILY_CAP_CONFIG,
)

__all__ = [
    # 담배
    'is_tobacco_category',
    'analyze_tobacco_pattern',
    'calculate_tobacco_dynamic_safety',
    'get_safety_stock_with_tobacco_pattern',
    'TobaccoPatternResult',
    'TOBACCO_DYNAMIC_SAFETY_CONFIG',
    # 라면
    'is_ramen_category',
    'analyze_ramen_pattern',
    'calculate_ramen_dynamic_safety',
    'get_safety_stock_with_ramen_pattern',
    'RamenPatternResult',
    'RAMEN_DYNAMIC_SAFETY_CONFIG',
    # 푸드류
    'is_food_category',
    'analyze_food_expiry_pattern',
    'calculate_food_dynamic_safety',
    'get_safety_stock_with_food_pattern',
    'get_food_expiry_group',
    'get_food_expiration_days',
    'get_food_disuse_coefficient',
    'get_dynamic_disuse_coefficient',
    'FoodExpiryResult',
    'FOOD_CATEGORIES',
    'FOOD_EXPIRY_SAFETY_CONFIG',
    'FOOD_EXPIRY_FALLBACK',
    'FOOD_DISUSE_COEFFICIENT',
    'get_delivery_waste_adjustment',
    'calculate_delivery_gap_consumption',
    'DELIVERY_WASTE_COEFFICIENT',
    'DELIVERY_GAP_CONFIG',
    'DELIVERY_TIME_DEMAND_RATIO',
    'FOOD_WEATHER_CROSS_COEFFICIENTS',
    'get_food_weekday_coefficient',
    'get_food_weather_cross_coefficient',
    'FOOD_ANALYSIS_DAYS',
    # (deprecated - use get_dynamic_disuse_coefficient instead)
    # 'get_food_disuse_coefficient',  -- still exported for backward compat
    # 맥주
    'is_beer_category',
    'analyze_beer_pattern',
    'calculate_beer_dynamic_safety',
    'get_safety_stock_with_beer_pattern',
    'get_beer_weekday_coef',
    'get_beer_safety_days',
    'BeerPatternResult',
    'BEER_CATEGORIES',
    'BEER_WEEKDAY_COEF',
    'BEER_SAFETY_CONFIG',
    # 소주
    'is_soju_category',
    'analyze_soju_pattern',
    'calculate_soju_dynamic_safety',
    'get_safety_stock_with_soju_pattern',
    'get_soju_weekday_coef',
    'get_soju_safety_days',
    'SojuPatternResult',
    'SOJU_CATEGORIES',
    'SOJU_WEEKDAY_COEF',
    'SOJU_SAFETY_CONFIG',
    # 소멸성 상품
    'is_perishable_category',
    'analyze_perishable_pattern',
    'calculate_perishable_dynamic_safety',
    'get_safety_stock_with_perishable_pattern',
    'PerishablePatternResult',
    'PERISHABLE_CATEGORIES',
    'PERISHABLE_EXPIRY_CONFIG',
    'PERISHABLE_DYNAMIC_SAFETY_CONFIG',
    'PERISHABLE_EXPIRY_FALLBACK',
    # 음료류
    'is_beverage_category',
    'analyze_beverage_pattern',
    'calculate_beverage_dynamic_safety',
    'get_safety_stock_with_beverage_pattern',
    'BeveragePatternResult',
    'BEVERAGE_CATEGORIES',
    'BEVERAGE_SAFETY_CONFIG',
    'BEVERAGE_DYNAMIC_SAFETY_CONFIG',
    # 디저트
    'is_dessert_category',
    'analyze_dessert_pattern',
    'calculate_dessert_dynamic_safety',
    'get_safety_stock_with_dessert_pattern',
    'DessertPatternResult',
    'DESSERT_TARGET_CATEGORIES',
    'DESSERT_EXPIRY_SAFETY_CONFIG',
    'DESSERT_TURNOVER_ADJUST',
    # 과자/간식
    'is_snack_confection_category',
    'analyze_snack_confection_pattern',
    'calculate_snack_confection_dynamic_safety',
    'get_safety_stock_with_snack_confection_pattern',
    'SnackConfectionPatternResult',
    'SNACK_CONFECTION_TARGET_CATEGORIES',
    'SNACK_CONFECTION_DYNAMIC_SAFETY_CONFIG',
    'SNACK_SAFETY_CONFIG',
    # 일반주류 (양주/와인)
    'is_alcohol_general_category',
    'analyze_alcohol_general_pattern',
    'calculate_alcohol_general_dynamic_safety',
    'get_safety_stock_with_alcohol_general_pattern',
    'AlcoholGeneralPatternResult',
    'ALCOHOL_GENERAL_TARGET_CATEGORIES',
    'ALCOHOL_GENERAL_DYNAMIC_SAFETY_CONFIG',
    'ALCOHOL_WEEKDAY_SAFETY',
    # 냉동/아이스크림
    'is_frozen_ice_category',
    'analyze_frozen_ice_pattern',
    'calculate_frozen_ice_dynamic_safety',
    'get_safety_stock_with_frozen_ice_pattern',
    'FrozenIcePatternResult',
    'FROZEN_ICE_CATEGORIES',
    'FROZEN_ICE_WEEKDAY_COEF',
    'FROZEN_SAFETY_CONFIG',
    'FROZEN_ICE_DYNAMIC_SAFETY_CONFIG',
    'DEFAULT_SEASONAL_COEF',
    # 즉석식품
    'is_instant_meal_category',
    'analyze_instant_meal_pattern',
    'calculate_instant_meal_dynamic_safety',
    'get_safety_stock_with_instant_meal_pattern',
    'InstantMealPatternResult',
    'INSTANT_MEAL_CATEGORIES',
    'INSTANT_MEAL_WEEKDAY_COEF',
    'INSTANT_EXPIRY_GROUPS',
    'INSTANT_MEAL_DYNAMIC_SAFETY_CONFIG',
    # 생활용품
    'is_daily_necessity_category',
    'analyze_daily_necessity_pattern',
    'calculate_daily_necessity_dynamic_safety',
    'get_safety_stock_with_daily_necessity_pattern',
    'DailyNecessityPatternResult',
    'DAILY_NECESSITY_CATEGORIES',
    'DAILY_NECESSITY_DYNAMIC_SAFETY_CONFIG',
    # 잡화/비식품
    'is_general_merchandise_category',
    'analyze_general_merchandise_pattern',
    'calculate_general_merchandise_dynamic_safety',
    'get_safety_stock_with_general_merchandise_pattern',
    'GeneralMerchandisePatternResult',
    'GENERAL_MERCHANDISE_CATEGORIES',
    'GENERAL_MERCHANDISE_DYNAMIC_SAFETY_CONFIG',
    # 기본
    'analyze_default_pattern',
    'get_safety_stock_days',
    'get_shelf_life_group',
    'get_weekday_coefficient',
    'DefaultPatternResult',
    'SHELF_LIFE_CONFIG',
    'WEEKDAY_COEFFICIENTS',
    'DEFAULT_WEEKDAY_COEFFICIENTS',
    'SAFETY_STOCK_MULTIPLIER',
    'CATEGORY_FIXED_SAFETY_DAYS',
    # 푸드류 총량 상한
    'apply_food_daily_cap',
    'get_weekday_avg_sales',
    'classify_items',
    'select_items_with_cap',
    'get_explore_failed_items',
    'FOOD_DAILY_CAP_CONFIG',
]
