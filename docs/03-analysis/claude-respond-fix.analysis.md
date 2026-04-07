# Gap Analysis: claude-respond-fix

> 작성일: 2026-04-07
> Design: docs/02-design/features/claude-respond-fix.design.md
> 이슈체인: scheduling.md#claude-auto-respond-claude-cli-호출-실패
> **Match Rate: 100% — PASS**

---

## 종합 점수

| 카테고리 | 점수 | 상태 |
|----------|:---:|:---:|
| Phase A (진단 로깅) | 100% | PASS |
| Phase B (근본 원인 수정) | 100% | PASS |
| 회귀 테스트 | 100% | PASS |
| **Match Rate** | **100%** | **PASS** |

---

## Design 항목 검증

### 1. Phase A: `_call_claude()` stdout fallback + `_dump_failure_context()`
- `claude_responder.py` L114-123: `stderr or stdout or "(no output)"` fallback ✅
- 에러 메시지에 bin + cwd 포함 (`(bin={claude_cmd}, cwd={_PROJECT_DIR})`) ✅
- `_dump_failure_context(cmd, result, env)` 예외 전 호출 (L116) ✅
- 덤프 8개 필드(timestamp/claude_bin/returncode/cwd/cmd/key_env/stdout/stderr) 전부 일치 ✅
- `key_env` 키 6개(PATH/PYTHONUTF8/PYTHONIOENCODING/USERPROFILE/APPDATA/LOCALAPPDATA) 일치 ✅

### 2. Debug 파일 경로
- `data/auto_respond/debug_{YYYYMMDD_HHMMSS}.log` 일치 ✅
- `mkdir(parents=True, exist_ok=True)` ✅
- **추가 강화**: 전체 덤프 로직을 try/except로 감싸 덤프 실패가 원본 RuntimeError를 가리지 않음 (Design 이상의 방어 코드)

### 3. Prompt 제외
- `cmd_safe = cmd[:2] + ["[prompt omitted]"] + cmd[3:]` ✅
- 테스트에서 `assert "[prompt omitted]" in content` 검증 ✅

### 4. 회귀 테스트 3개 (`TestCallClaudeFailureDiagnostics`)
- `test_call_claude_dumps_debug_on_failure`: stdout fallback + debug 파일 + `[returncode] 1` + `[prompt omitted]` 검증 ✅
- `test_call_claude_success_returns_stdout`: 성공 시 stdout 반환 ✅
- `test_call_claude_empty_output_reports_no_output`: 빈 출력 시 `(no output)` 표기 ✅
- **14/14 테스트 통과**

### 5. Phase B: 근본 원인 수정
- 원인 식별: `Error: Reached max turns (10)` — 분석 작업이 Read/Grep 다수 호출 필요
- `CLAUDE_AUTO_RESPOND_MAX_TURNS` 10→30 ✅
- `CLAUDE_AUTO_RESPOND_TIMEOUT` 300→600 ✅
- 수동 재현 성공: `data/auto_respond/2026-04-07_response.md` 생성 ✅

---

## Gap 목록

### Missing (Design O / Impl X)
없음.

### Added (Design X / Impl O — 긍정적)
| 항목 | 위치 | 설명 |
|------|------|------|
| 덤프 try/except 방어 | claude_responder.py L128, L155-156 | 덤프 실패가 원본 에러를 가리지 않음 |

### Changed (Design ≠ Impl)
| 항목 | Design | Impl | 영향 |
|------|--------|------|------|
| cmd 덤프 포맷 | f-string 삽입 | list 후 f-string | 표면적(cosmetic), 둘 다 prompt 제외 |

---

## 권고 조치

**없음.** 구현이 설계와 100% 일치하며 방어적 개선 1건이 추가됨.

---

## 잔여 검증 (스케줄 태스크)

- [ ] 2026-04-08 23:58 스케줄 자동 실행에서 `data/auto_respond/2026-04-08_response.md` 생성 확인
- 실패 시: `data/auto_respond/debug_*.log` 확인 → 새 원인 식별 → 이슈체인 시도 2 추가

---

## 다음 단계

Match Rate 100% → `/pdca report claude-respond-fix`로 완료 보고서 작성
