"""덧셈 기반 계수 통합기

기존 곱셈 파이프라인 (과잉 증폭 원인):
  adjusted = base * holiday * weather * weekday * season * assoc * trend

덧셈 파이프라인:
  delta_sum = (holiday-1) + (weather-1) + ... + (trend-1)
  adjusted = base * (1.0 + clamp(delta_sum, -cap, +cap))

패턴별 clamp 범위로 과잉 증폭 구조적 차단.
"""

from dataclasses import dataclass, field
from typing import Dict

from src.utils.logger import get_logger

logger = get_logger(__name__)


# 패턴별 clamp 범위
ADDITIVE_CLAMP_RANGES = {
    "daily":        (-0.5, +0.8),
    "frequent":     (-0.4, +0.5),
    "intermittent": (-0.3, +0.3),
    "slow":         (0.0, 0.0),
}


@dataclass
class AdditiveResult:
    """덧셈 보정 결과"""
    adjusted_prediction: float
    delta_sum: float
    clamped_delta: float
    multiplier: float
    deltas: Dict[str, float] = field(default_factory=dict)


class AdditiveAdjuster:
    """덧셈 기반 계수 통합기

    Args:
        pattern: 수요 패턴 (daily/frequent/intermittent/slow)
    """

    def __init__(self, pattern: str = "daily"):
        self.pattern = pattern
        self.clamp_min, self.clamp_max = ADDITIVE_CLAMP_RANGES.get(
            pattern, ADDITIVE_CLAMP_RANGES["daily"]
        )

    def apply(
        self,
        base_prediction: float,
        holiday_coef: float = 1.0,
        weather_coef: float = 1.0,
        food_weather_cross_coef: float = 1.0,
        weekday_coef: float = 1.0,
        seasonal_coef: float = 1.0,
        association_boost: float = 1.0,
        trend_adjustment: float = 1.0,
    ) -> AdditiveResult:
        """계수들을 덧셈 방식으로 통합

        Args:
            base_prediction: 기본 예측값 (WMA 또는 Croston)
            각 coef: 기존 곱셈 계수 (1.0 = 변화없음)

        Returns:
            AdditiveResult
        """
        deltas = {
            "holiday": holiday_coef - 1.0,
            "weather": weather_coef - 1.0,
            "food_weather_cross": food_weather_cross_coef - 1.0,
            "weekday": weekday_coef - 1.0,
            "seasonal": seasonal_coef - 1.0,
            "association": association_boost - 1.0,
            "trend": trend_adjustment - 1.0,
        }

        # slow 패턴: 계수 적용 안 함
        if self.pattern == "slow":
            return AdditiveResult(
                adjusted_prediction=base_prediction,
                delta_sum=0.0, clamped_delta=0.0, multiplier=1.0,
                deltas=deltas,
            )

        # delta 합산 + clamp
        delta_sum = sum(deltas.values())
        clamped_delta = max(self.clamp_min, min(self.clamp_max, delta_sum))
        multiplier = 1.0 + clamped_delta

        # 바닥값 방어
        adjusted = base_prediction * multiplier
        if adjusted < 0:
            adjusted = base_prediction * 0.15

        return AdditiveResult(
            adjusted_prediction=round(adjusted, 3),
            delta_sum=round(delta_sum, 4),
            clamped_delta=round(clamped_delta, 4),
            multiplier=round(multiplier, 4),
            deltas=deltas,
        )
