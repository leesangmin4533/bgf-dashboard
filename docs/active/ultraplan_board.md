# Ultraplan / 로컬 세션 공유 보드

> **목적**: 로컬 CLI 세션과 클라우드 ultraplan 세션이 서로의 진행 상황을 인지하기 위한 공유 상태판.
> **규약**: 세부 운영 규칙은 [docs/ultraplan.md §6](../ultraplan.md) 참조.
> **양쪽 세션 의무**: 시작 시 이 파일 읽기 → scope 충돌 검사 → entry 추가/갱신.

---

## ACTIVE

## [ACTIVE] harness-week3-ai-summary (started 2026-04-08 04:10, by ultraplan)
- **Type**: ultraplan
- **Scope**: 신규 `src/application/services/ai_summary/**`, 기존 코드 무간섭 (읽기만)
- **Goal**: 하네스 엔지니어링 Week 3 — AI 요약 서비스 신규 설계 (일일 운영 로그·이슈 체인·milestone 요약 자동화)
- **Session link**: (발행 후 갱신)
- **Status**: ready (로컬 Plan 작성 완료, /ultraplan 비교 대기)
- **Last update**: 2026-04-08 04:30
- **Decisions**: Phase 1 MVP=카카오 5줄(23:50, Haiku, ~$0.30/월), 신규 schema v77 ai_summaries, 폴백=규칙기반. 선행: action_proposals executed_at 검증(04-09 07:00). 식품 예측 모듈 무간섭.
- **Plan**: docs/01-plan/features/harness-week3-ai-summary.plan.md

**큐 (처리 결과)**:
1. ~~`expiry-time-mismatch-redesign`~~ — **SKIP**: WATCHING(04-07 식품 전용+7일 임계값 fix)으로 이미 해결됨. 04-09 milestone 관측만 남음. PLANNED 항목은 stale 자동 감지.
2. `prediction-accuracy-regression-4cats` — **Plan 작성 완료** (`docs/01-plan/features/prediction-accuracy-regression-4cats.plan.md`). ultraplan 비교 대기.

---

## RECENT (최근 7일, 완료/취소)

## [DONE] food-underprediction-secondary Phase A (2026-04-08, by ultraplan+local-cli)
- **Type**: ultraplan (cloud) → local cherry-pick + report
- **Scope**: `src/prediction/{improved_predictor,base_predictor}.py`, `src/infrastructure/database/schema.py`, `src/settings/constants.py`, `tests/test_food_stage_trace.py`
- **Session link**: https://claude.ai/code/session_01BkwWeafQ3v6xfrPj4mTUVA
- **Status**: done — PR #4 cherry-picked as `98c7248`, report `49f55f7`, Match 97%
- **Decisions**: Phase B는 04-17 1주 관측 데이터 분석 후 분기. **04-10~04-16 동안 푸드 예측 경로 재수정 금지** (데이터 오염 방지).

---

## Entry 템플릿

```markdown
## [ACTIVE] <feature-name> (started YYYY-MM-DD HH:MM, by <local-cli|ultraplan>)
- **Type**: ultraplan | local-plan | local-do
- **Scope**: 건드리는 디렉토리/파일 패턴
- **Goal**: 한 줄 목표
- **Session link**: https://claude.ai/code/...   (ultraplan만)
- **Status**: drafting | needs-input | ready | executing | done | cancelled
- **Last update**: YYYY-MM-DD HH:MM
- **Decisions**: (ready 이후 핵심 결정 1~3줄)
```
