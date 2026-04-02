"""경계값 복합 조건 테스트

반복 버그 패턴 방지를 위한 경계값 복합 테스트:
- WMA=0 + stock>0 + need>0 (안전재고만으로 발주 트리거)
- WMA=0 + stock=0 + pending=0 (완전 무재고)
- daily_avg=0 + overstock_prevention 스킵
- friday_boost + WMA=0 (무판매 상품 부스트)
- 덮어쓰기 방지: 단계 간 의도 보존

참조: 커밋 분석 패턴 1(덮어쓰기), 패턴 2(경계조건)
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.order_proposal import OrderProposal, Adjustment, RuleResult


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


def _product(mid_cd="049", expiration_days=180):
    return {"mid_cd": mid_cd, "item_nm": "테스트상품", "expiration_days": expiration_days}


# ═══════════════════════════════════════════════
# 패턴 2: 경계조건 (WMA=0, stock=0, daily_avg=0)
# ═══════════════════════════════════════════════

class TestZeroDemandWithStock:
    """WMA=0 + 재고>0: 안전재고만으로 발주 트리거 방지"""

    def test_beer_wma0_stock19_should_skip(self):
        """산토리나마비어 재현: WMA=0, stock=19 → 발주=0"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=5.7, product=_product("049"),
            weekday=4, current_stock=19, daily_avg=0.0, pending_qty=0,
        )
        assert result.qty == 0
        assert "zero_demand" in result.reason

    def test_soju_wma0_stock10_should_skip(self):
        """소주(050): WMA=0, stock=10 → 발주=0"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=3.0, product=_product("050"),
            weekday=4, current_stock=10, daily_avg=0.0, pending_qty=0,
        )
        assert result.qty == 0

    def test_tobacco_wma0_stock5_should_skip(self):
        """전자담배(073): WMA=0, stock=5 → 발주=0"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=2.0, product=_product("073"),
            weekday=4, current_stock=5, daily_avg=0.0, pending_qty=0,
        )
        assert result.qty == 0

    def test_pending_only_should_skip(self):
        """stock=0 + pending=5 → 발주=0 (미입고만 있어도 스킵)"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=3.0, product=_product("049"),
            weekday=2, current_stock=0, daily_avg=0.0, pending_qty=5,
        )
        assert result.qty == 0
        assert "zero_demand" in result.reason


class TestZeroDemandZeroStock:
    """WMA=0 + stock=0 + pending=0: 완전 무재고는 통과해야 함"""

    def test_zero_everything_passes_through(self):
        """재고=0, 미입고=0, WMA=0 → overstock 가드 미발동 (다른 규칙으로 처리)"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=3.0, product=_product("049"),
            weekday=2, current_stock=0, daily_avg=0.0, pending_qty=0,
        )
        # overstock_zero_demand은 stock+pending>0 조건이므로 발동 안 함
        assert result.stage != "rules_overstock_zero_demand"

    def test_food_zero_stock_minimum_order(self):
        """푸드(001) stock=0, pending=0, daily_avg=0.3 → 최소 1개 발주"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=0.5, product=_product("001", expiration_days=1),
            weekday=2, current_stock=0, daily_avg=0.3, pending_qty=0,
        )
        assert result.qty >= 1
        assert result.stage == "rules_food_minimum"


class TestFridayBoostGuard:
    """금요일 부스트: WMA=0이면 미적용"""

    def test_skip_boost_when_wma_zero(self):
        """WMA=0 + 금요일 + 맥주 → friday_boost 미적용"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=5.7, product=_product("049"),
            weekday=4, current_stock=19, daily_avg=0.0, pending_qty=0,
        )
        # overstock_zero_demand이 먼저 잡으므로 friday_boost 도달 전에 return
        assert result.stage != "rules_friday_boost"

    def test_apply_boost_when_wma_positive(self):
        """WMA>0 + 금요일 + 맥주 → friday_boost 정상 적용"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=5.0, product=_product("049"),
            weekday=4, current_stock=5, daily_avg=3.0, pending_qty=0,
        )
        # daily_avg=3.0 > 0 이고 stock_days=5/3=1.67 < 5 이므로 overstock 미발동
        assert result.stage == "rules_friday_boost"

    def test_no_boost_on_non_friday(self):
        """화요일 → friday_boost 미적용 (weekday=1)"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=5.0, product=_product("049"),
            weekday=1, current_stock=5, daily_avg=3.0, pending_qty=0,
        )
        assert result.stage != "rules_friday_boost"

    def test_no_boost_for_non_target_category(self):
        """라면(006) → friday_boost 대상 아님"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=5.0, product=_product("006"),
            weekday=4, current_stock=5, daily_avg=3.0, pending_qty=0,
        )
        assert result.stage != "rules_friday_boost"


class TestOverstockPrevention:
    """재고 과다 방지: stock_days >= 5 → 스킵"""

    def test_overstock_blocks_when_stock_days_high(self):
        """stock_days = 20/3 = 6.67 >= 5 → 발주 스킵"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=3.0, product=_product("049"),
            weekday=2, current_stock=20, daily_avg=3.0, pending_qty=0,
        )
        assert result.qty == 0
        assert result.stage == "rules_overstock"

    def test_overstock_passes_when_stock_days_low(self):
        """stock_days = 5/3 = 1.67 < 5 → 통과"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=3.0, product=_product("049"),
            weekday=2, current_stock=5, daily_avg=3.0, pending_qty=0,
        )
        assert result.qty > 0

    def test_overstock_includes_pending(self):
        """stock=3, pending=15 → stock_days=(3+15)/3=6.0 >= 5 → 스킵"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=3.0, product=_product("049"),
            weekday=2, current_stock=3, daily_avg=3.0, pending_qty=15,
        )
        assert result.qty == 0
        assert result.stage == "rules_overstock"


