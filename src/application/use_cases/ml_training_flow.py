"""
MLTrainingFlow -- ML 모델 학습 플로우

카테고리별 ML 모델을 학습합니다.
기존 run_scheduler.py의 ml_train_wrapper를 통합합니다.
"""

from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MLTrainingFlow:
    """ML 학습 플로우

    Usage:
        flow = MLTrainingFlow(store_ctx=ctx)
        result = flow.run(days=90)
    """

    def __init__(self, store_ctx=None):
        self.store_ctx = store_ctx

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def run(self, days: int = 90) -> Dict[str, Any]:
        """ML 모델 학습 실행

        Args:
            days: 학습 데이터 기간 (일)

        Returns:
            {success: bool, models_trained: int, results: {...}}
        """
        try:
            from src.prediction.ml.trainer import MLTrainer

            trainer = MLTrainer(store_id=self.store_id)
            results = trainer.train_all_groups(days=days)

            success_count = sum(
                1 for r in results.values()
                if isinstance(r, dict) and r.get("success")
            )
            logger.info(
                f"MLTraining 완료: store={self.store_id},"
                f" models={success_count}/{len(results)} 그룹 성공"
            )
            return {
                "success": True,
                "models_trained": success_count,
                "total_groups": len(results),
                "results": results,
            }
        except ImportError as e:
            logger.warning(f"MLTraining 건너뜀 (의존성 부족): {e}")
            return {"success": False, "error": f"의존성 부족: {e}"}
        except Exception as e:
            logger.error(f"MLTraining 실패: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
