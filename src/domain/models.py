"""
도메인 값 객체 (Value Objects)

비즈니스 로직에서 사용하는 데이터 구조를 정의합니다.
I/O 의존성 없이 순수 데이터 구조만 포함합니다.

Phase 5에서 improved_predictor.py의 PredictionResult,
auto_order.py의 OrderItem 등을 여기로 추출합니다.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class PredictionResult:
    """예측 결과

    improved_predictor.py의 PredictionResult를 여기로 이동 예정.
    현재는 인터페이스만 정의합니다.
    """
    item_cd: str
    item_nm: str = ""
    mid_cd: str = ""
    mid_nm: str = ""
    daily_avg: float = 0.0
    predicted_qty: int = 0
    safety_stock: float = 0.0
    current_stock: int = 0
    pending_qty: int = 0
    order_qty: int = 0
    order_unit: int = 1
    order_unit_nm: str = "낱개"
    eval_decision: str = ""     # SKIP, FORCE, URGENT, NORMAL
    eval_reason: str = ""
    category_strategy: str = ""  # 사용된 Strategy 이름
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderItem:
    """발주 항목"""
    item_cd: str
    item_nm: str = ""
    qty: int = 0
    order_unit: int = 1
    order_unit_nm: str = "낱개"
    mid_cd: str = ""
    mid_nm: str = ""
    eval_decision: str = ""
    source: str = ""  # "prediction", "force", "urgent"


@dataclass
class CostInfo:
    """비용 최적화 정보"""
    item_cd: str
    margin_rate: float = 0.0
    margin_level: str = "unknown"
    turnover_level: str = "unknown"
    category_share: float = 0.0
    share_bonus: float = 0.0
    composite_score: float = 1.0
    disuse_modifier: float = 0.0
    skip_offset: float = 0.0


@dataclass
class EvalDecision:
    """사전 평가 결정"""
    item_cd: str
    decision: str = "NORMAL"  # SKIP, FORCE, URGENT, NORMAL
    reason: str = ""
    confidence: float = 0.0
    daily_avg: float = 0.0
    cost_info: Optional[CostInfo] = None


@dataclass
class FlowResult:
    """DailyOrderFlow 실행 결과"""
    store_id: str
    total_predicted: int = 0
    total_ordered: int = 0
    skipped: int = 0
    failed: int = 0
    success: bool = False
    errors: List[str] = field(default_factory=list)
    predictions: List[PredictionResult] = field(default_factory=list)
    order_items: List[OrderItem] = field(default_factory=list)
    fail_reasons: Dict[str, Any] = field(default_factory=dict)
