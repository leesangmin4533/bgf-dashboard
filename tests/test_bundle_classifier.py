"""
test_bundle_classifier — bundle_classifier 단위 테스트

Issue-Chain: order-execution#bundle-suspect-dynamic-master
설계: docs/02-design/features/bundle-suspect-dynamic-master.design.md §8.1
"""

import pytest

from src.domain.order.bundle_classifier import (
    BundleClassification,
    BundleStats,
    classify,
    STRONG_THRESHOLD,
    WEAK_THRESHOLD,
    NULL_MAX,
    MIN_TOTAL,
)


# ─────────────────────────────────────────────────
# BundleStats property 테스트
# ─────────────────────────────────────────────────


class TestBundleStatsProperties:
    def test_bundle_pct_normal(self):
        s = BundleStats(mid_cd="021", total=130, bundle_n=115, null_n=3, unit1_n=12)
        assert round(s.bundle_pct, 1) == 88.5

    def test_bundle_pct_zero_total(self):
        s = BundleStats(mid_cd="999", total=0, bundle_n=0, null_n=0, unit1_n=0)
        assert s.bundle_pct == 0.0

    def test_null_ratio_normal(self):
        s = BundleStats(mid_cd="030", total=62, bundle_n=20, null_n=36, unit1_n=6)
        assert round(s.null_ratio, 1) == 58.1

    def test_null_ratio_zero_total(self):
        s = BundleStats(mid_cd="999", total=0, bundle_n=0, null_n=0, unit1_n=0)
        assert s.null_ratio == 0.0


# ─────────────────────────────────────────────────
# classify() 분류 우선순위
# ─────────────────────────────────────────────────


class TestClassify:
    # ── 1. 샘플 부족 (total < MIN_TOTAL) ──
    def test_total_below_min_returns_unknown(self):
        s = BundleStats(mid_cd="x", total=4, bundle_n=4, null_n=0, unit1_n=0)
        # total=4 < 5 → UNKNOWN (bundle_pct 100% 여도)
        assert classify(s) == BundleClassification.UNKNOWN

    def test_total_at_min_normal(self):
        s = BundleStats(mid_cd="x", total=5, bundle_n=4, null_n=0, unit1_n=1)
        # total=5 (경계 통과), bundle_pct=80% → STRONG
        assert classify(s) == BundleClassification.STRONG

    # ── 2. NULL 비율 (null_ratio > NULL_MAX) ──
    def test_null_ratio_above_max_returns_unknown(self):
        # 030: 36/62 = 58% NULL → UNKNOWN
        s = BundleStats(mid_cd="030", total=62, bundle_n=20, null_n=36, unit1_n=6)
        assert classify(s) == BundleClassification.UNKNOWN

    def test_null_ratio_at_30_normal_path(self):
        # 30/100 = 30% (경계, NOT > 30) → bundle_pct 평가
        s = BundleStats(mid_cd="x", total=100, bundle_n=80, null_n=30, unit1_n=0)
        # > 가 아닌 >= 30 은 통과 (NULL_MAX = 30, 조건은 > 30)
        assert classify(s) == BundleClassification.STRONG

    def test_null_ratio_just_above_30_unknown(self):
        # 31/100 = 31% → UNKNOWN
        s = BundleStats(mid_cd="x", total=100, bundle_n=80, null_n=31, unit1_n=0)
        assert classify(s) == BundleClassification.UNKNOWN

    # ── 3. STRONG 임계 (>= 70%) ──
    def test_strong_at_70_exact(self):
        s = BundleStats(mid_cd="x", total=100, bundle_n=70, null_n=0, unit1_n=30)
        assert classify(s) == BundleClassification.STRONG

    def test_strong_at_88(self):
        # 021 냉동식품 실측
        s = BundleStats(mid_cd="021", total=130, bundle_n=115, null_n=3, unit1_n=12)
        assert classify(s) == BundleClassification.STRONG

    def test_strong_just_below_70_is_weak(self):
        # 69/100 = 69% → WEAK
        s = BundleStats(mid_cd="x", total=100, bundle_n=69, null_n=0, unit1_n=31)
        assert classify(s) == BundleClassification.WEAK

    # ── 4. WEAK 임계 (50 <= pct < 70) ──
    def test_weak_at_50_exact(self):
        s = BundleStats(mid_cd="x", total=100, bundle_n=50, null_n=0, unit1_n=50)
        assert classify(s) == BundleClassification.WEAK

    def test_weak_at_67(self):
        # 041 실측
        s = BundleStats(mid_cd="041", total=31, bundle_n=21, null_n=3, unit1_n=7)
        assert classify(s) == BundleClassification.WEAK

    def test_weak_just_below_50_is_normal(self):
        s = BundleStats(mid_cd="x", total=100, bundle_n=49, null_n=0, unit1_n=51)
        assert classify(s) == BundleClassification.NORMAL

    # ── 5. NORMAL ──
    def test_normal_low_pct(self):
        s = BundleStats(mid_cd="x", total=100, bundle_n=10, null_n=0, unit1_n=90)
        assert classify(s) == BundleClassification.NORMAL

    def test_normal_zero_bundle(self):
        s = BundleStats(mid_cd="x", total=100, bundle_n=0, null_n=0, unit1_n=100)
        assert classify(s) == BundleClassification.NORMAL


