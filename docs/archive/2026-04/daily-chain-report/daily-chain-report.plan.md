# Plan: 일일 체인 리포트 — 파이프라인 통합 + 이슈체인 자동 등록 (daily-chain-report)

> 작성일: 2026-04-05
> 상태: Plan

---

## 1. 문제 정의

현재 23:55~23:58 3개 스케줄이 독립 실행되어 서로의 결과를 모름.
카카오 알림도 각각 발송(최대 3건/일). 통합 리포트 없음.
Claude 분석 결과가 이슈체인에 반영되지 않음.

## 2. 변경 방향

1. `pending_issues.json`을 **파이프라인 공유 상태 파일**로 확장 (각 단계가 결과 추가)
2. 마지막 단계에서 **통합 리포트** 1건만 카카오 발송
3. Claude 분석에서 새 문제 발견 시 **이슈체인 자동 등록**

## 3. 파이프라인 흐름

```
23:55  OpsIssueDetector → pending_issues.json (anomalies 기록)
23:56  MilestoneTracker → pending_issues.json (milestone 병합)
23:58  ClaudeResponder → pending_issues.json (analysis 병합) + response.md 저장
00:02  DailyChainReport → 통합 리포트 생성 + 카카오 1건 발송
                        → 문제 발견 시 이슈체인 등록
                        → pending_issues.json 삭제
```

## 4. 이슈체인 자동 등록 조건

Claude 분석 결과에서 **새 문제**를 발견했을 때만 등록:
- 마일스톤 KPI NOT_MET인데 관련 PLANNED 이슈가 없는 경우
- Claude가 "신규 이슈 제안"으로 명시한 경우 (구조화된 JSON 출력)

기존 IssueChainWriter를 재사용 (중복/쿨다운 검사 포함).
