"""
예측 하이퍼파라미터 -- prediction_config.py re-export

기존 prediction_config.py의 PREDICTION_PARAMS, ORDER_ADJUSTMENT_RULES를
새 경로에서 접근 가능하도록 re-export합니다.

Usage:
    from src.domain.prediction.prediction_params import PREDICTION_PARAMS
    from src.domain.prediction.prediction_params import ORDER_ADJUSTMENT_RULES
"""

import warnings
warnings.warn("Deprecated: use src.prediction.prediction_config", DeprecationWarning, stacklevel=2)

from src.prediction.prediction_config import PREDICTION_PARAMS  # noqa: F401
from src.prediction.prediction_config import ORDER_ADJUSTMENT_RULES  # noqa: F401
