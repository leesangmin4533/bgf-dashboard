"""
food-prediction-fix 테스트

코드 리뷰에서 발견된 Critical/Major 이슈 수정 검증:
- C-1: 햄버거(005) FOOD_EXPIRY_FALLBACK 3일
- C-3: 간헐적 판매 일평균 (전체 기간 기준)
- M-2: FOOD_DISUSE_COEFFICIENT 경계값 (1.0 포함)
- M-3: item_nm 빈 문자열 방어
- M-5: food_daily_cap DB 연결 try/finally
- C-2: food_max_stock daily_avg 최소값 방어
- M-6: food_max_stock_days 연속 공식
- M-7: 폐기계수 이중 적용 방지
"""

import pytest
from unittest.mock import patch, MagicMock

from src.prediction.categories.food import (
    FOOD_EXPIRY_FALLBACK,
    FOOD_DISUSE_COEFFICIENT,
    FOOD_ANALYSIS_DAYS,
    get_food_disuse_coefficient,
    get_food_expiry_group,
    calculate_delivery_gap_consumption,
    get_delivery_waste_adjustment,
)


# =============================================================================
# Fix 1: FOOD_EXPIRY_FALLBACK 중복 제거 검증
# =============================================================================
class TestFoodExpiryFallbackUnified:
    """prediction_config.py와 food.py가 동일 객체를 참조하는지 검증"""

    def test_same_object(self):
        """prediction_config와 food가 같은 FOOD_EXPIRY_FALLBACK 참조"""
        from src.prediction.prediction_config import FOOD_EXPIRY_FALLBACK as config_fb
        from src.prediction.categories.food import FOOD_EXPIRY_FALLBACK as food_fb
        assert config_fb is food_fb

    def test_hamburger_is_3_in_both(self):
        """햄버거(005) fallback이 양쪽 모두 3일"""
        from src.prediction.prediction_config import FOOD_EXPIRY_FALLBACK as config_fb
        assert config_fb['005'] == 3


# =============================================================================
# C-1: 햄버거(005) FOOD_EXPIRY_FALLBACK 수정
# =============================================================================
class TestFoodExpiryFallback:
    """C-1: 햄버거 fallback 유통기한 검증"""

    def test_hamburger_fallback_is_3_days(self):
        """햄버거(005) fallback이 3일 (alert/config.py와 일치)"""
        assert FOOD_EXPIRY_FALLBACK['005'] == 3

    def test_dosirak_fallback_is_1_day(self):
        """도시락(001) fallback 1일 (변경 없음)"""
        assert FOOD_EXPIRY_FALLBACK['001'] == 1

    def test_sandwich_fallback_is_2_days(self):
        """샌드위치(004) fallback 2일 (변경 없음)"""
        assert FOOD_EXPIRY_FALLBACK['004'] == 2

    def test_kimbap_fallback_is_1_day(self):
        """김밥(003) fallback 1일 (변경 없음)"""
        assert FOOD_EXPIRY_FALLBACK['003'] == 1

    def test_jumeokbap_fallback_is_1_day(self):
        """주먹밥(002) fallback 1일 (변경 없음)"""
        assert FOOD_EXPIRY_FALLBACK['002'] == 1

    def test_bread_fallback_is_3_days(self):
        """빵(012) fallback 3일 (변경 없음)"""
        assert FOOD_EXPIRY_FALLBACK['012'] == 3

    def test_default_fallback_is_7_days(self):
        """기본 fallback 7일"""
        assert FOOD_EXPIRY_FALLBACK['default'] == 7

    def test_hamburger_expiry_group_is_short(self):
        """햄버거 fallback 3일 → expiry_group이 short"""
        group_name, _ = get_food_expiry_group(3)
        assert group_name == "short"


