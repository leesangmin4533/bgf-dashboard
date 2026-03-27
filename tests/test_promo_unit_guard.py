"""
promo-unit-guard: 행사 보정 + 발주단위 과잉발주 방지 테스트

Fix A: _round_to_order_unit cat_max_stock 분기 surplus 취소 로직
Fix B: _apply_promotion_adjustment Case C 재고 충분 체크
"""
import pytest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.prediction.improved_predictor import ImprovedPredictor

STORE_ID = "46513"


# ─── 헬퍼 함수 ───────────────────────────────────────────────


def _make_predictor():
    """테스트용 ImprovedPredictor (DB 없이)"""
    with patch.object(ImprovedPredictor, '__init__', lambda self, **kwargs: None):
        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor._diff_feedback = None
        predictor._substitution_detector = None
        predictor._cost_optimizer = None
        predictor._ml_predictor = None
        predictor._calibrator = None
        predictor.store_id = STORE_ID
        return predictor


def _make_predictor_with_promo(promo_status):
    """ImprovedPredictor + promo mock 세팅"""
    predictor = _make_predictor()
    promo_mgr = MagicMock()
    promo_mgr.get_promotion_status.return_value = promo_status
    promo_adjuster = MagicMock()
    promo_adjuster.promo_manager = promo_mgr
    predictor._promo_adjuster = promo_adjuster
    return predictor


def _make_promo_status(current_promo="", promo_avg=0.0, normal_avg=2.0):
    """PromoStatus mock 생성"""
    status = MagicMock()
    status.current_promo = current_promo
    status.promo_avg = promo_avg
    status.normal_avg = normal_avg
    status.days_until_end = None
    status.next_promo = ""
    status.next_start_date = None
    return status


def _make_product(**overrides):
    """기본 product dict"""
    product = {
        "item_cd": "TEST001",
        "item_nm": "테스트상품",
        "mid_cd": "049",
        "order_unit_qty": 16,
        "expiration_days": 30,
    }
    product.update(overrides)
    return product


def _make_ctx(**overrides):
    """기본 ctx dict (cat_max_stock 없는 default)"""
    ctx = {
        "tobacco_max_stock": 100,
        "beer_max_stock": None,
        "soju_max_stock": None,
        "ramen_max_stock": 0,
        "food_expiration_days": None,
    }
    ctx.update(overrides)
    return ctx


# ═══════════════════════════════════════════════════════════════
# Fix A 테스트: _round_to_order_unit surplus 취소
# ═══════════════════════════════════════════════════════════════


