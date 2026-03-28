"""
ActionProposalService — integrity 이상 분석 → 액션 제안 생성

자동화 2단계: 6개 check_name 전체 커버.
실패해도 예외 전파 없음 (발주 플로우 보호).
"""

import json
from datetime import date
from typing import Dict, List, Any, Optional

from src.prediction.action_type import ActionType
from src.infrastructure.database.repos.action_proposal_repo import ActionProposalRepository
from src.infrastructure.database.repos.eval_outcome_repo import EvalOutcomeRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ActionProposalService:
    """integrity 결과 → 액션 제안 생성 → DB 저장"""

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.today = date.today().isoformat()
        self.proposal_repo = ActionProposalRepository(store_id=store_id)
        self.eval_repo = EvalOutcomeRepository(store_id=store_id)

    def generate(self, check_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """제안 생성 → DB 저장 → 목록 반환. 실패 시 빈 리스트."""
        proposals: List[Dict[str, Any]] = []
        try:
            proposals = self._analyze(check_results)
            for p in proposals:
                self.proposal_repo.save(p)
            if proposals:
                logger.info(f"[제안] {self.store_id} {len(proposals)}건 생성")
        except Exception as e:
            logger.error(f"[제안] {self.store_id} 실패 (무시): {e}")
        return proposals

    def _analyze(self, check_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = []

        for result in check_results:
            check_name = result.get("check_name", "")
            status = result.get("status", "OK")

            if status in ("OK", "RESTORED"):
                continue

            details = self._parse_details(result.get("details"))
            count = result.get("count", 0)

            if check_name == "unavailable_with_sales":
                for item in self._get_dangerous_skips():
                    proposals.append(self._make(
                        action_type=ActionType.RESTORE_IS_AVAILABLE,
                        item_cd=item.get("item_cd"),
                        reason=(
                            f"is_available=0인데 최근 7일 판매 "
                            f"{item.get('sales_7d', '?')}개 "
                            f"(재고:{item.get('stock', '?')})"
                        ),
                        suggestion=(
                            "is_available → 1 복원 권장. "
                            "BGF 사이트 취급안함 해제 또는 inventory_repo 직접 복원."
                        ),
                        evidence=item,
                    ))

            elif check_name == "food_ghost_stock":
                proposals.append(self._make(
                    action_type=ActionType.CLEAR_GHOST_STOCK,
                    reason=f"입고 기록 없는 재고 {count}건 감지",
                    suggestion="실물 확인 후 재고 0 보정",
                    evidence=details,
                ))

            elif check_name == "expiry_time_mismatch":
                proposals.append(self._make(
                    action_type=ActionType.FIX_EXPIRY_TIME,
                    reason=f"유통기한 불일치 {count}건",
                    suggestion="실물 확인 후 OT 수정",
                    evidence=details,
                ))

            elif check_name == "expired_batch_remaining":
                proposals.append(self._make(
                    action_type=ActionType.CLEAR_EXPIRED_BATCH,
                    reason=f"만료 배치 {count}건 잔존",
                    suggestion="만료 배치 제거 또는 확인",
                    evidence=details,
                ))

            elif check_name == "missing_delivery_type":
                proposals.append(self._make(
                    action_type=ActionType.CHECK_DELIVERY_TYPE,
                    reason=f"배송 유형 누락 {count}건",
                    suggestion="배송 유형 수동 확인",
                    evidence=details,
                ))

            elif check_name == "past_expiry_active":
                proposals.append(self._make(
                    action_type=ActionType.DEACTIVATE_EXPIRED,
                    reason=f"만료 후 활성 상품 {count}건",
                    suggestion="비활성화 또는 폐기 처리",
                    evidence=details,
                ))

            else:
                proposals.append(self._make(
                    action_type=ActionType.MANUAL_CHECK_REQUIRED,
                    reason=f"{check_name} 이상 (status={status})",
                    suggestion="수동 확인 필요",
                    evidence=details,
                ))

        return proposals

    def _make(
        self,
        action_type: str,
        reason: str,
        suggestion: str,
        evidence: Any = None,
        item_cd: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "proposal_date": self.today,
            "store_id": self.store_id,
            "item_cd": item_cd,
            "action_type": action_type,
            "reason": reason,
            "suggestion": suggestion,
            "evidence": (
                json.dumps(evidence, ensure_ascii=False) if evidence else None
            ),
            "status": "PENDING",
        }

    def _get_dangerous_skips(self) -> List[Dict[str, Any]]:
        try:
            return self.eval_repo.get_dangerous_skips(self.store_id, self.today)
        except Exception:
            return []

    def _parse_details(self, details: Any) -> Dict[str, Any]:
        if isinstance(details, str):
            try:
                return json.loads(details)
            except Exception:
                return {}
        if isinstance(details, list) and details:
            return details[0] if isinstance(details[0], dict) else {}
        return details if isinstance(details, dict) else {}