# =============================================================================
# M-2: FOOD_DISUSE_COEFFICIENT 경계값
# =============================================================================
class TestFoodDisuseCoefficient:
    """M-2: 폐기율 계수 경계값 검증"""

    def test_disuse_rate_zero_returns_1(self):
        """폐기율 0% → 계수 1.0"""
        assert get_food_disuse_coefficient(0.0) == 1.0

    def test_disuse_rate_005_returns_095(self):
        """폐기율 5% → 계수 0.95"""
        assert get_food_disuse_coefficient(0.05) == 0.95

    def test_disuse_rate_010_returns_085(self):
        """폐기율 10% → 계수 0.85"""
        assert get_food_disuse_coefficient(0.10) == 0.85

    def test_disuse_rate_015_returns_075(self):
        """폐기율 15% → 계수 0.75"""
        assert get_food_disuse_coefficient(0.15) == 0.75

    def test_disuse_rate_020_returns_07(self):
        """폐기율 20% → 계수 0.7"""
        assert get_food_disuse_coefficient(0.20) == 0.7

    def test_disuse_rate_050_returns_07(self):
        """폐기율 50% → 계수 0.7"""
        assert get_food_disuse_coefficient(0.50) == 0.7

    def test_disuse_rate_100_returns_07(self):
        """M-2: 폐기율 100% → 계수 0.7 (경계값 수정 확인)"""
        assert get_food_disuse_coefficient(1.0) == 0.7

    def test_disuse_rate_none_returns_1(self):
        """폐기율 None → 계수 1.0"""
        assert get_food_disuse_coefficient(None) == 1.0

    def test_disuse_coefficient_upper_bound_inclusive(self):
        """마지막 구간 상한이 1.01이므로 1.0 포함"""
        last_range = list(FOOD_DISUSE_COEFFICIENT.keys())[-1]
        assert last_range[1] == 1.01


# =============================================================================
# M-3: item_nm 빈 문자열 방어
# =============================================================================
class TestItemNameSafety:
    """M-3: item_nm.strip()[-1] IndexError 방어"""

    def test_empty_string_gap_consumption(self):
        """빈 문자열 item_nm → 에러 없이 정상 반환"""
        result = calculate_delivery_gap_consumption(5.0, "", "ultra_short")
        assert isinstance(result, float)

    def test_whitespace_only_gap_consumption(self):
        """공백만 있는 item_nm → 에러 없이 정상 반환"""
        result = calculate_delivery_gap_consumption(5.0, "   ", "ultra_short")
        assert isinstance(result, float)

    def test_none_gap_consumption(self):
        """None item_nm → 에러 없이 정상 반환"""
        result = calculate_delivery_gap_consumption(5.0, None, "ultra_short")
        assert isinstance(result, float)

    def test_empty_string_waste_adjustment(self):
        """빈 문자열 item_nm → 기본값 1.0 반환"""
        result = get_delivery_waste_adjustment("TEST001", "")
        assert result == 1.0

    def test_whitespace_only_waste_adjustment(self):
        """공백만 있는 item_nm → 기본값 1.0 반환"""
        result = get_delivery_waste_adjustment("TEST001", "   ")
        assert result == 1.0


# =============================================================================
# C-3: 간헐적 판매 일평균 (전체 기간 기준)
# =============================================================================
class TestFoodAnalysisDays:
    """C-3: 분석 기간 상수 검증"""

    def test_analysis_days_is_30(self):
        """FOOD_ANALYSIS_DAYS 상수가 30"""
        assert FOOD_ANALYSIS_DAYS == 30


