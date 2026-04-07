"""Claude CLI를 통한 자동 원인 분석 (L1: 읽기 전용)

이상 감지 시 claude -p 호출로 AI 분석을 실행.
코드 수정 권한 없음 (--allowed-tools Read Grep Glob).
"""

import json
import os
import shutil
import subprocess
import time
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
_CLAUDE_BIN = shutil.which("claude") or "claude"  # shutil.which로 정확한 경로
_MIN_RESPONSE_LEN = 50  # 이 길이 미만의 응답은 실패로 처리 (빈 응답/에러 메시지 방지)


def _empty_result(error: Optional[str] = None) -> dict:
    """skip/early-return 시 일관된 반환 구조"""
    return {
        "responded": False,
        "output_path": None,
        "summary": None,
        "duration_sec": 0.0,
        "anomaly_count": 0,
        "prompt_len": 0,
        "response_len": 0,
        "input_snapshot_path": None,
        "error": error,
    }


class ClaudeResponder:
    """Claude CLI를 통한 자동 원인 분석"""

    def respond_if_needed(self) -> dict:
        """pending_issues.json이 있으면 Claude 분석 실행

        Returns:
            {
              "responded": bool, "output_path": str|None, "summary": str|None,
              "duration_sec": float, "anomaly_count": int,
              "prompt_len": int, "response_len": int,
              "input_snapshot_path": str|None, "error": str|None,
            }
        """
        if not CLAUDE_AUTO_RESPOND_ENABLED:
            return _empty_result()

        if not _PENDING_PATH.exists():
            return _empty_result()

        try:
            issues = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[AutoRespond] pending_issues.json 파싱 실패: {e}")
            return _empty_result(error=f"pending 파싱 실패: {e}")

        # anomaly도 없고 milestone KPI 미달도 없으면 스킵
        has_anomalies = bool(issues.get("anomalies"))
        has_milestone_issue = any(
            isinstance(v, dict) and v.get("status") not in ("ACHIEVED", None)
            for v in (issues.get("milestone") or {}).values()
        )
        if not has_anomalies and not has_milestone_issue:
            return _empty_result()

        # 마일스톤 최신 스냅샷 병합
        milestone = self._get_latest_milestone()
        if milestone:
            issues["milestone"] = milestone

        # [NEW] 입력 스냅샷 저장 (역추적용 원본 보존)
        snapshot_path = self._snapshot_input(issues)
        snapshot_str = str(snapshot_path) if snapshot_path else None

        anomaly_count = len(issues.get("anomalies") or [])
        prompt = self._build_prompt(issues)
        prompt_len = len(prompt)

        t0 = time.monotonic()

        # Claude 호출
        try:
            output = self._call_claude(prompt)
        except Exception as e:
            duration = round(time.monotonic() - t0, 2)
            logger.warning(
                f"[AutoRespond] Claude 호출 실패 (duration={duration}s): {e}"
            )
            self._append_analysis_to_pending(str(e), None, [])
            return {
                "responded": False,
                "output_path": None,
                "summary": str(e),
                "duration_sec": duration,
                "anomaly_count": anomaly_count,
                "prompt_len": prompt_len,
                "response_len": 0,
                "input_snapshot_path": snapshot_str,
                "error": str(e),
            }

        duration = round(time.monotonic() - t0, 2)
        response_len = len(output)

        # 결과 저장
        today = date.today().isoformat()
        output_path = _OUTPUT_DIR / f"{today}_response.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")

        # 요약 추출 + pending에 analysis 결과 병합 (카카오는 체인 리포트에서 통합 발송)
        summary = self._extract_summary(output)
        suggested_issues = self._extract_suggested_issues(output)
        self._append_analysis_to_pending(summary, str(output_path), suggested_issues)

        logger.info(
            f"[AutoRespond] 분석 완료: duration={duration}s, "
            f"anomalies={anomaly_count}, prompt={prompt_len} chars, "
            f"response={response_len} chars, path={output_path}"
        )
        return {
            "responded": True,
            "output_path": str(output_path),
            "summary": summary,
            "duration_sec": duration,
            "anomaly_count": anomaly_count,
            "prompt_len": prompt_len,
            "response_len": response_len,
            "input_snapshot_path": snapshot_str,
            "error": None,
        }

    def _snapshot_input(self, issues: dict) -> Optional[Path]:
        """pending_issues.json 스냅샷을 data/auto_respond/에 저장.
        실패해도 본작업 중단 없음 (None 반환)."""
        try:
            today = date.today().isoformat()
            path = _OUTPUT_DIR / f"{today}_input.json"
            if path.exists():
                ts = datetime.now().strftime("%H%M%S")
                path = _OUTPUT_DIR / f"{today}_input_{ts}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(issues, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return path
        except Exception as e:
            logger.debug(f"[AutoRespond] 입력 스냅샷 저장 실패 (무시): {e}")
            return None

    def _call_claude(self, prompt: str) -> str:
        """claude CLI 비대화형 호출 (읽기 전용)"""
        claude_cmd = _CLAUDE_BIN
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        cmd = [
            claude_cmd,
            "-p", prompt,
            "--output-format", "text",
            "--model", CLAUDE_AUTO_RESPOND_MODEL,
            "--max-turns", str(CLAUDE_AUTO_RESPOND_MAX_TURNS),
            "--allowed-tools", "Read Grep Glob",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=CLAUDE_AUTO_RESPOND_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if result.returncode != 0:
            # 진단 정보 덤프 (data/auto_respond/debug_{timestamp}.log)
            self._dump_failure_context(cmd, result, env)
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or "(no output)"
            raise RuntimeError(
                f"claude exit={result.returncode}: {detail[:500]} "
                f"(bin={claude_cmd}, cwd={_PROJECT_DIR})"
            )

        output = result.stdout or ""
        # 응답 유효성 검증 — returncode=0이어도 빈/짧은 응답은 실패 처리
        if len(output.strip()) < _MIN_RESPONSE_LEN:
            self._dump_failure_context(cmd, result, env)
            raise RuntimeError(
                f"claude returned empty/short response: "
                f"len={len(output.strip())} < {_MIN_RESPONSE_LEN} "
                f"(bin={claude_cmd})"
            )
        return output

    def _dump_failure_context(self, cmd: list, result, env: dict) -> None:
        """실패 시 진단 정보를 debug 로그로 덤프"""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_path = _OUTPUT_DIR / f"debug_{ts}.log"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            key_env = {
                k: env.get(k, "")
                for k in (
                    "PATH", "PYTHONUTF8", "PYTHONIOENCODING",
                    "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                )
            }
            # prompt는 덤프 제외 (cmd[2]) — 길이/민감도 이슈
            cmd_safe = cmd[:2] + ["[prompt omitted]"] + cmd[3:]
            lines = [
                f"[timestamp] {datetime.now().isoformat()}",
                f"[claude_bin] {_CLAUDE_BIN}",
                f"[returncode] {result.returncode}",
                f"[cwd] {_PROJECT_DIR}",
                f"[cmd] {cmd_safe}",
                f"[env] {json.dumps(key_env, ensure_ascii=False)}",
                f"[stdout:{len(result.stdout or '')}chars]",
                (result.stdout or "")[:2000],
                f"[stderr:{len(result.stderr or '')}chars]",
                (result.stderr or "")[:2000],
            ]
            debug_path.write_text("\n".join(lines), encoding="utf-8")
            logger.warning(f"[AutoRespond] 진단 로그 저장: {debug_path}")
        except Exception as e:
            logger.debug(f"[AutoRespond] 진단 덤프 실패 (무시): {e}")

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
