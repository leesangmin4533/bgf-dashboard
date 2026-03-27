"""
강수 예보 기반 예측 계수 테스트
rain-prediction-factor PDCA
"""

import pytest
from unittest.mock import patch, MagicMock
from src.prediction.coefficient_adjuster import CoefficientAdjuster
from src.prediction.categories.food import (
    get_food_precipitation_cross_coefficient,
    FOOD_PRECIPITATION_CROSS_COEFFICIENTS,
)


@pytest.fixture
def adjuster():
    return CoefficientAdjuster(store_id="46513")


# =========================================================================
# get_precipitation_for_date 테스트
# =========================================================================

class TestGetPrecipitationForDate:
    """external_factors에서 강수 예보 조회 테스트"""

    def test_returns_default_when_no_data(self, adjuster):
        """데이터 없을 때 기본값 반환"""
        with patch.object(
            CoefficientAdjuster, 'get_precipitation_for_date',
            wraps=adjuster.get_precipitation_for_date
        ):
            # ExternalFactorRepository mock
            with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
                mock_repo.return_value.get_factors.return_value = []
                result = adjuster.get_precipitation_for_date("2026-03-05")
                assert result["rain_rate"] is None
                assert result["rain_qty"] is None
                assert result["is_snow"] is False

    def test_parses_rain_rate(self, adjuster):
        """강수확률 파싱"""
        with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
            mock_repo.return_value.get_factors.return_value = [
                {"factor_key": "rain_rate_forecast", "factor_value": "50"},
                {"factor_key": "rain_qty_forecast", "factor_value": "3"},
            ]
            result = adjuster.get_precipitation_for_date("2026-03-03")
            assert result["rain_rate"] == 50.0
            assert result["rain_qty"] == 3.0

    def test_parses_snow(self, adjuster):
        """눈 여부 파싱"""
        with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
            mock_repo.return_value.get_factors.return_value = [
                {"factor_key": "rain_rate_forecast", "factor_value": "70"},
                {"factor_key": "is_snow_forecast", "factor_value": "1"},
            ]
            result = adjuster.get_precipitation_for_date("2026-03-03")
            assert result["is_snow"] is True

    def test_handles_invalid_values(self, adjuster):
        """잘못된 값 처리"""
        with patch('src.prediction.coefficient_adjuster.ExternalFactorRepository') as mock_repo:
            mock_repo.return_value.get_factors.return_value = [
                {"factor_key": "rain_rate_forecast", "factor_value": "abc"},
            ]
            result = adjuster.get_precipitation_for_date("2026-03-03")
            assert result["rain_rate"] is None


# =========================================================================
# get_precipitation_coefficient 테스트
# =========================================================================

class TestGetPrecipitationCoefficient:
    """강수 예보 기반 계수 계산 테스트"""

    def _mock_precip(self, adjuster, rain_rate=None, rain_qty=None, is_snow=False):
        """강수 데이터 모킹 헬퍼"""
        return patch.object(
            adjuster, 'get_precipitation_for_date',
            return_value={"rain_rate": rain_rate, "rain_qty": rain_qty, "is_snow": is_snow}
        )

    def test_no_rain_returns_1(self, adjuster):
        """강수 없음 → 1.0"""
        with self._mock_precip(adjuster, rain_rate=10):
            assert adjuster.get_precipitation_coefficient("2026-03-03", "001") == 1.0

    def test_no_data_returns_1(self, adjuster):
        """데이터 없음 → 1.0"""
        with self._mock_precip(adjuster, rain_rate=None):
            assert adjuster.get_precipitation_coefficient("2026-03-03", "001") == 1.0

    def test_light_rain_food(self, adjuster):
        """약한 비(30~60%) → food 0.95"""
        with self._mock_precip(adjuster, rain_rate=45):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "001")
            assert coef == 0.95

    def test_moderate_rain_food(self, adjuster):
        """보통 비(60~80%) → food 0.90"""
        with self._mock_precip(adjuster, rain_rate=70):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "002")
            assert coef == 0.90

    def test_moderate_rain_ramen_boost(self, adjuster):
        """보통 비(60~80%) → 라면 1.05"""
        with self._mock_precip(adjuster, rain_rate=65):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "015")
            assert coef == 1.05

    def test_heavy_rain_food(self, adjuster):
        """폭우(80%+) → food 0.85"""
        with self._mock_precip(adjuster, rain_rate=85):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "003")
            assert coef == 0.85

    def test_heavy_rain_by_qty(self, adjuster):
        """강수량 10mm+ → heavy"""
        with self._mock_precip(adjuster, rain_rate=50, rain_qty=15):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "001")
            assert coef == 0.85  # heavy_rain

    def test_heavy_rain_boost(self, adjuster):
        """폭우(80%+) → 라면/핫푸드 1.10"""
        with self._mock_precip(adjuster, rain_rate=90):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "016")
            assert coef == 1.10

    def test_snow_food(self, adjuster):
        """눈 → food 0.82"""
        with self._mock_precip(adjuster, rain_rate=70, is_snow=True):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "001")
            assert coef == 0.82

    def test_snow_boost(self, adjuster):
        """눈 → 라면/핫푸드 1.12"""
        with self._mock_precip(adjuster, rain_rate=60, is_snow=True):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "017")
            assert coef == 1.12

    def test_unaffected_category(self, adjuster):
        """영향 없는 카테고리 → 1.0"""
        with self._mock_precip(adjuster, rain_rate=80):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "050")
            assert coef == 1.0

    def test_boundary_30(self, adjuster):
        """경계값 30% → light"""
        with self._mock_precip(adjuster, rain_rate=30):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "001")
            assert coef == 0.95

    def test_boundary_60(self, adjuster):
        """경계값 60% → moderate"""
        with self._mock_precip(adjuster, rain_rate=60):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "001")
            assert coef == 0.90

    def test_boundary_80(self, adjuster):
        """경계값 80% → heavy"""
        with self._mock_precip(adjuster, rain_rate=80):
            coef = adjuster.get_precipitation_coefficient("2026-03-03", "001")
            assert coef == 0.85


