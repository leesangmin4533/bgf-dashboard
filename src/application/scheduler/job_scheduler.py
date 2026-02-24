"""
MultiStoreRunner -- 다매장 병렬 실행기

기존 run_scheduler.py의 _multi_store_run()을 클래스로 통합합니다.
ThreadPoolExecutor를 사용하여 모든 활성 매장에 대해 작업을 병렬 실행합니다.

매장 간 시차(stagger_seconds)를 두어 ChromeDriver 동시 기동 시
파일 잠금 충돌을 방지합니다.
"""

import time
from typing import Callable, Any, List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 매장 간 시작 간격 (초). ChromeDriver 동시 기동 시 파일 잠금 충돌 방지.
DEFAULT_STAGGER_SECONDS = 5


class MultiStoreRunner:
    """다매장 병렬 실행기

    Usage:
        runner = MultiStoreRunner()
        results = runner.run_parallel(
            task_fn=lambda ctx: DailyOrderFlow(store_ctx=ctx).run(),
            task_name="daily_order"
        )
    """

    def __init__(self, max_workers: int = 4, stagger_seconds: float = DEFAULT_STAGGER_SECONDS):
        """초기화

        Args:
            max_workers: 최대 병렬 워커 수
            stagger_seconds: 매장 간 시작 간격 (초). 첫 매장은 즉시 시작,
                이후 매장은 이 간격만큼 지연 후 시작. 0이면 동시 시작.
        """
        self.max_workers = max_workers
        self.stagger_seconds = stagger_seconds

    def run_parallel(
        self,
        task_fn: Callable,
        task_name: str = "task",
    ) -> List[Dict[str, Any]]:
        """모든 활성 매장에 대해 시차 병렬 실행

        첫 매장은 즉시 시작, 이후 매장은 stagger_seconds 간격으로 submit.
        submit 후에는 병렬로 실행되므로 전체 소요시간은 거의 동일.

        Args:
            task_fn: StoreContext를 받는 작업 함수
            task_name: 작업 이름 (로깅용)

        Returns:
            [{store_id, success, result, error}, ...]
        """
        from src.settings.store_context import StoreContext

        stores = StoreContext.get_all_active()
        if not stores:
            logger.warning(f"[{task_name}] 활성 매장 없음")
            return []

        stagger_info = f", stagger={self.stagger_seconds}s" if self.stagger_seconds > 0 else ""
        logger.info(
            f"[{task_name}] {len(stores)}개 매장 병렬 실행 시작"
            f" (max_workers={self.max_workers}{stagger_info})"
        )

        results = []
        workers = min(self.max_workers, len(stores))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for idx, ctx in enumerate(stores):
                if idx > 0 and self.stagger_seconds > 0:
                    logger.info(
                        f"[{task_name}] 다음 매장 대기: {self.stagger_seconds}s"
                        f" ({idx}/{len(stores)-1})"
                    )
                    time.sleep(self.stagger_seconds)
                future = executor.submit(self._run_single, task_fn, ctx, task_name)
                futures[future] = ctx

            for future in as_completed(futures):
                ctx = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(
                        f"[{task_name}] {ctx.store_id} 실패: {e}",
                        exc_info=True,
                    )
                    results.append({
                        "store_id": ctx.store_id,
                        "success": False,
                        "error": str(e),
                    })

        success_count = sum(1 for r in results if r.get("success"))
        logger.info(
            f"[{task_name}] 완료:"
            f" {success_count}/{len(stores)} 성공"
        )
        return results

    def _run_single(
        self,
        task_fn: Callable,
        store_ctx: Any,
        task_name: str,
    ) -> Dict[str, Any]:
        """단일 매장 실행

        Args:
            task_fn: 작업 함수
            store_ctx: StoreContext
            task_name: 작업 이름

        Returns:
            {store_id, success, result}
        """
        store_id = store_ctx.store_id
        logger.info(f"[{task_name}] {store_id} 시작")
        try:
            result = task_fn(store_ctx)
            logger.info(f"[{task_name}] {store_id} 완료")
            return {
                "store_id": store_id,
                "success": True,
                "result": result,
            }
        except Exception as e:
            logger.error(f"[{task_name}] {store_id} 실패: {e}")
            return {
                "store_id": store_id,
                "success": False,
                "error": str(e),
            }
