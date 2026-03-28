"""
AISummaryService — integrity 체크 + SKIP 통계 자동 요약

Phase 1.67 DataIntegrityService.run_all_checks() 직후 호출.
anomaly > 0일 때만 실행하여 비용 절감.

기본: rule_based (외부 API 없음, $0)
선택: claude-haiku (BGF 외부전송 확인 후 .env에서 전환)
"""

import json
import os
from datetime import date
from typing import Dict, List, Any, Optional

from src.infrastructure.database.repos.ai_summary_repo import AISummaryRepository
from src.infrastructure.database.repos.eval_outcome_repo import EvalOutcomeRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)

AI_SUMMARY_ENABLED = os.getenv("AI_SUMMARY_ENABLED", "true").lower() == "true"
AI_MODEL = os.getenv("AI_SUMMARY_MODEL", "rule_based")


class AISummaryService:
    """integrity + SKIP 요약 서비스"""

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.today = date.today().isoformat()
        self.summary_repo = AISummaryRepository()
        self.eval_repo = EvalOutcomeRepository(store_id=store_id)

    def summarize_integrity(
        self, check_results: List[Dict[str, Any]]
    ) -> Optional[str]:
        """integrity 체크 결과 요약. anomaly=0이면 None 반환."""
        if not AI_SUMMARY_ENABLED:
            return None

        anomaly_count = sum(
            1 for r in check_results
            if r.get("status") not in ("OK", "RESTORED")
        )
        if anomaly_count == 0:
            logger.info(f"[AI요약] {self.store_id} 이상 없음, 건너뜀")
            return None

        try:
            data = self._build_report(check_results, anomaly_count)
            summary = self._generate_summary(data)

            self.summary_repo.upsert_summary(
                summary_date=self.today,
                summary_type="integrity",
                store_id=self.store_id,
                summary_text=summary,
                anomaly_count=anomaly_count,
                model_used=AI_MODEL,
                token_count=data.get("token_count", 0),
                cost_usd=data.get("cost_usd", 0.0),
            )
            logger.info(
                f"[AI요약] {self.store_id} 저장 완료 (이상 {anomaly_count}건)"
            )
            return summary
        except Exception as e:
            logger.error(f"[AI요약] {self.store_id} 실패: {e}")
            return None

    # ------------------------------------------------------------------
    # 보고서 데이터 조립
    # ------------------------------------------------------------------

    def _build_report(
        self, check_results: List[Dict[str, Any]], anomaly_count: int
    ) -> Dict[str, Any]:
        yesterday = self.summary_repo.get_latest_by_store(
            self.store_id, "integrity"
        ) or {}

        dangerous_skips: List[Dict] = []
        skip_stats: List[Dict] = []
        try:
            dangerous_skips = self.eval_repo.get_dangerous_skips(
                self.store_id, self.today
            )
            skip_stats = self.eval_repo.get_skip_stats_by_reason(
                self.store_id, self.today
            )
        except Exception as e:
            logger.debug(f"[AI요약] SKIP 통계 조회 실패 (무시): {e}")

        return {
            "store_id": self.store_id,
            "summary_date": self.today,
            "anomaly_count": anomaly_count,
            "integrity_results": check_results,
            "dangerous_skips": dangerous_skips,
            "skip_stats": skip_stats,
            "yesterday_anomaly": yesterday.get("anomaly_count"),
        }

    # ------------------------------------------------------------------
    # 요약 생성
    # ------------------------------------------------------------------

    def _generate_summary(self, data: Dict[str, Any]) -> str:
        if AI_MODEL == "rule_based":
            return self._rule_based_summary(data)
        return self._api_based_summary(data)

    def _rule_based_summary(self, data: Dict[str, Any]) -> str:
        """외부 API 없이 구조화된 텍스트 요약 생성"""
        lines = [
            f"=== [{data['store_id']}] 하네스 리포트 {data['summary_date']} ===",
            "",
        ]

        # 트렌드
        yest = data["yesterday_anomaly"]
        if yest is not None:
            diff = data["anomaly_count"] - yest
            trend = (
                f"(어제 {yest}건 대비 {'▲' if diff > 0 else '▼'}{abs(diff)}건)"
                if diff != 0
                else "(어제와 동일)"
            )
            lines.append(f"integrity 이상: {data['anomaly_count']}건 {trend}")
        else:
            lines.append(f"integrity 이상: {data['anomaly_count']}건")

        lines.append("")

        # 체크 결과
        lines.append("[무결성 체크 결과]")
        icons = {
            "OK": "OK", "WARN": "!!", "FAIL": "XX",
            "RESTORED": "FX", "ERROR": "ER",
        }
        for r in data["integrity_results"]:
            status = r.get("status", "?")
            icon = icons.get(status, "??")
            count = r.get("count", 0)
            lines.append(f"  {icon} {r['check_name']}: {status} ({count}건)")

        lines.append("")

        # 위험 SKIP
        dangerous = data.get("dangerous_skips", [])
        if dangerous:
            lines.append(
                f"[위험: 판매 중인데 발주 제외 ({len(dangerous)}건)]"
            )
            for item in dangerous[:5]:
                lines.append(
                    f"  - item_cd:{item.get('item_cd', '?')} "
                    f"재고:{item.get('stock', '?')} "
                    f"7일판매:{item.get('sales_7d', '?')}개"
                )
            if len(dangerous) > 5:
                lines.append(f"  ... 외 {len(dangerous) - 5}건")
        else:
            lines.append("[위험 항목 없음]")

        lines.append("")

        # SKIP 통계
        skip_stats = data.get("skip_stats", [])
        if skip_stats:
            lines.append("[SKIP/PASS 현황]")
            for stat in skip_stats:
                warn = ""
                if stat.get("sales_but_skipped", 0) > 0:
                    warn = f" (판매중 {stat['sales_but_skipped']}건 포함)"
                lines.append(
                    f"  - {stat['skip_reason']}: {stat['total_cnt']}건{warn}"
                )

        return "\n".join(lines)

    def _api_based_summary(self, data: Dict[str, Any]) -> str:
        """Anthropic API 요약. BGF 외부전송 확인 후 활성화."""
        try:
            import anthropic
        except ImportError:
            logger.warning("[AI요약] anthropic 패키지 미설치, 규칙 기반으로 대체")
            return self._rule_based_summary(data)

        daily_cost = self.summary_repo.get_daily_cost(self.today)
        if daily_cost > 0.5:
            logger.warning(
                f"[AI요약] 비용 상한 초과 ({daily_cost:.4f}$), 규칙 기반 대체"
            )
            return self._rule_based_summary(data)

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("[AI요약] ANTHROPIC_API_KEY 미설정, 규칙 기반 대체")
            return self._rule_based_summary(data)

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    "편의점 발주 시스템 점검 결과를 한국어 3-5문장으로 요약하세요. "
                    "이상 항목과 조치 방법을 구체적으로 포함하세요.\n\n"
                    f"{json.dumps(data, ensure_ascii=False, indent=2)}"
                ),
            }],
        )
        input_t = response.usage.input_tokens
        output_t = response.usage.output_tokens
        data["token_count"] = input_t + output_t
        data["cost_usd"] = (input_t * 0.00000025) + (output_t * 0.00000125)
        return response.content[0].text