# ─────────────────────────────────────────────────
# 실측 데이터 회귀 (04-08 product_details 기준)
# ─────────────────────────────────────────────────


class TestRealWorldData:
    """04-08 product_details 실측 데이터 기준 분류 회귀"""

    @pytest.mark.parametrize(
        "mid,total,bundle,null_,expected",
        [
            # 강한 의심 신규 (190b24f 미포함)
            ("021", 130, 115, 3, BundleClassification.STRONG),  # 88.5%
            ("605", 65, 56, 1, BundleClassification.STRONG),    # 86.2%
            ("037", 82, 67, 10, BundleClassification.STRONG),   # 81.7%
            ("044", 132, 104, 1, BundleClassification.STRONG),  # 78.8%
            ("072", 203, 149, 5, BundleClassification.STRONG),  # 73.4% 담배
            ("073", 110, 79, 12, BundleClassification.STRONG),  # 71.8% 전자담배
            # 강한 의심 기존 (190b24f 포함)
            ("032", 260, 201, 41, BundleClassification.STRONG),  # 77.3% 라면
            ("049", 151, 117, 17, BundleClassification.STRONG),  # 77.5% 맥주
            # 약한 의심
            ("041", 31, 21, 3, BundleClassification.WEAK),      # 67.7%
            ("900", 13, 8, 4, BundleClassification.UNKNOWN),    # null=4/13=30.8% > 30 → UNKNOWN
            # 실제 NULL 비율 높은 케이스 (현재 BUNDLE_SUSPECT 인데도 통계 약함)
            ("010", 26, 9, 12, BundleClassification.UNKNOWN),   # null=46% → UNKNOWN
            ("030", 62, 20, 36, BundleClassification.UNKNOWN),  # null=58% → UNKNOWN
        ],
    )
    def test_real_world_classification(self, mid, total, bundle, null_, expected):
        unit1 = total - bundle - null_
        s = BundleStats(
            mid_cd=mid, total=total, bundle_n=bundle, null_n=null_, unit1_n=unit1
        )
        assert classify(s) == expected, (
            f"mid={mid} bundle_pct={s.bundle_pct:.1f} null_ratio={s.null_ratio:.1f}"
        )


# ─────────────────────────────────────────────────
# 임계값 상수 보호 (변경 시 의도적이어야 함)
# ─────────────────────────────────────────────────


def test_thresholds_are_design_spec():
    """임계값 변경 시 design 문서 + 본 테스트 동시 갱신 강제"""
    assert STRONG_THRESHOLD == 70.0
    assert WEAK_THRESHOLD == 50.0
    assert NULL_MAX == 30.0
    assert MIN_TOTAL == 5
