"""
예측 설정 호환 모듈 (deprecated)

이 모듈은 src.prediction.prediction_config으로 통합되었습니다.
새 코드에서는 prediction_config를 직접 사용하세요.
"""
import warnings
warnings.warn(
    "src.prediction.config은 deprecated입니다. "
    "src.prediction.prediction_config을 사용하세요.",
    DeprecationWarning,
    stacklevel=2,
)

from src.prediction.prediction_config import (  # noqa: F401, E402
    # 상수
    CATEGORY_CONFIG,
    DEFAULT_CONFIG,
    WEEKDAY_FACTORS,
    ML_SWITCH_CONDITIONS,
    # 함수
    get_category_config,
    get_weekday_factor,
    calculate_weekday_factors_from_db,
    get_weekday_factor_from_db,
)
