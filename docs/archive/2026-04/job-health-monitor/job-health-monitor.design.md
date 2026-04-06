# Design — job-health-monitor

**Feature**: job-health-monitor
**Plan**: `docs/01-plan/features/job-health-monitor.plan.md`
**Created**: 2026-04-07
**Status**: Design

---

## 1. 아키텍처 개요

```
┌──────────────────────── In-Process (scheduler) ────────────────────────┐
│                                                                        │
│  run_scheduler.py ──▶ schedule.every().at(...).do(run_job)             │
│         │                                                              │
│         │  @track_job_run  (피처 플래그 체크)                           │
│         ▼                                                              │
│  MultiStoreRunner.run_parallel()                                       │
│         │ ┌──────────────────────────┐                                 │
│         │ │ JobRunTracker            │ ─▶ job_runs 테이블              │
│         │ │  enter/exit context      │    (common.db, v74)             │
│         │ └──────────────────────────┘                                 │
│         │        │                                                     │
│         │        ▼  (on failed)                                        │
│         │   NotificationDispatcher                                     │
│         │   ├─ 1차: KakaoNotifier                                      │
│         │   └─ 2차 fallback: alerts.log + winsound.Beep + stderr       │
│         │                                                              │
│  HeartbeatWriter (60s thread) ─▶ data/runtime/scheduler_heartbeat.txt  │
│                                                                        │
│  JobHealthChecker (5min thread) ─▶ detect "missed" / "stale running"  │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─────────────────── Out-of-Process (Windows Task Scheduler) ───────────┐
│                                                                       │
│  scripts/watchdog.py  (매 5분 실행)                                    │
│    ├─ heartbeat mtime > 10분 ─▶ "Scheduler Dead" 알림                  │
│    └─ job_runs 조회: 최근 1시간 failed > 0 ─▶ 이중확인 알림            │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

## 2. 파라미터 (통합)

| 이름 | 값 | 근거 |
|---|---|---|
| `JOB_HEALTH_TRACKER_ENABLED` | `True` | constants.py, 긴급 비활성화용 |
| `JOB_HEALTH_GRACE_PERIOD_MIN` | 10분 | daily_order 최장 20분 소요(49965 908s) 감안, 정각+10분이면 시작 확인 가능 |
| `JOB_HEALTH_CHECKER_INTERVAL_SEC` | 300 (5분) | 정각 정밀도는 불필요, 5분이면 최장 5분 지연 알림 |
| `JOB_HEALTH_ALERT_COOLDOWN_HOUR` | 1시간 | 같은 잡 반복 알림 방지 |
| `JOB_RUNS_RETENTION_DAYS` | 30일 | 비대화 방지, 장기는 별도 분석 |
| `HEARTBEAT_INTERVAL_SEC` | 60 | Watchdog 5분 주기 대비 여유 |
| `HEARTBEAT_STALE_THRESHOLD_SEC` | 600 (10분) | Watchdog 감지 임계 |
| `WATCHDOG_INTERVAL_MIN` | 5 | Windows Task Scheduler 최소 단위와 정합 |

## 3. DB 스키마 (v74)

> 현재 코드 기준: `constants.py DB_SCHEMA_VERSION = 73`, `models.py SCHEMA_MIGRATIONS` 최고 키 = 73. 메모리의 "v74" 기록은 stale. **이 feature는 v74로 추가**.

```sql
-- common.db
CREATE TABLE IF NOT EXISTS job_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name        TEXT NOT NULL,           -- SCHEDULED_JOBS.name
    store_id        TEXT,                    -- NULL 허용 (multi_store=False 잡)
    scheduled_for   TEXT NOT NULL,           -- 예정 시각 ISO8601 (missed 판정용)
    started_at      TEXT NOT NULL,           -- 실제 시작
    ended_at        TEXT,                    -- NULL = running
    status          TEXT NOT NULL,           -- running/success/failed/timeout/missed
    error_message   TEXT,
    error_type      TEXT,                    -- Exception class name (SyntaxError 등)
    duration_sec    REAL,
    alerted         INTEGER DEFAULT 0,       -- 알림 발송 여부 (중복 방지)
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_runs_name_time
    ON job_runs(job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_runs_status_time
    ON job_runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_runs_scheduled
    ON job_runs(scheduled_for, job_name);
```

### 마이그레이션
- `src/db/models.py` `SCHEMA_MIGRATIONS[75] = "CREATE TABLE job_runs ... "` 추가
- `src/settings/constants.py` `DB_SCHEMA_VERSION = 75`
- 공통 DB이므로 `src/infrastructure/database/schema.py` COMMON_SCHEMA에 추가

### 중복 방지 규칙
- **SQLite 부분 UNIQUE 인덱스** (지원됨):
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS idx_job_runs_missed_unique
      ON job_runs(job_name, store_id, scheduled_for)
      WHERE status = 'missed';
  ```
- 멀티스레드 환경에서 SELECT-then-INSERT 레이스 제거. INSERT 충돌 시 `ON CONFLICT DO NOTHING` 또는 IntegrityError 무시.
- `store_id IS NULL` 행은 COALESCE로 대체 키 생성 필요 시 검토 (monthly_store_analysis 대비).

## 4. 컴포넌트 명세

### 4.1 `src/infrastructure/job_health/job_run_tracker.py` (신규)
```python
class JobRunTracker:
    """스케줄 잡 실행 기록.

    용법:
        with JobRunTracker(job_name="daily_order", store_id="46513",
                           scheduled_for="2026-04-07T07:00:00") as tracker:
            # 작업 수행
            tracker.set_success()
        # with 탈출 시 status 자동 결정 (예외 있으면 failed, 없으면 success)
    """
    def __enter__(self) -> "JobRunTracker"
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool  # False 반환(예외 전파)
    def set_status(self, status: str, error: Optional[str] = None)

# StoreContext 래퍼 (MultiStoreRunner용)
def wrap_multistore_task(job_name: str, task_fn, scheduled_for: str):
    """MultiStoreRunner.run_parallel에 넘길 task_fn을 Tracker로 감싼다.

    MultiStoreRunner는 task_fn(ctx: StoreContext) 시그니처를 사용하므로
    ctx.store_id를 Tracker에 전달한다.
    """
    from src.settings.constants import JOB_HEALTH_TRACKER_ENABLED
    if not JOB_HEALTH_TRACKER_ENABLED:
        return task_fn  # 피처 플래그 OFF → 원본 투명 호출

    def wrapped(ctx):
        with JobRunTracker(job_name, ctx.store_id, scheduled_for):
            return task_fn(ctx)
    return wrapped

# 단일 실행 데코레이터 (non-multi_store 잡용)
def track_single_job(job_name: str, scheduled_for_getter: Callable[[], str]):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from src.settings.constants import JOB_HEALTH_TRACKER_ENABLED
            if not JOB_HEALTH_TRACKER_ENABLED:
                return func(*args, **kwargs)
            with JobRunTracker(job_name, store_id=None, scheduled_for=scheduled_for_getter()):
                return func(*args, **kwargs)
        return wrapper
    return decorator
```

**핵심**: constants.py에서 상수 **직접 import** (helper 함수 의존 없음).

**핵심 설계**:
- **피처 플래그 우회**: 플래그 OFF 시 **원본 함수를 투명 호출** — Tracker 자체 버그가 스케줄러를 내리지 못함
- **예외 전파**: `__exit__`가 False 반환 → 상위 로직 영향 없음
- **기록 실패는 silent**: Tracker가 DB write 실패해도 원본 잡은 계속 진행 (try/except + logger.warning)

### 4.2 `src/application/services/notification_dispatcher.py` (신규)
```python
class NotificationDispatcher:
    """1차 Kakao + 2차 fallback 통합 알림.

    fallback 조건: Kakao send가 False 반환 또는 예외
    """
    def send(self, subject: str, body: str, level: str = "WARNING") -> bool:
        success = False
        try:
            success = self._kakao.send(f"[{subject}] {body}")
        except Exception as e:
            logger.warning(f"[Dispatcher] Kakao 실패: {e}")
        if not success:
            self._fallback(subject, body, level)
        return success

    def _fallback(self, subject: str, body: str, level: str):
        # 1. 전용 로그 파일
        _ALERTS_LOG.warning(f"[{subject}] {body}")
        # 2. 콘솔 beep (Windows만)
        try:
            import winsound
            winsound.Beep(1000, 500)
        except Exception:
            pass
        # 3. stderr 빨간 프린트 (ANSI)
        print(f"\033[91m[ALERT/{subject}] {body}\033[0m", file=sys.stderr)
```

### 4.3 `src/application/services/job_health_checker.py` (신규)
```python
class JobHealthChecker:
    """예정 시각 + grace 내 success 기록 존재 여부 확인."""

    def check_missed_jobs(self, now: datetime) -> List[JobAnomaly]:
        """각 SCHEDULED_JOBS 순회:
        1. 현재 시각이 schedule + grace 를 지났는가?
        2. 그렇다면 scheduled_for 일치하는 success 기록이 있는가?
        3. 없으면 missed 이벤트 생성 (cooldown 체크)
        """

    def check_stale_running(self, now: datetime) -> List[JobAnomaly]:
        """status='running' 이 max_duration*2 초과 → timeout"""
```

**실행 주기**: 5분마다 (`HEARTBEAT_INTERVAL_SEC` × 5 여유) — Plan의 "정각" vs "정각+5분" 불일치는 **5분 주기로 통일**.

### 4.4 `src/infrastructure/job_health/heartbeat_writer.py` (신규)
```python
class HeartbeatWriter(threading.Thread):
    """데몬 스레드, 60초마다 파일 touch."""
    def run(self):
        while not self._stop.is_set():
            _HEARTBEAT_FILE.write_text(datetime.now().isoformat())
            self._stop.wait(HEARTBEAT_INTERVAL_SEC)
```

**경로**: `data/runtime/scheduler_heartbeat.txt` (DB 백업 스크립트 영향 회피)
**생성**: `data/runtime/` 디렉토리는 schema init 시 mkdir(exist_ok=True)

### 4.5 `scripts/watchdog.py` (신규, 외부 프로세스)
```python
"""Windows Task Scheduler에 등록 — 5분마다 실행.

1. heartbeat.txt mtime 확인 → 10분 초과면 "Scheduler Dead" 알림
2. common.db.job_runs 조회:
   - 최근 1시간 failed > 0 (alerted=0) → 이중확인 알림 + alerted=1
   - missed > 0 → 동일
3. NotificationDispatcher 재사용 (import scripts와 src 모두 접근)
"""
```

**등록 가이드**: `docs/runbook/watchdog_setup.md` (신규) — schtasks 명령 포함.

## 5. 통합 지점 (Integration Points)

### 5.1 통합 지점 (2경로)

**현재 MultiStoreRunner 실제 시그니처** (`job_scheduler.py:44`):
```python
def run_parallel(self, task_fn: Callable, task_name: str) -> List[Dict]:
    # StoreContext.get_all_active() 로 순회
    # task_fn(ctx: StoreContext) 호출
```

**전략**: MultiStoreRunner 내부는 건드리지 않고, **호출 지점에서 task_fn을 래핑**. 파급 최소화 + 롤백 용이.

#### 경로 A: multi_store=True 잡 (11개 JobDefinition)
```python
# run_scheduler.py 또는 job dispatch 지점
from src.infrastructure.job_health.job_run_tracker import wrap_multistore_task

def dispatch_job(job_def: JobDefinition):
    flow_cls = _import_flow(job_def.flow_class)
    def raw_task(ctx):
        return flow_cls(store_id=ctx.store_id).run()

    scheduled_for = _compute_scheduled_for(job_def)  # ISO8601 직전 정각
    wrapped = wrap_multistore_task(job_def.name, raw_task, scheduled_for)

    runner = MultiStoreRunner(...)
    runner.run_parallel(wrapped, task_name=job_def.name)
```

#### 경로 B: multi_store=False 잡 (현재 1개 — `monthly_store_analysis`)
`run_scheduler.py`의 해당 래퍼 함수에 `@track_single_job` 적용:
```python
@track_single_job("monthly_store_analysis",
                  lambda: _compute_scheduled_for_by_name("monthly_store_analysis"))
def _run_monthly_store_analysis():
    from src.application.services.analysis_service import run_store_analysis
    run_store_analysis()
```

#### `_compute_scheduled_for` 구현 확정
```python
def _compute_scheduled_for(job_def: JobDefinition) -> str:
    """job_def.schedule(s) 중 가장 최근 정각을 ISO8601로 반환.

    "07:00" / "Mon 08:00" / schedules list 모두 처리.
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    candidates = job_def.schedules or [job_def.schedule]
    best = None
    for s in candidates:
        # "Mon 08:00" or "08:00"
        parts = s.split()
        hhmm = parts[-1]
        h, m = map(int, hhmm.split(":"))
        fire = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if fire > now:
            fire -= timedelta(days=1)  # 아직 오늘 실행 안 됐으면 어제 값
        if len(parts) == 2:
            # 요일 필터 (Mon/Tue/...)
            weekday_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
            target = weekday_map[parts[0]]
            while fire.weekday() != target:
                fire -= timedelta(days=1)
        if best is None or fire > best:
            best = fire
    return best.isoformat()
```

**제약**: schedule 라이브러리 내부 상태에 의존하지 않고 `JobDefinition` 문자열만으로 계산 — 테스트 용이.

### 5.2 `run_scheduler.py` 진입
```python
# 스케줄러 시작 직후
from src.infrastructure.job_health.heartbeat_writer import HeartbeatWriter
from src.application.services.job_health_checker import JobHealthChecker

heartbeat = HeartbeatWriter(daemon=True); heartbeat.start()

def _health_check_loop():
    while True:
        try:
            JobHealthChecker().run_all()
        except Exception as e:
            logger.warning(f"[HealthChecker] 실행 실패: {e}")
        time.sleep(JOB_HEALTH_CHECKER_INTERVAL_SEC)

threading.Thread(target=_health_check_loop, daemon=True).start()
```

### 5.3 기존 자전 5고리와의 관계
- **ops_issue_detector** (23:55): 기존 그대로. 이 plan은 **실시간 감지**를 추가할 뿐 대체 아님
- **pending_issues.json**: JobHealthChecker가 감지한 anomaly도 여기에 병합
- **daily_chain_report**: 00:02 리포트에 job_runs 요약 섹션 추가

## 6. 구현 순서

| 순서 | 항목 | 파일 | 비고 |
|---|---|---|---|
| 1 | constants.py 파라미터 8개 추가 | `src/settings/constants.py` | 피처 플래그 포함 |
| 2 | DB 스키마 v74 | `models.py`, `constants.py`, `schema.py` | 마이그레이션 동기화 |
| 3 | job_runs Repository | `src/infrastructure/database/repos/job_run_repo.py` | BaseRepository 상속, db_type="common" |
| 4 | JobRunTracker 컨텍스트/데코레이터 | `src/infrastructure/job_health/job_run_tracker.py` | 피처 플래그 체크 |
| 5 | NotificationDispatcher | `src/application/services/notification_dispatcher.py` | Kakao + fallback |
| 6 | MultiStoreRunner 통합 | `src/application/scheduler/job_scheduler.py` | `@track_job_run` 래핑 |
| 7 | HeartbeatWriter | `src/infrastructure/job_health/heartbeat_writer.py` | daemon thread |
| 8 | JobHealthChecker | `src/application/services/job_health_checker.py` | 5분 loop |
| 9 | scripts/watchdog.py | `scripts/watchdog.py` | 독립 실행 가능 |
| 10 | run_scheduler.py 기동 로직 | `run_scheduler.py` | heartbeat + checker 스레드 시작 |
| 11 | daily_chain_report 섹션 추가 | `src/application/services/daily_chain_report.py` | job_runs 요약 |
| 12 | 런북 문서 | `docs/runbook/watchdog_setup.md` | schtasks 등록 가이드 |

## 7. 테스트 계획

### 유닛
- `test_job_run_tracker.py`: success/failed/예외 시 status 정확 기록, 피처 플래그 OFF 시 투명 호출
- `test_notification_dispatcher.py`: Kakao 실패 → fallback 3경로 모두 호출
- `test_job_health_checker.py`: grace 이내/이후 경계, cooldown, missed 중복 방지
- `test_job_run_repo.py`: retention 30일 퍼지, 인덱스 활용 쿼리

### 통합
- `test_scheduler_integration.py`: MultiStoreRunner + Tracker 연동, 실제 잡 실행 후 job_runs 검증
- **크로스 레이어**: JobRunTracker가 failed 기록 + JobHealthChecker가 같은 scheduled_for 재알림 안 하는지 (`alerted=1`)

### 시뮬레이션
- 일부러 `daily_order` 내부에 `raise RuntimeError("test")` → Kakao + fallback 3채널 도달 확인
- 스케줄러 프로세스 kill → watchdog 5분 내 "Scheduler Dead" 알림
- Kakao 토큰 파일 임시 손상 → fallback만으로 알림 도달
- `JOB_HEALTH_TRACKER_ENABLED=False` → 모든 잡 Tracker 우회, 정상 실행

### 회귀 (2주 관찰)
- false positive 0건
- job_runs 레코드 누락 0건 (SCHEDULED_JOBS 예정 vs 실제 실행 비교)

## 8. 롤백

| 시나리오 | 조치 |
|---|---|
| Tracker 버그로 스케줄러 느려짐 | `constants.py` `JOB_HEALTH_TRACKER_ENABLED=False` → 즉시 우회 |
| DB 테이블 이상 | 다운그레이드 SQL: `DROP TABLE job_runs; UPDATE schema_version SET version=73;` (v74 → v73) |
| False positive 스팸 | `JOB_HEALTH_CHECKER_INTERVAL_SEC`를 3600 (1시간) 으로 상향 |
| Watchdog 폭주 | Windows Task Scheduler에서 비활성화 (단일 스위치) |

## 9. 미해결 (Do 중 확인)

- [x] ~~SCHEDULED_JOBS 중 `multi_store=False` 잡 목록~~ → **1개 확인**: `monthly_store_analysis` (경로 B)
- [x] ~~`scheduled_for` 계산 방법~~ → **§5.1에 확정**: `JobDefinition.schedule(s)` 문자열 파싱
- [ ] watchdog.py의 src/ import — `sys.path.insert(0, str(Path(__file__).parent.parent))` 한 줄로 해결 예정

## 9a. Thread Inventory (명시)

| 스레드 | 기원 | 수명 | DB 접근 |
|---|---|---|---|
| 메인 | `run_scheduler.py` | 프로세스 전체 | X |
| ThreadPoolExecutor workers | `MultiStoreRunner` | 잡 단위 (최대 4) | O (JobRunRepo) |
| HeartbeatWriter daemon | `run_scheduler.py` 기동 시 1개 | 프로세스 전체 | X (파일만) |
| JobHealthChecker daemon | `run_scheduler.py` 기동 시 1개 | 프로세스 전체 | O (JobRunRepo) |
| Flask waitress workers | 별 프로세스(혹은 스레드) | 웹 서비스 | O (기존 repos) |

**DB 안전 규칙**:
- `JobRunRepo`는 모든 호출마다 `DBRouter.get_connection(table="job_runs")`로 fresh connection 획득 (WAL + busy_timeout=30s 기본 적용)
- 커서/커넥션 스레드 간 공유 금지
- `alerted` 업데이트는 `UPDATE ... WHERE id=? AND alerted=0`으로 원자적 처리 (double-alert 방지)

## 10. 참조

- Plan: `docs/01-plan/features/job-health-monitor.plan.md`
- 이번 장애: `docs/archive/2026-04/collection-indent-fix/collection-indent-fix.report.md`
- 기존 파이프라인: `src/application/scheduler/job_definitions.py` (12개 JobDefinition)
- MultiStoreRunner: `src/application/scheduler/job_scheduler.py:22`
- constants 패턴: `src/settings/constants.py`
- DB 마이그레이션 가이드: `CLAUDE.md` — DB_SCHEMA_VERSION 동기화 규칙
