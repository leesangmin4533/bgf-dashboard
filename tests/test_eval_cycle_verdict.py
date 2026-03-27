"""
eval-cycle-verdict: NORMAL_ORDER 판정 기준 개선 테스트

- 푸드류 제외 (기존 로직 유지)
- 저회전(daily_avg < 1.0): 판매주기 기반 판정
- 고회전(daily_avg >= 1.0): 재고 유지 기반 판정
- 최소 진열: 행사(1+1→2, 2+1→3) / 비행사(2개)
"""

import pytest
from unittest.mock import MagicMock, patch


# _judge_normal_order를 직접 테스트하기 위한 최소 EvalCalibrator 셋업
@pytest.fixture
def calibrator():
    """EvalCalibrator 인스턴스 (DB 미연결)"""
    from src.prediction.eval_calibrator import EvalCalibrator

    cal = EvalCalibrator.__new__(EvalCalibrator)
    cal.config = MagicMock()
    cal.store_id = None
    cal.outcome_repo = MagicMock()
    cal.calibration_repo = MagicMock()
    cal._sales_repo = MagicMock()
    return cal


def _make_record(mid_cd="047", daily_avg=1.5, promo_type=None,
                 eval_date="2026-03-01", item_cd="TEST001", **kwargs):
    """테스트용 eval_outcomes record 생성"""
    rec = {
        "item_cd": item_cd,
        "mid_cd": mid_cd,
        "daily_avg": daily_avg,
        "promo_type": promo_type,
        "eval_date": eval_date,
        "current_stock": kwargs.get("current_stock", 5),
        "pending_qty": kwargs.get("pending_qty", 0),
        "exposure_days": kwargs.get("exposure_days", 1.5),
        "popularity_score": kwargs.get("popularity_score", 0.5),
        "stockout_freq": kwargs.get("stockout_freq", 0.0),
    }
    rec.update(kwargs)
    return rec


# ============================================================
# 1. 푸드류 제외 (기존 로직 유지)
# ============================================================

class TestFoodExclusion:
    """푸드류(001~005, 012)는 기존 판정 로직 유지"""

    def test_food_dosirak_sold_correct(self, calibrator):
        """도시락(001) 판매 있으면 적중"""
        record = _make_record(mid_cd="001", daily_avg=0.3)
        assert calibrator._judge_normal_order(2, 3, False, record) == "CORRECT"

    def test_food_dosirak_no_sale_over(self, calibrator):
        """도시락(001) 판매 없고 재고 있으면 과잉"""
        record = _make_record(mid_cd="001", daily_avg=0.3)
        assert calibrator._judge_normal_order(0, 3, False, record) == "OVER_ORDER"

    def test_food_dosirak_no_sale_stockout_under(self, calibrator):
        """도시락(001) 판매 없고 품절이면 과소"""
        record = _make_record(mid_cd="001", daily_avg=0.3)
        assert calibrator._judge_normal_order(0, 0, True, record) == "UNDER_ORDER"

    def test_food_gimbap_sold(self, calibrator):
        """김밥(003) 판매 있으면 적중"""
        record = _make_record(mid_cd="003", daily_avg=0.1)
        assert calibrator._judge_normal_order(1, 0, True, record) == "CORRECT"

    def test_food_bread_no_sale(self, calibrator):
        """빵(012) 판매 없고 재고 있으면 과잉"""
        record = _make_record(mid_cd="012", daily_avg=0.5)
        assert calibrator._judge_normal_order(0, 5, False, record) == "OVER_ORDER"

    def test_food_sandwich(self, calibrator):
        """샌드위치(004) 기존 로직"""
        record = _make_record(mid_cd="004", daily_avg=0.2)
        assert calibrator._judge_normal_order(0, 0, True, record) == "UNDER_ORDER"

    def test_food_hamburger(self, calibrator):
        """햄버거(005) 기존 로직"""
        record = _make_record(mid_cd="005", daily_avg=0.1)
        assert calibrator._judge_normal_order(0, 2, False, record) == "OVER_ORDER"


# ============================================================
# 2. 저회전 상품 (daily_avg < 1.0) — 판매주기 기반
# ============================================================

