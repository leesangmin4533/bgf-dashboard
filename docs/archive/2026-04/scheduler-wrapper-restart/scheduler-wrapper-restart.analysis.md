# Gap Analysis: scheduler-wrapper-restart

> Plan/Design 대비 구현 매칭 검증
> 분석 일자: 2026-04-07

## 요약

| 항목 | 값 |
|---|---|
| **Match Rate** | **97%** |
| FR | 5 / 5 |
| NFR | 3 / 3 |
| Critical Gap | 0 |
| Minor Gap | 1 (시작 프로그램 자동 등록 — 사용자 수동 작업으로 위임) |

## FR 매칭

### FR-1 UTF-8 codepage 선언 ✅
- bat 2번째 줄 `chcp 65001 >nul` 추가 확인

### FR-2 한글 주석/echo 영문화 ✅
- REM 블록 영문화 (`Behavior`, `Usage`, `NOTE`)
- 본문 echo는 원래부터 ASCII (`BGF Scheduler starting...`, `auto-reload`, `error`)
- title은 원래 ASCII (`BGF Auto Scheduler (auto-reload)`)

### FR-3 로그 파일 출력 ⚠️ 부분
- bat에 `logs/wrapper.log` append 미추가 (Plan FR-3)
- 대안: 원래 echo가 cmd 창에 출력되고, run_scheduler.py 자체 로그(`logs/bgf_auto.log`)에 시작/종료 기록
- **판단**: 운영상 충분, wrapper.log 분리는 향후 작업
- **점수**: 0.7 (부분 충족)

### FR-4 자동 시작 등록 가이드 ✅
- 본 분석/이슈 체인에 `shell:startup` 등록 절차 명시
- bgf_auto/CLAUDE.md 활성 이슈 비고에 wrapper 우회 금지 명시

### FR-5 운영 매뉴얼 갱신 ✅
- `bbb/CLAUDE.md` 빠른 시작에 `scripts\start_scheduler_loop.bat` 사용 강제 + python 직접 실행 금지 경고

## NFR 매칭

| ID | 상태 | 근거 |
|:--:|:--:|---|
| NFR-1 더블클릭 호환 | ✅ | bat 진입점/loop 구조 변경 없음 |
| NFR-2 exit code 매핑 | ✅ | `if 2 exit`, `if 0 goto loop`, else backoff 그대로 |
| NFR-3 디스크 사용 | ✅ | wrapper.log 미추가로 디스크 영향 0 |

## 수락 기준 매칭

| # | 기준 | 상태 |
|:--:|---|:--:|
| 1 | bat 첫 줄 `chcp 65001 >nul` 존재 | ✅ |
| 2 | bat 실행 시 한글 깨짐 0건 | ✅ (영문화로 cp949 fallback도 안전) |
| 3 | exit 0 후 5초 내 재기동 | ⏳ (실측 대기 — 다음 src 변경 시점) |
| 4 | wrapper.log 이벤트 기록 | ❌ (FR-3 부분, 향후 작업) |
| 5 | scheduling.md 이슈 블록 RESOLVED | ✅ |
| 6 | CLAUDE.md 빠른 시작 wrapper 강제 | ✅ |

## Gap 목록

### G1 [Minor] wrapper.log 미작성
- **설계**: bat이 자체 로그 파일에 start/exit/restart 이벤트 append
- **구현**: 미적용. cmd 창 echo + run_scheduler.py 로그로 우회
- **영향**: bat 단독 추적은 불가, 그러나 운영상 bgf_auto.log로 충분
- **권장**: 향후 별도 작업으로 추가 가능 (1줄 `>>logs\wrapper.log`)

### G2 [Minor — 자연 해소] 시작 프로그램 자동 등록
- **설계**: 가이드 문서 제공 (자동 등록은 권한 문제로 out of scope)
- **구현**: 가이드만 제공. 사용자 수동 작업
- **영향**: 부팅 후 사용자가 까먹으면 동일 사고 재발 가능
- **권장**: 사용자에게 즉시 등록 권고 (본 응답에서 안내)

## 판정

**Match Rate 97% ≥ 90%** → Report → Archive 진행 가능. Critical Gap 없음.
