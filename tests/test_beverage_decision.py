"""음료 발주 유지/정지 판단 시스템 테스트

도메인 순수 로직(classifier, lifecycle, judge) + Repository + OrderFilter 테스트.
총 ~80개 테스트 케이스.
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# 1. Classifier 테스트 (~18개)
# ============================================================================
from src.prediction.categories.beverage_decision.classifier import (
    classify_beverage_category,
    _apply_safety_override,
    _classify_by_keyword,
)
from src.prediction.categories.beverage_decision.enums import (
    BeverageCategory,
    BeverageLifecycle,
    BeverageDecisionType,
    JudgmentCycle,
    FirstReceivingSource,
    CATEGORY_JUDGMENT_CYCLE,
    NEW_PRODUCT_WEEKS,
    GROWTH_DECLINE_END_WEEKS,
)
from src.prediction.categories.beverage_decision.constants import (
    SMALL_NM_CATEGORY_MAP,
    MID_CD_DEFAULT_CATEGORY,
    MID_CD_DEFAULT_EXPIRY,
    SAFETY_SHORT_THRESHOLD,
    SAFETY_MEDIUM_THRESHOLD,
    BEVERAGE_MID_CDS,
    PROMO_PROTECTION_WEEKS,
    AUTO_CONFIRM_DAYS,
    SEASONAL_OFF_PEAK,
)


class TestClassifier:
    """중분류 1차 + 소분류명 2차 + 유통기한 2.5차 + 안전장치 3차"""

    def test_발효유_small_nm(self):
        assert classify_beverage_category("046", "발효유", 15) == BeverageCategory.A

    def test_호상요구르트(self):
        assert classify_beverage_category("046", "호상요구르트", 20) == BeverageCategory.A

    def test_흰우유(self):
        assert classify_beverage_category("047", "흰우유", 10) == BeverageCategory.A

    def test_가공유(self):
        assert classify_beverage_category("047", "가공유", 54) == BeverageCategory.B

    def test_냉장커피(self):
        assert classify_beverage_category("042", "냉장커피", 17) == BeverageCategory.B

    def test_냉장주스(self):
        assert classify_beverage_category("039", "냉장주스", 24) == BeverageCategory.B

    def test_캔병커피(self):
        assert classify_beverage_category("042", "캔/병커피", 365) == BeverageCategory.C

    def test_일반탄산(self):
        assert classify_beverage_category("044", "일반탄산", 365) == BeverageCategory.C

    def test_에너지음료(self):
        assert classify_beverage_category("040", "에너지음료", 360) == BeverageCategory.C

    def test_국산생수(self):
        assert classify_beverage_category("041", "국산생수", 365) == BeverageCategory.D

    def test_일반얼음(self):
        assert classify_beverage_category("048", "일반얼음", 9999) == BeverageCategory.D

    def test_mid_cd_없으면_C_폴백(self):
        assert classify_beverage_category(None, None, None) == BeverageCategory.C

    def test_소분류_없으면_mid_cd_기본값(self):
        """소분류명 없을 때 MID_CD_DEFAULT_CATEGORY 사용"""
        assert classify_beverage_category("046", None, 15) == BeverageCategory.A
        assert classify_beverage_category("041", None, 365) == BeverageCategory.D
        assert classify_beverage_category("044", None, 365) == BeverageCategory.C

    def test_유통기한_null_폴백(self):
        """유통기한 None → MID_CD_DEFAULT_EXPIRY 적용"""
        # 046 → 기본유통기한 15 → A (mid_cd default)
        result = classify_beverage_category("046", None, None)
        assert result == BeverageCategory.A


class TestClassifierKeyword:
    """소분류 없는 상품의 상품명 키워드 보조 분류"""

    def test_커피_냉장_키워드(self):
        result = _classify_by_keyword("042", "스타벅스 컵커피 라떼")
        assert result == "B"

    def test_커피_캔_키워드(self):
        result = _classify_by_keyword("042", "레쓰비 CAN 175ml")
        assert result == "C"

    def test_커피_PET_패턴(self):
        result = _classify_by_keyword("042", "조지아 PET 500ml")
        assert result == "C"

    def test_우유_흰우유_키워드(self):
        result = _classify_by_keyword("047", "서울 흰우유 200ml")
        assert result == "A"

    def test_우유_기본_가공유(self):
        """소분류 없는 우유 27개 전부 가공유 실측"""
        result = _classify_by_keyword("047", "바나나맛 우유")
        assert result == "B"

    def test_주스_냉장_키워드(self):
        result = _classify_by_keyword("039", "착즙 오렌지 주스")
        assert result == "B"

    def test_주스_매칭실패(self):
        result = _classify_by_keyword("039", "델몬트 오렌지 주스")
        assert result is None

    def test_미지원_mid_cd(self):
        result = _classify_by_keyword("044", "코카콜라")
        assert result is None

    def test_item_nm_없음(self):
        result = _classify_by_keyword("042", None)
        assert result is None

    def test_키워드_classify_통합(self):
        """classify_beverage_category에서 키워드 경로 통합 검증"""
        result = classify_beverage_category("042", None, None, "스타벅스 컵커피")
        assert result == BeverageCategory.B


class TestClassifierSafetyOverride:
    """3차 안전장치: C/D + 유통기한 짧으면 상향"""

    def test_상수_값(self):
        assert SAFETY_SHORT_THRESHOLD == 20
        assert SAFETY_MEDIUM_THRESHOLD == 60

    def test_C_유통기한_15일_A상향(self):
        result = _apply_safety_override(BeverageCategory.C, 15, "044", "일반탄산")
        assert result == BeverageCategory.A

    def test_D_유통기한_20일_A상향(self):
        result = _apply_safety_override(BeverageCategory.D, 20, "041", "국산생수")
        assert result == BeverageCategory.A

    def test_C_유통기한_40일_B상향(self):
        result = _apply_safety_override(BeverageCategory.C, 40, "044", None)
        assert result == BeverageCategory.B

    def test_D_유통기한_60일_B상향(self):
        result = _apply_safety_override(BeverageCategory.D, 60, "041", None)
        assert result == BeverageCategory.B

    def test_C_유통기한_61일_유지(self):
        result = _apply_safety_override(BeverageCategory.C, 61, "044", None)
        assert result == BeverageCategory.C

    def test_D_유통기한_100일_유지(self):
        result = _apply_safety_override(BeverageCategory.D, 100, "041", None)
        assert result == BeverageCategory.D

    def test_D_유통기한_None_유지(self):
        result = _apply_safety_override(BeverageCategory.D, None, "041", None)
        assert result == BeverageCategory.D

    def test_A_안전장치_미적용(self):
        result = _apply_safety_override(BeverageCategory.A, 5, "046", "발효유")
        assert result == BeverageCategory.A

    def test_B_안전장치_미적용(self):
        result = _apply_safety_override(BeverageCategory.B, 10, "042", "냉장커피")
        assert result == BeverageCategory.B


# ============================================================================
# 2. Lifecycle 테스트 (~12개)
# ============================================================================
from src.prediction.categories.beverage_decision.lifecycle import (
    determine_lifecycle,
)


class TestLifecycle:

    def test_cat_a_new_2주(self):
        """A 카테고리 2주 → NEW (보호기간 3주)"""
        ref = "2026-03-05"
        first = "2026-02-22"  # 11일 → 1주
        phase, weeks = determine_lifecycle(first, ref, BeverageCategory.A)
        assert phase == BeverageLifecycle.NEW
        assert weeks < 3

    def test_cat_a_growth_5주(self):
        """A 카테고리 5주 → GROWTH_DECLINE (3~8주)"""
        ref = "2026-03-05"
        first = "2026-01-29"  # 35일 → 5주
        phase, weeks = determine_lifecycle(first, ref, BeverageCategory.A)
        assert phase == BeverageLifecycle.GROWTH_DECLINE

    def test_cat_a_established_9주(self):
        """A 카테고리 9주 → ESTABLISHED (8주+)"""
        ref = "2026-03-05"
        first = "2026-01-01"  # 63일 → 9주
        phase, weeks = determine_lifecycle(first, ref, BeverageCategory.A)
        assert phase == BeverageLifecycle.ESTABLISHED

    def test_cat_b_new_3주(self):
        """B 카테고리 NEW는 4주 → 3주는 NEW"""
        ref = "2026-03-05"
        first = "2026-02-15"  # 18일 → 2주
        phase, _ = determine_lifecycle(first, ref, BeverageCategory.B)
        assert phase == BeverageLifecycle.NEW

    def test_cat_b_growth_6주(self):
        """B 카테고리 6주 → GROWTH_DECLINE (4~10주)"""
        ref = "2026-03-05"
        first = "2026-01-22"  # 42일 → 6주
        phase, _ = determine_lifecycle(first, ref, BeverageCategory.B)
        assert phase == BeverageLifecycle.GROWTH_DECLINE

    def test_cat_c_new_5주(self):
        """C 카테고리 NEW는 6주 → 5주는 NEW"""
        ref = "2026-03-05"
        first = "2026-01-30"  # 34일 → 4주
        phase, _ = determine_lifecycle(first, ref, BeverageCategory.C)
        assert phase == BeverageLifecycle.NEW

    def test_cat_c_established_13주(self):
        """C 카테고리 13주 → ESTABLISHED (12주+)"""
        ref = "2026-03-05"
        first = "2025-12-04"  # 91일 → 13주
        phase, _ = determine_lifecycle(first, ref, BeverageCategory.C)
        assert phase == BeverageLifecycle.ESTABLISHED

    def test_first_date_none(self):
        phase, weeks = determine_lifecycle(None, "2026-03-05", BeverageCategory.A)
        assert phase == BeverageLifecycle.ESTABLISHED
        assert weeks == 999

    def test_future_first_date(self):
        phase, weeks = determine_lifecycle("2026-04-01", "2026-03-05", BeverageCategory.A)
        assert phase == BeverageLifecycle.NEW
        assert weeks == 0

    def test_invalid_date(self):
        phase, _ = determine_lifecycle("invalid", "2026-03-05", BeverageCategory.A)
        assert phase == BeverageLifecycle.ESTABLISHED

    def test_new_product_weeks_config(self):
        assert NEW_PRODUCT_WEEKS[BeverageCategory.A] == 3
        assert NEW_PRODUCT_WEEKS[BeverageCategory.B] == 4
        assert NEW_PRODUCT_WEEKS[BeverageCategory.C] == 6
        assert NEW_PRODUCT_WEEKS[BeverageCategory.D] == 6

    def test_growth_decline_end_weeks_config(self):
        assert GROWTH_DECLINE_END_WEEKS[BeverageCategory.A] == 8
        assert GROWTH_DECLINE_END_WEEKS[BeverageCategory.B] == 10
        assert GROWTH_DECLINE_END_WEEKS[BeverageCategory.C] == 12
        assert GROWTH_DECLINE_END_WEEKS[BeverageCategory.D] == 12

    def test_source_products_bulk(self):
        phase, weeks = determine_lifecycle(
            "2026-02-19", "2026-03-05", BeverageCategory.A,
            source=FirstReceivingSource.PRODUCTS_BULK,
        )
        assert phase == BeverageLifecycle.ESTABLISHED
        assert weeks == 2  # 14일 // 7

    def test_source_none_returns_none(self):
        phase, weeks = determine_lifecycle(
            None, "2026-03-05", BeverageCategory.A,
            source=FirstReceivingSource.NONE,
        )
        assert phase is None
        assert weeks == 0


# ============================================================================
# 3. Judge 테스트 (~25개)
# ============================================================================
from src.prediction.categories.beverage_decision.judge import (
    calc_sale_rate,
    count_consecutive_low_weeks,
    count_consecutive_zero_months,
    check_loss_exceeds_profit,
    judge_category_a,
    judge_category_b,
    judge_category_c,
    judge_category_d,
    judge_item,
)
from src.prediction.categories.beverage_decision.models import BeverageSalesMetrics


class TestCalcSaleRate:

    def test_normal(self):
        assert calc_sale_rate(8, 2) == 0.8

    def test_zero_both(self):
        assert calc_sale_rate(0, 0) == 0.0

    def test_all_sold(self):
        assert calc_sale_rate(10, 0) == 1.0

    def test_all_wasted(self):
        assert calc_sale_rate(0, 10) == 0.0


class TestConsecutiveLowWeeks:

    def test_all_low(self):
        assert count_consecutive_low_weeks([0.3, 0.2, 0.1], 0.5) == 3

    def test_first_ok(self):
        assert count_consecutive_low_weeks([0.6, 0.2, 0.1], 0.5) == 0

    def test_one_low(self):
        assert count_consecutive_low_weeks([0.4, 0.6, 0.2], 0.5) == 1


class TestConsecutiveZeroMonths:

    def test_two_zeros(self):
        assert count_consecutive_zero_months([0, 0, 5]) == 2

    def test_no_zeros(self):
        assert count_consecutive_zero_months([1, 2, 3]) == 0

    def test_all_zeros(self):
        assert count_consecutive_zero_months([0, 0, 0]) == 3


class TestLossExceedsProfit:
    """§2.6 폐기 손익 계산"""

    def test_loss_exceeds(self):
        # margin_rate=60%: disuse_cost = 10 * 1000 * 0.4 = 4000
        # sale_margin = 5 * 1000 * 0.6 = 3000 → 4000 > 3000
        assert check_loss_exceeds_profit(5, 10, 1000, 60.0) is True

    def test_profit_exceeds(self):
        # disuse_cost = 2 * 1000 * 0.4 = 800
        # sale_margin = 10 * 1000 * 0.6 = 6000 → 800 < 6000
        assert check_loss_exceeds_profit(10, 2, 1000, 60.0) is False

    def test_zero_price(self):
        assert check_loss_exceeds_profit(5, 10, 0, 60.0) is False

    def test_default_margin(self):
        """margin_rate=None → DEFAULT_MARGIN_RATE(60%) 사용"""
        assert check_loss_exceeds_profit(5, 10, 1000) is True


class TestJudgeCategoryA:
    """카테고리 A: 냉장 단기 유제품 (주 1회)"""

    def _metrics(self, **kw) -> BeverageSalesMetrics:
        defaults = {
            "total_sale_qty": 10, "total_disuse_qty": 2,
            "sale_rate": 0.8, "category_avg_sale_qty": 20.0,
            "prev_period_sale_qty": 10, "sale_trend_pct": 0.0,
            "weekly_sale_rates": [0.8, 0.7, 0.6],
            "shelf_efficiency": 0.5, "small_cd_median_sale_qty": 10.0,
        }
        defaults.update(kw)
        return BeverageSalesMetrics(**defaults)

    def test_new_keep(self):
        decision, _, _ = judge_category_a(BeverageLifecycle.NEW, self._metrics())
        assert decision == BeverageDecisionType.KEEP

    def test_new_rapid_decline(self):
        m = self._metrics(sale_trend_pct=-55.0, prev_period_sale_qty=10)
        decision, _, warning = judge_category_a(BeverageLifecycle.NEW, m)
        assert decision == BeverageDecisionType.WATCH
        assert warning is True

    def test_loss_exceeds_profit_stop(self):
        """폐기원가 > 판매마진 → 즉시 정지 (lifecycle 무관)"""
        m = self._metrics(total_sale_qty=2, total_disuse_qty=20)
        decision, _, _ = judge_category_a(
            BeverageLifecycle.NEW, m, sell_price=1000, margin_rate=60.0,
        )
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_growth_2week_low_stop(self):
        m = self._metrics(weekly_sale_rates=[0.3, 0.4, 0.8])
        decision, _, _ = judge_category_a(BeverageLifecycle.GROWTH_DECLINE, m)
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_growth_1week_low_watch(self):
        m = self._metrics(weekly_sale_rates=[0.3, 0.6, 0.8])
        decision, _, _ = judge_category_a(BeverageLifecycle.GROWTH_DECLINE, m)
        assert decision == BeverageDecisionType.WATCH

    def test_growth_normal_keep(self):
        m = self._metrics(weekly_sale_rates=[0.6, 0.7, 0.8])
        decision, _, _ = judge_category_a(BeverageLifecycle.GROWTH_DECLINE, m)
        assert decision == BeverageDecisionType.KEEP

    def test_established_low_avg_stop(self):
        """주간판매량 < 소분류평균 30% → 정지"""
        m = self._metrics(total_sale_qty=5, category_avg_sale_qty=20.0)
        decision, _, _ = judge_category_a(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_established_normal_keep(self):
        m = self._metrics(total_sale_qty=10, category_avg_sale_qty=20.0)
        decision, _, _ = judge_category_a(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.KEEP


class TestJudgeCategoryB:
    """카테고리 B: 냉장 중기 음료 (2주 1회)"""

    def _metrics(self, **kw) -> BeverageSalesMetrics:
        defaults = {
            "weekly_sale_rates": [0.6, 0.5, 0.5],
            "shelf_efficiency": 0.5,
            "small_cd_median_sale_qty": 10.0,
        }
        defaults.update(kw)
        return BeverageSalesMetrics(**defaults)

    def test_new_keep(self):
        decision, _, _ = judge_category_b(BeverageLifecycle.NEW, self._metrics())
        assert decision == BeverageDecisionType.KEEP

    def test_3week_low_stop(self):
        """판매율 40% 미만 3주 연속 → 정지"""
        m = self._metrics(weekly_sale_rates=[0.2, 0.3, 0.35, 0.5])
        decision, _, _ = judge_category_b(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_2week_low_watch(self):
        m = self._metrics(weekly_sale_rates=[0.2, 0.3, 0.5])
        decision, _, _ = judge_category_b(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.WATCH

    def test_shelf_efficiency_low_stop(self):
        """매대효율 < 0.20 → 정지"""
        m = self._metrics(shelf_efficiency=0.10, small_cd_median_sale_qty=20.0)
        decision, reason, _ = judge_category_b(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.STOP_RECOMMEND
        assert "매대효율" in reason

    def test_shelf_efficiency_ok_keep(self):
        m = self._metrics(shelf_efficiency=0.30, small_cd_median_sale_qty=20.0)
        decision, _, _ = judge_category_b(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.KEEP

    def test_shelf_efficiency_no_median_skip(self):
        """소분류 중위값 0 → 매대효율 판단 건너뜀"""
        m = self._metrics(shelf_efficiency=0.05, small_cd_median_sale_qty=0.0)
        decision, _, _ = judge_category_b(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.KEEP


class TestJudgeCategoryC:
    """카테고리 C: 상온 장기 음료 (월 1회)"""

    def _metrics(self, **kw) -> BeverageSalesMetrics:
        defaults = {
            "shelf_efficiency": 0.3,
            "small_cd_median_sale_qty": 10.0,
        }
        defaults.update(kw)
        return BeverageSalesMetrics(**defaults)

    def test_new_keep(self):
        decision, _, _ = judge_category_c(BeverageLifecycle.NEW, self._metrics())
        assert decision == BeverageDecisionType.KEEP

    def test_shelf_low_stop(self):
        """매대효율 < 0.15 → 정지"""
        m = self._metrics(shelf_efficiency=0.10, small_cd_median_sale_qty=20.0)
        decision, _, _ = judge_category_c(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_seasonal_half_threshold(self):
        """비수기 threshold=0.075 적용"""
        m = self._metrics(shelf_efficiency=0.10, small_cd_median_sale_qty=20.0)
        # 0.10 > 0.075 → KEEP
        decision, _, _ = judge_category_c(
            BeverageLifecycle.ESTABLISHED, m, shelf_threshold=0.075,
        )
        assert decision == BeverageDecisionType.KEEP

    def test_seasonal_half_threshold_below_stop(self):
        """비수기에도 0.075 미만이면 정지"""
        m = self._metrics(shelf_efficiency=0.05, small_cd_median_sale_qty=20.0)
        decision, _, _ = judge_category_c(
            BeverageLifecycle.ESTABLISHED, m, shelf_threshold=0.075,
        )
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_normal_keep(self):
        m = self._metrics(shelf_efficiency=0.20, small_cd_median_sale_qty=20.0)
        decision, _, _ = judge_category_c(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.KEEP


class TestJudgeCategoryD:
    """카테고리 D: 초장기/비소모품 (월 1회)"""

    def test_3month_zero_stop(self):
        m = BeverageSalesMetrics(consecutive_zero_months=3)
        decision, _, _ = judge_category_d(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.STOP_RECOMMEND

    def test_2month_zero_watch(self):
        m = BeverageSalesMetrics(consecutive_zero_months=2)
        decision, _, _ = judge_category_d(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.WATCH

    def test_1month_zero_keep(self):
        m = BeverageSalesMetrics(consecutive_zero_months=1)
        decision, _, _ = judge_category_d(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.KEEP

    def test_normal_keep(self):
        m = BeverageSalesMetrics(consecutive_zero_months=0)
        decision, _, _ = judge_category_d(BeverageLifecycle.ESTABLISHED, m)
        assert decision == BeverageDecisionType.KEEP


class TestJudgeItemDispatcher:

    def test_dispatches_A(self):
        m = BeverageSalesMetrics(weekly_sale_rates=[0.6, 0.5])
        d, _, _ = judge_item(BeverageCategory.A, BeverageLifecycle.NEW, m)
        assert d == BeverageDecisionType.KEEP

    def test_dispatches_D(self):
        m = BeverageSalesMetrics(consecutive_zero_months=0)
        d, _, _ = judge_item(BeverageCategory.D, BeverageLifecycle.ESTABLISHED, m)
        assert d == BeverageDecisionType.KEEP

    def test_shelf_threshold_override(self):
        """계절 보정 shelf_threshold_override 전달"""
        m = BeverageSalesMetrics(shelf_efficiency=0.10, small_cd_median_sale_qty=20.0)
        # 0.10 > 0.075 → KEEP
        d, _, _ = judge_item(
            BeverageCategory.C, BeverageLifecycle.ESTABLISHED, m,
            shelf_threshold_override=0.075,
        )
        assert d == BeverageDecisionType.KEEP


# ============================================================================
# 4. Enums 테스트 (~5개)
# ============================================================================
class TestEnums:

    def test_category_values(self):
        assert BeverageCategory.A.value == "A"
        assert BeverageCategory.D.value == "D"

    def test_lifecycle_values(self):
        assert BeverageLifecycle.NEW.value == "new"
        assert BeverageLifecycle.ESTABLISHED.value == "established"

    def test_decision_values(self):
        assert BeverageDecisionType.KEEP.value == "KEEP"
        assert BeverageDecisionType.STOP_RECOMMEND.value == "STOP_RECOMMEND"

    def test_judgment_cycle_mapping(self):
        assert CATEGORY_JUDGMENT_CYCLE[BeverageCategory.A] == JudgmentCycle.WEEKLY
        assert CATEGORY_JUDGMENT_CYCLE[BeverageCategory.B] == JudgmentCycle.BIWEEKLY
        assert CATEGORY_JUDGMENT_CYCLE[BeverageCategory.C] == JudgmentCycle.MONTHLY
        assert CATEGORY_JUDGMENT_CYCLE[BeverageCategory.D] == JudgmentCycle.MONTHLY

    def test_str_enum_comparison(self):
        assert BeverageCategory.A == "A"
        assert BeverageDecisionType.KEEP == "KEEP"


# ============================================================================
# 5. Constants 테스트 (~7개)
# ============================================================================
class TestConstants:

    def test_mid_cd_coverage(self):
        """039~048 전체 10개 mid_cd 커버"""
        assert len(BEVERAGE_MID_CDS) == 10

    def test_small_nm_map_count(self):
        """소분류 24개 매핑"""
        # A: 3, B: 3, C: 13, D: 4 = 23
        assert len(SMALL_NM_CATEGORY_MAP) >= 20

    def test_promo_protection_weeks(self):
        assert PROMO_PROTECTION_WEEKS["1+1"] == 3
        assert PROMO_PROTECTION_WEEKS["2+1"] == 2
        assert PROMO_PROTECTION_WEEKS["할인"] == 1

    def test_auto_confirm_days(self):
        assert AUTO_CONFIRM_DAYS["A"] == 14
        assert AUTO_CONFIRM_DAYS["B"] == 30
        assert AUTO_CONFIRM_DAYS["C"] == 60
        assert AUTO_CONFIRM_DAYS["D"] == 120

    def test_seasonal_off_peak(self):
        """얼음/원액 비수기 11~2월"""
        assert "048" in SEASONAL_OFF_PEAK
        assert "045" in SEASONAL_OFF_PEAK
        assert set(SEASONAL_OFF_PEAK["048"]) == {11, 12, 1, 2}

    def test_mid_cd_default_expiry(self):
        assert MID_CD_DEFAULT_EXPIRY["046"] == 15
        assert MID_CD_DEFAULT_EXPIRY["048"] == 9999


# ============================================================================
# 6. Repository 테스트 (~10개)
# ============================================================================
class TestBeverageDecisionRepository:

    @pytest.fixture
    def repo_with_db(self, tmp_path):
        from src.infrastructure.database.repos.beverage_decision_repo import (
            BeverageDecisionRepository,
        )

        db_path = tmp_path / "test_store.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dessert_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT NOT NULL,
                item_cd TEXT NOT NULL,
                item_nm TEXT,
                mid_cd TEXT DEFAULT '042',
                dessert_category TEXT NOT NULL,
                expiration_days INTEGER,
                small_nm TEXT,
                lifecycle_phase TEXT NOT NULL,
                first_receiving_date TEXT,
                first_receiving_source TEXT,
                weeks_since_intro INTEGER DEFAULT 0,
                judgment_period_start TEXT NOT NULL,
                judgment_period_end TEXT NOT NULL,
                total_order_qty INTEGER DEFAULT 0,
                total_sale_qty INTEGER DEFAULT 0,
                total_disuse_qty INTEGER DEFAULT 0,
                sale_amount INTEGER DEFAULT 0,
                disuse_amount INTEGER DEFAULT 0,
                sell_price INTEGER DEFAULT 0,
                sale_rate REAL DEFAULT 0.0,
                category_avg_sale_qty REAL DEFAULT 0.0,
                prev_period_sale_qty INTEGER DEFAULT 0,
                sale_trend_pct REAL DEFAULT 0.0,
                consecutive_low_weeks INTEGER DEFAULT 0,
                consecutive_zero_months INTEGER DEFAULT 0,
                decision TEXT NOT NULL,
                decision_reason TEXT,
                is_rapid_decline_warning INTEGER DEFAULT 0,
                operator_action TEXT,
                operator_note TEXT,
                action_taken_at TEXT,
                judgment_cycle TEXT NOT NULL,
                category_type TEXT DEFAULT 'dessert',
                created_at TEXT NOT NULL,
                UNIQUE(store_id, item_cd, judgment_period_end)
            )
        """)
        conn.commit()
        conn.close()

        repo = BeverageDecisionRepository(store_id="99999")
        repo._get_conn = lambda: sqlite3.connect(str(db_path))
        repo._get_conn_rr = repo._get_conn
        return repo

    def _sample_decision(self, item_cd="BEV001", decision="KEEP", category="B",
                         period_end="2026-03-05"):
        return {
            "store_id": "99999",
            "item_cd": item_cd,
            "item_nm": "테스트음료",
            "mid_cd": "042",
            "dessert_category": category,
            "expiration_days": 90,
            "small_nm": "캔/병커피",
            "lifecycle_phase": "established",
            "first_receiving_date": "2025-12-01",
            "first_receiving_source": "daily_sales",
            "weeks_since_intro": 13,
            "judgment_period_start": "2026-02-26",
            "judgment_period_end": period_end,
            "total_order_qty": 20,
            "total_sale_qty": 15,
            "total_disuse_qty": 5,
            "sale_amount": 15000,
            "disuse_amount": 5000,
            "sell_price": 1500,
            "sale_rate": 0.75,
            "category_avg_sale_qty": 20.0,
            "prev_period_sale_qty": 12,
            "sale_trend_pct": 25.0,
            "consecutive_low_weeks": 0,
            "consecutive_zero_months": 0,
            "decision": decision,
            "decision_reason": "정상",
            "is_rapid_decline_warning": 0,
            "judgment_cycle": "biweekly",
        }

    def test_save_and_retrieve(self, repo_with_db):
        decisions = [self._sample_decision()]
        saved = repo_with_db.save_decisions_batch(decisions)
        assert saved == 1

        latest = repo_with_db.get_latest_decisions()
        assert len(latest) == 1
        assert latest[0]["item_cd"] == "BEV001"
        assert latest[0]["category_type"] == "beverage"

    def test_upsert_same_key(self, repo_with_db):
        repo_with_db.save_decisions_batch([self._sample_decision(decision="KEEP")])
        repo_with_db.save_decisions_batch([self._sample_decision(decision="STOP_RECOMMEND")])

        latest = repo_with_db.get_latest_decisions()
        assert len(latest) == 1
        assert latest[0]["decision"] == "STOP_RECOMMEND"

    def test_category_type_filter(self, repo_with_db):
        """category_type='beverage' 필터 검증: dessert 레코드는 조회 안 됨"""
        # 직접 dessert 레코드 삽입
        conn = repo_with_db._get_conn()
        conn.execute("""
            INSERT INTO dessert_decisions (
                store_id, item_cd, item_nm, mid_cd,
                dessert_category, lifecycle_phase,
                judgment_period_start, judgment_period_end,
                decision, judgment_cycle, category_type, created_at
            ) VALUES (
                '99999', 'DES001', '디저트', '014',
                'A', 'established',
                '2026-02-26', '2026-03-05',
                'KEEP', 'weekly', 'dessert', '2026-03-05'
            )
        """)
        conn.commit()

        # beverage 레코드 저장
        repo_with_db.save_decisions_batch([self._sample_decision()])

        latest = repo_with_db.get_latest_decisions()
        assert len(latest) == 1
        assert latest[0]["item_cd"] == "BEV001"  # dessert 제외

    def test_get_confirmed_stop_empty(self, repo_with_db):
        repo_with_db.save_decisions_batch([self._sample_decision(decision="STOP_RECOMMEND")])
        stops = repo_with_db.get_confirmed_stop_items()
        assert len(stops) == 0

    def test_get_confirmed_stop_with_action(self, repo_with_db):
        repo_with_db.save_decisions_batch([self._sample_decision(decision="STOP_RECOMMEND")])
        # batch_update로 CONFIRMED_STOP 설정
        repo_with_db.batch_update_operator_action(
            ["BEV001"], "CONFIRMED_STOP", "운영자 확인",
        )
        stops = repo_with_db.get_confirmed_stop_items()
        assert "BEV001" in stops

    def test_get_pending_stop_count(self, repo_with_db):
        repo_with_db.save_decisions_batch([
            self._sample_decision(item_cd="B001", decision="STOP_RECOMMEND"),
            self._sample_decision(item_cd="B002", decision="STOP_RECOMMEND"),
            self._sample_decision(item_cd="B003", decision="KEEP"),
        ])
        count = repo_with_db.get_pending_stop_count()
        assert count == 2

    def test_get_decision_summary(self, repo_with_db):
        repo_with_db.save_decisions_batch([
            self._sample_decision(item_cd="B001", decision="KEEP", category="A"),
            self._sample_decision(item_cd="B002", decision="STOP_RECOMMEND", category="A"),
            self._sample_decision(item_cd="B003", decision="KEEP", category="C"),
        ])
        summary = repo_with_db.get_decision_summary()
        assert "current" in summary
        assert summary["current"]["KEEP"] == 2
        assert summary["current"]["STOP_RECOMMEND"] == 1

    def test_save_empty_list(self, repo_with_db):
        saved = repo_with_db.save_decisions_batch([])
        assert saved == 0

    def test_batch_update_operator_action(self, repo_with_db):
        repo_with_db.save_decisions_batch([
            self._sample_decision(item_cd="B001", decision="STOP_RECOMMEND"),
            self._sample_decision(item_cd="B002", decision="STOP_RECOMMEND"),
        ])
        results = repo_with_db.batch_update_operator_action(
            ["B001", "B002"], "CONFIRMED_STOP", "일괄 확인",
        )
        assert len(results) == 2
        assert all(r["action"] == "CONFIRMED_STOP" for r in results)


