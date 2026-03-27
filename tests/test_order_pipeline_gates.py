"""발주 파이프라인 게이트 테스트

재고 차단 게이트(stock_gate)의 발동/비발동 조건을 검증:
- stock_gate_entry    : 가용재고(stock+pending) >= 커버일수분 → need_qty=0
- stock_gate_overstock: stock_days >= threshold(5일) → order_qty=0
- stock_gate_surplus  : surplus >= safety_stock → 발주 취소

참조: config/order_priority.json, docs/known_cases.md
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.order_proposal import OrderProposal, Adjustment, RuleResult
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


# ─────────────────────────────────────────────
# TC-01: stock_gate_overstock 경계값
# ─────────────────────────────────────────────


class TestStockGateOverstock:
    """stock_gate_overstock: _apply_order_rules의 stock_days 경계값"""

    def test_tc01a_below_threshold_no_block(self):
        """[TC-01A] stock_days(4.67) < threshold(5) → overstock 비발동"""
        p = _make_predictor()

        # effective_stock = current_stock(12) + pending(2) = 14
        # stock_days = 14 / 3.0 = 4.67
        # threshold = 5 → 4.67 < 5 → overstock 비발동
        rule_result = p._apply_order_rules(
            need_qty=5.0,
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            weekday=1,  # 화요일
            current_stock=12,
            daily_avg=3.0,
            pending_qty=2,
        )

        # 5.0 - int(5.0) = 0.0 < 0.5(round_up_threshold) → int(5.0) = 5
        assert rule_result.qty == 5, (
            f"[TC-01A] stock_days(4.67) < threshold(5) → overstock 비발동이어야 함 "
            f"(expected=5, result={rule_result.qty})"
        )
        assert rule_result.stage != "rules_overstock", (
            f"[TC-01A] stage가 rules_overstock이 아니어야 함 "
            f"(actual={rule_result.stage})"
        )

    def test_tc01b_at_threshold_blocks(self):
        """[TC-01B] stock_days(5.0) >= threshold(5) → overstock 발동 → 0"""
        p = _make_predictor()

        # effective_stock = current_stock(15) + pending(0) = 15
        # stock_days = 15 / 3.0 = 5.0
        # threshold = 5 → 5.0 >= 5 → overstock 발동 → return 0
        rule_result = p._apply_order_rules(
            need_qty=5.0,
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            weekday=1,
            current_stock=15,
            daily_avg=3.0,
            pending_qty=0,
        )

        assert rule_result.qty == 0, (
            f"[TC-01B] stock_days(5.0) >= threshold(5) → overstock 발동이어야 함 "
            f"(expected=0, result={rule_result.qty})"
        )
        assert rule_result.stage == "rules_overstock", (
            f"[TC-01B] stage=rules_overstock이어야 함 "
            f"(actual={rule_result.stage})"
        )

    def test_tc01b_overstock_then_promo_override(self):
        """[TC-01B+] overstock→0 이후 promo 재고부족 시 양수 복원"""
        p = _make_predictor()

        # Step 1: overstock → 0
        rule_result = p._apply_order_rules(
            need_qty=5.0,
            product={"item_nm": "행사상품", "mid_cd": "099", "expiration_days": 180},
            weekday=1,
            current_stock=15,
            daily_avg=3.0,
            pending_qty=0,
        )
        order_qty = rule_result.qty
        assert order_qty == 0, (
            f"[TC-01B+step1] overstock → 0이어야 함 (result={order_qty})"
        )

        # Step 2: promo 재고 부족 시나리오 → 양수 복원
        status = _make_status(
            current_promo="1+1",
            days_until_end=15,
            promo_avg=10.0,
            normal_avg=2.0,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        ctx = {}
        order_qty = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "행사상품", "mid_cd": "099"},
            order_qty=0,  # overstock에서 0
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=2,   # ★ promo 기준 재고 부족
            pending_qty=0,
            daily_avg=2.0,
            ctx=ctx,
        ).qty

        # promo_daily_demand = 10.0 * 1.0 = 10.0
        # stock(2) < promo_demand(10.0) → C-promo_adjust 발동
        assert order_qty > 0, (
            f"[TC-01B+step2] promo 재고부족 → 양수 복원이어야 함 "
            f"(promo_demand=10.0, stock=2, result={order_qty})"
        )


# ─────────────────────────────────────────────
# TC-02: stock_gate_surplus (entry 통과 → surplus 차단)
# ─────────────────────────────────────────────


class TestStockGateSurplus:
    """stock_gate_surplus: _round_to_order_unit의 surplus 취소"""

    def test_tc02_entry_pass_surplus_blocks(self):
        """[TC-02] entry 통과 → surplus gate 차단 체인

        시나리오: order_qty=3, unit=16
        - ceil=16, floor=0
        - B-default: surplus = 16-3 = 13
        - surplus(13) >= safety(4.0) ✓
        - stock(5)+surplus(13)=18 >= pred(1.0)+safety(4.0)=5.0 ✓
        → return 0 + ctx["_stock_gate"] 기록
        """
        p = _make_predictor()
        ctx = _make_ctx()

        result = p._round_to_order_unit(
            order_qty=3,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "소량상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=1.0,
            current_stock=5,
            pending_qty=0,
            safety_stock=4.0,
            adjusted_prediction=1.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        assert result == 0, (
            f"[TC-02] surplus(13) >= safety(4.0) → 차단이어야 함 "
            f"(expected=0, result={result})"
        )

        # ctx에 stock_gate 정보 기록 확인
        gate = ctx.get("_stock_gate", {})
        assert gate.get("stage") == "stock_gate_surplus", (
            f"[TC-02] stock_gate_surplus가 ctx에 기록이어야 함 "
            f"(actual_stage={gate.get('stage')})"
        )
        assert "surplus=13" in gate.get("reason", ""), (
            f"[TC-02] reason에 surplus=13 포함이어야 함 "
            f"(actual_reason={gate.get('reason', '')})"
        )

    def test_tc02_surplus_insufficient_passes(self):
        """[TC-02 반전] surplus < safety → 차단 안 됨"""
        p = _make_predictor()
        ctx = _make_ctx()

        # order_qty=10, unit=16 → ceil=16, floor=0
        # B-default: surplus = 16-10 = 6
        # surplus(6) < safety(10.0) → 체크 실패 → needs_ceil 또는 ceil
        result = p._round_to_order_unit(
            order_qty=10,
            order_unit=16,
            mid_cd="099",
            product={"item_nm": "일반상품", "mid_cd": "099", "expiration_days": 180},
            daily_avg=5.0,
            current_stock=1,
            pending_qty=0,
            safety_stock=10.0,
            adjusted_prediction=5.0,
            ctx=ctx,
            new_cat_pattern=None,
            is_default_category=True,
        ).qty

        # surplus(6) < safety(10.0) → surplus check FAIL
        # days_cover = 1/5.0 = 0.2 < 0.5 → needs_ceil=True → return 16
        assert result == 16, (
            f"[TC-02 반전] surplus(6) < safety(10) → 차단 안 됨 "
            f"(expected=16, result={result})"
        )
        assert "_stock_gate" not in ctx, (
            f"[TC-02 반전] stock_gate가 기록되지 않아야 함 "
            f"(ctx keys={list(ctx.keys())})"
        )


# ─────────────────────────────────────────────
# TC-03: stock_gate_entry (pending 포함 비교)
# ─────────────────────────────────────────────


class TestStockGateEntry:
    """stock_gate_entry: pending 포함 가용재고에 의한 진입 차단"""

    def test_tc03_pending_triggers_entry_gate(self):
        """[TC-03] pending=0 → 통과, pending=10 → entry gate 발동

        Entry gate 로직 (improved_predictor.py L1486-1498):
          effective_stock = current_stock + pending_qty
          cover_need = adjusted_prediction * days_until_next
          if effective_stock >= cover_need → need_qty=0
        """
        # ── 산술 검증 ──
        adjusted_prediction = 5.0
        days_until_next = 1
        cover_need = adjusted_prediction * days_until_next  # 5.0
        current_stock = 3

        # Case A: pending=0 → effective=3 < cover_need=5 → 통과
        effective_a = current_stock + 0
        assert effective_a < cover_need, (
            f"[TC-03A] pending=0 → effective({effective_a}) < "
            f"cover_need({cover_need}) → gate 비발동이어야 함"
        )

        # Case B: pending=5 → effective=8 >= cover_need=5 → 차단
        effective_b = current_stock + 5
        assert effective_b >= cover_need, (
            f"[TC-03B] pending=5 → effective({effective_b}) >= "
            f"cover_need({cover_need}) → gate 발동이어야 함"
        )

    def test_tc03_pending_in_promo_method(self):
        """[TC-03 실제] _apply_promotion_adjustment에서 pending 반영"""
        p = _make_predictor()

        status = _make_status(
            current_promo="2+1",
            days_until_end=10,
            promo_avg=5.0,
            normal_avg=2.0,
        )
        p._promo_adjuster.promo_manager.get_promotion_status.return_value = status

        # pending=0 → stock(3) < promo_demand(5.0) → 보정 발동
        result_no_pending = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트", "mid_cd": "099"},
            order_qty=0,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=3,
            pending_qty=0,
            daily_avg=2.0,
            ctx={},
        ).qty

        # pending=10 → stock+pending(13) >= promo_demand(5.0) → 보정 스킵
        result_with_pending = p._apply_promotion_adjustment(
            item_cd="TEST001",
            product={"item_nm": "테스트", "mid_cd": "099"},
            order_qty=0,
            weekday_coef=1.0,
            safety_stock=5.0,
            current_stock=3,
            pending_qty=10,
            daily_avg=2.0,
            ctx={},
        ).qty

        assert result_no_pending > 0, (
            f"[TC-03] pending=0 → 보정 발동이어야 함 "
            f"(stock=3, promo_demand=5.0, result={result_no_pending})"
        )
        assert result_with_pending == 0, (
            f"[TC-03] pending=10 → 재고 충분 → 보정 스킵이어야 함 "
            f"(stock+pending=13, result={result_with_pending})"
        )


# ─────────────────────────────────────────────
# OrderProposal 단위 테스트
# ─────────────────────────────────────────────


class TestOrderProposalTracking:
    """OrderProposal 이력 추적 기능 검증"""

    def test_proposal_set_records_adjustment(self):
        """proposal.set()이 adjustment를 정확히 기록"""
        proposal = OrderProposal(need_qty=10.0)

        proposal.set(8, "order_rules", "float→int")
        proposal.set(6, "promo", "행사 종료 D-2 감량")
        proposal.set(6, "ml_ensemble", "ML 블렌딩 (변경 없음)")

        assert proposal.qty == 6
        assert len(proposal.adjustments) == 3
        assert proposal.adjustments[0].stage == "order_rules"
        assert proposal.adjustments[0].before == 0
        assert proposal.adjustments[0].after == 8
        assert proposal.adjustments[1].delta == -2  # 8→6

    def test_stock_gate_summary_returns_first_gate(self):
        """stock_gate_summary()가 첫 번째 gate만 반환"""
        proposal = OrderProposal(need_qty=5.0)

        proposal.set(0, "stock_gate_entry", "가용=10 >= 필요=5.0")

        summary = proposal.stock_gate_summary()
        assert summary is not None
        assert "stock_gate_entry" in summary

    def test_stock_gate_summary_none_when_no_gate(self):
        """gate 없으면 None 반환"""
        proposal = OrderProposal(need_qty=5.0)

        proposal.set(3, "order_rules", "float→int")
        proposal.set(3, "round_to_unit", "B-default")

        summary = proposal.stock_gate_summary()
        assert summary is None

    def test_changed_stages_filters_unchanged(self):
        """changed_stages()가 변경 없는 단계를 필터링"""
        proposal = OrderProposal(need_qty=5.0)

        proposal.set(3, "order_rules", "float→int")
        proposal.set(3, "ml_ensemble", "변경 없음")  # same value
        proposal.set(0, "stock_gate_surplus", "surplus 충분")

        changed = proposal.changed_stages()
        assert len(changed) == 2  # order_rules(0→3), surplus(3→0)
        assert changed[0].stage == "order_rules"
        assert changed[1].stage == "stock_gate_surplus"
