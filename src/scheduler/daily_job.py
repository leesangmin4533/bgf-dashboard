"""
일별 데이터 수집 작업
- 매일 아침 7시에 전날 + 당일 데이터 수집
- 한 번 로그인으로 수집 + 발주까지 처리
- DB 저장 및 로깅
- 중복 데이터는 자동으로 업데이트 (UPSERT)
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

from src.collectors.sales_collector import SalesCollector
from src.collectors.calendar_collector import save_calendar_info
from src.infrastructure.database.repos import SalesRepository, ExternalFactorRepository
from src.notification.kakao_notifier import KakaoNotifier, DEFAULT_REST_API_KEY
from src.analysis.daily_report import DailyReport
from src.alert.expiry_checker import run_expiry_check_and_alert
from src.prediction.improved_predictor import PredictionLogger
from src.prediction.eval_calibrator import EvalCalibrator
from src.prediction.eval_reporter import EvalReporter
from src.prediction.accuracy import AccuracyTracker

from src.settings.timing import (
    DAILY_JOB_AFTER_CLOSE_TAB, BACKFILL_BETWEEN_DATES,
)
from src.settings.constants import (
    LOG_SEPARATOR_WIDE, LOG_SEPARATOR_THIN,
    WEEKLY_REPORT_DAYS,
    DEFAULT_STORE_ID,
)
from src.collectors.fail_reason_collector import FailReasonCollector
from src.collectors.manual_order_detector import run_manual_order_detection
from src.collectors.receiving_collector import ReceivingCollector


class DailyCollectionJob:
    """일별 수집 작업"""

    def __init__(self, store_id: str = DEFAULT_STORE_ID) -> None:
        """일별 수집 작업 초기화 (SalesRepository, ExternalFactorRepository 생성)

        Args:
            store_id: 매장 코드
        """
        self.store_id = store_id
        self.collector: Optional[Any] = None
        self.repository: Any = SalesRepository(store_id=self.store_id)
        self.weather_repo: Any = ExternalFactorRepository()

    def run(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        일별 수집 실행 (단일 날짜)

        Args:
            target_date: 수집 대상 날짜 (YYYY-MM-DD)
                         기본값: 어제 날짜

        Returns:
            실행 결과
        """
        if target_date is None:
            yesterday = datetime.now() - timedelta(days=1)
            target_date = yesterday.strftime("%Y-%m-%d")

        logger.info(LOG_SEPARATOR_WIDE)
        logger.info("Starting daily collection")
        logger.info(f"Target date: {target_date}")
        logger.info(f"Start time: {datetime.now().isoformat()}")
        logger.info(LOG_SEPARATOR_WIDE)

        start_time = time.time()
        self.collector = SalesCollector(store_id=self.store_id)

        try:
            # 수집 실행
            result = self.collector.collect_with_retry(target_date)

            if result["success"]:
                # DB 저장
                stats = self.repository.save_daily_sales(
                    sales_data=result["data"],
                    sales_date=target_date,
                    store_id=self.store_id,
                    collected_at=result["collected_at"]
                )

                self.repository.log_collection(
                    collected_at=result["collected_at"],
                    sales_date=target_date,
                    stats=stats,
                    status="success",
                    duration=result["duration"]
                )

                logger.info(f"DB saved: {stats['total']} items")
                result["db_stats"] = stats

                # 날씨/캘린더 저장
                weather = result.get("weather")
                if weather:
                    self._save_weather_data(target_date, weather)

                try:
                    save_calendar_info(target_date, self.weather_repo)
                except Exception as e:
                    logger.error(f"Calendar save failed: {e}")

            result["duration"] = time.time() - start_time
            return result

        finally:
            if self.collector:
                self.collector.close()
                self.collector = None

    def run_optimized(
        self,
        run_auto_order: bool = True,
        use_improved_predictor: bool = True
    ) -> Dict[str, Any]:
        """
        최적화된 전체 플로우 실행
        - 한 번 로그인으로 전날+당일 수집 + 자동 발주

        Args:
            run_auto_order: 자동 발주 실행 여부
            use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)

        Returns:
            실행 결과
        """
        today = datetime.now()
        yesterday = today - timedelta(days=1)

        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        logger.info(LOG_SEPARATOR_WIDE)
        logger.info("Optimized flow started")
        logger.info(f"Dates: {yesterday_str}, {today_str}")
        logger.info(f"Auto-order: {run_auto_order}")
        logger.info(f"Predictor: {'Improved (31-day)' if use_improved_predictor else 'Legacy'}")
        logger.info(LOG_SEPARATOR_WIDE)

        start_time = time.time()
        self.collector = SalesCollector(store_id=self.store_id)
        collection_results = {}
        order_result = None
        fail_reason_result = None

        try:
            # 1. 데이터 수집 (한 번 로그인으로 여러 날짜)
            logger.info("[Phase 1] Data Collection")
            logger.info(LOG_SEPARATOR_THIN)

            dates_to_collect = [yesterday_str, today_str]

            def save_callback(date_str: str, data: List[Dict[str, Any]]) -> Dict[str, Any]:
                stats = self.repository.save_daily_sales(
                    sales_data=data,
                    sales_date=date_str,
                    store_id=self.store_id,
                    collected_at=datetime.now().isoformat()
                )
                self.repository.log_collection(
                    collected_at=datetime.now().isoformat(),
                    sales_date=date_str,
                    stats=stats,
                    status="success",
                    duration=0
                )
                return stats

            collection_results = self.collector.collect_multiple_dates(
                dates=dates_to_collect,
                save_callback=save_callback
            )

            # 날씨/캘린더 저장
            weather = self.collector.weather_data
            if weather:
                self._save_weather_data(today_str, weather)
            try:
                save_calendar_info(today_str, self.weather_repo)
            except Exception as e:
                logger.warning(f"스케줄러 정리 실패: {e}")

            # 수집 성공 여부 확인
            collection_success = any(
                r.get("success") for r in collection_results.values()
            )
            logger.info(f"수집 결과: {list(collection_results.keys())}, 성공={collection_success}")

            # ★ Phase 1.1: 입고 데이터 수집 (센터매입 조회/확정)
            if collection_success:
                try:
                    logger.info("[Phase 1.1] Receiving Collection")
                    logger.info(LOG_SEPARATOR_THIN)

                    driver = self.collector.get_driver()
                    if driver:
                        recv_collector = ReceivingCollector(driver=driver, store_id=self.store_id)

                        if recv_collector.navigate_to_receiving_menu():
                            # 어제 입고 데이터 수집
                            recv_stats = recv_collector.collect_and_save(
                                yesterday_str.replace('-', '')
                            )
                            logger.info(f"입고 수집 (어제): {recv_stats}")

                            # 오늘 입고 데이터 수집
                            today_recv = recv_collector.collect_and_save(
                                today_str.replace('-', '')
                            )
                            logger.info(f"입고 수집 (오늘): {today_recv}")

                            recv_collector.close_receiving_menu()
                        else:
                            logger.warning("센터매입 메뉴 이동 실패")
                    else:
                        logger.warning("드라이버 없음, 입고 수집 건너뜀")
                except Exception as e:
                    logger.warning(f"입고 수집 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.15: 폐기 전표 수집 (통합 전표 조회 > 전표구분=폐기)
            # 수집 기준: 전날 + 당일 (2일치)
            waste_slip_stats = None
            if collection_success:
                try:
                    logger.info("[Phase 1.15] Waste Slip Collection (전날+당일)")
                    logger.info(LOG_SEPARATOR_THIN)

                    driver = self.collector.get_driver()
                    if driver:
                        from src.collectors.waste_slip_collector import WasteSlipCollector
                        ws_collector = WasteSlipCollector(driver=driver, store_id=self.store_id)
                        waste_slip_stats = ws_collector.collect_waste_slips(
                            from_date=yesterday_str.replace("-", ""),
                            to_date=today_str.replace("-", ""),
                            save_to_db=True,
                        )
                        logger.info(f"폐기 전표 수집: {waste_slip_stats.get('count', 0)}건")

                        # 전날+당일 심층 검증 + 비교 보고서 생성
                        if waste_slip_stats.get("success"):
                            from src.application.services.waste_verification_service import (
                                WasteVerificationService,
                            )
                            verifier = WasteVerificationService(store_id=self.store_id)
                            for vdate in [yesterday_str, today_str]:
                                vr = verifier.verify_date_deep(
                                    vdate, generate_report=True
                                )
                                l1 = vr.get("level1", {})
                                l2 = vr.get("level2_summary", {})
                                logger.info(
                                    f"폐기 검증 ({vdate}): "
                                    f"L1={l1.get('status')} "
                                    f"gap={l1.get('gap')}건 | "
                                    f"매칭={l2.get('matched', 0)} "
                                    f"누락={l2.get('slip_only', 0)} "
                                    f"누락율={l2.get('miss_rate', 0)}%"
                                )
                    else:
                        logger.warning("드라이버 없음, 폐기 전표 수집 건너뜀")
                except Exception as e:
                    logger.warning(f"폐기 전표 수집 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.16: 폐기전표 -> daily_sales.disuse_qty 동기화
            if waste_slip_stats and waste_slip_stats.get("success"):
                try:
                    logger.info("[Phase 1.16] Waste Slip -> Daily Sales Sync")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.application.services.waste_disuse_sync_service import (
                        WasteDisuseSyncService,
                    )
                    syncer = WasteDisuseSyncService(store_id=self.store_id)
                    for sync_date in [yesterday_str, today_str]:
                        sync_stats = syncer.sync_date(sync_date)
                        if sync_stats["updated"] > 0 or sync_stats["inserted"] > 0:
                            logger.info(
                                f"폐기 동기화 ({sync_date}): "
                                f"updated={sync_stats['updated']}, "
                                f"inserted={sync_stats['inserted']}, "
                                f"skipped={sync_stats['skipped']}"
                            )
                except Exception as e:
                    logger.warning(f"폐기 동기화 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.2: 발주 제외 상품 수집 (자동/스마트)
            exclusion_stats = None
            if collection_success:
                try:
                    logger.info("[Phase 1.2] Exclusion Items Collection")
                    logger.info(LOG_SEPARATOR_THIN)
                    driver = self.collector.get_driver()
                    if driver:
                        exclusion_stats = self._collect_exclusion_items(driver)
                    else:
                        logger.warning("드라이버 없음, 제외 상품 수집 건너뜀")
                except Exception as e:
                    logger.warning(f"제외 상품 수집 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.3: 신상품 도입 현황 수집 (3일발주 후속 관리용)
            new_product_stats = None
            from src.settings.constants import NEW_PRODUCT_MODULE_ENABLED
            if collection_success and NEW_PRODUCT_MODULE_ENABLED:
                try:
                    logger.info("[Phase 1.3] New Product Introduction Collection")
                    logger.info(LOG_SEPARATOR_THIN)
                    driver = self.collector.get_driver()
                    if driver:
                        new_product_stats = self._collect_new_product_info(driver)
                    else:
                        logger.warning("드라이버 없음, 신상품 수집 건너뜀")
                except Exception as e:
                    logger.warning(f"신상품 수집 실패 (발주 플로우 계속): {e}")

            # ★ 사전 발주 평가 보정 (수집 성공 시)
            calibration_result = None
            if collection_success:
                try:
                    logger.info("[Phase 1.5] Eval Calibration")
                    logger.info(LOG_SEPARATOR_THIN)
                    calibrator = EvalCalibrator(store_id=self.store_id)
                    calibration_result = calibrator.run_daily_calibration()

                    # 소급 검증: 놓친 날의 미평가 건 일괄 처리
                    backfill_result = calibrator.backfill_verification(lookback_days=7)
                    if backfill_result["backfilled"] > 0:
                        logger.info(f"소급 검증 완료: {backfill_result['backfilled']}건")

                    # 일일 리포트 생성
                    reporter = EvalReporter(config=calibrator.config, store_id=self.store_id)
                    reporter.generate_daily_report(
                        verification_result=calibration_result.get("verification"),
                        calibration_result=calibration_result.get("calibration"),
                    )
                except Exception as e:
                    logger.warning(f"평가 보정 실패 (발주 플로우 계속): {e}")

            # ★ 폐기 원인 분석 (Phase 1.55 - EvalCalibrator 이후)
            if collection_success:
                try:
                    logger.info("[Phase 1.55] Waste Cause Analysis")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.analysis.waste_cause_analyzer import WasteCauseAnalyzer
                    waste_analyzer = WasteCauseAnalyzer(store_id=self.store_id)
                    waste_result = waste_analyzer.analyze_date(yesterday_str)
                    analyzed = waste_result.get("analyzed", 0)
                    by_cause = waste_result.get("by_cause", {})
                    if analyzed > 0:
                        logger.info(
                            f"폐기 원인 분석 완료: {analyzed}건 "
                            f"(과잉={by_cause.get('OVER_ORDER', 0)}, "
                            f"관리실패={by_cause.get('EXPIRY_MISMANAGEMENT', 0)}, "
                            f"수요감소={by_cause.get('DEMAND_DROP', 0)}, "
                            f"복합={by_cause.get('MIXED', 0)})"
                        )
                    else:
                        logger.info("폐기 원인 분석: 어제 폐기 이벤트 없음")
                except Exception as e:
                    logger.warning(f"폐기 원인 분석 실패 (발주 플로우 계속): {e}")

            # ★ 푸드 폐기율 자동 보정 (Phase 1.56 - WasteCauseAnalyzer 이후)
            if collection_success:
                try:
                    logger.info("[Phase 1.56] Food Waste Rate Calibration")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.prediction.food_waste_calibrator import FoodWasteRateCalibrator
                    waste_calibrator = FoodWasteRateCalibrator(store_id=self.store_id)
                    cal_result = waste_calibrator.calibrate()
                    if cal_result.get("calibrated"):
                        adjusted_count = sum(
                            1 for r in cal_result.get("results", []) if r.get("adjusted")
                        )
                        logger.info(f"푸드 폐기율 보정 완료: {adjusted_count}개 카테고리 조정")
                    else:
                        logger.info(
                            f"푸드 폐기율 보정: 조정 없음 "
                            f"({cal_result.get('reason', 'within_deadband')})"
                        )
                except Exception as e:
                    logger.warning(f"푸드 폐기율 보정 실패 (발주 플로우 계속): {e}")

            # ★ Bayesian 파라미터 최적화 (Phase 1.57 - 주 1회 일요일)
            if collection_success and datetime.now().weekday() == 6:  # 일요일만
                try:
                    logger.info("[Phase 1.57] Bayesian Parameter Optimization")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.prediction.bayesian_optimizer import (
                        BayesianParameterOptimizer,
                        BAYESIAN_ENABLED,
                    )
                    if BAYESIAN_ENABLED:
                        bayesian_opt = BayesianParameterOptimizer(store_id=self.store_id)

                        # 롤백 체크 (이전 최적화 결과 모니터링)
                        rollback_result = bayesian_opt.check_rollback()
                        if rollback_result.get("rolled_back"):
                            logger.warning(
                                f"[Phase 1.57] 이전 최적화 롤백: {rollback_result['reason']}"
                            )
                        else:
                            # 새 최적화 실행
                            opt_result = bayesian_opt.optimize()
                            if opt_result.success:
                                logger.info(
                                    f"[Phase 1.57] 최적화 완료: "
                                    f"loss={opt_result.best_objective:.4f}, "
                                    f"변경={len(opt_result.params_delta)}개"
                                )
                            else:
                                logger.info(
                                    f"[Phase 1.57] 최적화 스킵: {opt_result.error_message}"
                                )
                    else:
                        logger.debug("[Phase 1.57] Bayesian optimization disabled")
                except Exception as e:
                    logger.warning(f"[Phase 1.57] Bayesian optimization 실패 (발주 플로우 계속): {e}")

            # ★ 예측 실적 소급 (prediction_logs.actual_qty 채우기)
            if collection_success:
                try:
                    logger.info("[Phase 1.6] Prediction Actual Sales Update")
                    logger.info(LOG_SEPARATOR_THIN)
                    tracker = AccuracyTracker(store_id=self.store_id)
                    updated = tracker.update_actual_sales(yesterday_str)
                    logger.info(f"예측 실적 소급 완료: {updated}건 ({yesterday_str})")
                    # 최근 7일 소급 업데이트 (수집 누락일 보완)
                    backfilled = tracker.backfill_actual_sales(lookback_days=7)
                    if backfilled > 0:
                        logger.info(f"예측 실적 소급 보완: {backfilled}건 (최근 7일)")
                except Exception as e:
                    logger.warning(f"예측 실적 소급 실패 (발주 플로우 계속): {e}")

            # ★ 유령 재고 정리 (Phase 1.65 - 예측 전 오래된 realtime_inventory stock 초기화)
            if collection_success:
                try:
                    logger.info("[Phase 1.65] Stale Stock Cleanup")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository
                    ri_repo = RealtimeInventoryRepository(store_id=self.store_id)
                    cleanup_result = ri_repo.cleanup_stale_stock(
                        stale_hours=RealtimeInventoryRepository.STOCK_FRESHNESS_HOURS,
                        store_id=self.store_id
                    )
                    if cleanup_result["cleaned"] > 0:
                        logger.info(
                            f"유령 재고 정리 완료: {cleanup_result['cleaned']}개 상품, "
                            f"총 {cleanup_result['total_ghost_stock']}개 stock -> 0"
                        )
                    else:
                        logger.info("유령 재고 없음 (정리 불필요)")
                except Exception as e:
                    logger.warning(f"유령 재고 정리 실패 (발주 플로우 계속): {e}")

            # ★ 예측 로깅 (prediction_logs 기록 - 자동발주와 독립)
            if collection_success:
                try:
                    logger.info("[Phase 1.7] Prediction Logging")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.prediction.improved_predictor import ImprovedPredictor
                    predictor = ImprovedPredictor(store_id=self.store_id)
                    logged = predictor.predict_and_log()
                    logger.info(f"예측 로깅 완료: {logged}건")

                    # 푸드 카테고리 예측 요약 (과소/과다 발주 진단용)
                    try:
                        self._log_food_prediction_summary()
                    except Exception as summary_err:
                        logger.debug(f"푸드 예측 요약 로그 실패 (비치명적): {summary_err}")
                except Exception as e:
                    logger.warning(f"예측 로깅 실패 (발주 플로우 계속): {e}")

            # 카카오톡 리포트 발송
            if collection_results.get(today_str, {}).get("success"):
                self._send_kakao_report(today_str, {"success": True})
            elif collection_results.get(yesterday_str, {}).get("success"):
                self._send_kakao_report(yesterday_str, {"success": True})

            # 2. 자동 발주 (수집 성공 시)
            if run_auto_order and collection_success:
                logger.info("[Phase 2] Auto Order")
                logger.info(LOG_SEPARATOR_THIN)

                # 매출분석 탭 닫기
                close_result = self.collector.close_sales_menu()
                logger.info(f"매출분석 탭 닫기: {close_result}")
                time.sleep(DAILY_JOB_AFTER_CLOSE_TAB)

                # 드라이버 재사용하여 발주
                driver = self.collector.get_driver()
                if driver:
                    # Phase 1.2에서 DB 캐시 갱신 성공했으면 사이트 재조회 건너뜀
                    exclusion_cached = bool(exclusion_stats and exclusion_stats.get("success"))
                    order_result = self._run_auto_order_with_driver(
                        driver, use_improved_predictor,
                        skip_exclusion_fetch=exclusion_cached
                    )

                    # 3. 발주 실패 사유 수집 (실패 건이 있을 때)
                    fail_count = order_result.get("fail_count", 0)
                    if fail_count > 0 and driver:
                        fail_reason_result = self._run_fail_reason_collection(driver)
                else:
                    logger.warning("Driver not available, skipping auto-order")
            else:
                logger.info(f"자동발주 스킵: run_auto_order={run_auto_order}, collection_success={collection_success}")

            # 결과 통합
            total_items = sum(
                r.get("item_count", 0) for r in collection_results.values()
                if r.get("success")
            )

            return {
                "success": collection_success,
                "collection": collection_results,
                "exclusion": exclusion_stats,
                "new_product": new_product_stats,
                "order": order_result,
                "fail_reasons": fail_reason_result,
                "dates_collected": dates_to_collect,
                "total_items": total_items,
                "duration": time.time() - start_time
            }

        except Exception as e:
            logger.error(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "collection": collection_results,
                "order": order_result,
                "fail_reasons": fail_reason_result,
                "duration": time.time() - start_time
            }

        finally:
            if self.collector:
                self.collector.close()
                self.collector = None

    def _collect_exclusion_items(self, driver: Any) -> Dict[str, Any]:
        """발주 제외 상품 수집 (자동발주/스마트발주 탭)

        Args:
            driver: Selenium WebDriver

        Returns:
            {"auto_count", "smart_count", "success"} 결과 dict
        """
        from src.collectors.order_status_collector import OrderStatusCollector
        from src.infrastructure.database.repos import AutoOrderItemRepository, SmartOrderItemRepository

        result: Dict[str, Any] = {"auto_count": 0, "smart_count": 0, "success": False}

        collector = OrderStatusCollector(driver, store_id=self.store_id)
        try:
            if not collector.navigate_to_order_status_menu():
                logger.warning("발주 현황 조회 메뉴 이동 실패")
                return result

            # 자동발주
            auto_detail = collector.collect_auto_order_items_detail()
            if auto_detail is not None:
                saved = AutoOrderItemRepository(store_id=self.store_id).refresh(auto_detail, store_id=self.store_id)
                result["auto_count"] = saved
                logger.info(f"자동발주 제외 상품 {len(auto_detail)}개 수집, DB {saved}건 갱신")
            else:
                logger.warning("자동발주 제외 상품 사이트 조회 실패")

            # 스마트발주
            smart_detail = collector.collect_smart_order_items_detail()
            if smart_detail is not None:
                saved = SmartOrderItemRepository(store_id=self.store_id).refresh(smart_detail, store_id=self.store_id)
                result["smart_count"] = saved
                logger.info(f"스마트발주 제외 상품 {len(smart_detail)}개 수집, DB {saved}건 갱신")
            else:
                logger.warning("스마트발주 제외 상품 사이트 조회 실패")

            result["success"] = True
        finally:
            if not collector.close_menu():
                from src.utils.nexacro_helpers import close_tab_by_frame_id
                from src.settings.ui_config import FRAME_IDS
                close_tab_by_frame_id(driver, FRAME_IDS["ORDER_STATUS"])
                time.sleep(0.5)

        return result

    def _collect_new_product_info(self, driver: Any) -> Optional[Dict[str, Any]]:
        """신상품 도입 현황 수집

        Args:
            driver: Selenium WebDriver

        Returns:
            수집 결과 dict 또는 None
        """
        from src.collectors.new_product_collector import NewProductCollector

        collector = NewProductCollector(driver=driver, store_id=self.store_id)
        try:
            if not collector.navigate_to_menu():
                logger.warning("신상품 도입 현황 메뉴 이동 실패")
                return None

            result = collector.collect_and_save()
            logger.info(f"신상품 도입 현황 수집 완료: {result.get('weekly_count', 0)}주차")
            return result
        finally:
            collector.close_menu()

    def _run_auto_order_with_driver(
        self, driver: Any, use_improved_predictor: bool = True,
        skip_exclusion_fetch: bool = False
    ) -> Dict[str, Any]:
        """
        기존 드라이버로 자동 발주 실행 (DailyOrderFlow 위임)

        Args:
            driver: Selenium WebDriver
            use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)
            skip_exclusion_fetch: True면 자동/스마트발주 목록 사이트 재조회 건너뜀
        """
        try:
            from src.application.use_cases.daily_order_flow import DailyOrderFlow
            from src.settings.store_context import StoreContext

            logger.info("AutoOrder: Starting with existing session (via DailyOrderFlow)...")
            logger.info(f"AutoOrder: Predictor: {'Improved (31-day data)' if use_improved_predictor else 'Legacy'}")

            ctx = StoreContext.from_store_id(self.store_id)
            flow = DailyOrderFlow(
                store_ctx=ctx,
                driver=driver,
                use_improved_predictor=use_improved_predictor,
            )
            flow_result = flow.run_auto_order(
                dry_run=False,
                min_order_qty=1,
                max_items=None,
                prefetch_pending=True,
                collect_fail_reasons=False,  # 실패 사유는 run_optimized에서 별도 수집
                skip_exclusion_fetch=skip_exclusion_fetch,
            )

            # DailyOrderFlow 결과에서 execution 결과 추출 (기존 인터페이스 호환)
            order_result = flow_result.get("execution", {})
            if not order_result:
                order_result = {
                    "success": flow_result.get("success", False),
                    "success_count": flow_result.get("success_count", 0),
                    "fail_count": flow_result.get("fail_count", 0),
                }

            logger.info(f"AutoOrder: Done: {order_result.get('success_count', 0)} success, "
                        f"{order_result.get('fail_count', 0)} fail")

            return order_result

        except Exception as e:
            logger.error(f"AutoOrder: Error: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

    def _run_fail_reason_collection(self, driver: Any) -> Dict[str, Any]:
        """발주 실패 사유 수집 (기존 드라이버 재사용)

        Args:
            driver: Selenium WebDriver (자동발주에서 사용한 드라이버)

        Returns:
            {total, checked, success, failed} 또는 에러 dict
        """
        try:
            logger.info("[Phase 3] Fail Reason Collection")
            logger.info(LOG_SEPARATOR_THIN)

            collector = FailReasonCollector(driver=driver, store_id=self.store_id)
            result = collector.collect_all()

            logger.info(
                f"FailReason: Done: {result.get('success', 0)} success, "
                f"{result.get('failed', 0)} fail, {result.get('checked', 0)} saved"
            )
            return result

        except Exception as e:
            logger.error(f"FailReason: Error: {e}")
            return {"success": False, "error": str(e)}

    def run_with_today(self) -> Dict[str, Any]:
        """
        전날 + 당일 데이터 수집 (기존 호환용)
        - 최적화된 버전 사용

        Returns:
            실행 결과
        """
        return self.run_optimized(run_auto_order=False)

    def _log_food_prediction_summary(self) -> None:
        """푸드 카테고리 예측 요약 로그 출력 (과소/과다 발주 진단용)

        prediction_logs에서 오늘 푸드 예측 데이터를 집계하여
        중분류별 평균 예측량, 조정량, 발주 건수, 폐기계수 분포를 로깅한다.
        """
        import sqlite3
        from datetime import date

        db_path = self._get_store_db_path()
        if not db_path:
            return

        today = date.today().isoformat()
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            cursor = conn.execute("""
                SELECT mid_cd,
                       COUNT(*) as cnt,
                       AVG(predicted_qty) as avg_pred,
                       AVG(adjusted_qty) as avg_adj,
                       AVG(order_qty) as avg_ord,
                       SUM(CASE WHEN order_qty > 0 THEN 1 ELSE 0 END) as ordered_cnt,
                       SUM(order_qty) as total_ord
                FROM prediction_logs
                WHERE prediction_date = ?
                AND mid_cd IN ('001','002','003','004','005','012')
                GROUP BY mid_cd
                ORDER BY mid_cd
            """, (today,))
            rows = cursor.fetchall()
            if not rows:
                return

            logger.info("[예측요약] 푸드 카테고리 예측 요약:")
            total_items = 0
            total_ordered = 0
            total_ord_qty = 0
            for r in rows:
                mid_cd, cnt, avg_pred, avg_adj, avg_ord, ordered_cnt, total_o = r
                ratio = avg_adj / avg_pred if avg_pred and avg_pred > 0 else 0
                adj_pct = f"{ratio:.0%}"
                logger.info(
                    f"  mid={mid_cd}: {cnt}건 | "
                    f"avg_pred={avg_pred:.2f} -> avg_adj={avg_adj:.2f}({adj_pct}) | "
                    f"발주={ordered_cnt}/{cnt} 총{total_o or 0:.0f}개"
                )
                # 50% 이하 감량 경고
                if ratio > 0 and ratio <= 0.55 and cnt >= 3:
                    logger.warning(
                        f"  [과소발주주의] mid={mid_cd}: 예측→조정 감량이 {adj_pct}로 "
                        f"과도합니다. 동적폐기계수 또는 캘리브레이터 값 확인 필요"
                    )
                total_items += cnt
                total_ordered += (ordered_cnt or 0)
                total_ord_qty += (total_o or 0)

            logger.info(
                f"[예측요약] 푸드 합계: {total_items}건 중 "
                f"{total_ordered}건 발주 (총 {total_ord_qty:.0f}개)"
            )
        except Exception as e:
            logger.debug(f"푸드 예측 요약 생성 실패: {e}")
        finally:
            conn.close()

    def _get_store_db_path(self) -> str:
        """현재 매장의 store DB 경로 반환"""
        import os
        store_db = os.path.join("data", "stores", f"{self.store_id}.db")
        if os.path.exists(store_db):
            return store_db
        return ""

    def _send_kakao_report(self, target_date: str, result: Dict[str, Any]) -> None:
        """카카오톡으로 리포트 전송"""
        try:
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)

            if not notifier.access_token:
                logger.warning("Kakao token not found. Skipping.")
                return

            if result.get("success"):
                reporter = DailyReport()
                report = reporter.generate(target_date)
                notifier.send_report(report)
            else:
                notifier.send_message(
                    f"[BGF 수집 실패]\n\n"
                    f"날짜: {target_date}\n"
                    f"오류: {result.get('error', 'Unknown')}\n"
                    f"시간: {datetime.now().strftime('%H:%M:%S')}"
                )

            logger.info("Kakao notification sent")

        except Exception as e:
            logger.error(f"Kakao failed: {e}")

        # 폐기 위험 알림
        self._send_expiry_alert()

    def _send_expiry_alert(self) -> None:
        """폐기 위험 알림 발송"""
        try:
            logger.info("Checking expiry alerts...")
            result = run_expiry_check_and_alert(store_id=self.store_id)

            if result["success"]:
                total = result.get("total_alerts", 0)
                if total > 0:
                    logger.info(f"Expiry alert: {total} items")
                else:
                    logger.info("No expiry alerts")
            else:
                logger.error(f"Expiry check failed: {result.get('error')}")

        except Exception as e:
            logger.error(f"Expiry alert failed: {e}")

    def _save_weather_data(self, target_date: str, weather: Dict[str, Any]) -> None:
        """날씨 데이터를 DB에 저장"""
        try:
            if weather.get("temperature") is not None:
                self.weather_repo.save_factor(
                    factor_date=target_date,
                    factor_type="weather",
                    factor_key="temperature",
                    factor_value=str(weather["temperature"])
                )

            if weather.get("weather_type"):
                self.weather_repo.save_factor(
                    factor_date=target_date,
                    factor_type="weather",
                    factor_key="weather_type",
                    factor_value=weather["weather_type"]
                )

            if weather.get("day_of_week"):
                self.weather_repo.save_factor(
                    factor_date=target_date,
                    factor_type="calendar",
                    factor_key="day_of_week",
                    factor_value=weather["day_of_week"]
                )

            if weather.get("store_id"):
                self.weather_repo.save_factor(
                    factor_date=target_date,
                    factor_type="store",
                    factor_key="store_id",
                    factor_value=weather["store_id"]
                )

            # 예보 기온 저장 (내일/모레 최고기온)
            forecast_daily = weather.get("forecast_daily", {})
            if forecast_daily:
                for fdate, ftemp in forecast_daily.items():
                    self.weather_repo.save_factor(
                        factor_date=fdate,
                        factor_type="weather",
                        factor_key="temperature_forecast",
                        factor_value=str(ftemp)
                    )
                logger.info(f"Forecast saved: {forecast_daily}")

            logger.info(f"Weather saved: {weather.get('temperature')}도")

        except Exception as e:
            logger.error(f"Weather save failed: {e}")

    def run_range(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """날짜 범위 수집 (백필용)

        Args:
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date: 종료 날짜 (YYYY-MM-DD)

        Returns:
            날짜별 수집 결과 리스트
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        results = []
        current = start

        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            logger.info(f"Processing {date_str}...")

            result = self.run(date_str)
            results.append({
                "date": date_str,
                "success": result["success"],
                "items": len(result.get("data", []))
            })

            current += timedelta(days=1)

            if current <= end:
                time.sleep(BACKFILL_BETWEEN_DATES)

        return results


def run_daily_job(target_date: Optional[str] = None) -> Dict[str, Any]:
    """일별 수집 실행 헬퍼 함수

    Args:
        target_date: 수집 대상 날짜 (YYYY-MM-DD, 기본값: 어제)

    Returns:
        수집 실행 결과
    """
    job = DailyCollectionJob()
    return job.run(target_date)


def run_daily_job_with_today() -> Dict[str, Any]:
    """전날 + 당일 수집 실행 헬퍼 함수

    Returns:
        수집 실행 결과
    """
    job = DailyCollectionJob()
    return job.run_with_today()


def run_daily_job_optimized(
    run_auto_order: bool = True,
    use_improved_predictor: bool = True
) -> Dict[str, Any]:
    """
    최적화된 전체 플로우 실행 헬퍼 함수

    Args:
        run_auto_order: 자동 발주 실행 여부
        use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)

    Returns:
        최적화된 플로우 실행 결과
    """
    job = DailyCollectionJob()
    return job.run_optimized(
        run_auto_order=run_auto_order,
        use_improved_predictor=use_improved_predictor
    )


def weekly_accuracy_report(store_id: Optional[str] = None) -> Dict[str, Any]:
    """
    주간 예측 정확도 리포트 발송 (매주 월요일 08:00)

    - PredictionLogger.calculate_accuracy(7)로 지난 7일 정확도 계산
    - 카카오톡으로 리포트 발송

    Returns:
        실행 결과 {"success", "accuracy", "message"}
    """
    logger.info(LOG_SEPARATOR_WIDE)
    logger.info("주간 예측 정확도 리포트")
    logger.info(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(LOG_SEPARATOR_WIDE)

    try:
        # 예측 정확도 계산
        pred_logger = PredictionLogger(store_id=store_id)
        accuracy = pred_logger.calculate_accuracy(days=WEEKLY_REPORT_DAYS)

        # 메시지 생성
        if "message" in accuracy:
            # 데이터 부족
            message = (
                f"[BGF 주간 예측 리포트]\n\n"
                f"기간: 최근 7일\n"
                f"상태: {accuracy['message']}\n"
                f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            # 정확도 계산 성공
            total = accuracy.get('total_predictions', 0)
            avg_error = accuracy.get('avg_error', 0)
            mape = accuracy.get('mape', 0)
            accuracy_pct = accuracy.get('accuracy_pct', 0)

            message = (
                f"[BGF 주간 예측 리포트]\n\n"
                f"기간: 최근 7일\n"
                f"예측 건수: {total}건\n"
                f"평균 오차: {avg_error:.1f}개\n"
                f"MAPE: {mape:.1f}%\n"
                f"정확도: {accuracy_pct:.1f}%\n"
                f"\n시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        logger.info("정확도 계산 완료")
        logger.debug(f"메시지:\n{message}")

        # 카카오톡 발송
        notifier = KakaoNotifier(DEFAULT_REST_API_KEY)

        if not notifier.access_token:
            logger.warning("카카오 토큰 없음, 발송 건너뜀")
            return {
                "success": False,
                "accuracy": accuracy,
                "message": "Kakao token not found"
            }

        notifier.send_message(message)
        logger.info("카카오톡 발송 완료")

        return {
            "success": True,
            "accuracy": accuracy,
            "message": "sent"
        }

    except Exception as e:
        logger.error(f"주간 리포트 오류: {e}")
        import traceback
        traceback.print_exc()

        return {
            "success": False,
            "error": str(e)
        }


def run_manual_order_detect(target_date: Optional[str] = None, store_id: Optional[str] = None) -> Dict[str, Any]:
    """
    수동 발주 감지 실행 (스케줄러용)

    배송 스케줄:
    - 1차 입고: 당일 20:00 → 21:00에 감지 실행
    - 2차 입고: 익일 07:00 → 08:00에 감지 실행

    Args:
        target_date: 입고일 (기본: 오늘)
        store_id: 매장 코드 (기본: None)

    Returns:
        실행 결과
    """
    logger.info(LOG_SEPARATOR_THIN)
    logger.info("수동 발주 감지 시작")
    logger.info(LOG_SEPARATOR_THIN)

    try:
        result = run_manual_order_detection(target_date, store_id=store_id)

        if result['detected'] > 0:
            logger.info(
                f"수동 발주 감지 완료: "
                f"{result['detected']}건 감지, "
                f"{result['saved']}건 저장, "
                f"{result['skipped']}건 스킵"
            )
        else:
            logger.info("수동 발주 없음")

        return result

    except Exception as e:
        logger.error(f"수동 발주 감지 실패: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily sales collection job")
    parser.add_argument("--date", "-d", type=str, default=None)
    parser.add_argument("--range", "-r", nargs=2, metavar=("START", "END"))
    parser.add_argument("--with-today", action="store_true")
    parser.add_argument("--optimized", action="store_true",
                       help="Run optimized flow (collection + auto-order)")
    parser.add_argument("--no-order", action="store_true",
                       help="Skip auto-order in optimized mode")
    parser.add_argument("--legacy-predictor", action="store_true",
                       help="Use legacy predictor instead of improved predictor (31-day data)")
    parser.add_argument("--weekly-report", action="store_true",
                       help="Send weekly accuracy report via Kakao")

    args = parser.parse_args()

    job = DailyCollectionJob()

    if args.weekly_report:
        result = weekly_accuracy_report()
        sys.exit(0 if result["success"] else 1)
    elif args.range:
        results = job.run_range(args.range[0], args.range[1])
        print("\n=== Summary ===")
        for r in results:
            status = "OK" if r["success"] else "FAIL"
            print(f"  {r['date']}: {status} ({r['items']} items)")
    elif args.optimized:
        result = job.run_optimized(
            run_auto_order=not args.no_order,
            use_improved_predictor=not args.legacy_predictor
        )
        sys.exit(0 if result["success"] else 1)
    elif args.with_today:
        result = job.run_with_today()
        sys.exit(0 if result["success"] else 1)
    else:
        result = job.run(args.date)
        sys.exit(0 if result["success"] else 1)
