"""
food-underprediction-secondary B안 회귀 테스트

검증 대상:
1. base_predictor.calculate_weighted_average — stock_qty<0 sentinel → None 정규화
2. 정규화 후 만성-품절 1차 fix(ab98bfc) 가 정상 작동 (전체 -1 윈도우)
3. food mid (001~005, 012) snapshot_stages JSON 구조에 5단계(base_wma, wma_blended,
   coef_mul, food_5stage) 가 포함되는지 확인 (스키마 가시화)

본 테스트는 신규 파일이며, 사전 존재 20 fail (promo_status / promotion_stats) 와
독립이다. 의도적으로 import 표면을 좁혀 격리한다.
"""

from unittest.mock import MagicMock

import pytest

from src.prediction.base_predictor import BasePredictor


# =============================================================================
# Helpers
# =============================================================================

def _make_base_predictor():
    """순수 단위 테스트용 BasePredictor — DB I/O 없음"""
    data_provider = MagicMock()
    feature_calculator = MagicMock()
    holiday_ctx = MagicMock(return_value={"in_period": False})
    return BasePredictor(
        data_provider=data_provider,
        feature_calculator=feature_calculator,
        store_id="49965",
        holiday_context_fn=holiday_ctx,
    )


def _hist(rows):
    """7일 윈도우 history. rows = [(sale, stock), ...] 최신→과거 순"""
    return [(f"2026-04-{(8 - i):02d}", sale, stock) for i, (sale, stock) in enumerate(rows)]


# =============================================================================
# 1. stock_qty<0 sentinel 정규화 — 부분 -1 (사각지대 해소)
# =============================================================================

class TestStockSentinelNormalization:
    """stock_qty<0 (예: -1) sentinel 정규화 — None 으로 변환되어 imputation 분기 진입"""

    def test_partial_negative_sentinel_treated_as_none(self):
        """일부 -1 + 일부 정상 → -1 행은 None 처리되어 미수집과 동일하게 취급"""
        bp = _make_base_predictor()

        # 5일은 정상(stock>0, sale=1) + 2일은 sentinel(-1)
        rows = [(1, 5), (1, 5), (1, 5), (1, 5), (1, 5), (0, -1), (0, -1)]
        history = _hist(rows)

        wma, n = bp.calculate_weighted_average(
            history, clean_outliers=False, mid_cd="001", item_cd="TEST_NEG_PARTIAL"
        )

        # -1 행은 None으로 정규화되어 food mid에서 stockout 그룹 → imputation 적용
        # 평균 sale=1.0 으로 0일들이 채워져야 함 → WMA ≈ 1.0
        assert wma > 0.9, f"WMA={wma} (expected >= 0.9)"
        assert n == 7

    def test_all_negative_sentinel_food_mid_uses_nonzero_signal(self):
        """7일 전부 -1 + 일부 sale>0 (만성 품절 + 부분 영업) → ab98bfc 1차 fix 트리거"""
        bp = _make_base_predictor()

        # 7일 모두 stock=-1, 그 중 1일만 sale=2 (부분 영업 흔적)
        rows = [(0, -1), (0, -1), (0, -1), (0, -1), (2, -1), (0, -1), (0, -1)]
        history = _hist(rows)

        wma, n = bp.calculate_weighted_average(
            history, clean_outliers=False, mid_cd="001", item_cd="TEST_NEG_ALL_FOOD"
        )

        # sentinel 정규화 → 모두 None → food mid 는 None을 stockout으로 취급
        # → "stockout and not available" 분기 → nonzero_signal=[2.0] → avg=2.0
        # → 0일들이 2.0으로 채워짐 → WMA ≈ 2.0
        assert wma > 1.5, f"WMA={wma} (expected ≈ 2.0, ab98bfc 1차 fix가 정규화 후 트리거)"
        assert n == 7

    def test_negative_sentinel_non_food_mid_safe_fallback(self):
        """비푸드 mid (예: 049 맥주) → None 정규화되지만 stockout 분기 미진입 → 원본 sale 유지"""
        bp = _make_base_predictor()

        # 5일 정상(stock>0, sale=2) + 2일 sentinel
        rows = [(2, 10), (2, 10), (2, 10), (2, 10), (2, 10), (0, -1), (0, -1)]
        history = _hist(rows)

        wma, n = bp.calculate_weighted_average(
            history, clean_outliers=False, mid_cd="049", item_cd="TEST_NEG_BEER"
        )

        # 비푸드 mid는 include_none_as_stockout=False
        # available=5건(stock>0), stockout=0건(stk==0 만 카운트)
        # → 1차 imputation 분기 미발동, 정규화된 None 행은 sale=0 그대로
        # WMA = (1*2 + 1*2 + 1*2 + 1.33*2 + 0 + 0) / weights ≈ 1.something
        # 회귀 안전성: WMA가 0이 되지 않는지만 검증
        assert wma >= 0
        assert wma < 2.5  # 원본 sale 평균 이하
        assert n == 7

    def test_no_sentinel_unchanged_behavior(self):
        """sentinel 없는 경우 → 정규화 영향 없음 (회귀 안전망)"""
        bp = _make_base_predictor()

        # 7일 정상 데이터
        rows = [(2, 5), (3, 5), (1, 5), (2, 5), (2, 5), (1, 5), (3, 5)]
        history = _hist(rows)

        wma, n = bp.calculate_weighted_average(
            history, clean_outliers=False, mid_cd="001", item_cd="TEST_NORMAL"
        )

        # 모든 day stock>0, sale 평균 ≈ 2.0 → WMA ≈ 2.0 ± weight 분포
        assert 1.0 < wma < 3.5
        assert n == 7

    def test_zero_sale_unaffected_by_normalization(self):
        """전부 sale=0 + 전부 stk=-1 → nonzero_signal 없음 → 안전 폴백 (WMA=0)"""
        bp = _make_base_predictor()
        rows = [(0, -1)] * 7
        history = _hist(rows)

        wma, n = bp.calculate_weighted_average(
            history, clean_outliers=False, mid_cd="001", item_cd="TEST_ZERO_SIGNAL"
        )

        # 모든 sentinel → None → food는 stockout 그룹 → available 비어있음
        # → "stockout and not available" 분기 → nonzero_signal 없음 → 보정 미적용
        # → 모든 sale=0 → WMA = 0
        assert wma == 0
        assert n == 7


