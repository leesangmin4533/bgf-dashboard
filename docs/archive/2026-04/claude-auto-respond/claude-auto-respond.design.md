# Design: Claude 자동 대응 시스템 (claude-auto-respond)

> 작성일: 2026-04-05
> Plan 참조: `docs/01-plan/features/claude-auto-respond.plan.md`

---

## 1. 구현 개요

Python(규칙 엔진)이 이상 감지 시 `pending_issues.json`에 기록하면,
3분 후 `claude -p` CLI 호출로 AI 분석을 실행. 결과를 파일 저장 + 카카오 발송.

Phase 1은 **L1(읽기 전용)만** 구현.

---

## 2. 변경 파일

```
신규 파일 (1개):
  src/infrastructure/claude_responder.py     # Claude CLI 호출 + 결과 저장

변경 파일 (4개):
  src/application/services/ops_issue_detector.py  # pending_issues.json 저장 추가
  src/notification/kakao_notifier.py              # "ai_analysis" 카테고리 추가
  src/settings/constants.py                       # CLAUDE_AUTO_RESPOND_ENABLED 토글
  run_scheduler.py                                # 23:58 스케줄 등록
```

---

## 3. 상세 설계

### 3.1 constants.py

```python
# ── Claude 자동 대응 (claude_responder.py) ──
CLAUDE_AUTO_RESPOND_ENABLED = True     # False로 즉시 비활성화
CLAUDE_AUTO_RESPOND_MODEL = "sonnet"   # 비용 절감
CLAUDE_AUTO_RESPOND_MAX_TURNS = 10     # 무한 루프 방지
CLAUDE_AUTO_RESPOND_TIMEOUT = 300      # 5분 타임아웃
```

### 3.2 ops_issue_detector.py — pending_issues.json 저장

`run_all_stores()` 반환 후, anomaly가 있으면 JSON 파일에 기록.

```python
# run_all_stores() 맨 끝, return 직전에 추가
if unique:
    self._save_pending_issues(unique)

def _save_pending_issues(self, anomalies: list) -> None:
    """Claude 자동 대응용 pending_issues.json 저장"""
    from src.settings.constants import CLAUDE_AUTO_RESPOND_ENABLED
    if not CLAUDE_AUTO_RESPOND_ENABLED:
        return

    path = Path(__file__).parent.parent.parent.parent / ".claude" / "pending_issues.json"
    data = {
        "generated_at": datetime.now().isoformat(),
        "anomalies": [
            {
                "metric_name": a.metric_name,
                "title": a.title,
                "priority": a.priority,
                "description": a.description,
                "evidence": a.evidence,
            }
            for a in anomalies
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

### 3.3 claude_responder.py — 핵심 모듈

위치: `src/infrastructure/claude_responder.py` (I/O 계층: 외부 프로세스 호출)

```python
class ClaudeResponder:
    """Claude CLI를 통한 자동 원인 분석"""

    def respond_if_needed(self) -> dict:
        """pending_issues.json이 있으면 Claude 분석 실행"""
        # 1. pending_issues.json 로드 (없으면 스킵)
        # 2. 마일스톤 최신 스냅샷 병합
        # 3. prompt 구성
        # 4. claude -p 호출 (읽기 전용)
        # 5. 결과 저장: data/auto_respond/{date}_response.md
        # 6. 카카오 요약 발송
        # 7. pending_issues.json 삭제
        # 반환: {"responded": bool, "output_path": str, "summary": str}
