"""JobHealthChecker — 예정 시각 + grace 내 success 기록 존재 여부 확인.

run_scheduler.py의 daemon thread에서 5분마다 호출됨.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional

from src.infrastructure.database.repos.job_run_repo import JobRunRepository
from src.infrastructure.job_health.job_run_tracker import compute_scheduled_for
from src.utils.logger import get_logger

logger = get_logger(__name__)


class JobHealthChecker:
    """예정 시각 대비 실행 결과를 감시한다."""

    def __init__(self):
        self._repo = JobRunRepository()

    # ───────── Public ─────────

    def run_once(self) -> None:
        """1회 전체 점검."""
        try:
            self._check_missed()
        except Exception as e:
            logger.warning(f"[HealthChecker] check_missed 실패: {e}")
        try:
            self._dispatch_failed()
        except Exception as e:
            logger.warning(f"[HealthChecker] dispatch_failed 실패: {e}")
        try:
            self._purge_old()
        except Exception as e:
            logger.warning(f"[HealthChecker] purge 실패: {e}")

    # ───────── Missed detection ─────────

    def _check_missed(self) -> None:
        """SCHEDULED_JOBS 순회 — grace 경과했는데 success 없으면 missed 삽입."""
        from src.application.scheduler.job_definitions import SCHEDULED_JOBS
        from src.settings.constants import JOB_HEALTH_GRACE_PERIOD_MIN

        now = datetime.now()
        for job_def in SCHEDULED_JOBS:
            if not job_def.enabled:
                continue

            scheduled_for_iso = compute_scheduled_for(
                schedule_str=job_def.schedule,
                schedules=job_def.schedules,
            )
            scheduled_dt = datetime.fromisoformat(scheduled_for_iso)

            # 아직 grace 기간 안이면 스킵
            if now < scheduled_dt + timedelta(minutes=JOB_HEALTH_GRACE_PERIOD_MIN):
                continue

            # 너무 오래된 건(24h+) 스킵 — missed 누적 방지
            if now - scheduled_dt > timedelta(hours=24):
                continue

            # 매장별 잡이면 각 매장 확인
            if job_def.multi_store:
                store_ids = self._get_active_store_ids()
                for sid in store_ids:
                    self._ensure_missed_recorded(job_def.name, sid, scheduled_for_iso)
            else:
                self._ensure_missed_recorded(job_def.name, None, scheduled_for_iso)

    def _ensure_missed_recorded(
        self,
        job_name: str,
        store_id: Optional[str],
        scheduled_for: str,
    ) -> None:
        """success 없으면 missed INSERT (부분 UNIQUE로 중복 제거)."""
        existing = self._repo.get_last_success(job_name, store_id, scheduled_for)
        if existing:
            return
        new_id = self._repo.insert_missed(job_name, store_id, scheduled_for)
        if new_id:
            logger.warning(
                f"[HealthChecker] MISSED 감지: {job_name} "
                f"store={store_id or '-'} sched={scheduled_for}"
            )

    # ───────── Dispatch ─────────

    def _dispatch_failed(self) -> None:
        """최근 1시간 failed/missed/timeout 중 미알림 건을 NotificationDispatcher로."""
        from src.application.services.notification_dispatcher import NotificationDispatcher
        from src.settings.constants import JOB_HEALTH_ALERT_COOLDOWN_HOUR

        rows = self._repo.get_recent_failed_unalerted(hours=JOB_HEALTH_ALERT_COOLDOWN_HOUR)
        if not rows:
            return

        dispatcher = NotificationDispatcher()
        for row in rows:
            subject = f"JOB {row['status'].upper()}: {row['job_name']}"
            body = (
                f"store={row.get('store_id') or '-'} "
                f"sched={row['scheduled_for']} "
                f"err={row.get('error_type') or '-'}: {(row.get('error_message') or '')[:200]}"
            )
            try:
                dispatcher.send(subject, body, level=row['status'].upper())
                self._repo.mark_alerted(row['id'])
            except Exception as e:
                logger.warning(f"[HealthChecker] 알림 실패 id={row['id']}: {e}")

    # ───────── Purge ─────────

    def _purge_old(self) -> None:
        from src.settings.constants import JOB_RUNS_RETENTION_DAYS
        # 하루 1회 정도만 작동 (5분마다 호출되지만 영향 적음)
        deleted = self._repo.purge_old(JOB_RUNS_RETENTION_DAYS)
        if deleted:
            logger.info(f"[HealthChecker] 오래된 job_runs {deleted}건 삭제")

    # ───────── Helpers ─────────

    def _get_active_store_ids(self) -> List[str]:
        try:
            from src.settings.store_context import StoreContext
            return [ctx.store_id for ctx in StoreContext.get_all_active()]
        except Exception as e:
            logger.warning(f"[HealthChecker] 매장 목록 조회 실패: {e}")
            return []


def start_health_checker_thread() -> threading.Thread:
    """run_scheduler.py 진입 시 호출. daemon thread 반환."""
    from src.settings.constants import JOB_HEALTH_CHECKER_INTERVAL_SEC

    checker = JobHealthChecker()

    def _loop():
        logger.info(
            f"[HealthChecker] 시작 (interval={JOB_HEALTH_CHECKER_INTERVAL_SEC}s)"
        )
        while True:
            try:
                checker.run_once()
            except Exception as e:
                logger.warning(f"[HealthChecker] loop 예외: {e}")
            time.sleep(JOB_HEALTH_CHECKER_INTERVAL_SEC)

    th = threading.Thread(target=_loop, name="JobHealthChecker", daemon=True)
    th.start()
    return th