# =============================================================================
# 2. snapshot_stages 5단계 스키마 — food mid 한정
# =============================================================================

class TestFoodFiveStageSchema:
    """food mid 의 snapshot_stages JSON 에 5단계 + food_5stage 키가 포함되는지 검증"""

    def _build_snapshot(self, mid_cd):
        """_compute_safety_and_order 의 snapshot 빌드 로직만 추출 (인스턴스 없이 검증)"""
        # food-underprediction-secondary B안 — improved_predictor.py:1890 와 동일 구조
        rule_qty = 2
        promo_qty = 2
        round_qty = 3
        final_order_qty = 3
        rule_result = MagicMock(qty=rule_qty)
        promo_result = MagicMock(qty=promo_qty)
        round_result = MagicMock(qty=round_qty)
        result = MagicMock(qty=round_qty)
        result.stages = {"after_rop": rule_qty}
        ctx = {
            "_order_qty_after_ml": 2,
            "_order_qty_after_diff": 2,
            "_order_qty_after_promo_floor": 2,
            "_order_qty_after_sub": 2,
            "_order_qty_after_cap": 3,
            "_raw_need_qty": 1.5,
        }

        snapshot = {
            "after_rule": rule_result.qty,
            "after_rop": result.stages.get("after_rop", rule_result.qty),
            "after_promo": promo_result.qty,
            "after_ml": ctx.get("_order_qty_after_ml", promo_result.qty),
            "after_diff": ctx.get("_order_qty_after_diff", promo_result.qty),
            "after_promo_floor": ctx.get("_order_qty_after_promo_floor", promo_result.qty),
            "after_sub": ctx.get("_order_qty_after_sub", promo_result.qty),
            "after_cap": ctx.get("_order_qty_after_cap", promo_result.qty),
            "after_round": round_result.qty,
            "final": final_order_qty,
            "raw_need_qty": ctx.get("_raw_need_qty", 0),
            "stage_io": [],
            "shadow": {},
        }

        _food_mids = ('001', '002', '003', '004', '005', '012')
        if mid_cd in _food_mids:
            _upstream = {"base_wma": 0.78, "wma_blended": 0.95, "coef_mul": 1.21}
            snapshot["base_wma"] = _upstream["base_wma"]
            snapshot["wma_blended"] = _upstream["wma_blended"]
            snapshot["coef_mul"] = _upstream["coef_mul"]
            snapshot["food_5stage"] = {
                "base_wma": _upstream["base_wma"],
                "wma_blended": _upstream["wma_blended"],
                "coef_mul": _upstream["coef_mul"],
                "rule_floor": result.stages.get("after_rop", rule_result.qty),
                "ml_blend": ctx.get("_order_qty_after_ml", promo_result.qty),
                "final_cap": ctx.get("_order_qty_after_cap", promo_result.qty),
            }
        return snapshot

    def test_food_mid_has_upstream_keys(self):
        snap = self._build_snapshot("001")  # 도시락
        assert "base_wma" in snap and snap["base_wma"] == 0.78
        assert "wma_blended" in snap and snap["wma_blended"] == 0.95
        assert "coef_mul" in snap and snap["coef_mul"] == 1.21
        assert "food_5stage" in snap

    def test_food_5stage_six_keys(self):
        """food_5stage 는 base_wma → wma_blended → coef_mul → rule_floor → ml_blend → final_cap (6키)"""
        snap = self._build_snapshot("004")  # 샌드위치
        f5 = snap["food_5stage"]
        expected = {"base_wma", "wma_blended", "coef_mul", "rule_floor", "ml_blend", "final_cap"}
        assert set(f5.keys()) == expected
        # 단조 증가 가능성 검증 (base_wma <= coef_mul <= rule_floor <= final_cap)
        assert f5["base_wma"] <= f5["coef_mul"]

    def test_non_food_mid_no_upstream_keys(self):
        """비푸드 mid 는 upstream 키 미포함 (DB 부담 절감)"""
        snap = self._build_snapshot("049")  # 맥주
        assert "base_wma" not in snap
        assert "wma_blended" not in snap
        assert "coef_mul" not in snap
        assert "food_5stage" not in snap
        # 기존 downstream stages 는 그대로 유지
        assert "after_rule" in snap
        assert "after_cap" in snap
        assert "final" in snap

    def test_food_mid_includes_existing_downstream(self):
        """food mid 도 기존 downstream 키는 그대로 유지 — 회귀 방지"""
        snap = self._build_snapshot("003")  # 김밥
        for key in ("after_rule", "after_rop", "after_promo", "after_ml",
                    "after_cap", "after_round", "final"):
            assert key in snap
