"""
비인기상품 교체 전략

신상품 도입 시 동일 카테고리의 비인기상품을 발주정지 대상으로 선정
"""

from typing import List, Dict, Tuple
from src.utils.logger import get_logger

logger = get_logger(__name__)


def select_items_to_suspend(
    category_items: List[Dict],
    new_count: int,
) -> List[Dict]:
    """동일 카테고리 비인기상품 정지 대상 선정

    일평균 판매량이 가장 낮은 상품부터 new_count개 선정

    Args:
        category_items: 동일 카테고리 기존 상품 리스트
            각 항목: {item_cd, item_nm, daily_avg, mid_cd, ...}
        new_count: 도입할 신상품 수

    Returns:
        정지 대상 상품 리스트 (daily_avg 오름차순)
    """
    if new_count <= 0 or not category_items:
        return []

    sorted_items = sorted(
        category_items,
        key=lambda x: x.get("daily_avg", 0),
    )

    # 일평균 0.3 미만인 상품만 정지 대상 (너무 많이 정지하지 않도록)
    candidates = [
        item for item in sorted_items
        if item.get("daily_avg", 0) < 0.3
    ]

    return candidates[:new_count]


def prioritize_missing_items(
    missing_items: List[Dict],
    score_gap: float,
) -> List[Dict]:
    """미도입 상품 발주 우선순위 결정

    발주가능 상품을 먼저, 그 안에서 소분류 다양성을 고려하여 정렬

    Args:
        missing_items: 미도입 상품 리스트
        score_gap: 목표 점수까지 남은 격차

    Returns:
        우선순위 정렬된 상품 리스트
    """
    orderable = [
        item for item in missing_items
        if item.get("ord_pss_nm") in ("발주가능", "가능")
    ]
    not_orderable = [
        item for item in missing_items
        if item.get("ord_pss_nm") not in ("발주가능", "가능")
    ]

    # 발주가능 상품을 소분류별로 분산 배치
    by_small = {}
    for item in orderable:
        key = item.get("small_nm", "기타")
        by_small.setdefault(key, []).append(item)

    prioritized = []
    keys = list(by_small.keys())
    idx = 0
    while any(by_small.values()):
        key = keys[idx % len(keys)]
        if by_small[key]:
            prioritized.append(by_small[key].pop(0))
        idx += 1
        # 빈 키 제거
        keys = [k for k in keys if by_small.get(k)]
        if not keys:
            break

    return prioritized + not_orderable


def group_new_items_by_category(
    items: List[Dict],
) -> Dict[str, List[Dict]]:
    """미도입 상품을 소분류별로 그룹화"""
    groups: Dict[str, List[Dict]] = {}
    for item in items:
        key = item.get("small_nm", "기타")
        groups.setdefault(key, []).append(item)
    return groups
