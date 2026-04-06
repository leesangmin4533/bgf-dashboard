# Completion Report — job-health-monitor

**Feature**: job-health-monitor
**Status**: Completed ✅
**Date**: 2026-04-07
**Match Rate**: **99.5%** (Gap 수정 후)
**Commits**: e00e5cc (initial) + 후속 Gap 수정

---

## 1. 배경

2026-04-07 07:00 `daily_order` 스케줄이 `collection.py` SyntaxError로 4개 매장 모두 즉시 실패했으나, 결과 메트릭 기반 `ops_issue_detector`는 감지 못 했다. Import 시점에 죽어서 기존 자전 5고리 전체가 돌지 못한 케이스. 사용자가 "왜 07시 스케줄 안 돈 거야?"라고 질문해서야 발견.

**교훈**: "자동화가 돌고 있다고 믿는 것"과 "돌고 있다는 것을 시스템이 확인해주는 것"은 다르다.

## 2. 목표 및 달성 결과

| 목표 | 달성 |
|---|:---:|
| 모든 잡 실행을 `job_runs` 테이블에 기록 | ✅ |
| 예정 시각 + grace 내 success 없으면 알림 | ✅ (5분 주기 JobHealthChecker) |
| 잡 실패 시 즉시 카카오 + fallback 알림 | ✅ (JobRunTracker `__exit__`) |
| 스케줄러 본체 사망 시 외부 프로세스 감지 | ✅ (watchdog.py + heartbeat) |
| 웹 대시보드 타임라인 (별 feature로 분리) | 🔀 job-health-dashboard로 분리 |

## 3. 구현 산출물

### 신규 파일 (10개)
```
src/infrastructure/job_health/
  ├── __init__.py
  ├── job_run_tracker.py           # Context manager + wrappers + compute_scheduled_for
  └── heartbeat_writer.py          # Daemon thread

src/infrastructure/database/repos/
  └── job_run_repo.py              # BaseRepository 상속, db_type="common"

src/application/services/
  ├── notification_dispatcher.py   # 1차 Kakao + 2차 fallback (log/beep/stderr)
  └── job_health_checker.py        # 5분 주기 missed 감지 + dispatch + purge

scripts/
  └── watchdog.py                  # 외부 프로세스 (Windows Task Scheduler)

tests/
  └── test_job_health_monitor.py   # 19 tests

docs/runbook/
  └── watchdog_setup.md            # schtasks 등록 가이드
```

### 수정 파일 (5개)
- `src/settings/constants.py` — 상수 8개 + `DB_SCHEMA_VERSION = 74`
- `src/db/models.py` — v74 마이그레이션
- `src/infrastructure/database/schema.py` — COMMON_SCHEMA job_runs
- `run_scheduler.py` — `_run_task` 통합 + heartbeat/checker 기동 + monthly_store_analysis Path B
- `src/application/services/daily_chain_report.py` — job_runs 24h 요약 섹션

## 4. 아키텍처 요약

```
In-Process:
  JobRunTracker (context manager, MultiStoreRunner 경유 자동 래핑)
    → job_runs INSERT/UPDATE
    → 실패 시 NotificationDispatcher 즉시 호출
  HeartbeatWriter (60s daemon)
    → data/runtime/scheduler_heartbeat.txt
  JobHealthChecker (5min daemon)
    → missed 감지 / failed 재알림 / 30일 purge

Out-of-Process (Windows Task Scheduler 5min):
  scripts/watchdog.py
    → heartbeat mtime > 10분 → Scheduler Dead 알림
    → job_runs failed/missed unalerted → 이중확인

NotificationDispatcher (1차+2차):
  1차: Kakao
  2차 (fallback): logs/job_health_alerts.log + winsound.Beep + stderr ANSI
```

## 5. 핵심 설계 결정

| 결정 | 이유 |
|---|---|
| **피처 플래그** `JOB_HEALTH_TRACKER_ENABLED` | Tracker 자체 버그가 이번 장애 유형을 재현하지 못하게 (OFF 시 **원본 함수 투명 호출**) |
| **2차 Fallback 채널 인스코프** | 1차가 Kakao 단일이면 "조용한 장애 감지"라는 본래 가치와 자가모순 |
| **2계층 감시** (in-process + watchdog) | 스케줄러 본체가 죽어서 in-process 감지기도 함께 죽는 케이스 커버 |
| **호출 지점 래핑** (MultiStoreRunner 내부 아님) | 파급 최소화, 롤백 용이 |
| **SQLite 부분 UNIQUE 인덱스** | missed 중복 레이스 제거, SELECT-then-INSERT 대비 스레드 안전 |
| **`compute_scheduled_for` 자체 구현** | `schedule` 라이브러리 내부 상태 비의존 → 테스트 용이 |
| **`data/runtime/` 경로** | DB 백업 스크립트(`*.db`)와 heartbeat 파일 충돌 회피 |