class TestFixA_SurplusCancellation:
    """Fix A: cat_max_stock 분기에서 floor=0일 때 surplus 취소 체크"""

    def test_a1_high_unit_stock_sufficient_cancel(self):
        """TC-A1: unit=16, order=3, stock=14, safety=9.2 → 취소 (return 0)"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="032", order_unit_qty=16)  # 라면
        ctx = _make_ctx(ramen_max_stock=200)  # ramen cat_max_stock 활성화

        result = predictor._round_to_order_unit(
            order_qty=3, order_unit=16, mid_cd="032", product=product,
            daily_avg=1.17, current_stock=14, pending_qty=0,
            safety_stock=9.2, adjusted_prediction=1.69,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # surplus=13 >= safety=9.2, stock+surplus=27 >= pred+safety=10.89
        assert result == 0

    def test_a2_needs_ceil_overrides_surplus(self):
        """TC-A2: stock=0, days_cover<0.5 → needs_ceil=True → 올림 유지"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="032", order_unit_qty=16)
        ctx = _make_ctx(ramen_max_stock=200)

        result = predictor._round_to_order_unit(
            order_qty=3, order_unit=16, mid_cd="032", product=product,
            daily_avg=5.0, current_stock=0, pending_qty=0,
            safety_stock=3.0, adjusted_prediction=5.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # days_cover=0/5.0=0 < 0.5 → needs_ceil → return 16
        assert result == 16

    def test_a3_small_unit_surplus_below_safety(self):
        """TC-A3: unit=4, order=3, surplus=1 < safety=2 → 올림 유지"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="032", order_unit_qty=4)
        ctx = _make_ctx(ramen_max_stock=200)

        result = predictor._round_to_order_unit(
            order_qty=3, order_unit=4, mid_cd="032", product=product,
            daily_avg=3.0, current_stock=5, pending_qty=0,
            safety_stock=2.0, adjusted_prediction=3.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # surplus=4-3=1 < safety=2.0 → 조건 불충족 → return ceil=4
        assert result == 4

    def test_a4_high_need_surplus_small(self):
        """TC-A4: unit=16, order=15, surplus=1 < safety=9 → 올림 유지"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="032", order_unit_qty=16)
        ctx = _make_ctx(ramen_max_stock=200)

        result = predictor._round_to_order_unit(
            order_qty=15, order_unit=16, mid_cd="032", product=product,
            daily_avg=12.0, current_stock=2, pending_qty=0,
            safety_stock=9.0, adjusted_prediction=12.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # surplus=16-15=1 < safety=9.0 → 조건 불충족 → return ceil=16
        assert result == 16

    def test_a5_large_unit_large_stock_cancel(self):
        """TC-A5: unit=24, order=5, stock=20, safety=8 → 취소 (return 0)"""
        predictor = _make_predictor()
        # 라면 카테고리로 테스트 (cat_max_stock 활성화)
        product = _make_product(mid_cd="032", order_unit_qty=24)
        ctx = _make_ctx(ramen_max_stock=200)

        result = predictor._round_to_order_unit(
            order_qty=5, order_unit=24, mid_cd="032", product=product,
            daily_avg=4.0, current_stock=20, pending_qty=0,
            safety_stock=8.0, adjusted_prediction=4.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # surplus=24-5=19 >= safety=8.0 ✓
        # stock+surplus=20+19=39 >= pred+safety=4.0+8.0=12.0 ✓
        assert result == 0

    def test_a6_max_stock_exceeded_floor_positive(self):
        """TC-A6: max_stock 초과 + floor>0 → 내림 반환 (기존 로직 유지)"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="032", order_unit_qty=16)
        ctx = _make_ctx(ramen_max_stock=90)

        result = predictor._round_to_order_unit(
            order_qty=20, order_unit=16, mid_cd="032", product=product,
            daily_avg=5.0, current_stock=70, pending_qty=0,
            safety_stock=5.0, adjusted_prediction=5.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # ceil=32, floor=16
        # stock+pending+ceil = 70+0+32=102 > max_stock=90, floor=16>0
        # → return floor=16
        assert result == 16

    def test_a7_needs_ceil_priority_over_surplus(self):
        """TC-A7: stock=0, daily_avg=5 → days_cover=0 → needs_ceil → 올림"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="032", order_unit_qty=16)
        ctx = _make_ctx(ramen_max_stock=200)

        result = predictor._round_to_order_unit(
            order_qty=3, order_unit=16, mid_cd="032", product=product,
            daily_avg=5.0, current_stock=0, pending_qty=0,
            safety_stock=9.2, adjusted_prediction=5.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # days_cover=0/5.0=0 < 0.5 → needs_ceil=True → return 16
        assert result == 16

    def test_a8_tobacco_not_affected(self):
        """TC-A8: 담배는 별도 분기 → cat_max_stock 분기 미진입"""
        predictor = _make_predictor()
        product = _make_product(mid_cd="033", order_unit_qty=10)
        ctx = _make_ctx(tobacco_max_stock=100)

        result = predictor._round_to_order_unit(
            order_qty=3, order_unit=10, mid_cd="033", product=product,
            daily_avg=1.0, current_stock=50, pending_qty=0,
            safety_stock=5.0, adjusted_prediction=1.0,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        # 담배 분기(is_tobacco_category) → return ceil=10
        assert result == 10


# ═══════════════════════════════════════════════════════════════
# Fix B 테스트: _apply_promotion_adjustment Case C 재고 체크
# ═══════════════════════════════════════════════════════════════


class TestFixB_PromoCaseCStockCheck:
    """Fix B: 행사 Case C에서 재고가 행사 일수요를 커버하면 보정 스킵"""

    def test_b1_stock_sufficient_skip(self):
        """TC-B1: stock=14, promo_daily=8.35 → 스킵 (order_qty 변경 없음)"""
        promo_status = _make_promo_status(
            current_promo="2+1", promo_avg=5.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=0,
            weekday_coef=1.67, safety_stock=9.2,
            current_stock=14, pending_qty=0,
            daily_avg=1.17  # < promo_avg * 0.8 = 4.0 → Case C 진입
        ).qty
        # promo_daily_demand = 5.0 * 1.67 = 8.35
        # stock(14) >= 8.35 → 스킵
        assert result == 0

    def test_b2_stock_insufficient_apply(self):
        """TC-B2: stock=5, promo_daily=8.35 → 보정 적용"""
        promo_status = _make_promo_status(
            current_promo="2+1", promo_avg=5.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=0,
            weekday_coef=1.67, safety_stock=9.2,
            current_stock=5, pending_qty=0,
            daily_avg=1.17
        ).qty
        # promo_daily_demand = 5.0 * 1.67 = 8.35
        # stock(5) < 8.35 → 보정 적용
        # promo_need = 8.35 + 9.2 - 5 - 0 = 12.55 → 12
        assert result == 12

    def test_b3_pending_makes_sufficient_skip(self):
        """TC-B3: stock=0, pending=10 → 스킵 (0+10 >= 8.35)"""
        promo_status = _make_promo_status(
            current_promo="2+1", promo_avg=5.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=0,
            weekday_coef=1.67, safety_stock=9.2,
            current_stock=0, pending_qty=10,
            daily_avg=1.17
        ).qty
        # stock + pending = 0 + 10 = 10 >= 8.35 → 스킵
        assert result == 0

    def test_b4_case_a_stock_sufficient_skip(self):
        """TC-B4: 행사 종료 임박(Case A) + 재고 충분 → Fix B 스킵"""
        promo_status = _make_promo_status(
            current_promo="1+1", promo_avg=5.0, normal_avg=2.0
        )
        promo_status.days_until_end = 1  # D-1 → Case A 진입
        promo_status.next_promo = ""
        promo_status.next_start_date = None

        predictor = _make_predictor_with_promo(promo_status)
        adj_result = MagicMock()
        adj_result.adjusted_qty = 6
        predictor._promo_adjuster.adjust_order_quantity.return_value = adj_result

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=10,
            weekday_coef=1.0, safety_stock=5.0,
            current_stock=14, pending_qty=0,
            daily_avg=1.17
        ).qty
        # promo_daily_demand = 5.0 * 1.0 = 5.0
        # stock(14) >= 5.0 → 스킵, order_qty=10 유지
        assert result == 10
        # adjuster가 호출되지 않았어야 함
        predictor._promo_adjuster.adjust_order_quantity.assert_not_called()

    def test_b4b_case_a_stock_insufficient_adjust(self):
        """TC-B4b: 행사 종료 임박(Case A) + 재고 부족 → adjuster 호출"""
        promo_status = _make_promo_status(
            current_promo="1+1", promo_avg=5.0, normal_avg=2.0
        )
        promo_status.days_until_end = 1  # D-1 → Case A 진입
        promo_status.next_promo = ""
        promo_status.next_start_date = None

        predictor = _make_predictor_with_promo(promo_status)
        adj_result = MagicMock()
        adj_result.adjusted_qty = 6
        adj_result.adjustment_reason = "행사종료 보충"
        predictor._promo_adjuster.adjust_order_quantity.return_value = adj_result

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=0,
            weekday_coef=1.0, safety_stock=5.0,
            current_stock=2, pending_qty=0,  # 2 < 5.0 → 부족
            daily_avg=1.17
        ).qty
        # stock(2) < demand(5.0) → adjuster 호출 → 6
        assert result == 6
        predictor._promo_adjuster.adjust_order_quantity.assert_called_once()

    def test_b4c_case_a_promo_avg_zero_no_guard(self):
        """TC-B4c: 행사 종료 임박(Case A) + promo_avg=0 → 가드 스킵, adjuster 호출"""
        promo_status = _make_promo_status(
            current_promo="1+1", promo_avg=0.0, normal_avg=2.0
        )
        promo_status.days_until_end = 1  # D-1 → Case A 진입
        promo_status.next_promo = ""
        promo_status.next_start_date = None

        predictor = _make_predictor_with_promo(promo_status)
        adj_result = MagicMock()
        adj_result.adjusted_qty = 4
        adj_result.adjustment_reason = "행사종료 기본"
        predictor._promo_adjuster.adjust_order_quantity.return_value = adj_result

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=10,
            weekday_coef=1.0, safety_stock=5.0,
            current_stock=14, pending_qty=0,  # 재고 충분하지만 promo_avg=0
            daily_avg=1.17
        ).qty
        # promo_avg=0 → 가드 작동 안함 → adjuster 호출 → 4
        assert result == 4
        predictor._promo_adjuster.adjust_order_quantity.assert_called_once()

    def test_b5_case_d_not_affected(self):
        """TC-B5: 비행사(Case D) → Fix B 미적용"""
        promo_status = _make_promo_status(
            current_promo="",  # 비행사
            promo_avg=0.0,
            normal_avg=2.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=10,
            weekday_coef=1.0, safety_stock=5.0,
            current_stock=14, pending_qty=0,
            daily_avg=5.0  # > normal_avg * 1.3 = 2.6 → Case D
        ).qty
        # Case D: normal_need = 2.0*1.0 + 5.0 - 14 - 0 = -7.0 → 0
        # 0 < order_qty(10) → order_qty = 0
        assert result == 0

    def test_b7_case_b_stock_sufficient_skip(self):
        """TC-B7: 행사 시작 임박(Case B) + 재고 충분 → Fix B 스킵"""
        from datetime import date, timedelta
        next_date = (date.today() + timedelta(days=2)).strftime('%Y-%m-%d')
        promo_status = _make_promo_status(
            current_promo="", promo_avg=4.0, normal_avg=2.0
        )
        promo_status.next_promo = "1+1"
        promo_status.next_start_date = next_date

        predictor = _make_predictor_with_promo(promo_status)
        adj_result = MagicMock()
        adj_result.adjusted_qty = 8
        predictor._promo_adjuster.adjust_order_quantity.return_value = adj_result

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=5,
            weekday_coef=1.0, safety_stock=3.0,
            current_stock=10, pending_qty=0,  # 10 >= 4.0 → 스킵
            daily_avg=2.0
        ).qty
        # promo_daily_demand = 4.0 * 1.0 = 4.0
        # stock(10) >= 4.0 → 스킵, order_qty=5 유지
        assert result == 5
        predictor._promo_adjuster.adjust_order_quantity.assert_not_called()

    def test_b7b_case_b_stock_insufficient_adjust(self):
        """TC-B7b: 행사 시작 임박(Case B) + 재고 부족 → adjuster 호출"""
        from datetime import date, timedelta
        next_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        promo_status = _make_promo_status(
            current_promo="", promo_avg=8.0, normal_avg=2.0
        )
        promo_status.next_promo = "2+1"
        promo_status.next_start_date = next_date

        predictor = _make_predictor_with_promo(promo_status)
        adj_result = MagicMock()
        adj_result.adjusted_qty = 12
        adj_result.adjustment_reason = "행사시작 보충"
        predictor._promo_adjuster.adjust_order_quantity.return_value = adj_result

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=5,
            weekday_coef=1.0, safety_stock=3.0,
            current_stock=3, pending_qty=0,  # 3 < 8.0 → 부족
            daily_avg=2.0
        ).qty
        # stock(3) < demand(8.0) → adjuster 호출 → 12
        assert result == 12
        predictor._promo_adjuster.adjust_order_quantity.assert_called_once()

    def test_b7c_case_b_promo_avg_zero_no_guard(self):
        """TC-B7c: 행사 시작 임박(Case B) + promo_avg=0 → 가드 스킵, adjuster 호출"""
        from datetime import date, timedelta
        next_date = (date.today() + timedelta(days=0)).strftime('%Y-%m-%d')
        promo_status = _make_promo_status(
            current_promo="", promo_avg=0.0, normal_avg=2.0
        )
        promo_status.next_promo = "1+1"
        promo_status.next_start_date = next_date

        predictor = _make_predictor_with_promo(promo_status)
        adj_result = MagicMock()
        adj_result.adjusted_qty = 6
        adj_result.adjustment_reason = "행사시작 기본"
        predictor._promo_adjuster.adjust_order_quantity.return_value = adj_result

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=5,
            weekday_coef=1.0, safety_stock=3.0,
            current_stock=20, pending_qty=0,  # 재고 충분하지만 promo_avg=0
            daily_avg=2.0
        ).qty
        # promo_avg=0 → 가드 작동 안함 → adjuster 호출 → 6
        assert result == 6
        predictor._promo_adjuster.adjust_order_quantity.assert_called_once()

    def test_b6_1plus1_stock_sufficient_skip(self):
        """TC-B6: 1+1 행사, stock 충분 → 스킵"""
        promo_status = _make_promo_status(
            current_promo="1+1", promo_avg=3.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        result = predictor._apply_promotion_adjustment(
            item_cd="TEST001", product={}, order_qty=0,
            weekday_coef=1.0, safety_stock=5.0,
            current_stock=10, pending_qty=0,
            daily_avg=1.0  # < promo_avg * 0.8 = 2.4 → Case C
        ).qty
        # promo_daily_demand = 3.0 * 1.0 = 3.0
        # stock(10) >= 3.0 → 스킵
        assert result == 0


# ═══════════════════════════════════════════════════════════════
# 통합 테스트
# ═══════════════════════════════════════════════════════════════


class TestIntegration_PromoUnitGuard:
    """Fix A + Fix B 통합 테스트"""

    def test_int1_8801043022262_simulation(self):
        """TC-INT1: 실제 상품 시뮬레이션 (재고=14, unit=16, 행사 2+1)"""
        # Fix B: Case C 보정 스킵 확인
        promo_status = _make_promo_status(
            current_promo="2+1", promo_avg=5.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        order_qty = predictor._apply_promotion_adjustment(
            item_cd="8801043022262", product={}, order_qty=0,
            weekday_coef=1.67, safety_stock=9.2,
            current_stock=14, pending_qty=0,
            daily_avg=1.17
        ).qty
        assert order_qty == 0, f"Fix B 실패: 재고 충분인데 order_qty={order_qty}"

        # Fix A: order_qty=0이면 ceil=0, 바로 0 반환 (round_to_order_unit 진입 불필요)
        # 하지만 명시적으로 확인
        product = _make_product(
            item_cd="8801043022262", item_nm="컵라면테스트",
            mid_cd="032", order_unit_qty=16
        )
        ctx = _make_ctx(ramen_max_stock=200)

        final_qty = predictor._round_to_order_unit(
            order_qty=0, order_unit=16, mid_cd="032", product=product,
            daily_avg=1.17, current_stock=14, pending_qty=0,
            safety_stock=9.2, adjusted_prediction=1.69,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert final_qty == 0, f"최종 발주가 0이어야 함: {final_qty}"

    def test_int2_stock_insufficient_normal_order(self):
        """TC-INT2: 재고 부족 시 정상 발주 확인"""
        # Fix B: stock=2 < promo_daily=8.35 → 보정 적용
        promo_status = _make_promo_status(
            current_promo="2+1", promo_avg=5.0
        )
        predictor = _make_predictor_with_promo(promo_status)

        order_qty = predictor._apply_promotion_adjustment(
            item_cd="8801043022262", product={}, order_qty=0,
            weekday_coef=1.67, safety_stock=9.2,
            current_stock=2, pending_qty=0,
            daily_avg=1.17
        ).qty
        # promo_need = 8.35 + 9.2 - 2 - 0 = 15.55 → 15
        assert order_qty == 15, f"보정 적용 실패: order_qty={order_qty}"

        # Fix A: order_qty=15, unit=16 → surplus=1 < safety=9.2 → 올림 유지=16
        product = _make_product(
            item_cd="8801043022262", item_nm="컵라면테스트",
            mid_cd="032", order_unit_qty=16
        )
        ctx = _make_ctx(ramen_max_stock=200)

        final_qty = predictor._round_to_order_unit(
            order_qty=15, order_unit=16, mid_cd="032", product=product,
            daily_avg=1.17, current_stock=2, pending_qty=0,
            safety_stock=9.2, adjusted_prediction=1.69,
            ctx=ctx, new_cat_pattern=None, is_default_category=False
        ).qty
        assert final_qty == 16, f"재고 부족 시 발주=16이어야 함: {final_qty}"
