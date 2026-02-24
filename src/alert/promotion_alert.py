"""
행사 변경 알림 모듈

행사 종료/시작 임박 시 카카오톡 알림
"""

from typing import Any, Dict, List, Optional
from datetime import date, datetime

from src.prediction.promotion.promotion_manager import PromotionManager
from src.prediction.promotion.promotion_adjuster import PromotionAdjuster
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PromotionAlert:
    """
    행사 변경 알림

    사용법:
        alert = PromotionAlert()
        alert.send_daily_alert()  # 매일 스케줄러에서 호출
    """

    def __init__(self, kakao_notifier: Optional[Any] = None, store_id: Optional[str] = None) -> None:
        self.store_id = store_id
        self.promo_manager = PromotionManager(store_id=store_id)
        self.promo_adjuster = PromotionAdjuster(self.promo_manager)
        self.kakao = kakao_notifier

    def send_daily_alert(self, send_kakao: bool = True) -> str:
        """
        일일 행사 변경 알림

        내용:
        1. 오늘~3일 내 종료 예정 행사
        2. 오늘~3일 내 시작 예정 행사

        Args:
            send_kakao: True면 카카오톡 발송

        Returns:
            생성된 메시지
        """
        # 종료 임박 행사
        ending = self.promo_manager.get_ending_promotions(days=3)

        # 시작 임박 행사
        starting = self.promo_manager.get_starting_promotions(days=3)

        if not ending and not starting:
            logger.info("행사 변경 예정 없음")
            return ""

        # 메시지 생성
        message = self._format_alert_message(ending, starting)

        # 카카오 알림 발송
        if send_kakao and self.kakao:
            try:
                self.kakao.send_message(message)
                logger.info(f"행사 알림 발송 완료: 종료 {len(ending)}건, 시작 {len(starting)}건")
            except Exception as e:
                logger.error(f"카카오 알림 발송 실패: {e}")

        return message

    def _format_alert_message(
        self,
        ending: List[Any],
        starting: List[Any]
    ) -> str:
        """알림 메시지 포맷"""
        today = date.today().strftime('%m/%d')

        lines = [
            f"[행사 변경 알림] ({today})",
            ""
        ]

        # 종료 임박 (다음 행사 없는 것만 경고)
        ending_no_next = [s for s in ending if not s.next_promo]
        if ending_no_next:
            lines.append("[종료 임박 - 발주 주의]")
            lines.append("-" * 25)

            for status in ending_no_next[:10]:  # 최대 10개
                days = status.days_until_end
                day_text = "오늘" if days == 0 else f"D-{days}"
                lines.append(f"* {status.item_nm[:15]}")
                lines.append(f"  {status.current_promo} 종료 {day_text}")
                lines.append(f"  -> 재고 소진 유도 필요")

            lines.append("")

        # 시작 임박
        if starting:
            lines.append("[시작 예정 - 발주 증가]")
            lines.append("-" * 25)

            for status in starting[:10]:
                days = self._days_until(status.next_start_date)
                day_text = "오늘" if days == 0 else f"D-{days}"
                lines.append(f"* {status.item_nm[:15]}")
                lines.append(f"  {status.next_promo} 시작 {day_text}")
                # 배율 정보 추가
                multiplier = self.promo_manager.get_promo_multiplier(
                    status.item_cd, status.next_promo
                )
                lines.append(f"  -> 평시 대비 {multiplier:.1f}배 예상")

            lines.append("")

        # 요약
        total = len(ending_no_next) + len(starting)
        lines.append(f"총 {total}개 상품 확인 필요")

        return "\n".join(lines)

    def _days_until(self, date_str: str) -> int:
        """날짜까지 남은 일수"""
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
            return (target - date.today()).days
        except Exception:
            return 0

    def check_critical_items(self) -> List[Dict[str, Any]]:
        """
        긴급 주의 상품 조회

        조건:
        - 행사 종료 D-1 또는 D-day
        - 다음 행사 없음

        Returns:
            긴급 상품 리스트
        """
        return self.promo_adjuster.get_high_risk_items()

    def send_critical_alert(self, send_kakao: bool = True) -> str:
        """
        긴급 알림 발송 (종료 D-1 이내)

        Args:
            send_kakao: True면 카카오톡 발송

        Returns:
            생성된 메시지
        """
        critical = self.check_critical_items()

        if not critical:
            logger.info("긴급 알림 대상 없음")
            return ""

        today = date.today().strftime('%m/%d')
        lines = [
            f"[긴급] 행사 종료 임박 ({today})",
            "",
            "아래 상품 재고 확인 필요!",
            "-" * 25,
        ]

        for item in critical:
            days = item['days_remaining']
            day_text = "오늘 종료" if days == 0 else f"내일 종료"

            lines.append(f"* {item['item_nm'][:15]}")
            lines.append(f"  {item['promo']} {day_text}")
            lines.append(f"  행사: {item['promo_multiplier']:.1f}배 -> 평시: {item['normal_avg']:.1f}개/일")

        lines.append("")
        lines.append(f"총 {len(critical)}개 상품")

        message = "\n".join(lines)

        if send_kakao and self.kakao:
            try:
                self.kakao.send_message(message)
                logger.info(f"긴급 알림 발송 완료: {len(critical)}건")
            except Exception as e:
                logger.error(f"긴급 알림 발송 실패: {e}")

        return message

    def get_promotion_summary(self) -> Dict[str, int]:
        """
        현재 행사 상황 요약

        Returns:
            {
                'active_count': 현재 진행 중인 행사 수,
                'ending_count': 3일 내 종료 예정 수,
                'starting_count': 3일 내 시작 예정 수,
                'high_risk_count': 고위험 상품 수,
            }
        """
        active = self.promo_manager.get_all_active_promotions()
        ending = self.promo_manager.get_ending_promotions(days=3)
        starting = self.promo_manager.get_starting_promotions(days=3)
        high_risk = self.check_critical_items()

        return {
            'active_count': len(active),
            'ending_count': len(ending),
            'starting_count': len(starting),
            'high_risk_count': len(high_risk),
        }


# =============================================================================
# 테스트
# =============================================================================
if __name__ == "__main__":
    alert = PromotionAlert()

    # 요약 출력
    summary = alert.get_promotion_summary()
    print("\n[행사 현황 요약]")
    print(f"  진행 중: {summary['active_count']}개")
    print(f"  종료 예정 (3일): {summary['ending_count']}개")
    print(f"  시작 예정 (3일): {summary['starting_count']}개")
    print(f"  고위험: {summary['high_risk_count']}개")

    # 일일 알림 메시지 생성 (발송 안함)
    message = alert.send_daily_alert(send_kakao=False)
    if message:
        print("\n[일일 알림 메시지]")
        print(message)

    # 긴급 알림
    critical_msg = alert.send_critical_alert(send_kakao=False)
    if critical_msg:
        print("\n[긴급 알림 메시지]")
        print(critical_msg)
