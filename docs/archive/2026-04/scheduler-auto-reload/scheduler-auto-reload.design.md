# Design: Scheduler 자동 리로드 (scheduler-auto-reload)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/scheduler-auto-reload.plan.md

---

## 1. 컴포넌트

```
[scripts/run_scheduler_loop.ps1]  ← 외부 wrapper (무한 재시작 + backoff)
       │
       ▼
[run_scheduler.py main]
       │
       ├─ schedule loop (기존)
       ├─ heartbeat thread (기존)
       └─ ★ SrcWatcher daemon thread (신규)
              │
              └─ 60초마다 src_signature() 비교
                  │
                  └─ 변경 감지 → _reload_event.set()
                      │
                      └─ schedule loop이 매 분 _reload_event 체크
                          → set 상태이고 진행 중 작업 없으면 sys.exit(0)
```

---

## 2. Design 결정

### 결정 1: 감시 대상 = 특정 디렉토리만
- `src/`, `run_scheduler.py`, `config/` (전체)
- 제외: `data/`, `logs/`, `__pycache__/`, `.pyc`, `tests/`
- 이유: data/logs는 매 분 변경됨 → false trigger, tests는 운영 영향 없음

### 결정 2: 변경 감지 방식 = max mtime + size hash
```python
def src_signature() -> tuple[float, int]:
    """감시 대상 트리의 (max_mtime, total_size) — 빠르고 단순"""
    max_mtime = 0.0
    total_size = 0
    for root in WATCH_PATHS:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                st = path.stat()
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
                total_size += st.st_size
            except OSError:
                continue
    return (max_mtime, total_size)
```
- mtime만 쓰면 OneDrive 동기화 시 갱신 안 될 수도 → size 추가로 보강
- hash 안 씀 (1000+ 파일 hash는 매분 너무 무거움)

### 결정 3: graceful exit 조건
- `_reload_event.is_set()` AND `not _job_in_progress.is_set()`
- `_job_in_progress`: 각 wrapper 함수가 시작/종료에서 set/clear (decorator로 통일)
- 작업 중이면 wait → 작업 종료 후 1초 내 exit
- 최대 대기: 30분 (안전망)

### 결정 4: exit code 의미
- `sys.exit(0)`: 정상 reload (wrapper가 즉시 재시작)
- `sys.exit(1)`: 에러 (wrapper가 backoff로 재시작)
- `sys.exit(2)`: 의도된 정지 (wrapper가 재시작 안 함, 운영자 수동 명령용)

### 결정 5: wrapper script — PowerShell + backoff
```powershell
# scripts/run_scheduler_loop.ps1
$backoff = 5
while ($true) {
    $start = Get-Date
    python run_scheduler.py
    $code = $LASTEXITCODE
    $elapsed = (Get-Date) - $start

    if ($code -eq 2) {
        Write-Host "[wrapper] exit=2 (정지 명령) — 종료"
        break
    }
    if ($code -eq 0) {
        Write-Host "[wrapper] exit=0 (auto-reload) — 즉시 재시작"
        $backoff = 5  # reset
    } else {
        Write-Host "[wrapper] exit=$code (오류) — ${backoff}초 후 재시작"
        Start-Sleep -Seconds $backoff
        if ($elapsed.TotalSeconds -lt 30) {
            $backoff = [Math]::Min($backoff * 2, 60)  # 5→10→20→40→60
        } else {
            $backoff = 5  # 충분히 오래 살았으면 reset
        }
    }
}
```

### 결정 6: wrapper API 최소화 — _job_in_progress는 watcher가 자율 판단
- 이상적으로 wrapper마다 set/clear가 좋지만, 현재 wrapper 30+개 수정은 과함
- **단순화**: schedule.next_run()까지 시간을 보고 "지금이 작업 직전 5분 이내가 아니면" exit 가능
- 또는 watcher가 reload event 발생 후 **다음 한산 시점**(예: 분당 작업 직후)에 exit
- → **MVP**: graceful 없이 즉시 sys.exit(0). 작업 중이라면 어차피 다음 cycle에서 새 프로세스가 catch up 가능 (대부분 작업이 멱등)
- 작업 중 종료 위험은 **운영 모니터링**으로 보완 (alert)

