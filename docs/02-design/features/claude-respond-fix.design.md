# Design: claude-auto-respond Claude CLI 호출 실패 수정 (claude-respond-fix)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/claude-respond-fix.plan.md
> 이슈체인: scheduling.md#claude-auto-respond-claude-cli-호출-실패

---

## 1. 설계 개요

2단계 접근:
1. **Phase A (진단)**: `_call_claude()` 실패 시 stdout/cmd/cwd/env를 함께 기록 → 재현으로 원인 식별
2. **Phase B (수정)**: 식별된 원인별 타겟 수정

Phase B는 Phase A 결과에 따라 분기되므로, 본 Design은 **Phase A를 확정 설계**하고 **Phase B는 가설별 대응 표**로 문서화한다.

---

## 2. Phase A: 진단 로깅 강화

### 변경 대상
`src/infrastructure/claude_responder.py:_call_claude()` (line 92~118)

### 변경 전 (현재)
```python
if result.returncode != 0:
    stderr = (result.stderr or "").strip()
    raise RuntimeError(
        f"claude exit={result.returncode}: {stderr[:200] if stderr else '(no stderr)'}"
    )
```

### 변경 후 (설계)
```python
def _call_claude(self, prompt: str) -> str:
    claude_cmd = _CLAUDE_BIN
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    cmd = [
        claude_cmd,
        "-p", prompt,
        "--output-format", "text",
        "--model", CLAUDE_AUTO_RESPOND_MODEL,
        "--max-turns", str(CLAUDE_AUTO_RESPOND_MAX_TURNS),
        "--allowed-tools", "Read Grep Glob",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(_PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=CLAUDE_AUTO_RESPOND_TIMEOUT,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        # 진단 정보 덤프 (data/auto_respond/debug_{timestamp}.log)
        self._dump_failure_context(cmd, result, env)
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or "(no output)"
        raise RuntimeError(
            f"claude exit={result.returncode}: {detail[:500]} "
            f"(bin={claude_cmd}, cwd={_PROJECT_DIR})"
        )
    return result.stdout

def _dump_failure_context(self, cmd, result, env):
    """실패 시 진단 정보를 debug 로그로 덤프"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_path = _OUTPUT_DIR / f"debug_{ts}.log"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    key_env = {k: env.get(k, "") for k in ("PATH", "PYTHONUTF8", "PYTHONIOENCODING",
                                             "USERPROFILE", "APPDATA", "LOCALAPPDATA")}
    lines = [
        f"[timestamp] {datetime.now().isoformat()}",
        f"[claude_bin] {_CLAUDE_BIN}",
        f"[returncode] {result.returncode}",
        f"[cwd] {_PROJECT_DIR}",
        f"[cmd] {cmd[:2]} ... [prompt omitted] ... {cmd[3:]}",
        f"[env] {json.dumps(key_env, ensure_ascii=False)}",
        f"[stdout:{len(result.stdout or '')}chars]",
        (result.stdout or "")[:2000],
        f"[stderr:{len(result.stderr or '')}chars]",
        (result.stderr or "")[:2000],
    ]
    debug_path.write_text("\n".join(lines), encoding="utf-8")
    logger.warning(f"[AutoRespond] 진단 로그 저장: {debug_path}")
```

### 핵심 포인트
- **stdout도 에러 메시지로 사용**: stderr가 비면 stdout fallback
- **debug 파일 덤프**: `data/auto_respond/debug_{timestamp}.log`에 전체 컨텍스트 기록
- **prompt는 덤프 제외**: 길이 문제 + 민감할 수 있음
- **key_env만 기록**: 전체 env 덤프는 과도 → PATH 위주 핵심만

---

## 3. Phase B: 원인별 수정 가설 표

