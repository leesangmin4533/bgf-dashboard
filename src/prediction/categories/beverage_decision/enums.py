"""음료 판단 시스템 열거형 정의"""

from enum import Enum


class BeverageCategory(str, Enum):
    """음료 카테고리 분류

    A: 냉장 단기 유제품 (발효유, 요구르트, 흰우유) — 유통기한 6~30일
    B: 냉장 중기 음료 (가공유, 냉장커피, 냉장주스) — 유통기한 17~168일
    C: 상온 장기 음료 (탄산, 캔/병커피, 차 등 13개 소분류) — 유통기한 55~475일
    D: 초장기/비소모품 (생수, 얼음) — 유통기한 120일~inf
    """
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class BeverageLifecycle(str, Enum):
    """상품 생애주기"""
    NEW = "new"
    GROWTH_DECLINE = "growth_decline"
    ESTABLISHED = "established"


class BeverageDecisionType(str, Enum):
    """판단 결과"""
    KEEP = "KEEP"
    WATCH = "WATCH"
    STOP_RECOMMEND = "STOP_RECOMMEND"


class JudgmentCycle(str, Enum):
    """판단 주기"""
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class FirstReceivingSource(str, Enum):
    """첫 입고일 판별 소스"""
    DETECTED = "detected_new_products"
    DAILY_SALES_SOLD = "daily_sales_sold"
    DAILY_SALES_BOUGHT = "daily_sales_bought"
    PRODUCTS = "products"
    PRODUCTS_BULK = "products_bulk"
    NONE = "none"


# 카테고리별 판단 주기 매핑
CATEGORY_JUDGMENT_CYCLE = {
    BeverageCategory.A: JudgmentCycle.WEEKLY,
    BeverageCategory.B: JudgmentCycle.BIWEEKLY,
    BeverageCategory.C: JudgmentCycle.MONTHLY,
    BeverageCategory.D: JudgmentCycle.MONTHLY,
}

# 카테고리별 신상품 보호 기간 (주) — 설계서 §3.2
NEW_PRODUCT_WEEKS = {
    BeverageCategory.A: 3,
    BeverageCategory.B: 4,
    BeverageCategory.C: 6,
    BeverageCategory.D: 6,
}

# 성장/하락기 종료 시점 (주) — 카테고리별 차등
GROWTH_DECLINE_END_WEEKS = {
    BeverageCategory.A: 8,
    BeverageCategory.B: 10,
    BeverageCategory.C: 12,
    BeverageCategory.D: 12,
}