## 6. 검증 결과

### 유닛 테스트: **19/19 PASSED** (4.56s)

| 범주 | 테스트 수 |
|---|:---:|
| `compute_scheduled_for` | 4 |
| `JobRunRepository` | 5 |
| `JobRunTracker` | 4 (success/failure/flag on/off) |
| `NotificationDispatcher` | 3 (Kakao 성공/예외/False) |
| `JobHealthChecker` | 3 (missed dedup / success skip / **크로스 레이어 dispatch dedup**) |

### 런타임 Smoke Test
- ✅ common.db 마이그레이션 v73 → v74
- ✅ 부분 UNIQUE 인덱스로 missed 중복 차단 (DB 단에서 증명)
- ✅ Tracker 래핑 → 실제 기록 생성
- ✅ 피처 플래그 OFF 시 원본 함수 반환 (동일 객체 확인)

### Gap 분석: **99.5%** (Gap 수정 후)

| 섹션 | 완성도 |
|---|:---:|
| §2 Parameters (8) | 100% |
| §3 DB Schema v74 | 100% |
| §4 Components 5개 | 100% |
| §5 Integration (Path A/B/startup) | 100% (Path B 추가 후) |
| §6 12-step | 100% (step 11 daily_chain_report 추가 후) |
| §7 Tests | 100% |
| §8 Rollback | 100% |

### Positive Additions (Impl > Design)
- Tracker `__exit__` 즉시 알림 + `alerted=1` 마킹 → 크로스 레이어 dedup 테스트로 검증
- `JobHealthChecker._check_missed` 24h stale cap → missed 누적 방지
- `logs/job_health_alerts.log` 전용 파일 → 기존 alerts.log 오염 방지

## 7. 롤백 절차

| 시나리오 | 조치 | 시간 |
|---|---|:---:|
| Tracker 버그로 스케줄러 느려짐 | `constants.py JOB_HEALTH_TRACKER_ENABLED=False` | 10초 |
| DB 테이블 이상 | `DROP TABLE job_runs; UPDATE schema_version SET version=73;` | 30초 |
| False positive 스팸 | `JOB_HEALTH_CHECKER_INTERVAL_SEC=3600` | 10초 |
| Watchdog 폭주 | `schtasks /Change /TN "BGF_Watchdog" /DISABLE` | 5초 |

## 8. 운영 체크리스트 (사용자 수동 작업)

- [ ] Windows Task Scheduler에 watchdog 등록 (`docs/runbook/watchdog_setup.md` 참조)
- [ ] `run_scheduler.py` 재기동 → 다음 세션부터 heartbeat + health checker daemon 자동 시작
- [ ] 첫 24시간 관찰: false positive 0건 확인
- [ ] 2주 회귀 관찰 (CLAUDE.md "2주 연속" 패턴)

## 9. 교훈

1. **SyntaxError 유형 장애는 결과 메트릭 감지로 못 잡는다** — Tracker 본체가 돌지 못하면 감지기가 같이 죽는다. 외부 watchdog 필수.
2. **알림 경로 이중화는 선택이 아닌 필수** — 본 feature의 가치가 "조용한 장애 감지"인데 알림이 단일 채널이면 자가모순.
3. **피처 플래그는 방어 장치지 토글이 아니다** — 이 feature가 오히려 장애 원인이 되는 시나리오를 상정하고 설계해야 한다.
4. **PDCA + design-validator 2회 검증의 효과** — Plan 단계 1회, Design 단계 1회 검증을 거친 덕에 스키마 버전 오인(v75 vs v73), MultiStoreRunner 시그니처 오인, multi_store=False 잡 누락, SQLite partial UNIQUE 오인 등 4건의 major issue를 Do 진입 전에 잡았다.

## 10. 참조

- Plan: `docs/01-plan/features/job-health-monitor.plan.md`
- Design: `docs/02-design/features/job-health-monitor.design.md`
- Analysis: `docs/03-analysis/job-health-monitor.analysis.md`
- 촉발 장애: `docs/archive/2026-04/collection-indent-fix/collection-indent-fix.report.md`
- 런북: `docs/runbook/watchdog_setup.md`

## 11. 후속 작업

- **job-health-dashboard** (별 feature) — `/api/job-health` + 타임라인 뷰 (M5에서 분리)
- **pre-commit syntax guard** — `python -m compileall src/` or ruff (SyntaxError 원천 차단, 이번 장애 근본 예방)
