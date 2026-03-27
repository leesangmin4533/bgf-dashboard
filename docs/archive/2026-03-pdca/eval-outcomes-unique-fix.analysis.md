# Gap Analysis: eval-outcomes-unique-fix

> **Date**: 2026-03-24 | **Match Rate**: 100% | **Status**: PASS

## Bug Summary

eval_outcomes / order_fail_reasons 테이블의 UNIQUE 제약이 schema.py DDL과 repository 코드의 ON CONFLICT 절 사이에 불일치.
- schema.py: `UNIQUE(eval_date, item_cd)` (2컬럼)
- repo 코드: `ON CONFLICT(store_id, eval_date, item_cd)` (3컬럼)
- 47863.db가 schema.py DDL로 생성되어 2컬럼 UNIQUE → ON CONFLICT 실패

## Fix Applied

| # | Item | Status |
|---|------|--------|
| 1 | schema.py DDL: eval_outcomes UNIQUE → 3컬럼 | MATCH |
| 2 | schema.py DDL: order_fail_reasons UNIQUE → 3컬럼 | MATCH |
| 3 | `_fix_eval_outcomes_unique()` 마이그레이션 함수 | MATCH |
| 4 | `_fix_order_fail_reasons_unique()` 마이그레이션 함수 | MATCH |
| 5 | `_apply_store_column_patches()`에서 호출 | MATCH |
| 6 | 47863.db 마이그레이션 실행 검증 | MATCH |
| 7 | 3매장 모두 3컬럼 UNIQUE 확인 | MATCH |
| 8 | ON CONFLICT UPSERT 기능 검증 | MATCH |
| 9 | 전체 코드베이스 UNIQUE/ON CONFLICT 교차감사 (30+건, 0 불일치) | MATCH |

## Cross-Audit Results

전체 repository ON CONFLICT 절 30+건 vs schema.py DDL 교차감사: **0건 불일치**

## Test Results

| Category | Result |
|----------|--------|
| eval_outcome 관련 | 1 passed |
| schema 관련 (pre-existing 제외) | 19 passed |
| ON CONFLICT 기능 테스트 | PASS |
| Pre-existing: test_schema_version (64≠67) | 기존 실패 (무관) |

## Match Rate: 100%
