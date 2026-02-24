"""
BatchCollectFlow -- 대량 상품정보 수집 플로우

상품 마스터 정보(원가, 마진, 유통기한)를 대량 수집합니다.
기존 run_scheduler.py의 bulk_collect_wrapper를 통합합니다.
"""

from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BatchCollectFlow:
    """대량 수집 플로우

    Usage:
        flow = BatchCollectFlow(store_ctx=ctx, driver=driver)
        result = flow.run()
    """

    def __init__(self, store_ctx=None, driver=None):
        self.store_ctx = store_ctx
        self.driver = driver

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def run(self, max_items: int = 0, resume: bool = True) -> Dict[str, Any]:
        """대량 상품정보 수집 실행

        Args:
            max_items: 최대 수집 수 (0=제한없음)
            resume: 이전 중단 지점부터 재개

        Returns:
            {success: bool, collected: int, ...}
        """
        try:
            from scripts.collect_all_product_details import run_bulk_collect

            result = run_bulk_collect(
                max_items=max_items,
                resume=resume,
                store_id=self.store_id,
            )

            if result.get("success"):
                logger.info(
                    f"BatchCollect 완료: store={self.store_id},"
                    f" collected={result.get('collected', 0)}/"
                    f"{result.get('total', 0)},"
                    f" failed={result.get('failed', 0)}"
                )
            else:
                logger.error(
                    f"BatchCollect 실패: store={self.store_id},"
                    f" error={result.get('error', 'Unknown')}"
                )
            return result

        except ImportError as e:
            logger.warning(f"BatchCollect 건너뜀 (의존성 부족): {e}")
            return {"success": False, "error": f"의존성 부족: {e}"}
        except Exception as e:
            logger.error(f"BatchCollect 실패: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