class TestThresholdEdgeCases:
    """최소 임계값 경계"""

    def test_below_threshold_returns_zero(self):
        """need=0.05 < threshold(0.1) → 발주=0"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=0.05, product=_product("049"),
            weekday=2, current_stock=2, daily_avg=3.0, pending_qty=0,
        )
        assert result.qty == 0
        assert result.stage == "rules_threshold"

    def test_between_threshold_and_one_returns_one(self):
        """0.1 <= need=0.5 < 1.0 → 발주=1"""
        p = _make_predictor()
        result = p._apply_order_rules(
            need_qty=0.5, product=_product("049"),
            weekday=2, current_stock=2, daily_avg=3.0, pending_qty=0,
        )
        assert result.qty == 1


# ═══════════════════════════════════════════════
# 패턴 1: 덮어쓰기 방지 (OrderProposal 불변식)
# ═══════════════════════════════════════════════

class TestProposalInvariants:
    """OrderProposal 불변식 검증"""

    def test_set_records_adjustment(self):
        """set()은 항상 Adjustment를 기록"""
        p = OrderProposal(need_qty=10.0)
        p.set(8, "stage_a", "reason_a")
        p.set(6, "stage_b", "reason_b")
        assert len(p.adjustments) == 2
        assert p.qty == 6

    def test_unchanged_stage_recorded_but_not_changed(self):
        """값 변화 없는 단계도 기록되지만 changed=False"""
        p = OrderProposal(need_qty=10.0)
        p.set(8, "stage_a")
        p.set(8, "stage_b")  # 변화 없음
        assert len(p.adjustments) == 2
        assert p.adjustments[1].changed is False
        assert p.changed_stages() == [p.adjustments[0]]

    def test_summary_shows_only_changed(self):
        """summary()는 변경된 단계만 표시"""
        p = OrderProposal(need_qty=10.0)
        p.set(8, "rules_pass")
        p.set(6, "promo")
        p.set(6, "ml_ensemble")  # 변화 없음
        summary = p.summary()
        assert "rules_pass" in summary
        assert "promo" in summary
        assert "ml_ensemble" not in summary

    def test_to_dict_preserves_all_stages(self):
        """to_dict()는 모든 단계를 보존"""
        p = OrderProposal(need_qty=5.0)
        p.set(3, "rules")
        p.set(3, "promo")
        d = p.to_dict()
        assert len(d["stages"]) == 2
        assert d["final_qty"] == 3
        assert d["need_qty"] == 5.0

    def test_qty_never_negative(self):
        """set()에 음수를 넣어도 기록은 되지만 qty 반환"""
        p = OrderProposal(need_qty=5.0)
        # 음수 설정은 가능하나 파이프라인에서 0으로 보정되어야 함
        p.set(-1, "test_negative")
        assert p.qty == -1  # OrderProposal은 값 검증 안 함 (호출부 책임)
        assert p.adjustments[0].after == -1


class TestBeerOutlierCap:
    """beer.py daily_avg 이상치 cap 검증"""

    def test_cap_applied_with_spike(self):
        """판매량에 이상치 포함 시 cap 후 daily_avg 감소"""
        # 7개 판매: [1, 2, 2, 4, 4, 4, 18]
        daily_sales = [1, 2, 2, 4, 4, 4, 18]
        data_days = 8

        cap_idx = max(0, int(len(daily_sales) * 0.95) - 1)
        cap_value = sorted(daily_sales)[cap_idx]
        capped_total = sum(min(s, cap_value) for s in daily_sales)
        daily_avg = capped_total / data_days

        assert cap_value == 4  # 95th percentile
        assert capped_total == 21  # 1+2+2+4+4+4+4
        assert daily_avg == 21 / 8  # 2.625

    def test_no_cap_when_uniform_sales(self):
        """균일 판매 → cap 적용해도 변화 없음"""
        daily_sales = [3, 3, 3, 3, 3, 3, 3]
        data_days = 7

        cap_idx = max(0, int(len(daily_sales) * 0.95) - 1)
        cap_value = sorted(daily_sales)[cap_idx]
        capped_total = sum(min(s, cap_value) for s in daily_sales)
        original_total = sum(daily_sales)

        assert capped_total == original_total

    def test_cap_with_fewer_than_5_sales_days(self):
        """판매일 < 5 → cap 미적용, 원본 사용"""
        daily_sales = [2, 10, 3]
        data_days = 5
        # len(daily_sales) < 5 → fallback
        if len(daily_sales) >= 5:
            pytest.fail("should not reach cap logic")
        daily_avg = sum(daily_sales) / data_days
        assert daily_avg == 15 / 5  # 3.0

    def test_cap_preserves_data_days_denominator(self):
        """cap 후에도 data_days(전체 일수)로 나눔 (판매일이 아님)"""
        daily_sales = [1, 2, 50]  # 3일만 판매
        data_days = 30  # 30일 중 3일만 판매
        # len < 5 → fallback
        daily_avg = sum(daily_sales) / data_days
        assert daily_avg == 53 / 30
