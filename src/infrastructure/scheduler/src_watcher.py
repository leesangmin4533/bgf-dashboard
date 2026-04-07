"""Scheduler 자동 리로드를 위한 src 변경 감지 데몬.

(scheduler-auto-reload, 2026-04-07)

매 60초마다 src/, config/, run_scheduler.py의 .py 파일 트리에서
(max_mtime, total_size) 시그니처를 계산. 변경 감지 시 reload_event를 set하면
run_scheduler.py 본체 루프가 sys.exit(0)로 종료 → 외부 wrapper script가 재시작.
"""

import threading
import time
from pathlib import Path
from typing import Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # bgf_auto/
WATCH_PATHS = [
    _PROJECT_ROOT / "src",
    _PROJECT_ROOT / "config",
    _PROJECT_ROOT / "run_scheduler.py",
]
_EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".git"}


def src_signature(watch_paths=None) -> Tuple[float, int]:
    """감시 대상 트리의 (max_mtime, total_size) 시그니처.

    mtime만 쓰면 OneDrive 동기화 시 갱신 누락 가능 → size 추가로 보강.
    hash는 매 분 1000+ 파일에 대해 너무 무거워서 제외.
    """
    paths = watch_paths if watch_paths is not None else WATCH_PATHS
    max_mtime = 0.0
    total_size = 0
    for root in paths:
        if not root.exists():
            continue
        targets = root.rglob("*.py") if root.is_dir() else [root]
        for path in targets:
            if any(p in _EXCLUDE_PARTS for p in path.parts):
                continue
            try:
                st = path.stat()
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
                total_size += st.st_size
            except OSError:
                continue
    return (max_mtime, total_size)


class SrcWatcher(threading.Thread):
    """src/ 변경 감지 데몬 스레드.

    변경 감지 시 reload_event.set() 호출. 본체 루프가 매 분 event를 확인해
    sys.exit(0)로 graceful exit → wrapper script가 즉시 재시작.

    Args:
        reload_event: 본체 루프와 공유하는 threading.Event
        interval_sec: 폴링 간격 (기본 60초)
        watch_paths: 감시 경로 (테스트용 override, 기본 WATCH_PATHS)
    """

    def __init__(
        self,
        reload_event: threading.Event,
        interval_sec: int = 60,
        watch_paths=None,
    ):
        super().__init__(name="SrcWatcher", daemon=True)
        self.reload_event = reload_event
        self.interval_sec = interval_sec
        self.watch_paths = watch_paths
        self._initial_signature = src_signature(watch_paths)
        logger.info(
            f"[SrcWatcher] 시작 — interval={interval_sec}s, "
            f"initial_mtime={self._initial_signature[0]:.0f}, "
            f"initial_size={self._initial_signature[1]}"
        )

    def run(self) -> None:
        while not self.reload_event.is_set():
            time.sleep(self.interval_sec)
            try:
                current = src_signature(self.watch_paths)
                if current != self._initial_signature:
                    logger.warning(
                        f"[SrcWatcher] src 변경 감지 — "
                        f"mtime {self._initial_signature[0]:.0f} → {current[0]:.0f}, "
                        f"size {self._initial_signature[1]} → {current[1]}. "
                        f"reload event 발생"
                    )
                    self.reload_event.set()
                    return
            except Exception as e:
                logger.debug(f"[SrcWatcher] 시그니처 확인 실패 (무시): {e}")
