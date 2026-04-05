"""
행사 기반 발주 조정 모듈 테스트

PromotionAdjuster의 핵심 케이스:
1. 행사 종료 임박 (promo_avg 있음/없음)
2. 행사 시작 임박
3. 행사 중 (배율 적용)
4. 행사 정보 없음
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import date, timedelta

from src.prediction.promotion.promotion_adjuster import PromotionAdjuster, AdjustmentResult
from src.prediction.promotion.promotion_manager import PromotionManager, PromotionStatus


def _make_status(
    item_cd="TEST001",
    current_promo=None,
    current_end_date=None,
    days_until_end=None,
    next_promo=None,
    next_start_date=None,
    will_change=False,
    change_type=None,
    normal_avg=2.0,
    promo_avg=0.0,
    promo_multiplier=1.0,
):
    return PromotionStatus(
        item_cd=item_cd,
        item_nm="테스트상품",
        current_promo=current_promo,
        current_end_date=current_end_date,
        days_until_end=days_until_end,
        next_promo=next_promo,
        next_start_date=next_start_date,
        will_change=will_change,
        change_type=change_type,
        normal_avg=normal_avg,
        promo_avg=promo_avg,
        promo_multiplier=promo_multiplier,
    )


@pytest.fixture
def mock_manager():
    mgr = MagicMock(spec=PromotionManager)
    mgr.get_promo_multiplier.return_value = 2.0
    return mgr


@pytest.fixture
def adjuster(mock_manager):
    return PromotionAdjuster(promo_manager=mock_manager)


# ─────────────────────────────────────────────
# 케이스 1: 행사 종료 임박 (다음 행사 없음)
# ─────────────────────────────────────────────


class TestEndingPromo:
    """행사 종료 임박 시 발주 감소"""

    def test_end_d1_with_promo_stats(self, adjuster, mock_manager):
        """D-1 + promo_avg 있음 → 정밀 계산"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-02-28",
            days_until_end=1,
            next_promo=None,
            normal_avg=2.0,
            promo_avg=5.0,
            will_change=True,
            change_type="end",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10, current_stock=3, pending_qty=0)

        # expected_sales = 5.0 * 1 = 5
        # target_stock = 2.0 * 2 = 4
        # needed = 5 + 4 - 3 - 0 = 6
        assert result.adjusted_qty == 6
        assert "행사 종료 D-1" in result.adjustment_reason

    def test_end_d1_without_promo_stats(self, adjuster, mock_manager):
        """D-1 + promo_avg=0 → factor 기반 감소 (10%)"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-02-28",
            days_until_end=1,
            next_promo=None,
            normal_avg=2.0,
            promo_avg=0.0,  # 통계 미산출
            will_change=True,
            change_type="end",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10, current_stock=0, pending_qty=0)

        # factor=0.10 → 10 * 0.10 = 1
        assert result.adjusted_qty == 1
        assert result.adjusted_qty < 10  # 반드시 감소해야 함

    def test_end_d0_without_promo_stats(self, adjuster, mock_manager):
        """D-0 + promo_avg=0 → factor=0.0 → 발주 중단"""
        status = _make_status(
            current_promo="2+1",
            current_end_date="2026-02-27",
            days_until_end=0,
            next_promo=None,
            promo_avg=0.0,
            will_change=True,
            change_type="end",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10, current_stock=5)

        assert result.adjusted_qty == 0

    def test_end_d2_with_promo_stats(self, adjuster, mock_manager):
        """D-2 + promo_avg 있음 → 정밀 계산"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-03-01",
            days_until_end=2,
            next_promo=None,
            normal_avg=3.0,
            promo_avg=8.0,
            will_change=True,
            change_type="end",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=15, current_stock=10, pending_qty=5)

        # expected_sales = 8.0 * 2 = 16
        # target_stock = 3.0 * 2 = 6
        # needed = 16 + 6 - 10 - 5 = 7
        assert result.adjusted_qty == 7

    def test_end_d3_without_promo_stats(self, adjuster, mock_manager):
        """D-3 + promo_avg=0 → factor=0.50 → 50% 감소"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-03-02",
            days_until_end=3,
            next_promo=None,
            promo_avg=0.0,
            will_change=True,
            change_type="end",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        # factor=0.50 → 10 * 0.50 = 5
        assert result.adjusted_qty == 5

    def test_end_never_increases_without_stats(self, adjuster, mock_manager):
        """promo_avg=0일 때 발주량이 절대 base_qty보다 증가하면 안 됨"""
        for d in range(4):
            status = _make_status(
                current_promo="1+1",
                days_until_end=d,
                next_promo=None,
                promo_avg=0.0,
                normal_avg=5.0,
                will_change=True,
                change_type="end",
            )
            mock_manager.get_promotion_status.return_value = status

            result = adjuster.adjust_order_quantity("TEST001", base_qty=10, current_stock=0)

            assert result.adjusted_qty <= 10, (
                f"D-{d}: base=10 but adjusted={result.adjusted_qty} (must not increase)"
            )


# ─────────────────────────────────────────────
# 케이스 1b: 행사 종료 + 다음 행사 있음
# ─────────────────────────────────────────────


class TestEndingWithNextPromo:
    """행사 종료이지만 다음 행사가 있는 경우"""

    def test_same_promo_continues(self, adjuster, mock_manager):
        """같은 행사 연속 → 조정 없음"""
        status = _make_status(
            current_promo="1+1",
            days_until_end=1,
            next_promo="1+1",
            next_start_date="2026-03-01",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        assert result.adjusted_qty == 10
        assert "동일 행사 연속" in result.adjustment_reason

    def test_different_promo_change(self, adjuster, mock_manager):
        """다른 행사 변경 → 80% 감소"""
        status = _make_status(
            current_promo="1+1",
            days_until_end=1,
            next_promo="2+1",
            next_start_date="2026-03-01",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        assert result.adjusted_qty == 8  # 10 * 0.8
        assert "행사 변경 예정" in result.adjustment_reason


# ─────────────────────────────────────────────
# 케이스 2: 행사 시작 임박
# ─────────────────────────────────────────────


class TestStartingPromo:
    """행사 시작 임박 시 발주 증가"""

    def test_start_d2(self, adjuster, mock_manager):
        """D-2 → 150% 증가"""
        today = date.today()
        start = (today + timedelta(days=2)).strftime('%Y-%m-%d')
        status = _make_status(
            current_promo=None,
            next_promo="1+1",
            next_start_date=start,
            will_change=True,
            change_type="start",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        assert result.adjusted_qty == 15  # 10 * 1.50
        assert "행사 시작 D-2" in result.adjustment_reason

    def test_start_d1(self, adjuster, mock_manager):
        """D-1 → 200% 증가"""
        today = date.today()
        start = (today + timedelta(days=1)).strftime('%Y-%m-%d')
        status = _make_status(
            current_promo=None,
            next_promo="2+1",
            next_start_date=start,
            will_change=True,
            change_type="start",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=5)

        assert result.adjusted_qty == 10  # 5 * 2.0
        assert "행사 시작 D-1" in result.adjustment_reason

    def test_start_d3(self, adjuster, mock_manager):
        """D-3 → 120% 증가"""
        today = date.today()
        start = (today + timedelta(days=3)).strftime('%Y-%m-%d')
        status = _make_status(
            current_promo=None,
            next_promo="1+1",
            next_start_date=start,
            will_change=True,
            change_type="start",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        assert result.adjusted_qty == 12  # 10 * 1.20

    def test_start_d0_uses_multiplier(self, adjuster, mock_manager):
        """D-0 → 행사 배율 적용"""
        today = date.today()
        start = today.strftime('%Y-%m-%d')
        status = _make_status(
            current_promo=None,
            next_promo="1+1",
            next_start_date=start,
            promo_multiplier=3.5,
            will_change=True,
            change_type="start",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=4)

        assert result.adjusted_qty == 14  # 4 * 3.5

    def test_start_d4_no_adjustment(self, adjuster, mock_manager):
        """D-4 → 아직 조정 안함"""
        today = date.today()
        start = (today + timedelta(days=4)).strftime('%Y-%m-%d')
        status = _make_status(
            current_promo=None,
            next_promo="1+1",
            next_start_date=start,
            will_change=True,
            change_type="start",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        assert result.adjusted_qty == 10  # 변경 없음


# ─────────────────────────────────────────────
# 케이스 3: 현재 행사 중
# ─────────────────────────────────────────────


class TestInPromotion:
    """현재 행사 중 배율 적용"""

    def test_in_promo_applies_multiplier(self, adjuster, mock_manager):
        """행사 중 → 배율 적용"""
        status = _make_status(
            current_promo="1+1",
            days_until_end=10,
            promo_multiplier=2.5,
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=4)

        assert result.adjusted_qty == 10  # 4 * 2.5
        assert "행사 중" in result.adjustment_reason


# ─────────────────────────────────────────────
# 케이스 4: 행사 정보 없음
# ─────────────────────────────────────────────


class TestNoPromo:
    """행사 정보 없는 경우"""

    def test_no_status(self, adjuster, mock_manager):
        """상태 None → 조정 없음"""
        mock_manager.get_promotion_status.return_value = None

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        assert result.adjusted_qty == 10
        assert result.adjustment_factor == 1.0
        assert "행사 정보 없음" in result.adjustment_reason


# ─────────────────────────────────────────────
# _calculate_end_adjustment 직접 테스트
# ─────────────────────────────────────────────


class TestCalculateEndAdjustment:
    """행사 종료 정밀 계산"""

    def test_basic_calculation(self, adjuster):
        """기본 계산: 남은 판매 + 목표재고 - 현재고 - 미입고"""
        result = adjuster._calculate_end_adjustment(
            base_qty=10,
            current_stock=5,
            pending_qty=2,
            days_remaining=2,
            normal_avg=3.0,
            promo_avg=8.0,
        )
        # expected: 8*2 + 3*2 - 5 - 2 = 16 + 6 - 7 = 15
        assert result == 15

    def test_excess_stock_returns_zero(self, adjuster):
        """재고 과잉 시 0 반환"""
        result = adjuster._calculate_end_adjustment(
            base_qty=10,
            current_stock=50,
            pending_qty=10,
            days_remaining=1,
            normal_avg=2.0,
            promo_avg=5.0,
        )
        # expected: 5*1 + 2*2 - 50 - 10 = 9 - 60 = -51 → 0
        assert result == 0

    def test_normal_avg_zero_fallback(self, adjuster):
        """normal_avg=0 → 목표재고 2개"""
        result = adjuster._calculate_end_adjustment(
            base_qty=10,
            current_stock=0,
            pending_qty=0,
            days_remaining=1,
            normal_avg=0.0,
            promo_avg=5.0,
        )
        # expected: 5*1 + 2 - 0 - 0 = 7
        assert result == 7


# ─────────────────────────────────────────────
# get_adjustment_summary 테스트
# ─────────────────────────────────────────────


class TestAdjustmentSummary:
    """여러 상품 요약"""

    def test_categorization(self, adjuster, mock_manager):
        """상품을 올바르게 분류"""
        today = date.today()

        # ending
        ending_status = _make_status(
            item_cd="END1",
            current_promo="1+1",
            days_until_end=1,
            next_promo=None,
            will_change=True,
            change_type="end",
        )
        # starting
        start_date = (today + timedelta(days=2)).strftime('%Y-%m-%d')
        starting_status = _make_status(
            item_cd="START1",
            current_promo=None,
            next_promo="2+1",
            next_start_date=start_date,
            will_change=True,
            change_type="start",
        )
        # in promo
        in_promo_status = _make_status(
            item_cd="INPROMO1",
            current_promo="1+1",
            days_until_end=10,
        )

        def mock_get_status(item_cd):
            return {
                "END1": ending_status,
                "START1": starting_status,
                "INPROMO1": in_promo_status,
                "NONE1": None,
            }.get(item_cd)

        mock_manager.get_promotion_status.side_effect = mock_get_status
        mock_manager.get_promo_multiplier.return_value = 2.0

        summary = adjuster.get_adjustment_summary(["END1", "START1", "INPROMO1", "NONE1"])

        assert len(summary["ending_soon"]) == 1
        assert len(summary["starting_soon"]) == 1
        assert len(summary["in_promotion"]) == 1
        assert len(summary["no_change"]) == 1


# ─────────────────────────────────────────────
# Fix B 회귀: _apply_promotion_adjustment
# (ImprovedPredictor 내부 메서드 직접 테스트)
# ─────────────────────────────────────────────


class TestFixBPromoStockCheck:
    """Fix B 회귀: 행사 안정기 재고 충분 시 보정 스킵"""

    def _make_predictor(self):
        with patch(
            "src.prediction.improved_predictor.ImprovedPredictor.__init__",
            return_value=None,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor

            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._promo_adjuster = MagicMock()
            p.store_id = "46513"
            return p

    def test_p01_in_promo_stock_sufficient_skip(self):
        """[P-01] 행사중 + 재고 충분 → 보정 스킵 (Fix B 핵심)"""
        p = self._make_predictor()
        status = _make_status(
            current_promo="2+1",
            days_until_end=10,
            promo_avg=5.0,
            normal_avg=0.6,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=0,
            weekday_coef=1.67,
            safety_stock=8.67,
            current_stock=14,
            pending_qty=0,
            daily_avg=0.6,
            ctx=ctx,
        ).qty

        promo_demand = 5.0 * 1.67
        assert result == 0, (
            f"[P-01] 재고 충분 시 promo 보정 스킵이어야 함 "
            f"(stock=14, promo_demand={promo_demand:.2f}, result={result})"
        )

    def test_p04_in_promo_stock_insufficient_trigger(self):
        """[P-04] 행사중 + 재고 부족 → 양수 생성 허용"""
        p = self._make_predictor()
        status = _make_status(
            current_promo="2+1",
            days_until_end=10,
            promo_avg=5.0,
            normal_avg=0.6,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=0,
            weekday_coef=1.67,
            safety_stock=8.67,
            current_stock=3,
            pending_qty=0,
            daily_avg=0.6,
            ctx=ctx,
        ).qty

        promo_demand = 5.0 * 1.67
        assert result > 0, (
            f"[P-04] 재고 부족 시 promo 보정 발동이어야 함 "
            f"(stock=3, promo_demand={promo_demand:.2f}, result={result})"
        )


# ─────────────────────────────────────────────
# Tier 3: 프로모션 정책 보호
# ─────────────────────────────────────────────


class TestPromoPolicyProtection:
    """Tier 3: _apply_promotion_adjustment 분기 보호"""

    def _make_predictor(self):
        with patch(
            "src.prediction.improved_predictor.ImprovedPredictor.__init__",
            return_value=None,
        ):
            from src.prediction.improved_predictor import ImprovedPredictor

            p = ImprovedPredictor.__new__(ImprovedPredictor)
            p._promo_adjuster = MagicMock()
            p.store_id = "46513"
            return p

    def test_p05_ending_promo_triggers_branch_a(self):
        """[P-05] 행사 종료 D-3 이내 → (A) 분기 발동"""
        p = self._make_predictor()
        status = _make_status(
            current_promo="1+1",
            days_until_end=2,
            promo_avg=8.0,
            normal_avg=3.0,
            will_change=True,
            change_type="end",
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        mock_result = MagicMock()
        mock_result.adjusted_qty = 6
        mock_result.adjustment_reason = "행사 종료 D-2"
        p._promo_adjuster.adjust_order_quantity.return_value = mock_result

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=10,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=3,
            pending_qty=0,
            daily_avg=3.0,
            ctx=ctx,
        ).qty

        assert result == 6, (
            f"[P-05] 행사 종료 D-2에서 분기A 발동이어야 함 "
            f"(days_until_end=2, result={result})"
        )
        assert ctx["_promo_branch"] == "A-ending", (
            f"[P-05] 분기가 A-ending이어야 함 (actual={ctx['_promo_branch']})"
        )

    def test_p06_starting_promo_triggers_branch_b(self):
        """[P-06] 행사 시작 D-3 이내 (비행사 상태) → (B) 분기 발동"""
        p = self._make_predictor()
        start_date = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")
        status = _make_status(
            current_promo=None,
            next_promo="1+1",
            next_start_date=start_date,
            promo_avg=0.0,
            normal_avg=3.0,
            will_change=True,
            change_type="start",
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        mock_result = MagicMock()
        mock_result.adjusted_qty = 15
        mock_result.adjustment_reason = "행사 시작 D-2"
        p._promo_adjuster.adjust_order_quantity.return_value = mock_result

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=10,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=3,
            pending_qty=0,
            daily_avg=3.0,
            ctx=ctx,
        ).qty

        assert result == 15, (
            f"[P-06] 행사 시작 D-2에서 분기B 발동이어야 함 "
            f"(next_start={start_date}, result={result})"
        )
        assert ctx["_promo_branch"] == "B-starting", (
            f"[P-06] 분기가 B-starting이어야 함 (actual={ctx['_promo_branch']})"
        )

    def test_p07_no_promo_no_adjustment(self):
        """[P-07] 비행사 상품 → 보정 없음"""
        p = self._make_predictor()
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = None

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=10,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=5,
            pending_qty=0,
            daily_avg=3.0,
            ctx=ctx,
        ).qty

        assert result == 10, (
            f"[P-07] 비행사 상품은 보정 없어야 함 (result={result})"
        )
        assert ctx["_promo_branch"] == "none", (
            f"[P-07] 분기가 none이어야 함 (actual={ctx['_promo_branch']})"
        )

    def test_p08_promo_avg_zero_no_adjustment(self):
        """[P-08] promo_avg=0 → 보정 없음"""
        p = self._make_predictor()
        status = _make_status(
            current_promo="2+1",
            days_until_end=10,
            promo_avg=0.0,
            normal_avg=2.0,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=10,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=5,
            pending_qty=0,
            daily_avg=2.0,
            ctx=ctx,
        ).qty

        assert result == 10, (
            f"[P-08] promo_avg=0이면 보정 없어야 함 (result={result})"
        )

    def test_p09_stable_period_skip(self):
        """[P-09] daily_avg >= promo_avg×0.8 → 안정기 보정 스킵"""
        p = self._make_predictor()
        status = _make_status(
            current_promo="2+1",
            days_until_end=10,
            promo_avg=5.0,
            normal_avg=4.5,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=10,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=5,
            pending_qty=0,
            daily_avg=4.5,  # >= 5.0 * 0.8 = 4.0 → 안정기
            ctx=ctx,
        ).qty

        assert result == 10, (
            f"[P-09] 안정기(daily_avg>=promo_avg×0.8)에선 보정 스킵이어야 함 "
            f"(daily_avg=4.5, promo_avg=5.0, result={result})"
        )

    def test_p10_unstable_stock_insufficient_trigger(self):
        """[P-10] daily_avg < promo_avg×0.8 + 재고 부족 → 보정 발동"""
        p = self._make_predictor()
        status = _make_status(
            current_promo="1+1",
            days_until_end=15,
            promo_avg=10.0,
            normal_avg=2.0,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        result = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트상품", "mid_cd": "032"},
            order_qty=5,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=3,
            pending_qty=0,
            daily_avg=2.0,  # < 10.0 * 0.8 = 8.0 → 불안정기
            ctx=ctx,
        ).qty

        # promo_daily_demand = 10.0 * 1.0 = 10.0
        # stock(3) < 10.0 → trigger
        # promo_need = 10.0 + 5.0 - 3 - 0 = 12.0 → 12
        # 12 > 5 → order_qty = 12
        assert result > 5, (
            f"[P-10] 재고 부족+불안정기 시 보정 발동이어야 함 "
            f"(daily_avg=2.0, promo_avg=10.0, stock=3, result={result})"
        )
        assert ctx["_promo_branch"] == "C-promo_adjust", (
            f"[P-10] 분기가 C-promo_adjust이어야 함 "
            f"(actual={ctx['_promo_branch']})"
        )


# ─────────────────────────────────────────────
# 반월 행사 연장 사전 감지 테스트
# ─────────────────────────────────────────────


class TestPromotionExtension:
    """반월 행사 연장 사전 감지 관련 테스트"""

    def test_next_promo_detection_when_d5(self):
        """D-5 이내일 때 check_and_update_extensions가 대상 조회"""
        from unittest.mock import patch, MagicMock
        from src.collectors.promotion_collector import PromotionCollector

        driver = MagicMock()
        collector = PromotionCollector(driver=driver, db_path=":memory:", store_id="46513")

        # 반월 경계일을 D-3으로 설정
        fake_today = date(2026, 3, 12)  # 3/15 경계까지 D-3

        with patch('src.collectors.promotion_collector.date') as mock_date:
            mock_date.today.return_value = fake_today
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

            # DB 조회 모킹 (빈 결과)
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = []
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)

            with patch.object(collector, '_get_connection', return_value=mock_conn):
                result = collector.check_and_update_extensions()

            assert result == []
            # 쿼리가 실행되었는지 확인 (D-5 이내이므로 트리거됨)
            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args
            assert '2026-03-15' in call_args[0][1]  # boundary_end = 3/15

    def test_extension_updates_end_date(self):
        """연장 감지 시 _apply_extension이 DB end_date를 갱신"""
        from unittest.mock import patch, MagicMock, call
        from src.collectors.promotion_collector import PromotionCollector

        driver = MagicMock()
        collector = PromotionCollector(driver=driver, db_path=":memory:", store_id="46513")

        item = {
            'item_cd': '8801898160010',
            'item_nm': '훈제란',
            'promo_type': '1+1',
            'start_date': '2026-03-01',
            'end_date': '2026-03-15',
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch.object(collector, '_get_connection', return_value=mock_conn):
            collector._apply_extension(item, '2026-03-31')

        # UPDATE promotions end_date 호출 확인
        update_call = mock_cursor.execute.call_args_list[0]
        assert 'UPDATE promotions' in update_call[0][0]
        assert '2026-03-31' in update_call[0][1]  # new_end

        # INSERT promotion_changes 'extended' 호출 확인
        insert_call = mock_cursor.execute.call_args_list[1]
        assert 'extended' in str(insert_call[0])
        assert '8801898160010' in insert_call[0][1]

        mock_conn.commit.assert_called_once()

    def test_no_reduction_when_extended(self, mock_manager):
        """연장된 행사는 END_ADJUSTMENT 감소를 스킵"""
        adjuster = PromotionAdjuster(promo_manager=mock_manager)

        # D-3 상태지만 DB에는 이미 연장된 end_date
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-03-15",
            days_until_end=3,
            next_promo=None,
            normal_avg=2.0,
            promo_avg=5.0,
            will_change=True,
            change_type="end",
        )
        mock_manager.get_promotion_status.return_value = status
        # DB에는 3/31로 연장됨
        mock_manager.get_active_end_date.return_value = "2026-03-31"

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)

        # 감소 없이 원래 수량 유지
        assert result.adjusted_qty == 10
        assert "연장 감지" in result.adjustment_reason
        assert result.adjustment_factor == 1.0

    def test_non_boundary_date_not_triggered(self):
        """반월 경계가 아닌 날짜(D>5)에서는 연장 체크 스킵"""
        from unittest.mock import patch, MagicMock
        from src.collectors.promotion_collector import PromotionCollector

        driver = MagicMock()
        collector = PromotionCollector(driver=driver, db_path=":memory:", store_id="46513")

        # 3/5 → 3/15 경계까지 D-10 → 트리거 안 됨
        fake_today = date(2026, 3, 5)

        with patch('src.collectors.promotion_collector.date') as mock_date:
            mock_date.today.return_value = fake_today
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)

            result = collector.check_and_update_extensions()

        assert result == []


# ─────────────────────────────────────────────
# promo-end-reduction: D-5 확장 테스트
# ─────────────────────────────────────────────


class TestPromoEndReductionD5:
    """행사 종료 감량 D-3 → D-5 확장 테스트"""

    def test_end_d5_factor(self, adjuster, mock_manager):
        """D-5: 85%로 감소 (promo_avg=0 → factor 기반)"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-04-10",
            days_until_end=5,
            next_promo=None,
            promo_avg=0.0,
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)
        # factor=0.85 → 10 * 0.85 = 8
        assert result.adjusted_qty == 8
        assert "D-5" in result.adjustment_reason

    def test_end_d4_factor(self, adjuster, mock_manager):
        """D-4: 70%로 감소"""
        status = _make_status(
            current_promo="2+1",
            current_end_date="2026-04-09",
            days_until_end=4,
            next_promo=None,
            promo_avg=0.0,
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)
        # factor=0.70 → 10 * 0.70 = 7
        assert result.adjusted_qty == 7
        assert "D-4" in result.adjustment_reason

    def test_d6_no_reduction(self, adjuster, mock_manager):
        """D-6: 감량 범위 밖 → 케이스 3(행사 중) 적용"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-04-11",
            days_until_end=6,
            next_promo=None,
            promo_multiplier=1.8,
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)
        # 케이스 3: factor=1.8 → 10 * 1.8 = 18
        assert result.adjusted_qty == 18
        assert "행사 중" in result.adjustment_reason

    def test_d5_with_promo_stats(self, adjuster, mock_manager):
        """D-5 + promo_avg 있음 → 정밀 계산"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-04-10",
            days_until_end=5,
            next_promo=None,
            normal_avg=2.0,
            promo_avg=4.0,
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity(
            "TEST001", base_qty=10, current_stock=10, pending_qty=5
        )
        # expected_sales = 4.0 * 5 = 20
        # target_stock = 2.0 * 2 = 4
        # needed = 20 + 4 - 10 - 5 = 9
        assert result.adjusted_qty == 9

    def test_d5_extended_promo_skips(self, adjuster, mock_manager):
        """D-5이지만 행사 연장 감지 → 감량 스킵"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-04-10",
            days_until_end=5,
            next_promo=None,
        )
        mock_manager.get_promotion_status.return_value = status
        mock_manager.get_active_end_date.return_value = "2026-04-20"

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)
        assert result.adjusted_qty == 10
        assert "연장" in result.adjustment_reason

    def test_d5_next_promo_same_type(self, adjuster, mock_manager):
        """D-5 + 동일 행사 연속 → 감량 없음"""
        status = _make_status(
            current_promo="1+1",
            current_end_date="2026-04-10",
            days_until_end=5,
            next_promo="1+1",
        )
        mock_manager.get_promotion_status.return_value = status

        result = adjuster.adjust_order_quantity("TEST001", base_qty=10)
        assert result.adjusted_qty == 10
        assert "동일 행사 연속" in result.adjustment_reason