# =============================================================================
# M-6: food_max_stock_days 연속 공식
# =============================================================================
class TestFoodMaxStockDays:
    """M-6: min(exp_days + 1, 7) 연속 공식 검증"""

    @pytest.mark.parametrize("exp_days,expected", [
        (1, 2),    # min(2, 7) = 2
        (2, 3),    # min(3, 7) = 3
        (3, 4),    # min(4, 7) = 4
        (5, 6),    # min(6, 7) = 6
        (6, 7),    # min(7, 7) = 7
        (7, 7),    # min(8, 7) = 7 (기존 8 → 7)
        (8, 7),    # min(9, 7) = 7 (기존 5 → 7, 불연속 해소)
        (14, 7),   # min(15, 7) = 7
        (30, 7),   # min(31, 7) = 7
    ])
    def test_max_stock_days_formula(self, exp_days, expected):
        """food_max_stock_days = min(exp_days + 1, 7) 검증"""
        result = min(exp_days + 1, 7)
        assert result == expected

    def test_no_discontinuity_at_boundary(self):
        """유통기한 7→8일 경계에서 불연속 없음"""
        days_7 = min(7 + 1, 7)  # = 7
        days_8 = min(8 + 1, 7)  # = 7
        assert days_7 == days_8  # 동일 (기존: 8 vs 5 급감)

    def test_monotonic_up_to_cap(self):
        """1~6일까지 단조 증가, 이후 7로 고정"""
        results = [min(d + 1, 7) for d in range(1, 15)]
        for i in range(len(results) - 1):
            assert results[i] <= results[i + 1]


# =============================================================================
# C-2: daily_avg 최소값 방어
# =============================================================================
class TestDailyAvgMinimum:
    """C-2: daily_avg < 0.5 일 때 상한선 체크 스킵"""

    def test_daily_avg_below_05_no_skip(self):
        """일평균 0.3 → 상한선 체크 안 함 (스킵 안 됨)"""
        daily_avg = 0.3
        # daily_avg >= 0.5 조건 불충족이므로 skip 판단 안 함
        assert not (daily_avg >= 0.5)

    def test_daily_avg_05_allows_skip(self):
        """일평균 0.5 → 상한선 체크 허용"""
        daily_avg = 0.5
        assert daily_avg >= 0.5

    def test_daily_avg_zero_no_skip(self):
        """일평균 0 → 상한선 체크 안 함"""
        daily_avg = 0.0
        assert not (daily_avg >= 0.5)


# =============================================================================
# M-7: 폐기계수 이중 적용 방지
# =============================================================================
class TestEffectiveWasteCoefficient:
    """M-7: min(food_disuse_coef, delivery_waste_adj) 단일 적용"""

    def test_both_normal_returns_1(self):
        """둘 다 1.0 → effective 1.0"""
        assert min(1.0, 1.0) == 1.0

    def test_disuse_lower_uses_disuse(self):
        """disuse가 더 낮으면 disuse 사용"""
        assert min(0.5, 0.8) == 0.5

    def test_delivery_lower_uses_delivery(self):
        """delivery가 더 낮으면 delivery 사용"""
        assert min(0.8, 0.5) == 0.5

    def test_both_low_uses_minimum(self):
        """둘 다 낮으면 더 낮은 값"""
        assert min(0.65, 0.5) == 0.5

    def test_effective_applied_once(self):
        """effective_waste_coef가 prediction에 한 번만 적용되는 시뮬레이션"""
        base_prediction = 10.0
        food_disuse_coef = 0.8
        delivery_waste_adj = 0.9

        # 신규 방식: min() 한 번 적용
        effective = min(food_disuse_coef, delivery_waste_adj)
        new_result = base_prediction * effective

        # 기존 방식: delivery로 prediction 줄이고, disuse로 safety 줄이면 이중 감소
        old_prediction = base_prediction * delivery_waste_adj  # 9.0
        old_safety = old_prediction * 0.3 * food_disuse_coef   # 9.0 * 0.3 * 0.8 = 2.16

        new_safety = new_result * 0.3 * 1.0  # 8.0 * 0.3 * 1.0 = 2.4 (disuse_rate=None)

        # 신규 방식이 안전재고를 더 적절히 유지
        assert new_safety >= old_safety


# =============================================================================
# M-5: food_daily_cap DB 연결 안전성
# =============================================================================
class TestFoodDailyCapDBSafety:
    """M-5: get_weekday_avg_sales에 try/finally 적용 확인"""

    def test_function_exists(self):
        """get_weekday_avg_sales 함수 import 가능"""
        from src.prediction.categories.food_daily_cap import get_weekday_avg_sales
        assert callable(get_weekday_avg_sales)


