"""
미입고조정(v11) - 실시간 재고 반영 need 재계산 테스트

문제: 예측 시점에 DB 재고=0 → 과대 발주량 산출 → 실시간 조회에서 재고 발견
     → 기존(v10)은 단순 차감이라 올림/배수 반영된 과대 발주가 남음
해결: v11에서 need = pred + safety - 신재고 - 신미입고 로 재계산
"""
import pytest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# _recalculate_need_qty 단위 테스트
# ---------------------------------------------------------------------------
class TestRecalculateNeedQty:
    """_recalculate_need_qty 메서드 테스트"""

    def _make_system(self):
        """AutoOrderSystem 최소 인스턴스 생성 (DB 미접속)"""
        from src.order.auto_order import AutoOrderSystem
        sys_obj = object.__new__(AutoOrderSystem)
        return sys_obj

    def test_basic_need_positive(self):
        """기본 케이스: pred=3 + safety=2 - stock=1 - pending=0 = 4"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3, safety_stock=2.0,
            new_stock=1, new_pending=0, daily_avg=3.0
        )
        assert result == 4

    def test_stock_sufficient_returns_zero(self):
        """재고 충분: pred=3 + safety=2 - stock=6 - pending=0 = -1 → 0"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3, safety_stock=2.0,
            new_stock=6, new_pending=0, daily_avg=3.0
        )
        assert result == 0

    def test_pending_sufficient_returns_zero(self):
        """미입고 충분: pred=3 + safety=2 - stock=0 - pending=6 = -1 → 0"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3, safety_stock=2.0,
            new_stock=0, new_pending=6, daily_avg=3.0
        )
        assert result == 0

    def test_stock_and_pending_combined(self):
        """재고+미입고 합산: pred=5 + safety=3 - stock=4 - pending=2 = 2"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=5, safety_stock=3.0,
            new_stock=4, new_pending=2, daily_avg=5.0
        )
        assert result == 2

    def test_round_up_threshold(self):
        """올림 임계값: need=3.5 → round_up_threshold=0.5 → 4"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3.5, safety_stock=2.0,
            new_stock=2, new_pending=0, daily_avg=3.5
        )
        assert result == 4  # 3.5 → 0.5 >= 0.5 → 올림 → 4

    def test_no_round_up_below_threshold(self):
        """올림 안 됨: need=3.3 → 0.3 < 0.5 → 3"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3.3, safety_stock=2.0,
            new_stock=2, new_pending=0, daily_avg=3.3
        )
        assert result == 3

    def test_small_need_below_min_threshold(self):
        """최소 임계값 미만: need=0.05 < 0.1 → 0"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.05, safety_stock=0.0,
            new_stock=0, new_pending=0, daily_avg=0.05
        )
        assert result == 0

    def test_small_need_above_min_threshold(self):
        """최소 임계값 이상: 0.1 <= need < 1.0 → 1"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.5, safety_stock=0.0,
            new_stock=0, new_pending=0, daily_avg=0.5
        )
        assert result == 1

    def test_order_unit_qty_multiple(self):
        """발주 배수: need=5 → order_unit_qty=6 → 6"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=5, safety_stock=2.0,
            new_stock=2, new_pending=0, daily_avg=5.0,
            order_unit_qty=6
        )
        assert result == 6  # need=5, 올림→5 → 배수6 적용 → 6

    def test_order_unit_qty_exact(self):
        """발주 배수 정확히 맞을 때: need=12 → unit=6 → 12"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=10, safety_stock=4.0,
            new_stock=2, new_pending=0, daily_avg=10.0,
            order_unit_qty=6
        )
        assert result == 12  # need=12 → 배수6 적용 → 12


