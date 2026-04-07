# Report: scheduler-wrapper-restart

> Plan → Design → Do → Check 완료. Match Rate **97%**.
> 일자: 2026-04-07

## 1. 목적

04-07 18:58 SrcWatcher graceful exit 후 wrapper 미가동으로 약 1시간 스케줄러 중단된 사고를 재발 방지. UTF-8 bat의 cp949 fallback 안전성과 wrapper 사용 강제화.

## 2. 원인 분석

| 단계 | 원인 |
|---|---|
| 1차 | 사용자가 `python run_scheduler.py` 직접 실행 → wrapper 우회 → exit 0 시 재기동 주체 부재 |
| 2차 | bat이 UTF-8 저장인데 `chcp 65001` 미선언 → cmd 기본 cp949가 한글 주석을 명령으로 오인 |

직전 PDCA(`claude-respond-traceability`) 커밋이 src 수정 → SrcWatcher 트리거의 직접 원인. 동작 자체는 의도된 것이지만 wrapper 사슬이 끊어져 있었음.

## 3. 변경 요약

| 파일 | 변경 |
|---|---|
| `scripts/start_scheduler_loop.bat` | `chcp 65001 >nul` 추가, REM/주석 영문화 (NOTE 추가) |
| `bbb/CLAUDE.md` | 빠른 시작 → `scripts\start_scheduler_loop.bat` 강제, python 직접 실행 금지 경고 |
| `bgf_auto/CLAUDE.md` | 활성 이슈 테이블에 RESOLVED 행 추가 |
| `docs/05-issues/scheduling.md` | `scheduler-wrapper-restart` RESOLVED 블록 + 교훈 |

## 4. PDCA 진행

| Phase | 산출물 |
|---|---|
| Plan | `docs/01-plan/features/scheduler-wrapper-restart.plan.md` (FR 5/NFR 3) |
| Design | `docs/02-design/features/scheduler-wrapper-restart.design.md` |
| Do | bat 수정 + 임시 python 종료(PID 39264) + 새 기동(PID 39120) |
| Check | `docs/03-analysis/scheduler-wrapper-restart.analysis.md` Match Rate 97% |
| Report | 본 문서 |

## 5. 운영 복구 타임라인

| 시각 | 이벤트 |
|---|---|
| 18:58:10 | SrcWatcher reload 감지 |
| 18:59:05 | run_scheduler graceful exit(0) — wrapper 미가동, 영구 종료 |
| 19:58 | Claude 진단으로 중단 감지 |
| 20:03:46 | 임시 python 백그라운드 기동 (PID 39264) |
| 20:08:02 | bat fix 후 새 백그라운드 기동 (PID 39120) |
| heartbeat | 정상 갱신 확인 |

## 6. Gap 요약

- **G1 [Minor]**: wrapper.log 미작성 — 향후 작업
- **G2 [자연 해소]**: 시작 프로그램 자동 등록 — 사용자 수동 작업

Critical 0건. 본 작업 RESOLVED.

## 7. 후속 작업 (사용자)

1. **시작 프로그램 등록**:
   - `Win+R` → `shell:startup` → 폴더 열기
   - `scripts/start_scheduler_loop.bat`의 바로가기를 이 폴더에 복사
   - 다음 부팅부터 자동 시작
2. **현재 임시 python 전환**:
   - 본 Claude 세션 종료 전, 직접 bat 더블클릭으로 영구 wrapper 기반으로 전환 권장
3. **운영 매뉴얼 학습**: bbb/CLAUDE.md 빠른 시작 변경 사항 숙지

## 8. 학습 노트

- **Windows bat은 UTF-8 저장 시 `chcp 65001 >nul`이 필수**. 한글 주석/echo가 cp949 환경에서 명령으로 오인됨
- **wrapper 우회 = auto-reload 사슬 단절**. SrcWatcher의 graceful exit는 wrapper 재기동을 전제로 한 설계
- **PDCA 직후 src 수정 → SrcWatcher 트리거**라는 부수효과를 항상 고려. 대규모 수정 시 wrapper 가동 여부 사전 확인
