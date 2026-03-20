"""디저트 발주 유지/정지 판단 시스템 테스트

도메인 순수 로직(classifier, lifecycle, judge) + Repository + OrderFilter + API 테스트.
총 ~70개 테스트 케이스.
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# 1. Classifier 테스트 (~12개)
# ============================================================================
from src.prediction.categories.dessert_decision.classifier import (
    classify_dessert_category,
    _apply_safety_override,
    SMALL_NM_CATEGORY_MAP,
    ROOM_TEMP_THRESHOLD,
    SAFETY_SHORT_THRESHOLD,
    SAFETY_MEDIUM_THRESHOLD,
)
from src.prediction.categories.dessert_decision.enums import (
    DessertCategory,
    DessertLifecycle,
    DessertDecisionType,
    JudgmentCycle,
    FirstReceivingSource,
    CATEGORY_JUDGMENT_CYCLE,
)


class TestClassifier:
    """소분류명 1차 + 유통기한 2차 분류 테스트"""

    def test_냉장디저트_small_nm(self):
        assert classify_dessert_category("냉장디저트", 3) == DessertCategory.A

    def test_냉장디저트_유통기한_무관(self):
        """냉장디저트는 유통기한이 20일이어도 A"""
        assert classify_dessert_category("냉장디저트", 20) == DessertCategory.A

    def test_냉장디저트_유통기한_24일도_A(self):
        """v1.1 핵심: 냉장디저트 중 유통기한 9~24일 상품 오분류 방지"""
        assert classify_dessert_category("냉장디저트", 24) == DessertCategory.A

    def test_냉장젤리푸딩(self):
        assert classify_dessert_category("냉장젤리,푸딩", 90) == DessertCategory.D

    def test_냉동디저트(self):
        assert classify_dessert_category("냉동디저트", 180) == DessertCategory.D

    def test_상온디저트_단기(self):
        """상온디저트 유통기한 ≤20일 → B"""
        assert classify_dessert_category("상온디저트", 15) == DessertCategory.B

    def test_상온디저트_장기(self):
        """상온디저트 유통기한 >20일 → C"""
        assert classify_dessert_category("상온디저트", 30) == DessertCategory.C

    def test_상온디저트_경계값(self):
        """유통기한 = 20일 → B (이하)"""
        assert classify_dessert_category("상온디저트", 20) == DessertCategory.B

    def test_시즌디저트_유통기한_있음(self):
        assert classify_dessert_category("시즌디저트", 10) == DessertCategory.B

    def test_상온디저트_유통기한_없음(self):
        """상온디저트인데 유통기한 없으면 → B 기본값"""
        assert classify_dessert_category("상온디저트", None) == DessertCategory.B

    def test_둘다_없음_폴백_A(self):
        """소분류명도 유통기한도 없으면 → A (보수적)"""
        assert classify_dessert_category(None, None) == DessertCategory.A

    def test_미분류_유통기한만_있음(self):
        """소분류명 없고 유통기한만 있으면 → 유통기한 기반"""
        assert classify_dessert_category(None, 5) == DessertCategory.B
        assert classify_dessert_category(None, 30) == DessertCategory.C

    def test_유통기한_0_폴백(self):
        """유통기한 0 → 유효하지 않으므로 소분류 or 폴백"""
        assert classify_dessert_category(None, 0) == DessertCategory.A


# ============================================================================
# 1-2. Classifier 3차 안전장치 테스트
# ============================================================================


class TestClassifierSafetyOverride:
    """v1.2: C/D + 유통기한 짧으면 상향 조정"""

    def test_상수_값(self):
        assert SAFETY_SHORT_THRESHOLD == 20
        assert SAFETY_MEDIUM_THRESHOLD == 79

    def test_D_유통기한_15일_A상향(self):
        """냉장젤리,푸딩(D) + 유통기한 15일 → A 상향"""
        result = classify_dessert_category("냉장젤리,푸딩", 15)
        assert result == DessertCategory.A

    def test_D_유통기한_20일_A상향(self):
        """냉장젤리,푸딩(D) + 유통기한 20일(경계값) → A 상향"""
        result = classify_dessert_category("냉장젤리,푸딩", 20)
        assert result == DessertCategory.A

    def test_D_유통기한_50일_B상향(self):
        """냉동디저트(D) + 유통기한 50일 → B 상향"""
        result = classify_dessert_category("냉동디저트", 50)
        assert result == DessertCategory.B

    def test_D_유통기한_79일_B상향(self):
        """냉동디저트(D) + 유통기한 79일(경계값) → B 상향"""
        result = classify_dessert_category("냉동디저트", 79)
        assert result == DessertCategory.B

    def test_D_유통기한_80일_유지(self):
        """냉동디저트(D) + 유통기한 80일 → D 유지 (80 > 79)"""
        result = classify_dessert_category("냉동디저트", 80)
        assert result == DessertCategory.D

    def test_D_유통기한_90일_유지(self):
        """냉장젤리,푸딩(D) + 유통기한 90일 → D 유지"""
        result = classify_dessert_category("냉장젤리,푸딩", 90)
        assert result == DessertCategory.D

    def test_D_유통기한_None_유지(self):
        """냉장젤리,푸딩(D) + 유통기한 없음 → D 유지"""
        result = classify_dessert_category("냉장젤리,푸딩", None)
        assert result == DessertCategory.D

    def test_D_유통기한_0_유지(self):
        """냉동디저트(D) + 유통기한 0 → D 유지"""
        result = classify_dessert_category("냉동디저트", 0)
        assert result == DessertCategory.D

    def test_A_안전장치_미적용(self):
        """A는 이미 가장 엄격 → 안전장치 불필요"""
        result = _apply_safety_override(DessertCategory.A, 5, "냉장디저트")
        assert result == DessertCategory.A

    def test_B_안전장치_미적용(self):
        """B는 안전장치 대상 아님"""
        result = _apply_safety_override(DessertCategory.B, 10, "상온디저트")
        assert result == DessertCategory.B

    def test_냉장디저트_3일_A_유지(self):
        """냉장디저트(A) + 유통기한 3일 → A 유지 (안전장치 무관)"""
        result = classify_dessert_category("냉장디저트", 3)
        assert result == DessertCategory.A

    def test_safety_override_경계값_21일(self):
        """D + 유통기한 21일 → B 상향 (21 > 20, ≤ 79)"""
        result = _apply_safety_override(DessertCategory.D, 21, "냉동디저트")
        assert result == DessertCategory.B

    def test_C_유통기한_10일_A상향(self):
        """C + 유통기한 10일 → A 상향 (현실에선 발생 어려우나 안전장치)"""
        result = _apply_safety_override(DessertCategory.C, 10, None)
        assert result == DessertCategory.A

    def test_C_유통기한_50일_B상향(self):
        """C + 유통기한 50일 → B 상향"""
        result = _apply_safety_override(DessertCategory.C, 50, None)
        assert result == DessertCategory.B

    def test_C_유통기한_100일_유지(self):
        """C + 유통기한 100일 → C 유지"""
        result = _apply_safety_override(DessertCategory.C, 100, None)
        assert result == DessertCategory.C


# ============================================================================
# 2. Lifecycle 테스트 (~10개)
# ============================================================================
from src.prediction.categories.dessert_decision.lifecycle import (
    determine_lifecycle,
    NEW_PRODUCT_WEEKS,
    GROWTH_DECLINE_END_WEEKS,
)


class TestLifecycle:

    def test_cat_a_new_2주(self):
        """A 카테고리 2주 → NEW"""
        ref = "2026-03-04"
        first = "2026-02-20"  # 12일 전 → 1주
        phase, weeks = determine_lifecycle(first, ref, DessertCategory.A)
        assert phase == DessertLifecycle.NEW
        assert weeks < 4

    def test_cat_a_growth_5주(self):
        """A 카테고리 5주 → GROWTH_DECLINE"""
        ref = "2026-03-04"
        first = "2026-01-28"  # 35일 전 → 5주
        phase, weeks = determine_lifecycle(first, ref, DessertCategory.A)
        assert phase == DessertLifecycle.GROWTH_DECLINE

    def test_cat_a_established_10주(self):
        """A 카테고리 10주 → ESTABLISHED"""
        ref = "2026-03-04"
        first = "2025-12-24"  # 70일 전 → 10주
        phase, weeks = determine_lifecycle(first, ref, DessertCategory.A)
        assert phase == DessertLifecycle.ESTABLISHED

    def test_cat_b_new_2주(self):
        """B 카테고리 NEW는 3주"""
        ref = "2026-03-04"
        first = "2026-02-20"  # 12일 → 1주
        phase, _ = determine_lifecycle(first, ref, DessertCategory.B)
        assert phase == DessertLifecycle.NEW

    def test_cat_b_growth_4주(self):
        """B 카테고리 4주 → GROWTH_DECLINE (NEW는 3주까지)"""
        ref = "2026-03-04"
        first = "2026-02-04"  # 28일 → 4주
        phase, _ = determine_lifecycle(first, ref, DessertCategory.B)
        assert phase == DessertLifecycle.GROWTH_DECLINE

    def test_first_date_none(self):
        """첫 입고일 없으면 → ESTABLISHED, 999"""
        phase, weeks = determine_lifecycle(None, "2026-03-04", DessertCategory.A)
        assert phase == DessertLifecycle.ESTABLISHED
        assert weeks == 999

    def test_future_first_date(self):
        """미래 입고일 → NEW, 0주"""
        phase, weeks = determine_lifecycle("2026-04-01", "2026-03-04", DessertCategory.A)
        assert phase == DessertLifecycle.NEW
        assert weeks == 0

    def test_invalid_date_format(self):
        """잘못된 날짜 형식 → ESTABLISHED"""
        phase, _ = determine_lifecycle("invalid", "2026-03-04", DessertCategory.A)
        assert phase == DessertLifecycle.ESTABLISHED

    def test_exact_8주_boundary(self):
        """정확히 8주 → ESTABLISHED (8주 이상)"""
        ref = "2026-03-04"
        first = "2026-01-07"  # 56일 → 8주
        phase, weeks = determine_lifecycle(first, ref, DessertCategory.A)
        assert phase == DessertLifecycle.ESTABLISHED
        assert weeks == 8

    def test_new_product_weeks_config(self):
        """NEW_PRODUCT_WEEKS 설정값 확인"""
        assert NEW_PRODUCT_WEEKS[DessertCategory.A] == 4
        assert NEW_PRODUCT_WEEKS[DessertCategory.B] == 3
        assert NEW_PRODUCT_WEEKS[DessertCategory.C] == 4
        assert NEW_PRODUCT_WEEKS[DessertCategory.D] == 4

    # --- v1.3: source 파라미터 테스트 ---

    def test_source_products_bulk_forces_established(self):
        """products_bulk → 강제 ESTABLISHED, 실제 주차 계산"""
        ref = "2026-03-04"
        first = "2026-01-25"  # 38일 → 5주
        phase, weeks = determine_lifecycle(
            first, ref, DessertCategory.A,
            source=FirstReceivingSource.PRODUCTS_BULK,
        )
        assert phase == DessertLifecycle.ESTABLISHED
        assert weeks == 5  # 38 // 7

    def test_source_products_bulk_calculates_real_weeks(self):
        """products_bulk — 2주 전 등록이어도 ESTABLISHED (주차는 2)"""
        ref = "2026-03-04"
        first = "2026-02-18"  # 14일 → 2주
        phase, weeks = determine_lifecycle(
            first, ref, DessertCategory.B,
            source=FirstReceivingSource.PRODUCTS_BULK,
        )
        assert phase == DessertLifecycle.ESTABLISHED
        assert weeks == 2

    def test_source_products_bulk_none_date(self):
        """products_bulk + None 날짜 → ESTABLISHED, 999"""
        phase, weeks = determine_lifecycle(
            None, "2026-03-04", DessertCategory.A,
            source=FirstReceivingSource.PRODUCTS_BULK,
        )
        assert phase == DessertLifecycle.ESTABLISHED
        assert weeks == 999

    def test_source_none_returns_none_lifecycle(self):
        """source=NONE → lifecycle=None, weeks=0"""
        phase, weeks = determine_lifecycle(
            None, "2026-03-04", DessertCategory.A,
            source=FirstReceivingSource.NONE,
        )
        assert phase is None
        assert weeks == 0

    def test_source_daily_sales_sold_normal(self):
        """DAILY_SALES_SOLD → 기존 로직 정상 적용"""
        ref = "2026-03-04"
        first = "2026-02-20"  # 12일 → 1주
        phase, weeks = determine_lifecycle(
            first, ref, DessertCategory.A,
            source=FirstReceivingSource.DAILY_SALES_SOLD,
        )
        assert phase == DessertLifecycle.NEW

    def test_source_default_backward_compatible(self):
        """source 미지정(None) → 기존 호환 (None date → ESTABLISHED, 999)"""
        phase, weeks = determine_lifecycle(None, "2026-03-04", DessertCategory.A)
        assert phase == DessertLifecycle.ESTABLISHED
        assert weeks == 999


# ============================================================================
# 2-2. FirstReceivingSource Enum 테스트
# ============================================================================


class TestFirstReceivingSource:
    """FirstReceivingSource Enum 검증"""

    def test_enum_values(self):
        assert len(FirstReceivingSource) == 6
        assert FirstReceivingSource.DETECTED.value == "detected_new_products"
        assert FirstReceivingSource.DAILY_SALES_SOLD.value == "daily_sales_sold"
        assert FirstReceivingSource.DAILY_SALES_BOUGHT.value == "daily_sales_bought"
        assert FirstReceivingSource.PRODUCTS.value == "products"
        assert FirstReceivingSource.PRODUCTS_BULK.value == "products_bulk"
        assert FirstReceivingSource.NONE.value == "none"

    def test_str_enum_compatibility(self):
        """str, Enum 상속이므로 문자열 비교 가능"""
        source = FirstReceivingSource.DAILY_SALES_SOLD
        assert source == "daily_sales_sold"
        assert isinstance(source, str)


# ============================================================================
# 3. Judge 테스트 (~25개)
# ============================================================================
from src.prediction.categories.dessert_decision.judge import (
    calc_sale_rate,
    count_consecutive_low_weeks,
    count_consecutive_zero_months,
    judge_category_a,
    judge_category_b,
    judge_category_c,
    judge_category_d,
    judge_item,
)
from src.prediction.categories.dessert_decision.models import DessertSalesMetrics


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

    def test_one_low_then_ok(self):
        assert count_consecutive_low_weeks([0.4, 0.6, 0.2], 0.5) == 1

    def test_empty(self):
        assert count_consecutive_low_weeks([], 0.5) == 0


class TestConsecutiveZeroMonths:

    def test_two_zeros(self):
        assert count_consecutive_zero_months([0, 0, 5]) == 2

    def test_no_zeros(self):
        assert count_consecutive_zero_months([1, 2, 3]) == 0

    def test_all_zeros(self):
        assert count_consecutive_zero_months([0, 0, 0]) == 3


class TestJudgeCategoryA:

    def _metrics(self, **kw) -> DessertSalesMetrics:
        defaults = {
            "total_sale_qty": 10, "total_disuse_qty": 2,
            "sale_amount": 10000, "disuse_amount": 2000,
            "sale_rate": 0.8, "category_avg_sale_qty": 20.0,
            "prev_period_sale_qty": 10, "sale_trend_pct": 0.0,
            "weekly_sale_rates": [0.8, 0.7, 0.6],
        }
        defaults.update(kw)
        return DessertSalesMetrics(**defaults)

    def test_new_keep(self):
        decision, _, _ = judge_category_a(DessertLifecycle.NEW, self._metrics())
        assert decision == DessertDecisionType.KEEP

    def test_new_rapid_decline_warning(self):
        m = self._metrics(sale_trend_pct=-55.0, prev_period_sale_qty=10)
        decision, reason, warning = judge_category_a(DessertLifecycle.NEW, m)
        assert decision == DessertDecisionType.WATCH
        assert warning is True

    def test_waste_exceeds_sale_new_protected(self):
        """NEW 보호: 폐기금액 > 판매금액이어도 신상품은 KEEP/WATCH"""
        m = self._metrics(sale_amount=1000, disuse_amount=2000)
        decision, _, _ = judge_category_a(DessertLifecycle.NEW, m)
        assert decision in (DessertDecisionType.KEEP, DessertDecisionType.WATCH)

    def test_waste_rate_150_stop(self):
        """폐기율 150%+ → STOP_RECOMMEND (ESTABLISHED)"""
        m = self._metrics(total_sale_qty=2, total_disuse_qty=3)
        decision, _, _ = judge_category_a(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.STOP_RECOMMEND

    def test_growth_2week_low_reduce(self):
        """판매율 50% 미만 2주 연속 → REDUCE_ORDER (성장/하락기)"""
        m = self._metrics(weekly_sale_rates=[0.3, 0.4, 0.8])
        decision, _, _ = judge_category_a(DessertLifecycle.GROWTH_DECLINE, m)
        assert decision == DessertDecisionType.REDUCE_ORDER

    def test_growth_2week_30pct_stop(self):
        """판매율 30% 미만 2주 연속 → STOP_RECOMMEND (성장/하락기)"""
        m = self._metrics(weekly_sale_rates=[0.1, 0.2, 0.8])
        decision, _, _ = judge_category_a(DessertLifecycle.GROWTH_DECLINE, m)
        assert decision == DessertDecisionType.STOP_RECOMMEND

    def test_growth_1week_low_watch(self):
        m = self._metrics(weekly_sale_rates=[0.3, 0.6, 0.8])
        decision, _, _ = judge_category_a(DessertLifecycle.GROWTH_DECLINE, m)
        assert decision == DessertDecisionType.WATCH

    def test_growth_normal_keep(self):
        m = self._metrics(weekly_sale_rates=[0.6, 0.7, 0.8])
        decision, _, _ = judge_category_a(DessertLifecycle.GROWTH_DECLINE, m)
        assert decision == DessertDecisionType.KEEP

    def test_established_low_avg_stop(self):
        m = self._metrics(total_sale_qty=5, category_avg_sale_qty=20.0)
        decision, _, _ = judge_category_a(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.STOP_RECOMMEND

    def test_established_normal_keep(self):
        m = self._metrics(total_sale_qty=10, category_avg_sale_qty=20.0)
        decision, _, _ = judge_category_a(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.KEEP


class TestJudgeCategoryB:

    def _metrics(self, **kw) -> DessertSalesMetrics:
        defaults = {"weekly_sale_rates": [0.6, 0.5, 0.5]}
        defaults.update(kw)
        return DessertSalesMetrics(**defaults)

    def test_new_keep(self):
        decision, _, _ = judge_category_b(DessertLifecycle.NEW, self._metrics())
        assert decision == DessertDecisionType.KEEP

    def test_3week_low_stop(self):
        m = self._metrics(weekly_sale_rates=[0.2, 0.3, 0.35, 0.5])
        decision, _, _ = judge_category_b(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.STOP_RECOMMEND

    def test_2week_low_watch(self):
        m = self._metrics(weekly_sale_rates=[0.2, 0.3, 0.5])
        decision, _, _ = judge_category_b(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.WATCH

    def test_normal_keep(self):
        m = self._metrics(weekly_sale_rates=[0.5, 0.6, 0.7])
        decision, _, _ = judge_category_b(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.KEEP


class TestJudgeCategoryC:

    def _metrics(self, **kw) -> DessertSalesMetrics:
        defaults = {"total_sale_qty": 10, "category_avg_sale_qty": 20.0}
        defaults.update(kw)
        return DessertSalesMetrics(**defaults)

    def test_new_keep(self):
        decision, _, _ = judge_category_c(DessertLifecycle.NEW, self._metrics())
        assert decision == DessertDecisionType.KEEP

    def test_low_avg_stop(self):
        m = self._metrics(total_sale_qty=3, category_avg_sale_qty=20.0)
        decision, _, _ = judge_category_c(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.STOP_RECOMMEND

    def test_normal_keep(self):
        m = self._metrics(total_sale_qty=10, category_avg_sale_qty=20.0)
        decision, _, _ = judge_category_c(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.KEEP


class TestJudgeCategoryD:

    def test_2month_zero_stop(self):
        m = DessertSalesMetrics(consecutive_zero_months=2)
        decision, _, _ = judge_category_d(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.STOP_RECOMMEND

    def test_1month_zero_watch(self):
        m = DessertSalesMetrics(consecutive_zero_months=1)
        decision, _, _ = judge_category_d(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.WATCH

    def test_normal_keep(self):
        m = DessertSalesMetrics(consecutive_zero_months=0)
        decision, _, _ = judge_category_d(DessertLifecycle.ESTABLISHED, m)
        assert decision == DessertDecisionType.KEEP


class TestJudgeItemDispatcher:

    def test_dispatches_to_correct_category(self):
        m = DessertSalesMetrics(
            weekly_sale_rates=[0.6, 0.5],
            consecutive_zero_months=0,
        )
        # A 카테고리 → judge_category_a 호출
        d, _, _ = judge_item(DessertCategory.A, DessertLifecycle.NEW, m)
        assert d == DessertDecisionType.KEEP

        # D 카테고리 → judge_category_d 호출
        d, _, _ = judge_item(DessertCategory.D, DessertLifecycle.ESTABLISHED, m)
        assert d == DessertDecisionType.KEEP


# ============================================================================
# 4. Enums 테스트 (~5개)
# ============================================================================
class TestEnums:

    def test_category_values(self):
        assert DessertCategory.A.value == "A"
        assert DessertCategory.D.value == "D"

    def test_lifecycle_values(self):
        assert DessertLifecycle.NEW.value == "new"
        assert DessertLifecycle.ESTABLISHED.value == "established"

    def test_decision_values(self):
        assert DessertDecisionType.KEEP.value == "KEEP"
        assert DessertDecisionType.STOP_RECOMMEND.value == "STOP_RECOMMEND"

    def test_judgment_cycle_mapping(self):
        assert CATEGORY_JUDGMENT_CYCLE[DessertCategory.A] == JudgmentCycle.WEEKLY
        assert CATEGORY_JUDGMENT_CYCLE[DessertCategory.B] == JudgmentCycle.BIWEEKLY
        assert CATEGORY_JUDGMENT_CYCLE[DessertCategory.C] == JudgmentCycle.MONTHLY
        assert CATEGORY_JUDGMENT_CYCLE[DessertCategory.D] == JudgmentCycle.MONTHLY

    def test_str_enum_comparison(self):
        """str(Enum) 비교 가능"""
        assert DessertCategory.A == "A"
        assert DessertDecisionType.KEEP == "KEEP"


# ============================================================================
# 5. Repository 테스트 (~10개)
# ============================================================================
class TestDessertDecisionRepository:

    @pytest.fixture
    def repo_with_db(self, tmp_path):
        """in-memory DB 기반 Repository"""
        from src.infrastructure.database.repos.dessert_decision_repo import (
            DessertDecisionRepository,
        )

        db_path = tmp_path / "test_store.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dessert_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT NOT NULL,
                item_cd TEXT NOT NULL,
                item_nm TEXT,
                mid_cd TEXT DEFAULT '014',
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

        repo = DessertDecisionRepository(store_id="99999")
        repo._get_conn = lambda: sqlite3.connect(str(db_path))
        repo._get_conn_rr = repo._get_conn
        return repo

    def _sample_decision(self, item_cd="ITEM001", decision="KEEP", category="A",
                         period_end="2026-03-04"):
        return {
            "store_id": "99999",
            "item_cd": item_cd,
            "item_nm": "테스트상품",
            "mid_cd": "014",
            "dessert_category": category,
            "expiration_days": 3,
            "small_nm": "냉장디저트",
            "lifecycle_phase": "established",
            "first_receiving_date": "2025-12-01",
            "first_receiving_source": "daily_sales",
            "weeks_since_intro": 13,
            "judgment_period_start": "2026-02-25",
            "judgment_period_end": period_end,
            "total_order_qty": 20,
            "total_sale_qty": 15,
            "total_disuse_qty": 5,
            "sale_amount": 15000,
            "disuse_amount": 5000,
            "sell_price": 1000,
            "sale_rate": 0.75,
            "category_avg_sale_qty": 20.0,
            "prev_period_sale_qty": 12,
            "sale_trend_pct": 25.0,
            "consecutive_low_weeks": 0,
            "consecutive_zero_months": 0,
            "decision": decision,
            "decision_reason": "정상",
            "is_rapid_decline_warning": 0,
            "judgment_cycle": "weekly",
        }

    def test_save_and_retrieve(self, repo_with_db):
        decisions = [self._sample_decision()]
        saved = repo_with_db.save_decisions_batch(decisions)
        assert saved == 1

        latest = repo_with_db.get_latest_decisions()
        assert len(latest) == 1
        assert latest[0]["item_cd"] == "ITEM001"

    def test_upsert_same_key(self, repo_with_db):
        """동일 키 UPSERT 시 덮어쓰기"""
        repo_with_db.save_decisions_batch([self._sample_decision(decision="KEEP")])
        repo_with_db.save_decisions_batch([self._sample_decision(decision="STOP_RECOMMEND")])

        latest = repo_with_db.get_latest_decisions()
        assert len(latest) == 1
        assert latest[0]["decision"] == "STOP_RECOMMEND"

    def test_get_confirmed_stop_items_empty(self, repo_with_db):
        """CONFIRMED_STOP 없으면 빈 set"""
        repo_with_db.save_decisions_batch([self._sample_decision(decision="STOP_RECOMMEND")])
        stops = repo_with_db.get_confirmed_stop_items()
        assert len(stops) == 0  # STOP_RECOMMEND만으로는 포함 안 됨

    def test_get_confirmed_stop_items_with_action(self, repo_with_db):
        """operator_action='CONFIRMED_STOP'인 것만 반환"""
        repo_with_db.save_decisions_batch([self._sample_decision(decision="STOP_RECOMMEND")])
        repo_with_db.update_operator_action(1, "CONFIRMED_STOP", "운영자 확인")
        stops = repo_with_db.get_confirmed_stop_items()
        assert "ITEM001" in stops

    def test_get_stop_recommended_items(self, repo_with_db):
        repo_with_db.save_decisions_batch([
            self._sample_decision(item_cd="A001", decision="STOP_RECOMMEND"),
            self._sample_decision(item_cd="A002", decision="KEEP"),
        ])
        stops = repo_with_db.get_stop_recommended_items()
        assert "A001" in stops
        assert "A002" not in stops

    def test_update_operator_action(self, repo_with_db):
        repo_with_db.save_decisions_batch([self._sample_decision()])
        updated = repo_with_db.update_operator_action(1, "OVERRIDE_KEEP", "테스트")
        assert updated is True

    def test_get_decision_summary(self, repo_with_db):
        repo_with_db.save_decisions_batch([
            self._sample_decision(item_cd="A001", decision="KEEP", category="A"),
            self._sample_decision(item_cd="A002", decision="STOP_RECOMMEND", category="A"),
            self._sample_decision(item_cd="B001", decision="KEEP", category="B"),
        ])
        summary = repo_with_db.get_decision_summary()
        assert len(summary) > 0

    def test_get_item_history(self, repo_with_db):
        """상품별 이력 조회"""
        repo_with_db.save_decisions_batch([
            self._sample_decision(item_cd="A001", period_end="2026-02-25"),
            self._sample_decision(item_cd="A001", period_end="2026-03-04"),
        ])
        history = repo_with_db.get_item_decision_history("A001")
        assert len(history) == 2

    def test_save_empty_list(self, repo_with_db):
        saved = repo_with_db.save_decisions_batch([])
        assert saved == 0


# ============================================================================
# 6. OrderFilter 연동 테스트 (~5개)
# ============================================================================
class TestOrderFilterDessertStop:

    @pytest.fixture
    def order_filter(self):
        from src.order.order_filter import OrderFilter
        return OrderFilter(store_id="99999")

    def _make_order_list(self, item_codes):
        return [{"item_cd": cd, "item_nm": f"상품{cd}", "mid_cd": "014",
                 "order_qty": 5} for cd in item_codes]

    @patch("src.order.order_filter.ExclusionType")
    def test_dessert_stop_attribute_exists(self, mock_et):
        """ExclusionType.DESSERT_STOP 상수 존재"""
        from src.infrastructure.database.repos.order_exclusion_repo import ExclusionType
        assert hasattr(ExclusionType, "DESSERT_STOP")
        assert ExclusionType.DESSERT_STOP == "DESSERT_STOP"
        assert "DESSERT_STOP" in ExclusionType.ALL

    @patch("src.settings.constants.DESSERT_DECISION_ENABLED", True)
    @patch("src.infrastructure.database.repos.DessertDecisionRepository")
    @patch("src.infrastructure.database.repos.StoppedItemRepository")
    @patch("src.infrastructure.database.repos.AppSettingsRepository")
    def test_confirmed_stop_excluded(self, mock_settings, mock_stopped, mock_dessert,
                                      order_filter):
        """CONFIRMED_STOP 상품이 발주 목록에서 제외"""
        mock_settings.return_value.get.return_value = True
        mock_stopped.return_value.get_active_item_codes.return_value = set()
        mock_dessert.return_value.get_confirmed_stop_items.return_value = {"ITEM002"}

        orders = self._make_order_list(["ITEM001", "ITEM002", "ITEM003"])
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
        assert "ITEM002" not in item_cds
        assert "ITEM001" in item_cds
        assert "ITEM003" in item_cds

    @patch("src.settings.constants.DESSERT_DECISION_ENABLED", False)
    @patch("src.infrastructure.database.repos.StoppedItemRepository")
    @patch("src.infrastructure.database.repos.AppSettingsRepository")
    def test_disabled_no_exclusion(self, mock_settings, mock_stopped, order_filter):
        """DESSERT_DECISION_ENABLED=False면 필터 안 함"""
        mock_settings.return_value.get.return_value = True
        mock_stopped.return_value.get_active_item_codes.return_value = set()

        orders = self._make_order_list(["ITEM001", "ITEM002"])
        exclusion_records = []

        result = order_filter.exclude_filtered_items(
            order_list=orders,
            unavailable_items=set(),
            cut_items=set(),
            auto_order_items=set(),
            smart_order_items=set(),
            exclusion_records=exclusion_records,
        )

        assert len(result) == 2  # 아무것도 제외 안 됨

    @patch("src.settings.constants.DESSERT_DECISION_ENABLED", True)
    @patch("src.infrastructure.database.repos.DessertDecisionRepository")
    @patch("src.infrastructure.database.repos.StoppedItemRepository")
    @patch("src.infrastructure.database.repos.AppSettingsRepository")
    def test_stop_recommend_not_excluded(self, mock_settings, mock_stopped, mock_dessert,
                                         order_filter):
        """STOP_RECOMMEND만으로는 차단 안 함 (CONFIRMED_STOP 필요)"""
        mock_settings.return_value.get.return_value = True
        mock_stopped.return_value.get_active_item_codes.return_value = set()
        mock_dessert.return_value.get_confirmed_stop_items.return_value = set()

        orders = self._make_order_list(["ITEM001"])
        exclusion_records = []

        result = order_filter.exclude_filtered_items(
            order_list=orders,
            unavailable_items=set(),
            cut_items=set(),
            auto_order_items=set(),
            smart_order_items=set(),
            exclusion_records=exclusion_records,
        )

        assert len(result) == 1


# ============================================================================
# 7. Flow 테스트 (~3개)
# ============================================================================
class TestDessertDecisionFlow:

    def test_no_store_id_falls_back_to_default(self):
        from src.application.use_cases.dessert_decision_flow import DessertDecisionFlow
        from src.settings.constants import DEFAULT_STORE_ID
        flow = DessertDecisionFlow(store_id="")
        assert flow.store_id == DEFAULT_STORE_ID

    @patch("src.application.use_cases.dessert_decision_flow.DessertDecisionService")
    def test_delegates_to_service(self, mock_svc_cls):
        from src.application.use_cases.dessert_decision_flow import DessertDecisionFlow

        mock_svc_cls.return_value.run.return_value = {"total_items": 5}
        flow = DessertDecisionFlow(store_id="99999")
        result = flow.run(target_categories=["A"])

        mock_svc_cls.return_value.run.assert_called_once_with(
            target_categories=["A"],
            reference_date=None,
        )
        assert result["total_items"] == 5


# ============================================================================
# 8. Scheduler 래퍼 테스트 (~3개)
# ============================================================================
class TestSchedulerWrappers:

    @patch("run_scheduler.dessert_decision_wrapper")
    def test_biweekly_even_week_runs(self, mock_wrapper):
        """짝수 ISO 주에 실행"""
        with patch("run_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value.isocalendar.return_value = (2026, 10, 1)  # 짝수주
            mock_dt.now.return_value.isoformat.return_value = "2026-03-09T22:15:00"

            import run_scheduler
            run_scheduler.dessert_biweekly_wrapper()
            mock_wrapper.assert_called_once_with(["B"])

    @patch("run_scheduler.dessert_decision_wrapper")
    def test_biweekly_odd_week_skips(self, mock_wrapper):
        """홀수 ISO 주에 건너뜀"""
        with patch("run_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value.isocalendar.return_value = (2026, 9, 1)  # 홀수주

            import run_scheduler
            run_scheduler.dessert_biweekly_wrapper()
            mock_wrapper.assert_not_called()

    @patch("run_scheduler.dessert_decision_wrapper")
    def test_monthly_1st_runs(self, mock_wrapper):
        """매월 1일에만 실행"""
        with patch("run_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value.day = 1

            import run_scheduler
            run_scheduler.dessert_monthly_wrapper()
            mock_wrapper.assert_called_once_with(["C", "D"])

    @patch("run_scheduler.dessert_decision_wrapper")
    def test_monthly_not_1st_skips(self, mock_wrapper):
        """1일이 아니면 건너뜀"""
        with patch("run_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value.day = 15

            import run_scheduler
            run_scheduler.dessert_monthly_wrapper()
            mock_wrapper.assert_not_called()


# ============================================================================
# 9. BULK_DATES 상수 테스트
# ============================================================================
from src.settings.constants import DESSERT_BULK_REGISTRATION_DATES


class TestBulkDatesConstant:
    """DESSERT_BULK_REGISTRATION_DATES 상수 검증"""

    def test_bulk_dates_count(self):
        assert len(DESSERT_BULK_REGISTRATION_DATES) == 3

    def test_bulk_dates_values(self):
        assert '2026-01-25' in DESSERT_BULK_REGISTRATION_DATES
        assert '2026-01-26' in DESSERT_BULK_REGISTRATION_DATES
        assert '2026-02-06' in DESSERT_BULK_REGISTRATION_DATES

    def test_non_bulk_date(self):
        assert '2026-02-15' not in DESSERT_BULK_REGISTRATION_DATES


# ============================================================================
# 10. 메인 루프 SKIP 테스트
# ============================================================================
from src.application.services.dessert_decision_service import DessertDecisionService


class TestMainLoopSkip:
    """run() 메인 루프에서 source 기반 SKIP 처리 테스트"""

    @patch.object(DessertDecisionService, '_load_dessert_items')
    @patch.object(DessertDecisionService, '_resolve_first_receiving_date')
    @patch.object(DessertDecisionService, '_aggregate_metrics')
    @patch.object(DessertDecisionService, '_get_category_avg_sale')
    def test_source_none_skipped(self, mock_avg, mock_agg, mock_resolve, mock_load):
        """source=NONE 상품은 results에 포함되지 않음"""
        from src.prediction.categories.dessert_decision.models import (
            DessertItemContext, DessertSalesMetrics,
        )

        ctx1 = DessertItemContext(item_cd="SKIP001", item_nm="스킵상품",
                                  small_nm="냉장디저트", expiration_days=3, sell_price=3000)
        ctx2 = DessertItemContext(item_cd="KEEP001", item_nm="유지상품",
                                  small_nm="냉장디저트", expiration_days=3, sell_price=3000)

        mock_load.return_value = [ctx1, ctx2]
        mock_resolve.side_effect = [
            (None, FirstReceivingSource.NONE),       # SKIP001 → SKIP
            ("2025-10-01", FirstReceivingSource.DAILY_SALES_SOLD),  # KEEP001 → 정상
        ]
        mock_agg.return_value = DessertSalesMetrics(
            period_start="2026-02-25", period_end="2026-03-04",
            total_sale_qty=10, total_disuse_qty=2,
            sale_rate=0.83, sale_amount=30000, disuse_amount=6000,
            category_avg_sale_qty=8.0, prev_period_sale_qty=9,
        )

        service = DessertDecisionService.__new__(DessertDecisionService)
        service.store_id = "99999"
        service.decision_repo = MagicMock()
        service.decision_repo.save_decisions_batch.return_value = 1

        result = service.run(target_categories=None, reference_date="2026-03-04")

        assert result["total_items"] == 1
        assert result["results"][0]["item_cd"] == "KEEP001"

    @patch.object(DessertDecisionService, '_load_dessert_items')
    @patch.object(DessertDecisionService, '_resolve_first_receiving_date')
    @patch.object(DessertDecisionService, '_aggregate_metrics')
    def test_products_bulk_becomes_established(self, mock_agg, mock_resolve, mock_load):
        """products_bulk 소스 상품은 ESTABLISHED로 처리"""
        from src.prediction.categories.dessert_decision.models import (
            DessertItemContext, DessertSalesMetrics,
        )

        ctx = DessertItemContext(item_cd="BULK001", item_nm="벌크상품",
                                 small_nm="냉장디저트", expiration_days=3, sell_price=3000)
        mock_load.return_value = [ctx]
        mock_resolve.return_value = ("2026-01-25", FirstReceivingSource.PRODUCTS_BULK)
        mock_agg.return_value = DessertSalesMetrics(
            period_start="2026-02-25", period_end="2026-03-04",
            total_sale_qty=5, total_disuse_qty=1,
            sale_rate=0.83, sale_amount=15000, disuse_amount=3000,
            category_avg_sale_qty=8.0, prev_period_sale_qty=4,
        )

        service = DessertDecisionService.__new__(DessertDecisionService)
        service.store_id = "99999"
        service.decision_repo = MagicMock()
        service.decision_repo.save_decisions_batch.return_value = 1

        result = service.run(target_categories=None, reference_date="2026-03-04")

        assert result["total_items"] == 1
        assert result["results"][0]["lifecycle_phase"] == "established"
        assert result["results"][0]["first_receiving_source"] == "products_bulk"

    @patch.object(DessertDecisionService, '_load_dessert_items')
    @patch.object(DessertDecisionService, '_resolve_first_receiving_date')
    def test_all_none_returns_empty(self, mock_resolve, mock_load):
        """모든 상품 source=NONE이면 results 비어있음"""
        from src.prediction.categories.dessert_decision.models import DessertItemContext

        ctx = DessertItemContext(item_cd="NONE001", item_nm="없는상품",
                                 small_nm="냉장디저트", expiration_days=3, sell_price=3000)
        mock_load.return_value = [ctx]
        mock_resolve.return_value = (None, FirstReceivingSource.NONE)

        service = DessertDecisionService.__new__(DessertDecisionService)
        service.store_id = "99999"
        service.decision_repo = MagicMock()
        service.decision_repo.save_decisions_batch.return_value = 0

        result = service.run(target_categories=None, reference_date="2026-03-04")

        assert result["total_items"] == 0
        assert result["results"] == []