class TestLowTurnoverCycleVerdict:
    """저회전 상품: 판매주기 내 1회 판매 여부로 판정"""

    def test_low_turnover_sold_today_correct(self, calibrator):
        """당일 판매 있으면 주기 무관 적중"""
        record = _make_record(mid_cd="047", daily_avg=0.5)
        assert calibrator._judge_normal_order(1, 3, False, record) == "CORRECT"

    def test_low_turnover_cycle_2d_recent_sale(self, calibrator):
        """avg=0.5 → 2일주기, 주기 내 판매 있으면 적중"""
        record = _make_record(mid_cd="047", daily_avg=0.5, eval_date="2026-03-02")
        # 최근 2일 내 판매 합계 = 1
        calibrator.outcome_repo.get_by_item_date_range.return_value = [
            {"actual_sold_qty": 0, "eval_date": "2026-03-01"},
            {"actual_sold_qty": 1, "eval_date": "2026-03-02"},
        ]
        # 당일 판매=0이지만 주기 내 판매 있음
        # actual_sold=0 → 주기 룩백 → sum=1 → CORRECT
        result = calibrator._judge_normal_order(0, 3, False, record)
        assert result == "CORRECT"

    def test_low_turnover_cycle_3d_no_sale_stockout(self, calibrator):
        """avg=0.33 → 3일주기, 3일 내 미판매 + 품절 → UNDER"""
        record = _make_record(mid_cd="020", daily_avg=0.33, eval_date="2026-03-03")
        calibrator.outcome_repo.get_by_item_date_range.return_value = [
            {"actual_sold_qty": 0},
            {"actual_sold_qty": 0},
            {"actual_sold_qty": 0},
        ]
        result = calibrator._judge_normal_order(0, 0, True, record)
        assert result == "UNDER_ORDER"

    def test_low_turnover_cycle_no_sale_stock_ok(self, calibrator):
        """주기 내 미판매 + 재고 충분 → OVER"""
        record = _make_record(mid_cd="035", daily_avg=0.5, eval_date="2026-03-02")
        calibrator.outcome_repo.get_by_item_date_range.return_value = [
            {"actual_sold_qty": 0},
            {"actual_sold_qty": 0},
        ]
        result = calibrator._judge_normal_order(0, 5, False, record)
        assert result == "OVER_ORDER"

    def test_low_turnover_cycle_capped_7days(self, calibrator):
        """avg=0.1 → cycle=10 → capped to 7일"""
        record = _make_record(mid_cd="035", daily_avg=0.1, eval_date="2026-03-07")
        calibrator.outcome_repo.get_by_item_date_range.return_value = [
            {"actual_sold_qty": 0} for _ in range(7)
        ]
        result = calibrator._judge_normal_order(0, 3, False, record)
        # 7일 룩백으로 capped, 판매 없음 + 재고 있음 → OVER
        assert result == "OVER_ORDER"

    def test_low_turnover_no_eval_date_fallback(self, calibrator):
        """eval_date 없으면 당일 판매만으로 판정"""
        record = _make_record(mid_cd="047", daily_avg=0.5, eval_date="")
        result = calibrator._judge_normal_order(0, 3, False, record)
        # eval_date 없으면 주기 룩백 스킵 → 판매=0 + 재고 있음 → OVER
        assert result == "OVER_ORDER"


# ============================================================
# 3. 고회전 상품 (daily_avg >= 1.0) — 안전재고 기반
# ============================================================

class TestHighTurnoverVerdict:
    """고회전 상품: 품절 여부로 판정"""

    def test_high_turnover_stock_ok_correct(self, calibrator):
        """재고 충분 → 적중"""
        record = _make_record(mid_cd="047", daily_avg=3.0)
        assert calibrator._judge_normal_order(2, 5, False, record) == "CORRECT"

    def test_high_turnover_stockout_under(self, calibrator):
        """품절 발생 → 과소"""
        record = _make_record(mid_cd="015", daily_avg=2.0)
        assert calibrator._judge_normal_order(3, 0, True, record) == "UNDER_ORDER"

    def test_high_turnover_low_stock_not_stockout(self, calibrator):
        """재고 적지만 품절 아님 → 적중"""
        record = _make_record(mid_cd="032", daily_avg=5.0)
        assert calibrator._judge_normal_order(0, 2, False, record) == "CORRECT"

    def test_high_turnover_sold_and_stock(self, calibrator):
        """판매 있고 재고 유지 → 적중"""
        record = _make_record(mid_cd="016", daily_avg=1.5)
        assert calibrator._judge_normal_order(3, 4, False, record) == "CORRECT"


# ============================================================
# 4. 최소 진열 기준
# ============================================================

