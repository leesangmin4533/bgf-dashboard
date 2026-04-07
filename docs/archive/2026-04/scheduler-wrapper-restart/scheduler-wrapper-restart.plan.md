# Plan: scheduler-wrapper-restart

## 1. 배경

2026-04-07 18:58 SrcWatcher가 src 변경을 감지하고 18:59:05에 `auto-reload 트리거 — graceful exit (code=0)` 처리되었으나, 외부 wrapper script가 재시작하지 않아 **약 1시간 동안 스케줄러 완전 중단**.

원인 분석 (2단계):

1. **운영 절차 실수**: 사용자가 `python run_scheduler.py` 직접 실행 → wrapper(`scripts/start_scheduler_loop.bat`)를 거치지 않음 → exit 0 시 재기동 주체 없음
2. **wrapper 결함**: bat 파일이 UTF-8로 저장되어 있는데 첫 줄에 `chcp 65001` 선언이 없음 → cmd가 cp949(기본 codepage)로 해석 → 한글 주석/echo 문자열이 명령어로 오인되어 `'hon' 은(는) 내부 또는 외부 명령... 아닙니다` 다수 발생 후 `python run_scheduler.py` 라인은 도달은 하지만 보기 흉하고 사용자 신뢰 저하

영향:
- 04-07 19~24시 사이 모든 스케줄(`23:30 batch_expire`, `23:45 ml_train`, `23:55 ops_detect`, `23:58 claude_auto_respond`, `00:00 nightly_collect` 등) **미실행 위험**
- 마침 오늘 `claude-respond-traceability` 첫 검증일이라 04-09 자동 검증 태스크가 빈손으로 도착할 가능성

긴급 대응:
- 20:03 임시로 `python run_scheduler.py` 직접 백그라운드 기동 (PID 39264) — Claude 세션과 수명 결합되어 영구적이지 않음
- bat 파일에 `chcp 65001 >nul` 추가 + 한글 주석 영문화 (이번 PR에 함께 반영)

## 2. 목표

스케줄러가 **죽지 않고**, 죽더라도 **자동 재기동**되며, **사용자가 한 번 시작하면 잊어도 되는** 운영 구조 확보.

## 3. 범위

### In scope
- `scripts/start_scheduler_loop.bat` — UTF-8 codepage 선언, 한글 주석 → 영문화, 로그 파일 출력 추가
- 자동 시작 등록 (Windows 시작 프로그램 또는 Task Scheduler) 가이드
- `docs/05-issues/scheduling.md` — `scheduler-wrapper-restart` 이슈 블록 신규 등록
- `CLAUDE.md` 활성 이슈 테이블 갱신

### Out of scope
- SrcWatcher 자체 로직 변경 (auto-reload는 의도된 동작)
- Linux/macOS systemd 등가물 (Windows 환경 한정)
- 헬스체크 self-recovery (job-health-monitor가 별도로 처리)

## 4. 요구사항

### FR
| ID | 항목 | 설명 |
|---|---|---|
| FR-1 | UTF-8 codepage 선언 | bat 첫 줄에 `chcp 65001 >nul` 추가 |
| FR-2 | 한글 주석/echo 영문화 | cp949 환경 fallback 시에도 깨지지 않도록 ASCII만 사용 |
| FR-3 | 로그 파일 출력 | wrapper 자체 동작 추적 가능하도록 `logs/wrapper.log`에 시작/exit code/재시작 횟수 append |
| FR-4 | 자동 시작 등록 가이드 | Windows 시작 폴더(`shell:startup`) 또는 Task Scheduler 등록 절차를 이슈 체인에 명시 |
| FR-5 | 운영 매뉴얼 갱신 | CLAUDE.md 빠른 시작 섹션에 "wrapper로 시작 (`python run_scheduler.py` 직접 실행 금지)" 명시 |

### NFR
| ID | 항목 |
|---|---|
| NFR-1 | 기존 사용자가 bat을 더블클릭하던 흐름 그대로 유지 |
| NFR-2 | exit code 매핑(0=reload, 2=stop, 기타=error backoff) 동작 보존 |
| NFR-3 | 로그 파일 무한 증가 방지 — 일자별 분리 또는 단순 append (1주 분량은 무시 가능) |

## 5. 수락 기준

1. `scripts/start_scheduler_loop.bat` 첫 줄 `chcp 65001 >nul` 존재
2. bat 파일을 더블클릭 또는 `cmd /c scripts\start_scheduler_loop.bat`로 실행 시 한글 깨짐 에러 0건
3. python 프로세스가 graceful exit code 0 반환 후 5초 이내 새 프로세스로 재기동되는 것을 로그로 확인
4. `logs/wrapper.log`에 `start`/`exit code`/`restart` 이벤트 기록
5. `docs/05-issues/scheduling.md`에 `scheduler-wrapper-restart` 이슈 블록 등록 (status=RESOLVED, fix 04-07)
6. `CLAUDE.md` 빠른 시작에 wrapper 사용 강제 문구 추가

## 6. 리스크 & 가정

- **리스크 1**: `chcp 65001` 변경이 자식 python 프로세스의 stdout 인코딩에 영향을 줄 수 있음
  - 대응: 기존에도 PYTHONUTF8=1 전제로 동작 → 이미 UTF-8 환경. 영향 없음
- **리스크 2**: Windows 시작 프로그램 등록은 사용자별 수동 작업
  - 대응: 가이드 문서로 충분, 자동 등록은 권한 문제로 제외
- **가정**: 사용자는 향후 python을 직접 실행하지 않는다 (CLAUDE.md 경고로 보강)

## 7. 영향 파일

| 파일 | 변경 |
|---|---|
| `scripts/start_scheduler_loop.bat` | chcp + 영문화 + 로그 출력 |
| `docs/05-issues/scheduling.md` | 이슈 블록 신규 |
| `CLAUDE.md` | 빠른 시작 + 활성 이슈 테이블 |

## 8. 검증 계획

1. 수동: bat 더블클릭 → 한글 에러 없이 정상 시작 확인
2. 코드 변경 트리거: src 파일 touch → SrcWatcher 감지 → graceful exit → wrapper가 재기동 → heartbeat 갱신 확인
3. 운영: 04-07 20:03 임시 기동 프로세스를 종료하고 wrapper 기반으로 재기동 후 04-08 23:58까지 무중단 확인

## 9. 연관

- 직전 작업: `claude-respond-traceability` (이 작업의 src 수정이 SrcWatcher reload를 트리거)
- 관련: `archive/2026-04/scheduler-auto-reload/`, `archive/2026-04/job-health-monitor/`
