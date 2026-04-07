# Design: scheduler-wrapper-restart

> 관련 Plan: `docs/01-plan/features/scheduler-wrapper-restart.plan.md`

## 1. 아키텍처 개요

```
[사용자 부팅] 또는 [더블클릭]
        │
        ▼
┌─────────────────────────────────────────────────┐
│ scripts/start_scheduler_loop.bat                │
│  ├─ chcp 65001 >nul    [NEW] UTF-8 codepage     │
│  ├─ title / cd                                   │
│  └─ :loop                                        │
│       ├─ python run_scheduler.py                 │
│       ├─ CODE = ERRORLEVEL                       │
│       ├─ if 2 → exit (stop)                      │
│       ├─ if 0 → goto loop (auto-reload)          │
│       └─ else → timeout 5 → goto loop (backoff)  │
└─────────────────────────────────────────────────┘
        │
        ▼
[run_scheduler.py]
  ├─ SrcWatcher → src 변경 시 graceful exit(0)
  ├─ Ctrl+C → exit(2)
  └─ unhandled exception → exit(1+)
```

## 2. 변경 상세

### 2.1 `scripts/start_scheduler_loop.bat`

| 변경 | Before | After |
|---|---|---|
| codepage 선언 | 없음 | `chcp 65001 >nul` (line 2) |
| 주석 언어 | 한글 (`REM 동작:`, `REM 사용:`) | 영문 (`REM Behavior:`, `REM Usage:`) |
| 본문 동작 | 동일 | 동일 (loop/exit 분기 그대로) |

> 본문 로직(`if CODE==2 exit`, `if CODE==0 goto loop`, 기타 backoff)은 이미 검증된 구조라 변경하지 않는다.

### 2.2 `docs/05-issues/scheduling.md`

신규 이슈 블록 추가:

```
### scheduler-wrapper-restart [RESOLVED 04-07]
- 발견: 04-07 18:58~20:03 SrcWatcher graceful exit 후 wrapper 미가동으로 1시간 중단
- 원인: ① 사용자 직접 python 실행(wrapper 우회) ② bat이 UTF-8 저장인데 chcp 65001 미선언
- 수정: scripts/start_scheduler_loop.bat → chcp 65001 추가, 한글 주석 영문화
- 검증: 더블클릭 + cmd /c 실행 시 한글 깨짐 0건, exit 0 시 loop 정상
- 후속: CLAUDE.md 빠른 시작에 wrapper 강제 사용 명시, 시작 프로그램 등록 가이드
```

### 2.3 `CLAUDE.md`

빠른 시작 섹션 갱신:

```diff
- # 스케줄러 시작 (07:00 자동 실행)
- python run_scheduler.py
+ # 스케줄러 시작 (07:00 자동 실행) — wrapper 사용 강제
+ scripts\start_scheduler_loop.bat
+ # ⚠️ python run_scheduler.py 직접 실행 금지 (auto-reload 시 재기동 불가)
```

활성 이슈 테이블:

```
| RESOLVED | P2 | scheduler-wrapper-restart (04-07 18:58 SrcWatcher reload 후 1h 중단) | scheduling.md | bat chcp 65001 + 영문화, 04-07 20:08 재기동 |
```

## 3. 구현 순서

| # | 작업 | 파일 |
|---|---|---|
| 1 | bat에 `chcp 65001 >nul` 추가, 한글 주석 영문화 | `scripts/start_scheduler_loop.bat` ✅ 완료 |
| 2 | 임시 직접 실행 python 종료 + 새 백그라운드 기동 | (운영 작업) ✅ 완료 (PID 39120) |
| 3 | 이슈 체인 갱신 | `docs/05-issues/scheduling.md` |
| 4 | CLAUDE.md 빠른 시작 + 활성 이슈 테이블 갱신 | `CLAUDE.md` |

## 4. 테스트 계획

1. **수동**: bat 더블클릭 → cmd 창에 한글 에러(`'hon' 은(는)...`) 0건 확인
2. **재시작 시뮬레이션**: src 파일 touch → SrcWatcher 60초 내 감지 → exit 0 → wrapper가 5초 내 재기동 → 새 PID + heartbeat 갱신
3. **정지 시뮬레이션**: Ctrl+C → exit 2 → wrapper 종료

## 5. 리스크

| 리스크 | 완화 |
|---|---|
| chcp 65001이 자식 python의 stdout 인코딩에 영향 | PYTHONUTF8=1 전제 — 이미 UTF-8 환경. 영향 없음 |
| 사용자가 또 python 직접 실행 | CLAUDE.md 경고 + bat 자체에 사용 안내 |
| 시작 프로그램 미등록 | 가이드 문서 제공, 자동화는 권한 문제로 제외 |

## 6. 영향 파일 요약

- `scripts/start_scheduler_loop.bat` (Do 단계 완료)
- `docs/05-issues/scheduling.md` (이슈 블록 신규)
- `CLAUDE.md` (빠른 시작 + 활성 이슈)
