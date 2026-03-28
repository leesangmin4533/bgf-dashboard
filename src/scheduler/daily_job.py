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

from src.utils.logger import get_logger, set_session_id, clear_session_id

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


def _cleanup_dangling_tabs(driver: Any, frame_ids: List[str]) -> int:
    """잔여 nexacro 탭 강제 정리

    Phase 전환 시 이전 Phase의 탭이 남아있으면 이후 메뉴 이동이 실패할 수 있다.
    지정된 frame_id 목록의 탭이 열려있으면 강제로 닫는다.

    Args:
        driver: Selenium WebDriver
        frame_ids: 닫을 프레임 ID 목록

    Returns:
        닫은 탭 수
    """
    from src.utils.nexacro_helpers import close_tab_by_frame_id
    closed = 0
    for fid in frame_ids:
        try:
            # 프레임이 아직 열려있는지 확인
            exists = driver.execute_script("""
                try {
                    var el = document.querySelector('[id*="tab_openList"][id$=".' + arguments[0] + '"]');
                    return !!el;
                } catch(e) { return false; }
            """, fid)
            if exists:
                if close_tab_by_frame_id(driver, fid):
                    logger.info(f"잔여 탭 정리: {fid}")
                    closed += 1
                    time.sleep(0.1)  # [최적화: 0.3→0.1]
                else:
                    logger.warning(f"잔여 탭 정리 실패: {fid}")
        except Exception as e:
            logger.debug(f"탭 확인 실패 ({fid}): {e}")
    return closed


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
        use_improved_predictor: bool = True,
        target_dates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        최적화된 전체 플로우 실행
        - 한 번 로그인으로 전날+당일 수집 + 자동 발주

        Args:
            run_auto_order: 자동 발주 실행 여부
            use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)
            target_dates: 발주할 날짜 목록 (YYYY-MM-DD). None이면 전체 발주

        Returns:
            실행 결과
        """
        self._target_dates = target_dates
        today = datetime.now()
        yesterday = today - timedelta(days=1)

        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        sid = set_session_id()
        logger.info(LOG_SEPARATOR_WIDE)
        logger.info(f"Optimized flow started | session={sid}")
        logger.info(f"Dates: {yesterday_str}, {today_str}")
        logger.info(f"Auto-order: {run_auto_order}")
        logger.info(f"Predictor: {'Improved (31-day)' if use_improved_predictor else 'Legacy'}")
        logger.info(LOG_SEPARATOR_WIDE)

        start_time = time.time()

        # Store DB 테이블 보장 (누락된 테이블 자동 생성)
        try:
            from src.infrastructure.database.schema import init_store_db
            init_store_db(self.store_id)
        except Exception as e:
            logger.warning(f"Store DB 테이블 보장 실패 (계속 진행): {e}")

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

            # ★ Phase 1.04: 시간대별 매출 수집 (STMB010 Direct API)
            if collection_success:
                try:
                    logger.info("[Phase 1.04] Hourly Sales Collection (STMB010)")
                    logger.info(LOG_SEPARATOR_THIN)
                    driver = self.collector.get_driver()
                    if driver:
                        from src.collectors.hourly_sales_collector import HourlySalesCollector
                        from src.infrastructure.database.repos.hourly_sales_repo import HourlySalesRepository
                        hourly_collector = HourlySalesCollector(driver=driver)
                        hourly_repo = HourlySalesRepository(store_id=self.store_id)
                        hourly_repo.ensure_table()
                        for date_str in [yesterday_str, today_str]:
                            hourly_data = hourly_collector.collect(date_str.replace('-', ''))
                            if hourly_data:
                                saved = hourly_repo.save_hourly_sales(hourly_data, date_str)
                                logger.info(f"[Phase 1.04] {date_str}: {saved}건 저장")
                            else:
                                logger.debug(f"[Phase 1.04] {date_str}: 데이터 없음")
                    else:
                        logger.debug("[Phase 1.04] 드라이버 없음, 건너뜀")
                except Exception as e:
                    logger.warning(f"[Phase 1.04] 시간대별 매출 수집 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.05: 시간대별 매출 상세(품목별) 수집 (selPrdT3)
            if collection_success:
                try:
                    logger.info("[Phase 1.05] Hourly Sales Detail Collection (selPrdT3)")
                    logger.info(LOG_SEPARATOR_THIN)
                    driver = self.collector.get_driver()
                    if driver:
                        from src.collectors.hourly_sales_detail_collector import HourlySalesDetailCollector
                        from src.infrastructure.database.repos.hourly_sales_detail_repo import HourlySalesDetailRepository
                        hsd_collector = HourlySalesDetailCollector(driver=driver)
                        hsd_repo = HourlySalesDetailRepository(store_id=self.store_id)
                        hsd_repo.ensure_table()
                        for date_str in [yesterday_str, today_str]:
                            # 미수집 시간대만 수집
                            collected = hsd_repo.get_collected_hours(date_str)
                            if date_str == today_str:
                                from datetime import datetime as _dt
                                target_hours = [h for h in range(0, _dt.now().hour) if h not in collected]
                            else:
                                target_hours = [h for h in range(24) if h not in collected]
                            if not target_hours:
                                logger.debug(f"[Phase 1.05] {date_str}: 이미 수집 완료")
                                continue
                            # Direct API 딜레이 축소: 0.5초 → 0.25초 (~10-15초 절약)
                            results = hsd_collector.collect_all_hours(
                                date_str.replace('-', ''), hours=target_hours, delay=0.25
                            )
                            total_items = 0
                            for hour, items in results.items():
                                if items:
                                    hsd_repo.save_detail(date_str, hour, items)
                                    total_items += len(items)
                            logger.info(
                                f"[Phase 1.05] {date_str}: "
                                f"{len(results)}/{len(target_hours)}시간대, {total_items}품목"
                            )
                        # 재시도
                        if hsd_collector.failed_count > 0:
                            hsd_collector.retry_failed()
                    else:
                        logger.debug("[Phase 1.05] 드라이버 없음, 건너뜀")
                except Exception as e:
                    logger.warning(f"[Phase 1.05] 시간대별 매출 상세 수집 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.01: 이상치 탐지 (D-2)
            if collection_success:
                try:
                    logger.info("[Phase 1.01] Anomaly Detection (D-2)")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.analysis.anomaly_detector import run_anomaly_detection
                    anomaly_result = run_anomaly_detection(self.store_id, today_str)
                    if anomaly_result.has_alerts:
                        try:
                            from src.alert.kakao_notifier import KakaoNotifier
                            from src.settings.constants import DEFAULT_REST_API_KEY
                            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
                            if notifier.access_token:
                                notifier.send_message(anomaly_result.format_kakao())
                        except Exception as e_kakao:
                            logger.debug(f"[Phase 1.01] 카카오 알림 실패: {e_kakao}")
                        logger.info(
                            f"[Phase 1.01] 이상치 {len(anomaly_result.alerts)}건 감지 "
                            f"(spike={anomaly_result.spike_count} "
                            f"zero={anomaly_result.zero_count} "
                            f"stock={anomaly_result.stock_count})"
                        )
                    else:
                        logger.info("[Phase 1.01] 이상치 없음")
                except Exception as e:
                    logger.warning(f"[Phase 1.01] 이상치 탐지 실패 (발주 플로우 계속): {e}")

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

                # 잔여 탭 강제 정리 (통합 전표 조회 탭이 남아있으면 이후 Phase 실패)
                driver = self.collector.get_driver()
                if driver:
                    from src.settings.ui_config import FRAME_IDS
                    _cleanup_dangling_tabs(driver, [
                        FRAME_IDS.get("WASTE_SLIP", "STGJ020_M0"),
                    ])

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
                    logger.warning(f"제외 상품 수집 실패 — Phase 2에서 사이트 재조회로 복구됩니다: {e}")

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

            # ★ Phase 1.35: 신제품 라이프사이클 모니터링
            if collection_success:
                try:
                    logger.info("[Phase 1.35] New Product Lifecycle Monitoring")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.application.services.new_product_monitor import NewProductMonitor
                    monitor = NewProductMonitor(store_id=self.store_id)
                    monitor_stats = monitor.run()
                    if monitor_stats["active_items"] > 0:
                        logger.info(
                            f"[Phase 1.35] 모니터링: {monitor_stats['active_items']}건, "
                            f"추적저장: {monitor_stats['tracking_saved']}건, "
                            f"상태변경: {len(monitor_stats['status_changes'])}건"
                        )
                except Exception as e:
                    logger.warning(f"신제품 모니터링 실패 (발주 플로우 계속): {e}")

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

            # ★ Croston alpha/beta 최적화 (Phase 1.58 - 주 1회 일요일)
            if collection_success and datetime.now().weekday() == 6:  # 일요일만
                try:
                    logger.info("[Phase 1.58] Croston Parameter Optimization (C-1)")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.analysis.croston_optimizer import run_optimization
                    croston_result = run_optimization(self.store_id)
                    logger.info(f"[Phase 1.58] Croston 최적화 완료: {croston_result}")

                    # C-2: Stacking 메타 학습기 재학습
                    from src.analysis.stacking_predictor import train_stacking_model
                    stacking_result = train_stacking_model(self.store_id)
                    logger.info(f"[C-2] Stacking 학습: {stacking_result}")
                except Exception as e:
                    logger.warning(f"[Phase 1.58] Croston/Stacking 최적화 실패 (발주 플로우 계속): {e}")

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

            # ★ 수요 패턴 분류 DB 갱신 (Phase 1.61 - prediction-redesign)
            if collection_success:
                try:
                    logger.info("[Phase 1.61] Demand Pattern Classification")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.prediction.demand_classifier import DemandClassifier
                    from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository
                    from src.infrastructure.database.connection import DBRouter
                    pd_repo = ProductDetailRepository()
                    active_items = pd_repo.get_all_active_items(days=30, store_id=self.store_id)
                    if active_items:
                        classifier = DemandClassifier(store_id=self.store_id)
                        # 벌크 쿼리로 mid_cd 일괄 조회 (get_detail 대신)
                        conn = DBRouter.get_common_connection()
                        try:
                            cursor = conn.cursor()
                            placeholders = ",".join("?" for _ in active_items)
                            cursor.execute(
                                f"SELECT item_cd, mid_cd FROM products WHERE item_cd IN ({placeholders})",
                                active_items
                            )
                            mid_cd_map = {row[0]: row[1] for row in cursor.fetchall()}
                        finally:
                            conn.close()
                        items_for_classify = [
                            {"item_cd": ic, "mid_cd": mid_cd_map.get(ic, "")}
                            for ic in active_items
                            if ic in mid_cd_map
                        ]
                        if items_for_classify:
                            batch_results = classifier.classify_batch(items_for_classify)
                            patterns = {
                                item_cd: result.pattern.value
                                for item_cd, result in batch_results.items()
                            }
                            updated = pd_repo.bulk_update_demand_pattern(patterns)
                            from collections import Counter
                            counts = Counter(patterns.values())
                            logger.info(
                                f"수요 패턴 분류 완료: {updated}건 "
                                f"(daily={counts.get('daily', 0)}, "
                                f"frequent={counts.get('frequent', 0)}, "
                                f"intermittent={counts.get('intermittent', 0)}, "
                                f"slow={counts.get('slow', 0)})"
                            )
                except Exception as e:
                    logger.warning(f"[Phase 1.61] 수요 패턴 분류 실패 (발주 플로우 계속): {e}")

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
                    # 만료 배치 remaining_qty 정리
                    try:
                        from fix_expired_batch_remaining import cleanup_expired_batches
                        expired_cleaned = cleanup_expired_batches(self.store_id)
                        if expired_cleaned > 0:
                            logger.info(f"만료 배치 정리: {expired_cleaned}건")
                    except Exception as e_batch:
                        logger.debug(f"만료 배치 정리 실패 (비치명적): {e_batch}")
                except Exception as e:
                    logger.warning(f"유령 재고 정리 실패 (발주 플로우 계속): {e}")

            # ★ 배치 FIFO 재동기화 + 푸드 유령재고 정리 (Phase 1.66)
            # Phase 1.65 TTL 정리 후, active 배치 없는 푸드의 RI stock_qty → 0
            # Phase 2 발주 전에 실행하여 정확한 재고 기반 발주 보장
            if collection_success:
                try:
                    logger.info("[Phase 1.66] Batch FIFO Sync + Food Ghost Cleanup")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.infrastructure.database.repos import InventoryBatchRepository

                    batch_repo = InventoryBatchRepository(store_id=self.store_id)
                    sync_result = batch_repo.sync_remaining_with_stock(
                        store_id=self.store_id
                    )
                    adj = sync_result.get("adjusted", 0)
                    ghost = sync_result.get("ghost_cleared", 0)
                    if adj > 0 or ghost > 0:
                        logger.info(
                            f"배치 FIFO 동기화: 보정 {adj}건, "
                            f"consumed {sync_result.get('consumed', 0)}건, "
                            f"푸드 유령재고 정리 {ghost}건"
                        )
                    else:
                        logger.info("배치 FIFO 정합성 양호 (보정 불필요)")
                except Exception as e:
                    logger.warning(f"[Phase 1.66] 배치 FIFO 동기화 실패 (발주 플로우 계속): {e}")

            # ★ 데이터 무결성 검증 (Phase 1.67)
            # Phase 1.66 FIFO 동기화 후, 남아있는 데이터 이상치 자동 감지
            if collection_success:
                try:
                    logger.info("[Phase 1.67] Data Integrity Verification")
                    logger.info(LOG_SEPARATOR_THIN)
                    from src.application.services.data_integrity_service import DataIntegrityService

                    integrity_svc = DataIntegrityService(store_id=self.store_id)
                    integrity_result = integrity_svc.run_all_checks(self.store_id)
                    anomalies = integrity_result.get("total_anomalies", 0)
                    if anomalies > 0:
                        logger.warning(
                            f"데이터 무결성 이상 {anomalies}건 감지 "
                            f"({', '.join(r['check_name'] + '=' + str(r['count']) for r in integrity_result['results'] if r['status'] != 'OK')})"
                        )
                    else:
                        logger.info("데이터 무결성 검증 통과 (이상 없음)")
                except Exception as e:
                    logger.warning(f"[Phase 1.67] 무결성 검증 실패 (발주 플로우 계속): {e}")

            # ★ Phase 1.68: DirectAPI 실시간 재고 갱신 (예측 전)
            # stale RI 상품의 NOW_QTY를 BGF selSearch로 조회하여 RI 갱신
            if run_auto_order and collection_success:
                try:
                    from src.infrastructure.database.repos.inventory_repo import (
                        RealtimeInventoryRepository,
                    )
                    ri_repo = RealtimeInventoryRepository(store_id=self.store_id)
                    stale_items = ri_repo.get_stale_stock_item_codes(store_id=self.store_id)

                    # ★ 행사 사전 동기화: product_details에 행사 있지만 promotions 미등록 상품
                    from src.settings.constants import PROMO_SYNC_ENABLED
                    promo_missing_items = []
                    if PROMO_SYNC_ENABLED:
                        promo_missing_items = ri_repo.get_promo_missing_item_codes(
                            store_id=self.store_id
                        )
                        if promo_missing_items:
                            logger.info(
                                f"[Phase 1.68] 행사 미등록 상품: {len(promo_missing_items)}개"
                            )

                    # 중복 제거 후 병합
                    refresh_items = list(set(stale_items + promo_missing_items))

                    if refresh_items:
                        logger.info(
                            f"[Phase 1.68] DirectAPI Refresh: "
                            f"stale={len(stale_items)}, promo_sync={len(promo_missing_items)}, "
                            f"total={len(refresh_items)}개"
                        )
                        logger.info(LOG_SEPARATOR_THIN)
                        driver = self.collector.get_driver()
                        if driver:
                            from src.collectors.order_prep_collector import OrderPrepCollector
                            stock_collector = OrderPrepCollector(
                                driver=driver,
                                store_id=self.store_id,
                                save_to_db=True,
                            )
                            try:
                                api_results = stock_collector._collect_via_direct_api(refresh_items)

                                if api_results:
                                    refreshed = len([
                                        r for r in api_results.values()
                                        if r and r.get('success')
                                    ])
                                    logger.info(
                                        f"[Phase 1.68] 갱신 완료: "
                                        f"{refreshed}/{len(refresh_items)}건"
                                    )
                                else:
                                    logger.info(
                                        f"[Phase 1.68] DirectAPI 실패 → "
                                        f"Selenium 폴백 생략 (Phase 2에서 수집)"
                                    )
                            finally:
                                # ★ 반드시 탭 닫기 — 이후 Phase 2 발주 화면과 충돌 방지
                                try:
                                    stock_collector.close_menu()
                                    logger.info("[Phase 1.68] 단품별 발주 탭 정리 완료")
                                except Exception as close_err:
                                    logger.debug(f"[Phase 1.68] 탭 닫기: {close_err}")
                        else:
                            logger.warning("[Phase 1.68] 드라이버 없음 — 스킵")
                    else:
                        logger.info("[Phase 1.68] 갱신 대상 없음 — 스킵")
                except Exception as e:
                    logger.warning(
                        f"[Phase 1.68] 실시간 재고 갱신 실패 (발주 플로우 계속): {e}"
                    )

            # ★ 예측 로깅 (prediction_logs 기록 - 자동발주와 독립)
            # auto-order 활성화 시 Phase 2에서 예측+로깅 수행하므로 스킵 (~5-10초 절약)
            if collection_success and not run_auto_order:
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
            elif collection_success and run_auto_order:
                logger.info("[Phase 1.7] 스킵 (Phase 2에서 예측+로깅 수행)")

            # 카카오톡 리포트 발송
            if collection_results.get(today_str, {}).get("success"):
                self._send_kakao_report(today_str, {"success": True})
            elif collection_results.get(yesterday_str, {}).get("success"):
                self._send_kakao_report(yesterday_str, {"success": True})

            # ★ Phase 1.95: 발주현황 → OT 동기화 (수동발주 pending 인식)
            # Phase 1.2에서 이미 OT 동기화 완료 시 스킵 (~5-8초 절약)
            ot_sync_done = (exclusion_stats or {}).get("ot_sync_done", False)
            if run_auto_order and collection_success and not ot_sync_done:
                try:
                    logger.info("[Phase 1.95] Order Status -> OT Sync (Phase 1.2에서 미완료, 재시도)")
                    logger.info(LOG_SEPARATOR_THIN)
                    driver = self.collector.get_driver()
                    if driver:
                        from src.collectors.order_status_collector import OrderStatusCollector
                        os_collector = OrderStatusCollector(
                            driver=driver, store_id=self.store_id
                        )

                        # 만료된 pending 클리어 (최대 1일 유효)
                        today_str_for_pending = datetime.now().strftime("%Y-%m-%d")
                        try:
                            clear_result = os_collector.clear_expired_pending(today_str_for_pending)
                            if clear_result.get("cleared_ot", 0) > 0:
                                logger.info(
                                    f"[Phase 1.95] Expired pending cleared: "
                                    f"OT={clear_result['cleared_ot']}, RI={clear_result['cleared_ri']}"
                                )
                        except Exception as e:
                            logger.debug(f"[Phase 1.95] Pending 만료 클리어 실패 (무시): {e}")

                        if os_collector.navigate_to_order_status_menu():
                            sync_result = os_collector.sync_pending_to_order_tracking(
                                days_back=7
                            )
                            logger.info(
                                f"[Phase 1.95] 동기화 완료: "
                                f"신규={sync_result['synced']}건, "
                                f"스킵={sync_result['skipped']}건"
                            )

                            # ★ 발주 정합성 검증: 어제 발주현황 재수집 + order_tracking 대조
                            # BGF에 있는 건 → pending_confirmed=1 (미입고 보정 근거)
                            # BGF에 없는 건 → 무효화 (false positive 제거)
                            try:
                                from datetime import timedelta as _td
                                yesterday_str = (datetime.now() - _td(days=1)).strftime("%Y-%m-%d")
                                bgf_orders = os_collector.collect_yesterday_orders(yesterday_str)
                                if bgf_orders is not None:
                                    bgf_item_set = {o['item_cd'] for o in bgf_orders}
                                    from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository
                                    tracking_repo = OrderTrackingRepository(store_id=self.store_id)
                                    result = tracking_repo.reconcile_with_bgf_orders(
                                        order_date=yesterday_str,
                                        bgf_confirmed_items=bgf_item_set,
                                        store_id=self.store_id,
                                    )
                                    confirmed = result.get('confirmed', 0)
                                    invalidated = result.get('invalidated', 0)
                                    if confirmed > 0 or invalidated > 0:
                                        logger.info(
                                            f"[Phase 1.96] 발주정합성: "
                                            f"BGF확인={confirmed}건, 무효화={invalidated}건"
                                        )
                                    else:
                                        logger.info("[Phase 1.96] 발주정합성: 대조 대상 없음")
                            except Exception as e:
                                logger.warning(f"[Phase 1.96] 발주정합성 검증 실패 (무시): {e}")

                            # 탭 닫기
                            os_collector.close_menu()
                        else:
                            logger.warning("[Phase 1.95] 발주현황 메뉴 이동 실패, 동기화 건너뜀")
                    else:
                        logger.warning("[Phase 1.95] 드라이버 없음, 동기화 건너뜀")
                except Exception as e:
                    logger.warning(f"[Phase 1.95] 발주현황 동기화 실패 (발주 플로우 계속): {e}")
            elif ot_sync_done:
                logger.info("[Phase 1.95] 스킵 (Phase 1.2에서 OT 동기화 완료)")

            # 2. 자동 발주 (수집 성공 시)
            if run_auto_order and collection_success:
                logger.info("[Phase 2] Auto Order")
                logger.info(LOG_SEPARATOR_THIN)

                # 매출분석 탭 닫기
                close_result = self.collector.close_sales_menu()
                logger.info(f"매출분석 탭 닫기: {close_result}")
                time.sleep(DAILY_JOB_AFTER_CLOSE_TAB)

                # Phase 2 시작 전 잔여 탭 강제 정리 (이전 Phase 탭 잔류 방지)
                driver = self.collector.get_driver()
                if driver:
                    from src.settings.ui_config import FRAME_IDS
                    _cleanup_dangling_tabs(driver, [
                        FRAME_IDS.get("WASTE_SLIP", "STGJ020_M0"),
                        FRAME_IDS.get("ORDER_STATUS", "STBJ070_M0"),
                        FRAME_IDS.get("NEW_PRODUCT_STATUS", "SS_STBJ460_M0"),
                        FRAME_IDS.get("RECEIVING", "STGJ010_M0"),
                        "STMB010_M0",  # 시간대별 매출 탭 (Phase 1.04/1.05 잔여)
                    ])

                # 드라이버 재사용하여 발주
                driver = self.collector.get_driver()
                if driver:
                    # Phase 1.2에서 DB 캐시 갱신 성공했으면 사이트 재조회 건너뜀
                    exclusion_cached = bool(exclusion_stats and exclusion_stats.get("success"))
                    if not exclusion_cached:
                        logger.info("[Phase 2] 제외 상품 캐시 없음 — 사이트 직접 조회")
                    order_result = self._run_auto_order_with_driver(
                        driver, use_improved_predictor,
                        skip_exclusion_fetch=exclusion_cached,
                        target_dates=getattr(self, '_target_dates', None),
                    )

                    # 2.5 Q5 만료 재고 처리 내역 카카오 알림
                    q5_data = order_result.get("q5_expired_stock")
                    if q5_data and q5_data.get("total_skus", 0) > 0:
                        try:
                            q5_lines = [
                                f"[만료재고 처리] 만료 차감 발생: {q5_data['total_skus']}개 SKU"
                            ]
                            for qi in q5_data.get("applied_items", [])[:3]:
                                q5_lines.append(
                                    f"  {qi.get('item_nm', qi['item_cd'])[:15]} "
                                    f"재고{qi['stock_before']}→{qi['stock_after']} "
                                    f"(만료분 차감)"
                                )
                            q5_msg = "\n".join(q5_lines)
                            logger.info(f"[Q5] 카카오 알림:\n{q5_msg}")

                            from src.notification.kakao_notifier import KakaoNotifier
                            from src.settings.constants import DEFAULT_REST_API_KEY
                            _notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
                            if _notifier.access_token:
                                _notifier.send_message(q5_msg)
                        except Exception as e_q5:
                            logger.debug(f"[Q5] 카카오 알림 실패: {e_q5}")

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
            clear_session_id()
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
            nav_ok = collector.navigate_to_order_status_menu()
            fallback_normal_only = False

            if not nav_ok:
                logger.warning("발주 현황 조회 메뉴 이동 실패 - 3초 후 수동발주 수집 위해 재시도")
                time.sleep(3)
                nav_ok = collector.navigate_to_order_status_menu()
                if nav_ok:
                    fallback_normal_only = True
                    logger.info("메뉴 이동 재시도 성공 - 일반(수동) 발주만 수집")
                else:
                    logger.error("메뉴 이동 재시도 실패 - 수동발주 수집 불가")
                    return result

            if not fallback_normal_only:
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

            # 일반(수동) 발주 수집 (푸드 차감용) — 항상 실행
            from src.infrastructure.database.repos import ManualOrderItemRepository
            normal_items = collector.collect_normal_order_items()
            if normal_items is not None:
                from datetime import datetime as _dt
                today_str = _dt.now().strftime("%Y-%m-%d")
                saved = ManualOrderItemRepository(store_id=self.store_id).refresh(
                    normal_items, order_date=today_str, store_id=self.store_id
                )
                result["normal_count"] = saved
                food_count = sum(
                    1 for it in normal_items
                    if it.get("mid_cd", "") in ("001", "002", "003", "004", "005", "012")
                )
                logger.info(
                    f"일반(수동) 발주 {len(normal_items)}개 수집 "
                    f"(푸드: {food_count}, 비푸드: {len(normal_items) - food_count}), "
                    f"DB {saved}건 갱신"
                )
            else:
                logger.warning("일반(수동) 발주 사이트 조회 실패")

            result["success"] = True

            # ★ OT 동기화를 같은 메뉴 세션에서 수행 (~5-8초 절약)
            # Phase 1.95의 로직을 여기서 실행하여 메뉴 중복 진입 방지
            try:
                # 만료된 pending 클리어 (최대 1일 유효)
                today_str_for_pending = datetime.now().strftime("%Y-%m-%d")
                try:
                    clear_result = collector.clear_expired_pending(today_str_for_pending)
                    if clear_result.get("cleared_ot", 0) > 0:
                        logger.info(
                            f"[Phase 1.2→OT] Expired pending cleared: "
                            f"OT={clear_result['cleared_ot']}, RI={clear_result['cleared_ri']}"
                        )
                except Exception as e:
                    logger.debug(f"[Phase 1.2→OT] Pending 만료 클리어 실패 (무시): {e}")

                # 이미 메뉴가 열려 있으므로 navigate 없이 바로 동기화
                sync_result = collector.sync_pending_to_order_tracking(days_back=7)
                logger.info(
                    f"[Phase 1.2→OT] 동기화 완료: "
                    f"신규={sync_result['synced']}건, "
                    f"스킵={sync_result['skipped']}건"
                )
                result["ot_sync_done"] = True
            except Exception as e:
                logger.warning(f"[Phase 1.2→OT] OT 동기화 실패 (Phase 1.95에서 재시도): {e}")
                result["ot_sync_done"] = False
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
        skip_exclusion_fetch: bool = False,
        target_dates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        기존 드라이버로 자동 발주 실행 (DailyOrderFlow 위임)

        Args:
            driver: Selenium WebDriver
            use_improved_predictor: True면 개선된 예측기 사용 (31일 데이터 기반)
            skip_exclusion_fetch: True면 자동/스마트발주 목록 사이트 재조회 건너뜀
            target_dates: 발주할 날짜 목록 (YYYY-MM-DD). None이면 전체 발주
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
                target_dates=target_dates,
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
        """날씨 데이터를 DB에 저장 (매장별 store_id 격리)"""
        try:
            sid = self.store_id  # 매장별 날씨 격리

            if weather.get("temperature") is not None:
                self.weather_repo.save_factor(
                    factor_date=target_date,
                    factor_type="weather",
                    factor_key="temperature",
                    factor_value=str(weather["temperature"]),
                    store_id=sid
                )

            if weather.get("weather_type"):
                self.weather_repo.save_factor(
                    factor_date=target_date,
                    factor_type="weather",
                    factor_key="weather_type",
                    factor_value=weather["weather_type"],
                    store_id=sid
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
                        factor_value=str(ftemp),
                        store_id=sid
                    )
                logger.info(f"Forecast saved: {forecast_daily}")

            # 강수 예보 저장 (강수확률/강수량/강수유형/날씨설명/눈여부)
            forecast_precipitation = weather.get("forecast_precipitation", {})
            if forecast_precipitation:
                for fdate, precip in forecast_precipitation.items():
                    if precip.get("rain_rate") is not None:
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="rain_rate_forecast",
                            factor_value=str(precip["rain_rate"]),
                            store_id=sid
                        )
                    if precip.get("rain_qty") is not None:
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="rain_qty_forecast",
                            factor_value=str(precip["rain_qty"]),
                            store_id=sid
                        )
                    if precip.get("rain_type_nm"):
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="rain_type_nm_forecast",
                            factor_value=precip["rain_type_nm"],
                            store_id=sid
                        )
                    if precip.get("weather_cd_nm"):
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="weather_cd_nm_forecast",
                            factor_value=precip["weather_cd_nm"],
                            store_id=sid
                        )
                    if precip.get("is_snow"):
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="is_snow_forecast",
                            factor_value="1",
                            store_id=sid
                        )
                    # Phase A-4: 미세먼지 등급 저장
                    if precip.get("dust_grade"):
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="dust_grade_forecast",
                            factor_value=precip["dust_grade"],
                            store_id=sid
                        )
                    if precip.get("fine_dust_grade"):
                        self.weather_repo.save_factor(
                            factor_date=fdate,
                            factor_type="weather",
                            factor_key="fine_dust_grade_forecast",
                            factor_value=precip["fine_dust_grade"],
                            store_id=sid
                        )
                logger.info(
                    f"Precipitation forecast saved: {list(forecast_precipitation.keys())} "
                    f"(dust_src={weather.get('dust_source', '?')})"
                )

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


def run_second_delivery_adjustment(store_id: Optional[str] = None) -> Dict[str, Any]:
    """
    D-1: 2차 배송 보정 (14:00 실행)

    Phase 1: DB 판단 (Selenium 없음) → 부스트 대상 결정
    Phase 2: 부스트 대상 있을 때만 Selenium 세션 시작 → 추가 발주

    Args:
        store_id: 매장 코드 (None이면 전체 활성 매장)

    Returns:
        실행 결과
    """
    from src.analysis.second_delivery_adjuster import (
        run_second_delivery_adjustment as _run_adjustment,
        execute_boost_orders,
    )

    if store_id is None:
        from src.settings.store_context import StoreContext
        store_ids = [ctx.store_id for ctx in StoreContext.get_all_active()]
    else:
        store_ids = [store_id]

    all_results = {}

    for sid in store_ids:
        set_session_id(f"D1_{sid}")
        logger.info(LOG_SEPARATOR_WIDE)
        logger.info(f"[D-1] 2차 배송 보정 시작 (store={sid})")

        try:
            # Phase 1: DB 판단 (Selenium 불필요)
            result = _run_adjustment(sid)

            # Phase 2: 부스트 대상 있을 때만 Selenium
            if result.boost_orders:
                logger.info(f"[D-1] 부스트 대상 {len(result.boost_orders)}개 → Selenium 세션 시작")
                try:
                    from src.collectors.bgf_collector import BGFCollector
                    collector = BGFCollector(store_id=sid)
                    driver = collector.login()
                    if driver:
                        exec_result = execute_boost_orders(result, driver)
                        collector.close()
                        all_results[sid] = {
                            "success": True,
                            "boost_targets": result.boost_targets,
                            "executed": exec_result["executed"],
                            "failed": exec_result["failed"],
                            "reduce_logged": result.reduce_logged,
                        }
                    else:
                        logger.warning(f"[D-1] 로그인 실패 (store={sid})")
                        all_results[sid] = {"success": False, "error": "login failed"}
                except Exception as e:
                    logger.error(f"[D-1] Selenium 실행 실패 (store={sid}): {e}")
                    all_results[sid] = {"success": False, "error": str(e)}
            else:
                logger.info(f"[D-1] 부스트 대상 없음 → Selenium 생략")
                all_results[sid] = {
                    "success": True,
                    "boost_targets": 0,
                    "reduce_logged": result.reduce_logged,
                    "skipped": result.skipped,
                }

        except Exception as e:
            logger.error(f"[D-1] 실행 실패 (store={sid}): {e}")
            all_results[sid] = {"success": False, "error": str(e)}
        finally:
            clear_session_id()

    return all_results


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
