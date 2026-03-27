"""
데이터 무결성 검증 서비스 (DataIntegrityService)

매일 Phase 1.67에서 실행. 6개 검증 항목을 전 매장 대상으로 실행하고
이상치 발견 시 카카오 알림을 발송한다.

검증 항목:
1. expired_batch_remaining  — expired 배치 remaining_qty > 0
2. food_ghost_stock         — 활성 배치 없는 푸드 RI stock > 0
3. expiry_time_mismatch     — OT/IB 유통기한 1일 초과 불일치
4. missing_delivery_type    — 활성 OT delivery_type NULL
5. past_expiry_active       — 만료 시간 경과인데 arrived 유지
6. unavailable_with_sales   — is_available=0인데 최근 3일 판매 있음
"""

from datetime import datetime
from typing import Dict, List, Any, Optional

from src.infrastructure.database.repos.integrity_check_repo import IntegrityCheckRepository
from src.settings.store_context import StoreContext
from src.utils.logger import get_logger, get_session_id

logger = get_logger(__name__)

CHECK_NAMES = [
    "expired_batch_remaining",
    "food_ghost_stock",
    "expiry_time_mismatch",
    "missing_delivery_type",
    "past_expiry_active",
    "unavailable_with_sales",
]


class DataIntegrityService:
    """데이터 무결성 검증 서비스"""

    def __init__(self, store_id: Optional[str] = None) -> None:
        self.store_id = store_id

    def run_all_checks(self, store_id: str) -> Dict[str, Any]:
        """단일 매장의 5개 검증 항목 실행

        Args:
            store_id: 매장 코드

        Returns:
            {
                "store_id": str,
                "check_date": str,
                "session_id": str,
                "total_anomalies": int,
                "results": [{check_name, status, count, details}, ...]
            }
        """
        check_date = datetime.now().strftime("%Y-%m-%d")
        session_id = get_session_id() or "--------"

        repo = IntegrityCheckRepository(store_id=store_id)
        repo.ensure_table()

        results = []
        total_anomalies = 0

        for check_name in CHECK_NAMES:
            try:
                result = self._run_single_check(repo, store_id, check_name)
                results.append(result)

                repo.save_check_result(
                    store_id=store_id,
                    session_id=session_id,
                    check_date=check_date,
                    check_name=check_name,
                    status=result["status"],
                    anomaly_count=result["count"],
                    details=result.get("details", [])[:20],
                )

                if result["status"] in ("WARN", "FAIL"):
                    total_anomalies += result["count"]
                    logger.warning(
                        f"[Integrity] {store_id}/{check_name}: "
                        f"{result['status']} ({result['count']}건)"
                    )
                else:
                    logger.info(f"[Integrity] {store_id}/{check_name}: OK")

            except Exception as e:
                logger.warning(
                    f"[Integrity] {store_id}/{check_name} 실행 실패: {e}"
                )
                results.append({
                    "check_name": check_name,
                    "status": "ERROR",
                    "count": 0,
                    "details": [{"error": str(e)}],
                })

        # 이상 발견 시 카카오 알림
        if total_anomalies > 0:
            self._send_alert(store_id, results)

        return {
            "store_id": store_id,
            "check_date": check_date,
            "session_id": session_id,
            "total_anomalies": total_anomalies,
            "results": results,
        }

    def run_all_stores(self) -> Dict[str, Any]:
        """전체 활성 매장 순회 검증

        Returns:
            {
                "total_stores": int,
                "stores_with_anomalies": int,
                "store_results": {store_id: {...}, ...},
            }
        """
        active_stores = StoreContext.get_all_active()
        store_results = {}
        stores_with_anomalies = 0

        for ctx in active_stores:
            try:
                result = self.run_all_checks(ctx.store_id)
                store_results[ctx.store_id] = result
                if result["total_anomalies"] > 0:
                    stores_with_anomalies += 1
            except Exception as e:
                logger.warning(f"[Integrity] 매장 {ctx.store_id} 검증 실패: {e}")
                store_results[ctx.store_id] = {
                    "store_id": ctx.store_id,
                    "error": str(e),
                }

        logger.info(
            f"[Integrity] 전체 검증 완료: "
            f"{len(active_stores)}개 매장, "
            f"이상 {stores_with_anomalies}개 매장"
        )

        return {
            "total_stores": len(active_stores),
            "stores_with_anomalies": stores_with_anomalies,
            "store_results": store_results,
        }

    def _run_single_check(
        self, repo: IntegrityCheckRepository, store_id: str, check_name: str
    ) -> Dict[str, Any]:
        """개별 검증 항목 실행 (디스패치)"""
        dispatch = {
            "expired_batch_remaining": repo.check_expired_batch_remaining,
            "food_ghost_stock": repo.check_food_ghost_stock,
            "expiry_time_mismatch": repo.check_expiry_time_mismatch,
            "missing_delivery_type": repo.check_missing_delivery_type,
            "past_expiry_active": repo.check_past_expiry_active,
            "unavailable_with_sales": repo.check_unavailable_with_sales,
        }
        check_fn = dispatch.get(check_name)
        if not check_fn:
            raise ValueError(f"Unknown check: {check_name}")
        return check_fn(store_id)

    def _send_alert(
        self, store_id: str, results: List[Dict[str, Any]]
    ) -> bool:
        """카카오 알림 발송 (이상치 있을 때만)"""
        try:
            from src.notification.kakao_notifier import (
                KakaoNotifier, DEFAULT_REST_API_KEY,
            )
            notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
        except Exception as e:
            logger.warning(f"[Integrity] KakaoNotifier 초기화 실패: {e}")
            return False

        lines = ["[데이터 무결성 검증]", ""]
        lines.append(f"매장 {store_id}:")
        for check in results:
            if check["status"] in ("WARN", "FAIL"):
                marker = "!!" if check["status"] == "FAIL" else "! "
                lines.append(
                    f"  {marker} {check['check_name']}: "
                    f"{check['count']}건"
                )

        lines.append("")
        lines.append(f"검증: {datetime.now().strftime('%H:%M')}")

        text = "\n".join(lines)

        try:
            return notifier.send_message(text)
        except Exception as e:
            logger.warning(f"[Integrity] 카카오 알림 발송 실패: {e}")
            return False

    def get_latest_results(
        self, store_id: str, days: int = 7
    ) -> List[Dict[str, Any]]:
        """최근 검증 결과 조회 (대시보드용)"""
        repo = IntegrityCheckRepository(store_id=store_id)
        return repo.get_latest_results(store_id, days=days)
