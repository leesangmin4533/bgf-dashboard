"""finalize_order() 단위 테스트

Phase 4 — 4차 리팩토링: finalize_order 통합
rule/promo/round 세 Result를 받아 최종 발주 수량 결정하는 로직 검증.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.order_proposal import (
    RuleResult, PromoResult, RoundResult, OrderProposal,
)


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
        p.store_id = "46513"
        return p


def _make_proposal():
    """기본 OrderProposal"""
    return OrderProposal(need_qty=5.0)


def _make_item_info(**overrides):
    """기본 item_info dict"""
    info = {"store_id": "46513", "item_cd": "TEST001"}
    info.update(overrides)
    return info


# ─────────────────────────────────────────────
# TC-F01: surplus 차단 → 0 반환
# ─────────────────────────────────────────────


class TestFinalizeSurplus:
    """round_surplus_zero 차단 케이스"""

    def test_tc_f01_surplus_returns_zero(self):
        """[TC-F01] surplus 차단이 finalize에서 0 반환"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        round_result = RoundResult(
            qty=0, delta=-3, reason="surplus=10>=safety=5",
            stage="round_surplus_zero",
            floor_qty=0, ceil_qty=16, selected="zero"
        )
        rule_result = RuleResult(
            qty=3, delta=0, reason="pass", stage="rules_pass"
        )
        promo_result = PromoResult(
            qty=3, delta=0, reason="pass",
            stage="promo_pass", skipped=False
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 0, (
            f"[TC-F01] surplus 차단 시 0이어야 함 (result={result})"
        )

    def test_tc_f01b_surplus_ignores_nonzero_rule_promo(self):
        """[TC-F01b] rule/promo가 양수여도 surplus → 0"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        round_result = RoundResult(
            qty=0, delta=-16, reason="surplus=20>=safety=5",
            stage="round_surplus_zero",
            floor_qty=0, ceil_qty=16, selected="zero"
        )
        rule_result = RuleResult(
            qty=16, delta=0, reason="pass", stage="rules_pass"
        )
        promo_result = PromoResult(
            qty=16, delta=0, reason="행사 안정기",
            stage="promo_C_active", skipped=False
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 0, (
            f"[TC-F01b] surplus 차단은 rule/promo 무관 → 0 (result={result})"
        )


# ─────────────────────────────────────────────
# TC-F02: overstock 차단 → 0 반환
# ─────────────────────────────────────────────


class TestFinalizeOverstock:
    """rules_overstock 차단 케이스"""

    def test_tc_f02_overstock_returns_zero(self):
        """[TC-F02] overstock 차단이 finalize에서 0 반환"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=0, delta=-5, reason="stock_days=8>=7",
            stage="rules_overstock"
        )
        promo_result = PromoResult(
            qty=0, delta=0, reason="pass",
            stage="promo_pass", skipped=False
        )
        round_result = RoundResult(
            qty=0, delta=0, reason="pass",
            stage="round_pass",
            floor_qty=0, ceil_qty=0, selected="zero"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 0, (
            f"[TC-F02] overstock 차단 시 0이어야 함 (result={result})"
        )


# ─────────────────────────────────────────────
# TC-F03: 정상 케이스 — round_result.qty 반환
# ─────────────────────────────────────────────


class TestFinalizeNormal:
    """정상 발주 케이스"""

    def test_tc_f03_normal_returns_round_qty(self):
        """[TC-F03] 정상 케이스 — round_result.qty 반환"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=3, delta=0, reason="pass", stage="rules_pass"
        )
        promo_result = PromoResult(
            qty=3, delta=0, reason="pass",
            stage="promo_pass", skipped=False
        )
        round_result = RoundResult(
            qty=16, delta=13, reason="ceil 선택",
            stage="round_ceil_needs",
            floor_qty=0, ceil_qty=16, selected="ceil"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 16, (
            f"[TC-F03] 정상 케이스 round_qty=16이어야 함 (result={result})"
        )

    def test_tc_f03b_floor_returns_round_qty(self):
        """[TC-F03b] floor 선택 케이스 — round_result.qty 반환"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=5, delta=0, reason="pass", stage="rules_pass"
        )
        promo_result = PromoResult(
            qty=8, delta=3, reason="행사 보정",
            stage="promo_C_active", skipped=False
        )
        round_result = RoundResult(
            qty=6, delta=-2, reason="floor 선택",
            stage="round_floor_default",
            floor_qty=6, ceil_qty=12, selected="floor"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 6, (
            f"[TC-F03b] floor 선택 시 round_qty=6이어야 함 (result={result})"
        )

    def test_tc_f03c_round_pass_returns_qty(self):
        """[TC-F03c] round_pass (배수=1) — round_result.qty 반환"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=3, delta=0, reason="pass", stage="rules_pass"
        )
        promo_result = PromoResult(
            qty=3, delta=0, reason="disabled",
            stage="promo_pass", skipped=True
        )
        round_result = RoundResult(
            qty=3, delta=0, reason="skip (unit=1 or qty=0)",
            stage="round_pass",
            floor_qty=0, ceil_qty=0, selected="none"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 3, (
            f"[TC-F03c] round_pass 시 qty=3이어야 함 (result={result})"
        )


# ─────────────────────────────────────────────
# TC-F04: 우선순위 검증 (surplus > overstock)
# ─────────────────────────────────────────────


class TestFinalizePriority:
    """차단 우선순위: round_surplus_zero > rules_overstock"""

    def test_tc_f04_surplus_beats_overstock(self):
        """[TC-F04] surplus와 overstock 동시 → surplus 우선 (0 반환)"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=0, delta=-5, reason="stock_days=10>=7",
            stage="rules_overstock"
        )
        promo_result = PromoResult(
            qty=0, delta=0, reason="pass",
            stage="promo_pass", skipped=False
        )
        round_result = RoundResult(
            qty=0, delta=0, reason="surplus=15>=safety=5",
            stage="round_surplus_zero",
            floor_qty=0, ceil_qty=16, selected="zero"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 0, (
            f"[TC-F04] surplus+overstock 동시 → 0 (result={result})"
        )

    def test_tc_f04b_overstock_without_surplus(self):
        """[TC-F04b] overstock만 (surplus 아님) → 0"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=0, delta=-5, reason="stock_days=9>=7",
            stage="rules_overstock"
        )
        promo_result = PromoResult(
            qty=0, delta=0, reason="pass",
            stage="promo_pass", skipped=False
        )
        round_result = RoundResult(
            qty=0, delta=0, reason="pass",
            stage="round_pass",
            floor_qty=0, ceil_qty=0, selected="none"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 0, (
            f"[TC-F04b] overstock만 → 0 (result={result})"
        )

    def test_tc_f04c_no_block_returns_round_qty(self):
        """[TC-F04c] 차단 없음 → round_result.qty 반환"""
        p = _make_predictor()
        proposal = _make_proposal()
        item_info = _make_item_info()

        rule_result = RuleResult(
            qty=5, delta=0, reason="pass", stage="rules_pass"
        )
        promo_result = PromoResult(
            qty=8, delta=3, reason="행사 시작 부스트",
            stage="promo_B_starting", skipped=False
        )
        round_result = RoundResult(
            qty=12, delta=4, reason="ceil 선택",
            stage="round_ceil_needs",
            floor_qty=6, ceil_qty=12, selected="ceil"
        )

        result = p._finalize_order(
            rule_result, promo_result, round_result,
            proposal, item_info
        )
        assert result == 12, (
            f"[TC-F04c] 차단 없음 → round_qty=12 (result={result})"
        )
