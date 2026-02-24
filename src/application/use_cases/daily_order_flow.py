"""
DailyOrderFlow -- 일일 발주 파이프라인 (유일한 오케스트레이터)

기존 3곳의 중복 오케스트레이션을 하나로 통합:
- src/scheduler/daily_job.py
- scripts/run_auto_order.py
- scripts/run_full_flow.py

파이프라인:
    수집(Collect) -> 예측(Predict) -> 평가(Evaluate) ->
    필터(Filter) -> 조정(Adjust) -> 실행(Execute) ->
    추적(Track) -> 알림(Notify)
"""

from typing import Optional, List, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DailyOrderFlow:
    """일일 발주 파이프라인

    Usage:
        # 스케줄러에서 (전체 파이프라인: AutoOrderSystem 경유)
        ctx = StoreContext.from_store_id("46513")
        flow = DailyOrderFlow(store_ctx=ctx, driver=driver)
        result = flow.run_auto_order(dry_run=False)

        # CLI에서 (서비스 레이어 경유: 예측 -> 필터 -> 실행)
        flow = DailyOrderFlow(store_ctx=ctx, driver=driver)
        result = flow.run(dry_run=True, max_items=3)

        # 웹 API에서 (예측만)
        flow = DailyOrderFlow(store_ctx=ctx)
        predictions = flow.predict_only(min_order_qty=0)
    """

    def __init__(self, store_ctx=None, driver=None, use_improved_predictor: bool = True):
        """초기화

        Args:
            store_ctx: StoreContext 인스턴스
            driver: Selenium WebDriver (None이면 수집/실행 스킵)
            use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)
        """
        self.store_ctx = store_ctx
        self.driver = driver
        self.use_improved_predictor = use_improved_predictor
        self._result = {
            "success": False,
            "store_id": store_ctx.store_id if store_ctx else None,
            "stages": {},
            "errors": [],
        }

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    # ==================================================================
    # 메인 실행 메서드: run_auto_order (AutoOrderSystem 경유, 운영 경로)
    # ==================================================================

    def run_auto_order(
        self,
        dry_run: bool = True,
        min_order_qty: int = 1,
        max_items: Optional[int] = None,
        prefetch_pending: bool = True,
        max_pending_items: int = 200,
        collect_fail_reasons: bool = True,
        skip_exclusion_fetch: bool = False,
    ) -> Dict[str, Any]:
        """AutoOrderSystem 경유 전체 파이프라인 실행

        AutoOrderSystem.run_daily_order()를 사용하여 운영 환경과 동일한
        파이프라인을 실행합니다:
            예측 -> 사전평가 -> 미입고 조회 -> CUT/미취급 필터 ->
            발주 실행 -> 화면 정리

        Args:
            dry_run: True면 실제 발주 실행하지 않음
            min_order_qty: 최소 발주 수량
            max_items: 최대 발주 항목 수 (None=제한없음)
            prefetch_pending: True면 미입고 수량 사전 조회 (중복 발주 방지)
            max_pending_items: 미입고 조회 최대 상품 수
            collect_fail_reasons: True면 실패 건 발생 시 사유 수집
            skip_exclusion_fetch: True면 자동/스마트발주 목록 사이트 재조회 건너뜀

        Returns:
            실행 결과 딕셔너리
        """
        logger.info(
            f"=== DailyOrderFlow.run_auto_order 시작 ==="
            f" store={self.store_id},"
            f" dry_run={dry_run}, min_order_qty={min_order_qty},"
            f" max_items={max_items}, prefetch_pending={prefetch_pending}"
        )

        try:
            order_result = self._stage_auto_order(
                dry_run=dry_run,
                min_order_qty=min_order_qty,
                max_items=max_items,
                prefetch_pending=prefetch_pending,
                max_pending_items=max_pending_items,
                skip_exclusion_fetch=skip_exclusion_fetch,
            )

            # 실패 사유 수집
            fail_count = order_result.get("fail_count", 0)
            if collect_fail_reasons and fail_count > 0 and self.driver:
                self._collect_fail_reasons()

            self._result["success"] = True
            self._result["execution"] = order_result
            self._result["success_count"] = order_result.get("success_count", 0)
            self._result["fail_count"] = fail_count

        except Exception as e:
            logger.error(f"DailyOrderFlow.run_auto_order 실패: {e}", exc_info=True)
            self._result["errors"].append(str(e))

        logger.info(
            f"=== DailyOrderFlow.run_auto_order 완료 ==="
            f" success={self._result['success']},"
            f" success_count={self._result.get('success_count', 0)},"
            f" fail_count={self._result.get('fail_count', 0)}"
        )
        return self._result

    # ==================================================================
    # 서비스 레이어 경유 실행 (기존 호환, 경량 경로)
    # ==================================================================

    def run(
        self,
        collect_sales: bool = True,
        dry_run: bool = True,
        max_items: int = 0,
        min_order_qty: int = 1,
        categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """서비스 레이어 경유 파이프라인 실행

        PredictionService + OrderService를 통한 경량 파이프라인입니다.
        운영 환경에서는 run_auto_order()를 사용하세요.

        Args:
            collect_sales: 판매 데이터 수집 여부
            dry_run: True면 실제 발주 실행하지 않음
            max_items: 최대 발주 항목 수 (0=제한없음)
            min_order_qty: 최소 발주 수량
            categories: 특정 카테고리만 처리 (None=전체)

        Returns:
            실행 결과 딕셔너리
        """
        logger.info(
            f"=== DailyOrderFlow 시작 ==="
            f" store={self.store_id},"
            f" collect={collect_sales}, dry_run={dry_run},"
            f" max_items={max_items}, min_order_qty={min_order_qty}"
        )

        try:
            # Stage 1: 판매 데이터 수집
            if collect_sales and self.driver:
                self._stage_collect()

            # Stage 2: 예측
            predictions = self._stage_predict(
                min_order_qty=min_order_qty,
                categories=categories,
            )

            # Stage 3: 필터링
            filtered = self._stage_filter(predictions)

            # Stage 4: 실행
            if not dry_run and self.driver:
                exec_result = self._stage_execute(
                    filtered, max_items=max_items
                )
            else:
                exec_result = {
                    "dry_run": True,
                    "total": len(filtered),
                    "success_count": len(filtered),
                    "fail_count": 0,
                }
                logger.info(f"[DRY-RUN] 발주 대상: {len(filtered)}개 상품")

            # Stage 5: 추적 + 알림
            self._stage_track(exec_result)

            self._result["success"] = True
            self._result["predictions_count"] = len(predictions)
            self._result["filtered_count"] = len(filtered)
            self._result["execution"] = exec_result

        except Exception as e:
            logger.error(f"DailyOrderFlow 실패: {e}", exc_info=True)
            self._result["errors"].append(str(e))

        logger.info(
            f"=== DailyOrderFlow 완료 ==="
            f" success={self._result['success']},"
            f" predictions={self._result.get('predictions_count', 0)},"
            f" filtered={self._result.get('filtered_count', 0)}"
        )
        return self._result

    def predict_only(self, min_order_qty: int = 0) -> List[Dict[str, Any]]:
        """예측만 수행 (웹 API용)

        Returns:
            예측 결과 리스트
        """
        from src.application.services.prediction_service import PredictionService

        service = PredictionService(store_ctx=self.store_ctx)
        return service.predict_all(min_order_qty=min_order_qty)

    # ------------------------------------------------------------------
    # Internal stages
    # ------------------------------------------------------------------

    def _stage_auto_order(
        self,
        dry_run: bool = True,
        min_order_qty: int = 1,
        max_items: Optional[int] = None,
        prefetch_pending: bool = True,
        max_pending_items: int = 200,
        skip_exclusion_fetch: bool = False,
    ) -> Dict[str, Any]:
        """AutoOrderSystem 경유 발주 실행

        AutoOrderSystem.run_daily_order()를 호출하여
        예측 -> 사전평가 -> 미입고 -> 필터 -> 실행을 일괄 처리합니다.
        """
        logger.info(
            f"[Stage: AutoOrder] 시작"
            f" (predictor={'improved' if self.use_improved_predictor else 'legacy'})"
        )
        from src.order.auto_order import AutoOrderSystem

        system = AutoOrderSystem(
            driver=self.driver,
            use_improved_predictor=self.use_improved_predictor,
            store_id=self.store_id,
        )
        try:
            result = system.run_daily_order(
                min_order_qty=min_order_qty,
                max_items=max_items,
                dry_run=dry_run,
                prefetch_pending=prefetch_pending,
                skip_exclusion_fetch=skip_exclusion_fetch,
            )

            self._result["stages"]["auto_order"] = {
                "success": True,
                "result_summary": {
                    "success_count": result.get("success_count", 0),
                    "fail_count": result.get("fail_count", 0),
                    "cut_count": result.get("cut_count", 0),
                },
            }

            logger.info(
                f"[Stage: AutoOrder] 완료:"
                f" success={result.get('success_count', 0)},"
                f" fail={result.get('fail_count', 0)}"
            )
            return result

        finally:
            system.close()

    def _stage_collect(self):
        """Stage 1: 판매 데이터 수집"""
        logger.info("[Stage 1] 판매 데이터 수집 시작")
        try:
            from src.scheduler.daily_job import DailyCollectionJob

            job = DailyCollectionJob(store_id=self.store_id)
            # driver가 있으면 SalesCollector를 통해 수집
            result = job.run()
            self._result["stages"]["collect"] = {
                "success": True,
                "result": result,
            }
            logger.info("[Stage 1] 판매 데이터 수집 완료")
        except Exception as e:
            logger.warning(f"[Stage 1] 수집 실패 (계속 진행): {e}")
            self._result["stages"]["collect"] = {
                "success": False,
                "error": str(e),
            }

    def _stage_predict(
        self,
        min_order_qty: int = 1,
        categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Stage 2: 예측"""
        logger.info("[Stage 2] 예측 시작")
        from src.application.services.prediction_service import PredictionService

        service = PredictionService(store_ctx=self.store_ctx)
        predictions = service.predict_all(
            min_order_qty=min_order_qty,
            categories=categories,
        )
        self._result["stages"]["predict"] = {
            "success": True,
            "count": len(predictions),
        }
        logger.info(f"[Stage 2] 예측 완료: {len(predictions)}개 상품")
        return predictions

    def _stage_filter(
        self, predictions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Stage 3: 필터링"""
        logger.info("[Stage 3] 필터링 시작")
        from src.application.services.order_service import OrderService
        from src.application.services.inventory_service import InventoryService

        inv_service = InventoryService(store_ctx=self.store_ctx)
        order_service = OrderService(store_ctx=self.store_ctx)

        filtered = order_service.filter_items(
            predictions,
            unavailable_items=inv_service.get_unavailable_items(),
            cut_items=inv_service.get_cut_items(),
        )
        self._result["stages"]["filter"] = {
            "success": True,
            "before": len(predictions),
            "after": len(filtered),
        }
        logger.info(
            f"[Stage 3] 필터링 완료: {len(predictions)} -> {len(filtered)}개"
        )
        return filtered

    def _stage_execute(
        self,
        items: List[Dict[str, Any]],
        max_items: int = 0,
    ) -> Dict[str, Any]:
        """Stage 4: 발주 실행"""
        logger.info(f"[Stage 4] 발주 실행 시작: {len(items)}개 상품")
        from src.application.services.order_service import OrderService

        service = OrderService(store_ctx=self.store_ctx)
        result = service.execute(
            items,
            driver=self.driver,
            dry_run=False,
            max_items=max_items,
        )
        self._result["stages"]["execute"] = result
        logger.info(
            f"[Stage 4] 발주 실행 완료:"
            f" success={result.get('success_count', 0)},"
            f" fail={result.get('fail_count', 0)}"
        )

        # 실패 건수가 있으면 실패 사유 수집
        if result.get("fail_count", 0) > 0 and self.driver:
            self._collect_fail_reasons()

        return result

    def _stage_track(self, exec_result: Dict[str, Any]):
        """Stage 5: 추적 + 알림"""
        logger.info("[Stage 5] 추적/알림")
        # 카카오 알림은 별도 설정이 필요하므로 Phase 8에서 통합
        self._result["stages"]["track"] = {"success": True}

    def _collect_fail_reasons(self):
        """발주 실패 사유 수집"""
        try:
            from src.collectors.fail_reason_collector import FailReasonCollector

            collector = FailReasonCollector(self.driver, store_id=self.store_id)
            reasons = collector.collect_all()
            self._result["fail_reasons"] = reasons
            logger.info(f"실패 사유 수집: {len(reasons)}건")
        except Exception as e:
            logger.warning(f"실패 사유 수집 실패: {e}")
