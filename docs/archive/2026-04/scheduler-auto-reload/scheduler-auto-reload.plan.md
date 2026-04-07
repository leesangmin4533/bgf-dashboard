# Plan: Scheduler 자동 리로드 (scheduler-auto-reload)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: scheduling.md (등록 예정)
> 마일스톤 기여: 운영 자동화 신뢰성 — HIGH (오늘 4건 fix 공통 운영 이슈)

---

## 1. 문제 정의

### 현상
오늘(2026-04-07) PDCA 4건 작업(claude-respond-fix, ops-metrics-waste-query-fix, d1-bgf-collector-import-fix, k4-non-food-sentinel-filter)이 모두 동일한 운영 이슈를 가짐:

> **코드를 수정·커밋·푸시해도 실행 중인 `run_scheduler.py` 프로세스는 옛 모듈을 메모리에 캐시하므로 fix가 무효화됨. 운영자가 수동 재시작해야만 적용됨.**

### 실증 사례
- **04-06 `bgf-collector-import-fix`**: daily_job.py L934 SalesCollector로 교체 완료. 그러나 04-07 14:00까지도 동일 ModuleNotFoundError 재현 → 1차 fix가 무력화. d1-bgf-collector-import-fix(2차 fix)에서 진짜 원인이 메서드명 + scheduler 캐시 둘 다였음을 발견
- **04-07 4건 모두**: 코드/테스트 통과했지만 실 효과는 다음 scheduler 재시작 후 검증 가능

### 영향
- **fix → 적용** 사이 무한정 지연 (운영자가 잊으면 며칠/주 단위)
- 검증 시점 불명확 → 회귀 추적 어려움
- 매번 똑같은 "수동 재시작" 의식으로 시간/주의력 소모
- 진짜 원인 파악을 방해 (1차 fix 후에도 같은 에러 → 새 원인 추정 → 디버깅 시간 낭비)

### 근본 원인
Python 모듈은 `import` 시 sys.modules에 캐시되며 자동 reload 없음. `importlib.reload()`도 transitive 종속성/객체 정체성 문제로 안전하지 않음. long-running 프로세스는 본질적으로 코드 변경에 둔감.

---

## 2. 목표

### 1차
코드 변경(특히 src/ 트리)이 감지되면 scheduler 프로세스가 **graceful exit**하고, 외부 wrapper script가 **즉시 재시작**.

### 2차
재시작 시점/사유가 로그에 명시되어 추적 가능.

### 3차
운영자가 수동 재시작할 필요 없음 (운영 부담 0).

### 비목표
- Hot-reload (모듈 in-place 교체) — 위험성 + 복잡도 큼
- supervisor/systemd 같은 외부 프로세스 매니저 도입 (Windows 환경 + 단순성 유지)
- 개발 편의용 watcher (이건 운영 안정성 목적)

---

## 3. 해결 방향 옵션 비교

| # | 옵션 | 장점 | 단점 | 평가 |
|---|---|---|---|---|
| A | git pull-watch 외부 스크립트 | 단순, 외부 의존 | 작업 직전 재시작 시점 제어 어려움 | ⚪ |
| B | `importlib.reload` 주기 실행 | 프로세스 유지 | transitive 깨짐, 위험 | ❌ |
| C | **file mtime watch + graceful exit + 외부 wrapper 재시작** | 표준 패턴, 안전, 단순 | wrapper script 1개 추가 필요 | ✅ |
| D | 각 wrapper가 자기 모듈만 reload | 부분 적용 | 모든 wrapper 수정, 누락 위험 | ⚪ |
| E | 운영 가이드만 (현 상태) | 코드 변경 0 | 사람 실수 의존 | ❌ |

### 채택: **옵션 C**

```
[run_loop.ps1 / run_loop.sh]  ← 외부 wrapper (무한 재시작 루프)
       │
       ▼
[python run_scheduler.py]  ← 본체
       │
       ├─ heartbeat thread
       ├─ schedule loop
       └─ ★ src_watcher thread (신규)
              │
              └─ src/ mtime 변경 감지 → graceful exit (sys.exit(0))
                  ↑
                  외부 wrapper가 exit code 0 감지 → 즉시 재시작
```

