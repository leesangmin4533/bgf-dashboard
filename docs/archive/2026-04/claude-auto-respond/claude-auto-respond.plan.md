# Plan: Claude 자동 대응 시스템 (claude-auto-respond)

> 작성일: 2026-04-05
> 상태: Plan
> 마일스톤 기여: 전체 (자동 개선 루프)

---

## 1. 문제 정의

### 현상
현재 시스템은 **계기판**(측정+표시)만 존재하고 **자동 조종**(분석→판단→실행)이 없음.

```
현재 흐름:
Python(규칙) → 이상 감지 → 카카오 알림 → [사람이 읽음] → [사람이 Claude Code 세션 시작] → 수정

원하는 흐름:
Python(규칙) → 이상 감지 → Claude Code에 문제 전달 → Claude가 원인 분석 + 대응 → 카카오로 결과 보고
```

### 근본 원인
프로젝트 전체에서 Claude API/CLI를 프로그래밍적으로 호출하는 코드가 0건.
모든 "판단"은 사람이 짠 if문이고, Claude는 사람이 세션을 열 때만 동작.

### 기술적 가능성 확인
- `claude` CLI: `/c/Users/kanur/.local/bin/claude` (v2.1.92) 설치 확인
- `-p` 플래그: 비대화형 호출 가능 (`claude -p "prompt" --output-format text`)
- `--permission-mode auto`: 도구 권한 자동 승인 가능
- `--allowed-tools`: 사용 가능 도구 제한 가능 (안전성 확보)

---

## 2. 설계 원칙

### 안전 등급 (3단계)

| 등급 | Claude 권한 | 예시 |
|------|------------|------|
| **L1 분석만** | 읽기 전용 (Read, Grep, Glob, Bash 읽기) | 원인 분석, 리포트 생성 |
| **L2 제안+승인** | L1 + 카카오로 제안 발송, 사람 승인 후 실행 | 상수 조정, 로직 변경 |
| **L3 자동 실행** | L1 + 코드 수정 + 테스트 + 커밋 | 검증된 안전한 변경만 |

**Phase 1에서는 L1(분석만)만 구현**. L2/L3는 L1의 신뢰도 확인 후 확장.

### 비용 제어
- 일 1회 호출 (23:58, 이상 감지 직후)
- 이상 없으면 호출 안 함 (조건부 실행)
- `--model sonnet` 사용 (비용 절감, 분석 충분)
- `--max-turns 10` 제한 (무한 루프 방지)

---

## 3. Phase 1 구현: L1 분석 자동 대응

### 3.1 흐름

```
매일 23:55: OpsIssueDetector → 이상 감지 → pending_issues.json 저장
매일 23:58: claude_auto_respond() → pending_issues.json 읽기
              ↓
         Claude CLI 호출 (-p 모드, 읽기 전용)
              ↓
         "K2 미달 원인을 분석하고 대응 제안을 작성해"
              ↓
         분석 결과 → data/auto_respond/{date}_response.md 저장
              ↓
         카카오 발송: "[AI 분석] K2 미달 원인: 행사 종료 후 폐기 23건..."
```

### 3.2 pending_issues.json 형식

OpsIssueDetector가 이상 감지 시 저장:

```json
{
  "generated_at": "2026-04-06T23:55:00",
  "anomalies": [
    {
      "metric_name": "waste_rate",
      "title": "001 폐기율 상승 조사",
      "priority": "P1",
      "evidence": {"mid_cd": "001", "rate_7d": 0.05, "rate_30d": 0.02}
    }
  ],
  "milestone": {
    "K1": {"value": 1.03, "status": "ACHIEVED"},
    "K2": {"value": 0.035, "status": "NOT_MET"},
    "K3": {"value": 0.02, "status": "ACHIEVED"},
    "K4": {"value": 1, "status": "ACHIEVED"}
  }
}
```

### 3.3 Claude CLI 호출

```python
def claude_auto_respond():
    """Claude CLI로 이상 원인 분석 (L1: 읽기 전용)"""
    issues_path = PROJECT_DIR / ".claude" / "pending_issues.json"
    if not issues_path.exists():
        return  # 이상 없으면 스킵

    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    if not issues.get("anomalies"):
        return

    today = date.today().isoformat()
    output_path = PROJECT_DIR / "data" / "auto_respond" / f"{today}_response.md"

    prompt = _build_analysis_prompt(issues)

    result = subprocess.run(
        [
            "claude",
            "-p", prompt,
            "--output-format", "text",
            "--model", "sonnet",
            "--max-turns", "10",
            "--allowed-tools", "Read Grep Glob Bash(git log:*)",
        ],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=300,  # 5분 타임아웃
        encoding="utf-8",
    )

    if result.returncode == 0 and result.stdout.strip():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.stdout, encoding="utf-8")

        # 카카오로 요약 발송
        summary = _extract_summary(result.stdout, max_chars=300)
        _send_kakao(f"[AI 분석] {today}\n{summary}")

    # 처리 완료된 파일 삭제
    issues_path.unlink(missing_ok=True)
```

