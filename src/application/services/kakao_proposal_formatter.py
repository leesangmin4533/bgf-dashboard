"""
KakaoProposalFormatter — 액션 제안 → 카카오 메시지 변환

카카오 메시지 최대 1000자 고려.
"""

from typing import Dict, List, Any


class KakaoProposalFormatter:
    """action_proposals → 카카오 메시지 문자열"""

    MAX_ITEMS = 5
    MAX_LENGTH = 1000

    def format(self, store_id: str, proposals: List[Dict[str, Any]]) -> str:
        if not proposals:
            return f"[{store_id}] 오늘 이상 없음"

        lines = [f"[{store_id}] 조치 필요 {len(proposals)}건\n"]

        for i, p in enumerate(proposals[: self.MAX_ITEMS], 1):
            item_info = f"item_cd:{p['item_cd']} | " if p.get("item_cd") else ""
            lines.append(
                f"{i}. {item_info}{p['action_type']}\n"
                f"   원인: {p['reason']}\n"
                f"   제안: {p['suggestion']}\n"
            )

        if len(proposals) > self.MAX_ITEMS:
            lines.append(f"... 외 {len(proposals) - self.MAX_ITEMS}건\n")

        lines.append("내일 발주 전 확인 부탁드립니다")

        return "\n".join(lines)[: self.MAX_LENGTH]
