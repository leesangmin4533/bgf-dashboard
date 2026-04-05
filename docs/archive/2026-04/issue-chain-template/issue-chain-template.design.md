# Design: 이슈 체인 템플릿 (issue-chain-template)

> 작성일: 2026-04-05
> Plan: `docs/01-plan/features/issue-chain-template.plan.md`

## 1. 템플릿 구조

### 1.1 파일 헤더

```markdown
# {영역명} 이슈 체인

> 최종 갱신: {YYYY-MM-DD}
> 현재 상태: {한줄 요약}
```

### 1.2 이슈 블록

```markdown
## [{상태}] {이슈 제목} ({기간})

**문제**: {무엇이 잘못됐는지}
**영향**: {사용자/시스템에 미치는 영향, 가능하면 숫자}
**연관**: → {파일}#{이슈명} (다른 영역에 영향 있을 때)

### 시도 N: {한줄 요약} ({커밋해시}, {MM-DD})
- 원인/동기: {왜 이 수정을 했는지}
- 수정: {뭘 바꿨는지}
- **결과**: {성공 ✓ / 실패 ✗ + 왜 안 됐는지}
- **실패 패턴**: #{태그} (해당 시)

### 교훈
- {다음 수정자가 알아야 할 핵심 (왜 특정 접근이 안 되는지)}

### 해결: {한줄 요약} ({커밋해시}, {MM-DD})
- {최종 해결 방법}
- 검증:
  - [ ] {체크포인트 1} (예정일: {MM-DD})
  - [x] {체크포인트 2} (완료: {MM-DD})
```

### 1.3 상태 태그

| 태그 | 의미 |
|------|------|
| `[OPEN]` | 미해결 — 현재 문제가 존재 |
| `[RESOLVED]` | 해결됨 — 검증 완료 |
| `[WATCHING]` | 해결했지만 재발 가능성 모니터링 중 |

### 1.4 실패 패턴 태그 (공통)

| 태그 | 의미 | 예시 |
|------|------|------|
| `#stale-data` | 오래된 데이터로 인한 오판 | 수개월 전 stock_qty로 폴백 |
| `#data-format` | 데이터 형식 불일치 | date-only vs datetime |
| `#no-consumption-tracking` | 소진 추적 없는 데이터 소스 사용 | receiving_history에 잔량 없음 |
| `#timing-gap` | 수집 시점 간 시차 | 판매수집 07:00 vs 입고 20:00 |
| `#cross-contamination` | 매장 간 데이터 오염 | store_id 없이 API 호출 |
| `#one-shot-fix` | 일회성 수정, 자동 반복 안 됨 | 수동 스크립트 보정 |
| `#patch-on-patch` | 근본 해결 없이 패치 누적 | 교차검증 → 폴백 → 방어 |

새 태그가 필요하면 자유롭게 추가. 단, 3회 이상 반복되는 패턴만 태그화.

### 1.5 연관 이슈 링크 형식

```markdown
**연관**: → prediction.md#pending-qty-과다
**연관**: → order-execution.md#수동발주-감지-누락
```

`{파일}#{이슈 제목의 kebab-case}` 형식. 파일은 `docs/05-issues/` 기준 상대 경로.

### 1.6 검증 체크포인트

해결 후 반드시 검증 일정을 기록:

```markdown
검증:
  - [ ] D+2 첫 정확한 알림 확인 (04-07)
  - [ ] 1주일 운영 후 matched/unmatched 비율 (04-12)
  - [x] 4매장 스냅샷 저장 확인 (04-05, 314건)
```

스케줄 태스크와 연동 시 태스크 ID도 기록:
```markdown
  - [ ] Gap 분석 (스케줄 태스크: expiry-tracking-gap-analysis, 04-07)
```

## 2. 템플릿 파일

`docs/05-issues/_TEMPLATE.md`로 저장하여 새 영역 생성 시 복사.

## 3. CLAUDE.md 규칙 갱신

기존 "이슈 체인 규칙" 섹션에 추가:
- 새 이슈 체인 문서 생성 시 `_TEMPLATE.md` 복사하여 시작
- 실패 패턴 태그 필수 기입
- 해결 후 검증 체크포인트 필수 기입
- 검증 완료 시 `[RESOLVED]`, 모니터링 중이면 `[WATCHING]`

## 4. 기존 문서 리팩터링

`docs/05-issues/expiry-tracking.md`에 추가할 것:
- 실패 패턴 태그 (시도 1~5에 각각)
- 검증 체크포인트 (해결 섹션에)
- 연관 이슈 링크 (있으면)

## 5. 구현 순서

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | `_TEMPLATE.md` 생성 | `docs/05-issues/_TEMPLATE.md` |
| 2 | CLAUDE.md 규칙 갱신 | `CLAUDE.md` |
| 3 | expiry-tracking.md 리팩터링 | `docs/05-issues/expiry-tracking.md` |
| 4 | 커밋 | — |
