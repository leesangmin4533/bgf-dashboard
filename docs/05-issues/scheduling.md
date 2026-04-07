# 스케줄링 이슈 체인

> 최종 갱신: 2026-04-06
> 현재 상태: executed_at 수정 완료 + GHOST_STOCK 승격/하네스 Week3 계획

---

## [WATCHING] action_proposals v70 컬럼 매장 DB 미적용 (04-05 ~ )

**문제**: v70 마이그레이션(executed_at, verified_at, verified_result)이 common.db SCHEMA_MIGRATIONS에만 있고 _STORE_COLUMN_PATCHES에 누락 → 매장 DB에 미적용
**영향**: ExecutionVerifier.verify_previous_day()가 매일 4매장에서 "no such column: executed_at" 오류 → 자전 시스템 4고리(검증) 전체 무력화
**설계 의도**: 매장 DB 스키마 변경은 반드시 _STORE_COLUMN_PATCHES에도 동기화. SCHEMA_MIGRATIONS(common.db)와 이중 관리 필요.

### 시도 1: _STORE_COLUMN_PATCHES에 3개 컬럼 ADD 추가 + 기존 DB 직접 보정 (2bd24bf, 04-05)
- **왜**: 근본 원인이 _STORE_COLUMN_PATCHES 누락이므로 직접 추가
- **결과**: 4매장 DB 컬럼 추가 확인, 테스트 13개 통과

> 변경 내용은 `git show 2bd24bf`로 확인.

### 교훈
- **매장 DB 스키마 변경 시 2곳 동기화 필수**: `models.py SCHEMA_MIGRATIONS` + `schema.py _STORE_COLUMN_PATCHES`
- SCHEMA_MIGRATIONS은 common.db(레거시)에만 적용됨. 매장 store DB는 _STORE_COLUMN_PATCHES 경로로만 컬럼이 추가됨
- schema_version이 72까지 올라가도 실제 컬럼이 없을 수 있음 (version 숫자와 실제 스키마 불일치 가능)

### 해결: _STORE_COLUMN_PATCHES 동기화 (2bd24bf, 04-05)
- 검증:
  - [ ] 내일(04-06) 07:00 로그에서 [Verify] executed_at 오류 소멸 확인 (스케줄: eb6c54ca)

---

## [WATCHING] claude-auto-respond Claude CLI 호출 실패 (04-06 ~ 04-07)

**문제**: 2026-04-06 23:58 스케줄(claude-auto-respond)이 `claude exit=1: (no stderr)`로 실패. stderr가 비어있어 원인 추적 불가.
**영향**: 이상 감지 시 L1 자동 원인 분석 무력화 → DailyChainReport에 분석 결과 누락 → 운영자가 수동 분석 필요
**설계 의도**: 이상 감지(23:55 OpsIssueDetector) → pending_issues.json 저장 → 23:58 ClaudeResponder가 claude -p 읽기전용 호출 → 분석 결과를 pending에 병합 → 00:02 DailyChainReport가 통합 발송

**재현 로그**:
```
2026-04-06 23:55:02 [OpsIssueDetector] pending_issues.json 저장 (5건)
2026-04-07 00:00:06 [AutoRespond] Claude 호출 실패: claude exit=1: (no stderr)
```

**가설 5가지**:
1. stdout에 에러 메시지 출력 (CLI가 stderr 대신 stdout 사용)
2. Windows 스케줄러 컨텍스트에서 `claude.cmd` shim PATH 미인식
3. `--allowed-tools "Read Grep Glob"` 플래그 형식 변경 (최신 CLI에서 콤마/반복 옵션 요구)
4. 비대화형 환경에서 OAuth 재인증 트리거
5. Windows cp949 ↔ UTF-8 prompt 인코딩 충돌

**관련 파일**: `src/infrastructure/claude_responder.py:92-118` `_call_claude()`

### 시도 1: 진단 로깅 강화 + max_turns 10→30 상향 (04-07)
- **왜**: 기존 코드는 stderr만 출력했는데 claude CLI는 stdout으로 에러 출력 → 원인 불명
- **조치**:
  1. `_call_claude()` stdout fallback + `_dump_failure_context()`로 debug 파일 덤프
  2. 재현 결과 실제 원인 = `Error: Reached max turns (10)` (분석 작업에 Read/Grep 다수 호출)
  3. `CLAUDE_AUTO_RESPOND_MAX_TURNS 10→30`, `TIMEOUT 300→600` 상향
- **결과**: ✓ 성공 — `data/auto_respond/2026-04-07_response.md` 생성 확인
- **실패 패턴**: (신규) stderr-only-error-reporting

### 교훈
- **서브프로세스 에러는 stdout/stderr 둘 다 확인**: claude CLI는 사용량/turn 제한 에러를 stdout으로 출력
- **max_turns는 실제 분석 복잡도 기준**: 읽기 전용 도구(Read/Grep/Glob)를 많이 쓰는 분석 작업은 10 turn으로 부족
- **진단 덤프는 실패 재현의 핵심**: stderr만 찍으면 1차 조사에서 헤맨다. cmd/cwd/env/stdout 전체 덤프 표준화 필요

### 검증 체크포인트
- [x] 진단 로깅 강화 후 수동 재현 (04-07 13:25 debug_20260407_132510.log)
- [x] 원인 식별: max_turns 초과 (stdout만 출력)
- [x] 수정 후 재실행 성공 (04-07 13:29, 2026-04-07_response.md 생성)
- [x] 회귀 테스트 3개 추가 (TestCallClaudeFailureDiagnostics, 14/14 통과)
- [ ] 04-08 23:58 스케줄에서 `data/auto_respond/2026-04-08_response.md` 자동 생성 확인

**관련 Plan**: [docs/01-plan/features/claude-respond-fix.plan.md](../01-plan/features/claude-respond-fix.plan.md)

---

## [PLANNED] CLEAR_GHOST_STOCK 자동실행 승격 검토 (P2)

**목표**: 유령 재고 보정(CLEAR_GHOST_STOCK)을 LOW(승인 필요) → HIGH(자동 실행)로 승격
**동기**: 현재 LOW 분류라 매번 카카오 승인 필요. 2주 오탐률 확인 후 안전하면 자동화
**선행조건**: integrity_checks 2주 누적 데이터에서 food_ghost_stock 오탐률 < 5%
**예상 영향**: constants.py AUTO_EXEC_HIGH/LOW, auto_executor.py _execute_action()

---

## [PLANNED] 하네스 엔지니어링 Week 3 — AI 요약 서비스 (P2)

**목표**: integrity 체크 + 발주 결과를 규칙 기반 템플릿으로 요약해서 카카오 리포트에 포함
**동기**: 매일 카카오 알림이 원시 데이터만 전달 → 의사결정에 필요한 요약 부족
**선행조건**: executed_at 검증 완료 (WATCHING 이슈 해결)
**예상 영향**: schema.py (ai_summaries DDL), notification/summary_report_service.py (신규), daily_job.py Phase 3, kakao_notifier.py

## [PLANNED] 자전 시스템 미해결 항목 (expiry_time_mismatch) (P1)

**목표**: 체크 expiry_time_mismatch이(가) 31일 연속 anomaly 발생 중 (1개 항목)
**동기**: 자동 감지 (2026-04-06) -- integrity_unresolved
**선행조건**: 없음
**예상 영향**: integrity_unresolved 관련 파일


---
