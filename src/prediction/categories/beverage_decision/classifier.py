"""음료 카테고리 분류기

4단계 분류 로직:
    1차 — 중분류코드 기반 (100% 커버)
        046 → A, 041/048 → D, 나머지 → 소분류로 세분화
    2차 — 소분류명 매핑 (있으면)
        냉장커피 → B, 캔/병커피 → C 등
    2.5차 — 유통기한 NULL 폴백
        MID_CD_DEFAULT_EXPIRY 적용
    3차 — 안전장치
        C/D + 유통기한 ≤ 20일 → A 상향
        C/D + 유통기한 21~60일 → B 상향
"""

import re
import logging
from typing import Optional

from .enums import BeverageCategory
from .constants import (
    MID_CD_DEFAULT_CATEGORY,
    SMALL_NM_CATEGORY_MAP,
    MID_CD_DEFAULT_EXPIRY,
    SAFETY_SHORT_THRESHOLD,
    SAFETY_MEDIUM_THRESHOLD,
    COLD_COFFEE_KEYWORDS,
    CAN_COFFEE_KEYWORDS,
    PET_PATTERN,
    PLAIN_MILK_KEYWORDS,
    COLD_JUICE_KEYWORDS,
)

logger = logging.getLogger(__name__)


def _apply_safety_override(
    category: BeverageCategory,
    expiration_days: Optional[int],
    mid_cd: Optional[str],
    small_nm: Optional[str],
) -> BeverageCategory:
    """3차 안전장치: C/D로 분류되었으나 유통기한이 짧으면 상향 조정."""
    if category not in (BeverageCategory.C, BeverageCategory.D):
        return category

    if expiration_days is None or expiration_days <= 0:
        return category

    if expiration_days <= SAFETY_SHORT_THRESHOLD:
        logger.warning(
            "음료 분류 안전장치: %s(mid=%s, small=%s, 유통기한=%d일) → A 상향",
            category.value, mid_cd, small_nm, expiration_days,
        )
        return BeverageCategory.A

    if expiration_days <= SAFETY_MEDIUM_THRESHOLD:
        logger.warning(
            "음료 분류 안전장치: %s(mid=%s, small=%s, 유통기한=%d일) → B 상향",
            category.value, mid_cd, small_nm, expiration_days,
        )
        return BeverageCategory.B

    return category


def _classify_by_keyword(mid_cd: str, item_nm: str) -> Optional[str]:
    """소분류 없을 때 상품명 키워드로 냉장/상온 분류 (§2.4).

    Returns:
        카테고리 문자열 ('A'~'D') 또는 None (키워드 매칭 실패)
    """
    if not item_nm:
        return None

    name_upper = item_nm.upper()

    if mid_cd == '042':
        # 커피: 냉장(B) vs 캔/병(C)
        for kw in COLD_COFFEE_KEYWORDS:
            if kw in item_nm:
                return 'B'
        for kw in CAN_COFFEE_KEYWORDS:
            if kw in name_upper:
                return 'C'
        if re.search(PET_PATTERN, name_upper):
            return 'C'
        return None  # 폴백은 호출부에서 처리

    elif mid_cd == '047':
        # 우유: 흰우유(A) vs 가공유(B)
        for kw in PLAIN_MILK_KEYWORDS:
            if kw in item_nm:
                return 'A'
        return 'B'  # 실측: 소분류 없는 27개 전부 가공유

    elif mid_cd == '039':
        # 주스: 냉장(B) vs 과즙(C)
        for kw in COLD_JUICE_KEYWORDS:
            if kw in item_nm:
                return 'B'
        return None

    return None


def classify_beverage_category(
    mid_cd: Optional[str],
    small_nm: Optional[str],
    expiration_days: Optional[int],
    item_nm: Optional[str] = None,
) -> BeverageCategory:
    """음료 카테고리 분류 (4단계).

    Args:
        mid_cd: 중분류코드 (039~048)
        small_nm: 소분류명
        expiration_days: 유통기한 일수 (None 가능)
        item_nm: 상품명 (키워드 분류용)

    Returns:
        BeverageCategory (A/B/C/D)
    """
    # 폴백: 중분류코드 없으면 C
    if not mid_cd:
        return BeverageCategory.C

    # 2.5차: 유통기한 NULL → 폴백 적용
    effective_expiry = expiration_days
    if effective_expiry is None and mid_cd in MID_CD_DEFAULT_EXPIRY:
        effective_expiry = MID_CD_DEFAULT_EXPIRY[mid_cd]

    # 2차: 소분류명 매핑
    if small_nm and small_nm in SMALL_NM_CATEGORY_MAP:
        cat_str = SMALL_NM_CATEGORY_MAP[small_nm]
        category = BeverageCategory(cat_str)
        return _apply_safety_override(category, effective_expiry, mid_cd, small_nm)

    # 소분류 없음 → 키워드 보조 분류 (042, 047, 039)
    if not small_nm and item_nm and mid_cd in ('042', '047', '039'):
        kw_cat = _classify_by_keyword(mid_cd, item_nm)
        if kw_cat:
            category = BeverageCategory(kw_cat)
            return _apply_safety_override(category, effective_expiry, mid_cd, small_nm)

    # 1차: 중분류코드 기본 카테고리
    default_cat = MID_CD_DEFAULT_CATEGORY.get(mid_cd, 'C')
    category = BeverageCategory(default_cat)
    return _apply_safety_override(category, effective_expiry, mid_cd, small_nm)