# ---------------------------------------------------------------------------
# _apply_pending_and_stock_to_order_list 통합 테스트
# ---------------------------------------------------------------------------
class TestApplyPendingAndStockV11:
    """v11 미입고조정 통합 테스트"""

    def _make_system(self):
        """AutoOrderSystem 최소 인스턴스 생성"""
        from src.order.auto_order import AutoOrderSystem
        s = object.__new__(AutoOrderSystem)
        s._cut_items = set()
        s._unavailable_items = set()
        s._last_eval_results = {}
        return s

    def _make_item(self, item_cd="TEST001", item_nm="테스트상품",
                   final_order_qty=10, current_stock=0,
                   pending_receiving_qty=0, predicted_sales=3,
                   safety_stock=5.0, daily_avg=3.0,
                   order_unit_qty=1, mid_cd="015"):
        return {
            "item_cd": item_cd,
            "item_nm": item_nm,
            "mid_cd": mid_cd,
            "final_order_qty": final_order_qty,
            "recommended_qty": final_order_qty,
            "current_stock": current_stock,
            "pending_receiving_qty": pending_receiving_qty,
            "predicted_sales": predicted_sales,
            "safety_stock": safety_stock,
            "daily_avg": daily_avg,
            "order_unit_qty": order_unit_qty,
            "expected_stock": current_stock + pending_receiving_qty,
        }

    def test_no_change_keeps_original(self):
        """재고 변동 없으면 원래 발주량 유지"""
        s = self._make_system()
        item = self._make_item(final_order_qty=10, current_stock=0)
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 0},  # 변동 없음
            min_order_qty=1
        )
        assert len(result) == 1
        assert result[0]['final_order_qty'] == 10

    def test_stock_increase_recalculates_need(self):
        """핵심 테스트: 재고 0→8 발견 시 need 재계산
        예측시: stock=0, pred=3+safety=5=8 → 원발주=8 (올림 후)
        실시간: stock=8 → need=3+5-8-0=0 → 발주 제거
        """
        s = self._make_system()
        item = self._make_item(
            final_order_qty=8,  # 예측시 stock=0 기반
            current_stock=0,
            predicted_sales=3,
            safety_stock=5.0
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 8},
            min_order_qty=1
        )
        assert len(result) == 0  # 재고 충분 → 발주 제거

    def test_partial_stock_reduces_order(self):
        """부분 재고 발견 시 발주량 감소
        예측시: stock=0, pred=3+safety=5=8 → 원발주=8
        실시간: stock=3 → need=3+5-3-0=5 → 최종=5
        v10이었다면: 8-(3-0)=5 → 동일 (이 경우는 올림 없이 동일)
        """
        s = self._make_system()
        item = self._make_item(
            final_order_qty=8,
            current_stock=0,
            predicted_sales=3,
            safety_stock=5.0
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 3},
            min_order_qty=1
        )
        assert len(result) == 1
        assert result[0]['final_order_qty'] == 5

    def test_overinflated_order_corrected(self):
        """v11 핵심: 배수/올림으로 과대해진 원발주를 재계산으로 교정
        예측시: stock=0, pred=0+safety=6.3=6.3 → 올림→7 → 배수11 → 원발주=11
        실시간: stock=9 → need=0+6.3-9-0=-2.7 → 0
        v10이었다면: 11-(9-0)=2 (과대!)
        """
        s = self._make_system()
        item = self._make_item(
            final_order_qty=11,  # 배수/올림 적용된 과대값
            current_stock=0,
            predicted_sales=0,
            safety_stock=6.3,
            order_unit_qty=1
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 9},
            min_order_qty=1
        )
        assert len(result) == 0  # v11: 재계산으로 정확히 0

    def test_large_overinflated_with_unit_qty(self):
        """대량 과대발주 교정 (배수 단위 포함)
        예측시: stock=0, pred=0.07+safety=1.3 → need=1.37 → 올림→2 → 배수16→16
        실시간: stock=8 → need=0.07+1.3-8-0=-6.6 → 0
        v10이었다면: 16-(8-0)=8 (과대!)
        """
        s = self._make_system()
        item = self._make_item(
            final_order_qty=16,
            current_stock=0,
            predicted_sales=0.07,
            safety_stock=1.3,
            order_unit_qty=16
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": 8},
            min_order_qty=1
        )
        assert len(result) == 0

    def test_pending_increase_recalculates(self):
        """미입고 증가 시 재계산
        예측시: pending=0, pred=3+safety=2=5 → 원발주=5
        실시간: pending=3 → need=3+2-0-3=2
        """
        s = self._make_system()
        item = self._make_item(
            final_order_qty=5,
            current_stock=0,
            pending_receiving_qty=0,
            predicted_sales=3,
            safety_stock=2.0
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={"TEST001": 3},
            stock_data={},
            min_order_qty=1
        )
        assert len(result) == 1
        assert result[0]['final_order_qty'] == 2

    def test_cut_items_excluded(self):
        """CUT 상품은 여전히 제외"""
        s = self._make_system()
        s._cut_items = {"CUT001"}
        item = self._make_item(item_cd="CUT001", final_order_qty=5)
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={},
            min_order_qty=1
        )
        assert len(result) == 0

    def test_unavailable_items_excluded(self):
        """미취급 상품은 여전히 제외"""
        s = self._make_system()
        s._unavailable_items = {"UNA001"}
        item = self._make_item(item_cd="UNA001", final_order_qty=5)
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={},
            min_order_qty=1
        )
        assert len(result) == 0

    def test_force_order_protection_with_stock(self):
        """FORCE_ORDER이지만 재고 있으면 보호 생략"""
        from src.prediction.pre_order_evaluator import EvalDecision, PreOrderEvalResult
        s = self._make_system()
        s._last_eval_results = {
            "FORCE001": PreOrderEvalResult(
                item_cd="FORCE001",
                item_nm="테스트FORCE",
                mid_cd="015",
                decision=EvalDecision.FORCE_ORDER,
                exposure_days=0.0,
                stockout_frequency=0.5,
                popularity_score=0.8,
                daily_avg=1.0,
                current_stock=0,
                pending_qty=0,
                reason="stock=0"
            )
        }
        item = self._make_item(
            item_cd="FORCE001",
            final_order_qty=1,
            current_stock=0,
            predicted_sales=1,
            safety_stock=0.5
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"FORCE001": 2},  # 실시간 재고 발견
            min_order_qty=1
        )
        # need=1+0.5-2-0=-0.5 → 0 → min_order_qty 미만 → FORCE인데 stock>0 → 보호생략
        assert len(result) == 0

    def test_force_order_protection_no_stock(self):
        """FORCE_ORDER이고 재고 없으면 최소 1개 보장"""
        from src.prediction.pre_order_evaluator import EvalDecision, PreOrderEvalResult
        s = self._make_system()
        s._last_eval_results = {
            "FORCE002": PreOrderEvalResult(
                item_cd="FORCE002",
                item_nm="테스트FORCE2",
                mid_cd="015",
                decision=EvalDecision.FORCE_ORDER,
                exposure_days=0.0,
                stockout_frequency=0.5,
                popularity_score=0.8,
                daily_avg=1.0,
                current_stock=0,
                pending_qty=0,
                reason="stock=0"
            )
        }
        item = self._make_item(
            item_cd="FORCE002",
            final_order_qty=1,
            current_stock=0,
            predicted_sales=0,
            safety_stock=0.0
        )
        # 실시간에서도 재고=0, 미입고 증가로 need=0이 되는 경우는 아님
        # stock 변동 없으면 원발주 유지 = 1
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"FORCE002": 0},  # 변동 없음
            min_order_qty=1
        )
        assert len(result) == 1
        assert result[0]['final_order_qty'] == 1

    def test_negative_stock_treated_as_zero(self):
        """음수 재고는 0으로 처리
        신재고=-1 → max(0, -1)=0 으로 재계산
        """
        s = self._make_system()
        item = self._make_item(
            final_order_qty=5,
            current_stock=0,
            predicted_sales=3,
            safety_stock=2.0
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"TEST001": -1},
            min_order_qty=1
        )
        assert len(result) == 1
        assert result[0]['final_order_qty'] == 5  # need=3+2-0-0=5

    def test_real_case_kinder_chocolate(self):
        """실제 사례: 킨더초콜릿조이걸
        예측시: stock=0, pred=0+safety=약24개분 → 원발주=24
        실시간: stock=8 → v10: 24-8=16, v11: need=0+24-8-0=16 (safety가 큰 경우)
        하지만 실제로는 pred_sales(adjusted)가 작고 safety가 대부분
        실제: pred_sales=0, safety=1.73 → need=0+1.73-8=-6.27 → 0
        (원발주 24는 ensemble 모델 결과이므로 pred_sales와 다를 수 있음)
        """
        s = self._make_system()
        # ensemble 모델은 safety 외에도 모델 자체 예측값이 반영되므로
        # predicted_sales (= adjusted_qty → int)가 실제 발주량과 다를 수 있음
        # 하지만 need 재계산은 pred_sales + safety 기반이므로 더 정확
        item = self._make_item(
            item_cd="8801000001",
            item_nm="매일)킨더초콜릿조이걸",
            final_order_qty=24,
            current_stock=0,
            predicted_sales=0,  # adj=0 (매출 0)
            safety_stock=1.73,
            daily_avg=0.0,
            order_unit_qty=8
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"8801000001": 8},
            min_order_qty=1
        )
        assert len(result) == 0  # 재고 충분 → 발주 제거

    def test_multiple_items_mixed(self):
        """여러 상품 혼합: 변동 있는 것과 없는 것"""
        s = self._make_system()
        items = [
            self._make_item(item_cd="A", item_nm="변동없음", final_order_qty=5,
                          current_stock=2, predicted_sales=3, safety_stock=2.0),
            self._make_item(item_cd="B", item_nm="재고충분", final_order_qty=10,
                          current_stock=0, predicted_sales=2, safety_stock=3.0),
            self._make_item(item_cd="C", item_nm="부분재고", final_order_qty=8,
                          current_stock=0, predicted_sales=5, safety_stock=3.0),
        ]
        result = s._apply_pending_and_stock_to_order_list(
            order_list=items,
            pending_data={},
            stock_data={"A": 2, "B": 10, "C": 3},
            min_order_qty=1
        )
        names = {r['item_cd']: r['final_order_qty'] for r in result}
        assert "A" in names and names["A"] == 5  # 변동 없음 → 원래 유지
        assert "B" not in names  # 재고 10: need=2+3-10=0-5 → 제거
        assert "C" in names and names["C"] == 5  # need=5+3-3-0=5