```

#### subprocess 호출 상세

```python
def _call_claude(self, prompt: str) -> str:
    result = subprocess.run(
        [
            "claude",
            "-p", prompt,
            "--output-format", "text",
            "--model", CLAUDE_AUTO_RESPOND_MODEL,
            "--max-turns", str(CLAUDE_AUTO_RESPOND_MAX_TURNS),
            "--allowed-tools", "Read Grep Glob",
        ],
        cwd=str(self._project_dir),
        capture_output=True,
        text=True,
        timeout=CLAUDE_AUTO_RESPOND_TIMEOUT,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude 실패: {result.stderr[:200]}")
    return result.stdout
```

`--allowed-tools`: Read, Grep, Glob만 허용. **Bash, Edit, Write 차단** → 코드 수정 불가.

#### prompt 구성

```python
def _build_prompt(self, issues: dict) -> str:
    """분석 prompt 생성"""
    # 1. 감지된 anomaly 목록
    # 2. 마일스톤 KPI 현황 (최신 milestone_snapshots)
    # 3. 이슈체인 PLANNED 목록 (docs/05-issues/*.md 참조 지시)
    # 4. 분석 요청:
    #    - 근본 원인을 DB 데이터 기반으로 분석
    #    - PLANNED 중 기여 항목 식별
    #    - 즉시 적용 가능한 대응 제안
    #    - 첫 3줄에 핵심 요약
```

#### 카카오 요약 추출

```python
def _extract_summary(self, full_text: str, max_chars: int = 300) -> str:
    """분석 결과의 첫 3줄 추출 (카카오 발송용)"""
    lines = full_text.strip().split("\n")
    summary_lines = [l for l in lines[:5] if l.strip()][:3]
    summary = "\n".join(summary_lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars - 3] + "..."
    return summary
```

### 3.4 kakao_notifier.py

```python
# 변경
ALLOWED_CATEGORIES = {"food_expiry", "promotion", "receiving_mismatch", "milestone", "ai_analysis", "test"}
```

### 3.5 run_scheduler.py

```python
def claude_auto_respond_wrapper() -> None:
    """Claude 자동 대응 (매일 23:58, 이상 감지 3분 후)"""
    try:
        from src.settings.constants import CLAUDE_AUTO_RESPOND_ENABLED
        if not CLAUDE_AUTO_RESPOND_ENABLED:
            return

        from src.infrastructure.claude_responder import ClaudeResponder
        result = ClaudeResponder().respond_if_needed()
        if result.get("responded"):
            logger.info(f"[AutoRespond] 분석 완료: {result.get('output_path', '')}")
        else:
            logger.debug("[AutoRespond] pending 없음, 스킵")
    except Exception as e:
        logger.warning(f"[AutoRespond] 실패 (무시): {e}")

# 스케줄 등록
schedule.every().day.at("23:58").do(claude_auto_respond_wrapper)
```

---

## 4. 데이터 흐름

```
23:55  OpsIssueDetector.run_all_stores()
         ↓ anomaly 있으면
       _save_pending_issues() → .claude/pending_issues.json

23:58  claude_auto_respond_wrapper()
         ↓
       ClaudeResponder.respond_if_needed()
         ↓ pending_issues.json 로드
       _build_prompt() → prompt 구성
         ↓
       subprocess: claude -p "..." --allowed-tools "Read Grep Glob"
         ↓ (최대 5분)
       결과 → data/auto_respond/2026-04-06_response.md
         ↓
       _extract_summary() → 첫 3줄 추출
         ↓
       KakaoNotifier.send_message(summary, category="ai_analysis")
         ↓
       pending_issues.json 삭제
```

---

## 5. 구현 순서

| 순서 | 작업 | 파일 | 예상 줄 |
|------|------|------|--------|
| 1 | 상수 추가 | constants.py | 5줄 |
| 2 | 카카오 카테고리 | kakao_notifier.py | 1줄 |
| 3 | **claude_responder.py 신규** | src/infrastructure/ | ~120줄 |
| 4 | pending_issues.json 저장 | ops_issue_detector.py | ~25줄 |
| 5 | 스케줄러 등록 | run_scheduler.py | ~20줄 |
| 6 | 테스트 | tests/test_claude_responder.py | ~80줄 |

**총 ~250줄** (신규 ~200줄 + 기존 수정 ~30줄)

---

## 6. 테스트 설계

```python
# pending_issues.json 없으면 스킵
def test_respond_skips_when_no_pending():

# pending_issues.json 있으면 Claude 호출
def test_respond_calls_claude_with_pending(mock_subprocess):

# Claude 실패 시 graceful 처리
def test_respond_handles_claude_failure(mock_subprocess):

# 결과 파일 저장 확인
def test_response_saved_to_file(mock_subprocess):

# 카카오 요약 추출
def test_extract_summary_first_3_lines():
def test_extract_summary_truncates():

# ENABLED=False면 스킵
def test_disabled_skips_everything():

# prompt 구성에 anomaly 포함
def test_build_prompt_includes_anomalies():
```

---

## 7. 하위호환

| 변경 | 기존 동작 영향 |
|------|---------------|
| ops_issue_detector에 _save_pending 추가 | 기존 return값 변경 없음, 파일 저장만 추가 |
| ALLOWED_CATEGORIES 추가 | 기존 카테고리 유지 |
| 23:58 스케줄 추가 | 기존 23:55(ops) 영향 없음, 3분 간격 |
| CLAUDE_AUTO_RESPOND_ENABLED | 기본 True, False로 즉시 비활성화 |