# =========================================================================
# food_precipitation_cross_coefficient 테스트
# =========================================================================

class TestFoodPrecipitationCrossCoefficient:
    """푸드×강수 교차 계수 테스트"""

    def test_no_rain(self):
        """강수 없음 → 1.0"""
        assert get_food_precipitation_cross_coefficient("001", None) == 1.0

    def test_low_rain(self):
        """20% → 1.0 (30% 미만)"""
        assert get_food_precipitation_cross_coefficient("001", 20) == 1.0

    def test_light_dosirak(self):
        """약한 비 → 도시락(001) 0.97"""
        assert get_food_precipitation_cross_coefficient("001", 40) == 0.97

    def test_light_gimbap(self):
        """약한 비 → 김밥(003) 0.95"""
        assert get_food_precipitation_cross_coefficient("003", 50) == 0.95

    def test_moderate_dosirak(self):
        """보통 비 → 도시락(001) 0.93"""
        assert get_food_precipitation_cross_coefficient("001", 70) == 0.93

    def test_heavy_gimbap(self):
        """폭우 → 김밥(003) 0.85"""
        assert get_food_precipitation_cross_coefficient("003", 90) == 0.85

    def test_heavy_sandwich(self):
        """폭우 → 샌드위치(004) 0.93"""
        assert get_food_precipitation_cross_coefficient("004", 85) == 0.93

    def test_non_food_category(self):
        """비푸드 카테고리 → 1.0"""
        assert get_food_precipitation_cross_coefficient("050", 80) == 1.0

    def test_all_levels_exist(self):
        """모든 레벨 상수 존재 확인"""
        assert "light" in FOOD_PRECIPITATION_CROSS_COEFFICIENTS
        assert "moderate" in FOOD_PRECIPITATION_CROSS_COEFFICIENTS
        assert "heavy" in FOOD_PRECIPITATION_CROSS_COEFFICIENTS


# =========================================================================
# 통합 테스트
# =========================================================================

class TestPrecipitationIntegration:
    """apply() 통합 테스트 - 강수 계수가 weather_coef에 곱해지는지"""

    def test_precip_coef_merged_into_weather(self, adjuster):
        """강수 계수가 weather_coef에 병합되어 적용"""
        with patch.object(adjuster, 'get_weather_coefficient', return_value=1.0), \
             patch.object(adjuster, 'get_precipitation_coefficient', return_value=0.90) as mock_precip, \
             patch.object(adjuster, 'get_holiday_coefficient', return_value=1.0), \
             patch.object(adjuster, 'get_temperature_for_date', return_value=15.0), \
             patch.object(adjuster, 'get_precipitation_for_date', return_value={"rain_rate": 70}):

            from datetime import datetime
            product = {"mid_cd": "001", "item_nm": "테스트도시락"}

            result = adjuster.apply(
                base_prediction=10.0,
                item_cd="TEST001",
                product=product,
                target_date=datetime(2026, 3, 3),
                sqlite_weekday=2,
                feat_result=None,
                demand_pattern_cache={},
                food_weekday_cache={},
                association_adjuster=None,
                db_path=None,
            )

            # 강수 계수가 적용되었는지 확인 (weather_coef *= precip_coef)
            mock_precip.assert_called_once()
            _, adjusted, _, _ = result
            # 10.0 * 0.90 (weather*precip) * food_precip_coef * weekday 등
            assert adjusted < 10.0  # 비 영향으로 감소

    def test_no_precip_no_change(self, adjuster):
        """강수 없음 → 기존 동작 유지"""
        with patch.object(adjuster, 'get_weather_coefficient', return_value=1.0), \
             patch.object(adjuster, 'get_precipitation_coefficient', return_value=1.0), \
             patch.object(adjuster, 'get_holiday_coefficient', return_value=1.0), \
             patch.object(adjuster, 'get_temperature_for_date', return_value=15.0), \
             patch.object(adjuster, 'get_precipitation_for_date', return_value={"rain_rate": None}):

            from datetime import datetime
            product = {"mid_cd": "050", "item_nm": "일반상품"}

            result = adjuster.apply(
                base_prediction=10.0,
                item_cd="TEST002",
                product=product,
                target_date=datetime(2026, 3, 3),
                sqlite_weekday=2,
                feat_result=None,
                demand_pattern_cache={},
                food_weekday_cache={},
                association_adjuster=None,
                db_path=None,
            )

            _, adjusted, weekday_coef, _ = result
            # 강수 계수 1.0이므로 weekday 반영만 적용됨
            assert adjusted == pytest.approx(10.0 * weekday_coef, abs=0.01)
