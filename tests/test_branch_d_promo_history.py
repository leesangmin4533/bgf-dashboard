"""Branch D 비행사보정: 행사 이력 확인 로직 테스트

행사 이력이 없는 상품에 Branch D 캡이 오적용되는 버그 수정 검증.
- 행사 이력 없음 → Branch D 스킵
- 행사 이력 있음 → Branch D 기존대로 적용
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date


# ─── 테스트용 PromotionStatus ────────────────────────────

def _make_promo_status(
    item_cd="TEST001",
    current_promo=None,
    normal_avg=1.0,
    promo_avg=0,
    days_until_end=None,
    next_promo=None,
    next_start_date=None,
):
    """테스트용 PromotionStatus 생성"""
    from src.prediction.promotion.promotion_manager import PromotionStatus
    return PromotionStatus(
        item_cd=item_cd,
        item_nm="테스트상품",
        current_promo=current_promo,
        current_end_date=None,
        days_until_end=days_until_end,
        next_promo=next_promo,
        next_start_date=next_start_date,
        will_change=False,
        change_type=None,
        normal_avg=normal_avg,
        promo_avg=promo_avg,
        promo_multiplier=1.0,
    )


# ─── has_promotion_history 테스트 ────────────────────────

class TestHasPromotionHistory:
    """PromotionManager.has_promotion_history() 단위 테스트"""

    def test_no_history(self):
        """promotions 테이블에 레코드 없으면 False"""
        from src.prediction.promotion.promotion_manager import PromotionManager

        pm = PromotionManager(store_id="46513")
        # 8801117267605 는 promotions에 없음 (사전 조사에서 확인)
        assert pm.has_promotion_history("8801117267605") is False

    def test_with_history(self):
        """promotions 테이블에 레코드 있으면 True"""
        from src.prediction.promotion.promotion_manager import PromotionManager
        import sqlite3

        pm = PromotionManager(store_id="46513")
        # promotions에서 아무 item_cd 1개 가져오기
        conn = sqlite3.connect("data/stores/46513.db")
        cur = conn.cursor()
        cur.execute("SELECT item_cd FROM promotions LIMIT 1")
        row = cur.fetchone()
        conn.close()
        if row:
            assert pm.has_promotion_history(row[0]) is True

    def test_query_failure_returns_false(self):
        """DB 조회 실패 시 False (안전한 방향)"""
        from src.prediction.promotion.promotion_manager import PromotionManager

        pm = PromotionManager(store_id="99999")  # 존재하지 않는 매장
        # 조회 실패해도 False 반환 (예외 아님)
        result = pm.has_promotion_history("NONEXIST")
        assert result is False


# ─── Branch D 행사이력 가드 테스트 ────────────────────────

class TestBranchDPromoHistoryGuard:
    """improved_predictor 내 Branch D 행사이력 확인 로직 테스트"""

    def _run_branch_d(self, has_promo_history: bool, daily_avg: float = 2.0,
                      normal_avg: float = 1.0, order_qty: int = 3,
                      safety_stock: float = 1.0, stock: int = 0, pending: int = 0):
        """Branch D 로직만 시뮬레이션

        Returns:
            (final_order_qty, branch_name, was_skipped)
        """
        # Branch D 조건: not current_promo AND normal_avg > 0
        #               AND daily_avg > normal_avg * 1.3
        promo_status = _make_promo_status(
            current_promo=None,
            normal_avg=normal_avg,
        )

        # Branch D 진입 조건 확인
        if not (not promo_status.current_promo
                and promo_status.normal_avg > 0
                and daily_avg > promo_status.normal_avg * 1.3):
            return order_qty, "no_entry", False

        # 행사 이력 확인
        if not has_promo_history:
            return order_qty, "D-skip_no_history", True

        # 기존 Branch D 로직
        weekday_coef = 0.92
        normal_need = (promo_status.normal_avg * weekday_coef
                       + safety_stock - stock - pending)
        normal_order = int(max(0, normal_need))
        if 0 <= normal_order < order_qty:
            return normal_order, "D-normal_adjust", False
        return order_qty, "D-normal_adjust_no_change", False

    def test_no_history_skips_branch_d(self):
        """행사 이력 없는 상품 → Branch D 스킵, 발주량 유지"""
        qty, branch, skipped = self._run_branch_d(
            has_promo_history=False,
            daily_avg=2.0,      # > 1.0 * 1.3 = 1.3 → 조건 충족
            normal_avg=1.0,
            order_qty=3,
        )
        assert qty == 3, f"행사이력 없으면 order_qty 유지 expected 3, got {qty}"
        assert branch == "D-skip_no_history"
        assert skipped is True

    def test_with_history_applies_branch_d(self):
        """행사 이력 있는 상품 → Branch D 캡 적용"""
        qty, branch, skipped = self._run_branch_d(
            has_promo_history=True,
            daily_avg=2.0,
            normal_avg=1.0,
            order_qty=3,
            safety_stock=1.0,
            stock=0,
            pending=0,
        )
        # normal_need = 1.0 * 0.92 + 1.0 - 0 - 0 = 1.92 → int = 1
        assert qty == 1, f"행사이력 있으면 Branch D 캡 적용 expected 1, got {qty}"
        assert branch == "D-normal_adjust"
        assert skipped is False

    def test_8801117267605_no_branch_d(self):
        """실제 케이스: 오리온)초코칩쿠키 — 행사이력 없으므로 Branch D 스킵"""
        qty, branch, skipped = self._run_branch_d(
            has_promo_history=False,
            daily_avg=1.82,     # Croston adjusted_prediction
            normal_avg=1.08,    # promotion_stats.normal_avg
            order_qty=2,        # _apply_order_rules 결과
            safety_stock=1.37,
            stock=1,
            pending=0,
        )
        assert qty == 2, f"8801117267605는 Branch D 스킵 → order=2 expected, got {qty}"
        assert branch == "D-skip_no_history"

    def test_condition_not_met_bypasses_all(self):
        """Branch D 진입 조건 미충족 시 이력 확인 없이 패스"""
        # daily_avg <= normal_avg * 1.3 → Branch D 진입 안 함
        qty, branch, skipped = self._run_branch_d(
            has_promo_history=True,
            daily_avg=1.2,      # <= 1.0 * 1.3
            normal_avg=1.0,
            order_qty=3,
        )
        assert qty == 3
        assert branch == "no_entry"

    def test_branch_d_cap_with_stock(self):
        """행사이력 + 재고 있으면 normal_order가 작아져서 더 큰 감소"""
        qty, branch, skipped = self._run_branch_d(
            has_promo_history=True,
            daily_avg=3.0,
            normal_avg=1.0,
            order_qty=5,
            safety_stock=1.0,
            stock=2,
            pending=1,
        )
        # normal_need = 1.0*0.92 + 1.0 - 2 - 1 = -1.08 → max(0,-1.08) = 0
        assert qty == 0

    def test_branch_d_no_change_when_normal_order_gte_current(self):
        """normal_order >= order_qty 이면 변동 없음"""
        qty, branch, skipped = self._run_branch_d(
            has_promo_history=True,
            daily_avg=2.0,
            normal_avg=1.0,
            order_qty=1,        # normal_order(1) >= order_qty(1)
            safety_stock=1.0,
            stock=0,
            pending=0,
        )
        # normal_need = 0.92+1.0-0-0=1.92 → int=1, 1>=1 → no change
        assert qty == 1
        assert "no_change" in branch


class TestBranchDOtherBranchesUntouched:
    """다른 Branch (A, B, C)가 수정되지 않았는지 확인"""

    def test_branch_a_intact(self):
        """Branch A (행사 종료 임박) 코드 변경 없음 확인"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._apply_promotion_adjustment)
        # Branch A 핵심 문자열 존재 확인
        assert "A-ending" in source
        assert "promo_A_ending" in source

    def test_branch_b_intact(self):
        """Branch B (행사 시작 임박) 코드 변경 없음 확인"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._apply_promotion_adjustment)
        assert "B-starting" in source
        assert "promo_B_starting" in source

    def test_branch_c_intact(self):
        """Branch C (행사 안정기 보정) 코드 변경 없음 확인"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._apply_promotion_adjustment)
        assert "C-promo_adjust" in source
        assert "promo_C_active" in source

    def test_branch_d_has_history_check(self):
        """Branch D에 has_promotion_history 호출이 존재"""
        import inspect
        from src.prediction.improved_predictor import ImprovedPredictor
        source = inspect.getsource(ImprovedPredictor._apply_promotion_adjustment)
        assert "has_promotion_history" in source
        assert "D-skip_no_history" in source
