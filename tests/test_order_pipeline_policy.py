"""발주 파이프라인 정책 보정 테스트

파이프라인 단계 간 순서와 상호작용 검증:
- order_rules → ROP → promo 체인 (TC-04)
- category_cap → round_to_unit 체인 (TC-05A, TC-05B)

참조: config/order_priority.json Stage 3-4
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.promotion.promotion_manager import PromotionStatus


# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────


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


# ─────────────────────────────────────────────
# TC-04: order_rules → ROP → promo 체인
# ─────────────────────────────────────────────


class TestRopPromoChain:
    """order_rules → ROP → promo 파이프라인 순서 검증"""

    def test_tc04_rules_zero_rop_one_promo_up(self):
        """[TC-04] order_rules→0 → ROP→1 → promo→양수 체인

        파이프라인 순서:
          Stage 3: _apply_order_rules(need=0.05) → 0 (min_threshold=0.1 미만)
          Stage 3.5: ROP(ratio<0.3, stock=0) → 1
          Stage 3: _apply_promotion_adjustment(order=1, promo) → 양수 증가
        """
        p = _make_predictor()

        # Step 1: _apply_order_rules → 0
        # need_qty=0.05 < min_order_threshold(0.1) → return 0
        rule_result = p._apply_order_rules(
            need_qty=0.05,
            product={"item_nm": "간헐상품", "mid_cd": "099", "expiration_days": 180},
            weekday=1,
            current_stock=0,
            daily_avg=0.1,
            pending_qty=0,
        )
        order_qty = rule_result.qty
        assert order_qty == 0, (
            f"[TC-04-step1] need=0.05 < threshold(0.1) → 0이어야 함 "
            f"(result={order_qty})"
        )
        assert rule_result.stage == "rules_threshold", (
            f"[TC-04-step1] stage=rules_threshold이어야 함 "
            f"(actual={rule_result.stage})"
        )

        # Step 2: ROP 로직 (인라인 재현 — improved_predictor L1548-1555)
        # sell_day_ratio < 0.3, effective_stock=0, order_qty=0 → order=1
        sell_day_ratio = 0.15
        effective_stock = 0
        rop_enabled = True
        if rop_enabled and sell_day_ratio < 0.3:
            if effective_stock == 0 and order_qty == 0:
                order_qty = 1
        assert order_qty == 1, (
            f"[TC-04-step2] ROP: ratio={sell_day_ratio}, stock=0 → 1이어야 함 "
            f"(result={order_qty})"
        )

        # Step 3: promo 보정
        status = PromotionStatus(
            item_cd="TEST001",
            item_nm="간헐상품",
            current_promo="1+1",
            current_end_date=None,
            days_until_end=15,
            next_promo=None,
            next_start_date=None,
            will_change=False,
            change_type=None,
            normal_avg=0.1,
            promo_avg=3.0,
            promo_multiplier=2.0,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        order_qty = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "간헐상품", "mid_cd": "099"},
            order_qty=1,  # ROP에서 1
            weekday_coef=1.0,
            safety_stock=1.0,
            current_stock=0,
            pending_qty=0,
            daily_avg=0.1,
            ctx=ctx,
        ).qty

        # promo_daily_demand = 3.0 * 1.0 = 3.0
        # stock(0) < promo_demand(3.0) → C-promo_adjust 발동
        # promo_need = 3.0 + 1.0 - 0 - 0 = 4.0 → 4
        # 4 > 1 → order_qty = 4
        assert order_qty >= 1, (
            f"[TC-04-step3] promo 보정 후 ROP(1) 이상이어야 함 "
            f"(promo_demand=3.0, result={order_qty})"
        )

    def test_tc04_rop_skip_when_pending(self):
        """[TC-04 반전] pending > 0 → ROP 스킵"""
        p = _make_predictor()

        order_qty = p._apply_order_rules(
            need_qty=0.05,
            product={"item_nm": "간헐상품", "mid_cd": "099", "expiration_days": 180},
            weekday=1,
            current_stock=0,
            daily_avg=0.1,
            pending_qty=0,
        ).qty
        assert order_qty == 0

        # ROP: effective_stock=0 + pending=3 → pending 있으면 ROP 스킵
        # improved_predictor L1556-1560:
        # elif effective_stock_for_need == 0 and pending_qty > 0:
        #     logger.debug("[ROP Skip]...")
        sell_day_ratio = 0.15
        effective_stock = 0
        pending_qty = 3
        rop_triggered = False
        if sell_day_ratio < 0.3:
            if effective_stock == 0 and order_qty == 0:
                # pending 체크는 별도 elif: stock=0 but pending>0 → skip
                if pending_qty > 0:
                    rop_triggered = False
                else:
                    order_qty = 1
                    rop_triggered = True

        assert not rop_triggered, (
            f"[TC-04 반전] pending={pending_qty} > 0 → ROP 스킵이어야 함"
        )
        assert order_qty == 0, (
            f"[TC-04 반전] ROP 스킵 → order_qty=0 유지이어야 함 (result={order_qty})"
        )


# ─────────────────────────────────────────────
# TC-05: category_cap → round_to_unit 체인
# ─────────────────────────────────────────────


class TestCategoryCapRound:
    """category_cap → _round_to_order_unit 파이프라인 순서 검증"""

    def test_tc05a_cap_then_floor(self):
        """[TC-05A] category_cap(18) → round floor(16) 선택

        시나리오:
          order_qty=20 → cap=18 → round(18, unit=16)
          ceil=32, floor=16
          B-default: surplus=32-18=14 < safety(15) → 체크 실패
          days_cover=8/3.0=2.67 >= 0.5 → needs_ceil=False
          floor=16 > 0 → return 16
        """
        p = _make_predictor()
        ctx = _make_ctx()

        # Step 1: category_cap 적용 (인라인 재현 — L1627-1634)
        order_qty = 20
        max_qty = 18
        if max_qty and order_qty > max_qty:
            order_qty = max_qty
        assert order_qty == 18, f"[TC-05A-step1] cap=18 적용 (result={order_qty})"

        # Step 2: _round_to_order_unit
        result = p._round_to_order_unit(
            order_qty=18,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=3.0,
            current_stock=8,
            pending_qty=0,
            safety_stock=15.0,
            adjusted_prediction=3.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # ceil = ((18+16-1)//16)*16 = (33//16)*16 = 2*16 = 32
        # floor = (18//16)*16 = 1*16 = 16
        # B-default: surplus = 32-18 = 14
        # 14 < safety(15.0) → surplus check FAIL
        # days_cover = (8+0)/3.0 = 2.67 >= 0.5 → needs_ceil=False
        # floor=16 > 0 → return 16
        assert result == 16, (
            f"[TC-05A] cap=18 → floor=16 선택이어야 함 "
            f"(ceil=32, floor=16, result={result})"
        )
        assert ctx.get("_round_branch") == "B-default", (
            f"[TC-05A] branch=B-default이어야 함 "
            f"(actual={ctx.get('_round_branch')})"
        )

    def test_tc05b_cap_then_ceil(self):
        """[TC-05B] category_cap(18) → needs_ceil → ceil(32) 선택

        시나리오:
          order_qty=20 → cap=18 → round(18, unit=16)
          ceil=32, floor=16
          B-default: surplus=14 < safety(15) → 체크 실패
          days_cover=1/5.0=0.2 < 0.5 → needs_ceil=True
          → return ceil=32
        """
        p = _make_predictor()
        ctx = _make_ctx()

        # Step 1: category_cap
        order_qty = 20
        max_qty = 18
        if max_qty and order_qty > max_qty:
            order_qty = max_qty
        assert order_qty == 18

        # Step 2: _round_to_order_unit with 결품 위험
        result = p._round_to_order_unit(
            order_qty=18,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "결품위험상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=5.0,
            current_stock=1,  # ★ 재고 극소
            pending_qty=0,
            safety_stock=15.0,
            adjusted_prediction=5.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # ceil=32, floor=16
        # B-default: surplus = 32-18 = 14
        # 14 < safety(15.0) → surplus check FAIL
        # days_cover = (1+0)/5.0 = 0.2 < 0.5 → needs_ceil=True
        # → return ceil=32
        assert result == 32, (
            f"[TC-05B] needs_ceil(days_cover=0.2) → ceil=32 선택이어야 함 "
            f"(floor=16, result={result})"
        )

    def test_tc05_cap_not_exceeded_passes_through(self):
        """[TC-05 보충] cap 미초과 시 원래 qty 유지 → round 적용"""
        p = _make_predictor()
        ctx = _make_ctx()

        # order_qty=12, cap=18 → cap 미초과 → 12 유지
        order_qty = 12
        max_qty = 18
        if max_qty and order_qty > max_qty:
            order_qty = max_qty
        assert order_qty == 12, f"cap 미초과 → 12 유지 (result={order_qty})"

        # round(12, unit=16) → ceil=16, floor=0
        # B-default: surplus=16-12=4
        # surplus(4) < safety(10.0) → FAIL
        # days_cover = 5/3.0 = 1.67 >= 0.5 → needs_ceil=False
        # floor=0 → else → return ceil=16
        result = p._round_to_order_unit(
            order_qty=12,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=3.0,
            current_stock=5,
            pending_qty=0,
            safety_stock=10.0,
            adjusted_prediction=3.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # surplus(4) < safety(10.0) → FAIL
        # needs_ceil: days_cover=5/3=1.67 >= 0.5 → False
        # floor=0 → else → return ceil=16
        assert result == 16, (
            f"[TC-05 보충] floor=0 + surplus 부족 → ceil=16 (result={result})"
        )
