"""
최적화된 7시 스케줄 플로우 테스트 스크립트

발주저장(BGF 전송)만 제외하고 전체 플로우를 동일하게 실행합니다.
- Phase 1: 데이터 수집 (매출, 시간대별, 검수, 폐기전표, 분석/보정/재고)
- Phase 2: 자동 발주 (dry_run=True — 예측/평가/필터링만, 실제 저장 안 함)

Usage:
    python scripts/test_optimized_flow.py                  # 46513 단일 매장
    python scripts/test_optimized_flow.py --store 46704    # 특정 매장
    python scripts/test_optimized_flow.py --skip-collect    # 수집 스킵, 발주만 테스트
"""

import sys
import os
import io
import time
from datetime import datetime
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    for _sn in ("stdout", "stderr"):
        _s = getattr(sys, _sn, None)
        if _s and hasattr(_s, "buffer") and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            setattr(sys, _sn, io.TextIOWrapper(_s.buffer, encoding="utf-8", errors="replace", line_buffering=True))

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.utils.logger import get_logger

logger = get_logger(__name__)


def run_test_flow(store_id: str = "46513", skip_collect: bool = False):
    """최적화된 플로우 테스트 실행 (발주저장 제외)

    Args:
        store_id: 테스트 대상 매장 ID
        skip_collect: True면 수집 스킵, 발주 dry_run만 실행
    """
    start_time = time.time()

    logger.info("=" * 70)
    logger.info(f"[TEST] 최적화 플로우 테스트 시작 (발주저장 제외)")
    logger.info(f"[TEST] 매장: {store_id}")
    logger.info(f"[TEST] 수집 스킵: {skip_collect}")
    logger.info(f"[TEST] 시작 시각: {datetime.now().strftime('%H:%M:%S')}")
    logger.info("=" * 70)

    from src.scheduler.daily_job import DailyCollectionJob

    job = DailyCollectionJob(store_id=store_id)

    if skip_collect:
        # 수집 스킵 — 발주 dry_run만 테스트
        logger.info("[TEST] Phase 1 수집 스킵 → Phase 2 dry_run만 실행")
        _run_dry_order_only(job, store_id)
    else:
        # 전체 플로우 실행 (run_optimized 내부에서 dry_run 적용)
        _run_full_flow_dry(job, store_id)

    elapsed = time.time() - start_time
    logger.info("=" * 70)
    logger.info(f"[TEST] 완료! 소요시간: {elapsed:.1f}초 ({elapsed/60:.1f}분)")
    logger.info("=" * 70)


