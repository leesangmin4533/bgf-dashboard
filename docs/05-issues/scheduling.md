# 스케줄링 이슈 체인

> 최종 갱신: 2026-04-05
> 현재 상태: 자전 시스템 executed_at 컬럼 누락 수정 완료

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