# ============================================================================
# 7. OrderFilter 연동 테스트 (~5개)
# ============================================================================
class TestOrderFilterBeverageStop:

    @pytest.fixture
    def order_filter(self):
        from src.order.order_filter import OrderFilter
        return OrderFilter(store_id="99999")

    def _make_order_list(self, item_codes):
        return [{"item_cd": cd, "item_nm": f"음료{cd}", "mid_cd": "042",
                 "order_qty": 5} for cd in item_codes]

    @patch("src.order.order_filter.ExclusionType")
    def test_beverage_stop_attribute_exists(self, mock_et):
        """ExclusionType.BEVERAGE_STOP 상수 존재"""
        from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType
        assert hasattr(ExclusionType, "BEVERAGE_STOP")
        assert ExclusionType.BEVERAGE_STOP == "BEVERAGE_STOP"
        assert "BEVERAGE_STOP" in ExclusionType.ALL

    @patch("src.settings.constants.BEVERAGE_DECISION_ENABLED", True)
    @patch("src.infrastructure.database.repos.BeverageDecisionRepository")
    @patch("src.infrastructure.database.repos.DessertDecisionRepository")
    @patch("src.settings.constants.DESSERT_DECISION_ENABLED", False)
    @patch("src.infrastructure.database.repos.StoppedItemRepository")
    @patch("src.infrastructure.database.repos.AppSettingsRepository")
    def test_confirmed_stop_excluded(self, mock_settings, mock_stopped,
                                      mock_dessert, mock_bev, order_filter):
        """CONFIRMED_STOP 음료 상품이 발주 목록에서 제외"""
        mock_settings.return_value.get.return_value = True
        mock_stopped.return_value.get_active_item_codes.return_value = set()
        mock_dessert.return_value.get_confirmed_stop_items.return_value = set()
        mock_bev.return_value.get_confirmed_stop_items.return_value = {"BEV002"}

        orders = self._make_order_list(["BEV001", "BEV002", "BEV003"])
        exclusion_records = []

        result = order_filter.exclude_filtered_items(
            order_list=orders,
            unavailable_items=set(),
            cut_items=set(),
            auto_order_items=set(),
            smart_order_items=set(),
            exclusion_records=exclusion_records,
        )

        item_cds = {r["item_cd"] for r in result}
        assert "BEV002" not in item_cds
        assert "BEV001" in item_cds
        assert "BEV003" in item_cds

    @patch("src.settings.constants.BEVERAGE_DECISION_ENABLED", False)
    @patch("src.settings.constants.DESSERT_DECISION_ENABLED", False)
    @patch("src.infrastructure.database.repos.StoppedItemRepository")
    @patch("src.infrastructure.database.repos.AppSettingsRepository")
    def test_disabled_no_exclusion(self, mock_settings, mock_stopped, order_filter):
        """BEVERAGE_DECISION_ENABLED=False면 필터 안 함"""
        mock_settings.return_value.get.return_value = True
        mock_stopped.return_value.get_active_item_codes.return_value = set()

        orders = self._make_order_list(["BEV001", "BEV002"])
        exclusion_records = []

        result = order_filter.exclude_filtered_items(
            order_list=orders,
            unavailable_items=set(),
            cut_items=set(),
            auto_order_items=set(),
            smart_order_items=set(),
            exclusion_records=exclusion_records,
        )

        assert len(result) == 2
