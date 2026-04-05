"""파이프라인 단계별 실행 상태 기록 + 체인 검증

각 야간 스케줄 단계(23:55~00:02)가 완료 시 결과를 기록.
다음 단계는 이전 단계 성공 여부를 확인하고 조건부 실행/스킵.
실패 시 즉시 카카오 긴급 알림.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATUS_PATH = Path(__file__).parent.parent.parent / ".claude" / "pipeline_status.json"

# 파이프라인 단계 순서 + 의존관계
PIPELINE_CHAIN = {
    "ops_detect": {"depends_on": [], "critical": True},
    "claude_respond": {"depends_on": ["ops_detect"], "critical": False},
    "chain_report": {"depends_on": [], "critical": False},
}


def record_step(
    step_name: str,
    success: bool,
    summary: str,
    details: dict = None,
) -> None:
    """파이프라인 단계 실행 결과 기록"""
    try:
        data = _load_or_init()

        data["steps"][step_name] = {
            "success": success,
            "summary": summary,
            "details": details or {},
            "completed_at": datetime.now().isoformat(),
        }

        _save(data)

        # 실패 시 즉시 카카오 긴급 알림
        if not success:
            _send_failure_alert(step_name, summary)

    except Exception as e:
        logger.debug(f"[PipelineStatus] 기록 실패 (무시): {e}")


def check_dependencies(step_name: str) -> dict:
    """이전 단계 성공 여부 확인

    Returns:
        {"can_proceed": bool, "reason": str, "failed_deps": list}
    """
    chain = PIPELINE_CHAIN.get(step_name, {})
    deps = chain.get("depends_on", [])

    if not deps:
        return {"can_proceed": True, "reason": "의존 단계 없음", "failed_deps": []}

    data = _load_or_init()
    steps = data.get("steps", {})

    failed_deps = []
    for dep in deps:
        dep_step = steps.get(dep)
        if dep_step is None:
            failed_deps.append(f"{dep} (미실행)")
        elif not dep_step.get("success"):
            failed_deps.append(f"{dep} (실패: {dep_step.get('summary', '?')[:50]})")

    if failed_deps:
        return {
            "can_proceed": False,
            "reason": f"선행 단계 실패: {', '.join(failed_deps)}",
            "failed_deps": failed_deps,
        }

    return {"can_proceed": True, "reason": "선행 단계 정상", "failed_deps": []}


def get_pipeline_summary() -> str:
    """전체 파이프라인 상태 한줄 요약"""
    data = _load_or_init()
    steps = data.get("steps", {})
    if not steps:
        return "파이프라인 미실행"

    total = len(steps)
    ok = sum(1 for s in steps.values() if s.get("success"))
    failed = [name for name, s in steps.items() if not s.get("success")]

    if not failed:
        return f"전체 {total}단계 정상"
    return f"{ok}/{total} 성공, 실패: {', '.join(failed)}"


def load_status() -> dict:
    """현재 파이프라인 상태 로드"""
    if not _STATUS_PATH.exists():
        return {}
    try:
        return json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clear_status() -> None:
    """파이프라인 상태 초기화"""
    _STATUS_PATH.unlink(missing_ok=True)


# ── 내부 함수 ──

def _load_or_init() -> dict:
    """상태 파일 로드 또는 초기화"""
    today = datetime.now().strftime("%Y-%m-%d")
    if _STATUS_PATH.exists():
        try:
            data = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "steps": {}}


def _save(data: dict) -> None:
    """상태 파일 저장"""
    _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATUS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _send_failure_alert(step_name: str, error_msg: str) -> None:
    """단계 실패 시 즉시 카카오 긴급 알림"""
    try:
        from src.notification.kakao_notifier import KakaoNotifier
        message = (
            f"[파이프라인 실패] {step_name}\n"
            f"시각: {datetime.now().strftime('%H:%M')}\n"
            f"오류: {error_msg[:150]}"
        )
        notifier = KakaoNotifier()
        notifier.send_message(message, category="daily_chain")
    except Exception as e:
        logger.debug(f"[PipelineStatus] 긴급 알림 실패 (무시): {e}")
