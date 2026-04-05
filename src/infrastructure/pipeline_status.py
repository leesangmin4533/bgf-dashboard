"""파이프라인 단계별 실행 상태 기록

각 야간 스케줄 단계(23:55~00:02)가 완료 시 결과를 기록.
00:05에 Claude Code가 전체 검토.
"""

import json
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATUS_PATH = Path(__file__).parent.parent.parent / ".claude" / "pipeline_status.json"


def record_step(
    step_name: str,
    success: bool,
    summary: str,
    details: dict = None,
) -> None:
    """파이프라인 단계 실행 결과 기록

    Args:
        step_name: 단계명 (ops_detect, milestone, claude_respond, chain_report)
        success: 성공 여부
        summary: 한줄 요약
        details: 추가 데이터 (건수 등)
    """
    try:
        if _STATUS_PATH.exists():
            data = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
        else:
            data = {"date": datetime.now().strftime("%Y-%m-%d"), "steps": {}}

        # 날짜가 바뀌었으면 초기화
        today = datetime.now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            data = {"date": today, "steps": {}}

        data["steps"][step_name] = {
            "success": success,
            "summary": summary,
            "details": details or {},
            "completed_at": datetime.now().isoformat(),
        }

        _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATUS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.debug(f"[PipelineStatus] 기록 실패 (무시): {e}")


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