→ **MVP 채택**: 작업 progress lock 없이 reload 즉시 exit. 단순성 우선.

---

## 3. SrcWatcher 모듈

### 파일: `src/infrastructure/scheduler/src_watcher.py` (신규)

```python
"""Scheduler 자동 리로드를 위한 src 변경 감지 데몬."""

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
_EXCLUDE_PARTS = {"__pycache__", ".pytest_cache"}


def src_signature() -> Tuple[float, int]:
    """감시 대상 트리의 (max_mtime, total_size) 시그니처"""
    max_mtime = 0.0
    total_size = 0
    for root in WATCH_PATHS:
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

    변경 감지 시 reload_event.set() 호출. 본체 루프가 sys.exit(0).
    """

    def __init__(self, reload_event: threading.Event, interval_sec: int = 60):
        super().__init__(name="SrcWatcher", daemon=True)
        self.reload_event = reload_event
        self.interval_sec = interval_sec
        self._initial_signature = src_signature()
        logger.info(
            f"[SrcWatcher] 시작 — interval={interval_sec}s, "
            f"initial_mtime={self._initial_signature[0]:.0f}, "
            f"initial_size={self._initial_signature[1]}"
        )

    def run(self) -> None:
        while not self.reload_event.is_set():
            time.sleep(self.interval_sec)
            try:
                current = src_signature()
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
```

### `run_scheduler.py` 패치
```python
# main 진입점 안에서 (무한 루프 직전)
import threading
from src.infrastructure.scheduler.src_watcher import SrcWatcher

_reload_event = threading.Event()
SrcWatcher(_reload_event).start()

# 무한 루프 (기존)
while True:
    schedule.run_pending()
    time.sleep(60)
    if _reload_event.is_set():
        logger.warning("[Scheduler] auto-reload 트리거 — graceful exit (code=0)")
        sys.exit(0)
```

---

## 4. 회귀 테스트

`tests/test_src_watcher.py` (신규)

```python
class TestSrcWatcher:
    def test_signature_changes_on_file_modification(self, tmp_path):
        """파일 mtime 변경 시 시그니처가 달라진다"""
        # ... tmp_path/src/foo.py 만들고 patch WATCH_PATHS
        sig1 = src_signature()
        time.sleep(1.1)
        (tmp_path / "src/foo.py").write_text("new")
        sig2 = src_signature()
        assert sig1 != sig2

    def test_signature_stable_when_unchanged(self, tmp_path):
        """변경 없으면 시그니처 동일"""
        sig1 = src_signature()
        sig2 = src_signature()
        assert sig1 == sig2

    def test_watcher_sets_reload_event_on_change(self, tmp_path):
        """SrcWatcher가 변경 감지 시 event set"""
        event = threading.Event()
        watcher = SrcWatcher(event, interval_sec=1)
        watcher.start()
        time.sleep(0.5)
        # 파일 변경 시뮬레이션
        # ... patch _initial_signature and trigger
```

---

## 5. 구현 순서

| # | 작업 |
|---|---|
| 1 | `src/infrastructure/scheduler/__init__.py` + `src_watcher.py` 작성 |
| 2 | `run_scheduler.py` main에 SrcWatcher 시작 + reload check |
| 3 | `scripts/run_scheduler_loop.ps1` 작성 (Windows wrapper) |
| 4 | `tests/test_src_watcher.py` 회귀 테스트 |
| 5 | pytest 통과 |
| 6 | 운영 가이드 CLAUDE.md 1줄 추가 |
| 7 | 이슈체인 [WATCHING] + 시도 1 |
| 8 | 커밋 + 푸시 |

---

## 6. 성공 기준
1. `src_signature()` 함수가 mtime+size 반환
2. SrcWatcher가 60초 간격 폴링 + 변경 시 event set
3. run_scheduler.py main 루프가 event 감지 시 sys.exit(0)
4. wrapper script가 exit 0이면 즉시 재시작, 비정상 exit이면 backoff
5. 회귀 테스트 통과
6. CLAUDE.md에 운영 절차 추가

## 7. 다음 단계
`/pdca do scheduler-auto-reload`
