"""Croston/TSB 간헐 수요 예측 모델

TSB(Teunter-Syntetos-Babai) 변형:
- 수요 크기(z)와 수요 확률(prob)을 분리 추정
- 판매 0인 날을 정상으로 처리 (WMA는 0을 "하락"으로 봄)
- 구식 상품의 확률 자연 감쇄 (0판매 기간이 길수록 prob → 0)

사용 대상: DemandPattern.INTERMITTENT (sell_day_ratio 15~39%)
"""

from dataclasses import dataclass
from typing import List, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CrostonResult:
    """Croston 예측 결과"""
    forecast: float
    demand_size: float
    demand_probability: float
    intervals_estimate: float
    method: str


# 기본 파라미터
CROSTON_ALPHA = 0.15
CROSTON_BETA = 0.10
MIN_HISTORY_POINTS = 3
FORECAST_FLOOR = 0.05


class CrostonPredictor:
    """Croston/TSB 간헐 수요 예측기

    Args:
        alpha: 수요 크기 스무딩 계수 (0.05~0.3)
        beta: 수요 확률 스무딩 계수 (0.05~0.2)
    """

    def __init__(self, alpha: float = CROSTON_ALPHA, beta: float = CROSTON_BETA):
        self.alpha = alpha
        self.beta = beta

    def predict(self, history: List[int]) -> CrostonResult:
        """판매 이력에서 일평균 수요 예측

        Args:
            history: 일별 판매량 리스트 (오래된것→최신 순)

        Returns:
            CrostonResult
        """
        if not history:
            return CrostonResult(
                forecast=0.0, demand_size=0.0, demand_probability=0.0,
                intervals_estimate=0.0, method="fallback"
            )

        # 판매 발생 시점 추출
        demand_points = [(i, v) for i, v in enumerate(history) if v > 0]

        if len(demand_points) < MIN_HISTORY_POINTS:
            # fallback: 단순 평균
            total = sum(history)
            n = len(history)
            avg = total / n if n > 0 else 0.0
            dp_count = max(len(demand_points), 1)
            return CrostonResult(
                forecast=round(max(avg, FORECAST_FLOOR) if avg > 0 else 0.0, 3),
                demand_size=round(total / dp_count, 3),
                demand_probability=round(len(demand_points) / n, 4) if n > 0 else 0.0,
                intervals_estimate=round(n / dp_count, 1),
                method="fallback"
            )

        # TSB 초기값
        z = float(demand_points[0][1])
        if len(demand_points) > 1:
            initial_interval = demand_points[1][0] - demand_points[0][0]
            prob = 1.0 / max(initial_interval, 1)
        else:
            prob = 1.0 / len(history)

        # TSB 스무딩
        prev_demand_idx = demand_points[0][0]
        for idx, qty in demand_points[1:]:
            # 수요 크기 업데이트
            z = self.alpha * qty + (1 - self.alpha) * z

            # 수요 확률 업데이트 (TSB: 직접 스무딩)
            prob = self.beta * 1.0 + (1 - self.beta) * prob

            prev_demand_idx = idx

        # 마지막 수요 이후 0판매 기간의 확률 감쇄
        days_since_last = len(history) - 1 - prev_demand_idx
        for _ in range(days_since_last):
            prob = (1 - self.beta) * prob

        # TSB forecast
        forecast = max(z * prob, FORECAST_FLOOR)

        # 수요 간격 추정
        total_span = demand_points[-1][0] - demand_points[0][0]
        intervals = total_span / (len(demand_points) - 1) if len(demand_points) > 1 else len(history)

        return CrostonResult(
            forecast=round(forecast, 3),
            demand_size=round(z, 3),
            demand_probability=round(min(prob, 1.0), 4),
            intervals_estimate=round(intervals, 1),
            method="tsb"
        )

    def predict_with_safety(self, history: List[int], order_interval: int = 2) -> Tuple[float, float]:
        """예측값 + 안전재고 반환

        Args:
            history: 일별 판매량 리스트
            order_interval: 발주 간격 (일)

        Returns:
            (daily_forecast, safety_stock)
        """
        result = self.predict(history)

        if result.demand_probability > 0:
            safety_stock = result.demand_size * 0.5
        else:
            safety_stock = 0.0

        period_demand = result.forecast * order_interval
        safety_stock = max(safety_stock, period_demand * 0.3)

        return result.forecast, round(safety_stock, 2)
