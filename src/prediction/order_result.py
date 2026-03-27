"""
발주 파이프라인 단계별 결과 전달용 DTO

각 _stage_* 메서드가 OrderResult를 받아 새 OrderResult를 반환한다.
with_qty()는 불변 복사본을 만들어 원본을 보호한다.
"""
from dataclasses import dataclass, replace
from typing import Dict, Any


@dataclass(frozen=True)
class OrderResult:
    """단계별 발주량 전달 객체 (불변)"""
    qty: int                       # 현재 단계의 발주량
    decided_by: str                # 마지막으로 값을 바꾼 단계명
    stages: Dict[str, Any]        # snapshot_stages 누적 기록

    def with_qty(self, qty: int, stage: str) -> "OrderResult":
        """새 qty로 새 OrderResult를 반환. 원본 불변."""
        new_stages = {**self.stages, stage: qty}
        return OrderResult(qty=qty, decided_by=stage, stages=new_stages)

    def with_stage_only(self, stage: str) -> "OrderResult":
        """qty 변경 없이 stage만 기록 (값이 같아도 추적)"""
        new_stages = {**self.stages, stage: self.qty}
        return OrderResult(qty=self.qty, decided_by=self.decided_by, stages=new_stages)

    @staticmethod
    def initial(qty: int, stage: str) -> "OrderResult":
        """파이프라인 첫 단계에서 생성"""
        return OrderResult(qty=qty, decided_by=stage, stages={stage: qty})
