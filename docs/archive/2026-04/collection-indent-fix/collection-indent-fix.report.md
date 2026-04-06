# collection.py 들여쓰기 복구 — Hotfix Report

**Date**: 2026-04-07
**Type**: Hotfix (ad-hoc, no plan/design phase)
**Commit**: 20f0de7
**Match Rate**: 100%

## 문제
2026-04-07 07:00 스케줄 `daily_order` 4매장(46513/46704/47863/49965) 모두 즉시 실패.

```
Error: expected an indented block after 'with' statement on line 60 (collection.py, line 63)
```

각 매장 0.1~1.5초 만에 ERROR 종료 → 그날 자동 발주 전면 스킵.

## 원인
`src/scheduler/phases/collection.py` line 58-102 — collect_only 모드 분기(`if/else`) 추가 리팩토링 시
`else` 분기의 Phase 1.0 `with phase_timer(...)` 본문(line 63~102)을 들여쓰기하지 않음.
with 블록이 비어 있는 상태로 커밋되어 모든 매장 import 단계에서 SyntaxError.

## 수정
- `with phase_timer(...)` 헤더 연속 줄 정렬
- 본문 40줄(dates_to_collect ~ logger.info) 한 단계 들여쓰기
- `python -m ast` 파싱 통과 확인

## 검증
- Syntax: `ast.parse` OK
- 런타임: `python run_scheduler.py --now` 재실행
  - 46513: **74건 발주 성공 / 0 실패** (609s)
  - 46704/47863/49965: 병렬 진행 정상

## 교훈
- `if/else` 추가 리팩토링 시 기존 본문 들여쓰기 누락은 파이썬 SyntaxError로 즉시 import 실패 → 단위 테스트로도 못 잡음
- **권장**: pre-commit에 `python -m compileall src/` 또는 ruff 추가 (이런 오류는 커밋 단계에서 차단되어야 함)
- CLAUDE.md "후행 덮어쓰기 방지" 체크리스트에 **"기존 블록의 들여쓰기 레벨 변경 여부"** 항목 추가 고려

## 영향 파일
- `src/scheduler/phases/collection.py` (+40/-40)
