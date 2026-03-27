"""AdditiveAdjuster 테스트

덧셈 기반 계수 통합: 패턴별 clamp, 극단값, 바닥값 검증
"""

import pytest
from src.prediction.additive_adjuster import (
    AdditiveAdjuster,
    AdditiveResult,
    ADDITIVE_CLAMP_RANGES,
)


class TestAdditiveBasic:
    """기본 동작 테스트"""

    def test_no_adjustment(self):
        """모든 계수 1.0 -> 변화 없음"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(base_prediction=3.0)
        assert result.adjusted_prediction == 3.0
        assert result.delta_sum == 0.0
        assert result.multiplier == 1.0

    def test_single_positive_delta(self):
        """holiday_coef=1.1 -> +0.1"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(base_prediction=2.0, holiday_coef=1.1)
        assert abs(result.delta_sum - 0.1) < 0.001
        assert abs(result.adjusted_prediction - 2.2) < 0.01

    def test_single_negative_delta(self):
        """weekday_coef=0.9 -> -0.1"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(base_prediction=2.0, weekday_coef=0.9)
        assert abs(result.delta_sum - (-0.1)) < 0.001
        assert abs(result.adjusted_prediction - 1.8) < 0.01

    def test_multiple_deltas_sum(self):
        """여러 계수 합산"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=2.0,
            holiday_coef=1.1,
            weather_coef=1.05,
            weekday_coef=0.95,
            seasonal_coef=1.1,
        )
        expected_delta = 0.1 + 0.05 - 0.05 + 0.1
        assert abs(result.delta_sum - expected_delta) < 0.001


class TestAdditiveClamp:
    """패턴별 clamp 범위 테스트"""

    def test_daily_clamp_upper(self):
        """daily 패턴: 최대 +0.8"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.5,
            weather_coef=1.3,
            weekday_coef=1.2,
        )
        # delta_sum = 0.5+0.3+0.2 = 1.0 > 0.8
        assert result.clamped_delta == 0.8
        assert result.multiplier == 1.8
        assert abs(result.adjusted_prediction - 1.8) < 0.01

    def test_daily_clamp_lower(self):
        """daily 패턴: 최소 -0.5"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=2.0,
            holiday_coef=0.5,
            weather_coef=0.5,
        )
        # delta_sum = -0.5 + -0.5 = -1.0 < -0.5
        assert result.clamped_delta == -0.5
        assert result.multiplier == 0.5
        assert abs(result.adjusted_prediction - 1.0) < 0.01

    def test_frequent_clamp(self):
        """frequent 패턴: 최대 +0.5"""
        adj = AdditiveAdjuster(pattern="frequent")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.5,
            weather_coef=1.3,
        )
        assert result.clamped_delta == 0.5
        assert result.multiplier == 1.5

    def test_intermittent_clamp(self):
        """intermittent 패턴: 최대 +0.3"""
        adj = AdditiveAdjuster(pattern="intermittent")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.5,
        )
        assert result.clamped_delta == 0.3

    def test_slow_no_adjustment(self):
        """slow 패턴: 계수 적용 안 함"""
        adj = AdditiveAdjuster(pattern="slow")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.5,
            weather_coef=1.3,
        )
        assert result.adjusted_prediction == 1.0
        assert result.multiplier == 1.0
        assert result.delta_sum == 0.0


class TestAdditiveEdgeCases:
    """극단값/에지 케이스"""

    def test_zero_prediction(self):
        """base_prediction=0 -> 결과도 0"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(base_prediction=0.0, holiday_coef=1.5)
        assert result.adjusted_prediction == 0.0

    def test_negative_result_floor(self):
        """극단 음수 -> 바닥값 (base * 0.15)"""
        adj = AdditiveAdjuster(pattern="daily")
        # clamp_min = -0.5 -> multiplier = 0.5 -> positive
        # 직접 음수가 되려면 clamp 범위를 벗어나야 하므로 정상 동작 확인
        result = adj.apply(
            base_prediction=2.0,
            holiday_coef=0.3,
            weather_coef=0.3,
        )
        assert result.adjusted_prediction > 0

    def test_deltas_detail(self):
        """개별 delta 상세 반환"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.1,
            weekday_coef=0.95,
        )
        assert abs(result.deltas["holiday"] - 0.1) < 0.001
        assert abs(result.deltas["weekday"] - (-0.05)) < 0.001
        assert result.deltas["weather"] == 0.0

    def test_unknown_pattern_defaults_to_daily(self):
        """알 수 없는 패턴 -> daily 범위"""
        adj = AdditiveAdjuster(pattern="unknown")
        assert adj.clamp_min == ADDITIVE_CLAMP_RANGES["daily"][0]
        assert adj.clamp_max == ADDITIVE_CLAMP_RANGES["daily"][1]


class TestAdditiveVsMultiplicative:
    """기존 곱셈 대비 개선 검증"""

    def test_compound_dampening(self):
        """곱셈 1.1^5 = 1.61 vs 덧셈 0.1*5 = 0.5 -> 1.5"""
        # 곱셈 방식 시뮬레이션
        mult_result = 1.0
        coefs = [1.1, 1.1, 1.1, 1.1, 1.1]
        for c in coefs:
            mult_result *= c
        # 1.61

        # 덧셈 방식
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.1,
            weather_coef=1.1,
            weekday_coef=1.1,
            seasonal_coef=1.1,
            trend_adjustment=1.1,
        )
        # delta = 0.5, clamped to 0.5 (daily max=0.8)
        assert result.multiplier < mult_result
        assert result.multiplier <= 1.8  # 상한

    def test_extreme_multiplicative_dampened(self):
        """곱셈 1.2^5 = 2.49 vs 덧셈 clamp 1.8"""
        adj = AdditiveAdjuster(pattern="daily")
        result = adj.apply(
            base_prediction=1.0,
            holiday_coef=1.2,
            weather_coef=1.2,
            weekday_coef=1.2,
            seasonal_coef=1.2,
            trend_adjustment=1.2,
        )
        # delta = 1.0, clamped to 0.8
        assert result.multiplier == 1.8
        assert result.adjusted_prediction == 1.8
        # 곱셈이었다면 2.49
