# Design: 일일 체인 리포트 (daily-chain-report)

> 작성일: 2026-04-05
> Plan 참조: `docs/01-plan/features/daily-chain-report.plan.md`

---

## 1. 변경 파일

```
신규 (1개):
  src/application/services/daily_chain_report.py  # 통합 리포트 + 이슈체인 등록

변경 (4개):
  src/application/services/ops_issue_detector.py  # 카카오 발송 제거 (체인 리포트로 이관)
  src/analysis/milestone_tracker.py               # pending에 milestone 병합
  src/infrastructure/claude_responder.py          # 카카오 발송 제거 + pending에 analysis 병합
  run_scheduler.py                                # 00:02 스케줄 등록

변경 없음:
  src/infrastructure/issue_chain_writer.py        # 기존 그대로 재사용
  src/notification/kakao_notifier.py              # "daily_chain" 카테고리 추가
  src/settings/constants.py                       # DAILY_CHAIN_REPORT_ENABLED 토글
```

---

## 2. pending_issues.json 확장 형식

```json
{
  "generated_at": "2026-04-06T23:55:00",
  "anomalies": [...],
  "milestone": {"K1": {...}, "K2": {...}, ...},
  "analysis": {
    "responded": true,
    "summary": "K2 미달 원인: 행사 종료 후 폐기 23건...",
    "output_path": "data/auto_respond/2026-04-06_response.md",
    "suggested_issues": [
      {
        "metric_name": "waste_rate",
        "issue_chain_file": "expiry-tracking.md",
        "title": "행사 종료 후 냉각 로직 미구현",
        "priority": "P2",
        "description": "D+1~D+7 WMA 블렌딩으로 과발주 방지"
      }
    ]
  }
}
```

핵심: `analysis.suggested_issues`가 있으면 이슈체인에 자동 등록.

---

## 3. 상세 설계

### 3.1 각 단계별 변경

**ops_issue_detector.py**: `_send_alert()` 호출을 제거하지 않되, `_save_pending_issues()`는 **항상** 실행 (anomaly 있든 없든 milestone/analysis 단계가 이어져야 하므로).

실제 변경: `unique` 조건 없이 항상 pending 저장 (anomalies가 빈 리스트여도).

**milestone_tracker.py**: `evaluate()` 결과를 pending에 병합하는 `_append_to_pending()` 추가.

**claude_responder.py**:
- 카카오 직접 발송 제거 (`_send_kakao` 호출 제거)
- 분석 결과를 pending에 병합 (`_append_analysis_to_pending()`)
- prompt에 "신규 이슈가 있으면 JSON 블록으로 제안" 지시 추가

### 3.2 daily_chain_report.py — 핵심 신규 모듈

```python
class DailyChainReport:
    """일일 파이프라인 통합 리포트"""

    def run(self) -> dict:
        """pending_issues.json 읽기 → 통합 리포트 → 이슈체인 등록 → 카카오"""
        # 1. pending 로드 (없으면 "이상 없음" 리포트)
        # 2. 통합 리포트 텍스트 생성
        # 3. suggested_issues → IssueChainWriter로 등록
        # 4. 카카오 발송 (1건)
        # 5. CLAUDE.md 이슈 테이블 동기화
        # 6. pending 삭제

    def _build_chain_report(self, data: dict) -> str:
        """통합 리포트 텍스트"""
        # [일일 체인 리포트] 04-06
        #
        # 이상 감지: 2건 (P1: 1, P2: 1)
        # 마일스톤: 3/4 달성 (K2 미달)
        # AI 분석: 행사 종료 후 폐기 23건이 원인
        # 신규 이슈: 1건 등록 (expiry-tracking.md)

    def _register_suggested_issues(self, suggested: list) -> int:
        """Claude 제안 이슈를 이슈체인에 등록"""
        # OpsAnomaly 호환 객체로 변환 후 IssueChainWriter 호출
```

### 3.3 Claude prompt 변경 (suggested_issues 출력 유도)

claude_responder._build_prompt()에 추가:

```
5. 신규 이슈가 필요하면 아래 JSON 형식으로 제안 (```json 블록):
   [{"metric_name": "...", "issue_chain_file": "...", "title": "...", "priority": "P2", "description": "..."}]
   기존 이슈체인(docs/05-issues/)에 이미 있는 내용이면 제안하지 마세요.
```

### 3.4 카카오 통합 (알림 1건화)

| 변경 전 | 변경 후 |
|---------|---------|
| 23:55 카카오: "[운영 이상 감지]" | 유지 (긴급 P1은 즉시 알림) |
| 23:58 카카오: "[AI 분석]" | **제거** (체인 리포트로 통합) |
| 00:02 없음 | **신규**: "[일일 체인 리포트]" 1건 |

P1 긴급 알림은 23:55에 즉시 발송 유지 (대기 불가). 나머지는 00:02 통합.

---

## 4. 구현 순서

| 순서 | 작업 | 파일 | 줄 |
|------|------|------|---|
| 1 | constants.py 토글 추가 | constants.py | 1줄 |
| 2 | kakao 카테고리 추가 | kakao_notifier.py | 1줄 |
| 3 | **daily_chain_report.py 신규** | src/application/services/ | ~100줄 |
| 4 | milestone_tracker _append_to_pending | milestone_tracker.py | ~15줄 |
| 5 | claude_responder 카카오 제거 + pending 병합 + prompt 변경 | claude_responder.py | ~20줄 수정 |
| 6 | ops_issue_detector pending 항상 저장 | ops_issue_detector.py | 3줄 수정 |
| 7 | run_scheduler 00:02 등록 | run_scheduler.py | ~15줄 |
| 8 | 테스트 | tests/ | ~60줄 |

---

## 5. 하위호환

| 변경 | 영향 |
|------|------|
| P1 알림 유지 | 긴급 알림 경로 변경 없음 |
| claude_responder 카카오 제거 | 00:02 통합으로 대체 |
| pending 항상 저장 | 빈 anomalies도 저장 → milestone/analysis 단계 체인 |
| IssueChainWriter 재사용 | 중복/쿨다운 검사 기존 그대로 |
