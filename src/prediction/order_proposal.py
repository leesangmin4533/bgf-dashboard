"""
OrderProposal: 발주 파이프라인 이력 추적 객체

리팩토링 1단계 — 기존 동작 변경 없이 order_qty 재할당 이력만 기록.
각 보정 단계의 전후값, 단계명, 사유를 보존하여 진단/디버깅에 활용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RuleResult:
    """_apply_order_rules() 반환 구조

    로직 변경 없이 반환값에 delta/reason/stage를 포함하여
    호출부에서 proposal 기록에 활용.

    Attributes:
        qty: 최종 발주 수량
        delta: 변화량 (qty - need_qty)
        reason: 변경 사유
        stage: 적용된 규칙명
            - rules_overstock    : 과재고 차단
            - rules_threshold    : 임계값 미만
            - rules_food_minimum : 푸드 최소 발주
            - rules_friday_boost : 금요일 부스트
            - rules_expiry_reduction : 폐기 방지
            - rules_pass         : 변경 없음 (float→int 변환만)
    """
    qty: int
    delta: float
    reason: str
    stage: str


@dataclass
class PromoResult:
    """_apply_promotion_adjustment() 반환 구조

    로직 변경 없이 반환값에 delta/reason/stage/skipped를 포함하여
    호출부에서 proposal 기록에 활용.

    Attributes:
        qty: 최종 발주 수량
        delta: 변화량 (qty - before_qty)
        reason: 변경 사유
        stage: 적용된 분기명
            - promo_A_ending   : 행사 종료 임박 (D-3)
            - promo_B_starting : 행사 시작 임박
            - promo_C_active   : 행사 안정기 보정
            - promo_C_skip     : 행사 안정기 보정 스킵 (Fix B)
            - promo_D_normal   : 비행사 보정
            - promo_pass       : 보정 없음
        skipped: Fix B 보정 스킵 여부
    """
    qty: int
    delta: float
    reason: str
    stage: str
    skipped: bool


@dataclass
class RoundResult:
    """_round_to_order_unit() 반환 구조

    로직 변경 없이 반환값에 delta/reason/stage를 포함하여
    호출부에서 proposal 기록에 활용.

    Attributes:
        qty: 최종 수량 (반올림 후)
        delta: 변화량 (qty - before_qty)
        reason: 변경 이유
        stage: 적용된 분기명
            - round_surplus_zero  : surplus 충분 → 0 반환 (stock_gate_surplus)
            - round_ceil_needs    : 결품 위험 → ceil 선택
            - round_floor_default : 기본 floor 선택
            - round_pass          : order_unit=1 또는 변경 없음
        floor_qty: floor 후보
        ceil_qty: ceil 후보
        selected: "floor" or "ceil" or "zero"
    """
    qty: int
    delta: float
    reason: str
    stage: str
    floor_qty: int
    ceil_qty: int
    selected: str


@dataclass
class Adjustment:
    """단일 보정 이력"""
    stage: str          # 단계명 (e.g. "order_rules", "promo", "ml_ensemble")
    before: int         # 보정 전 값
    after: int          # 보정 후 값
    reason: str = ""    # 변경 사유

    @property
    def delta(self) -> int:
        return self.after - self.before

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass
class OrderProposal:
    """발주 제안 + 이력 추적 객체

    기존 order_qty 변수의 재할당을 감싸서,
    각 단계별 전후값을 adjustments 리스트에 자동 기록.

    Usage:
        proposal = OrderProposal(need_qty=12.5)
        proposal.set(8, "order_rules", "float→int 변환")
        proposal.set(6, "promo", "행사 종료 D-2 감량")
        proposal.set(6, "ml_ensemble", "ML 블렌딩 (변경 없음)")
        # proposal.qty == 6
        # proposal.adjustments == [Adj(rules,12→8), Adj(promo,8→6), Adj(ml,6→6)]

    # stock_gate stages (재고 차단 가시화 — 로직 변경 없음)
    # stock_gate_entry    : 입구 차단 (pending/stock 충분, need_qty=0)
    # stock_gate_overstock: 중간 차단 (stock_days >= threshold)
    # stock_gate_surplus  : 출구 차단 (surplus 충분, round 단계)
    """
    need_qty: float = 0.0
    _qty: int = 0
    adjustments: List[Adjustment] = field(default_factory=list)

    @property
    def qty(self) -> int:
        """현재 발주 수량"""
        return self._qty

    def set(self, new_qty: int, stage: str, reason: str = "") -> int:
        """발주 수량 변경 + 이력 기록.

        Args:
            new_qty: 새 발주 수량
            stage: 보정 단계명
            reason: 변경 사유 (선택)

        Returns:
            new_qty (편의상 반환하여 기존 패턴과 호환)
        """
        adj = Adjustment(
            stage=stage,
            before=self._qty,
            after=new_qty,
            reason=reason,
        )
        self.adjustments.append(adj)
        self._qty = new_qty
        return new_qty

    def changed_stages(self) -> List[Adjustment]:
        """실제 변경이 발생한 단계만 반환"""
        return [a for a in self.adjustments if a.changed]

    def summary(self) -> str:
        """한 줄 요약: 변경된 단계만 표시"""
        parts = []
        for a in self.adjustments:
            if a.changed:
                parts.append(f"{a.stage}({a.before}→{a.after})")
        if not parts:
            return f"need={self.need_qty:.1f} → order={self._qty} (변경 없음)"
        return f"need={self.need_qty:.1f} → " + " → ".join(parts) + f" = {self._qty}"

    def log_trace(self, item_nm: str, trace_id: str = "") -> None:
        """전체 이력을 로그로 출력 (INFO 레벨)"""
        prefix = f"[Proposal][{trace_id}] " if trace_id else "[Proposal] "
        lines = [f"{prefix}{item_nm}: {self.summary()}"]
        for a in self.adjustments:
            marker = "!" if a.changed else "="
            reason_str = f" ({a.reason})" if a.reason else ""
            lines.append(
                f"  {marker} {a.stage}: {a.before} → {a.after}{reason_str}"
            )
        logger.info("\n".join(lines))

    def stock_gate_summary(self) -> Optional[str]:
        """첫 번째 stock_gate 차단 지점을 한 줄로 반환.

        파이프라인 순서상 가장 먼저 차단된 게이트를 찾는다.
        차단 없으면 None 반환.
        """
        gate_stages = [
            "stock_gate_entry",
            "stock_gate_overstock",
            "stock_gate_surplus",
            "round_surplus_zero",
        ]
        for a in self.adjustments:
            if a.stage in gate_stages:
                return (
                    f"[STOCK_GATE] {a.stage} → "
                    f"order=0 ({a.reason})"
                )
        return None

    def to_dict(self) -> dict:
        """ctx에 저장할 딕셔너리 변환"""
        return {
            "need_qty": self.need_qty,
            "final_qty": self._qty,
            "stages": [
                {
                    "stage": a.stage,
                    "before": a.before,
                    "after": a.after,
                    "reason": a.reason,
                }
                for a in self.adjustments
            ],
            "summary": self.summary(),
        }
