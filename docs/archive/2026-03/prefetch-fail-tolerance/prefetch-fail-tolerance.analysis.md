# prefetch-fail-tolerance Analysis Report

> **Analysis Type**: Gap Analysis (Plan vs Implementation)
>
> **Project**: BGF Auto Order System
> **Version**: DB Schema v51
> **Analyst**: gap-detector
> **Date**: 2026-03-04
> **Plan Doc**: [prefetch-fail-tolerance.plan.md](../../01-plan/features/prefetch-fail-tolerance.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Plan 문서(prefetch-fail-tolerance.plan.md)와 실제 구현 코드 간 Gap 분석.
1회 조회 실패 시 즉시 미취급 마킹하던 기존 동작을 3회 연속 실패 시에만 마킹하도록 변경하는 기능의 완성도를 검증한다.

### 1.2 Analysis Scope

- **Plan Document**: `docs/01-plan/features/prefetch-fail-tolerance.plan.md`
- **Implementation Files**:
  - `src/infrastructure/database/repos/inventory_repo.py`
  - `src/collectors/order_prep_collector.py`
  - `src/db/models.py`
  - `src/settings/constants.py`
  - `tests/test_prefetch_fail_tolerance.py`
- **Analysis Date**: 2026-03-04

---

## 2. Gap Analysis (Plan vs Implementation)

### 2.1 DB Schema Changes

| Plan | Implementation | Status | Notes |
|------|---------------|--------|-------|
| `query_fail_count` INTEGER DEFAULT 0 | `ALTER TABLE realtime_inventory ADD COLUMN query_fail_count INTEGER DEFAULT 0` (models.py:1561) | Match | |
| `unavail_reason` TEXT DEFAULT NULL | `ALTER TABLE realtime_inventory ADD COLUMN unavail_reason TEXT` (models.py:1562) | Match | |
| 마이그레이션 위치: "schema.py" | 실제 위치: `src/db/models.py` SCHEMA_MIGRATIONS[51] | Minor | Plan은 "schema.py" 언급, 실제 프로젝트 관례는 models.py. 동작 동일 |
| DB_SCHEMA_VERSION 증가 | constants.py:241 `DB_SCHEMA_VERSION = 51` | Match | v50 -> v51 |

### 2.2 inventory_repo.py Changes

| Plan | Implementation | Status | Notes |
|------|---------------|--------|-------|
| `UNAVAILABLE_FAIL_THRESHOLD = 3` 상수 | Line 19: `UNAVAILABLE_FAIL_THRESHOLD = 3` | Match | |
| `save()` 성공 시 `query_fail_count=0` 리셋 | Line 74: `query_fail_count = 0` in UPSERT | Match | |
| `save()` 성공 시 `unavail_reason=NULL` 리셋 | Line 75: `unavail_reason = NULL` in UPSERT | Match | |
| `save_bulk()` 성공 시 동일 리셋 | Lines 135-136: `query_fail_count = 0, unavail_reason = NULL` | Match | Plan에 명시적 언급 없으나 일관성 확보 |
| `mark_unavailable()` 에 `unavail_reason='manual'` | Lines 691-695: INSERT/UPSERT with `unavail_reason = 'manual'` | Match | |
| `increment_fail_count(item_cd, store_id)` 신규 메서드 | Lines 707-769: 완전 구현 | Match | |
| increment: fail_count += 1 | Line 730: `COALESCE(realtime_inventory.query_fail_count, 0) + 1` | Match | COALESCE로 NULL 방어 추가 (Plan 대비 개선) |
| fail_count >= 3 -> is_available=0, unavail_reason='query_fail' | Lines 737-745: 별도 UPDATE 쿼리로 임계값 판정 | Match | |

### 2.3 order_prep_collector.py Changes

| Plan | Implementation | Status | Notes |
|------|---------------|--------|-------|
| `mark_unavailable(item_cd, store_id)` -> `increment_fail_count(item_cd, store_id)` | Line 909: `self._repo.increment_fail_count(item_cd, store_id=self.store_id)` | Match | |
| 주석 갱신 | Line 907: `# 조회 실패 시 실패 카운트 증가 (3회 연속 시 미취급 마킹)` | Match | 의도 명확 |

### 2.4 Implementation Extras (Plan에 없으나 구현됨)

| Item | Location | Description | Impact |
|------|----------|-------------|--------|
| COALESCE 방어 | inventory_repo.py:730 | `COALESCE(query_fail_count, 0) + 1` -- 기존 레코드의 NULL 방어 | Positive (안정성) |
| 로깅 | inventory_repo.py:750-767 | 실패 횟수/임계값 도달 시 로그 출력 (warning/info 분리) | Positive (운영 가시성) |
| UPSERT 신규 상품 처리 | inventory_repo.py:724-734 | DB에 없는 상품도 INSERT로 자동 생성 (is_available=1, fail_count=1) | Positive (엣지케이스 대응) |
| save_bulk() 리셋 | inventory_repo.py:135-136 | 벌크 저장 시에도 동일 리셋 적용 | Positive (일관성) |

### 2.5 Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 100%                    |
+---------------------------------------------+
|  Match:            12 items (100%)           |
|  Missing design:    0 items (0%)             |
|  Not implemented:   0 items (0%)             |
|  Extras (positive): 4 items                  |
+---------------------------------------------+
```

---

## 3. Test Coverage Analysis

### 3.1 Plan Test Cases vs Implementation

| # | Plan Test Name | Implementation | Status |
|---|---------------|----------------|--------|
| 1 | test_first_fail_stays_available | `TestIncrementFailCount::test_first_fail_stays_available` (L95) | Match |
| 2 | test_second_fail_stays_available | `TestIncrementFailCount::test_second_fail_stays_available` (L107) | Match |
| 3 | test_third_fail_marks_unavailable | `TestIncrementFailCount::test_third_fail_marks_unavailable` (L120) | Match |
| 4 | test_success_resets_fail_count | `TestSuccessResetsFailCount::test_success_resets_fail_count` (L164) | Match |
| 5 | test_intermittent_fail_resets | `TestSuccessResetsFailCount::test_intermittent_fail_resets` (L202) | Match |
| 6 | test_mark_unavailable_still_works | `TestMarkUnavailableManual::test_mark_unavailable_still_works` (L243) | Match |
| 7 | test_save_resets_after_threshold | `TestSuccessResetsFailCount::test_save_resets_after_threshold` (L183) | Match |
| 8 | test_unavail_reason_distinguishes | `TestMarkUnavailableManual::test_unavail_reason_distinguishes` (L254) | Match |

### 3.2 Extra Tests (Plan 범위 초과)

| # | Test Name | Coverage Area | Value |
|---|-----------|---------------|-------|
| 9 | test_threshold_constant_is_3 | 상수 검증 | 임계값 하드코딩 방지 |
| 10 | test_new_item_first_fail | 엣지케이스: DB 미등록 상품 첫 실패 | UPSERT 정합성 |
| 11 | test_fourth_fail_stays_unavailable | 경계값 초과 | 임계값 이후 계속 미취급 유지 |
| 12 | test_save_bulk_resets_fail_count | save_bulk 리셋 | 벌크 API 일관성 |
| 13 | test_mark_unavailable_new_item | 엣지케이스: 미등록 상품 수동 마킹 | UPSERT 정합성 |
| 14 | test_collector_calls_increment_on_failure | 통합: collector -> repo 호출 체인 | 실제 호출 경로 검증 |

### 3.3 Test Coverage Score

```
+---------------------------------------------+
|  Test Coverage Score: 100%                   |
+---------------------------------------------+
|  Plan test cases:   8/8 implemented (100%)   |
|  Extra tests:       6 (bonus coverage)       |
|  Total tests:       14 passing               |
+---------------------------------------------+
```

---

## 4. Code Quality Analysis

### 4.1 Complexity

| File | Method | Complexity | Status | Notes |
|------|--------|:----------:|:------:|-------|
| inventory_repo.py | increment_fail_count | Low | Good | 선형 흐름: INSERT/UPSERT -> threshold CHECK -> logging |
| inventory_repo.py | save (UPSERT) | Low | Good | 기존 구조에 2개 필드 추가 |
| inventory_repo.py | mark_unavailable | Low | Good | 기존 구조에 unavail_reason 추가 |

### 4.2 Code Quality Notes

| Aspect | Finding | Status |
|--------|---------|:------:|
| SQL Injection 방어 | 파라미터 바인딩(`?`) 사용 | Good |
| NULL 방어 | `COALESCE(query_fail_count, 0)` | Good |
| 커넥션 관리 | try/finally + conn.close() 패턴 | Good |
| 로깅 | warning(임계값 도달) / info(진행중) 분리 | Good |
| 상수 관리 | `UNAVAILABLE_FAIL_THRESHOLD` 모듈 레벨 상수 | Good |
| 2-step UPDATE | INSERT/UPSERT 후 별도 UPDATE로 임계값 판정 -- 원자성은 commit으로 보장 | Acceptable |

### 4.3 Potential Improvements (non-blocking)

| Item | Description | Priority |
|------|-------------|----------|
| 로깅 중 SELECT | increment_fail_count 내에서 commit 후 SELECT 수행 (L751-757). conn은 이미 close 직전이므로 문제 없으나, commit 전 SELECT로 이동하면 더 안전 | Low |
| 상수 위치 중복 | `UNAVAILABLE_FAIL_THRESHOLD`가 inventory_repo.py 모듈 레벨에 정의. constants.py에 중앙화할 수도 있으나, 현재 사용처가 이 파일뿐이므로 적절 | Info |

---

## 5. Architecture Compliance

### 5.1 Layer Placement

| Component | Expected Layer | Actual Location | Status |
|-----------|---------------|-----------------|:------:|
| `increment_fail_count()` | Infrastructure (DB) | `src/infrastructure/database/repos/inventory_repo.py` | Match |
| `UNAVAILABLE_FAIL_THRESHOLD` | Infrastructure (module constant) | `src/infrastructure/database/repos/inventory_repo.py:19` | Match |
| `SCHEMA_MIGRATIONS[51]` | Infrastructure (DB schema) | `src/db/models.py:1559` | Match |
| `DB_SCHEMA_VERSION = 51` | Settings | `src/settings/constants.py:241` | Match |
| Collector 호출부 | Infrastructure (Collectors) | `src/collectors/order_prep_collector.py:909` | Match |

### 5.2 Dependency Direction

```
order_prep_collector (Infrastructure/Collectors)
    --> RealtimeInventoryRepository.increment_fail_count (Infrastructure/DB)
         --> UNAVAILABLE_FAIL_THRESHOLD (same module constant)
```

Dependency direction: Infrastructure -> Infrastructure (same layer). No violations.

---

## 6. Convention Compliance

| Convention | Check | Status |
|-----------|-------|:------:|
| snake_case 함수명 | `increment_fail_count` | Match |
| UPPER_SNAKE 상수 | `UNAVAILABLE_FAIL_THRESHOLD` | Match |
| 한글 주석 | 전체 한글 docstring/주석 사용 | Match |
| logger 사용 (print 금지) | `logger.warning()`, `logger.info()` | Match |
| Repository 패턴 | BaseRepository 상속, try/finally | Match |
| DB 스키마 버전 규칙 | constants.py 증가 + models.py SCHEMA_MIGRATIONS 추가 | Match |

---

## 7. Overall Score

```
+---------------------------------------------+
|  Overall Score: 100/100                      |
+---------------------------------------------+
|  Plan Match:            100% (12/12 items)   |
|  Test Coverage:         100% (8/8 + 6 extra) |
|  Code Quality:          95%  (minor notes)   |
|  Architecture:          100% (no violations) |
|  Convention:            100% (all compliant) |
+---------------------------------------------+
```

| Category | Score | Status |
|----------|:-----:|:------:|
| Plan-Implementation Match | 100% | PASS |
| Test Coverage | 100% | PASS |
| Code Quality | 95% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## 8. File-Level Change Summary

| # | File | Plan Change | Implemented | Lines |
|---|------|-------------|:-----------:|-------|
| 1 | `src/infrastructure/database/repos/inventory_repo.py` | UNAVAILABLE_FAIL_THRESHOLD 상수 | Yes | 19 |
| 2 | `src/infrastructure/database/repos/inventory_repo.py` | save() UPSERT 리셋 | Yes | 74-75 |
| 3 | `src/infrastructure/database/repos/inventory_repo.py` | save_bulk() UPSERT 리셋 | Yes | 135-136 |
| 4 | `src/infrastructure/database/repos/inventory_repo.py` | mark_unavailable() unavail_reason | Yes | 691-695 |
| 5 | `src/infrastructure/database/repos/inventory_repo.py` | increment_fail_count() 신규 | Yes | 707-769 |
| 6 | `src/collectors/order_prep_collector.py` | mark_unavailable -> increment_fail_count | Yes | 909 |
| 7 | `src/db/models.py` | SCHEMA_MIGRATIONS[51] | Yes | 1559-1563 |
| 8 | `src/settings/constants.py` | DB_SCHEMA_VERSION = 51 | Yes | 241 |
| 9 | `tests/test_prefetch_fail_tolerance.py` | 8 planned + 6 extra tests | Yes | 1-312 |

---

## 9. Recommended Actions

### 9.1 Immediate: None Required

Plan의 모든 요구사항이 구현 완료. 누락 항목 없음.

### 9.2 Optional Improvements (backlog)

| Priority | Item | File | Notes |
|----------|------|------|-------|
| Low | 로깅 SELECT 순서 | inventory_repo.py:751 | commit 전 SELECT로 이동 가능 (현재도 정상 동작) |
| Info | Plan 문서 파일명 보정 | plan.md Section 4 | "schema.py" -> "models.py" 표기 (관례상 models.py가 정확) |

### 9.3 Design Document Updates

- [ ] Plan 문서 Section 4에서 `src/infrastructure/database/schema.py` -> `src/db/models.py` 수정 (실제 마이그레이션 위치)
- [ ] Plan 문서에 save_bulk() 리셋 명시 추가 (현재 save()만 언급)

---

## 10. Conclusion

prefetch-fail-tolerance 기능은 Plan 문서의 **모든 요구사항을 100% 충족**한다.

- 3회 연속 실패 임계값 (`UNAVAILABLE_FAIL_THRESHOLD = 3`) 정상 적용
- 성공 조회 시 fail_count / unavail_reason 리셋 정상 동작
- `query_fail` vs `manual` 사유 구분 가능
- Plan에서 명시하지 않은 엣지케이스(신규 상품, save_bulk, 4회+ 실패)까지 추가 커버
- 14개 테스트 전수 통과

Match Rate >= 90% -- **Check 단계 완료. Report 생성 가능.**

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-04 | Initial gap analysis | gap-detector |
