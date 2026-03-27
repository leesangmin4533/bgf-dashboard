"""_round_to_order_unit 회귀 테스트

Fix A: floor=0 시 surplus >= safety_stock이고 재고+surplus >= pred+safety → return 0
Need-qty-fix: 내림 우선 + 결품 안전망(days_cover < 0.5 → ceil 강제)
"""

import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────


def _make_predictor():
    """ImprovedPredictor 인스턴스 (init 우회)"""
    with patch(
        "src.prediction.improved_predictor.ImprovedPredictor.__init__",
        return_value=None,
    ):
        from src.prediction.improved_predictor import ImprovedPredictor

        p = ImprovedPredictor.__new__(ImprovedPredictor)
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
# Tier 1: Fix A 핵심
# ─────────────────────────────────────────────


class TestRoundToOrderUnitFixA:
    """Fix A 핵심 + 변형 케이스"""

    def test_r01_small_demand_stock_sufficient_zero(self):
        """[R-01] 소량 수요 + 재고 충분 → 0 반환 (Fix A 핵심)"""
        p = _make_predictor()
        ctx = _make_ctx(ramen_max_stock=17.3)

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=16,
            mid_cd="032",
            product={"item_nm": "테스트라면", "mid_cd": "032", "expiration_days": 365},
            daily_avg=0.6,
            current_stock=14,
            pending_qty=0,
            safety_stock=8.67,
            adjusted_prediction=1.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=False,
        ).qty

        # floor=0, ceil=16, surplus=13
        # surplus(13) >= safety(8.67) ✓
        # stock(14)+surplus(13)=27 >= pred(1.0)+safety(8.67)=9.67 ✓ → 0
        assert result == 0, (
            f"[R-01] surplus 충분 시 return 0이어야 함 "
            f"(surplus=13, safety=8.67, result={result})"
        )


# ─────────────────────────────────────────────
# Tier 2: 변형 케이스
# ─────────────────────────────────────────────


class TestRoundToOrderUnitVariants:
    """Tier 2: floor/ceil 변형"""

    def test_r02_small_demand_stock_empty_one_box(self):
        """[R-02] 소량 수요 + 재고 없음 → 1박스 반환"""
        p = _make_predictor()
        ctx = _make_ctx(ramen_max_stock=17.3)

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=16,
            mid_cd="032",
            product={"item_nm": "테스트라면", "mid_cd": "032", "expiration_days": 365},
            daily_avg=0.6,
            current_stock=0,
            pending_qty=0,
            safety_stock=8.67,
            adjusted_prediction=1.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=False,
        ).qty

        # stock=0, days_cover=0 → needs_ceil=True → ceil=16
        assert result == 16, (
            f"[R-02] 재고 없을 시 ceil_qty 반환이어야 함 "
            f"(stock=0, result={result})"
        )


# ─────────────────────────────────────────────
# Tier 3: 정책 보호
# ─────────────────────────────────────────────


class TestRoundToOrderUnitPolicies:
    """Tier 3: 발주단위 정책 보호"""

    def test_r03_ceil_roundup(self):
        """[R-03] order_qty=20, unit=16, stock=0 → 32 (ceil 올림)"""
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=20,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=3.0,
            current_stock=0,
            pending_qty=0,
            safety_stock=15.0,
            adjusted_prediction=3.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # ceil=32, floor=16, surplus=12
        # surplus(12) < safety(15) → surplus 체크 실패
        # needs_ceil=True (stock=0) → 32
        assert result == 32, (
            f"[R-03] ceil 올림이어야 함 "
            f"(order_qty=20, unit=16, result={result})"
        )

    def test_r04_exact_one_box(self):
        """[R-04] order_qty=16, unit=16 → 16 (정확히 1박스)"""
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=16,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=3.0,
            current_stock=5,
            pending_qty=0,
            safety_stock=5.0,
            adjusted_prediction=3.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # ceil=16, floor=16, surplus=0
        # surplus(0) < safety(5.0) → 체크 실패
        # needs_ceil=False (days_cover=5/3=1.67)
        # floor=16 > 0 → 16
        assert result == 16, (
            f"[R-04] 정확히 1박스이어야 함 "
            f"(order_qty=16, unit=16, result={result})"
        )

    def test_r07_tobacco_always_ceil(self):
        """[R-07] 담배 카테고리 → 항상 ceil"""
        p = _make_predictor()
        ctx = _make_ctx(tobacco_max_stock=0)

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=10,
            mid_cd="072",
            product={"item_nm": "테스트담배", "mid_cd": "072", "expiration_days": 365},
            daily_avg=2.0,
            current_stock=5,
            pending_qty=0,
            safety_stock=3.0,
            adjusted_prediction=2.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=False,
        ).qty

        # C-tobacco: 항상 ceil=10
        assert result == 10, (
            f"[R-07] 담배는 항상 ceil이어야 함 "
            f"(order_qty=3, unit=10, result={result})"
        )
        assert ctx["_round_branch"] == "C-tobacco", (
            f"[R-07] 분기가 C-tobacco이어야 함 (actual={ctx['_round_branch']})"
        )

    def test_r08_max_stock_overflow_floor(self):
        """[R-08] max_stock 초과 + floor>0 → floor 반환"""
        p = _make_predictor()
        ctx = _make_ctx(ramen_max_stock=30)

        result = p._round_to_order_unit(
            order_qty=20,
            order_unit=16,
            mid_cd="032",
            product={"item_nm": "테스트라면", "mid_cd": "032", "expiration_days": 365},
            daily_avg=5.0,
            current_stock=10,
            pending_qty=0,
            safety_stock=5.0,
            adjusted_prediction=5.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=False,
        ).qty

        # ceil=32, floor=16
        # stock(10)+pending(0)+ceil(32)=42 > max_stock(30) AND floor(16)>0 → 16
        assert result == 16, (
            f"[R-08] max_stock 초과 시 floor 반환이어야 함 "
            f"(max_stock=30, stock+ceil=42, result={result})"
        )

    def test_r09_needs_ceil_forced(self):
        """[R-09] needs_ceil=True (days_cover<0.5) → ceil 강제"""
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=10.0,
            current_stock=0,
            pending_qty=0,
            safety_stock=15.0,
            adjusted_prediction=10.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # days_cover=0/10=0 < 0.5 → needs_ceil
        # surplus=13 < safety(15) → surplus 체크 실패 → needs_ceil → 16
        assert result == 16, (
            f"[R-09] 결품 위험 시 ceil 강제이어야 함 "
            f"(days_cover=0, result={result})"
        )

    def test_r10_default_floor_zero_surplus_sufficient(self):
        """[R-10] default 카테고리 + floor=0 + surplus 충분 → 0"""
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=0.6,
            current_stock=14,
            pending_qty=0,
            safety_stock=8.67,
            adjusted_prediction=1.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # B-default: surplus=13 >= safety(8.67) ✓
        # stock(14)+13=27 >= pred(1.0)+safety(8.67) ✓ → 0
        assert result == 0, (
            f"[R-10] default 카테고리 surplus 충분 시 0이어야 함 "
            f"(surplus=13, safety=8.67, result={result})"
        )
