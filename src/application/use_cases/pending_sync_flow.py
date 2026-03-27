"""
PendingSyncFlow -- 발주 확정 pending 동기화 (독립 스케줄)

10:30 스케줄로 실행. BGF 로그인 → 발주현황 조회 → pending 마킹 → 종료.
07:00 daily_order 플로우와 완전히 독립적인 세션.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class PendingSyncFlow:
    """발주 확정 pending 동기화 플로우

    Usage:
        flow = PendingSyncFlow(store_ctx=ctx)
        result = flow.run()

    또는 직접 실행:
        python -c "from src.application.use_cases.pending_sync_flow import run_pending_sync; run_pending_sync()"
    """

    def __init__(self, store_ctx=None, driver=None):
        self.store_ctx = store_ctx
        self._external_driver = driver

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def run(self, **kwargs) -> Dict[str, Any]:
        """BGF 로그인 → 발주현황 pending sync → 만료 클리어 → 종료

        Returns:
            {success: bool, synced: int, cleared_ot: int, ...}
        """
        today = datetime.now().strftime("%Y-%m-%d")
        store_id = self.store_id
        result: Dict[str, Any] = {"success": False, "store_id": store_id}

        if not store_id:
            logger.error("[PendingSyncFlow] store_id 없음")
            result["error"] = "no_store_id"
            return result

        logger.info(f"[PendingSyncFlow] 시작: store={store_id}, date={today}")

        from src.collectors.order_status_collector import OrderStatusCollector

        # 1. 만료된 pending 클리어 (Selenium 불필요)
        try:
            os_collector = OrderStatusCollector(driver=None, store_id=store_id)
            clear_result = os_collector.clear_expired_pending(today)
            result["cleared_ot"] = clear_result.get("cleared_ot", 0)
            result["cleared_ri"] = clear_result.get("cleared_ri", 0)
            if result["cleared_ot"] > 0:
                logger.info(
                    f"[PendingSyncFlow] 만료 클리어: "
                    f"OT={result['cleared_ot']}, RI={result['cleared_ri']}"
                )
        except Exception as e:
            logger.warning(f"[PendingSyncFlow] 만료 클리어 실패 (계속): {e}")

        # 2. BGF 로그인 (독립 세션)
        driver = self._external_driver
        own_driver = False
        analyzer = None

        if not driver:
            try:
                from src.sales_analyzer import SalesAnalyzer
                analyzer = SalesAnalyzer(store_id=store_id)
                analyzer.setup_driver()
                analyzer.connect()
                if not analyzer.do_login():
                    raise Exception("do_login() returned False")
                analyzer.close_popup()
                driver = analyzer.driver
                own_driver = True
                if not driver:
                    logger.error("[PendingSyncFlow] BGF 로그인 실패")
                    result["error"] = "login_failed"
                    return result
            except Exception as e:
                logger.error(f"[PendingSyncFlow] BGF 로그인 예외: {e}")
                result["error"] = f"login_error: {e}"
                return result

        try:
            # 3. 발주현황 메뉴 이동 + pending sync
            os_collector = OrderStatusCollector(driver=driver, store_id=store_id)

            if not os_collector.navigate_to_order_status_menu():
                logger.warning("[PendingSyncFlow] 발주현황 메뉴 이동 실패")
                result["error"] = "menu_nav_failed"
                return result

            sync_result = os_collector.post_order_pending_sync(today)
            result.update(sync_result)
            result["success"] = True

            logger.info(
                f"[PendingSyncFlow] 완료: "
                f"synced={sync_result.get('synced', 0)}, "
                f"skipped={sync_result.get('skipped', 0)}, "
                f"mismatch={sync_result.get('date_mismatch', False)}"
            )

            # 탭 닫기
            os_collector.close_menu()

        except Exception as e:
            logger.error(f"[PendingSyncFlow] 실행 오류: {e}")
            result["error"] = str(e)
        finally:
            # 4. 자체 로그인한 경우만 드라이버 종료
            if own_driver:
                try:
                    if analyzer:
                        analyzer.close()
                    elif driver:
                        driver.quit()
                except Exception:
                    pass

        return result


def run_pending_sync(store_ids: Optional[list] = None, parallel: bool = True) -> Dict[str, Any]:
    """다매장 pending sync 실행 (스크립트/스케줄러용)

    Args:
        store_ids: 매장 ID 리스트. None이면 stores.json에서 로드.
        parallel: True면 ThreadPoolExecutor 병렬 실행

    Returns:
        {store_id: result, ...}
    """
    if not store_ids:
        try:
            from src.application.services.store_service import StoreService
            store_ids = StoreService.get_active_store_ids()
        except Exception:
            store_ids = ["46513", "46704", "47863"]

    def _run_one(sid: str) -> tuple:
        try:
            from src.settings.store_context import StoreContext
            ctx = StoreContext(store_id=sid, store_name=f"Store {sid}")
            flow = PendingSyncFlow(store_ctx=ctx)
            return sid, flow.run()
        except Exception as e:
            logger.error(f"[PendingSyncFlow] store={sid} 실패: {e}")
            return sid, {"success": False, "error": str(e)}

    all_results = {}

    if parallel and len(store_ids) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        logger.info(f"[PendingSyncFlow] 병렬 실행: {len(store_ids)}매장")
        with ThreadPoolExecutor(max_workers=len(store_ids)) as executor:
            futures = {executor.submit(_run_one, sid): sid for sid in store_ids}
            for future in as_completed(futures):
                sid, result = future.result()
                all_results[sid] = result
    else:
        for sid in store_ids:
            sid, result = _run_one(sid)
            all_results[sid] = result

    return all_results


if __name__ == "__main__":
    results = run_pending_sync()
    for sid, r in results.items():
        print(f"{sid}: {r}")
