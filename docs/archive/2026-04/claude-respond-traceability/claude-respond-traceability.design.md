# Design: claude-respond-traceability

> 관련 Plan: `docs/01-plan/features/claude-respond-traceability.plan.md`

## 1. 아키텍처 개요

```
┌─────────────── 23:58 claude_auto_respond_wrapper (run_scheduler.py) ────────────────┐
│                                                                                      │
│  check_dependencies("claude_respond")                                                │
│       │                                                                              │
│       ├─ 선행 실패 → record_step(status="skipped", duration_sec=0) ──────► END      │
│       │                                                                              │
│       └─ 통과 → ClaudeResponder().respond_if_needed()                                │
│                      │                                                               │
│                      ├─ [NEW] _snapshot_input() → data/auto_respond/{date}_input.json│
│                      ├─ t0 = time.monotonic()                                        │
│                      ├─ _call_claude(prompt)                                         │
│                      │     └─ [NEW] 응답 길이 < MIN_LEN → RuntimeError              │
│                      ├─ duration = time.monotonic() - t0                            │
│                      ├─ 결과 저장 response.md + pending analysis 병합                │
│                      └─ return {responded, output_path, summary,                     │
│                                  duration_sec, anomaly_count,                        │
│                                  prompt_len, response_len}                           │
│                                                                                      │
│  record_step("claude_respond", status, summary, details={                            │
│      started_at, duration_sec, anomaly_count, prompt_len, response_len,              │
│      output_path, input_snapshot_path, ...                                           │
│  })                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. 데이터 모델

### 2.1 `pipeline_status.json` 스키마 확장

```jsonc
{
  "steps": {
    "claude_respond": {
      "success": true,                  // 기존 유지 (하위호환)
      "status": "ok",                   // [NEW] "ok" | "skipped" | "failed"
      "summary": "분석 완료: ...",
      "details": {
        "started_at": "2026-04-07T23:58:00.123",  // [NEW]
        "duration_sec": 42.5,                      // [NEW]
        "anomaly_count": 3,                        // [NEW]
        "prompt_len": 1820,                        // [NEW]
        "response_len": 2453,                      // [NEW]
        "output_path": "data/auto_respond/2026-04-07_response.md",
        "input_snapshot_path": "data/auto_respond/2026-04-07_input.json"  // [NEW]
      },
      "completed_at": "2026-04-07T23:58:42.623"
    }
  }
}
```

**하위호환 규칙**
- `success` 불리언 유지 (`status=="ok"` ↔ `success=true`, `status=="skipped"` → `success=true`, `status=="failed"` → `success=false`)
- 기존 DailyChainReport가 `success`만 읽는다면 영향 없음
- 신규 필드는 모두 details 하위에 추가 → top-level 스키마 변경 없음

### 2.2 입력 스냅샷 파일

```
data/auto_respond/
├── 2026-04-07_input.json        # 그 날 23:58 시점의 pending_issues.json 복사
├── 2026-04-07_response.md       # Claude 분석 결과
└── debug_20260407_235842.log    # 실패 시 진단 (기존)
```

- 같은 날 재실행 시 `2026-04-07_input_235959.json`으로 HHMMSS suffix 추가

## 3. 컴포넌트 변경 상세

### 3.1 `src/infrastructure/claude_responder.py`

#### 신규 상수
```python
_MIN_RESPONSE_LEN = 50  # 이 길이 미만이면 실패 처리
```

#### `respond_if_needed()` 반환 스키마 확장
```python
{
    "responded": bool,
    "output_path": str | None,
    "summary": str | None,
    "duration_sec": float,        # [NEW] 실패/빈응답 포함 전체 경과
    "anomaly_count": int,         # [NEW]
    "prompt_len": int,            # [NEW] 0 if skipped
    "response_len": int,          # [NEW] 0 if failed
    "input_snapshot_path": str | None,  # [NEW]
    "error": str | None,          # [NEW] 실패 사유
}
```

#### 신규 메서드
```python
def _snapshot_input(self, issues: dict) -> Optional[Path]:
    """pending_issues.json 스냅샷 저장. 실패해도 무해화."""
    try:
        today = date.today().isoformat()
        path = _OUTPUT_DIR / f"{today}_input.json"
        if path.exists():
            ts = datetime.now().strftime("%H%M%S")
            path = _OUTPUT_DIR / f"{today}_input_{ts}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(issues, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return path
    except Exception as e:
        logger.debug(f"[AutoRespond] 입력 스냅샷 저장 실패 (무시): {e}")
        return None
