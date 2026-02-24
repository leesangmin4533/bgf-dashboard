"""
OrderAdjuster -- 발주 수량 조정 순수 로직

improved_predictor.py와 auto_order.py에서 추출한 수량 조정 관련 순수 함수들.
I/O 의존성 없이 입력 데이터만으로 계산합니다.

포함 로직:
- 발주 단위 올림/내림 (round_to_order_unit)
- 과잉발주 보정 (check_overstock_on_round_up)
- 발주 규칙 적용 (apply_order_rules)
- 미입고/재고 차감 (adjust_for_pending_stock)

Usage:
    from src.domain.order.order_adjuster import (
        round_to_order_unit,
        apply_order_rules,
        adjust_for_pending_stock,
    )
"""

import math
from typing import Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


def round_to_order_unit(
    order_qty: float,
    order_unit: int,
    *,
    has_dedicated_handler: bool = True,
    safety_stock: float = 0.0,
    current_stock: int = 0,
    adjusted_prediction: float = 0.0,
) -> int:
    """발주 단위 올림 + 과잉발주 보정

    Args:
        order_qty: 원 발주량
        order_unit: 발주 배수 (예: 1=낱개, 6=6개묶음)
        has_dedicated_handler: 전용 카테고리 핸들러 존재 여부
        safety_stock: 안전재고 (과잉발주 보정에 사용)
        current_stock: 현재 재고 (과잉발주 보정에 사용)
        adjusted_prediction: 조정 예측량 (과잉발주 보정에 사용)

    Returns:
        올림된 발주 수량 (int)
    """
    if order_qty <= 0 or order_unit <= 1:
        return max(0, int(order_qty))

    # 올림: ((qty + unit - 1) // unit) * unit
    unit_qty = ((int(order_qty) + order_unit - 1) // order_unit) * order_unit

    # 전용 핸들러 없는 카테고리만 과잉발주 보정 적용
    if not has_dedicated_handler:
        surplus = unit_qty - order_qty
        if (surplus >= safety_stock
                and current_stock + surplus >= adjusted_prediction + safety_stock):
            return 0
        return unit_qty

    return unit_qty


def apply_order_rules(
    need_qty: float,
    *,
    mid_cd: str = "",
    expiration_days: int = 999,
    weekday: int = 0,
    current_stock: int = 0,
    daily_avg: float = 0.0,
    food_categories: tuple = (),
    rules: Optional[Dict[str, Any]] = None,
    prediction_params: Optional[Dict[str, Any]] = None,
) -> int:
    """발주 조정 규칙 적용

    금요일 부스트, 폐기 방지, 재고 과다 방지 등의 규칙을 순차 적용.

    Args:
        need_qty: 기본 필요량
        mid_cd: 중분류 코드
        expiration_days: 유통기한 (일)
        weekday: 요일 (0=월, 6=일)
        current_stock: 현재 재고
        daily_avg: 일평균 판매량
        food_categories: 푸드류 mid_cd 튜플
        rules: ORDER_ADJUSTMENT_RULES 딕셔너리
        prediction_params: PREDICTION_PARAMS 딕셔너리

    Returns:
        조정된 발주 수량 (int)
    """
    if rules is None:
        rules = {}
    if prediction_params is None:
        prediction_params = {}

    order_qty = need_qty

    # Priority 1.5: 푸드류 최소 발주량 보장 (재고 없을 때)
    if mid_cd in food_categories:
        if current_stock == 0 and daily_avg >= 0.2:
            return 1

    # 1. 금요일 부스트 (주류, 담배)
    friday_cfg = rules.get("friday_boost", {})
    if friday_cfg.get("enabled"):
        if weekday == 4 and mid_cd in friday_cfg.get("categories", []):
            order_qty *= friday_cfg.get("boost_rate", 1.0)

    # 2. 폐기 방지 (초단기 상품)
    disuse_cfg = rules.get("disuse_prevention", {})
    if disuse_cfg.get("enabled"):
        if expiration_days <= disuse_cfg.get("shelf_life_threshold", 3):
            order_qty *= disuse_cfg.get("reduction_rate", 1.0)

    # 3. 재고 과다 방지
    overstock_cfg = rules.get("overstock_prevention", {})
    if overstock_cfg.get("enabled"):
        if daily_avg > 0:
            stock_days = current_stock / daily_avg
            if stock_days >= overstock_cfg.get("stock_days_threshold", 7):
                if overstock_cfg.get("skip_order"):
                    return 0

    # 4. 반올림 + 최소 발주 임계값
    min_order_threshold = prediction_params.get("min_order_threshold", 0.5)

    if order_qty < min_order_threshold:
        return 0
    elif order_qty < 1.0:
        return 1
    else:
        threshold = prediction_params.get("round_up_threshold", 0.5)
        if order_qty - int(order_qty) >= threshold:
            return int(order_qty) + 1
        return int(order_qty)


def adjust_for_pending_stock(
    original_qty: int,
    original_stock: int,
    original_pending: int,
    new_stock: int,
    new_pending: int,
    *,
    is_protected: bool = False,
    min_order_qty: int = 1,
) -> int:
    """미입고/재고 변동에 따른 발주량 조정

    Args:
        original_qty: 원래 발주량
        original_stock: 원래 재고
        original_pending: 원래 미입고
        new_stock: 새 재고
        new_pending: 새 미입고
        is_protected: FORCE/URGENT 보호 여부
        min_order_qty: 최소 발주량

    Returns:
        조정된 발주 수량 (int)
    """
    pending_diff = new_pending - original_pending
    stock_diff = new_stock - original_stock
    adjustment = pending_diff + stock_diff
    new_qty = max(0, original_qty - adjustment)

    if new_qty < min_order_qty:
        if is_protected:
            return 1  # FORCE/URGENT 최소 보장
        return 0

    return new_qty


def check_skip_by_max_stock(
    *,
    skip_order: bool = False,
    available_space: int = 0,
    need_qty: float = 0.0,
) -> float:
    """상한선 체크로 발주량 조정

    Args:
        skip_order: 카테고리 패턴에서 skip 판정 여부
        available_space: 여유 공간 (담배 등, 0이면 미사용)
        need_qty: 현재 필요량

    Returns:
        조정된 필요량
    """
    if skip_order:
        return 0.0
    if available_space > 0 and need_qty > available_space:
        return float(available_space)
    return need_qty
