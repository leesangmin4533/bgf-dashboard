"""Watchdog — 외부 프로세스에서 스케줄러 생존 감시.

Windows 작업 스케줄러에 5분 주기로 등록.
스케줄러 본체가 죽어서 in-process 감지기도 함께 죽은 경우를 커버.

사용법:
    python scripts/watchdog.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# src/ import 경로
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.infrastructure.job_health.heartbeat_writer import HEARTBEAT_PATH  # noqa: E402
from src.infrastructure.database.repos.job_run_repo import JobRunRepository  # noqa: E402
from src.application.services.notification_dispatcher import NotificationDispatcher  # noqa: E402
from src.settings.constants import (  # noqa: E402
    HEARTBEAT_STALE_THRESHOLD_SEC,
    JOB_HEALTH_ALERT_COOLDOWN_HOUR,
)


def check_heartbeat(dispatcher: NotificationDispatcher) -> bool:
    """heartbeat 파일 mtime > 임계면 Scheduler Dead 알림. 살아있으면 True."""
    if not HEARTBEAT_PATH.exists():
        dispatcher.send(
            "WATCHDOG: Heartbeat Missing",
            f"heartbeat 파일 없음: {HEARTBEAT_PATH}",
            level="CRITICAL",
        )
        return False

    mtime = datetime.fromtimestamp(HEARTBEAT_PATH.stat().st_mtime)
    age = (datetime.now() - mtime).total_seconds()
    if age > HEARTBEAT_STALE_THRESHOLD_SEC:
        dispatcher.send(
            "WATCHDOG: Scheduler Dead",
            f"heartbeat 정체 {int(age)}초 "
            f"(마지막 갱신: {mtime.isoformat()}, 임계: {HEARTBEAT_STALE_THRESHOLD_SEC}s)",
            level="CRITICAL",
        )
        return False
    return True


def check_job_runs(dispatcher: NotificationDispatcher) -> int:
    """job_runs에서 최근 failed/missed 미알림 건 확인.

    Returns: 발송한 알림 건수.
    """
    try:
        repo = JobRunRepository()
        rows = repo.get_recent_failed_unalerted(hours=JOB_HEALTH_ALERT_COOLDOWN_HOUR)
    except Exception as e:
        dispatcher.send(
            "WATCHDOG: DB 조회 실패",
            f"job_runs 조회 실패: {e}",
            level="WARNING",
        )
        return 0

    count = 0
    for row in rows:
        subject = f"WATCHDOG/{row['status'].upper()}: {row['job_name']}"
        body = (
            f"store={row.get('store_id') or '-'} "
            f"sched={row['scheduled_for']} "
            f"err={row.get('error_type') or '-'}: {(row.get('error_message') or '')[:200]}"
        )
        dispatcher.send(subject, body, level=row['status'].upper())
        try:
            repo.mark_alerted(row['id'])
        except Exception:
            pass
        count += 1
    return count


def main() -> int:
    dispatcher = NotificationDispatcher()
    alive = check_heartbeat(dispatcher)
    alerted = check_job_runs(dispatcher)
    print(f"[Watchdog] heartbeat={'OK' if alive else 'DEAD'} alerts_sent={alerted}")
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
