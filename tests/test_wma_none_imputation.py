"""
WMA None-day Imputation 테스트 (category-level-prediction)

신선식품(001~005)에서 stock_qty=None인 날(레코드 부재일)을
품절로 취급하여 비품절일 평균으로 impute하는 로직 검증.

테스트 범위:
- 신선식품 None일 imputation 동작
- 비식품 None일 imputation 미적용
- 가용일 부족 시 imputation 포기
- None + stock=0 혼합 처리
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.prediction.improved_predictor import ImprovedPredictor


def _make_predictor(**overrides):
    """테스트용 ImprovedPredictor 인스턴스 (DB 미접속)"""
    with patch.object(ImprovedPredictor, '__init__', return_value=None):
        p = ImprovedPredictor.__new__(ImprovedPredictor)

        mock_data = MagicMock()
        mock_data._stock_cache = {}
        mock_data._pending_cache = {}
        mock_data._use_db_inventory = True
        mock_data._inventory_repo = MagicMock()
        p._data = mock_data
        p._ot_pending_cache = None
        p.store_id = "46513"
        p.db_path = ":memory:"

        for k, v in overrides.items():
            if k in ('_stock_cache', '_pending_cache', '_use_db_inventory', '_inventory_repo'):
                setattr(mock_data, k, v)
            else:
                setattr(p, k, v)
        return p


# prediction_config에서 사용하는 설정 mock
MOCK_PARAMS = {
    "stockout_filter": {"enabled": True},
    "category_floor": {
        "enabled": True,
        "target_mid_cds": ["001", "002", "003", "004", "005"],
    },
    "holiday_wma_correction": {"enabled": False},
    "promo_wma_correction": {"enabled": False},
    "weights": {
        "day_1": 0.25,
        "day_2": 0.20,
        "day_3": 0.15,
        "day_4_7": 0.40,
    },
    "min_prediction": 0,
    "max_prediction": 999,
}


class TestNoneImputationFreshFood:
    """신선식품 None일 imputation 테스트"""

    @patch("src.prediction.improved_predictor.PREDICTION_PARAMS", MOCK_PARAMS)
    def test_fresh_food_none_imputed(self):
        """
        신선식품(002 주먹밥): None일 → 비품절일 평균으로 대체

        데이터: 7일 중 3일 재고있음(stock>0, 각 1개 판매), 4일 None(레코드없음)
        기존: None 무시 → WMA ≈ 3일 데이터만
        변경: None = 품절 → impute → WMA 상승
        """
        p = _make_predictor()

        # 3일 정상 판매(stock>0, sale=1) + 4일 None
        sales_history = [
            ("2026-02-25", 1, 5),     # 재고5, 판매1
            ("2026-02-24", 0, None),   # 레코드없음
            ("2026-02-23", 1, 3),      # 재고3, 판매1
            ("2026-02-22", 0, None),   # 레코드없음
            ("2026-02-21", 0, None),   # 레코드없음
            ("2026-02-20", 1, 2),      # 재고2, 판매1
            ("2026-02-19", 0, None),   # 레코드없음
        ]

        avg, days = p.calculate_weighted_average(
            sales_history, clean_outliers=False, mid_cd="002"
        )

        # 비품절일 평균 = (1+1+1)/3 = 1.0
        # None일 → 1.0으로 impute
        # 전체 7일 모두 1.0 → WMA ≈ 1.0
        assert avg > 0.5, f"WMA should be > 0.5 but got {avg}"
        assert avg <= 1.5, f"WMA should be <= 1.5 but got {avg}"

    @patch("src.prediction.improved_predictor.PREDICTION_PARAMS", MOCK_PARAMS)
    def test_fresh_food_none_increases_wma(self):
        """
        None imputation이 WMA를 상승시키는지 확인

        동일 데이터에서 mid_cd="002"(신선) vs mid_cd="016"(비식품) 비교
        """
        p = _make_predictor()

        sales_history = [
            ("2026-02-25", 2, 5),     # 판매2
            ("2026-02-24", 0, None),   # None
            ("2026-02-23", 0, None),   # None
            ("2026-02-22", 1, 3),      # 판매1
            ("2026-02-21", 0, None),   # None
        ]

        avg_fresh, _ = p.calculate_weighted_average(
            sales_history, clean_outliers=False, mid_cd="002"
        )
        avg_nonfood, _ = p.calculate_weighted_average(
            sales_history, clean_outliers=False, mid_cd="016"
        )

        # 신선식품은 None일이 impute되므로 WMA가 더 높아야 함
        assert avg_fresh > avg_nonfood, (
            f"Fresh food WMA ({avg_fresh}) should be > non-food WMA ({avg_nonfood})"
        )


class TestNoneNoImputationNonFood:
    """비식품 None일 imputation 미적용 테스트"""

    @patch("src.prediction.improved_predictor.PREDICTION_PARAMS", MOCK_PARAMS)
    def test_non_food_none_not_imputed(self):
        """
        비식품(016 면류): None일 → 원본 sale_qty=0 유지 (imputation 없음)
        """
        p = _make_predictor()

        sales_history = [
            ("2026-02-25", 2, 5),
            ("2026-02-24", 0, None),   # None → 비식품이므로 impute 안함
            ("2026-02-23", 0, None),
            ("2026-02-22", 3, 4),
            ("2026-02-21", 0, None),
        ]

        avg, _ = p.calculate_weighted_average(
            sales_history, clean_outliers=False, mid_cd="016"
        )

        # None일은 sale_qty=0 그대로 사용
        # 0이 3개 포함되어 WMA가 낮아야 함
        assert avg < 2.0, f"Non-food WMA should be < 2.0 but got {avg}"


class TestNoneImputationMinAvailable:
    """가용일 부족 시 imputation 포기 테스트"""

    @patch("src.prediction.improved_predictor.PREDICTION_PARAMS", MOCK_PARAMS)
    def test_no_available_days_no_imputation(self):
        """
        비품절일 0일 → imputation 불가 → 원본 유지
        """
        p = _make_predictor()

        # 모든 날이 None (재고 보유일 없음)
        sales_history = [
            ("2026-02-25", 0, None),
            ("2026-02-24", 0, None),
            ("2026-02-23", 0, None),
        ]

        avg, _ = p.calculate_weighted_average(
            sales_history, clean_outliers=False, mid_cd="002"
        )

        # available이 0이면 imputation skip → sale_qty=0 그대로
        assert avg == 0.0


class TestMixedNoneAndStockout:
    """None + stock=0 혼합 처리 테스트"""

    @patch("src.prediction.improved_predictor.PREDICTION_PARAMS", MOCK_PARAMS)
    def test_mixed_none_and_stockout(self):
        """
        신선식품: None일 + stock=0일 모두 품절로 취급 → 동일하게 impute

        데이터:
        - 2일 정상 (stock>0, sale=2)
        - 2일 품절 (stock=0, sale=0)
        - 1일 None (레코드없음)

        비품절 평균 = (2+2)/2 = 2.0
        품절2일 + None1일 → 모두 2.0으로 impute
        전체 [2, 2.0, 2, 2.0, 2.0] → WMA ≈ 2.0
        """
        p = _make_predictor()

        sales_history = [
            ("2026-02-25", 2, 3),     # 정상
            ("2026-02-24", 0, 0),     # 품절 (stock=0)
            ("2026-02-23", 2, 4),     # 정상
            ("2026-02-22", 0, 0),     # 품절 (stock=0)
            ("2026-02-21", 0, None),  # None (레코드없음)
        ]

        avg, _ = p.calculate_weighted_average(
            sales_history, clean_outliers=False, mid_cd="002"
        )

        # 모든 품절/None일이 2.0으로 impute → WMA ≈ 2.0
        assert 1.5 < avg < 2.5, f"Mixed imputation WMA should be ~2.0 but got {avg}"
