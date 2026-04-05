"""Claude CLI를 통한 자동 원인 분석 (L1: 읽기 전용)

이상 감지 시 claude -p 호출로 AI 분석을 실행.
코드 수정 권한 없음 (--allowed-tools Read Grep Glob).
"""

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from src.settings.constants import (
    CLAUDE_AUTO_RESPOND_ENABLED,
    CLAUDE_AUTO_RESPOND_MODEL,
    CLAUDE_AUTO_RESPOND_MAX_TURNS,
    CLAUDE_AUTO_RESPOND_TIMEOUT,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_DIR = Path(__file__).parent.parent.parent  # bgf_auto/
_PENDING_PATH = _PROJECT_DIR / ".claude" / "pending_issues.json"
_OUTPUT_DIR = _PROJECT_DIR / "data" / "auto_respond"
_CLAUDE_BIN = Path.home() / ".local" / "bin" / "claude"  # 절대 경로


class ClaudeResponder:
    """Claude CLI를 통한 자동 원인 분석"""

    def respond_if_needed(self) -> dict:
        """pending_issues.json이 있으면 Claude 분석 실행

        Returns:
            {"responded": bool, "output_path": str|None, "summary": str|None}
        """
        if not CLAUDE_AUTO_RESPOND_ENABLED:
            return {"responded": False, "output_path": None, "summary": None}

        if not _PENDING_PATH.exists():
            return {"responded": False, "output_path": None, "summary": None}

        try:
            issues = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[AutoRespond] pending_issues.json 파싱 실패: {e}")
            return {"responded": False, "output_path": None, "summary": None}

        if not issues.get("anomalies"):
            _PENDING_PATH.unlink(missing_ok=True)
            return {"responded": False, "output_path": None, "summary": None}

        # 마일스톤 최신 스냅샷 병합
        milestone = self._get_latest_milestone()
        if milestone:
            issues["milestone"] = milestone

        # prompt 구성 + Claude 호출
        prompt = self._build_prompt(issues)
        try:
            output = self._call_claude(prompt)
        except Exception as e:
            logger.warning(f"[AutoRespond] Claude 호출 실패: {e}")
            # pending 삭제하지 않음 — DailyChainReport가 최종 처리
            self._append_analysis_to_pending(str(e), None, [])
            return {"responded": False, "output_path": None, "summary": str(e)}

        # 결과 저장
        today = date.today().isoformat()
        output_path = _OUTPUT_DIR / f"{today}_response.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")

        # 요약 추출 + pending에 analysis 결과 병합 (카카오는 체인 리포트에서 통합 발송)
        summary = self._extract_summary(output)
        suggested_issues = self._extract_suggested_issues(output)
        self._append_analysis_to_pending(summary, str(output_path), suggested_issues)

        # pending 삭제하지 않음 — DailyChainReport가 최종 처리

        logger.info(f"[AutoRespond] 분석 완료: {output_path}")
        return {"responded": True, "output_path": str(output_path), "summary": summary}

    def _call_claude(self, prompt: str) -> str:
        """claude CLI 비대화형 호출 (읽기 전용)"""
        claude_cmd = str(_CLAUDE_BIN) if _CLAUDE_BIN.exists() else "claude"
        result = subprocess.run(
            [
                claude_cmd,
                "-p", prompt,
                "--output-format", "text",
                "--model", CLAUDE_AUTO_RESPOND_MODEL,
                "--max-turns", str(CLAUDE_AUTO_RESPOND_MAX_TURNS),
                "--allowed-tools", "Read Grep Glob",
            ],
            cwd=str(_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=CLAUDE_AUTO_RESPOND_TIMEOUT,
            encoding="utf-8",
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"claude exit={result.returncode}: {stderr[:200] if stderr else '(no stderr)'}"
            )
        return result.stdout

    def _build_prompt(self, issues: dict) -> str:
        """분석 prompt 생성"""
        anomalies = issues.get("anomalies", [])
        milestone = issues.get("milestone", {})

        lines = [
            "bgf_auto 프로젝트에서 운영 이상이 감지되었습니다.",
            "원인을 분석하고 대응 방안을 제안해주세요.",
            "",
            "## 감지된 이상",
        ]
        for a in anomalies:
            lines.append(f"- [{a.get('priority', '?')}] {a.get('title', '?')}")
            if a.get("evidence"):
                lines.append(f"  데이터: {json.dumps(a['evidence'], ensure_ascii=False)}")

        if milestone:
            lines.append("")
            lines.append("## 마일스톤 KPI 현황")
            for k, v in milestone.items():
                if isinstance(v, dict):
                    lines.append(f"- {k}: {v.get('value')} ({v.get('status')})")

        lines.extend([
            "",
            "## 분석 요청",
            "1. 이상의 근본 원인을 DB 데이터 기반으로 분석 (eval_outcomes, waste_slip_items, order_fail_reasons 조회)",
            "2. docs/05-issues/ 이슈체인에서 관련 PLANNED 항목 확인",
            "3. 즉시 적용 가능한 대응 제안 (상수 조정, 기존 로직 활용 등)",
            "4. 코드 변경이 필요하면 구체적 방향 제시 (수정은 하지 마세요)",
            "5. 신규 이슈가 필요하면 아래 JSON 형식으로 제안 (```json 블록):",
            '   [{"metric_name": "...", "issue_chain_file": "prediction.md 또는 order-execution.md 등", '
            '"title": "...", "priority": "P2", "description": "..."}]',
            "   기존 이슈체인(docs/05-issues/)에 이미 있는 내용이면 제안하지 마세요.",
            "",
            "결과를 마크다운으로 작성하되, 첫 3줄에 핵심 요약을 넣어주세요.",
        ])

        return "\n".join(lines)

    def _extract_summary(self, full_text: str, max_chars: int = 300) -> str:
        """분석 결과의 첫 3줄 추출 (카카오 발송용)"""
        lines = full_text.strip().split("\n")
        summary_lines = [line for line in lines[:5] if line.strip()][:3]
        summary = "\n".join(summary_lines)
        if len(summary) > max_chars:
            summary = summary[:max_chars - 3] + "..."
        return summary

    def _get_latest_milestone(self) -> Optional[dict]:
        """milestone_snapshots에서 최신 KPI 조회"""
        try:
            from src.infrastructure.database.connection import DBRouter
            conn = DBRouter.get_common_connection()
            try:
                row = conn.execute(
                    "SELECT k1_value, k1_status, k2_food_value, k2_total_value, k2_status, "
                    "k3_value, k3_status, k4_value, k4_status "
                    "FROM milestone_snapshots WHERE stage = '3' "
                    "ORDER BY snapshot_date DESC LIMIT 1"
                ).fetchone()
                if not row:
                    return None
                return {
                    "K1": {"value": row["k1_value"], "status": row["k1_status"]},
                    "K2": {"value": row["k2_food_value"], "status": row["k2_status"]},
                    "K3": {"value": row["k3_value"], "status": row["k3_status"]},
                    "K4": {"value": row["k4_value"], "status": row["k4_status"]},
                }
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[AutoRespond] 마일스톤 조회 실패 (무시): {e}")
            return None

    def _extract_suggested_issues(self, full_text: str) -> list:
        """분석 결과에서 ```json 블록의 suggested_issues 추출"""
        import re
        pattern = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
        for match in pattern.finditer(full_text):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, list) and data and "title" in data[0]:
                    return data
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        return []

    def _append_analysis_to_pending(
        self, summary: str, output_path: Optional[str], suggested_issues: list
    ) -> None:
        """pending_issues.json에 analysis 결과 병합"""
        try:
            if _PENDING_PATH.exists():
                data = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
            else:
                data = {"anomalies": []}

            data["analysis"] = {
                "responded": output_path is not None,
                "summary": summary,
                "output_path": output_path,
                "suggested_issues": suggested_issues,
            }

            _PENDING_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[AutoRespond] pending 병합 실패 (무시): {e}")
