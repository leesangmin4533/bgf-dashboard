"""
ExpiryAlertFlow -- 유통기한 알림 플로우

유통기한 임박 상품을 확인하고 알림을 발송합니다.
기존 run_scheduler.py의 expiry_alert_wrapper를 통합합니다.
"""

from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExpiryAlertFlow:
    """유통기한 알림 플로우

    Usage:
        flow = ExpiryAlertFlow(store_ctx=ctx)
        result = flow.run(days=3)
    """

    def __init__(self, store_ctx=None):
        self.store_ctx = store_ctx

    @property
    def store_id(self) -> Optional[str]:
        return self.store_ctx.store_id if self.store_ctx else None

    def run(self, days: int = 3) -> Dict[str, Any]:
        """유통기한 알림 실행

        Args:
            days: 임박 기준 일수

        Returns:
            {expired: [...], expiring_soon: [...], notified: int}
        """
        try:
            from src.alert.expiry_checker import ExpiryChecker

            checker = ExpiryChecker(store_id=self.store_id)
            try:
                # 배치 만료 처리
                expired = checker.check_and_expire_batches()

                # 임박 상품 조회
                expiring_soon = checker.get_batch_expiring_items(days_ahead=days)

                # 알림 발송 시도
                notified = 0
                alert_items = checker.get_alert_items()
                if alert_items:
                    sent = checker.send_kakao_alert()
                    if sent:
                        notified = 1

                result = {
                    "expired": expired or [],
                    "expiring_soon": expiring_soon or [],
                    "notified": notified,
                }

                logger.info(
                    f"ExpiryAlert 완료: store={self.store_id},"
                    f" expired={len(result['expired'])},"
                    f" expiring_soon={len(result['expiring_soon'])}"
                )
                return result
            finally:
                checker.close()

        except Exception as e:
            logger.error(f"ExpiryAlert 실패: {e}", exc_info=True)
            return {"error": str(e), "expired": [], "expiring_soon": []}
