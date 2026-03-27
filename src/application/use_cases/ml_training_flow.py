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

    def run(self, days: int = 90, incremental: bool = False) -> Dict[str, Any]:
        """ML 모델 학습 실행

        Args:
            days: 학습 데이터 기간 (일). incremental=True이면 90일로 자동 설정.
            incremental: True면 증분학습 모드 (90일 윈도우, 성능 보호 게이트 적용)

        Returns:
            {success: bool, models_trained: int, results: {...}}
        """
        try:
            from src.prediction.ml.trainer import MLTrainer

            if incremental:
                days = 90

            mode = f"증분({days}일)" if incremental else f"전체({days}일)"
            trainer = MLTrainer(store_id=self.store_id)
            results = trainer.train_all_groups(days=days, incremental=incremental)

            success_count = sum(
                1 for r in results.values()
                if isinstance(r, dict) and r.get("success")
            )
            gated_count = sum(
                1 for r in results.values()
                if isinstance(r, dict) and r.get("gated")
            )
            logger.info(
                f"MLTraining 완료 ({mode}): store={self.store_id},"
                f" models={success_count}/{len(results)} 그룹 성공"
                + (f", {gated_count}개 롤백" if gated_count else "")
            )
            return {
                "success": True,
                "models_trained": success_count,
                "total_groups": len(results),
                "gated_count": gated_count,
                "incremental": incremental,
                "days": days,
                "results": results,
            }
        except ImportError as e:
            logger.warning(f"MLTraining 건너뜀 (의존성 부족): {e}")
            return {"success": False, "error": f"의존성 부족: {e}"}
        except Exception as e:
            logger.error(f"MLTraining 실패: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
