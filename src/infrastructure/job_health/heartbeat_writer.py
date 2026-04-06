"""HeartbeatWriter — 스케줄러 생존 신호 파일 갱신 (daemon thread)."""

import threading
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

# data/runtime/ 아래 — DB 백업 스크립트(*.db 대상)와 충돌 회피
HEARTBEAT_PATH = Path(__file__).resolve().parents[3] / "data" / "runtime" / "scheduler_heartbeat.txt"


class HeartbeatWriter(threading.Thread):
    """매 interval_sec마다 heartbeat 파일에 현재 시각 기록.

    Watchdog이 mtime 기준으로 스케줄러 생존 여부 판단.
    파일 내용에도 ISO 타임스탬프 기록 (디버깅용).
    """

    def __init__(self, interval_sec: int = 60):
        super().__init__(name="HeartbeatWriter", daemon=True)
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()

    def run(self) -> None:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[Heartbeat] 시작: {HEARTBEAT_PATH} (interval={self.interval_sec}s)")
        while not self._stop_event.is_set():
            try:
                HEARTBEAT_PATH.write_text(
                    datetime.now().isoformat(), encoding="utf-8"
                )
            except Exception as e:
                logger.warning(f"[Heartbeat] 쓰기 실패: {e}")
            self._stop_event.wait(self.interval_sec)

    def stop(self) -> None:
        self._stop_event.set()
