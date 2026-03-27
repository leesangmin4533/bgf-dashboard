"""저회전 박스상품 통합 회귀 테스트

2026-03-07 장애 직접 회귀:
- Fix B: 행사중 재고 충분 시 promo 보정 스킵
- Fix A: _round_to_order_unit floor=0 surplus 충분 시 발주 취소
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.promotion.promotion_manager import PromotionStatus


# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────


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


def _make_predictor():
    """ImprovedPredictor 인스턴스 (init 우회)"""
    with patch(
        "src.prediction.improved_predictor.ImprovedPredictor.__init__",
        return_value=None,
    ):
        from src.prediction.improved_predictor import ImprovedPredictor

        p = ImprovedPredictor.__new__(ImprovedPredictor)
        p._promo_adjuster = MagicMock()
        p.store_id = "46513"
        return p


def _make_ctx(**overrides):
    """_round_to_order_unit이 필요로 하는 최소 ctx"""
    ctx = {
        "tobacco_max_stock": 0,
        "beer_max_stock": None,
        "soju_max_stock": None,
        "ramen_max_stock": 0,
        "food_expiration_days": None,
    }
    ctx.update(overrides)
    return ctx


# [I-02] 역방향 검증 (문서용)
# Fix B 이전(a4b92cd): stock=14, promo=2+1 → order=16 (버그)
# Fix B 이후(25f5a6a): stock=14, promo=2+1 → order=0 (정상)
# Fix B를 비활성화한 mock 환경에서만 16이 나와야 함 → 별도 조건부 테스트로 분리 필요


# ─────────────────────────────────────────────
# Tier 2: 통합 회귀 테스트
# ─────────────────────────────────────────────


class TestLowTurnoverBoxRegression:
    """저회전 박스상품 통합 회귀"""

    def test_i01_full_regression_zero(self):
        """[I-01] 오늘 버그 전체 재현 → 최종 order=0"""
        p = _make_predictor()
        item_cd = "8801043022262"

        # 2+1 행사 안정기, promo_avg=5.0
        status = _make_status(
            item_cd=item_cd,
            current_promo="2+1",
            days_until_end=10,
            promo_avg=5.0,
            normal_avg=0.6,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        # Phase 1: 예측 → stock=14 충분 → order_qty=0
        initial_order_qty = 0

        # Phase 2: Fix B (promo 보정)
        ctx = {}
        order_qty = p._apply_promotion_adjustment(
            item_cd=item_cd,
            product={"item_nm": "2+1행사라면", "mid_cd": "032"},
            order_qty=initial_order_qty,
            weekday_coef=1.67,
            safety_stock=9.2,
            current_stock=14,
            pending_qty=0,
            daily_avg=0.6,
            ctx=ctx,
        ).qty

        # Phase 3: 발주단위 정렬 (order_qty=0이면 호출 안됨)
        order_unit = 16
        if order_qty > 0 and order_unit > 1:
            round_ctx = _make_ctx(ramen_max_stock=17.3)
            order_qty = p._round_to_order_unit(
                order_qty=order_qty,
                order_unit=order_unit,
                mid_cd="032",
                product={
                    "item_nm": "2+1행사라면",
                    "mid_cd": "032",
                    "expiration_days": 365,
                },
                daily_avg=0.6,
                current_stock=14,
                pending_qty=0,
                safety_stock=9.2,
                adjusted_prediction=1.0,
                ctx=round_ctx,
                new_cat_pattern=None,
                is_default_category=False,
            ).qty

        assert order_qty == 0, (
            f"[I-01] 재고 충분 행사상품은 발주 0이어야 함 "
            f"(stock=14, unit=16, result={order_qty})"
        )

    def test_i05_pending_reflected_in_need(self):
        """[I-05] pending > 0 → need_qty에 반영"""
        p = _make_predictor()

        status = _make_status(
            current_promo="2+1",
            days_until_end=10,
            promo_avg=5.0,
            normal_avg=2.0,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        # Case A: pending=0 → 재고 부족(5 < 8.35) → 보정 발동
        result_no_pending = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트", "mid_cd": "032"},
            order_qty=0,
            weekday_coef=1.67,
            safety_stock=4.0,
            current_stock=5,
            pending_qty=0,
            daily_avg=2.0,
            ctx={},
        ).qty

        # Case B: pending=10 → 재고 충분(15 >= 8.35) → 보정 스킵
        result_with_pending = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트", "mid_cd": "032"},
            order_qty=0,
            weekday_coef=1.67,
            safety_stock=4.0,
            current_stock=5,
            pending_qty=10,
            daily_avg=2.0,
            ctx={},
        ).qty

        assert result_with_pending <= result_no_pending, (
            f"[I-05] pending_qty가 need_qty 계산에 반영이어야 함 "
            f"(pending=10, with={result_with_pending}, without={result_no_pending})"
        )
        assert result_with_pending == 0, (
            f"[I-05] pending 포함 시 재고 충분 → 발주 0이어야 함 "
            f"(pending=10, result={result_with_pending})"
        )

    def test_i07_unit1_no_rounding(self):
        """[I-07] order_unit=1 상품 → rounding 영향 없음"""
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=1,
            mid_cd="099",
            product={"item_nm": "낱개상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=1.0,
            current_stock=5,
            pending_qty=0,
            safety_stock=2.0,
            adjusted_prediction=1.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # ceil=3, floor=3 (unit=1이면 동일)
        # floor=3 > 0 → 3
        assert result == 3, (
            f"[I-07] 낱개 상품은 rounding 영향 없어야 함 "
            f"(unit=1, result={result})"
        )


# ─────────────────────────────────────────────
# Tier 3: 추가 회귀
# ─────────────────────────────────────────────


class TestManualOrderDeduction:
    """Tier 2.5: 수동발주 수집 누락 회귀 테스트"""

    def test_manual_order_missing_causes_no_deduction(self):
        """[I-06] 수동발주 수집 누락 시 차감 실패가 재발하지 않아야 함

        점포 47863, Phase 1.2 수동발주 수집 실패 상황 재현:
        - manual_order_items가 비어있을 때 deduct 함수가 0을 차감
        - manual_order_items에 데이터 있을 때 deduct 함수가 정상 차감
        - 두 결과의 차이가 실제 수동발주 수량과 일치
        """
        from src.order.order_filter import OrderFilter

        order_filter = OrderFilter(store_id="47863")

        item_cd = "8801043022262"
        manual_qty = 5

        # 푸드 카테고리(001~005, 012) order_list
        order_list = [
            {
                "item_cd": item_cd,
                "item_nm": "테스트도시락",
                "mid_cd": "001",
                "final_order_qty": 10,
            },
        ]

        # Case 1: manual_order_items 빈 상태 (수집 누락)
        # → get_today_food_orders()가 빈 dict 반환 → 차감 0
        with patch(
            "src.infrastructure.database.repos.ManualOrderItemRepository"
        ) as MockRepo:
            mock_instance = MockRepo.return_value
            mock_instance.get_today_food_orders.return_value = {}  # 수집 누락

            with patch(
                "src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True
            ):
                result_no_manual = order_filter.deduct_manual_food_orders(
                    order_list=[dict(item) for item in order_list],
                    min_order_qty=1,
                )

        qty_no_manual = result_no_manual[0]["final_order_qty"]

        # Case 2: manual_order_items에 수동발주 데이터 존재 → 정상 차감
        with patch(
            "src.infrastructure.database.repos.ManualOrderItemRepository"
        ) as MockRepo:
            mock_instance = MockRepo.return_value
            mock_instance.get_today_food_orders.return_value = {
                item_cd: manual_qty
            }

            with patch(
                "src.settings.constants.MANUAL_ORDER_FOOD_DEDUCTION", True
            ):
                result_with_manual = order_filter.deduct_manual_food_orders(
                    order_list=[dict(item) for item in order_list],
                    min_order_qty=1,
                )

        qty_with_manual = result_with_manual[0]["final_order_qty"]

        # 검증 1: 수집 누락 시 원래 수량 유지 (차감 0)
        deducted = qty_no_manual - qty_with_manual
        assert qty_no_manual == 10, (
            f"[I-06] 수동발주 수집 누락 시 차감 실패가 재발하지 않아야 함 "
            f"(manual_qty={manual_qty}, deducted={deducted}, result={qty_no_manual})"
        )

        # 검증 2: 수동발주 있을 때 정상 차감
        assert qty_with_manual == 10 - manual_qty, (
            f"[I-06] 수동발주 수집 누락 시 차감 실패가 재발하지 않아야 함 "
            f"(manual_qty={manual_qty}, deducted={deducted}, result={qty_with_manual})"
        )

        # 검증 3: 차이가 실제 수동발주 수량과 일치
        assert deducted == manual_qty, (
            f"[I-06] 수동발주 수집 누락 시 차감 실패가 재발하지 않아야 함 "
            f"(manual_qty={manual_qty}, deducted={deducted}, result={deducted})"
        )


class TestAdditionalRegression:
    """Tier 3: 저회전/고회전 추가 검증"""

    def test_i03_low_turnover_box_stock_sufficient_zero(self):
        """[I-03] 저회전 박스상품 + 재고 충분 → 발주 0"""
        p = _make_predictor()
        ctx = _make_ctx(ramen_max_stock=15.0)

        result = p._round_to_order_unit(
            order_qty=2,
            order_unit=12,
            mid_cd="032",
            product={"item_nm": "저회전라면", "mid_cd": "032", "expiration_days": 365},
            daily_avg=0.3,
            current_stock=10,
            pending_qty=0,
            safety_stock=4.0,
            adjusted_prediction=0.5,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=False,
        ).qty

        # ceil=12, floor=0, surplus=10
        # surplus(10) >= safety(4.0) ✓
        # stock(10)+surplus(10)=20 >= pred(0.5)+safety(4.0)=4.5 ✓ → 0
        assert result == 0, (
            f"[I-03] 저회전 재고 충분 시 발주 0이어야 함 "
            f"(stock=10, unit=12, daily=0.3, result={result})"
        )

    def test_i04_high_turnover_near_stockout_positive(self):
        """[I-04] 고회전 품절직전 + pending=0 → 발주 발생"""
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=15,
            order_unit=6,
            mid_cd="099",
            product={"item_nm": "고회전상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=8.0,
            current_stock=1,
            pending_qty=0,
            safety_stock=5.0,
            adjusted_prediction=8.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # ceil=18, floor=12
        # B-default: surplus=3, 3 < safety(5) → 체크 실패
        # days_cover=1/8=0.125 < 0.5 → needs_ceil → 18
        assert result > 0, (
            f"[I-04] 고회전 품절직전 시 발주 발생이어야 함 "
            f"(stock=1, daily=8.0, result={result})"
        )
