# 스케줄링 이슈 체인

> 최종 갱신: 2026-04-06
> 현재 상태: executed_at 수정 완료 + GHOST_STOCK 승격/하네스 Week3 계획

---

## [WATCHING] action_proposals v70 컬럼 매장 DB 미적용 (04-05 ~ )

**문제**: v70 마이그레이션(executed_at, verified_at, verified_result)이 common.db SCHEMA_MIGRATIONS에만 있고 _STORE_COLUMN_PATCHES에 누락 → 매장 DB에 미적용
**영향**: ExecutionVerifier.verify_previous_day()가 매일 4매장에서 "no such column: executed_at" 오류 → 자전 시스템 4고리(검증) 전체 무력화
**설계 의도**: 매장 DB 스키마 변경은 반드시 _STORE_COLUMN_PATCHES에도 동기화. SCHEMA_MIGRATIONS(common.db)와 이중 관리 필요.

### 시도 1: _STORE_COLUMN_PATCHES에 3개 컬럼 ADD 추가 + 기존 DB 직접 보정 (2bd24bf, 04-05)
- **왜**: 근본 원인이 _STORE_COLUMN_PATCHES 누락이므로 직접 추가
- **결과**: 4매장 DB 컬럼 추가 확인, 테스트 13개 통과

> 변경 내용은 `git show 2bd24bf`로 확인.

### 교훈
- **매장 DB 스키마 변경 시 2곳 동기화 필수**: `models.py SCHEMA_MIGRATIONS` + `schema.py _STORE_COLUMN_PATCHES`
- SCHEMA_MIGRATIONS은 common.db(레거시)에만 적용됨. 매장 store DB는 _STORE_COLUMN_PATCHES 경로로만 컬럼이 추가됨
- schema_version이 72까지 올라가도 실제 컬럼이 없을 수 있음 (version 숫자와 실제 스키마 불일치 가능)

### 해결: _STORE_COLUMN_PATCHES 동기화 (2bd24bf, 04-05)
- 검증:
  - [ ] 내일(04-06) 07:00 로그에서 [Verify] executed_at 오류 소멸 확인 (스케줄: eb6c54ca)

---

## [PLANNED] CLEAR_GHOST_STOCK 자동실행 승격 검토 (P2)

**목표**: 유령 재고 보정(CLEAR_GHOST_STOCK)을 LOW(승인 필요) → HIGH(자동 실행)로 승격
**동기**: 현재 LOW 분류라 매번 카카오 승인 필요. 2주 오탐률 확인 후 안전하면 자동화
**선행조건**: integrity_checks 2주 누적 데이터에서 food_ghost_stock 오탐률 < 5%
**예상 영향**: constants.py AUTO_EXEC_HIGH/LOW, auto_executor.py _execute_action()

---

## [PLANNED] 하네스 엔지니어링 Week 3 — AI 요약 서비스 (P2)

**목표**: integrity 체크 + 발주 결과를 규칙 기반 템플릿으로 요약해서 카카오 리포트에 포함
**동기**: 매일 카카오 알림이 원시 데이터만 전달 → 의사결정에 필요한 요약 부족
**선행조건**: executed_at 검증 완료 (WATCHING 이슈 해결)
**예상 영향**: schema.py (ai_summaries DDL), notification/summary_report_service.py (신규), daily_job.py Phase 3, kakao_notifier.py

## [PLANNED] 자전 시스템 미해결 항목 (expiry_time_mismatch) (P1)

**목표**: 체크 expiry_time_mismatch이(가) 31일 연속 anomaly 발생 중 (1개 항목)
**동기**: 자동 감지 (2026-04-06) -- integrity_unresolved
**선행조건**: 없음
**예상 영향**: integrity_unresolved 관련 파일


---
