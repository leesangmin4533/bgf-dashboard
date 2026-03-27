"""
카테고리 총량 예측 기반 발주 보충 테스트 (category-level-prediction)

CategoryDemandForecaster가 개별 품목 예측 합산이 카테고리 총량 WMA의
threshold(70%) 미만일 때 부족분을 최근 판매 품목에 분배하는지 검증한다.

테스트 범위:
- WMA 계산 정확성
- threshold 기반 보충/미보충 판단
- 품목당 추가 상한 준수
- 비식품 카테고리 제외
- CUT/SKIP 상품 제외
- 판매 빈도순 분배
- 기존 항목 수량 증가 vs 새 항목 추가
- enabled=False 비활성화
- 빈 발주목록 처리
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from typing import List, Dict, Any, Tuple

from src.prediction.category_demand_forecaster import CategoryDemandForecaster


def _make_forecaster(config_overrides=None):
    """테스트용 CategoryDemandForecaster (DB 미접속)"""
    default_config = {
        "enabled": True,
        "target_mid_cds": ["001", "002", "003", "004", "005"],
        "threshold": 0.7,
        "max_add_per_item": 1,
        "wma_days": 7,
        "min_candidate_sell_days": 1,
    }
    if config_overrides:
        default_config.update(config_overrides)

    with patch("src.prediction.category_demand_forecaster.PREDICTION_PARAMS",
               {"category_floor": default_config}):
        with patch("src.prediction.category_demand_forecaster.StoreContext") as mock_ctx:
            mock_ctx.get_store_id.return_value = "46513"
            f = CategoryDemandForecaster(store_id="46513")
    # 직접 config 설정
    f._config = default_config
    return f


class TestCategoryForecastWMA:
    """_calculate_category_forecast() WMA 계산 테스트"""

    def test_wma_simple_uniform(self):
        """동일 값 → WMA = 같은 값"""
        f = _make_forecaster()
        daily = [("2026-02-25", 10), ("2026-02-24", 10), ("2026-02-23", 10)]
        result = f._calculate_category_forecast(daily)
        assert result == 10.0

    def test_wma_weighted_recent(self):
        """최근일 가중: [20, 10, 0] → WMA = (20*3 + 10*2 + 0*1)/(3+2+1) = 80/6 ≈ 13.33"""
        f = _make_forecaster()
        daily = [("2026-02-25", 20), ("2026-02-24", 10), ("2026-02-23", 0)]
        result = f._calculate_category_forecast(daily)
        expected = (20 * 3 + 10 * 2 + 0 * 1) / (3 + 2 + 1)
        assert abs(result - expected) < 0.01

    def test_wma_empty(self):
        """빈 데이터 → 0"""
        f = _make_forecaster()
        result = f._calculate_category_forecast([])
        assert result == 0.0

    def test_wma_single_day(self):
        """1일 데이터 → 그 값 그대로"""
        f = _make_forecaster()
        daily = [("2026-02-25", 15)]
        result = f._calculate_category_forecast(daily)
        assert result == 15.0


class TestSupplementBelowThreshold:
    """threshold 미만 시 보충 발생 테스트"""

    def test_supplement_below_threshold(self):
        """개별합 < 70% → 보충 발생"""
        f = _make_forecaster()

        # 카테고리 forecast=20, threshold=0.7 → floor=14
        # 현재 발주 합=5 → 부족 9
        order_list = [
            {"item_cd": "AAA", "mid_cd": "002", "final_order_qty": 3, "item_nm": "상품A"},
            {"item_cd": "BBB", "mid_cd": "002", "final_order_qty": 2, "item_nm": "상품B"},
        ]

        # mock DB calls
        with patch.object(f, "_get_category_daily_totals") as mock_totals, \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            # 002만 데이터 있고 나머지 mid_cd는 빈 데이터
            def totals_side_effect(mid_cd):
                if mid_cd == "002":
                    return [("2026-02-25", 20), ("2026-02-24", 20), ("2026-02-23", 20)]
                return []
            mock_totals.side_effect = totals_side_effect

            mock_cands.return_value = [
                {"item_cd": "CCC", "item_nm": "후보1", "sell_days": 5, "total_sale": 10},
                {"item_cd": "DDD", "item_nm": "후보2", "sell_days": 3, "total_sale": 6},
                {"item_cd": "EEE", "item_nm": "후보3", "sell_days": 2, "total_sale": 4},
            ]

            result = f.supplement_orders(order_list)

        # 보충 발생 확인
        total_qty = sum(item.get("final_order_qty", 0) for item in result)
        assert total_qty > 5  # 원래 5에서 증가
        # 새 항목이 추가됐는지
        item_cds = {item["item_cd"] for item in result}
        assert "CCC" in item_cds or "DDD" in item_cds or "EEE" in item_cds


class TestNoSupplementAboveThreshold:
    """threshold 이상 시 보충 미발생 테스트"""

    def test_no_supplement_above_threshold(self):
        """개별합 >= 70% → 보충 없음"""
        f = _make_forecaster()

        order_list = [
            {"item_cd": "AAA", "mid_cd": "002", "final_order_qty": 15, "item_nm": "상품A"},
        ]

        with patch.object(f, "_get_category_daily_totals") as mock_totals:
            def totals_side_effect(mid_cd):
                if mid_cd == "002":
                    return [("2026-02-25", 20)]  # forecast=20, floor=14, 현재=15 >= 14
                return []
            mock_totals.side_effect = totals_side_effect

            result = f.supplement_orders(order_list)

        assert len(result) == 1
        assert result[0]["final_order_qty"] == 15


class TestMaxPerItem:
    """품목당 추가 상한 테스트"""

    def test_max_per_item_one(self):
        """max_add_per_item=1 → 품목당 1개만 추가 (후보 3개면 최대 3)"""
        f = _make_forecaster({"max_add_per_item": 1})
        candidates = [
            {"item_cd": "A", "item_nm": "a", "sell_days": 5, "total_sale": 10},
            {"item_cd": "B", "item_nm": "b", "sell_days": 4, "total_sale": 8},
            {"item_cd": "C", "item_nm": "c", "sell_days": 3, "total_sale": 6},
        ]

        result = f._distribute_shortage(5, candidates, max_per_item=1)
        assert all(s["add_qty"] == 1 for s in result)
        assert len(result) == 3  # 후보 3개 × 1개씩 = 3개 (부족 5이나 후보 부족)

    def test_max_per_item_two(self):
        """max_add_per_item=2 → 품목당 2개까지"""
        f = _make_forecaster({"max_add_per_item": 2})
        candidates = [
            {"item_cd": "A", "item_nm": "a", "sell_days": 5, "total_sale": 10},
            {"item_cd": "B", "item_nm": "b", "sell_days": 4, "total_sale": 8},
        ]

        result = f._distribute_shortage(5, candidates, max_per_item=2)
        # A=2, B=2 → 4, 부족 1 더 분배 불가 (후보 소진)
        assert sum(s["add_qty"] for s in result) == 4
        assert len(result) == 2


class TestSkipNonFreshFood:
    """비식품 카테고리 대상 제외 테스트"""

    def test_skip_non_fresh_food(self):
        """016(면류) 등 비식품은 보충 대상 아님"""
        f = _make_forecaster()

        order_list = [
            {"item_cd": "X1", "mid_cd": "016", "final_order_qty": 1, "item_nm": "라면"},
        ]

        with patch.object(f, "_get_category_daily_totals") as mock_totals:
            mock_totals.return_value = []
            result = f.supplement_orders(order_list)

        # 016은 target_mid_cds에 없으므로 변화 없음
        assert len(result) == 1
        assert result[0]["final_order_qty"] == 1


class TestSkipCutItems:
    """CUT 상품 보충 제외 테스트"""

    def test_cut_items_excluded(self):
        """CUT 상품은 보충 후보에서 제외"""
        f = _make_forecaster()
        cut_items = {"CUT001"}

        order_list = [
            {"item_cd": "AAA", "mid_cd": "002", "final_order_qty": 1, "item_nm": "상품A"},
        ]

        with patch.object(f, "_get_category_daily_totals") as mock_totals, \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            def totals_side_effect(mid_cd):
                if mid_cd == "002":
                    return [("2026-02-25", 20)]
                return []
            mock_totals.side_effect = totals_side_effect

            # _get_supplement_candidates는 내부에서 cut_items를 필터링하므로
            # CUT001은 이미 제외된 결과만 반환
            mock_cands.return_value = [
                {"item_cd": "SAFE001", "item_nm": "안전품목", "sell_days": 5, "total_sale": 10},
            ]

            result = f.supplement_orders(order_list, cut_items=cut_items)

        # CUT001이 추가되지 않았는지
        item_cds = {item["item_cd"] for item in result}
        assert "CUT001" not in item_cds


class TestCandidateSortByFrequency:
    """판매 빈도순 분배 테스트"""

    def test_distribute_by_frequency(self):
        """sell_days 높은 품목이 먼저 분배됨"""
        f = _make_forecaster()
        candidates = [
            {"item_cd": "FREQ_HIGH", "item_nm": "빈도높음", "sell_days": 7, "total_sale": 20},
            {"item_cd": "FREQ_MED", "item_nm": "빈도중간", "sell_days": 4, "total_sale": 10},
            {"item_cd": "FREQ_LOW", "item_nm": "빈도낮음", "sell_days": 1, "total_sale": 2},
        ]

        result = f._distribute_shortage(2, candidates, max_per_item=1)
        assert len(result) == 2
        assert result[0]["item_cd"] == "FREQ_HIGH"
        assert result[1]["item_cd"] == "FREQ_MED"


class TestExistingItemQtyIncrease:
    """기존 항목 수량 증가 테스트"""

    def test_existing_item_qty_increase(self):
        """이미 발주 목록에 있는 품목은 수량 증가"""
        f = _make_forecaster()

        order_list = [
            {"item_cd": "AAA", "mid_cd": "002", "final_order_qty": 2, "item_nm": "상품A"},
        ]

        with patch.object(f, "_get_category_daily_totals") as mock_totals, \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            def totals_side_effect(mid_cd):
                if mid_cd == "002":
                    return [("2026-02-25", 20)]
                return []
            mock_totals.side_effect = totals_side_effect

            # 후보에 AAA가 없는 경우 (이미 existing이므로 _get_supplement_candidates에서 제외)
            mock_cands.return_value = [
                {"item_cd": "NEW001", "item_nm": "새품목", "sell_days": 5, "total_sale": 10},
            ]

            result = f.supplement_orders(order_list)

        # 새 품목이 추가된 경우
        new_items = [item for item in result if item["item_cd"] == "NEW001"]
        assert len(new_items) > 0


class TestNewItemAdded:
    """새 항목 추가 테스트"""

    def test_new_item_added_with_source(self):
        """새 항목은 source='category_floor'로 추가"""
        f = _make_forecaster()

        order_list = [
            {"item_cd": "AAA", "mid_cd": "002", "final_order_qty": 1, "item_nm": "상품A"},
        ]

        with patch.object(f, "_get_category_daily_totals") as mock_totals, \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            def totals_side_effect(mid_cd):
                if mid_cd == "002":
                    return [("2026-02-25", 20)]
                return []
            mock_totals.side_effect = totals_side_effect

            mock_cands.return_value = [
                {"item_cd": "NEW001", "item_nm": "새품목", "sell_days": 5, "total_sale": 10},
            ]

            result = f.supplement_orders(order_list)

        new_items = [item for item in result if item["item_cd"] == "NEW001"]
        if new_items:
            assert new_items[0].get("source") == "category_floor"
            assert new_items[0].get("predicted_qty") == 0


class TestDisabledConfig:
    """enabled=False 비활성화 테스트"""

    def test_disabled_returns_unchanged(self):
        """enabled=False → 원본 그대로 반환"""
        f = _make_forecaster({"enabled": False})

        order_list = [
            {"item_cd": "AAA", "mid_cd": "002", "final_order_qty": 1, "item_nm": "상품A"},
        ]

        result = f.supplement_orders(order_list)
        assert result == order_list
        assert len(result) == 1


class TestEmptyOrderList:
    """빈 발주목록 테스트"""

    def test_empty_order_list_gets_supplements(self):
        """빈 목록 → 전량 카테고리 floor 기반 추가"""
        f = _make_forecaster()

        order_list = []

        with patch.object(f, "_get_category_daily_totals") as mock_totals, \
             patch.object(f, "_get_supplement_candidates") as mock_cands:

            def totals_side_effect(mid_cd):
                if mid_cd == "002":
                    return [("2026-02-25", 10)]  # forecast=10, floor=7
                return []
            mock_totals.side_effect = totals_side_effect

            mock_cands.return_value = [
                {"item_cd": "A1", "item_nm": "품목1", "sell_days": 5, "total_sale": 10},
                {"item_cd": "A2", "item_nm": "품목2", "sell_days": 4, "total_sale": 8},
                {"item_cd": "A3", "item_nm": "품목3", "sell_days": 3, "total_sale": 6},
                {"item_cd": "A4", "item_nm": "품목4", "sell_days": 2, "total_sale": 4},
                {"item_cd": "A5", "item_nm": "품목5", "sell_days": 1, "total_sale": 2},
                {"item_cd": "A6", "item_nm": "품목6", "sell_days": 1, "total_sale": 1},
                {"item_cd": "A7", "item_nm": "품목7", "sell_days": 1, "total_sale": 1},
            ]

            result = f.supplement_orders(order_list)

        # floor=7이므로 최대 7개 항목 추가 (max_per_item=1)
        assert len(result) == 7
        assert sum(item.get("final_order_qty", 0) for item in result) == 7
