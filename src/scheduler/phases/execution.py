"""Phase 2.0~3.0: 발주 실행

daily_job.py run_optimized()에서 분리된 발주 실행 Phase.
- Phase 2.0: 자동 발주
- Q5: 만료재고 알림
- Phase 3.0: 실패사유 수집
"""

import time
from typing import Dict, Any

from src.utils.logger import get_logger, phase_timer
from src.settings.constants import LOG_SEPARATOR_THIN
from src.settings.timing import DAILY_JOB_AFTER_CLOSE_TAB

logger = get_logger(__name__)


def run_execution_phases(ctx: Dict[str, Any], job: Any) -> Dict[str, Any]:
    """Phase 2.0~3.0 발주 실행 Phase

    Args:
        ctx: 공유 상태 dict
        job: DailyCollectionJob 인스턴스 (헬퍼 메서드 호출용)

    Returns:
        업데이트된 ctx
    """
    run_auto_order = ctx["run_auto_order"]
    collection_success = ctx["collection_success"]
    use_improved_predictor = ctx["use_improved_predictor"]
    dry_run = ctx.get("dry_run", False)
    exclusion_stats = ctx.get("exclusion_stats")
    order_result = None
    fail_reason_result = None

    # 2. 자동 발주 (수집 성공 시)
    if run_auto_order and collection_success:
        try:
            with phase_timer("2.0", "Auto Order",
                             store_id=ctx.get("store_id", ""), _logger=logger,
                             timings=ctx.get("_phase_timings")):
                # 매출분석 탭 닫기
                close_result = job.collector.close_sales_menu()
                logger.info(f"매출분석 탭 닫기: {close_result}")
                time.sleep(DAILY_JOB_AFTER_CLOSE_TAB)

                # Phase 2 시작 전 잔여 탭 강제 정리 (이전 Phase 탭 잔류 방지)
                driver = job.collector.get_driver()
                if driver:
                    from src.settings.ui_config import FRAME_IDS
                    from src.scheduler.daily_job import _cleanup_dangling_tabs
                    _cleanup_dangling_tabs(driver, [
                        FRAME_IDS.get("WASTE_SLIP", "STGJ020_M0"),
                        FRAME_IDS.get("ORDER_STATUS", "STBJ070_M0"),
                        FRAME_IDS.get("NEW_PRODUCT_STATUS", "SS_STBJ460_M0"),
                        FRAME_IDS.get("RECEIVING", "STGJ010_M0"),
                        "STMB010_M0",  # 시간대별 매출 탭 (Phase 1.04/1.05 잔여)
                    ])

                # 드라이버 재사용하여 발주
                driver = job.collector.get_driver()
                if driver:
                    # Phase 1.2에서 DB 캐시 갱신 성공했으면 사이트 재조회 건너뜀
                    exclusion_cached = bool(exclusion_stats and exclusion_stats.get("success"))
                    if not exclusion_cached:
                        logger.info("[Phase 2] 제외 상품 캐시 없음 — 사이트 직접 조회")
                    order_result = job._run_auto_order_with_driver(
                        driver, use_improved_predictor,
                        skip_exclusion_fetch=exclusion_cached,
                        target_dates=ctx.get("target_dates"),
                        dry_run=dry_run,
                    )

                    # ★ Phase 2 발주 결과 요약 로그
                    if order_result:
                        success_count = order_result.get("success_count", 0)
                        fail_count = order_result.get("fail_count", 0)
                        logger.info(
                            f"[Phase 2] Order Summary | "
                            f"success={success_count} fail={fail_count} "
                            f"[store={ctx.get('store_id', '')}]"
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
                        fail_reason_result = job._run_fail_reason_collection(driver)

                else:
                    logger.warning("Driver not available, skipping auto-order")
        except Exception as e:
            logger.warning(f"[Phase 2.0] 자동 발주 실패: {e}")
    else:
        logger.info(f"자동발주 스킵: run_auto_order={run_auto_order}, collection_success={collection_success}")

    ctx["order_result"] = order_result
    ctx["fail_reason_result"] = fail_reason_result

    # ★ dry_run 시 Excel 리포트 자동 생성
    if dry_run and order_result:
        try:
            order_list = order_result.get("order_list", [])
            if order_list:
                from pathlib import Path
                from datetime import datetime as _dt

                export_dir = (
                    Path(__file__).parent.parent.parent.parent / "data" / "exports"
                )
                export_dir.mkdir(parents=True, exist_ok=True)
                timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                store_id = ctx.get("store_id", "unknown")
                xlsx_path = str(
                    export_dir / f"dry_order_{store_id}_{timestamp}.xlsx"
                )

                from scripts.run_full_flow import create_dryrun_excel
                create_dryrun_excel(
                    order_list, output_path=xlsx_path, store_id=store_id
                )
                logger.info(
                    f"[DryRun] Excel 리포트 생성: {xlsx_path} "
                    f"({len(order_list)}건)"
                )
            else:
                logger.info("[DryRun] 발주 목록 없음 — Excel 생성 스킵")
        except ImportError:
            logger.debug("[DryRun] Excel 생성 함수 미사용 가능 (run_full_flow 미임포트)")
        except Exception as e:
            logger.warning(f"[DryRun] Excel 리포트 생성 실패 (무시): {e}")

    return ctx
