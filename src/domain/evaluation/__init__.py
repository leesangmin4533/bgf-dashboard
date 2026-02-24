"""
평가 도메인 -- 사전 평가, 보정, 리포트

기존 코드는 src/prediction/ 아래에 있으며,
새 경로(src/domain/evaluation/)에서도 접근 가능하도록 re-export합니다.

Usage:
    from src.domain.evaluation import PreOrderEvaluator
    from src.domain.evaluation import EvalCalibrator
"""

import warnings
warnings.warn("Deprecated: use src.prediction.pre_order_evaluator / eval_calibrator / eval_reporter", DeprecationWarning, stacklevel=2)

from src.prediction.pre_order_evaluator import PreOrderEvaluator  # noqa: F401
from src.prediction.eval_calibrator import EvalCalibrator  # noqa: F401
from src.prediction.eval_reporter import EvalReporter  # noqa: F401
