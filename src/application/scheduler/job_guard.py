"""
JobGuard — 스케줄러 작업 중복 실행 방지 + 실패 재시도 + 매장별 추적

3단계 방어:
  1. JobGuard: lock 파일 기반 중복 실행 차단
  2. RetryManager: 실패 매장 15분 재시도 (최대 3회)
  3. PhaseCheckpoint: Phase별 완료 상태 추적 (재시도 시 스킵)
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from src.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_LOCK_DIR = _PROJECT_ROOT / "data" / "job_locks"
_STATE_DIR = _PROJECT_ROOT / "data" / "job_state"

# 설정
LOCK_STALE_HOURS = 2          # lock이 이 시간 초과하면 stale 판정
MAX_RETRY_ATTEMPTS = 3        # 최대 재시도 횟수
RETRY_INTERVAL_MINUTES = 15   # 재시도 간격 (분)


# ═══════════════════════════════════════════════
# 1단계: JobGuard (중복 실행 방지)
# ═══════════════════════════════════════════════

class JobGuard:
    """작업 수준 lock으로 동일 작업 중복 실행 방지

    Usage:
        guard = JobGuard("daily_order")
        if not guard.acquire():
            logger.warning("이미 실행 중 — 스킵")
            return
        try:
            do_work()
        finally:
            guard.release()

    또는 context manager:
        with JobGuard("daily_order") as guard:
            if not guard.acquired:
                return
            do_work()
    """

    def __init__(self, job_name: str):
        self.job_name = job_name
        self.acquired = False
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self._lock_path = _LOCK_DIR / f"{job_name}.lock"

    def acquire(self) -> bool:
        """lock 획득 시도

        Returns:
            True: 획득 성공 (진행 가능)
            False: 이미 다른 실행이 진행 중 (스킵 필요)
        """
        if self._lock_path.exists():
            lock_data = self._read_lock()
            if lock_data:
                pid = lock_data.get("pid")
                started = lock_data.get("started_at", "")

                # PID가 살아있는지 확인
                if pid and self._is_pid_alive(pid):
                    # stale 체크 (2시간 초과)
                    if self._is_stale(started):
                        logger.warning(
                            f"[JobGuard] {self.job_name}: lock stale "
                            f"(pid={pid}, started={started}, {LOCK_STALE_HOURS}h 초과) "
                            f"→ 강제 해제 후 진입"
                        )
                        self._lock_path.unlink(missing_ok=True)
                    else:
                        logger.warning(
                            f"[JobGuard] {self.job_name}: 이미 실행 중 "
                            f"(pid={pid}, started={started}) → SKIP"
                        )
                        return False
                else:
                    # PID 죽음 → stale lock 정리
                    logger.info(
                        f"[JobGuard] {self.job_name}: stale lock 정리 "
                        f"(pid={pid} 종료됨)"
                    )
                    self._lock_path.unlink(missing_ok=True)

        # lock 생성
        lock_data = {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
            "job_name": self.job_name,
        }
        try:
            with open(self._lock_path, "w", encoding="utf-8") as f:
                json.dump(lock_data, f, ensure_ascii=False)
            self.acquired = True
            logger.info(f"[JobGuard] {self.job_name}: lock 획득 (pid={os.getpid()})")
            return True
        except Exception as e:
            logger.error(f"[JobGuard] lock 파일 생성 실패: {e}")
            return False

    def release(self):
        """lock 해제"""
        if self._lock_path.exists():
            try:
                self._lock_path.unlink()
                logger.info(f"[JobGuard] {self.job_name}: lock 해제")
            except Exception as e:
                logger.warning(f"[JobGuard] lock 해제 실패: {e}")
        self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

    def _read_lock(self) -> Optional[Dict]:
        try:
            with open(self._lock_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _is_stale(self, started_at: str) -> bool:
        try:
            started = datetime.fromisoformat(started_at)
            return (datetime.now() - started).total_seconds() > LOCK_STALE_HOURS * 3600
        except Exception:
            return True  # 파싱 실패 → stale 판정


# ═══════════════════════════════════════════════
# 2단계: RetryManager (실패 매장 재시도)
# ═══════════════════════════════════════════════

class RetryManager:
    """실패 매장 추적 + 재시도 관리

    Usage:
        retry = RetryManager("daily_order")

        # 실행 결과 기록
        retry.mark_completed("46513")
        retry.mark_failed("46704", "ConnectionError")

        # 재시도 대상 조회
        failed = retry.get_retry_targets()  # ["46704", "47863"]

        # 재시도 가능 여부
        if retry.can_retry():
            retry.increment_attempt()
            run_for_stores(failed)

        # 최종 실패 시
        if not retry.can_retry():
            send_alert("3회 실패")
    """

    def __init__(self, job_name: str, date: str = None):
        self.job_name = job_name
        self.date = date or datetime.now().strftime("%Y-%m-%d")
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_path = _STATE_DIR / f"{job_name}_{self.date}.json"
        self._state = self._load_state()

    def _load_state(self) -> Dict:
        if self._state_path.exists():
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "job_name": self.job_name,
            "date": self.date,
            "attempt": 0,
            "started_at": None,
            "stores": {},
        }

    def _save_state(self):
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[RetryManager] 상태 저장 실패: {e}")

    def start_attempt(self):
        """새 시도 시작"""
        self._state["attempt"] += 1
        self._state["started_at"] = datetime.now().isoformat()
        self._save_state()
        logger.info(
            f"[RetryManager] {self.job_name}: 시도 {self._state['attempt']}/{MAX_RETRY_ATTEMPTS}"
        )

    def mark_completed(self, store_id: str):
        """매장 성공 기록"""
        self._state["stores"][store_id] = {
            "status": "completed",
            "finished_at": datetime.now().isoformat(),
        }
        self._save_state()

    def mark_failed(self, store_id: str, error: str = ""):
        """매장 실패 기록"""
        self._state["stores"][store_id] = {
            "status": "failed",
            "error": error[:200],
            "failed_at": datetime.now().isoformat(),
        }
        self._save_state()

    def get_retry_targets(self) -> List[str]:
        """재시도 대상 매장 목록 (failed + 미실행)"""
        from src.settings.store_context import StoreContext
        all_stores = [ctx.store_id for ctx in StoreContext.get_all_active()]
        completed = {
            sid for sid, info in self._state["stores"].items()
            if info.get("status") == "completed"
        }
        return [sid for sid in all_stores if sid not in completed]

    def can_retry(self) -> bool:
        """재시도 가능 여부"""
        return self._state["attempt"] < MAX_RETRY_ATTEMPTS

    @property
    def attempt(self) -> int:
        return self._state["attempt"]

    @property
    def all_completed(self) -> bool:
        """모든 매장 성공 여부"""
        return len(self.get_retry_targets()) == 0

    def get_summary(self) -> Dict:
        """상태 요약"""
        stores = self._state["stores"]
        return {
            "attempt": self._state["attempt"],
            "completed": sum(1 for s in stores.values() if s.get("status") == "completed"),
            "failed": sum(1 for s in stores.values() if s.get("status") == "failed"),
            "retry_targets": self.get_retry_targets(),
            "can_retry": self.can_retry(),
        }


# ═══════════════════════════════════════════════
# 3단계: PhaseCheckpoint (Phase 재개)
# ═══════════════════════════════════════════════

class PhaseCheckpoint:
    """매장별 Phase 완료 상태 추적

    Usage:
        cp = PhaseCheckpoint("daily_order", "46513")

        if cp.is_completed("collection"):
            skip collection
        else:
            run collection
            cp.mark_completed("collection")

        # 재시도 시 완료된 Phase 스킵
        resume_from = cp.get_resume_phase()
    """

    PHASE_ORDER = ["collection", "calibration", "preparation", "execution"]

    def __init__(self, job_name: str, store_id: str, date: str = None):
        self.job_name = job_name
        self.store_id = store_id
        self.date = date or datetime.now().strftime("%Y-%m-%d")
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_path = _STATE_DIR / f"{job_name}_{self.date}_{store_id}.json"
        self._state = self._load_state()

    def _load_state(self) -> Dict:
        if self._state_path.exists():
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"phases": {}, "store_id": self.store_id, "date": self.date}

    def _save_state(self):
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[PhaseCheckpoint] 저장 실패: {e}")

    def is_completed(self, phase_name: str) -> bool:
        """해당 Phase가 완료됐는지"""
        info = self._state["phases"].get(phase_name, {})
        return info.get("status") == "completed"

    def mark_completed(self, phase_name: str, duration: float = 0):
        """Phase 완료 기록"""
        self._state["phases"][phase_name] = {
            "status": "completed",
            "finished_at": datetime.now().isoformat(),
            "duration": round(duration, 1),
        }
        self._save_state()
        logger.debug(f"[PhaseCheckpoint] {self.store_id}/{phase_name}: completed")

    def mark_failed(self, phase_name: str, error: str = ""):
        """Phase 실패 기록"""
        self._state["phases"][phase_name] = {
            "status": "failed",
            "error": error[:200],
            "failed_at": datetime.now().isoformat(),
        }
        self._save_state()

    def get_resume_phase(self) -> Optional[str]:
        """재개할 Phase 이름 반환 (None이면 처음부터)"""
        for phase in self.PHASE_ORDER:
            if not self.is_completed(phase):
                return phase
        return None  # 모든 Phase 완료

    def reset(self):
        """체크포인트 초기화"""
        self._state["phases"] = {}
        self._save_state()