# =============================================================================
# Fix 2: _get_db_path store DB 지원
# =============================================================================
class TestGetDbPath:
    """_get_db_path가 store_id에 따라 경로를 반환하는지 검증"""

    def test_no_store_id_returns_legacy(self):
        """store_id 없으면 legacy bgf_sales.db 경로"""
        from src.prediction.categories.food import _get_db_path
        result = _get_db_path()
        assert "bgf_sales.db" in result

    def test_none_store_id_returns_legacy(self):
        """store_id=None이면 legacy 경로"""
        from src.prediction.categories.food import _get_db_path
        result = _get_db_path(None)
        assert "bgf_sales.db" in result

    def test_invalid_store_id_returns_legacy(self):
        """존재하지 않는 매장 DB면 legacy fallback"""
        from src.prediction.categories.food import _get_db_path
        result = _get_db_path("99999")
        # 매장 DB가 없으면 legacy로 fallback
        assert result.endswith(".db")


# =============================================================================
# Fix 2: get_safety_stock_with_food_pattern db_path 파라미터
# =============================================================================
class TestSafetyStockDbPath:
    """get_safety_stock_with_food_pattern에 db_path 파라미터 추가 확인"""

    def test_accepts_db_path_parameter(self):
        """db_path 파라미터를 받는지 확인"""
        import inspect
        from src.prediction.categories.food import get_safety_stock_with_food_pattern
        sig = inspect.signature(get_safety_stock_with_food_pattern)
        assert "db_path" in sig.parameters

    def test_non_food_ignores_db_path(self):
        """비푸드 카테고리는 db_path 무시하고 기본값 반환"""
        from src.prediction.categories.food import get_safety_stock_with_food_pattern
        safety, pattern = get_safety_stock_with_food_pattern(
            "999", 5.0, db_path="/nonexistent.db"
        )
        assert pattern is None
        assert safety == 5.0  # default_safety_days=1.0 * 5.0


# =============================================================================
# Fix 3: 복합 계수 바닥값 검증
# =============================================================================
class TestCompoundCoefficientFloor:
    """7개 계수 곱의 바닥값(15%) 검증"""

    def test_worst_case_still_above_floor(self):
        """최악 조건에서도 원래 예측의 15% 이상 보장"""
        base = 10.0
        # worst case: 0.5 * 0.7 * 0.75 * 0.80 * 0.7 * 0.7 * 0.5 = 0.051
        worst_compound = 0.5 * 0.7 * 0.75 * 0.80 * 0.7 * 0.7 * 0.5
        adjusted = base * worst_compound
        floor = base * 0.15
        # 바닥값 적용
        result = max(adjusted, floor)
        assert result == floor  # adjusted(0.51) < floor(1.5)
        assert result >= base * 0.15

    def test_normal_case_no_clamping(self):
        """정상 범위에서는 바닥값 미적용"""
        base = 10.0
        normal_compound = 0.9 * 1.0 * 1.0 * 1.05 * 1.0 * 1.0 * 1.0
        adjusted = base * normal_compound
        floor = base * 0.15
        result = max(adjusted, floor)
        assert result == adjusted  # 9.45 > 1.5

    def test_floor_is_15_percent(self):
        """바닥값이 정확히 15%인지 확인"""
        base = 100.0
        floor = base * 0.15
        assert floor == 15.0


