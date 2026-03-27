"""음료 판단 시스템 데이터 모델"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class BeverageItemContext:
    """판단 대상 음료 상품의 컨텍스트"""
    item_cd: str
    item_nm: str = ""
    mid_cd: str = ""
    small_nm: Optional[str] = None
    expiration_days: Optional[int] = None
    sell_price: int = 0
    margin_rate: Optional[float] = None
    first_receiving_date: Optional[str] = None
    first_receiving_source: str = ""


@dataclass
class BeverageSalesMetrics:
    """판단 기간의 판매 집계"""
    period_start: str = ""
    period_end: str = ""
    total_order_qty: int = 0
    total_sale_qty: int = 0
    total_disuse_qty: int = 0
    sale_amount: int = 0
    disuse_amount: int = 0
    sale_rate: float = 0.0
    # 매대효율지표: 해당 상품 주간판매량 / 소분류 중위 주간판매량
    shelf_efficiency: float = 0.0
    small_cd_median_sale_qty: float = 0.0
    category_avg_sale_qty: float = 0.0
    prev_period_sale_qty: int = 0
    sale_trend_pct: float = 0.0
    consecutive_low_weeks: int = 0
    consecutive_zero_months: int = 0
    weekly_sale_rates: List[float] = field(default_factory=list)
