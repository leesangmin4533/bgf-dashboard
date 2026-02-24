"""
피처 엔지니어링 -- 래그/롤링 피처 계산

기존 코드는 src/prediction/features/ 아래에 있으며,
새 경로(src/domain/prediction/features/)에서도 접근 가능하도록 re-export합니다.

Usage:
    from src.domain.prediction.features import LagFeatures, RollingFeatures
    from src.domain.prediction.features import FeatureCalculator
"""

import warnings
warnings.warn("Deprecated: use src.prediction.features", DeprecationWarning, stacklevel=2)

from src.prediction.features.lag_features import LagFeatures  # noqa: F401
from src.prediction.features.rolling_features import RollingFeatures  # noqa: F401
from src.prediction.features.feature_calculator import FeatureCalculator  # noqa: F401