class TestRecalculateNeedQtyEdgeCases:
    """_recalculate_need_qty 엣지 케이스 테스트"""

    def _make_system(self):
        from src.order.auto_order import AutoOrderSystem
        return object.__new__(AutoOrderSystem)

    def test_zero_prediction_and_safety(self):
        """예측=0, 안전재고=0 → 0"""
        s = self._make_system()
        assert s._recalculate_need_qty(0, 0.0, 0, 0, 0.0) == 0

    def test_exactly_one(self):
        """need=1.0 → 정확히 1"""
        s = self._make_system()
        assert s._recalculate_need_qty(1.0, 0.0, 0, 0, 1.0) == 1

    def test_very_small_positive(self):
        """need=0.15 → min_threshold(0.1) 이상 → 1"""
        s = self._make_system()
        assert s._recalculate_need_qty(0.15, 0.0, 0, 0, 0.15) == 1

    def test_unit_qty_rounds_up(self):
        """need=7 → unit=6 → ceil(7/6)*6=12"""
        s = self._make_system()
        assert s._recalculate_need_qty(7, 2.0, 2, 0, 7.0, order_unit_qty=6) == 12

    def test_unit_qty_one_passthrough(self):
        """unit=1이면 배수 적용 안 함"""
        s = self._make_system()
        assert s._recalculate_need_qty(3, 2.0, 0, 0, 3.0, order_unit_qty=1) == 5