class TestMinDisplayQty:
    """최소 진열 수량 판정"""

    def test_promo_1plus1_stock_ok(self, calibrator):
        """1+1 행사 + 재고 3개 → 진열 충족"""
        record = _make_record(mid_cd="047", daily_avg=1.5, promo_type="1+1")
        result = calibrator._judge_normal_order(0, 3, False, record)
        assert result == "CORRECT"

    def test_promo_1plus1_stock_1_under(self, calibrator):
        """1+1 행사 + 재고 1개 + 품절 → UNDER"""
        record = _make_record(mid_cd="047", daily_avg=1.5, promo_type="1+1")
        result = calibrator._judge_normal_order(0, 0, True, record)
        assert result == "UNDER_ORDER"

    def test_promo_2plus1_stock_ok(self, calibrator):
        """2+1 행사 + 재고 4개 → 진열 충족"""
        record = _make_record(mid_cd="015", daily_avg=2.0, promo_type="2+1")
        result = calibrator._judge_normal_order(0, 4, False, record)
        assert result == "CORRECT"

    def test_promo_2plus1_stock_2_under(self, calibrator):
        """2+1 행사 + 재고 2개(< 3) + 품절 → UNDER"""
        record = _make_record(mid_cd="015", daily_avg=2.0, promo_type="2+1")
        result = calibrator._judge_normal_order(0, 0, True, record)
        assert result == "UNDER_ORDER"

    def test_no_promo_stock_1_under(self, calibrator):
        """비행사 + 재고 1개(< 2) + 품절 → UNDER"""
        record = _make_record(mid_cd="047", daily_avg=1.5, promo_type=None)
        result = calibrator._judge_normal_order(0, 0, True, record)
        assert result == "UNDER_ORDER"

    def test_no_promo_stock_ok(self, calibrator):
        """비행사 + 재고 3개 → 진열 충족"""
        record = _make_record(mid_cd="047", daily_avg=1.5, promo_type=None)
        result = calibrator._judge_normal_order(0, 3, False, record)
        assert result == "CORRECT"

    def test_get_min_display_qty_promo(self, calibrator):
        """_get_min_display_qty 행사 1+1 → 2"""
        record = _make_record(promo_type="1+1")
        assert calibrator._get_min_display_qty(record) == 2

    def test_get_min_display_qty_promo_2plus1(self, calibrator):
        """_get_min_display_qty 행사 2+1 → 3"""
        record = _make_record(promo_type="2+1")
        assert calibrator._get_min_display_qty(record) == 3

    def test_get_min_display_qty_no_promo(self, calibrator):
        """_get_min_display_qty 비행사 → 2"""
        record = _make_record(promo_type=None)
        assert calibrator._get_min_display_qty(record) == 2

    def test_promo_1plus1_stock_1_no_stockout(self, calibrator):
        """1+1 행사 + 재고 1개(< 2) + 품절 아님 → 고회전 CORRECT (재고 유지)"""
        record = _make_record(mid_cd="047", daily_avg=1.5, promo_type="1+1")
        # stock=1 < min_display=2이지만 was_stockout=False이고 stock>0
        # → min_display 분기 통과 → 고회전 판정 → 품절 아님 = CORRECT
        result = calibrator._judge_normal_order(0, 1, False, record)
        assert result == "CORRECT"

    def test_daily_avg_exactly_1(self, calibrator):
        """daily_avg=1.0 → 고회전 기준 경계값, 품절 없으면 CORRECT"""
        record = _make_record(mid_cd="047", daily_avg=1.0)
        assert calibrator._judge_normal_order(0, 3, False, record) == "CORRECT"

    def test_daily_avg_just_below_1(self, calibrator):
        """daily_avg=0.99 → 저회전 기준, 주기 판정"""
        record = _make_record(mid_cd="047", daily_avg=0.99, eval_date="2026-03-02")
        calibrator.outcome_repo.get_by_item_date_range.return_value = [
            {"actual_sold_qty": 1},
            {"actual_sold_qty": 0},
        ]
        result = calibrator._judge_normal_order(0, 3, False, record)
        assert result == "CORRECT"


# ============================================================
# 5. 엣지 케이스
# ============================================================

class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_daily_avg_zero_stockout(self, calibrator):
        """daily_avg=0 + 품절 → UNDER (폴백)"""
        record = _make_record(mid_cd="047", daily_avg=0)
        assert calibrator._judge_normal_order(0, 0, True, record) == "UNDER_ORDER"

    def test_daily_avg_zero_stock_ok(self, calibrator):
        """daily_avg=0 + 재고 → OVER (폴백)"""
        record = _make_record(mid_cd="047", daily_avg=0)
        assert calibrator._judge_normal_order(0, 5, False, record) == "OVER_ORDER"

    def test_daily_avg_none(self, calibrator):
        """daily_avg=None → 0 취급"""
        record = _make_record(mid_cd="047", daily_avg=None)
        assert calibrator._judge_normal_order(0, 5, False, record) == "OVER_ORDER"

    def test_judge_outcome_delegates_to_normal(self, calibrator):
        """_judge_outcome에서 NORMAL_ORDER가 _judge_normal_order로 위임"""
        record = _make_record(mid_cd="047", daily_avg=2.0)
        result = calibrator._judge_outcome(
            "NORMAL_ORDER", 1, 5, False, record
        )
        assert result == "CORRECT"

    def test_recent_sales_sum_db_error(self, calibrator):
        """DB 에러 시 _get_recent_sales_sum → 0 반환"""
        calibrator.outcome_repo.get_by_item_date_range.side_effect = Exception("DB error")
        result = calibrator._get_recent_sales_sum("TEST001", "2026-03-02", 2)
        assert result == 0

    def test_low_turnover_with_promo_stock_low(self, calibrator):
        """저회전 + 1+1 행사 + 재고 부족 → UNDER"""
        record = _make_record(mid_cd="019", daily_avg=0.5, promo_type="1+1")
        result = calibrator._judge_normal_order(0, 0, True, record)
        assert result == "UNDER_ORDER"
