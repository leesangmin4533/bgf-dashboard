# Plan: claude-auto-respond Claude CLI 호출 실패 수정 (claude-respond-fix)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: scheduling.md#claude-auto-respond-claude-cli-호출-실패
> 마일스톤 기여: 운영 자동화 신뢰성 — MEDIUM

---

## 1. 문제 정의

### 현상
2026-04-06 23:58 스케줄(claude-auto-respond)이 실행됐으나 실패.

```
2026-04-07 00:00:06 | WARNING | src.infrastructure.claude_responder
[AutoRespond] Claude 호출 실패: claude exit=1: (no stderr)
```

- 23:55:02 OpsIssueDetector는 정상 동작 (5건 감지/카카오 알림/pending_issues.json 저장)
- 23:58 트리거 → 약 2분 지연 후 00:00:06에 ClaudeResponder 실행
- `claude` CLI exit code 1, stderr 비어있음 → 원인 불명

### 영향
- 이상 감지 시 L1 자동 원인 분석이 작동하지 않음
- 매일 23:58 스케줄(claude-auto-respond) 무력화 상태
- DailyChainReport에 분석 결과 누락 → 운영자가 수동 분석해야 함

### 근본 원인 (가설)
`src/infrastructure/claude_responder.py:113-117`

```python
if result.returncode != 0:
    stderr = (result.stderr or "").strip()
    raise RuntimeError(
        f"claude exit={result.returncode}: {stderr[:200] if stderr else '(no stderr)'}"
    )
```

stderr가 비어있어 원인 추적 불가. 가설:
1. **stdout에 에러 메시지 출력** — `claude -p`가 인증/권한/모델 오류를 stdout으로 보낼 가능성
2. **Windows 환경 PATH/실행 권한** — 스케줄러 컨텍스트(unattended)에서 `claude.cmd` shim 미인식
3. **`--allowed-tools` 플래그 형식 변경** — 최신 CLI에서 공백 구분이 아닌 콤마/반복 옵션 요구
4. **세션 만료/로그인 필요** — 비대화형 세션에서 OAuth 재인증 트리거
5. **prompt 인코딩** — Windows cp949 ↔ UTF-8 충돌

---

## 2. 목표

### 1차 목표 (디버깅 가능성 확보)
- 실패 원인 식별 가능한 진단 정보를 로그에 남긴다
- 재발 시 stderr 외에 stdout / 명령어 / 환경변수도 기록

### 2차 목표 (수정)
- 실제 원인을 파악하여 ClaudeResponder가 정상 동작하도록 수정
- 다음 23:58 스케줄에서 분석 결과 파일(`data/auto_respond/{date}_response.md`) 생성 확인

### 비목표
- claude-auto-respond 기능 자체의 재설계
- 분석 prompt 품질 개선 (별도 작업)

---

## 3. 범위

### 대상 파일
- `src/infrastructure/claude_responder.py` — `_call_claude()` 진단 강화
- `tests/test_claude_responder.py` — 실패 케이스 회귀 테스트 추가

### 검증 방법
1. **수동 재현**: 동일 환경에서 `python -c "from src.infrastructure.claude_responder import ClaudeResponder; ClaudeResponder().respond_if_needed()"` 실행
2. **stdout/stderr/exit code 캡처**: 강화된 로깅으로 원인 식별
3. **04-08 23:58 스케줄 검증**: `data/auto_respond/2026-04-08_response.md` 생성 확인
4. **회귀 테스트**: `pytest tests/test_claude_responder.py -v`

---

## 4. 우선순위 / 단계

| 단계 | 작업 | 우선순위 |
|------|------|---------|
| 1 | `_call_claude()` 진단 로깅 강화 (stdout/cmd/env 기록) | P1 |
| 2 | 수동 재현으로 실제 원인 식별 | P1 |
| 3 | 원인별 수정 (PATH / 플래그 / 인코딩 / 인증) | P1 |
| 4 | 회귀 테스트 추가 | P2 |
| 5 | 04-08 23:58 검증 + 이슈체인 갱신 | P2 |

---

## 5. 리스크

- **재현 어려움**: stderr 없이 exit=1만 남아 무엇이 문제인지 알 수 없음 → 진단 로깅 강화가 선결
- **Claude CLI 버전 변경**: 최신 버전에서 플래그 호환성 깨졌을 가능성 → CLI `--help` 대조 필요
- **Windows 스케줄러 컨텍스트**: 사용자 세션과 다른 환경변수일 수 있음 → `os.environ` 전체 기록
- **인증 만료**: 비대화형 환경에서 재로그인 불가 → 별도 인증 체크 필요할 수도

---

## 6. 성공 조건

- [ ] `_call_claude()` 실패 시 stdout/cmd/cwd/key env가 로그에 남는다
- [ ] 실제 원인이 이슈체인에 기록된다
- [ ] `python run_scheduler.py` 컨텍스트에서 ClaudeResponder가 성공한다
- [ ] 04-08 또는 다음 발생 시 `data/auto_respond/{date}_response.md` 파일이 생성된다
- [ ] 회귀 테스트가 실패 케이스를 커버한다

---

## 7. 다음 단계

`/pdca design claude-respond-fix` — Design 문서 작성
