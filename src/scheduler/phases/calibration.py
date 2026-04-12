"""Phase 1.5~1.67: 보정/검증

daily_job.py run_optimized()에서 분리된 보정 Phase.
- Phase 1.5: 사전 발주 평가 보정
- Phase 1.55: 폐기 원인 분석
- Phase 1.56: 푸드 폐기율 자동 보정
- Phase 1.57: Bayesian 파라미터 최적화 (주 1회)
- Phase 1.58: Croston alpha/beta 최적화 (주 1회)
- Phase 1.6: 예측 실적 소급
- Phase 1.61: 수요 패턴 분류 DB 갱신
- Phase 1.65: 유령 재고 정리
- Phase 1.66: 배치 FIFO 재동기화
- Phase 1.67: 데이터 무결성 검증
"""

from datetime import datetime
from typing import Dict, Any

from src.utils.logger import get_logger, phase_timer
from src.prediction.eval_calibrator import EvalCalibrator
from src.prediction.eval_reporter import EvalReporter
from src.prediction.accuracy import AccuracyTracker
from src.settings.constants import LOG_SEPARATOR_THIN

logger = get_logger(__name__)


def run_calibration_phases(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 1.5~1.67 보정/검증 Phase 실행

    Args:
        ctx: 공유 상태 dict (collection_success=True 전제)

    Returns:
        업데이트된 ctx
    """
    store_id = ctx["store_id"]
    yesterday_str = ctx["yesterday_str"]

    # ★ 사전 발주 평가 보정 (수집 성공 시)
    calibration_result = None
    try:
        with phase_timer("1.5", "Eval Calibration",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            calibrator = EvalCalibrator(store_id=store_id)
            calibration_result = calibrator.run_daily_calibration()

            # 소급 검증: 놓친 날의 미평가 건 일괄 처리
            backfill_result = calibrator.backfill_verification(lookback_days=7)
            if backfill_result["backfilled"] > 0:
                logger.info(f"소급 검증 완료: {backfill_result['backfilled']}건")

            # 일일 리포트 생성
            reporter = EvalReporter(config=calibrator.config, store_id=store_id)
            reporter.generate_daily_report(
                verification_result=calibration_result.get("verification"),
                calibration_result=calibration_result.get("calibration"),
            )
    except Exception as e:
        logger.warning(f"평가 보정 실패 (발주 플로우 계속): {e}")

    ctx["calibration_result"] = calibration_result

    # ★ 폐기 원인 분석 (Phase 1.55 - EvalCalibrator 이후)
    try:
        with phase_timer("1.55", "Waste Cause Analysis",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.analysis.waste_cause_analyzer import WasteCauseAnalyzer
            waste_analyzer = WasteCauseAnalyzer(store_id=store_id)
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
    try:
        with phase_timer("1.56", "Food Waste Rate Calibration",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.prediction.food_waste_calibrator import FoodWasteRateCalibrator
            waste_calibrator = FoodWasteRateCalibrator(store_id=store_id)
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
    if datetime.now().weekday() == 6:  # 일요일만
        try:
            with phase_timer("1.57", "Bayesian Parameter Optimization",
                             store_id=store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                from src.prediction.bayesian_optimizer import (
                    BayesianParameterOptimizer,
                    BAYESIAN_ENABLED,
                )
                if BAYESIAN_ENABLED:
                    bayesian_opt = BayesianParameterOptimizer(store_id=store_id)

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
    if datetime.now().weekday() == 6:  # 일요일만
        try:
            with phase_timer("1.58", "Croston Parameter Optimization",
                             store_id=store_id, _logger=logger,
                             timings=ctx.get("_phase_timings")):
                from src.analysis.croston_optimizer import run_optimization
                croston_result = run_optimization(store_id)
                logger.info(f"[Phase 1.58] Croston 최적화 완료: {croston_result}")

                # C-2: Stacking 메타 학습기 재학습
                from src.analysis.stacking_predictor import train_stacking_model
                stacking_result = train_stacking_model(store_id)
                logger.info(f"[C-2] Stacking 학습: {stacking_result}")
        except Exception as e:
            logger.warning(f"[Phase 1.58] Croston/Stacking 최적화 실패 (발주 플로우 계속): {e}")

    # ★ 예측 실적 소급 (prediction_logs.actual_qty 채우기)
    try:
        with phase_timer("1.6", "Prediction Actual Sales Update",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            tracker = AccuracyTracker(store_id=store_id)
            updated = tracker.update_actual_sales(yesterday_str)
            logger.info(f"예측 실적 소급 완료: {updated}건 ({yesterday_str})")
            # 최근 7일 소급 업데이트 (수집 누락일 보완)
            backfilled = tracker.backfill_actual_sales(lookback_days=7)
            if backfilled > 0:
                logger.info(f"예측 실적 소급 보완: {backfilled}건 (최근 7일)")
    except Exception as e:
        logger.warning(f"예측 실적 소급 실패 (발주 플로우 계속): {e}")

    # ★ 수요 패턴 분류 DB 갱신 (Phase 1.61 - prediction-redesign)
    try:
        with phase_timer("1.61", "Demand Pattern Classification",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.prediction.demand_classifier import DemandClassifier
            from src.infrastructure.database.repos.product_detail_repo import ProductDetailRepository
            from src.infrastructure.database.connection import DBRouter
            pd_repo = ProductDetailRepository()
            active_items = pd_repo.get_all_active_items(days=30, store_id=store_id)
            if active_items:
                classifier = DemandClassifier(store_id=store_id)
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
    try:
        with phase_timer("1.65", "Stale Stock Cleanup",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.infrastructure.database.repos.inventory_repo import RealtimeInventoryRepository
            ri_repo = RealtimeInventoryRepository(store_id=store_id)
            cleanup_result = ri_repo.cleanup_stale_stock(
                stale_hours=RealtimeInventoryRepository.STOCK_FRESHNESS_HOURS,
                store_id=store_id
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
                expired_cleaned = cleanup_expired_batches(store_id)
                if expired_cleaned > 0:
                    logger.info(f"만료 배치 정리: {expired_cleaned}건")
            except Exception as e_batch:
                logger.debug(f"만료 배치 정리 실패 (비치명적): {e_batch}")
    except Exception as e:
        logger.warning(f"유령 재고 정리 실패 (발주 플로우 계속): {e}")

    # ★ 배치 FIFO 재동기화 + 푸드 유령재고 정리 (Phase 1.66)
    # Phase 1.65 TTL 정리 후, active 배치 없는 푸드의 RI stock_qty → 0
    # Phase 2 발주 전에 실행하여 정확한 재고 기반 발주 보장
    try:
        with phase_timer("1.66", "Batch FIFO Sync + Food Ghost Cleanup",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.infrastructure.database.repos import InventoryBatchRepository

            batch_repo = InventoryBatchRepository(store_id=store_id)
            sync_result = batch_repo.sync_remaining_with_stock(
                store_id=store_id
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

    # ★ 자전 시스템 (Phase 1.67) — 감지 → 판단 → 보고 → 실행 → 검증

    # 1.67a: 전날 실행 건 검증 (4번째 고리)
    try:
        from src.application.services.execution_verifier import ExecutionVerifier
        verify_result = ExecutionVerifier(store_id).verify_previous_day()
        if verify_result["verified"] > 0:
            logger.info(
                f"[Phase 1.67a] 전날 실행 검증: "
                f"{verify_result['success']}건 성공, {verify_result['failed']}건 실패"
            )
    except Exception as e:
        logger.warning(f"[Phase 1.67a] 전날 검증 실패 (계속): {e}")

    # 1.67b: 데이터 무결성 검증 (1~2번째 고리: 감지+판단+보고)
    try:
        with phase_timer("1.67", "Data Integrity Verification",
                         store_id=store_id, _logger=logger,
                         timings=ctx.get("_phase_timings")):
            from src.application.services.data_integrity_service import DataIntegrityService

            integrity_svc = DataIntegrityService(store_id=store_id)
            integrity_result = integrity_svc.run_all_checks(store_id)
            anomalies = integrity_result.get("total_anomalies", 0)
            if anomalies > 0:
                logger.warning(
                    f"데이터 무결성 이상 {anomalies}건 감지 "
                    f"({', '.join(r['check_name'] + '=' + str(r['count']) for r in integrity_result['results'] if r['status'] != 'OK')})"
                )
            else:
                logger.info("데이터 무결성 검증 통과 (이상 없음)")
    except Exception as e:
        logger.warning(f"[Phase 1.67b] 무결성 검증 실패 (발주 플로우 계속): {e}")

    # 1.67c: 자동 실행 (3번째 고리)
    try:
        from src.application.services.auto_executor import AutoExecutor
        exec_result = AutoExecutor(store_id).run()
        if exec_result["executed"] + exec_result["approval_requested"] > 0:
            logger.info(
                f"[Phase 1.67c] 자전 실행: "
                f"자동={exec_result['executed']}, 승인요청={exec_result['approval_requested']}"
            )
    except Exception as e:
        logger.warning(f"[Phase 1.67c] 자전 실행 실패 (계속): {e}")

    return ctx
