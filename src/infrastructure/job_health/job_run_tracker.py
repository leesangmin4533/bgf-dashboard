"""JobRunTracker — 스케줄 잡 실행 기록 (context manager + wrappers).

피처 플래그 JOB_HEALTH_TRACKER_ENABLED=False 시 원본 함수를 투명 호출하여
Tracker 자체 버그가 스케줄러 전체를 마비시키지 못하게 한다.
"""

import functools
from datetime import datetime
from typing import Any, Callable, Optional

from src.infrastructure.database.repos.job_run_repo import JobRunRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class JobRunTracker:
    """스케줄 잡 실행 context manager.

    용법:
        with JobRunTracker("daily_order", "46513", "2026-04-07T07:00:00"):
            do_work()
    """

    def __init__(
        self,
        job_name: str,
        store_id: Optional[str],
        scheduled_for: str,
    ):
        self.job_name = job_name
        self.store_id = store_id
        self.scheduled_for = scheduled_for
        self._run_id: Optional[int] = None
        self._start_ts: Optional[float] = None
        self._repo = JobRunRepository()

    def __enter__(self) -> "JobRunTracker":
        self._start_ts = datetime.now().timestamp()
        try:
            self._run_id = self._repo.insert_running(
                job_name=self.job_name,
                store_id=self.store_id,
                scheduled_for=self.scheduled_for,
                started_at=datetime.now().isoformat(),
            )
        except Exception as e:
            # Tracker DB 실패는 원본 잡을 막지 않는다
            logger.warning(f"[JobRunTracker] insert_running 실패 ({self.job_name}): {e}")
            self._run_id = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._run_id is None:
            return False  # 예외 전파

        ended_at = datetime.now().isoformat()
        duration = datetime.now().timestamp() - (self._start_ts or 0)

        if exc_type is None:
            status = "success"
            err_msg, err_type = None, None
        else:
            status = "failed"
            err_msg = str(exc_val)[:2000]
            err_type = exc_type.__name__

        try:
            self._repo.update_end(
                run_id=self._run_id,
                status=status,
                ended_at=ended_at,
                duration_sec=round(duration, 2),
                error_message=err_msg,
                error_type=err_type,
            )
        except Exception as e:
            logger.warning(f"[JobRunTracker] update_end 실패 ({self.job_name}): {e}")

        # 실패 즉시 알림 시도 (dispatcher는 자체 try/except)
        if status == "failed":
            try:
                from src.application.services.notification_dispatcher import (
                    NotificationDispatcher,
                )
                dispatcher = NotificationDispatcher()
                dispatcher.send(
                    subject=f"JOB FAILED: {self.job_name}",
                    body=(
                        f"store={self.store_id or '-'} "
                        f"duration={round(duration, 1)}s "
                        f"err={err_type}: {err_msg}"
                    ),
                    level="ERROR",
                )
                # 알림 성공 시 alerted=1 마킹
                self._repo.mark_alerted(self._run_id)
            except Exception as e:
                logger.warning(f"[JobRunTracker] 알림 디스패치 실패: {e}")

        return False  # 예외 전파


def _is_enabled() -> bool:
    """피처 플래그 읽기. import 실패도 안전."""
    try:
        from src.settings.constants import JOB_HEALTH_TRACKER_ENABLED
        return bool(JOB_HEALTH_TRACKER_ENABLED)
    except Exception:
        return False


def wrap_multistore_task(
    job_name: str,
    task_fn: Callable,
    scheduled_for: str,
) -> Callable:
    """MultiStoreRunner.run_parallel에 넘길 task_fn을 Tracker로 감싼다.

    MultiStoreRunner는 task_fn(ctx: StoreContext) 시그니처 사용.
    피처 플래그 OFF 시 원본 투명 호출.
    """
    if not _is_enabled():
        return task_fn

    @functools.wraps(task_fn)
    def wrapped(ctx: Any) -> Any:
        store_id = getattr(ctx, "store_id", None)
        with JobRunTracker(job_name, store_id, scheduled_for):
            return task_fn(ctx)

    return wrapped


def track_single_job(
    job_name: str,
    scheduled_for_getter: Callable[[], str],
    store_id: Optional[str] = None,
):
    """non-multi_store 잡용 데코레이터.

    Usage:
        @track_single_job("monthly_store_analysis", lambda: compute_scheduled_for(...))
        def run_monthly_analysis():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not _is_enabled():
                return func(*args, **kwargs)
            with JobRunTracker(job_name, store_id, scheduled_for_getter()):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def compute_scheduled_for(schedule_str: str = "", schedules: Optional[list] = None) -> str:
    """JobDefinition.schedule(s) 중 가장 최근 정각을 ISO8601로.

    - "07:00" → 오늘 07:00 (아직 안 됐으면 어제)
    - "Mon 08:00" → 가장 최근 월요일 08:00
    - schedules=[...] → 후보 중 최대(가장 최근)
    """
    from datetime import timedelta
    now = datetime.now()
    candidates = schedules or [schedule_str]
    weekday_map = {
        "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
        "Fri": 4, "Sat": 5, "Sun": 6,
    }

    best: Optional[datetime] = None
    for s in candidates:
        if not s:
            continue
        parts = s.split()
        hhmm = parts[-1]
        try:
            h, m = map(int, hhmm.split(":"))
        except ValueError:
            continue
        fire = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if fire > now:
            fire -= timedelta(days=1)
        if len(parts) == 2:
            target = weekday_map.get(parts[0])
            if target is None:
                continue
            while fire.weekday() != target:
                fire -= timedelta(days=1)
        if best is None or fire > best:
            best = fire

    if best is None:
        best = now
    return best.isoformat()