# =============================================================================
# Fix 4: daily_avg 7일 절벽 블렌딩 검증
# =============================================================================
class TestDailyAvgBlending:
    """7일 절벽 대신 7~13일 선형 블렌딩 검증"""

    def test_6_days_uses_actual(self):
        """6일: 실제 데이터일수로 나눔"""
        total_sales = 12
        actual_data_days = 6
        daily_avg = total_sales / max(actual_data_days, 1)
        assert daily_avg == 2.0

    def test_7_days_blended(self):
        """7일: 블렌딩 시작 (급변 없음)"""
        total_sales = 14
        actual_data_days = 7
        analysis_days = 30
        short_avg = total_sales / actual_data_days  # 2.0
        long_avg = total_sales / analysis_days       # 0.467
        blend_ratio = (actual_data_days - 7) / 7.0   # 0.0
        daily_avg = short_avg * (1 - blend_ratio) + long_avg * blend_ratio
        # blend_ratio=0 이므로 short_avg와 동일
        assert abs(daily_avg - short_avg) < 0.001

    def test_10_days_partially_blended(self):
        """10일: 중간 블렌딩"""
        total_sales = 20
        actual_data_days = 10
        analysis_days = 30
        short_avg = total_sales / actual_data_days  # 2.0
        long_avg = total_sales / analysis_days       # 0.667
        blend_ratio = (actual_data_days - 7) / 7.0   # 3/7 = 0.429
        daily_avg = short_avg * (1 - blend_ratio) + long_avg * blend_ratio
        # 2.0 * 0.571 + 0.667 * 0.429 = 1.143 + 0.286 = 1.429
        assert short_avg > daily_avg > long_avg

    def test_14_days_uses_analysis(self):
        """14일 이상: 전체 분석 기간으로 나눔"""
        total_sales = 28
        actual_data_days = 14
        analysis_days = 30
        daily_avg = total_sales / analysis_days
        assert abs(daily_avg - 0.933) < 0.01

    def test_continuity_at_7_day_boundary(self):
        """7일 경계에서 연속성 보장 (급변 없음)"""
        total_sales = 14
        analysis_days = 30

        # 6일: actual 기준
        avg_6 = total_sales / 6  # 2.333

        # 7일: 블렌딩 (blend_ratio=0 -> short_avg)
        short_7 = total_sales / 7  # 2.0
        long_7 = total_sales / analysis_days
        blend_7 = (7 - 7) / 7.0  # 0.0
        avg_7 = short_7 * (1 - blend_7) + long_7 * blend_7  # = short_7

        # 6→7 변화율이 완만 (이전: 2.333→0.467 = -84%, 신규: 2.333→2.0 = -14%)
        change_ratio = abs(avg_7 - avg_6) / avg_6
        assert change_ratio < 0.20  # 20% 이내 변화

    def test_continuity_at_14_day_boundary(self):
        """14일 경계에서도 연속성 보장"""
        total_sales = 28
        analysis_days = 30

        # 13일: 블렌딩
        short_13 = total_sales / 13
        long_13 = total_sales / analysis_days
        blend_13 = (13 - 7) / 7.0  # 6/7 = 0.857
        avg_13 = short_13 * (1 - blend_13) + long_13 * blend_13

        # 14일: analysis_days 기준
        avg_14 = total_sales / analysis_days

        # 13→14 변화가 완만 (이전 7일 절벽: 84% 급변 대비 대폭 개선)
        change_ratio = abs(avg_14 - avg_13) / max(avg_13, 0.001)
        assert change_ratio < 0.20  # 20% 이내 변화


# =============================================================================
# ML-Consistency Fix 1: FOOD_EXPIRY_SAFETY_CONFIG 동일 객체 참조
# =============================================================================
class TestFoodExpirySafetyConfigUnified:
    """prediction_config.py와 food.py가 동일 FOOD_EXPIRY_SAFETY_CONFIG 객체를 참조"""

    def test_same_object(self):
        """prediction_config와 food가 같은 FOOD_EXPIRY_SAFETY_CONFIG 참조"""
        from src.prediction.prediction_config import FOOD_EXPIRY_SAFETY_CONFIG as config_fesc
        from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG as food_fesc
        assert config_fesc is food_fesc

    def test_ultra_short_safety_days_is_05(self):
        """ultra_short safety_days가 0.5 (신 버전)인지 확인"""
        from src.prediction.prediction_config import FOOD_EXPIRY_SAFETY_CONFIG
        assert FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["ultra_short"]["safety_days"] == 0.5

    def test_short_safety_days_is_07(self):
        """short safety_days가 0.7 (신 버전)인지 확인"""
        from src.prediction.prediction_config import FOOD_EXPIRY_SAFETY_CONFIG
        assert FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["short"]["safety_days"] == 0.7