```

#### `_call_claude()` 응답 검증 추가
```python
# returncode==0 이후
output = result.stdout or ""
if len(output.strip()) < _MIN_RESPONSE_LEN:
    self._dump_failure_context(cmd, result, env)
    raise RuntimeError(
        f"claude returned empty/short response: len={len(output)} "
        f"(bin={claude_cmd})"
    )
return output
```

#### `respond_if_needed()` 흐름 (핵심 변경)
```python
import time
t0 = time.monotonic()
anomaly_count = len(issues.get("anomalies") or [])
snapshot_path = self._snapshot_input(issues)

try:
    output = self._call_claude(prompt)
except Exception as e:
    duration = time.monotonic() - t0
    logger.warning(f"[AutoRespond] 실패 (duration={duration:.1f}s): {e}")
    self._append_analysis_to_pending(str(e), None, [])
    return {
        "responded": False, "output_path": None, "summary": str(e),
        "duration_sec": round(duration, 2),
        "anomaly_count": anomaly_count,
        "prompt_len": len(prompt),
        "response_len": 0,
        "input_snapshot_path": str(snapshot_path) if snapshot_path else None,
        "error": str(e),
    }

duration = time.monotonic() - t0
# ... 기존 저장 로직 ...
logger.info(
    f"[AutoRespond] 분석 완료: duration={duration:.1f}s, "
    f"anomalies={anomaly_count}, prompt={len(prompt)} chars, "
    f"response={len(output)} chars, path={output_path}"
)
return {
    "responded": True,
    "output_path": str(output_path),
    "summary": summary,
    "duration_sec": round(duration, 2),
    "anomaly_count": anomaly_count,
    "prompt_len": len(prompt),
    "response_len": len(output),
    "input_snapshot_path": str(snapshot_path) if snapshot_path else None,
    "error": None,
}
```

### 3.2 `src/infrastructure/pipeline_status.py`

#### `record_step` 시그니처 확장
```python
def record_step(
    step_name: str,
    success: bool,
    summary: str,
    details: dict = None,
    status: str = None,          # [NEW] "ok"|"skipped"|"failed", None이면 success로 추론
) -> None:
    if status is None:
        status = "ok" if success else "failed"
    # ...
    data["steps"][step_name] = {
        "success": success,          # 하위호환
        "status": status,            # [NEW]
        "summary": summary,
        "details": details or {},
        "completed_at": datetime.now().isoformat(),
    }
```

**하위호환**: `status` 파라미터는 기본값 `None` → 기존 호출부 수정 불요.

### 3.3 `run_scheduler.py:1692~1721` `claude_auto_respond_wrapper`

```python
def claude_auto_respond_wrapper() -> None:
    from src.infrastructure.pipeline_status import record_step, check_dependencies
    import time
    t_start = time.monotonic()
    started_at = datetime.now().isoformat()
    try:
        dep = check_dependencies("claude_respond")
        if not dep["can_proceed"]:
            msg = f"스킵 — {dep['reason']}"
            logger.warning(f"[AutoRespond] {msg}")
            record_step(
                "claude_respond", True, msg,
                details={
                    "started_at": started_at,
                    "duration_sec": round(time.monotonic() - t_start, 2),
                    "skip_reason": dep["reason"],
                },
                status="skipped",   # [NEW] 명시적 구분
            )
            return

        from src.settings.constants import CLAUDE_AUTO_RESPOND_ENABLED
        if not CLAUDE_AUTO_RESPOND_ENABLED:
            record_step(
                "claude_respond", True, "비활성화 (ENABLED=False)",
                details={"started_at": started_at, "duration_sec": 0.0},
                status="skipped",
            )
            return

        from src.infrastructure.claude_responder import ClaudeResponder
        result = ClaudeResponder().respond_if_needed()

        details = {
            "started_at": started_at,
            "duration_sec": result.get("duration_sec", 0.0),
            "anomaly_count": result.get("anomaly_count", 0),
            "prompt_len": result.get("prompt_len", 0),
            "response_len": result.get("response_len", 0),
            "output_path": result.get("output_path"),
            "input_snapshot_path": result.get("input_snapshot_path"),
        }

        if result.get("responded"):
            logger.info(f"[AutoRespond] 분석 완료: {result.get('output_path', '')}")
            record_step(
                "claude_respond", True,
                f"분석 완료: {result.get('summary', '')[:80]}",
                details=details, status="ok",
            )
        elif result.get("error"):
            details["error"] = result["error"]
            record_step(
                "claude_respond", False,
                f"실패: {result['error'][:80]}",
                details=details, status="failed",
            )
        else:
            logger.debug("[AutoRespond] pending 없음, 스킵")
            record_step(
                "claude_respond", True, "pending 없음, 스킵",
                details=details, status="skipped",
            )
    except Exception as e:
        duration = round(time.monotonic() - t_start, 2)
        logger.warning(f"[AutoRespond] 실패 (무시): {e}")
        record_step(
            "claude_respond", False, str(e),
            details={"started_at": started_at, "duration_sec": duration},
            status="failed",
        )
