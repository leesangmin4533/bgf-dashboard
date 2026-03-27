"""CrostonPredictor 테스트

TSB(Teunter-Syntetos-Babai) 간헐수요 예측 모델 검증
"""

import pytest
from src.prediction.croston_predictor import (
    CrostonPredictor,
    CrostonResult,
    CROSTON_ALPHA,
    CROSTON_BETA,
    MIN_HISTORY_POINTS,
    FORECAST_FLOOR,
)


class TestCrostonBasic:
    """기본 동작 테스트"""

    def setup_method(self):
        self.predictor = CrostonPredictor()

    def test_empty_history(self):
        """빈 이력 -> 0 예측"""
        result = self.predictor.predict([])
        assert result.forecast == 0.0
        assert result.method == "fallback"

    def test_all_zeros(self):
        """전부 0 -> fallback 0"""
        result = self.predictor.predict([0] * 60)
        assert result.forecast == 0.0
        assert result.method == "fallback"

    def test_single_demand(self):
        """1회 판매 -> fallback"""
        history = [0] * 30 + [5] + [0] * 29
        result = self.predictor.predict(history)
        assert result.method == "fallback"
        assert result.forecast > 0

    def test_two_demands(self):
        """2회 판매 -> fallback"""
        history = [0] * 20 + [3] + [0] * 19 + [4] + [0] * 19
        result = self.predictor.predict(history)
        assert result.method == "fallback"

    def test_three_demands_tsb(self):
        """3회 판매 -> TSB"""
        history = [0] * 10 + [3] + [0] * 10 + [4] + [0] * 10 + [2] + [0] * 27
        result = self.predictor.predict(history)
        assert result.method == "tsb"
        assert result.forecast > 0
        assert result.demand_size > 0
        assert 0 < result.demand_probability <= 1.0


class TestCrostonAccuracy:
    """정확도 검증"""

    def setup_method(self):
        self.predictor = CrostonPredictor()

    def test_regular_intermittent(self):
        """규칙적 간헐수요: 7일마다 판매"""
        history = []
        for _ in range(8):
            history.extend([5, 0, 0, 0, 0, 0, 0])  # 7일마다 5개
        history = history[:60]

        result = self.predictor.predict(history)
        assert result.method == "tsb"
        # 실제 일평균: 5/7 = 0.71
        assert 0.2 < result.forecast < 2.0
        assert result.demand_size > 3.0  # 판매일 평균 ~5
        assert result.intervals_estimate > 5  # ~7일 간격

    def test_decreasing_demand(self):
        """감소 추세 -> 확률 감쇄"""
        # 초반 활발, 후반 정적
        history = [3, 0, 0, 2, 0, 4, 0, 0, 3, 0] + [0] * 50
        result = self.predictor.predict(history)
        assert result.method == "tsb"
        # 50일간 0이므로 확률 감쇄
        assert result.demand_probability < 0.3

    def test_recent_spike(self):
        """최근 급증 -> 크기 반영"""
        history = [0] * 30 + [1, 0, 0, 1, 0, 0, 10, 0, 0, 8] + [0] * 20
        result = self.predictor.predict(history)
        # 최근 큰 수요로 demand_size 상승
        assert result.demand_size > 2.0

    def test_forecast_floor(self):
        """최소 예측값 보장"""
        # 아주 희소한 수요
        history = [0] * 55 + [1, 0, 0, 1, 0]
        result = self.predictor.predict(history)
        assert result.forecast >= FORECAST_FLOOR


class TestCrostonSafety:
    """안전재고 계산 테스트"""

    def setup_method(self):
        self.predictor = CrostonPredictor()

    def test_safety_basic(self):
        """기본 안전재고"""
        history = [0] * 10 + [5] + [0] * 10 + [3] + [0] * 10 + [4] + [0] * 27
        forecast, safety = self.predictor.predict_with_safety(history, order_interval=3)
        assert forecast > 0
        assert safety >= 0
        assert safety >= forecast * 3 * 0.3  # 최소 기간수요 30%

    def test_safety_longer_interval(self):
        """발주 간격 길수록 안전재고 증가"""
        history = [0] * 10 + [5] + [0] * 10 + [3] + [0] * 10 + [4] + [0] * 27
        _, safety_2 = self.predictor.predict_with_safety(history, order_interval=2)
        _, safety_7 = self.predictor.predict_with_safety(history, order_interval=7)
        assert safety_7 >= safety_2

    def test_safety_zero_demand(self):
        """판매 없음 -> 안전재고 0"""
        forecast, safety = self.predictor.predict_with_safety([0] * 60)
        assert forecast == 0.0
        assert safety == 0.0


class TestCrostonParameters:
    """파라미터 테스트"""

    def test_high_alpha(self):
        """높은 alpha -> 최근 수요에 민감"""
        history = [0] * 10 + [1] + [0] * 10 + [1] + [0] * 10 + [10] + [0] * 27
        pred_low = CrostonPredictor(alpha=0.05).predict(history)
        pred_high = CrostonPredictor(alpha=0.4).predict(history)
        # 높은 alpha -> 최근 10에 더 민감 -> 더 큰 demand_size
        assert pred_high.demand_size > pred_low.demand_size

    def test_high_beta(self):
        """높은 beta -> 확률 감쇄 빠름"""
        history = [3, 0, 0, 2, 0, 4, 0, 0, 3, 0] + [0] * 50
        pred_low = CrostonPredictor(beta=0.05).predict(history)
        pred_high = CrostonPredictor(beta=0.3).predict(history)
        # 높은 beta -> 50일 0판매에서 확률 더 빠르게 감쇄
        assert pred_high.demand_probability < pred_low.demand_probability