| # | 가설 | 증상 | 수정 방향 |
|---|------|------|----------|
| 1 | stdout에 에러 출력 | debug 덤프 stdout에 "Error:" / "Please login" 등 | stdout fallback만으로 원인 파악 → 메시지별 분기 |
| 2 | Windows PATH `claude.cmd` 미인식 | `_CLAUDE_BIN` = "claude", FileNotFoundError 별도 발생 | `shutil.which("claude.cmd") or shutil.which("claude")` |
| 3 | `--allowed-tools` 형식 변경 | stderr/stdout에 "unknown option" / "invalid value" | `--allowed-tools Read --allowed-tools Grep --allowed-tools Glob` 반복 또는 콤마 |
| 4 | OAuth 재인증 필요 | stdout에 "log in" / "authentication required" | CLAUDE_AUTO_RESPOND_ENABLED=False 폴백 + 알림, 수동 로그인 안내 |
| 5 | prompt 인코딩 | UnicodeEncodeError in subprocess | 이미 PYTHONUTF8=1 설정됨 → 확인 후 추가 대응 |
| 6 | --max-turns 최소값 변경 | "max-turns must be >= N" | CLAUDE_AUTO_RESPOND_MAX_TURNS 상수 조정 |

---

## 4. 회귀 테스트 설계

### 대상 파일
`tests/test_claude_responder.py` (신규 또는 기존 확장)

### 테스트 케이스
1. **test_call_claude_dumps_debug_on_failure**
   - `subprocess.run`을 mock하여 returncode=1, stdout="error msg", stderr=""
   - `_dump_failure_context`가 호출되어 debug 파일 생성 확인
   - RuntimeError 메시지에 stdout 내용 포함 확인

2. **test_call_claude_success**
   - returncode=0, stdout="# Analysis\nOK" → 그대로 반환 확인

3. **test_respond_if_needed_handles_claude_failure**
   - pending_issues.json 존재 + Claude 실패 시
   - pending은 삭제되지 않음 (기존 보장)
   - `analysis.responded = False` + summary에 에러 내용 기록

### 실행
```bash
pytest tests/test_claude_responder.py -v
```

---

## 5. 구현 순서

| 순서 | 작업 | 파일 | 예상 변경 |
|------|------|------|----------|
| 1 | `_call_claude()` 진단 로깅 + stdout fallback | claude_responder.py | +40 lines |
| 2 | `_dump_failure_context()` 추가 | claude_responder.py | +20 lines |
| 3 | 회귀 테스트 3개 추가 | test_claude_responder.py | +60 lines |
| 4 | 수동 재현: `python -c "from src.infrastructure.claude_responder import ClaudeResponder; ClaudeResponder().respond_if_needed()"` | — | — |
| 5 | debug log 분석 → 원인 식별 | data/auto_respond/debug_*.log | — |
| 6 | 가설 표의 해당 원인별 수정 | claude_responder.py (or constants.py) | 가변 |
| 7 | 재실행 → 성공 확인 | `data/auto_respond/{date}_response.md` | — |
| 8 | 이슈체인 갱신: [OPEN] → [WATCHING] + 시도 N 기록 | docs/05-issues/scheduling.md | — |
| 9 | 04-08 23:58 스케줄 검증 | logs/bgf_auto.log | — |

---

## 6. 검증 방법

### 단위 테스트
```bash
pytest tests/test_claude_responder.py -v
```

### 통합 재현 (manual)
```bash
# pending_issues.json이 있는 상태에서
cd bgf_auto
python -c "from src.infrastructure.claude_responder import ClaudeResponder; print(ClaudeResponder().respond_if_needed())"
```

### 성공 기준
1. `data/auto_respond/debug_*.log` 생성 (실패 시) 또는
2. `data/auto_respond/{date}_response.md` 생성 (성공 시)
3. `logs/bgf_auto.log`에 `[AutoRespond] 분석 완료` 로그 존재
4. 회귀 테스트 통과

---

## 7. 롤백 계획

Phase A는 로깅 강화만 하므로 롤백 불필요 (기존 동작 유지).
Phase B에서 문제 발생 시: `CLAUDE_AUTO_RESPOND_ENABLED = False`로 즉시 비활성화 가능.

---

## 8. 다음 단계

`/pdca do claude-respond-fix` — 구현 시작
