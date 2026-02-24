"""
행사 기반 발주량 조정 모듈

행사 시작/종료 예정에 따라 발주량 자동 조정
"""

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import date, datetime

from .promotion_manager import PromotionManager, PromotionStatus


@dataclass
class AdjustmentResult:
    """발주 조정 결과"""
    item_cd: str
    original_qty: int                   # 원래 발주량
    adjusted_qty: int                   # 조정된 발주량
    adjustment_reason: str              # 조정 사유
    adjustment_factor: float            # 조정 계수
    promo_status: Optional[PromotionStatus]  # 행사 상태


class PromotionAdjuster:
    """
    행사 기반 발주 조정기

    사용법:
        adjuster = PromotionAdjuster()

        # 발주량 조정
        result = adjuster.adjust_order_quantity(
            item_cd="8801234567890",
            base_qty=15,
            current_stock=5
        )

        print(f"조정 전: {result.original_qty}")
        print(f"조정 후: {result.adjusted_qty}")
        print(f"사유: {result.adjustment_reason}")
    """

    # 행사 종료 시 발주 감소율
    END_ADJUSTMENT = {
        3: 0.50,    # D-3: 50%로 감소
        2: 0.30,    # D-2: 30%로 감소
        1: 0.10,    # D-1: 10%로 감소 (거의 중단)
        0: 0.00,    # D-day: 발주 중단
    }

    # 행사 시작 시 발주 증가율
    START_ADJUSTMENT = {
        3: 1.20,    # D-3: 20% 증가
        2: 1.50,    # D-2: 50% 증가
        1: 2.00,    # D-1: 2배
        0: 1.00,    # D-day: 행사 배율 적용 (별도 처리)
    }

    def __init__(self, promo_manager: Optional[PromotionManager] = None) -> None:
        self.promo_manager = promo_manager or PromotionManager()

    def adjust_order_quantity(
        self,
        item_cd: str,
        base_qty: int,
        current_stock: int = 0,
        pending_qty: int = 0
    ) -> AdjustmentResult:
        """
        행사 상태에 따른 발주량 조정

        Args:
            item_cd: 상품코드
            base_qty: 기본 예측 발주량 (행사 미고려)
            current_stock: 현재 재고
            pending_qty: 미입고 수량

        Returns:
            AdjustmentResult
        """
        # 행사 상태 조회
        status = self.promo_manager.get_promotion_status(item_cd)

        if not status:
            return AdjustmentResult(
                item_cd=item_cd,
                original_qty=base_qty,
                adjusted_qty=base_qty,
                adjustment_reason="행사 정보 없음",
                adjustment_factor=1.0,
                promo_status=None
            )

        # 기본값
        adjusted_qty = base_qty
        reason = "조정 없음"
        factor = 1.0

        # === 케이스 1: 행사 종료 임박 ===
        if status.current_promo and status.days_until_end is not None:
            if status.days_until_end <= 3:
                # 다음 행사 없으면 발주 감소
                if not status.next_promo:
                    factor = self.END_ADJUSTMENT.get(status.days_until_end, 0.5)
                    adjusted_qty = self._calculate_end_adjustment(
                        base_qty=base_qty,
                        current_stock=current_stock,
                        pending_qty=pending_qty,
                        days_remaining=status.days_until_end,
                        normal_avg=status.normal_avg,
                        promo_avg=status.promo_avg
                    )
                    reason = f"행사 종료 D-{status.days_until_end}, 발주 {int(factor*100)}%"

                # 다음 행사 있고 같은 유형이면 유지
                elif status.next_promo == status.current_promo:
                    reason = f"동일 행사 연속 ({status.current_promo})"

                # 다음 행사 있지만 다른 유형이면 약간 감소
                else:
                    factor = 0.8
                    adjusted_qty = int(base_qty * factor)
                    reason = f"행사 변경 예정 ({status.current_promo}→{status.next_promo})"

        # === 케이스 2: 행사 시작 임박 ===
        elif status.next_promo and status.next_start_date:
            days_until_start = self._days_until(status.next_start_date)

            if days_until_start is not None and 0 <= days_until_start <= 3:
                factor = self.START_ADJUSTMENT.get(days_until_start, 1.0)

                if days_until_start == 0:
                    # D-day: 행사 배율 적용
                    factor = status.promo_multiplier

                adjusted_qty = int(base_qty * factor)
                reason = f"행사 시작 D-{days_until_start}, 발주 {int(factor*100)}%"

        # === 케이스 3: 현재 행사 중 (변경 없음) ===
        elif status.current_promo:
            # 행사 배율 적용
            factor = status.promo_multiplier
            adjusted_qty = int(base_qty * factor)
            reason = f"행사 중 ({status.current_promo}), 배율 {factor:.1f}x"

        # 최소 0 보장
        adjusted_qty = max(0, adjusted_qty)

        return AdjustmentResult(
            item_cd=item_cd,
            original_qty=base_qty,
            adjusted_qty=adjusted_qty,
            adjustment_reason=reason,
            adjustment_factor=factor,
            promo_status=status
        )

    def _calculate_end_adjustment(
        self,
        base_qty: int,
        current_stock: int,
        pending_qty: int,
        days_remaining: int,
        normal_avg: float,
        promo_avg: float
    ) -> int:
        """
        행사 종료 시 발주량 계산

        목표: 행사 종료 시점에 재고가 평시 2~3일치만 남도록

        계산:
        1. 남은 행사 기간 예상 판매량
        2. 행사 후 필요 재고 (평시 2일치)
        3. 필요 발주량 = 예상판매 + 목표재고 - 현재고 - 미입고
        """
        # 남은 기간 예상 판매량 (행사 판매량 기준)
        expected_sales = promo_avg * days_remaining if promo_avg else base_qty * days_remaining

        # 행사 후 목표 재고 (평시 2일치)
        target_stock_after = normal_avg * 2 if normal_avg else base_qty * 0.3 * 2

        # 필요 발주량
        needed = expected_sales + target_stock_after - current_stock - pending_qty

        return max(0, int(needed))

    def _days_until(self, date_str: str) -> Optional[int]:
        """특정 날짜까지 남은 일수

        Args:
            date_str: 대상 날짜 문자열 (YYYY-MM-DD)

        Returns:
            남은 일수 또는 None (파싱 실패)
        """
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
            return (target - date.today()).days
        except Exception:
            return None

    def get_adjustment_summary(
        self,
        items: List[str]
    ) -> Dict[str, List[Any]]:
        """
        여러 상품의 조정 요약

        Args:
            items: 상품코드 리스트

        Returns:
            {
                'ending_soon': [...],    # 종료 임박 (감소 필요)
                'starting_soon': [...],  # 시작 임박 (증가 필요)
                'in_promotion': [...],   # 행사 중
                'no_change': [...]       # 변경 없음
            }
        """
        result = {
            'ending_soon': [],
            'starting_soon': [],
            'in_promotion': [],
            'no_change': []
        }

        for item_cd in items:
            status = self.promo_manager.get_promotion_status(item_cd)

            if not status:
                result['no_change'].append(item_cd)
                continue

            # 종료 임박 (다음 행사 없는 경우만)
            if status.current_promo and status.days_until_end is not None:
                if status.days_until_end <= 3 and not status.next_promo:
                    result['ending_soon'].append({
                        'item_cd': item_cd,
                        'item_nm': status.item_nm,
                        'promo': status.current_promo,
                        'days_remaining': status.days_until_end,
                        'normal_avg': status.normal_avg,
                        'promo_multiplier': status.promo_multiplier,
                    })
                    continue

            # 시작 임박
            if status.next_promo and status.next_start_date:
                days = self._days_until(status.next_start_date)
                if days is not None and 0 <= days <= 3:
                    result['starting_soon'].append({
                        'item_cd': item_cd,
                        'item_nm': status.item_nm,
                        'promo': status.next_promo,
                        'days_until': days,
                        'promo_multiplier': self.promo_manager.get_promo_multiplier(item_cd, status.next_promo),
                    })
                    continue

            # 현재 행사 중
            if status.current_promo:
                result['in_promotion'].append({
                    'item_cd': item_cd,
                    'item_nm': status.item_nm,
                    'promo': status.current_promo,
                    'days_remaining': status.days_until_end,
                    'promo_multiplier': status.promo_multiplier,
                })
                continue

            result['no_change'].append(item_cd)

        return result

    def get_high_risk_items(self) -> List[Dict[str, Any]]:
        """
        고위험 상품 조회

        조건:
        - 행사 종료 D-1 또는 D-day
        - 다음 행사 없음

        Returns:
            고위험 상품 리스트
        """
        ending = self.promo_manager.get_ending_promotions(days=1)

        high_risk = []
        for status in ending:
            if not status.next_promo:
                high_risk.append({
                    'item_cd': status.item_cd,
                    'item_nm': status.item_nm,
                    'promo': status.current_promo,
                    'days_remaining': status.days_until_end,
                    'promo_multiplier': status.promo_multiplier,
                    'normal_avg': status.normal_avg,
                })

        return high_risk


# =============================================================================
# 테스트
# =============================================================================
if __name__ == "__main__":
    adjuster = PromotionAdjuster()

    # 고위험 상품 조회
    print("\n[고위험 상품 (행사 종료 D-1 이내, 다음 행사 없음)]")
    high_risk = adjuster.get_high_risk_items()
    for item in high_risk:
        print(f"  {item['item_nm'][:15]}: {item['promo']} D-{item['days_remaining']}")
        print(f"    배율 {item['promo_multiplier']:.1f}x → 평시 {item['normal_avg']:.1f}개/일")
