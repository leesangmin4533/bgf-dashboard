"""
OrderFilter -- 발주 항목 필터링 순수 로직

auto_order.py에서 추출한 필터링 관련 순수 함수들.
I/O 의존성 없이 입력 데이터(set)만으로 필터링합니다.

포함 로직:
- 미취급 상품 제외 (filter_unavailable)
- CUT 상품 제외 (filter_cut_items)
- 자동발주/스마트발주 제외 (filter_auto_order, filter_smart_order)
- 통합 필터 (apply_all_filters)

Usage:
    from src.domain.order.order_filter import apply_all_filters

    filtered = apply_all_filters(
        order_list,
        unavailable_items=unavailable_set,
        cut_items=cut_set,
        auto_order_items=auto_set,
        smart_order_items=smart_set,
        exclude_auto=True,
        exclude_smart=True,
    )
"""

from typing import List, Dict, Any, Set, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


def filter_unavailable(
    order_list: List[Dict[str, Any]],
    unavailable_items: Set[str],
) -> List[Dict[str, Any]]:
    """미취급 상품 제외

    Args:
        order_list: 발주 목록
        unavailable_items: 미취급 상품 코드 set

    Returns:
        필터링된 발주 목록
    """
    if not unavailable_items:
        return order_list

    before = len(order_list)
    result = [item for item in order_list
              if item.get("item_cd") not in unavailable_items]
    excluded = before - len(result)
    if excluded > 0:
        logger.info(f"점포 미취급 {excluded}개 상품 제외")
    return result


def filter_cut_items(
    order_list: List[Dict[str, Any]],
    cut_items: Set[str],
) -> List[Dict[str, Any]]:
    """발주중지(CUT) 상품 제외

    Args:
        order_list: 발주 목록
        cut_items: CUT 상품 코드 set

    Returns:
        필터링된 발주 목록
    """
    if not cut_items:
        return order_list

    before = len(order_list)
    result = [item for item in order_list
              if item.get("item_cd") not in cut_items]
    excluded = before - len(result)
    if excluded > 0:
        logger.info(f"발주중지(CUT) {excluded}개 상품 제외")
    return result


def filter_auto_order(
    order_list: List[Dict[str, Any]],
    auto_order_items: Set[str],
    *,
    enabled: bool = True,
) -> List[Dict[str, Any]]:
    """자동발주(본부관리) 상품 제외

    Args:
        order_list: 발주 목록
        auto_order_items: 자동발주 상품 코드 set
        enabled: 대시보드 설정에 의한 활성화 여부

    Returns:
        필터링된 발주 목록
    """
    if not enabled:
        logger.info("자동발주 상품 제외 OFF (대시보드 설정) - 제외 안 함")
        return order_list
    if not auto_order_items:
        return order_list

    before = len(order_list)
    result = [item for item in order_list
              if item.get("item_cd") not in auto_order_items]
    excluded = before - len(result)
    if excluded > 0:
        logger.info(f"자동발주(본부관리) {excluded}개 상품 제외")
    return result


def filter_smart_order(
    order_list: List[Dict[str, Any]],
    smart_order_items: Set[str],
    *,
    enabled: bool = True,
) -> List[Dict[str, Any]]:
    """스마트발주(본부관리) 상품 제외

    Args:
        order_list: 발주 목록
        smart_order_items: 스마트발주 상품 코드 set
        enabled: 대시보드 설정에 의한 활성화 여부

    Returns:
        필터링된 발주 목록
    """
    if not enabled:
        logger.info("스마트발주 상품 제외 OFF (대시보드 설정) - 제외 안 함")
        return order_list
    if not smart_order_items:
        return order_list

    before = len(order_list)
    result = [item for item in order_list
              if item.get("item_cd") not in smart_order_items]
    excluded = before - len(result)
    if excluded > 0:
        logger.info(f"스마트발주(본부관리) {excluded}개 상품 제외")
    return result


def filter_stopped_items(
    order_list: List[Dict[str, Any]],
    stopped_items: Set[str],
) -> List[Dict[str, Any]]:
    """발주정지 상품 제외

    Args:
        order_list: 발주 목록
        stopped_items: 발주정지 상품 코드 set

    Returns:
        필터링된 발주 목록
    """
    if not stopped_items:
        return order_list

    before = len(order_list)
    result = [item for item in order_list
              if item.get("item_cd") not in stopped_items]
    excluded = before - len(result)
    if excluded > 0:
        logger.info(f"발주정지 {excluded}개 상품 제외")
    return result


def apply_all_filters(
    order_list: List[Dict[str, Any]],
    *,
    unavailable_items: Optional[Set[str]] = None,
    cut_items: Optional[Set[str]] = None,
    auto_order_items: Optional[Set[str]] = None,
    smart_order_items: Optional[Set[str]] = None,
    exclude_auto: bool = True,
    exclude_smart: bool = True,
) -> List[Dict[str, Any]]:
    """전체 필터 순차 적용

    4개 필터를 순서대로 적용합니다:
    1. 미취급 상품 제외
    2. CUT 상품 제외
    3. 자동발주 상품 제외 (설정에 따라)
    4. 스마트발주 상품 제외 (설정에 따라)

    Args:
        order_list: 원본 발주 목록
        unavailable_items: 미취급 상품 코드 set
        cut_items: CUT 상품 코드 set
        auto_order_items: 자동발주 상품 코드 set
        smart_order_items: 스마트발주 상품 코드 set
        exclude_auto: 자동발주 제외 활성화 여부
        exclude_smart: 스마트발주 제외 활성화 여부

    Returns:
        모든 필터가 적용된 발주 목록
    """
    result = order_list

    result = filter_unavailable(result, unavailable_items or set())
    result = filter_cut_items(result, cut_items or set())
    result = filter_auto_order(
        result, auto_order_items or set(), enabled=exclude_auto)
    result = filter_smart_order(
        result, smart_order_items or set(), enabled=exclude_smart)

    return result
