# Plan: claude-respond-traceability

## 1. 배경

매일 23:58 `claude_auto_respond_wrapper` (run_scheduler.py:1692) 가 `ClaudeResponder`를 호출해 운영 이상에 대한 AI 원인 분석을 수행한다. 현재도 기본 산출물(`response.md`, `debug_*.log`, `pipeline_status.json`, `pending_issues.json`의 `analysis`)은 남지만, 사후 역추적 시 다음 정보가 부족하다.

- 실행 소요 시간 (max_turns 10→30 상향 후 타임아웃 근접 여부 확인 불가)
- 당시 입력 스냅샷 (pending_issues.json은 DailyChainReport가 최종 처리 후 덮어씀 → 원본 유실)
- 스킵(선행 실패)과 정상 성공의 구분 (둘 다 success=True)
- 응답 유효성 (returncode=0이면 빈 문자열이어도 성공)
- 성공 경로의 중간 지표 (input 이슈 수, prompt/응답 길이)

오늘(2026-04-07)은 `claude-auto-respond Claude CLI 호출 실패` 이슈 수정 후 `max_turns=30`로 올린 **첫 검증일**이라, 역추적성 강화가 필요한 시점.

## 2. 목표

23:58 실행 1회의 모든 입력/출력/타이밍을 **다음 날 원본 소실 없이** 재구성할 수 있는 구조 확보.

## 3. 범위

### In scope
- `src/infrastructure/claude_responder.py` — 입력 스냅샷, 소요시간, 응답 유효성 검증, 로그 상세화
- `src/infrastructure/pipeline_status.py` — `record_step` details에 duration/status 구분 추가
- `run_scheduler.py:1692~1721` — 스킵 상태 명시적 표기

### Out of scope
- Claude CLI 자체의 동작/max_turns 조정 (별도 이슈)
- DailyChainReport 로직 변경
- 카카오 알림 포맷 변경

## 4. 요구사항

### FR (기능 요구사항)

| ID | 항목 | 설명 |
|---|---|---|
| FR-1 | 입력 스냅샷 보존 | 실행 직전 `pending_issues.json` 전체를 `data/auto_respond/{YYYY-MM-DD}_input.json`로 저장. 기존 파일 있으면 `_input_{HHMMSS}.json`으로 중복 회피 |
| FR-2 | 소요시간 기록 | `started_at`, `duration_sec`를 `record_step` details에 추가. 성공/실패/스킵 모두 |
| FR-3 | 응답 유효성 검증 | Claude stdout 길이가 50자 미만이거나 빈 응답이면 RuntimeError로 처리 → debug 로그 생성 |
| FR-4 | 스킵 상태 구분 | `record_step`에 `status` 필드("ok"/"skipped"/"failed") 추가. 기존 `success` 필드는 하위호환 유지 |
| FR-5 | 성공 경로 로그 상세화 | INFO 로그에 `anomaly_count`, `prompt_len`, `response_len`, `duration_sec` 포함 |

### NFR (비기능 요구사항)

| ID | 항목 | 설명 |
|---|---|---|
| NFR-1 | 하위호환 | pipeline_status.json의 기존 필드(success/summary/details/completed_at) 유지. `status`/`duration_sec`는 신규 추가 |
| NFR-2 | 실패 무해화 | 스냅샷 저장 실패는 본 작업을 중단시키지 않음 (`logger.debug`로 흘려보냄) |
| NFR-3 | 디스크 사용 | 입력 스냅샷은 `data/auto_respond/` 하위, 90일 보존 가정 (별도 로테이션은 향후) |

## 5. 수락 기준

1. 23:58 실행 후 `data/auto_respond/{date}_input.json`이 존재하고 당시 pending_issues.json과 동일
2. `.claude/pipeline_status.json`의 `steps.claude_respond.details`에 `started_at`, `duration_sec`, `status` 필드가 포함됨
3. `status` 값이 "ok" / "skipped" / "failed" 중 하나로 명확히 구분됨
4. Claude가 빈 응답을 반환한 가상 테스트에서 failed로 기록되고 debug 로그가 생성됨
5. INFO 로그에 `[AutoRespond] 분석 완료: duration=XXs, anomalies=N, prompt=N chars, response=N chars` 패턴이 나타남
6. 기존 DailyChainReport가 pipeline_status.json을 읽는 로직이 깨지지 않음 (하위호환)

## 6. 리스크 & 가정

- **리스크 1**: `pipeline_status.json` 스키마 변경이 DailyChainReport 파싱에 영향. → `status` 필드는 신규 키로 추가하고 `success` 불리언은 유지
- **리스크 2**: 입력 스냅샷이 민감 정보(이상 감지 세부)를 포함. → `data/auto_respond/`는 이미 로컬 전용, 외부 노출 없음
- **가정**: `pending_issues.json` 크기는 일반적으로 수 KB ~ 수십 KB. 스냅샷 저장 비용은 무시 가능

## 7. 영향 파일

| 파일 | 변경 |
|---|---|
| `src/infrastructure/claude_responder.py` | 입력 스냅샷, duration 측정, 응답 유효성 검증, 로그 상세화 |
| `src/infrastructure/pipeline_status.py` | `record_step` details에 status/duration 기록 보조 (선택적 헬퍼 추가 가능) |
| `run_scheduler.py` | `record_step` 호출부에 `status="skipped"` 명시 (1701 라인 주변) |

## 8. 검증 계획

1. 단위: `ClaudeResponder` mock 테스트 — 빈 응답 / 정상 응답 / Timeout 3가지
2. 통합: `run_scheduler.py`에서 수동으로 `claude_auto_respond_wrapper()` 호출 → 파일 생성 확인
3. 운영: 2026-04-08 23:58 실행 후 산출물 4종 교차 확인
4. 이슈 체인: `docs/05-issues/scheduling.md` — `claude-auto-respond` 블록에 검증 결과 갱신

## 9. 연관

- 선행 이슈: `scheduling.md` — `claude-auto-respond Claude CLI 호출 실패` (max_turns 10→30)
- CLAUDE.md 활성 이슈 테이블 해당 항목 갱신 필요
