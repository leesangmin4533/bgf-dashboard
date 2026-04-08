"""
BundleClassifier — 묶음 의심 카테고리 동적 분류기

bundle-suspect-dynamic-master 의 Domain 레이어. 순수 함수.
product_details 통계로부터 mid_cd 를 STRONG/WEAK/UNKNOWN/NORMAL 로 분류한다.

설계: docs/02-design/features/bundle-suspect-dynamic-master.design.md
토론: data/discussions/20260408-bundle-dynamic-master/03-최종-리포트.md
Issue-Chain: order-execution#bundle-suspect-dynamic-master
"""

from dataclasses import dataclass
from enum import Enum

# 임계값 상수 (토론 권고 A안 — 70%/50% 이중)
STRONG_THRESHOLD = 70.0
WEAK_THRESHOLD = 50.0
NULL_MAX = 30.0
MIN_TOTAL = 5


class BundleClassification(str, Enum):
    """묶음 카테고리 분류 라벨"""

    STRONG = "STRONG"    # bundle_pct >= 70%, 즉시 BLOCK 대상
    WEAK = "WEAK"        # 50% <= bundle_pct < 70%, BLOCK + WARN 로그
    UNKNOWN = "UNKNOWN"  # NULL>30% or total<5, fallback 위임
    NORMAL = "NORMAL"    # < 50%, 가드 미적용


@dataclass(frozen=True)
class BundleStats:
    """mid_cd 별 묶음 통계 (도메인 값 객체)

    product_details 의 order_unit_qty 분포를 mid_cd 단위로 집계한 결과.
    """

    mid_cd: str       # 3자리 제로패딩
    total: int        # product_details row 수
    bundle_n: int     # order_unit_qty > 1 row 수
    null_n: int       # order_unit_qty IS NULL row 수
    unit1_n: int      # order_unit_qty = 1 row 수

    @property
    def bundle_pct(self) -> float:
        """묶음 비율 (%)"""
        return 100.0 * self.bundle_n / self.total if self.total else 0.0

    @property
    def null_ratio(self) -> float:
        """NULL 비율 (%) — 통계 신뢰도 측정"""
        return 100.0 * self.null_n / self.total if self.total else 0.0


def classify(stats: BundleStats) -> BundleClassification:
    """순수 함수: BundleStats → BundleClassification

    분류 우선순위:
    1. 샘플 부족 (total < MIN_TOTAL) → UNKNOWN
    2. 통계 신뢰도 낮음 (null_ratio > NULL_MAX) → UNKNOWN
    3. 강한 의심 (bundle_pct >= STRONG_THRESHOLD) → STRONG
    4. 약한 의심 (bundle_pct >= WEAK_THRESHOLD) → WEAK
    5. 그 외 → NORMAL

    Args:
        stats: mid 별 BundleStats

    Returns:
        BundleClassification 라벨
    """
    if stats.total < MIN_TOTAL:
        return BundleClassification.UNKNOWN
    if stats.null_ratio > NULL_MAX:
        return BundleClassification.UNKNOWN
    if stats.bundle_pct >= STRONG_THRESHOLD:
        return BundleClassification.STRONG
    if stats.bundle_pct >= WEAK_THRESHOLD:
        return BundleClassification.WEAK
    return BundleClassification.NORMAL
