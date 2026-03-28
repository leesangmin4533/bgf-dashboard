"""Phase 1.68~1.95: 발주 준비

daily_job.py run_optimized()에서 분리된 발주 준비 Phase.
- Phase 1.68: DirectAPI 실시간 재고 갱신
- Phase 1.7: 예측 로깅
- Phase 1.95: 발주현황 -> OT 동기화
"""

import time
from datetime import datetime
from typing import Dict, Any

from src.utils.logger import get_logger
from src.settings.constants import LOG_SEPARATOR_THIN

logger = get_logger(__name__)


def run_preparation_phases(ctx: Dict[str, Any], job: Any) -> Dict[str, Any]:
    """Phase 1.68~1.95 발주 준비 Phase 실행

    Args:
        ctx: 공유 상태 dict (collection_success=True 전제)
        job: DailyCollectionJob 인스턴스 (헬퍼 메서드 호출용)

    Returns:
        업데이트된 ctx
    """
    store_id = ctx["store_id"]
    run_auto_order = ctx["run_auto_order"]
    collection_success = ctx["collection_success"]
    today_str = ctx["today_str"]
    yesterday_str = ctx["yesterday_str"]
    exclusion_stats = ctx.get("exclusion_stats")

    # ★ Phase 1.68: DirectAPI 실시간 재고 갱신 (예측 전)
    # stale RI 상품의 NOW_QTY를 BGF selSearch로 조회하여 RI 갱신
    if run_auto_order and collection_success:
        try:
            from src.infrastructure.database.repos.inventory_repo import (
                RealtimeInventoryRepository,
            )
            ri_repo = RealtimeInventoryRepository(store_id=store_id)
            stale_items = ri_repo.get_stale_stock_item_codes(store_id=store_id)

            # ★ 행사 사전 동기화: product_details에 행사 있지만 promotions 미등록 상품
            from src.settings.constants import PROMO_SYNC_ENABLED
            promo_missing_items = []
            if PROMO_SYNC_ENABLED:
                promo_missing_items = ri_repo.get_promo_missing_item_codes(
                    store_id=store_id
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
                driver = job.collector.get_driver()
                if driver:
                    from src.collectors.order_prep_collector import OrderPrepCollector
                    stock_collector = OrderPrepCollector(
                        driver=driver,
                        store_id=store_id,
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
            predictor = ImprovedPredictor(store_id=store_id)
            logged = predictor.predict_and_log()
            logger.info(f"예측 로깅 완료: {logged}건")

            # 푸드 카테고리 예측 요약 (과소/과다 발주 진단용)
            try:
                job._log_food_prediction_summary()
            except Exception as summary_err:
                logger.debug(f"푸드 예측 요약 로그 실패 (비치명적): {summary_err}")
        except Exception as e:
            logger.warning(f"예측 로깅 실패 (발주 플로우 계속): {e}")
    elif collection_success and run_auto_order:
        logger.info("[Phase 1.7] 스킵 (Phase 2에서 예측+로깅 수행)")

    # 카카오톡 리포트 발송
    collection_results = ctx["collection_results"]
    if collection_results.get(today_str, {}).get("success"):
        job._send_kakao_report(today_str, {"success": True})
    elif collection_results.get(yesterday_str, {}).get("success"):
        job._send_kakao_report(yesterday_str, {"success": True})

    # ★ Phase 1.95: 발주현황 → OT 동기화 (수동발주 pending 인식)
    # Phase 1.2에서 이미 OT 동기화 완료 시 스킵 (~5-8초 절약)
    ot_sync_done = (exclusion_stats or {}).get("ot_sync_done", False)
    if run_auto_order and collection_success and not ot_sync_done:
        try:
            logger.info("[Phase 1.95] Order Status -> OT Sync (Phase 1.2에서 미완료, 재시도)")
            logger.info(LOG_SEPARATOR_THIN)
            driver = job.collector.get_driver()
            if driver:
                from src.collectors.order_status_collector import OrderStatusCollector
                os_collector = OrderStatusCollector(
                    driver=driver, store_id=store_id
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
                            tracking_repo = OrderTrackingRepository(store_id=store_id)
                            result = tracking_repo.reconcile_with_bgf_orders(
                                order_date=yesterday_str,
                                bgf_confirmed_items=bgf_item_set,
                                store_id=store_id,
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

    return ctx
