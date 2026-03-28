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

        # Phase 간 공유 상태
        ctx = {
            "store_id": self.store_id,
            "today_str": today_str,
            "yesterday_str": yesterday_str,
            "run_auto_order": run_auto_order,
            "use_improved_predictor": use_improved_predictor,
            "target_dates": target_dates,
            "collection_success": False,
            "collection_results": {},
            "exclusion_stats": None,
            "waste_slip_stats": None,
            "calibration_result": None,
            "order_result": None,
            "fail_reason_result": None,
            "new_product_stats": None,
        }

        try:
            # Phase별 시간 측정
            phase_timings = {}

            # Phase 1.0~1.35: 데이터 수집
            from src.scheduler.phases.collection import run_collection_phases
            _t0 = time.time()
            ctx = run_collection_phases(ctx, self)
            phase_timings["collection"] = round(time.time() - _t0, 1)

            # Phase 1.5~1.67: 보정/검증 (수집 성공 시)
            _t0 = time.time()
            if ctx["collection_success"]:
                from src.scheduler.phases.calibration import run_calibration_phases
                ctx = run_calibration_phases(ctx)
            phase_timings["calibration"] = round(time.time() - _t0, 1)

            # Phase 1.68~1.95: 발주 준비 (수집 성공 시)
            _t0 = time.time()
            if ctx["collection_success"]:
                from src.scheduler.phases.preparation import run_preparation_phases
                ctx = run_preparation_phases(ctx, self)
            phase_timings["preparation"] = round(time.time() - _t0, 1)

            # Phase 2.0~3.0: 발주 실행
            from src.scheduler.phases.execution import run_execution_phases
            _t0 = time.time()
            ctx = run_execution_phases(ctx, self)
            phase_timings["execution"] = round(time.time() - _t0, 1)

            # 결과 통합
            collection_results = ctx["collection_results"]
            total_items = sum(
                r.get("item_count", 0) for r in collection_results.values()
                if r.get("success")
            )

            return {
                "success": ctx["collection_success"],
                "collection": collection_results,
                "exclusion": ctx["exclusion_stats"],
                "new_product": ctx["new_product_stats"],
                "order": ctx["order_result"],
                "fail_reasons": ctx["fail_reason_result"],
                "dates_collected": ctx.get("dates_to_collect", [yesterday_str, today_str]),
                "total_items": total_items,
                "duration": time.time() - start_time,
                "phase_timings": phase_timings
            }

        except Exception as e:
            logger.error(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "collection": ctx["collection_results"],
                "order": ctx["order_result"],
                "fail_reasons": ctx["fail_reason_result"],
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

        # 폐기 위험 알림: ���도 프로세스로 실행 (Phase 2 차단 방지)
        self._send_expiry_alert_async()

    def _send_expiry_alert_async(self) -> None:
        """폐기 위험 알림을 별도 프로세스로 실행 (블로킹 방지)"""
        import subprocess
        import sys

        try:
            logger.info("Checking expiry alerts (subprocess)...")
            script_path = str(Path(__file__).parent.parent.parent / "scripts" / "run_expiry_alert.py")
            proc = subprocess.Popen(
                [sys.executable, script_path, "--store", str(self.store_id)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=30)
                logger.info(f"Expiry alert subprocess completed (exit={proc.returncode})")
            except subprocess.TimeoutExpired:
                logger.warning("Expiry alert subprocess timed out (30s) — killing")
                proc.kill()
                proc.wait(timeout=5)
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
