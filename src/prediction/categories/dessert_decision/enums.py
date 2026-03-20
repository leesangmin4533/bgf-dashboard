"""디저트 판단 시스템 열거형 정의"""

from enum import Enum


class DessertCategory(str, Enum):
    """디저트 카테고리 분류

    A: 냉장디저트 (생크림빵, 도넛, 케이크, 마카롱 등) — 유통기한 2~5일
    B: 상온디저트-단기 (크로플, 팬케이크, 휘낭시에 등) — 유통기한 9~20일
    C: 상온디저트-장기 (와플, 양갱 등) — 유통기한 20일 초과
    D: 냉장젤리/푸딩 + 냉동디저트 — 유통기한 80일 이상
    """
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class DessertLifecycle(str, Enum):
    """상품 생애주기"""
    NEW = "new"
    GROWTH_DECLINE = "growth_decline"
    ESTABLISHED = "established"


class DessertDecisionType(str, Enum):
    """판단 결과"""
    KEEP = "KEEP"
    WATCH = "WATCH"
    REDUCE_ORDER = "REDUCE_ORDER"
    STOP_RECOMMEND = "STOP_RECOMMEND"


class JudgmentCycle(str, Enum):
    """판단 주기"""
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class FirstReceivingSource(str, Enum):
    """첫 입고일 판별 소스

    _resolve_first_receiving_date() 반환값으로 사용.
    str, Enum 상속이므로 DB TEXT 컬럼에 직접 저장 가능.
    """
    DETECTED = "detected_new_products"        # 센터매입 감지 시스템
    DAILY_SALES_SOLD = "daily_sales_sold"      # 첫 판매일 (sale_qty > 0)
    DAILY_SALES_BOUGHT = "daily_sales_bought"  # 첫 입고일 (buy_qty > 0, 판매 없는 상품)
    PRODUCTS = "products"                      # DB 등록일 (정상)
    PRODUCTS_BULK = "products_bulk"            # DB 등록일 (일괄등록 오염)
    NONE = "none"                              # 모든 소스 실패


# 카테고리별 판단 주기 매핑
CATEGORY_JUDGMENT_CYCLE = {
    DessertCategory.A: JudgmentCycle.WEEKLY,
    DessertCategory.B: JudgmentCycle.BIWEEKLY,
    DessertCategory.C: JudgmentCycle.MONTHLY,
    DessertCategory.D: JudgmentCycle.MONTHLY,
}