# ---------------------------------------------------------------------------
# 단기유통 푸드 미입고 할인 테스트
# ---------------------------------------------------------------------------
class TestShortExpiryFoodPendingDiscount:
    """유통기한 ≤1일 푸드의 미입고 차감 할인 테스트

    근거: 유통기한 1일 미입고 = 오늘 배송분 → 오늘 소진 예상
          → 내일 발주 차감에 50%만 반영 (FOOD_SHORT_EXPIRY_PENDING_DISCOUNT)
    """

    def _make_system(self):
        from src.order.auto_order import AutoOrderSystem
        return object.__new__(AutoOrderSystem)

    def _make_system_with_eval(self):
        """통합 테스트용 시스템 (eval_results 포함)"""
        from src.order.auto_order import AutoOrderSystem
        s = object.__new__(AutoOrderSystem)
        s._cut_items = set()
        s._unavailable_items = set()
        s._last_eval_results = {}
        return s

    def _make_food_item(self, item_cd="FOOD001", item_nm="주먹밥",
                        final_order_qty=5, current_stock=0,
                        pending_receiving_qty=0, predicted_sales=3.0,
                        safety_stock=2.0, daily_avg=3.0,
                        order_unit_qty=1, mid_cd="002",
                        expiration_days=1):
        return {
            "item_cd": item_cd,
            "item_nm": item_nm,
            "mid_cd": mid_cd,
            "final_order_qty": final_order_qty,
            "recommended_qty": final_order_qty,
            "current_stock": current_stock,
            "pending_receiving_qty": pending_receiving_qty,
            "predicted_sales": predicted_sales,
            "safety_stock": safety_stock,
            "daily_avg": daily_avg,
            "order_unit_qty": order_unit_qty,
            "expected_stock": current_stock + pending_receiving_qty,
            "expiration_days": expiration_days,
        }

    # --- _recalculate_need_qty 단위 테스트 ---

    def test_pending_discount_preserves_order(self):
        """1일 유통기한 주먹밥: pending=1 → effective_pending=0(50%할인) → 발주 유지

        핵심 시나리오: 46704 동양점 주먹밥
        pred=0.5, safety=0.3, stock=0, pending=1
        기존: need=0.5+0.3-0-1=-0.2 → 0 (발주 소멸!)
        수정: effective_pending=int(1*0.5)=0 → need=0.5+0.3-0-0=0.8 → 1 (발주 유지!)
        """
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.5, safety_stock=0.3,
            new_stock=0, new_pending=1, daily_avg=0.5,
            expiration_days=1, mid_cd="002"  # 주먹밥
        )
        assert result == 1  # 할인 후 need > 0 → 발주 유지

    def test_no_discount_for_2day_food(self):
        """2일 유통기한(샌드위치) → 할인 없음 (full 차감)"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.5, safety_stock=0.3,
            new_stock=0, new_pending=1, daily_avg=0.5,
            expiration_days=2, mid_cd="004"  # 샌드위치
        )
        assert result == 0  # 할인 없이 need=0.5+0.3-0-1=-0.2 → 0

    def test_no_discount_for_non_food(self):
        """비푸드 mid=032 → 할인 없음"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.5, safety_stock=0.3,
            new_stock=0, new_pending=1, daily_avg=0.5,
            expiration_days=1, mid_cd="032"  # 비푸드
        )
        assert result == 0  # 푸드가 아니므로 할인 없음

    def test_no_discount_when_no_pending(self):
        """pending=0 → 할인 로직 미진입"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.5, safety_stock=0.3,
            new_stock=0, new_pending=0, daily_avg=0.5,
            expiration_days=1, mid_cd="002"
        )
        assert result == 1  # pending=0이므로 할인 불필요, need=0.8 → 1

    def test_backward_compat_no_expiry_param(self):
        """expiration_days=None → 기존 동작 (할인 없음)"""
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=0.5, safety_stock=0.3,
            new_stock=0, new_pending=1, daily_avg=0.5
            # expiration_days, mid_cd 생략 → 기본값 None, ""
        )
        assert result == 0  # 기존 동작: need=-0.2 → 0

    def test_pending_discount_2units(self):
        """pending=2 → effective=int(2*0.5)=1 (50% 할인)

        pred=3, safety=2, stock=0, pending=2
        기존: need=3+2-0-2=3
        수정: effective=1 → need=3+2-0-1=4
        """
        s = self._make_system()
        result = s._recalculate_need_qty(
            predicted_sales=3, safety_stock=2.0,
            new_stock=0, new_pending=2, daily_avg=3.0,
            expiration_days=1, mid_cd="001"  # 도시락
        )
        assert result == 4  # effective_pending=1 → need=3+2-0-1=4

    # --- _apply_pending_and_stock_to_order_list 통합 테스트 ---

    def test_short_expiry_protection_keeps_order(self):
        """통합: 미입고 증가 시 원발주 유지

        예측시: pending=0, pred=0.5, safety=0.3 → 원발주=1
        실시간: pending=1(증가) → need=0.5+0.3-0-1=-0.2 → 0
        But: 1일 유통기한 푸드 → 원발주 1 유지
        """
        s = self._make_system_with_eval()
        item = self._make_food_item(
            final_order_qty=1,
            current_stock=0,
            pending_receiving_qty=0,
            predicted_sales=0.5,
            safety_stock=0.3,
            mid_cd="002",
            expiration_days=1
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={"FOOD001": 1},  # pending 0→1 증가
            stock_data={},
            min_order_qty=1
        )
        assert len(result) == 1
        assert result[0]['final_order_qty'] == 1  # 원발주 유지

    def test_non_food_pending_cancels_normally(self):
        """통합: 비푸드는 미입고 증가 시 정상 취소"""
        s = self._make_system_with_eval()
        item = self._make_food_item(
            item_cd="NONFOOD001",
            item_nm="일반상품",
            final_order_qty=1,
            current_stock=0,
            pending_receiving_qty=0,
            predicted_sales=0.5,
            safety_stock=0.3,
            mid_cd="032",  # 비푸드
            expiration_days=365
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={"NONFOOD001": 1},
            stock_data={},
            min_order_qty=1
        )
        assert len(result) == 0  # 비푸드 → 보호 안 됨 → 제거

    def test_stock_increase_not_protected(self):
        """재고 증가면 보호 불필요 (미입고 증가가 아님)"""
        s = self._make_system_with_eval()
        item = self._make_food_item(
            final_order_qty=1,
            current_stock=0,
            pending_receiving_qty=0,
            predicted_sales=0.5,
            safety_stock=0.3,
            mid_cd="002",
            expiration_days=1
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={},
            stock_data={"FOOD001": 2},  # 재고 증가 (pending 아님)
            min_order_qty=1
        )
        # 재고 증가 → new_pending==original_pending → 보호 조건 불충족 → 제거
        assert len(result) == 0

    def test_protection_only_when_pending_increased(self):
        """pending 감소 시 보호 안 함"""
        s = self._make_system_with_eval()
        item = self._make_food_item(
            final_order_qty=5,
            current_stock=0,
            pending_receiving_qty=3,  # 원래 pending=3
            predicted_sales=0.5,
            safety_stock=0.3,
            mid_cd="002",
            expiration_days=1
        )
        result = s._apply_pending_and_stock_to_order_list(
            order_list=[item],
            pending_data={"FOOD001": 2},  # pending 감소 3→2
            stock_data={"FOOD001": 3},  # stock도 증가
            min_order_qty=1
        )
        # pending 감소(3→2) → new_pending < original_pending → 보호 안 함
        # need=0.5+0.3-3-1(할인적용)=-3.2 → 0 → 제거
        assert len(result) == 0


# ---------------------------------------------------------------------------
# int→round 캐스팅 수정 + expiration_days 테스트
# ---------------------------------------------------------------------------
class TestIntCastingFix:
    """predicted_sales int→round 캐스팅 수정 + expiration_days dict 포함 테스트"""

    def _make_system(self, product_detail=None):
        """AutoOrderSystem 최소 인스턴스 + product_detail 캐시 설정"""
        from src.order.auto_order import AutoOrderSystem
        s = object.__new__(AutoOrderSystem)
        s.store_id = "46704"
        # _convert_prediction_result_to_dict 내부에서 self._product_detail_cache 사용
        s._product_detail_cache = {}
        s._product_repo = MagicMock()
        s._product_repo.get.return_value = product_detail
        return s

    def _make_result(self, item_cd="TEST001", item_nm="테스트", mid_cd="002",
                     adjusted_qty=1.0, predicted_qty=1.0, weekday_coef=1.0,
                     current_stock=0, pending_qty=0, safety_stock=0.3,
                     order_qty=1, food_expiration_days=None):
        result = MagicMock()
        result.item_cd = item_cd
        result.item_nm = item_nm
        result.mid_cd = mid_cd
        result.adjusted_qty = adjusted_qty
        result.predicted_qty = predicted_qty
        result.weekday_coef = weekday_coef
        result.current_stock = current_stock
        result.pending_qty = pending_qty
        result.safety_stock = safety_stock
        result.order_qty = order_qty
        result.target_date = "2026-02-25"
        result.confidence = 0.8
        result.data_days = 30
        result.stock_source = "realtime"
        result.pending_source = "bgf"
        result.is_stock_stale = False
        result.food_expiration_days = food_expiration_days
        return result

    def test_predicted_sales_preserves_float(self):
        """adj=0.50 → predicted_sales=0.5 (기존: int(0.50)=0)"""
        s = self._make_system()
        result = self._make_result(adjusted_qty=0.50, food_expiration_days=1)
        d = s._convert_prediction_result_to_dict(result)
        assert d['predicted_sales'] == 0.5  # round(0.50, 2)=0.5

    def test_predicted_sales_integer_unchanged(self):
        """adj=3.0 → predicted_sales=3.0 (정수도 정상 처리)"""
        s = self._make_system()
        result = self._make_result(adjusted_qty=3.0, mid_cd="015")
        d = s._convert_prediction_result_to_dict(result)
        assert d['predicted_sales'] == 3.0

    def test_expiration_days_in_dict(self):
        """dict에 expiration_days 포함 확인"""
        s = self._make_system()
        result = self._make_result(
            item_cd="TEST003", mid_cd="001",
            adjusted_qty=5.0, food_expiration_days=1
        )
        d = s._convert_prediction_result_to_dict(result)
        assert 'expiration_days' in d
        assert d['expiration_days'] == 1  # food_expiration_days에서 가져옴

    def test_expiration_days_fallback_to_product_detail(self):
        """food_expiration_days 없으면 product_detail.expiration_days 사용"""
        product_detail = {"expiration_days": 2, "order_unit_qty": 1}
        s = self._make_system(product_detail=product_detail)
        result = self._make_result(
            item_cd="TEST004", mid_cd="003",
            adjusted_qty=2.0, food_expiration_days=None
        )
        d = s._convert_prediction_result_to_dict(result)
        assert d['expiration_days'] == 2  # product_detail에서 가져옴

    def test_expiration_days_fallback_to_category(self):
        """food_expiration_days, product_detail 모두 없으면 CATEGORY_EXPIRY_DAYS 사용"""
        s = self._make_system(product_detail={})  # expiration_days 없음
        result = self._make_result(
            item_cd="TEST005", mid_cd="001",  # 도시락 → CATEGORY_EXPIRY_DAYS["001"]=1
            adjusted_qty=1.0, food_expiration_days=None
        )
        d = s._convert_prediction_result_to_dict(result)
        assert d['expiration_days'] == 1  # CATEGORY_EXPIRY_DAYS["001"]=1
