# Plan — job-health-monitor

**Feature**: job-health-monitor
**Created**: 2026-04-07
**Author**: PDCA / collection-indent-fix 후속
**Status**: Plan
**Priority**: P1
**Related Issue**: `docs/05-issues/scheduling.md`

---

## 1. 배경 (Why)

2026-04-07 07:00 `daily_order` 스케줄이 `collection.py` SyntaxError로 4개 매장 모두 즉시 실패(0.1~1.5초). 수동으로 로그를 열어보기 전까지 **수 시간 동안 아무도 몰랐다**. 장애를 사용자가 "왜 07시 스케줄 안 돈 거야?"라고 물어서 발견했다.

기존 자전(self-healing) 5고리(`DataIntegrityService`, `ops_issue_detector`, `AutoExecutor` 등)는 **"결과 메트릭"**만 감시하고 **"스케줄 잡 자체의 성공/실패"**는 추적하지 않는다. 특히:

- `ops_issue_detector`는 예측오차·발주실패율·폐기율 등 *데이터 품질*만 체크
- Import/SyntaxError로 파이썬 프로세스가 죽으면 감지기 자체도 못 돎
- 스케줄러에 last_success_at / job_runs 테이블이 없어 "오늘 07시 성공 여부"를 알 방법 자체가 없음
- CloudSync 실패, Kakao 토큰 만료 같은 외부 의존성 실패도 조용히 지나감

**핵심 문제**: "자동화가 **돌고 있다고 믿는 것**"과 "**돌고 있다는 것을 시스템이 확인해주는 것**"의 차이.

## 2. 목표 (What)

매일 실행되어야 하는 스케줄 잡이 **예정 시각에 성공했는지**를 독립적으로 추적·알림한다.

### 성공 기준 (Definition of Done)
- [ ] 모든 `SCHEDULED_JOBS` 실행이 `job_runs` 테이블에 `started_at / ended_at / status / error` 기록된다
- [ ] `daily_order` 등 주요 잡이 **예정 시각 + grace period** 내 success 기록이 없으면 카카오 알림이 간다
- [ ] 잡이 실행되기는 했으나 실패로 종료(`status='failed'`)한 경우도 즉시 알림
- [ ] 외부 watchdog 프로세스가 **스케줄러 본체가 죽어도** 감지할 수 있다 (최소한 "last_heartbeat > N분" 감지)
- [ ] 웹 대시보드에 최근 24시간 잡 실행 타임라인 표시

### 비목표 (Out of Scope)
- 실패 시 **자동 재실행** (이번에는 알림만. 재실행은 Phase 2)
- SyntaxError 원천 차단 (별건: pre-commit hook / ruff — 다른 feature)
- 잡 실행 시간의 장기 추세 분석 (별건: ops_metrics 확장)

## 3. 범위 (Scope)

### 포함
1. **job_runs 테이블** (common.db) — 스케줄 잡 실행 로그
   - `(id, job_name, store_id, started_at, ended_at, status, error_message, duration_sec)`
   - `status`: `running / success / failed / timeout / missed`
2. **JobRunTracker** 래퍼 — `SCHEDULED_JOBS` 진입/종료 시 자동 기록 (Decorator 또는 context manager)
   - **피처 플래그**: `JOB_HEALTH_TRACKER_ENABLED`(constants.py, 기본 True) — Tracker 자체 버그 발생 시 즉시 비활성화 가능 (이 플랜이 예방하려는 장애 유형과 동일한 파급 방지)
3. **JobHealthChecker** — 각 잡의 "예정시각 + grace" 내 success 존재 여부 확인
4. **알림 경로 (1차)**: 기존 `KakaoNotifier` 재사용 + `pending_issues.json` 병합
5. **알림 경로 (2차, Fallback)**: Kakao 전송 실패 시 아래 채널 동시 사용
   - `logs/alerts.log` 전용 파일에 WARNING 레벨 append
   - 콘솔 beep (`winsound.Beep`) + stderr 빨간 프린트
   - 이유: 이 플랜의 본래 가치가 "조용한 장애 감지"인데 감지 결과가 Kakao 단일 채널에 의존하면 자가모순 (Kakao 토큰 만료 리스크 기지정)
6. **스케줄러 heartbeat 파일**: `data/runtime/scheduler_heartbeat.txt` (매 60초 touch)
7. **외부 watchdog 스크립트**: `scripts/watchdog.py` — heartbeat 파일 시각 확인, 10분 이상 지체 시 1차/2차 알림
8. **(이 plan 스코프 아님)** ~~웹 대시보드 `/api/job-health`~~ → 별 feature `job-health-dashboard`로 분리 (M1~M4 ship 후 후속)

### 제외
- 자동 재실행 / 자가 치유
- 잡별 SLA 대시보드(성공률, p95 등)
- 다중 노드 분산
- **웹 대시보드 / 타임라인 뷰** (별 feature로 분리)

## 4. 이해관계자 / 영향

| 항목 | 영향 |
|---|---|
| 사용자(운영자) | 아침에 발주 안 도는 사고를 **수 시간 내** 인지 가능 |
| 스케줄러 | `JobRunTracker` 1회 래핑으로 모든 잡 기록 자동화 |
| DB | common.db에 `job_runs` 테이블 추가 (v75) |
| 알림 | Kakao 수신량 증가 (예상 평시 0건 / 장애 시 1~5건) |
| 운영 부하 | watchdog 프로세스 1개 상시 실행 (Windows 작업 스케줄러) |

