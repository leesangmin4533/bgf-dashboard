"""DemandClassifier 테스트

수요 패턴 4단계 분류 + 면제 + 데이터 부족 처리 검증
+ 누락일 보정 (missing_days correction) 검증
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.demand_classifier import (
    DemandClassifier,
    DemandPattern,
    ClassificationResult,
    DEMAND_PATTERN_THRESHOLDS,
    DEMAND_PATTERN_EXEMPT_MIDS,
    MIN_DATA_DAYS,
    ANALYSIS_WINDOW_DAYS,
    SPARSE_FIX_MIN_WINDOW_RATIO,
)


class TestDemandClassifierClassify:
    """_classify_from_stats 내부 로직 테스트"""

    def setup_method(self):
        self.classifier = DemandClassifier(store_id="46513")

    def test_daily_pattern(self):
        """sell_ratio >= 0.70 -> DAILY"""
        stats = {"total_days": 60, "available_days": 50, "sell_days": 40}
        result = self.classifier._classify_from_stats("ITEM001", stats)
        assert result.pattern == DemandPattern.DAILY
        assert result.sell_day_ratio == round(40 / 50, 4)
        assert result.data_sufficient is True

    def test_frequent_pattern(self):
        """0.40 <= sell_ratio < 0.70 -> FREQUENT"""
        stats = {"total_days": 60, "available_days": 50, "sell_days": 25}
        result = self.classifier._classify_from_stats("ITEM002", stats)
        assert result.pattern == DemandPattern.FREQUENT
        assert 0.40 <= result.sell_day_ratio < 0.70

    def test_intermittent_pattern(self):
        """0.15 <= sell_ratio < 0.40 -> INTERMITTENT"""
        stats = {"total_days": 60, "available_days": 50, "sell_days": 10}
        result = self.classifier._classify_from_stats("ITEM003", stats)
        assert result.pattern == DemandPattern.INTERMITTENT
        assert 0.15 <= result.sell_day_ratio < 0.40

    def test_slow_pattern(self):
        """sell_ratio < 0.15 -> SLOW"""
        stats = {"total_days": 60, "available_days": 50, "sell_days": 3}
        result = self.classifier._classify_from_stats("ITEM004", stats)
        assert result.pattern == DemandPattern.SLOW
        assert result.sell_day_ratio < 0.15

    def test_boundary_daily(self):
        """정확히 0.70 경계 -> DAILY"""
        stats = {"total_days": 60, "available_days": 100, "sell_days": 70}
        result = self.classifier._classify_from_stats("ITEM005", stats)
        assert result.pattern == DemandPattern.DAILY

    def test_boundary_frequent(self):
        """정확히 0.40 경계 -> FREQUENT"""
        stats = {"total_days": 60, "available_days": 100, "sell_days": 40}
        result = self.classifier._classify_from_stats("ITEM006", stats)
        assert result.pattern == DemandPattern.FREQUENT

    def test_boundary_intermittent(self):
        """정확히 0.15 경계 -> INTERMITTENT"""
        stats = {"total_days": 60, "available_days": 100, "sell_days": 15}
        result = self.classifier._classify_from_stats("ITEM007", stats)
        assert result.pattern == DemandPattern.INTERMITTENT

    def test_data_insufficient_sparse_seller(self):
        """데이터 14일 미만 + 극희소 판매 -> SLOW (과발주 방지)

        sell_days=2, window_ratio = 2/60 = 3.3% < 15% → SLOW
        """
        stats = {"total_days": 10, "available_days": 8, "sell_days": 2}
        result = self.classifier._classify_from_stats("ITEM008", stats)
        assert result.pattern == DemandPattern.SLOW
        assert result.data_sufficient is False
        assert result.sell_day_ratio == round(2 / 60, 4)

    def test_data_insufficient_enough_selling(self):
        """데이터 14일 미만 + 충분한 판매 -> FREQUENT 폴백

        sell_days=10, window_ratio = 10/60 = 16.7% >= 15% → FREQUENT
        """
        stats = {"total_days": 13, "available_days": 12, "sell_days": 10}
        result = self.classifier._classify_from_stats("ITEM008B", stats)
        assert result.pattern == DemandPattern.FREQUENT
        assert result.data_sufficient is False

    def test_zero_available_days(self):
        """available=0, missing=0 -> effective_available=0 -> ratio=0.0"""
        stats = {"total_days": 60, "available_days": 0, "sell_days": 0}
        result = self.classifier._classify_from_stats("ITEM009", stats)
        assert result.sell_day_ratio == 0.0
        assert result.pattern == DemandPattern.SLOW

    def test_zero_total_days(self):
        """데이터 없음 -> window_ratio=0/60=0% -> SLOW"""
        stats = {"total_days": 0, "available_days": 0, "sell_days": 0}
        result = self.classifier._classify_from_stats("ITEM010", stats)
        assert result.pattern == DemandPattern.SLOW
        assert result.data_sufficient is False


class TestDemandClassifierExempt:
    """면제 카테고리 테스트"""

    def setup_method(self):
        self.classifier = DemandClassifier(store_id="46513")

    def test_food_exempt_dosirak(self):
        """도시락(001) -> DAILY 고정"""
        result = self.classifier.classify_item("FOOD001", mid_cd="001")
        assert result.pattern == DemandPattern.DAILY
        assert result.sell_day_ratio == 1.0
        assert result.data_sufficient is True

    def test_food_exempt_gimbap(self):
        """김밥(003) -> DAILY 고정"""
        result = self.classifier.classify_item("FOOD003", mid_cd="003")
        assert result.pattern == DemandPattern.DAILY

    def test_food_exempt_bread(self):
        """빵(012) -> DAILY 고정"""
        result = self.classifier.classify_item("BREAD001", mid_cd="012")
        assert result.pattern == DemandPattern.DAILY

    def test_non_exempt_ramen(self):
        """라면(006) -> 면제 아님, DB 조회 필요"""
        assert "006" not in DEMAND_PATTERN_EXEMPT_MIDS


class TestDemandClassifierBatch:
    """배치 분류 테스트"""

    def setup_method(self):
        self.classifier = DemandClassifier(store_id="46513")

    @patch.object(DemandClassifier, '_query_sell_stats_batch')
    def test_batch_mixed(self, mock_batch):
        """면제 + 비면제 혼합 배치"""
        mock_batch.return_value = {
            "RAMEN001": {"total_days": 60, "available_days": 50, "sell_days": 10},
            "BEER001": {"total_days": 60, "available_days": 50, "sell_days": 40},
        }

        items = [
            {"item_cd": "FOOD001", "mid_cd": "001"},   # 면제 -> DAILY
            {"item_cd": "RAMEN001", "mid_cd": "006"},   # intermittent
            {"item_cd": "BEER001", "mid_cd": "049"},    # daily
        ]

        results = self.classifier.classify_batch(items)

        assert results["FOOD001"].pattern == DemandPattern.DAILY
        assert results["RAMEN001"].pattern == DemandPattern.INTERMITTENT
        assert results["BEER001"].pattern == DemandPattern.DAILY
        assert len(results) == 3

    @patch.object(DemandClassifier, '_query_sell_stats_batch')
    def test_batch_empty(self, mock_batch):
        """빈 배치"""
        mock_batch.return_value = {}
        results = self.classifier.classify_batch([])
        assert len(results) == 0

    @patch.object(DemandClassifier, '_query_sell_stats_batch')
    def test_batch_all_exempt(self, mock_batch):
        """전부 면제"""
        items = [
            {"item_cd": "F1", "mid_cd": "001"},
            {"item_cd": "F2", "mid_cd": "003"},
        ]
        results = self.classifier.classify_batch(items)
        assert all(r.pattern == DemandPattern.DAILY for r in results.values())
        mock_batch.assert_not_called()


class TestMissingDaysCorrection:
    """누락일 보정 테스트 (daily_sales 레코드 없는 날 보정)

    핵심 버그: daily_sales에 판매 0인 날이 미등록 → total_days=레코드수.
    sell_day_ratio = sell_days/available_days 에서 분모가 레코드 수가 되면
    3일 판매/3일 레코드 = 100% → DAILY로 오분류.

    수정: missing_days = max(0, 60 - total_days)
          effective_available = available_days + missing_days
    """

    def setup_method(self):
        self.classifier = DemandClassifier(store_id="46513")

    def test_actual_bug_scenario(self):
        """실제 버그 시나리오: 수집 갭으로 인한 오분류 방지

        60일 중 3일만 판매 레코드 → total_days=3, sell_days=3
        수정 전: 3/3 = 100% → DAILY (과발주)
        1차 수정: window_ratio = 3/60 = 5% → SLOW (과소발주, 예측=0 버그)
        2차 수정: data_ratio = 3/3 = 100% >= 40% → FREQUENT (수집 갭 보정)
        """
        stats = {"total_days": 3, "available_days": 3, "sell_days": 3}
        result = self.classifier._classify_from_stats("8801111912075", stats)
        # total_days=3 < 14 → data insufficient path
        # window_ratio = 3/60 = 5% < 15% BUT data_ratio = 3/3 = 100% >= 40%
        # → 수집 갭 보정: FREQUENT (WMA가 저절로 적절한 예측량 산출)
        assert result.pattern == DemandPattern.FREQUENT
        assert result.data_sufficient is False
        assert result.sell_day_ratio == 1.0  # data_ratio 기반

    def test_missing_days_normal_path(self):
        """total_days=20 (40일 누락) → effective_available 보정

        available=15, sell=5 → missing=40 → effective=55
        ratio = 5/55 = 9.1% → SLOW (보정 없으면 5/15=33% → INTERMITTENT)
        """
        stats = {"total_days": 20, "available_days": 15, "sell_days": 5}
        result = self.classifier._classify_from_stats("SPARSE001", stats)
        # missing_days = 60 - 20 = 40
        # effective_available = 15 + 40 = 55
        # ratio = 5/55 = 0.0909 → SLOW
        assert result.pattern == DemandPattern.SLOW
        expected_ratio = round(5 / 55, 4)
        assert result.sell_day_ratio == expected_ratio
        assert result.data_sufficient is True  # total_days=20 >= 14

    def test_missing_days_reclassify_intermittent(self):
        """누락일 보정으로 DAILY→INTERMITTENT 재분류

        total_days=30, available=25, sell=15
        보정 전: 15/25=60% → FREQUENT
        보정 후: missing=30, effective=55, 15/55=27.3% → INTERMITTENT
        """
        stats = {"total_days": 30, "available_days": 25, "sell_days": 15}
        result = self.classifier._classify_from_stats("MID001", stats)
        # missing_days = 30, effective_available = 55
        # ratio = 15/55 = 0.2727 → INTERMITTENT
        assert result.pattern == DemandPattern.INTERMITTENT
        assert 0.15 <= result.sell_day_ratio < 0.40

    def test_no_missing_days(self):
        """total_days=60 → 누락 없음, 기존 로직 동일"""
        stats = {"total_days": 60, "available_days": 55, "sell_days": 45}
        result = self.classifier._classify_from_stats("FULL001", stats)
        # missing_days = 0, effective_available = 55
        # ratio = 45/55 = 0.818 → DAILY
        assert result.pattern == DemandPattern.DAILY
        assert result.sell_day_ratio == round(45 / 55, 4)

    def test_total_days_exceeds_window(self):
        """total_days > 60 → missing_days=0 (음수 방지)"""
        stats = {"total_days": 90, "available_days": 80, "sell_days": 60}
        result = self.classifier._classify_from_stats("OVER001", stats)
        # missing_days = max(0, 60-90) = 0
        # effective_available = 80 + 0 = 80
        # ratio = 60/80 = 0.75 → DAILY
        assert result.pattern == DemandPattern.DAILY
        assert result.sell_day_ratio == round(60 / 80, 4)

    def test_data_insufficient_boundary(self):
        """total_days=13 (경계) → data insufficient path

        sell_days=9 → window_ratio = 9/60 = 15% = 경계 → FREQUENT
        """
        stats = {"total_days": 13, "available_days": 13, "sell_days": 9}
        result = self.classifier._classify_from_stats("EDGE001", stats)
        assert result.data_sufficient is False
        # window_ratio = 9/60 = 0.15 >= 0.15 → NOT slow → FREQUENT fallback
        assert result.pattern == DemandPattern.FREQUENT

    def test_data_insufficient_boundary_slow(self):
        """total_days=13, sell_days=8 → window_ratio=8/60=13.3% < 15%
        BUT data_ratio = 8/13 = 61.5% >= 40% → FREQUENT (수집 갭 보정)
        """
        stats = {"total_days": 13, "available_days": 13, "sell_days": 8}
        result = self.classifier._classify_from_stats("EDGE002", stats)
        assert result.data_sufficient is False
        # data_ratio = 8/13 = 61.5% >= 40% → 수집 갭 보정 → FREQUENT
        assert result.pattern == DemandPattern.FREQUENT

    def test_data_sufficient_boundary(self):
        """total_days=14 (경계) → data sufficient, normal path"""
        stats = {"total_days": 14, "available_days": 10, "sell_days": 5}
        result = self.classifier._classify_from_stats("EDGE003", stats)
        assert result.data_sufficient is True
        # missing_days = 46, effective_available = 56
        # ratio = 5/56 = 0.0893 → SLOW
        assert result.pattern == DemandPattern.SLOW

    def test_sparse_seller_classification(self):
        """희소 데이터 상품 분류: 수집 갭 보정 반영

        data_ratio >= 40%: 수집 갭으로 판단 → FREQUENT (WMA로 적절히 처리)
        data_ratio < 40%: 진짜 slow → SLOW
        """
        sparse_cases = [
            # (total_days, available, sell, expected, reason)
            # sparse-fix-v2: window_ratio >= 5% AND data_ratio >= 40% → FREQUENT
            (3, 3, 3, DemandPattern.FREQUENT, "window=5%>=5% data=100%>=40% 수집갭"),
            (5, 5, 2, DemandPattern.SLOW,     "window=3.3%<5% data=40%>=40% BUT 윈도우하한미달"),
            (7, 7, 5, DemandPattern.FREQUENT, "window=8.3%>=5% data=71%>=40% 수집갭"),
            (10, 10, 1, DemandPattern.SLOW,   "data_ratio=10%<40% 진짜slow"),
            (10, 10, 3, DemandPattern.SLOW,   "data_ratio=30%<40% 진짜slow"),
            (5, 5, 1, DemandPattern.SLOW,     "data_ratio=20%<40% 진짜slow"),
        ]
        for total, avail, sell, expected, reason in sparse_cases:
            stats = {"total_days": total, "available_days": avail, "sell_days": sell}
            result = self.classifier._classify_from_stats(f"SPARSE_{total}_{sell}", stats)
            assert result.pattern == expected, (
                f"total={total}, sell={sell}: expected {expected}, "
                f"got {result.pattern} ({reason})"
            )

    def test_classification_result_fields(self):
        """ClassificationResult 필드 정합성 확인"""
        stats = {"total_days": 20, "available_days": 18, "sell_days": 3}
        result = self.classifier._classify_from_stats("FIELD001", stats)
        assert result.item_cd == "FIELD001"
        assert result.available_days == 18
        assert result.sell_days == 3
        assert result.total_days == 20
        assert isinstance(result.pattern, DemandPattern)
        assert isinstance(result.sell_day_ratio, float)
        assert isinstance(result.data_sufficient, bool)


class TestSparseFixWindowRatioGuard:
    """sparse-fix-v2: window_ratio 하한 가드 테스트

    근본 원인: 8801116032600 릴하이브리드3누아르블랙 (mid_cd=073, 전자담배)
    - 60일 중 2일만 판매 (일평균 0.09) → slow 상품
    - total_days=2, sell_days=2 → data_ratio=100% >= 40%
    - 기존 sparse-fix가 SLOW→FREQUENT로 오분류
    - TobaccoStrategy safety_stock=9.35 → 8개 발주 → PASS cap 3개

    수정: window_ratio >= SPARSE_FIX_MIN_WINDOW_RATIO(5%) 조건 추가
    → 2/60=3.3% < 5%이므로 SLOW 유지
    """

    def setup_method(self):
        self.classifier = DemandClassifier(store_id="46513")

    def test_actual_bug_8801116032600(self):
        """실제 버그 재현: 릴하이브리드 60일 중 2일 판매 → SLOW여야 함

        수정 전: total_days=2, data_ratio=2/2=100% → FREQUENT (버그)
        수정 후: window_ratio=2/60=3.3% < 5% → SLOW (정상)
        """
        stats = {"total_days": 2, "available_days": 2, "sell_days": 2}
        result = self.classifier._classify_from_stats("8801116032600", stats)
        assert result.pattern == DemandPattern.SLOW, (
            f"window_ratio=2/60=3.3% < 5%이므로 SLOW여야 함, got {result.pattern}"
        )
        assert result.data_sufficient is False

    def test_window_ratio_guard_boundary_exact_5pct(self):
        """경계: sell_days=3 → window_ratio=3/60=5% (정확히 하한) → FREQUENT 허용"""
        stats = {"total_days": 3, "available_days": 3, "sell_days": 3}
        result = self.classifier._classify_from_stats("BOUNDARY_5PCT", stats)
        # 3/60=0.05 >= 0.05 AND 3/3=100% >= 40% → FREQUENT
        assert result.pattern == DemandPattern.FREQUENT

    def test_window_ratio_guard_below_5pct(self):
        """하한 미달: sell_days=2 → window_ratio=2/60=3.3% < 5% → SLOW 유지"""
        stats = {"total_days": 2, "available_days": 2, "sell_days": 2}
        result = self.classifier._classify_from_stats("BELOW_5PCT", stats)
        assert result.pattern == DemandPattern.SLOW

    def test_window_ratio_guard_1_sell_day(self):
        """극단: sell_days=1 → window_ratio=1/60=1.7% < 5% → SLOW"""
        stats = {"total_days": 1, "available_days": 1, "sell_days": 1}
        result = self.classifier._classify_from_stats("ULTRA_LOW", stats)
        assert result.pattern == DemandPattern.SLOW

    def test_data_ratio_low_regardless_window(self):
        """data_ratio < 40%이면 window_ratio와 무관하게 SLOW"""
        # sell=2, total=10 → data_ratio=20% < 40%, window=2/60=3.3%
        stats = {"total_days": 10, "available_days": 10, "sell_days": 2}
        result = self.classifier._classify_from_stats("LOW_DATA_RATIO", stats)
        assert result.pattern == DemandPattern.SLOW

    def test_both_conditions_met_above_5pct(self):
        """양쪽 조건 모두 충족: window>=5% AND data>=40% → FREQUENT"""
        # sell=4, total=8 → data=50%>=40%, window=4/60=6.7%>=5%
        stats = {"total_days": 8, "available_days": 8, "sell_days": 4}
        result = self.classifier._classify_from_stats("BOTH_MET", stats)
        assert result.pattern == DemandPattern.FREQUENT
        assert result.data_sufficient is False

    def test_window_met_data_not(self):
        """window>=5% BUT data<40% → SLOW"""
        # sell=3, total=10 → data=30%<40%, window=3/60=5%>=5%
        stats = {"total_days": 10, "available_days": 10, "sell_days": 3}
        result = self.classifier._classify_from_stats("WINDOW_MET_ONLY", stats)
        assert result.pattern == DemandPattern.SLOW

    def test_sparse_fix_constant_value(self):
        """상수값 검증: 5% = 60일 중 최소 3일"""
        assert SPARSE_FIX_MIN_WINDOW_RATIO == 0.05
        min_sell_days = int(ANALYSIS_WINDOW_DAYS * SPARSE_FIX_MIN_WINDOW_RATIO)
        assert min_sell_days == 3, "60일 × 5% = 3일 이상 판매 필요"

    def test_tobacco_slow_item_stays_slow(self):
        """담배류 초저회전 상품: sparse-fix 적용 안 됨 확인

        릴하이브리드 패턴: 한 달에 1~2개 판매, 재고 1개 유지
        → 60일 중 2~3일 판매, total_days=2~3
        → window_ratio=3.3~5%, data_ratio=66~100%
        → window < 5%이면 SLOW 유지
        """
        for sell in range(1, 3):  # sell=1, sell=2
            total = sell  # 판매 있는 날만 레코드 존재
            stats = {"total_days": total, "available_days": total, "sell_days": sell}
            result = self.classifier._classify_from_stats(f"TOBACCO_SLOW_{sell}", stats)
            if sell / ANALYSIS_WINDOW_DAYS < SPARSE_FIX_MIN_WINDOW_RATIO:
                assert result.pattern == DemandPattern.SLOW, (
                    f"sell={sell}: window={sell}/60={sell/60:.1%} < 5% → SLOW"
                )