### 핵심 메커니즘
1. **src_watcher thread**: 60초마다 `src/`, `run_scheduler.py`, `config/` 트리의 최대 mtime을 비교. 시작 시점보다 새로운 파일이 있으면 변경 감지
2. **graceful exit**: 진행 중인 작업이 없을 때 (heartbeat 기준) `sys.exit(0)`. 작업 중이면 완료 대기 (최대 N분)
3. **외부 wrapper**: `while true; do python run_scheduler.py; done` 한 줄 (PowerShell/Bash)
4. **로그 명시**: `[AutoReload] src/order/order_executor.py changed at HH:MM:SS → graceful exit` + 재시작 후 `[Scheduler] Started (auto-reload trigger: ...)`

---

## 4. 범위

### 대상 파일
- `src/infrastructure/scheduler/src_watcher.py` (신규) — mtime 감지 데몬 스레드
- `run_scheduler.py` — main 진입점에서 watcher 시작 + graceful exit 신호 처리
- `scripts/run_scheduler_loop.ps1` 또는 `scripts/run_scheduler_loop.sh` (신규) — 무한 재시작 wrapper
- `tests/test_src_watcher.py` (신규) — mtime 감지 회귀 테스트
- `docs/05-issues/scheduling.md` — 이슈 등록
- `CLAUDE.md` 또는 README — 운영 가이드 1줄 추가

### 비범위
- 부분 hot-reload (모듈 단위)
- 테스트/dev 환경 watcher (별도)
- 작업 중 graceful 종료 시 in-flight 작업 결과 보존

---

## 5. 단계

| # | 작업 | 산출물 |
|---|---|---|
| 1 | `src_watcher.py` 작성 (mtime poller, 60초 간격) | 모듈 1개 |
| 2 | `run_scheduler.py`에서 watcher 시작 + graceful exit handler | 패치 |
| 3 | `run_scheduler_loop.ps1` (Windows) 작성 | 스크립트 1개 |
| 4 | 회귀 테스트: tmp_path에 src 시뮬레이션 + mtime 변경 후 watcher가 감지하는지 | test 1개 |
| 5 | 수동 검증: scheduler 시작 → src/ 파일 touch → graceful exit + wrapper 재시작 확인 | — |
| 6 | 이슈체인 등록 + 커밋 + 푸시 | — |
| 7 | 운영 가이드: `python run_scheduler.py` 대신 `./run_scheduler_loop.ps1` 사용 명문화 | CLAUDE.md |

---

## 6. 성공 조건

- [ ] src/ 파일 touch 시 60초 이내 watcher가 변경 감지
- [ ] graceful exit 후 wrapper script가 즉시 재시작
- [ ] 새 프로세스가 변경된 코드를 로드 (검증: 임의 변경 후 동작 확인)
- [ ] 회귀 테스트 1개 통과
- [ ] CLAUDE.md에 운영 절차 1줄 추가

---

## 7. 리스크

- **작업 도중 재시작**: 06:55에 src 변경 후 07:00 daily_order 직전 재시작이면 작업 누락 가능 → graceful exit는 진행 중 작업 끝까지 대기. 단 발주 작업이 5~10분 걸리면 다음 작업 시간과 충돌 가능. **mitigation**: 작업 실행 중에는 exit 보류하고 작업 종료 후 즉시 exit
- **무한 재시작 루프**: 코드 자체에 import 에러가 있으면 wrapper가 무한 재시작 → 카카오 알림 폭탄. **mitigation**: wrapper에 backoff (재시작 간격 5초 → 30초 → 1분) + import 에러 시 알림 1회만
- **mtime 신뢰성**: Windows OneDrive 동기화로 mtime이 갱신되지 않을 가능성. **mitigation**: 파일 hash 백업 옵션 또는 git rev-parse HEAD로 commit 변경 감지

---

## 8. 다음 단계

`/pdca design scheduler-auto-reload` — Design 문서 작성 (watcher 알고리즘, graceful exit 조건, wrapper script 상세)