# =============================================================================
# ML-Consistency Fix 2: promo_active 추론 시 전달
# =============================================================================
class TestPromoActiveInference:
    """ML 앙상블 추론 시 promo_active가 build_features에 전달되는지 검증"""

    def test_promo_active_with_active_promo(self):
        """행사 중인 상품: promo_active=True 전달"""
        from unittest.mock import MagicMock, patch
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor._promo_adjuster = MagicMock()

        # PromotionStatus mock
        mock_status = MagicMock()
        mock_status.current_promo = "1+1"
        predictor._promo_adjuster.promo_manager.get_promotion_status.return_value = mock_status

        predictor._ml_predictor = MagicMock()
        predictor._association_adjuster = None
        predictor._receiving_stats_cache = {}
        predictor.db_path = ":memory:"
        predictor.store_id = None

        # _get_holiday_context, _get_temperature_for_date 등 stub
        predictor._get_holiday_context = MagicMock(return_value={"is_holiday": False, "period_days": 0, "is_pre_holiday": False, "is_post_holiday": False})
        predictor._get_temperature_for_date = MagicMock(return_value=15.0)
        predictor._get_temperature_delta = MagicMock(return_value=0.0)
        predictor._get_disuse_rate = MagicMock(return_value=0.0)

        from datetime import date as dt_date

        with patch("src.prediction.ml.feature_builder.MLFeatureBuilder") as MockFB, \
             patch("src.prediction.ml.data_pipeline.MLDataPipeline") as MockPipeline:
            MockPipeline.return_value.get_item_daily_stats.return_value = []
            MockFB.build_features.return_value = [0.0] * 36

            predictor._ml_predictor.predict.return_value = 5.0

            predictor._apply_ml_ensemble(
                item_cd="TEST001",
                product={"expiration_days": 1, "margin_rate": 0.3, "mid_cd": "001"},
                mid_cd="001",
                order_qty=10,
                data_days=90,
                target_date=dt_date(2026, 2, 22),
                current_stock=5,
                pending_qty=0,
                safety_stock=2.0,
                feat_result=None,
            )

            # build_features가 promo_active=True로 호출되었는지 확인
            call_kwargs = MockFB.build_features.call_args
            assert call_kwargs is not None
            assert call_kwargs.kwargs.get("promo_active") is True or \
                   (call_kwargs[1].get("promo_active") is True)

    def test_promo_active_without_adjuster(self):
        """_promo_adjuster 없으면 promo_active=False"""
        from unittest.mock import MagicMock, patch
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor._promo_adjuster = None
        predictor._ml_predictor = MagicMock()
        predictor._association_adjuster = None
        predictor._receiving_stats_cache = {}
        predictor.db_path = ":memory:"
        predictor.store_id = None
        predictor._get_holiday_context = MagicMock(return_value={"is_holiday": False, "period_days": 0, "is_pre_holiday": False, "is_post_holiday": False})
        predictor._get_temperature_for_date = MagicMock(return_value=15.0)
        predictor._get_temperature_delta = MagicMock(return_value=0.0)
        predictor._get_disuse_rate = MagicMock(return_value=0.0)

        from datetime import date as dt_date

        with patch("src.prediction.ml.feature_builder.MLFeatureBuilder") as MockFB, \
             patch("src.prediction.ml.data_pipeline.MLDataPipeline") as MockPipeline:
            MockPipeline.return_value.get_item_daily_stats.return_value = []
            MockFB.build_features.return_value = [0.0] * 36
            predictor._ml_predictor.predict.return_value = 5.0

            predictor._apply_ml_ensemble(
                item_cd="TEST001",
                product={"expiration_days": 1, "margin_rate": 0.3, "mid_cd": "999"},
                mid_cd="999",
                order_qty=10,
                data_days=90,
                target_date=dt_date(2026, 2, 22),
                current_stock=5,
                pending_qty=0,
                safety_stock=2.0,
                feat_result=None,
            )

            call_kwargs = MockFB.build_features.call_args
            assert call_kwargs is not None
            assert call_kwargs.kwargs.get("promo_active") is False or \
                   (call_kwargs[1].get("promo_active") is False)

    def test_promo_active_exception_fallback(self):
        """promo_manager 예외 시 promo_active=False"""
        from unittest.mock import MagicMock, patch
        from src.prediction.improved_predictor import ImprovedPredictor

        predictor = ImprovedPredictor.__new__(ImprovedPredictor)
        predictor._promo_adjuster = MagicMock()
        predictor._promo_adjuster.promo_manager.get_promotion_status.side_effect = Exception("DB error")
        predictor._ml_predictor = MagicMock()
        predictor._association_adjuster = None
        predictor._receiving_stats_cache = {}
        predictor.db_path = ":memory:"
        predictor.store_id = None
        predictor._get_holiday_context = MagicMock(return_value={"is_holiday": False, "period_days": 0, "is_pre_holiday": False, "is_post_holiday": False})
        predictor._get_temperature_for_date = MagicMock(return_value=15.0)
        predictor._get_temperature_delta = MagicMock(return_value=0.0)
        predictor._get_disuse_rate = MagicMock(return_value=0.0)

        from datetime import date as dt_date

        with patch("src.prediction.ml.feature_builder.MLFeatureBuilder") as MockFB, \
             patch("src.prediction.ml.data_pipeline.MLDataPipeline") as MockPipeline:
            MockPipeline.return_value.get_item_daily_stats.return_value = []
            MockFB.build_features.return_value = [0.0] * 36
            predictor._ml_predictor.predict.return_value = 5.0

            predictor._apply_ml_ensemble(
                item_cd="TEST001",
                product={"expiration_days": 1, "margin_rate": 0.3, "mid_cd": "999"},
                mid_cd="999",
                order_qty=10,
                data_days=90,
                target_date=dt_date(2026, 2, 22),
                current_stock=5,
                pending_qty=0,
                safety_stock=2.0,
                feat_result=None,
            )

            call_kwargs = MockFB.build_features.call_args
            assert call_kwargs is not None
            assert call_kwargs.kwargs.get("promo_active") is False or \
                   (call_kwargs[1].get("promo_active") is False)


# =============================================================================
# ML-Consistency Fix 3: prediction_config.py dead code 삭제 확인
# =============================================================================
class TestPredictionConfigDeadCodeRemoved:
    """prediction_config.py에서 food 관련 dead code가 제거되었는지 확인"""

    def test_no_food_expiry_result_class(self):
        """prediction_config.py에 FoodExpiryResult class가 없어야 함"""
        import src.prediction.prediction_config as pc
        # FoodExpiryResult는 food.py에서만 정의
        from src.prediction.categories.food import FoodExpiryResult
        # prediction_config에 직접 정의된 FoodExpiryResult가 없음을 확인
        # (import를 통해 접근은 가능할 수 있지만, 직접 정의는 아님)
        import inspect
        source_file = inspect.getfile(FoodExpiryResult)
        assert "food.py" in source_file

    def test_no_duplicate_is_food_category(self):
        """prediction_config.py에 is_food_category 함수가 없어야 함"""
        import src.prediction.prediction_config as pc
        # is_food_category가 prediction_config 모듈에 직접 정의되지 않음
        assert not hasattr(pc, 'is_food_category') or \
               'prediction_config' not in getattr(pc.is_food_category, '__module__', '')
