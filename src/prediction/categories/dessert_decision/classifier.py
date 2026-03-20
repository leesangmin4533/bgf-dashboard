"""디저트 카테고리 분류기

소분류명(small_nm) 1차, 유통기한(expiration_days) 2차 기준으로 분류합니다.
3차 안전장치로 C/D 분류 후 유통기한이 짧으면 상향 조정합니다.

분류 규칙:
    1차 — small_nm 매핑:
        "냉장디저트"     → A (유통기한 무관)
        "냉장젤리,푸딩"  → D
        "냉동디저트"     → D
        "상온디저트"     → 2차 판단
        "시즌디저트"     → 2차 판단

    2차 — expiration_days (상온/시즌/미분류만):
        ≤ 20일  → B
        > 20일  → C

    3차 — 안전장치 (C/D 분류 후 유통기한 재검증):
        C/D + 유통기한 ≤ 20일  → A 상향
        C/D + 유통기한 21~79일 → B 상향

    폴백:
        소분류도 유통기한도 없으면 → A (보수적, 가장 엄격한 기준 적용)
"""

import logging
from typing import Optional

from .enums import DessertCategory

logger = logging.getLogger(__name__)


# 소분류명 → 카테고리 직접 매핑 (1차 분류)
SMALL_NM_CATEGORY_MAP = {
    "냉장디저트": DessertCategory.A,
    "냉장젤리,푸딩": DessertCategory.D,  # 실제 BGF 소분류명에 쉼표 포함
    "냉동디저트": DessertCategory.D,
}

# 상온디저트 B/C 분류 기준 (유통기한 일수)
ROOM_TEMP_THRESHOLD = 20

# 3차 안전장치 기준 (C/D 분류 후 유통기한 재검증)
SAFETY_SHORT_THRESHOLD = 20   # 이하 → A 상향
SAFETY_MEDIUM_THRESHOLD = 79  # 이하 → B 상향


def _apply_safety_override(
    category: DessertCategory,
    expiration_days: Optional[int],
    small_nm: Optional[str],
) -> DessertCategory:
    """3차 안전장치: C/D로 분류되었으나 유통기한이 짧으면 상향 조정.

    C/D + 유통기한 ≤ 20일 → A (냉장디저트급 엄격 관리)
    C/D + 유통기한 21~79일 → B (상온디저트-단기급 관리)
    """
    if category not in (DessertCategory.C, DessertCategory.D):
        return category

    if expiration_days is None or expiration_days <= 0:
        return category

    if expiration_days <= SAFETY_SHORT_THRESHOLD:
        logger.warning(
            "분류 안전장치 발동: %s(small_nm=%s, 유통기한=%d일) "
            "%s → A 상향 (유통기한 ≤ %d일)",
            category.value, small_nm, expiration_days,
            category.value, SAFETY_SHORT_THRESHOLD,
        )
        return DessertCategory.A

    if expiration_days <= SAFETY_MEDIUM_THRESHOLD:
        logger.warning(
            "분류 안전장치 발동: %s(small_nm=%s, 유통기한=%d일) "
            "%s → B 상향 (유통기한 ≤ %d일)",
            category.value, small_nm, expiration_days,
            category.value, SAFETY_MEDIUM_THRESHOLD,
        )
        return DessertCategory.B

    return category


def classify_dessert_category(
    small_nm: Optional[str],
    expiration_days: Optional[int],
) -> DessertCategory:
    """소분류명 1차, 유통기한 2차, 안전장치 3차 기준으로 카테고리 분류.

    Args:
        small_nm: 소분류명 (예: "냉장디저트", "상온디저트")
        expiration_days: 유통기한 일수

    Returns:
        DessertCategory (A/B/C/D)
    """
    # 1차: 소분류명 기반 직접 매핑
    if small_nm and small_nm in SMALL_NM_CATEGORY_MAP:
        category = SMALL_NM_CATEGORY_MAP[small_nm]
        return _apply_safety_override(category, expiration_days, small_nm)

    # 2차: 상온디저트 / 시즌디저트 / 미분류 → 유통기한 기반
    if expiration_days is not None and expiration_days > 0:
        if expiration_days <= ROOM_TEMP_THRESHOLD:
            return DessertCategory.B
        else:
            return DessertCategory.C

    # 소분류명이 "상온디저트" / "시즌디저트"이지만 유통기한 없음
    if small_nm and small_nm in ("상온디저트", "시즌디저트"):
        return DessertCategory.B  # 상온 기본값

    # 폴백: 둘 다 없으면 A (보수적 — 가장 엄격한 기준 적용)
    return DessertCategory.A