## 5. 접근 방향 (High-level)

### 2-계층 감시 구조
```
[계층 1: In-process] — JobRunTracker
  스케줄러 내부에서 모든 잡 진입/종료 기록
  장점: 정확한 duration, error 스택
  약점: 스케줄러 자체가 죽으면 기록 불가

[계층 2: Out-of-process] — Watchdog
  별도 프로세스가 heartbeat 파일 + job_runs 조회
  장점: 스케줄러 본체가 죽어도 감지
  약점: heartbeat 라이브니스만 확인
```

### 감지 유형 3가지
| 유형 | 감지 주체 | 예시 |
|---|---|---|
| **Job Failed** | JobRunTracker (즉시) | daily_order 내부 exception → 알림 즉시 |
| **Job Missed** | JobHealthChecker (정각) | 07:00 이후 07:30까지 daily_order 기록 0건 → 알림 |
| **Scheduler Dead** | Watchdog (10분 주기) | heartbeat.txt mtime > 10분 전 → 알림 |

### 재사용
- `pending_issues.json` (daily_chain_report에서 이미 사용)
- `KakaoNotifier` (알림)
- `issue_chain_writer` (이슈체인 자동 등록 — scheduling.md에 이번 장애도 시도 N번째로 기록)

## 6. 대안 검토 (Alternatives Considered)

| 대안 | 장점 | 단점 | 채택 |
|---|---|---|:---:|
| A. **현재안**: job_runs + watchdog | 2계층, 재사용 가능 | 테이블 하나 추가 | ✅ |
| B. 외부 모니터링(UptimeRobot, Healthchecks.io) | 구현 제로, 견고 | 네트워크 의존, 비용 | ❌ (네트워크 끊기면 무용지물) |
| C. Windows Event Log 기록 | OS 친화 | 크로스플랫폼 X, 알림 파이프라인 따로 | ❌ |
| D. 기존 `ops_issue_detector`에 job-level 체커만 추가 | 최소 변경 | 매일 23:55 1회만 → 실시간성 부족 | ❌ |
| E. 스케줄러에 예외 try/except + 알림만 추가 | 초간단 | Import 실패 못 잡음 (이번 케이스가 정확히 이 유형) | ❌ |

**D를 버린 이유**: 23:55에 "오늘 07:00 daily_order 실패했네" 알림 받으면 이미 하루 발주 놓친 뒤. 정각 직후 알림이 필수.

**E를 버린 이유**: 오늘 장애가 정확히 import 시점에 죽은 케이스. except로는 못 잡는다. 외부 감시가 필요.

## 7. 리스크 & 의존성

| 리스크 | 확률 | 영향 | 대응 |
|---|:---:|:---:|---|
| 알림 스팸 (false positive) | 중 | 중 | grace period 10분 + cooldown 1시간 |
| job_runs 테이블 비대화 | 낮 | 낮 | 30일 이상 자동 퍼지 |
| watchdog 자체가 죽음 | 낮 | 중 | Windows 작업 스케줄러가 재시작 정책 담당 |
| Kakao 토큰 만료 시 알림 경로 없음 | 중 | 높 | **M4에 2차 채널(logs/alerts.log + winsound.Beep + stderr) 인스코프** |
| JobRunTracker 자체 버그로 전 스케줄 마비 | 낮 | 매우높 | **피처 플래그 `JOB_HEALTH_TRACKER_ENABLED` 즉시 비활성화**, 오늘 장애와 동일 파급 방지 |

### 의존성
- `KakaoNotifier` 정상 작동 (4/6 KOE101 이슈와 별건)
- `DBRouter` common.db 경로
- Windows 작업 스케줄러 또는 cron (watchdog 실행)

## 8. 마일스톤

| 단계 | 산출물 |
|---|---|
| M1: DB + Tracker | `job_runs` 테이블(v75), JobRunTracker 데코레이터 + 피처 플래그, 기존 job_definitions.py 적용 |
| M2: Inline 알림 (1차+2차) | JobRunTracker 실패 시 즉시 Kakao, 실패 시 alerts.log + beep fallback, pending_issues 등록 |
| M3: Missed 감지 | JobHealthChecker (실행 주기는 Design에서 확정) |
| M4: Watchdog | heartbeat 파일 + scripts/watchdog.py + 2차 채널 연동 + Windows 작업 스케줄러 등록 가이드 |
| ~~M5: 대시보드~~ | **별 feature `job-health-dashboard`로 분리** (이 plan 범위 아님) |

## 9. 검증 계획 (테스트)

- [ ] **유닛**: JobRunTracker가 예외 상황에서 status=failed 기록
- [ ] **유닛**: JobHealthChecker가 grace 내 success 없으면 anomaly 반환
- [ ] **통합**: 일부러 `daily_order`에 raise Exception → 카카오 알림 수신 확인
- [ ] **시뮬레이션**: 스케줄러 프로세스 kill → watchdog가 10분 내 알림
- [ ] **회귀**: 정상 실행 시 false positive 0건 (1일 관찰)

## 10. 참조

- 이번 장애: `docs/archive/2026-04/collection-indent-fix/collection-indent-fix.report.md`
- 기존 자전 5고리: `CLAUDE.md` — Phase 1.67 섹션
- `src/application/services/ops_issue_detector.py` (결과 메트릭 감지)
- `src/application/scheduler/job_scheduler.py` — `MultiStoreRunner`
- `src/application/scheduler/job_definitions.py` — `SCHEDULED_JOBS` 레지스트리