```

## 4. 구현 순서

| # | 작업 | 파일 | 영향 |
|---|---|---|---|
| 1 | `_MIN_RESPONSE_LEN` 상수 + `_snapshot_input()` 추가 | claude_responder.py | 신규 |
| 2 | `_call_claude()` 응답 유효성 검증 | claude_responder.py | 기존 로직 강화 |
| 3 | `respond_if_needed()` duration/count/len 계산 및 반환 확장 | claude_responder.py | 반환 스키마 확장 |
| 4 | `record_step`에 `status` 파라미터 추가 (기본값 None, 하위호환) | pipeline_status.py | 시그니처 확장 |
| 5 | `claude_auto_respond_wrapper` 재작성 — status 명시, details 풍부화 | run_scheduler.py | 로직 교체 |
| 6 | 단위 테스트: 빈응답/정상/타임아웃/스킵 | tests/ | 신규 |
| 7 | 수동 smoke: `python -c "from run_scheduler import claude_auto_respond_wrapper; claude_auto_respond_wrapper()"` | — | 검증 |

## 5. 테스트 계획

### 5.1 단위 테스트 (tests/test_claude_responder_traceability.py)

```python
def test_snapshot_input_creates_file(tmp_path, monkeypatch):
    # _OUTPUT_DIR를 tmp_path로 patch
    # _snapshot_input 호출 → 파일 생성 확인

def test_empty_response_raises(monkeypatch):
    # subprocess.run mock → stdout=""
    # _call_claude → RuntimeError("empty/short")

def test_short_response_raises(monkeypatch):
    # stdout="ok" (길이 2)
    # RuntimeError 발생

def test_respond_if_needed_returns_duration(monkeypatch):
    # 정상 응답 mock → duration_sec > 0, prompt_len > 0, response_len > 0

def test_record_step_status_default(tmp_path, monkeypatch):
    # status 미지정 → success=True → status="ok"
    # success=False → status="failed"
```

### 5.2 통합 smoke
- `pending_issues.json` 더미 생성 → `claude_auto_respond_wrapper()` 수동 호출
- `data/auto_respond/`에 input.json, response.md 생성 확인
- `.claude/pipeline_status.json`의 `claude_respond.details`에 신규 필드 확인

### 5.3 운영 검증 (2026-04-08 23:58)
- [ ] input.json 생성됨
- [ ] status 필드에 "ok"/"skipped"/"failed" 중 하나
- [ ] duration_sec 기록 (max_turns=30 실측값)
- [ ] INFO 로그에 duration/anomalies/prompt/response 포함

## 6. 리스크 대응

| 리스크 | 대응 |
|---|---|
| `time.monotonic()` 누락으로 duration=0 | try/except 바깥에서 t0 선언, finally 없이도 모든 경로에서 계산 |
| 스냅샷 저장 실패가 본작업 중단시킴 | `_snapshot_input` 내부 try/except, None 반환 |
| `status` 파라미터 추가로 기존 호출부 경고 | 기본값 None → 하위호환. 기존 `ops_detect`/`chain_report` 호출 무변경 |
| `_MIN_RESPONSE_LEN=50`이 너무 엄격 | 운영 1주 관찰 후 조정 가능하도록 모듈 상수로 분리 |

## 7. 관측 지표 (운영 후 확인)

- 일 1회 `pipeline_status.json` 읽어서 `claude_respond.details.duration_sec` 추적
- 1주 후: 평균/최대 duration, failed 횟수, skipped 횟수 집계
- 이상 발생 시: `input_snapshot_path` → `response.md` → `debug_*.log` 순 역추적

## 8. 문서 갱신

- `docs/05-issues/scheduling.md` — `claude-auto-respond` 블록에 traceability 강화 기록
- `CLAUDE.md` 활성 이슈 테이블 — 본 작업 WATCHING 추가, 04-08 검증 후 RESOLVED