### 3.4 분석 prompt 구성

```python
def _build_analysis_prompt(issues: dict) -> str:
    anomalies = issues.get("anomalies", [])
    milestone = issues.get("milestone", {})

    lines = [
        "bgf_auto 프로젝트에서 운영 이상이 감지되었습니다.",
        "원인을 분석하고 대응 방안을 제안해주세요.",
        "",
        "## 감지된 이상",
    ]
    for a in anomalies:
        lines.append(f"- [{a['priority']}] {a['title']}")
        if a.get("evidence"):
            lines.append(f"  데이터: {json.dumps(a['evidence'], ensure_ascii=False)}")

    lines.append("")
    lines.append("## 마일스톤 KPI 현황")
    for k, v in milestone.items():
        lines.append(f"- {k}: {v.get('value')} ({v.get('status')})")

    lines.extend([
        "",
        "## 분석 요청",
        "1. 이상의 근본 원인을 DB 데이터 기반으로 분석",
        "2. PLANNED 이슈 중 이 문제에 기여하는 항목 식별 (docs/05-issues/ 참조)",
        "3. 즉시 적용 가능한 대응 제안 (코드 변경 없이 상수 조정 등)",
        "4. 코드 변경이 필요한 경우 구체적 방향 제시",
        "",
        "결과를 마크다운으로 작성하되, 첫 3줄에 핵심 요약을 넣어주세요.",
    ])

    return "\n".join(lines)
```

---

## 4. 변경 파일

| # | 파일 | 변경 |
|---|------|------|
| 1 | `src/application/services/ops_issue_detector.py` | 이상 감지 시 pending_issues.json 저장 |
| 2 | `src/analysis/milestone_tracker.py` | evaluate() 결과를 pending_issues.json에 병합 |
| 3 | `src/infrastructure/claude_responder.py` | **신규** — Claude CLI 호출 + 결과 저장 |
| 4 | `run_scheduler.py` | 23:58 스케줄 등록 |
| 5 | `src/notification/kakao_notifier.py` | ALLOWED_CATEGORIES에 "ai_analysis" 추가 |
| 6 | `src/settings/constants.py` | CLAUDE_AUTO_RESPOND_ENABLED 토글 |

---

## 5. 안전장치

| 장치 | 내용 |
|------|------|
| `--allowed-tools` 제한 | Read, Grep, Glob, Bash(git log) 만 허용 — 코드 수정 불가 |
| `--max-turns 10` | 무한 루프 방지 |
| `timeout=300` | 5분 초과 시 강제 종료 |
| `CLAUDE_AUTO_RESPOND_ENABLED` | False로 즉시 비활성화 가능 |
| 조건부 실행 | pending_issues.json 없으면 Claude 호출 안 함 |
| 비용 제한 | `--model sonnet` + 일 최대 1회 |

---

## 6. Phase 2 확장 로드맵 (Phase 1 안정화 후)

### L2: 제안 + 승인

```
Claude 분석 → "PREDICTION_PARAMS['moving_avg_days']를 7→5로 변경 제안"
  → 카카오: "상수 조정을 제안합니다. 승인하시겠습니까? Y/N"
  → 사람 Y → Claude가 constants.py 수정 + 테스트 + 커밋
```

### L3: 자동 실행 (검증된 패턴만)

- eval_params.json 자동 튜닝 (상수 조정, 테스트 통과 필수)
- 신규 이슈체인 [PLANNED] 자동 등록
- WATCHING → RESOLVED 자동 전환 (검증 통과 시)

---

## 7. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| Claude 분석이 부정확 | 잘못된 제안 → 사람이 잘못된 방향으로 판단 | L1은 읽기 전용이라 시스템 영향 없음. 분석 품질은 2주 모니터링 |
| API 비용 초과 | 월 비용 증가 | sonnet 사용 + 조건부 호출 + 일 1회 제한 |
| Claude CLI 버전 변경 | 호출 실패 | try/except로 graceful 실패, 실패해도 기존 시스템 영향 없음 |
| 타임아웃 | 5분 내 분석 미완 | 부분 결과라도 저장, 다음날 재시도 |
