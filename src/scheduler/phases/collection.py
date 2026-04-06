"""Phase 1.0~1.35: 데이터 수집

daily_job.py run_optimized()에서 분리된 수집 Phase.
- Phase 1.0: 기본 수집 (판매/날씨/캘린더)
- Phase 1.04: 시간대별 매출 수집
- Phase 1.05: 시간대별 매출 상세 수집
- Phase 1.01: 이상치 탐지
- Phase 1.1: 입고 데이터 수집
- Phase 1.15: 폐기 전표 수집/검증
- Phase 1.16: 폐기 동기화
- Phase 1.2: 발주 제외 상품 수집
- Phase 1.3: 신상품 도입 현황 수집
- Phase 1.35: 신제품 라이프사이클 모니터링
"""

import time
from datetime import datetime
from typing import Dict, Any, List

from src.utils.logger import get_logger, phase_timer
from src.collectors.calendar_collector import save_calendar_info
from src.collectors.receiving_collector import ReceivingCollector
from src.settings.constants import LOG_SEPARATOR_THIN

logger = get_logger(__name__)


class _SkipPhase(Exception):
    """collect_only 모드에서 불필요한 Phase를 스킵하기 위한 내부 예외"""
    pass


def run_collection_phases(ctx: Dict[str, Any], job: Any) -> Dict[str, Any]:
    """Phase 1.0~1.35 수집 Phase 실행

    Args:
        ctx: 공유 상태 dict
        job: DailyCollectionJob 인스턴스 (헬퍼 메서드 호출용)

    Returns:
        업데이트된 ctx
    """
    today_str = ctx["today_str"]
    yesterday_str = ctx["yesterday_str"]
    collect_only = ctx.get("collect_only")  # None이면 전체 Phase 실행

    # ★ collect_only 모드에서 sales 스킵 시 로그인만 수행
    if collect_only and "sales" not in collect_only:
        with phase_timer("login", "BGF Login (lightweight)",
                         store_id=job.store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            job.collector._ensure_login()
            ctx["collection_success"] = True
            ctx["collection_results"] = {}
            ctx["dates_to_collect"] = [yesterday_str, today_str]
            logger.info(f"[collect_only] 로그인 완료, Phase: {collect_only}")
        collection_success = True
    else:
        # Phase 1.0: 기본 수집 (판매/날씨/캘린더)
        with phase_timer("1.0", "Data Collection",
                     store_id=job.store_id, _logger=logger,
                     timings=ctx.get("_phase_timings")):
        dates_to_collect = [yesterday_str, today_str]
        ctx["dates_to_collect"] = dates_to_collect

        def save_callback(date_str: str, data: List[Dict[str, Any]]) -> Dict[str, Any]:
            stats = job.repository.save_daily_sales(
                sales_data=data,
                sales_date=date_str,
                store_id=job.store_id,
                collected_at=datetime.now().isoformat()
            )
            job.repository.log_collection(
                collected_at=datetime.now().isoformat(),
                sales_date=date_str,
                stats=stats,
                status="success",
                duration=0
            )
            return stats

        collection_results = job.collector.collect_multiple_dates(
            dates=dates_to_collect,
            save_callback=save_callback
        )
        ctx["collection_results"] = collection_results

        # 날씨/캘린더 저장
        weather = job.collector.weather_data
        if weather:
            job._save_weather_data(today_str, weather)
        try:
            save_calendar_info(today_str, job.weather_repo)
        except Exception as e:
            logger.warning(f"스케줄러 정리 실패: {e}")

        # 수집 성공 여부 확인
        collection_success = any(
            r.get("success") for r in collection_results.values()
        )
        ctx["collection_success"] = collection_success
        logger.info(f"수집 결과: {list(collection_results.keys())}, 성공={collection_success}")

    # ── collect_only 가드: None이면 전체 모드 ──
    _full = collect_only is None

    # ★ Phase 1.04: 시간대별 매출 수집 (STMB010 Direct API)
    if _full and collection_success:
        try:
            with phase_timer("1.04", "Hourly Sales Collection (STMB010)",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                driver = job.collector.get_driver()
                if driver:
                    from src.collectors.hourly_sales_collector import HourlySalesCollector
                    from src.infrastructure.database.repos.hourly_sales_repo import HourlySalesRepository
                    hourly_collector = HourlySalesCollector(driver=driver)
                    hourly_repo = HourlySalesRepository(store_id=job.store_id)
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
    # ※ collection_success 독립: Direct API는 Phase 1.0 팝업과 독립, driver만 필요
    try:
        if not _full:
            raise _SkipPhase()
        with phase_timer("1.05", "Hourly Sales Detail Collection (selPrdT3)",
                         store_id=job.store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            driver = job.collector.get_driver()
            if driver:
                from src.collectors.hourly_sales_detail_collector import HourlySalesDetailCollector
                from src.infrastructure.database.repos.hourly_sales_detail_repo import HourlySalesDetailRepository
                hsd_collector = HourlySalesDetailCollector(driver=driver)
                hsd_repo = HourlySalesDetailRepository(store_id=job.store_id)
                hsd_repo.ensure_table()

                # 당일 + 어제 수집 (기존 동작)
                for date_str in [yesterday_str, today_str]:
                    collected = hsd_repo.get_collected_hours(date_str)
                    if date_str == today_str:
                        from datetime import datetime as _dt
                        target_hours = [h for h in range(0, _dt.now().hour) if h not in collected]
                    else:
                        target_hours = [h for h in range(24) if h not in collected]
                    if not target_hours:
                        logger.debug(f"[Phase 1.05] {date_str}: 이미 수집 완료")
                        continue
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

                # ★ 최근 3일 누락 시간대 자동 보충 (backfill)
                from datetime import timedelta as _td
                backfill_total = 0
                for offset in range(2, 5):  # D-2, D-3, D-4
                    bf_date = (datetime.now() - _td(days=offset)).strftime('%Y-%m-%d')
                    bf_collected = hsd_repo.get_collected_hours(bf_date)
                    bf_missing = [h for h in range(24) if h not in bf_collected]
                    if not bf_missing:
                        continue
                    logger.debug(
                        f"[Phase 1.05] backfill {bf_date}: "
                        f"{len(bf_missing)}시간대 누락 감지 "
                        f"(수집완료={len(bf_collected)}/24)"
                    )
                    bf_results = hsd_collector.collect_all_hours(
                        bf_date.replace('-', ''), hours=bf_missing, delay=0.25
                    )
                    bf_items = 0
                    for hour, items in bf_results.items():
                        if items:
                            hsd_repo.save_detail(bf_date, hour, items)
                            bf_items += len(items)
                    if bf_items > 0:
                        backfill_total += bf_items
                        logger.info(
                            f"[Phase 1.05] backfill {bf_date}: "
                            f"{len(bf_results)}/{len(bf_missing)}시간대, {bf_items}품목"
                        )
                if backfill_total > 0:
                    logger.info(f"[Phase 1.05] backfill 총 {backfill_total}품목 보충")

                # 재시도
                if hsd_collector.failed_count > 0:
                    hsd_collector.retry_failed()
            else:
                logger.debug("[Phase 1.05] 드라이버 없음, 건너뜀")
    except _SkipPhase:
        pass
    except Exception as e:
        logger.warning(f"[Phase 1.05] 시간대별 매출 상세 수집 실패 (발주 플로우 계속): {e}")

    # ★ Phase 1.06: 소진율 곡선 갱신 (food_popularity_curve)
    if _full and collection_success:
        try:
            with phase_timer("1.06", "Food Depletion Curve Update",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                from src.application.services.food_depletion_service import FoodDepletionService
                service = FoodDepletionService(store_id=job.store_id)
                curve_result = service.update_curves_daily()
                logger.info(
                    f"[Phase 1.06] 곡선 갱신: "
                    f"{curve_result.get('updated', 0)}건, "
                    f"{curve_result.get('skipped', 0)}건 스킵"
                )
        except Exception as e:
            logger.warning(f"[Phase 1.06] 소진율 곡선 갱신 실패 (발주 플로우 계속): {e}")

    # ★ Phase 1.01: 이상치 탐지 (D-2)
    if _full and collection_success:
        try:
            with phase_timer("1.01", "Anomaly Detection (D-2)",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                from src.analysis.anomaly_detector import run_anomaly_detection
                anomaly_result = run_anomaly_detection(job.store_id, today_str)
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
    if _full and collection_success:
        try:
            with phase_timer("1.1", "Receiving Collection",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                driver = job.collector.get_driver()
                if driver:
                    recv_collector = ReceivingCollector(driver=driver, store_id=job.store_id)

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

                        # ★ 2차 입고 매칭: 어제 발주 스냅샷 vs 오늘 입고
                        try:
                            from src.application.use_cases.delivery_match_flow import (
                                match_confirmed_with_receiving,
                            )
                            match_result = match_confirmed_with_receiving(
                                job.store_id, "2차"
                            )
                            logger.info(f"2차 입고 매칭: {match_result}")
                        except Exception as me:
                            logger.warning(f"2차 입고 매칭 실패 (수집은 완료): {me}")

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
            with phase_timer("1.15", "Waste Slip Collection",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                driver = job.collector.get_driver()
                if driver:
                    from src.collectors.waste_slip_collector import WasteSlipCollector
                    ws_collector = WasteSlipCollector(driver=driver, store_id=job.store_id)
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
                        verifier = WasteVerificationService(store_id=job.store_id)
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
        driver = job.collector.get_driver()
        if driver:
            from src.settings.ui_config import FRAME_IDS
            from src.scheduler.daily_job import _cleanup_dangling_tabs
            _cleanup_dangling_tabs(driver, [
                FRAME_IDS.get("WASTE_SLIP", "STGJ020_M0"),
            ])

    ctx["waste_slip_stats"] = waste_slip_stats

    # ★ Phase 1.16: 폐기전표 -> daily_sales.disuse_qty 동기화
    if waste_slip_stats and waste_slip_stats.get("success"):
        try:
            with phase_timer("1.16", "Waste Slip -> Daily Sales Sync",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                from src.application.services.waste_disuse_sync_service import (
                    WasteDisuseSyncService,
                )
                syncer = WasteDisuseSyncService(store_id=job.store_id)
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

    # ★ Phase 1.17: 상품 유통기한 관리 수집 (BGF STCM130_M0)
    if _full and collection_success:
        try:
            with phase_timer("1.17", "Expiry Management Collection",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                driver = job.collector.get_driver()
                if driver:
                    from src.collectors.expiry_management_collector import ExpiryManagementCollector
                    em_collector = ExpiryManagementCollector(driver=driver, store_id=job.store_id)
                    em_result = em_collector.collect()
                    logger.info(f"유통기한 관리 수집: {em_result.get('count', 0)}건")
                    em_collector.close_tab()
                else:
                    logger.warning("드라이버 없음, 유통기한 관리 수집 건너뜀")
        except Exception as e:
            logger.warning(f"유통기한 관리 수집 실패 (발주 플로우 계속): {e}")

    # ★ Phase 1.2: 발주 제외 상품 수집 (자동/스마트)
    exclusion_stats = None
    if _full and collection_success:
        try:
            with phase_timer("1.2", "Exclusion Items Collection",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                driver = job.collector.get_driver()
                if driver:
                    exclusion_stats = job._collect_exclusion_items(driver)
                else:
                    logger.warning("드라이버 없음, 제외 상품 수집 건너뜀")
        except Exception as e:
            logger.warning(f"제외 상품 수집 실패 — Phase 2에서 사이트 재조회로 복구됩니다: {e}")

    ctx["exclusion_stats"] = exclusion_stats

    # ★ Phase 1.3: 신상품 도입 현황 수집 (3일발주 후속 관리용)
    new_product_stats = None
    from src.settings.constants import NEW_PRODUCT_MODULE_ENABLED
    if _full and collection_success and NEW_PRODUCT_MODULE_ENABLED:
        try:
            with phase_timer("1.3", "New Product Introduction Collection",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                driver = job.collector.get_driver()
                if driver:
                    new_product_stats = job._collect_new_product_info(driver)
                else:
                    logger.warning("드라이버 없음, 신상품 수집 건너뜀")
        except Exception as e:
            logger.warning(f"신상품 수집 실패 (발주 플로우 계속): {e}")

    ctx["new_product_stats"] = new_product_stats

    # ★ Phase 1.35: 신제품 라이프사이클 모니터링
    if _full and collection_success:
        try:
            with phase_timer("1.35", "New Product Lifecycle Monitoring",
                             store_id=job.store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                from src.application.services.new_product_monitor import NewProductMonitor
                monitor = NewProductMonitor(store_id=job.store_id)
                monitor_stats = monitor.run()
                if monitor_stats["active_items"] > 0:
                    logger.info(
                        f"[Phase 1.35] 모니터링: {monitor_stats['active_items']}건, "
                        f"추적저장: {monitor_stats['tracking_saved']}건, "
                        f"상태변경: {len(monitor_stats['status_changes'])}건"
                    )
        except Exception as e:
            logger.warning(f"신제품 모니터링 실패 (발주 플로우 계속): {e}")

    # ★ Phase 1.36: 데이터 보존 정책 (오래된 기록 자동 삭제)
    try:
        if not _full:
            raise _SkipPhase()
        with phase_timer("1.36", "Data Retention Cleanup",
                         store_id=job.store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.infrastructure.database.connection import DBRouter
            from datetime import timedelta

            retention_rules = {
                "eval_outcomes": ("eval_date", 90),
                "prediction_logs": ("prediction_date", 60),
                "hourly_sales_detail": ("sales_date", 90),
                "calibration_history": ("calibration_date", 120),
            }

            purge_conn = DBRouter.get_store_connection(job.store_id)
            purge_results = {}
            for table, (date_col, days) in retention_rules.items():
                cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                cursor = purge_conn.execute(
                    f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,)
                )
                if cursor.rowcount > 0:
                    purge_results[table] = cursor.rowcount
            purge_conn.commit()
            purge_conn.close()

            if purge_results:
                logger.info(f"[Phase 1.36] 데이터 정리: {purge_results}")
    except _SkipPhase:
        pass
    except Exception as e:
        logger.warning(f"[Phase 1.36] 데이터 정리 실패 (무시): {e}")

    return ctx