def _run_full_flow_dry(job, store_id: str):
    """전체 플로우 실행 — _run_auto_order_with_driver의 dry_run=True 패치

    run_optimized()를 호출하되 내부 _run_auto_order_with_driver를
    dry_run=True로 패치합니다.
    """
    # _run_auto_order_with_driver를 dry_run=True로 패치
    original_method = job._run_auto_order_with_driver

    def _dry_run_auto_order(driver, use_improved_predictor=True,
                            skip_exclusion_fetch=False, target_dates=None):
        """dry_run=True로 자동 발주 실행 (발주저장 안 함)"""
        try:
            from src.application.use_cases.daily_order_flow import DailyOrderFlow
            from src.settings.store_context import StoreContext

            logger.info("[TEST-DRY] AutoOrder: dry_run=True (발주저장 안 함)")

            ctx = StoreContext.from_store_id(store_id)
            flow = DailyOrderFlow(
                store_ctx=ctx,
                driver=driver,
                use_improved_predictor=use_improved_predictor,
            )
            flow_result = flow.run_auto_order(
                dry_run=True,  # ★ 핵심: 발주저장 안 함
                min_order_qty=1,
                max_items=None,
                prefetch_pending=True,
                collect_fail_reasons=False,
                skip_exclusion_fetch=skip_exclusion_fetch,
                target_dates=target_dates,
            )

            # 결과 요약
            order_result = flow_result.get("execution", {})
            if not order_result:
                order_result = {
                    "success": flow_result.get("success", False),
                    "dry_run": True,
                }

            # 예측 결과 로깅
            predictions = flow_result.get("predictions", [])
            logger.info(f"[TEST-DRY] 예측 완료: {len(predictions)}건")

            # 필터링 결과
            filtered = flow_result.get("filtered", [])
            logger.info(f"[TEST-DRY] 필터링 후 발주 대상: {len(filtered)}건")

            # 카테고리별 요약
            cat_summary = {}
            for item in (filtered or predictions):
                mid = item.get("mid_cd", "???")
                cat_summary[mid] = cat_summary.get(mid, 0) + item.get("qty", item.get("order_qty", 0))
            if cat_summary:
                logger.info(f"[TEST-DRY] 카테고리별 수량:")
                for mid, qty in sorted(cat_summary.items()):
                    logger.info(f"  {mid}: {qty}개")

            logger.info(f"[TEST-DRY] AutoOrder 완료 (dry_run — 실제 전송 없음)")
            return order_result

        except Exception as e:
            logger.error(f"[TEST-DRY] AutoOrder 오류: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e), "dry_run": True}

    # 패치 적용
    job._run_auto_order_with_driver = _dry_run_auto_order

    # run_optimized 실행 (수집 + dry_run 발주)
    result = job.run_optimized(
        run_auto_order=True,
        use_improved_predictor=True,
    )

    # 패치 복원
    job._run_auto_order_with_driver = original_method

    # 결과 출력
    logger.info("[TEST] ── 전체 플로우 결과 ──")
    logger.info(f"  성공: {result.get('success')}")
    logger.info(f"  수집 품목: {result.get('total_items', 0)}")
    logger.info(f"  발주(dry): {result.get('order', {})}")

    # Phase별 타이밍 출력
    timings = result.get("phase_timings", {})
    if timings:
        logger.info("[TEST] ── Phase별 소요시간 ──")
        for phase, elapsed in sorted(timings.items()):
            logger.info(f"  {phase}: {elapsed:.1f}초")

    return result


def _run_dry_order_only(job, store_id: str):
    """수집 스킵 — 발주 dry_run만 실행"""
    from src.sales_analyzer import SalesAnalyzer

    analyzer = SalesAnalyzer()
    driver = None

    try:
        logger.info("[TEST] BGF 로그인 시작...")
        if not analyzer.login():
            logger.error("[TEST] 로그인 실패")
            return

        driver = analyzer.driver
        logger.info("[TEST] 로그인 성공")

        from src.application.use_cases.daily_order_flow import DailyOrderFlow
        from src.settings.store_context import StoreContext

        ctx = StoreContext.from_store_id(store_id)
        flow = DailyOrderFlow(
            store_ctx=ctx,
            driver=driver,
            use_improved_predictor=True,
        )

        logger.info("[TEST] DailyOrderFlow.run_auto_order(dry_run=True) 시작...")
        flow_result = flow.run_auto_order(
            dry_run=True,
            min_order_qty=1,
            max_items=None,
            prefetch_pending=True,
        )

        logger.info("[TEST] ── Dry Run 결과 ──")
        logger.info(f"  성공: {flow_result.get('success')}")
        predictions = flow_result.get("predictions", [])
        logger.info(f"  예측 건수: {len(predictions)}")
        filtered = flow_result.get("filtered", [])
        logger.info(f"  필터링 후: {len(filtered)}")

    except Exception as e:
        logger.error(f"[TEST] 오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="최적화 플로우 테스트 (발주저장 제외)")
    parser.add_argument("--store", default="46513", help="매장 ID (기본: 46513)")
    parser.add_argument("--skip-collect", action="store_true", help="수집 스킵, 발주만 테스트")
    args = parser.parse_args()

    run_test_flow(store_id=args.store, skip_collect=args.skip_collect)
