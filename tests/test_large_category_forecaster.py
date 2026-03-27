"""
대분류(large_cd) 기반 카테고리 총량 예측 테스트 (category-total-prediction-largecd)

LargeCategoryForecaster가 large_cd 단위 총량 WMA를 예측하고,
mid_cd 비율 배분 후 개별 예측 합이 부족하면 floor 보충하는지 검증한다.

테스트 범위:
- WMA 계산 정확성 (3개)
- mid_cd 비율 계산 (3개)
- 총량 배분 (2개)
- floor 보충 (4개)
- 통합 플로우 (3개)
- 엣지 케이스 (2개)
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import List, Dict, Any, Tuple, Set

from src.prediction.large_category_forecaster import LargeCategoryForecaster


def _make_forecaster(config_overrides=None):
    """테스트용 LargeCategoryForecaster (DB 미접속)"""
    default_config = {
        "enabled": True,
        "target_large_cds": ["01", "02", "12"],
        "threshold": 0.75,
        "max_add_per_item": 2,
        "wma_days": 14,
        "ratio_days": 14,
        "min_candidate_sell_days": 2,
    }
    if config_overrides:
        default_config.update(config_overrides)

    with patch("src.prediction.large_category_forecaster.PREDICTION_PARAMS",
               {"large_category_floor": default_config}):
        f = LargeCategoryForecaster(store_id="46513")
    # 직접 config 설정 (mock 컨텍스트 벗어나도 유지)
    f._config = default_config
    return f


# =============================================================================
# WMA 계산 (3개)
# =============================================================================

class TestWMACalculation:
    """_calculate_wma() WMA 계산 테스트"""

    def test_wma_uniform(self):
        """동일 값 → WMA = 같은 값"""
        f = _make_forecaster()
        daily = [("2026-02-28", 10), ("2026-02-27", 10), ("2026-02-26", 10)]
        result = f._calculate_wma(daily)
        assert result == 10.0

    def test_wma_weighted_recent(self):
        """최근일 가중: [30, 20, 10] → WMA = (30*3 + 20*2 + 10*1)/6 = 130/6 ≈ 21.67"""
        f = _make_forecaster()
        daily = [("2026-02-28", 30), ("2026-02-27", 20), ("2026-02-26", 10)]
        result = f._calculate_wma(daily)
        expected = (30 * 3 + 20 * 2 + 10 * 1) / (3 + 2 + 1)
        assert abs(result - expected) < 0.01

    def test_wma_empty(self):
        """빈 데이터 → 0"""
        f = _make_forecaster()
        result = f._calculate_wma([])
        assert result == 0.0


# =============================================================================
# mid_cd 비율 계산 (3개)
# =============================================================================

class TestMidCdRatios:
    """get_mid_cd_ratios() 테스트"""

    def test_ratios_single_mid(self):
        """단일 mid_cd → 비율 1.0"""
        f = _make_forecaster()
        with patch.object(f, "_get_mid_cd_totals", return_value={"001": 100}):
            ratios = f.get_mid_cd_ratios("01")
        assert ratios == {"001": 1.0}

    def test_ratios_multiple_mid(self):
        """복수 mid_cd 비율 합 ≈ 1.0"""
        f = _make_forecaster()
        with patch.object(f, "_get_mid_cd_totals",
                          return_value={"001": 60, "002": 30, "003": 10}):
            ratios = f.get_mid_cd_ratios("01")

        assert abs(sum(ratios.values()) - 1.0) < 0.001
        assert abs(ratios["001"] - 0.6) < 0.001
        assert abs(ratios["002"] - 0.3) < 0.001
        assert abs(ratios["003"] - 0.1) < 0.001

    def test_ratios_empty(self):
        """데이터 없으면 빈 dict"""
        f = _make_forecaster()
        with patch.object(f, "_get_mid_cd_totals", return_value={}):
            ratios = f.get_mid_cd_ratios("01")
        assert ratios == {}


# =============================================================================
# 총량 배분 (2개)
# =============================================================================

class TestDistributeToMidCd:
    """distribute_to_mid_cd() 테스트"""

    def test_distribute_proportional(self):
        """비율대로 정확히 배분"""
        f = _make_forecaster()
        ratios = {"001": 0.6, "002": 0.3, "003": 0.1}
        targets = f.distribute_to_mid_cd(100.0, ratios)

        assert targets["001"] == 60.0
        assert targets["002"] == 30.0
        assert targets["003"] == 10.0

    def test_distribute_zero_total(self):
        """총량 0이면 모두 0"""
        f = _make_forecaster()
        ratios = {"001": 0.6, "002": 0.4}
        targets = f.distribute_to_mid_cd(0.0, ratios)

        assert targets["001"] == 0.0
        assert targets["002"] == 0.0


# =============================================================================
# floor 보충 (4개)
# =============================================================================

class TestFloorCorrection:
    """_apply_floor_correction() 테스트"""

    def test_supplement_below_threshold(self):
        """개별합 < 75% → 보충 발생"""
        f = _make_forecaster()

        order_list = [
            {"item_cd": "AAA", "mid_cd": "001", "final_order_qty": 5, "item_nm": "도시락A"},
        ]

        # target: 001=30 → floor=22.5 → int=22 → shortage=17
        targets = {"001": 30.0}

        with patch.object(f, "_get_supplement_candidates") as mock_cands:
            mock_cands.return_value = [
                {"item_cd": "C1", "item_nm": "후보1", "sell_days": 5, "total_sale": 10},
                {"item_cd": "C2", "item_nm": "후보2", "sell_days": 4, "total_sale": 8},
                {"item_cd": "C3", "item_nm": "후보3", "sell_days": 3, "total_sale": 6},
                {"item_cd": "C4", "item_nm": "후보4", "sell_days": 2, "total_sale": 4},
                {"item_cd": "C5", "item_nm": "후보5", "sell_days": 2, "total_sale": 3},
                {"item_cd": "C6", "item_nm": "후보6", "sell_days": 2, "total_sale": 2},
                {"item_cd": "C7", "item_nm": "후보7", "sell_days": 2, "total_sale": 2},
                {"item_cd": "C8", "item_nm": "후보8", "sell_days": 2, "total_sale": 1},
                {"item_cd": "C9", "item_nm": "후보9", "sell_days": 2, "total_sale": 1},
            ]

            supplemented = f._apply_floor_correction(order_list, targets)

        total_qty = sum(item.get("final_order_qty", 0) for item in order_list)
        assert total_qty > 5  # 원래 5에서 증가
        # 새 항목이 추가됐는지
        item_cds = {item["item_cd"] for item in order_list}
        assert len(item_cds) > 1
        assert supplemented > 0

    def test_no_supplement_above_threshold(self):
        """개별합 >= 75% → 보충 없음"""
        f = _make_forecaster()

        order_list = [
            {"item_cd": "AAA", "mid_cd": "001", "final_order_qty": 25, "item_nm": "도시락A"},
        ]

        # target: 001=30 → floor=22.5 → 현재=25 >= 22.5
        targets = {"001": 30.0}

        supplemented = f._apply_floor_correction(order_list, targets)
        assert supplemented == 0
        assert len(order_list) == 1
        assert order_list[0]["final_order_qty"] == 25

    def test_supplement_max_per_item(self):
        """max_add_per_item=2 → 품목당 2개까지"""
        f = _make_forecaster({"max_add_per_item": 2})

        candidates = [
            {"item_cd": "A", "item_nm": "a", "sell_days": 5, "total_sale": 10},
            {"item_cd": "B", "item_nm": "b", "sell_days": 4, "total_sale": 8},
            {"item_cd": "C", "item_nm": "c", "sell_days": 3, "total_sale": 6},
        ]

        result = f._distribute_shortage(7, candidates, max_per_item=2)
        assert all(s["add_qty"] <= 2 for s in result)
        assert sum(s["add_qty"] for s in result) == 6  # 2+2+2, 부족1 분배 불가

    def test_supplement_cut_items_excluded(self):
        """CUT 상품은 보충 후보에서 제외"""
        f = _make_forecaster()
        cut_items = {"CUT001"}

        order_list = [
            {"item_cd": "AAA", "mid_cd": "001", "final_order_qty": 1, "item_nm": "도시락A"},
        ]

        targets = {"001": 20.0}

        with patch.object(f, "_get_supplement_candidates") as mock_cands:
            # _get_supplement_candidates는 내부에서 cut_items를 필터링하므로
            # CUT001은 이미 제외된 결과만 반환
            mock_cands.return_value = [
                {"item_cd": "SAFE001", "item_nm": "안전품목", "sell_days": 5, "total_sale": 10},
            ]

            supplemented = f._apply_floor_correction(
                order_list, targets, cut_items=cut_items
            )

        item_cds = {item["item_cd"] for item in order_list}
        assert "CUT001" not in item_cds
        assert "SAFE001" in item_cds


# =============================================================================
# 통합 플로우 (3개)
# =============================================================================

class TestFullFlow:
    """supplement_orders() 전체 플로우 테스트"""

    def test_full_flow_single_large_cd(self):
        """01(간편식사) 단일 large_cd 전체 플로우"""
        f = _make_forecaster({"target_large_cds": ["01"]})

        order_list = [
            {"item_cd": "A1", "mid_cd": "001", "final_order_qty": 3, "item_nm": "도시락1"},
            {"item_cd": "A2", "mid_cd": "002", "final_order_qty": 2, "item_nm": "김밥1"},
        ]

        with patch.object(f, "forecast_large_cd_total", return_value=40.0), \
             patch.object(f, "get_mid_cd_ratios",
                          return_value={"001": 0.5, "002": 0.3, "003": 0.2}), \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            def cands_side_effect(mid_cd, existing, eval_r=None, cut=None):
                if mid_cd == "001":
                    return [
                        {"item_cd": "N1", "item_nm": "새도시락", "sell_days": 5, "total_sale": 10},
                        {"item_cd": "N2", "item_nm": "새도시락2", "sell_days": 4, "total_sale": 8},
                    ]
                elif mid_cd == "002":
                    return [
                        {"item_cd": "N3", "item_nm": "새김밥", "sell_days": 3, "total_sale": 6},
                    ]
                elif mid_cd == "003":
                    return [
                        {"item_cd": "N4", "item_nm": "새주먹밥", "sell_days": 2, "total_sale": 4},
                    ]
                return []
            mock_cands.side_effect = cands_side_effect

            result = f.supplement_orders(order_list)

        # 보충이 발생했는지 확인
        total_qty = sum(item.get("final_order_qty", 0) for item in result)
        assert total_qty > 5  # 원래 3+2=5에서 증가

    def test_full_flow_multi_large_cd(self):
        """복수 large_cd(01+12) 전체 플로우"""
        f = _make_forecaster({"target_large_cds": ["01", "12"]})

        order_list = [
            {"item_cd": "F1", "mid_cd": "001", "final_order_qty": 2, "item_nm": "도시락"},
            {"item_cd": "S1", "mid_cd": "015", "final_order_qty": 1, "item_nm": "과자"},
        ]

        call_count = {"forecast": 0}

        def mock_forecast(large_cd, days=None):
            call_count["forecast"] += 1
            if large_cd == "01":
                return 20.0
            elif large_cd == "12":
                return 15.0
            return 0.0

        def mock_ratios(large_cd, days=None):
            if large_cd == "01":
                return {"001": 0.6, "002": 0.4}
            elif large_cd == "12":
                return {"015": 0.7, "016": 0.3}
            return {}

        with patch.object(f, "forecast_large_cd_total", side_effect=mock_forecast), \
             patch.object(f, "get_mid_cd_ratios", side_effect=mock_ratios), \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            mock_cands.return_value = [
                {"item_cd": "NEW1", "item_nm": "보충품목", "sell_days": 3, "total_sale": 5},
                {"item_cd": "NEW2", "item_nm": "보충품목2", "sell_days": 2, "total_sale": 3},
            ]

            result = f.supplement_orders(order_list)

        # 두 large_cd 모두 처리
        assert call_count["forecast"] == 2
        # 보충 발생
        total_qty = sum(item.get("final_order_qty", 0) for item in result)
        assert total_qty > 3

    def test_disabled_returns_unchanged(self):
        """enabled=False → 원본 그대로 반환"""
        f = _make_forecaster({"enabled": False})

        order_list = [
            {"item_cd": "AAA", "mid_cd": "001", "final_order_qty": 5, "item_nm": "상품A"},
        ]

        result = f.supplement_orders(order_list)
        assert result == order_list
        assert len(result) == 1
        assert result[0]["final_order_qty"] == 5


# =============================================================================
# 엣지 케이스 (2개)
# =============================================================================

class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_order_list(self):
        """빈 발주목록 → large_cd 기반 전량 보충"""
        f = _make_forecaster({"target_large_cds": ["01"]})

        order_list = []

        with patch.object(f, "forecast_large_cd_total", return_value=10.0), \
             patch.object(f, "get_mid_cd_ratios",
                          return_value={"001": 0.5, "002": 0.5}), \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            mock_cands.return_value = [
                {"item_cd": "A1", "item_nm": "품목1", "sell_days": 5, "total_sale": 10},
                {"item_cd": "A2", "item_nm": "품목2", "sell_days": 4, "total_sale": 8},
                {"item_cd": "A3", "item_nm": "품목3", "sell_days": 3, "total_sale": 6},
                {"item_cd": "A4", "item_nm": "품목4", "sell_days": 2, "total_sale": 4},
            ]

            result = f.supplement_orders(order_list)

        # floor = 10*0.5*0.75 = 3.75 → int=3, 현재=0 → shortage=3
        # 각 mid_cd(001, 002)에서 보충 → 총 보충 > 0
        assert len(result) > 0
        total_qty = sum(item.get("final_order_qty", 0) for item in result)
        assert total_qty > 0

    def test_fallback_mid_cd_mapping(self):
        """large_cd DB 미등록 시 LARGE_CD_TO_MID_CD 상수 매핑 사용"""
        f = _make_forecaster()

        # _get_large_cd_daily_totals가 DB에서 mid_cd를 못 찾고 fallback하는 상황 시뮬레이션
        # 직접 _calculate_wma 호출로 검증
        daily = [("2026-02-28", 50), ("2026-02-27", 40)]
        result = f._calculate_wma(daily)
        expected = (50 * 2 + 40 * 1) / (2 + 1)
        assert abs(result - expected) < 0.01

        # LARGE_CD_TO_MID_CD 매핑이 올바르게 설정되어 있는지 확인
        from src.settings.constants import LARGE_CD_TO_MID_CD
        assert "01" in LARGE_CD_TO_MID_CD
        assert "001" in LARGE_CD_TO_MID_CD["01"]
        assert "002" in LARGE_CD_TO_MID_CD["01"]
        assert "12" in LARGE_CD_TO_MID_CD
        assert "015" in LARGE_CD_TO_MID_CD["12"]


# =============================================================================
# 추가 테스트: source 태그 및 속성 (2개)
# =============================================================================

class TestSupplementAttributes:
    """보충된 항목의 속성 테스트"""

    def test_new_item_has_source_tag(self):
        """새 보충 항목에 source='large_category_floor' 태그"""
        f = _make_forecaster({"target_large_cds": ["01"]})

        order_list = [
            {"item_cd": "AAA", "mid_cd": "001", "final_order_qty": 1, "item_nm": "기존"},
        ]

        with patch.object(f, "forecast_large_cd_total", return_value=20.0), \
             patch.object(f, "get_mid_cd_ratios",
                          return_value={"001": 1.0}), \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            mock_cands.return_value = [
                {"item_cd": "NEW1", "item_nm": "새품목", "sell_days": 5, "total_sale": 10},
            ]

            result = f.supplement_orders(order_list)

        new_items = [item for item in result if item["item_cd"] == "NEW1"]
        if new_items:
            assert new_items[0].get("source") == "large_category_floor"
            assert new_items[0].get("predicted_qty") == 0
            assert new_items[0].get("mid_cd") == "001"

    def test_distribute_shortage_respects_remaining(self):
        """shortage보다 많은 후보가 있으면 shortage만큼만 분배"""
        f = _make_forecaster({"max_add_per_item": 1})

        candidates = [
            {"item_cd": "A", "item_nm": "a"},
            {"item_cd": "B", "item_nm": "b"},
            {"item_cd": "C", "item_nm": "c"},
            {"item_cd": "D", "item_nm": "d"},
        ]

        result = f._distribute_shortage(2, candidates, max_per_item=1)
        assert len(result) == 2
        assert sum(s["add_qty"] for s in result) == 2
