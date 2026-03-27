# PDCA Completion Report: dessert-data-fix

> **Feature**: dessert-data-fix (디저트 대시보드 데이터 정합성 수정)
>
> **Date**: 2026-03-04
> **Match Rate**: 100%
> **Tests**: 13 passed (10 original + 3 auto-confirm)
> **Status**: COMPLETED

---

## 1. Executive Summary

디저트 대시보드에 표시되는 데이터가 실제 DB/서비스와 불일치하는 문제를 해결했다.
코드 버그 3건 수정, 테스트 데이터 정리, 30일 미판매 자동확인 로직 추가, 대시보드 UI 필터 개선까지 완료.

### 성과 요약

| 항목 | Before | After |
|------|--------|-------|
| 주간추이 차트 | 모든 데이터 W0 집계 (단일 포인트) | 정상 주차별 분리 |
| 미확인 STOP 카운트 | 0 반환 (실제 14건) | 정확한 카운트 |
| 일괄 운영자 액션 | 과거 레코드 대상 (잘못된 기간) | 최신 기간 대상 |
| STOP 미확인 목록 | 116건 (전부 수동 확인 필요) | **14건** (30일 미판매 102건 자동처리) |
| 대시보드 상품 목록 | 174개 전부 표시 | 미확인 72개만 기본 표시 (토글로 전체 확인 가능) |

---

## 2. Problem & Root Cause

### 2.1 코드 버그 (3건)

| # | 버그 | 위치 | 근본 원인 |
|---|------|------|----------|
| 1 | `strftime('%%W', ...)` | `dessert_decision_repo.py` L367 | Python `?` 바인딩에서 `%`는 특수문자 아님. `%%W`가 SQLite에 그대로 전달 → 리터럴 `%W` → `CAST('%W' AS INTEGER)` = 0 |
| 2 | `MAX(id)` in `get_pending_stop_count()` | L401-414 | 삽입 순서 ≠ 시간순. 과거 레코드가 높은 ID를 가질 수 있음 |
| 3 | `MAX(id)` in `batch_update_operator_action()` | L309-323 | 위와 동일. 일괄 처리 대상이 과거 레코드 |

### 2.2 운영 비효율

- STOP_RECOMMEND 116건 중 대부분(102건)은 30일간 판매 0 → 운영자 확인이 불필요한 뻔한 상품
- 대시보드에 자동확인된 상품까지 모두 표시 → 미확인 항목 식별 어려움

---

## 3. Solution

### Phase 1: 코드 버그 수정 (Fix 1/2/3)

**Fix 1**: `strftime('%%W', ...)` → `strftime('%W', ...)`
- 1줄 수정, 주간추이 차트 정상화

**Fix 2/3**: `MAX(id)` → `MAX(judgment_period_end)` + JOIN 기준 변경
- `get_pending_stop_count()`: 서브쿼리 3줄 수정
- `batch_update_operator_action()`: 서브쿼리 3줄 수정
- 전체 11개 메서드가 `MAX(judgment_period_end)` 기준으로 통일

### Phase 2: 30일 미판매 자동확인

**`DessertDecisionService._auto_confirm_zero_sales()`**
- `run()` 끝에서 호출 (save_decisions_batch 직후)
- 단일 쿼리로 "30일 내 판매 있는 상품" 조회 → 나머지가 미판매
- 기존 `batch_update_operator_action()` 재사용
- `operator_note`에 "auto: 30일 미판매 자동확인" 기록
- 매장 독립 (store_id 기반 자동 격리)

### Phase 3: 대시보드 UI 필터

**`dessert.js` 수정 (4개소)**
1. `_filter` 초기값에 `hideProcessed: true` 추가
2. `_getFilteredData()`에 `operator_action` 존재 시 필터 로직 추가
3. `renderFilters()`에 "처리됨 포함 (N)" / "미확인만" 토글 버튼 추가
4. 카테고리 핸들러 셀렉터를 `[data-cat]`으로 제한 (토글 버튼 충돌 방지)

**`dessert.css` 수정 (1개소)**
- `.dessert-filter-toggle` 스타일 추가

---

## 4. Modified Files

| # | 파일 | 변경 유형 | 변경량 |
|---|------|----------|--------|
| 1 | `src/infrastructure/database/repos/dessert_decision_repo.py` | 버그 수정 3개소 | ~10줄 |
| 2 | `src/application/services/dessert_decision_service.py` | 자동확인 메서드 추가 + run() 수정 | ~35줄 |
| 3 | `src/web/static/js/dessert.js` | 필터 로직 + 토글 UI | ~25줄 |
| 4 | `src/web/static/css/dessert.css` | 토글 버튼 스타일 | ~8줄 |
| 5 | `tests/test_dessert_data_fix.py` | 신규 테스트 13개 | ~400줄 |

---

## 5. Test Results

```
tests/test_dessert_data_fix.py — 13 passed in 6.06s
```

| Class | Tests | Coverage |
|-------|:-----:|----------|
| `TestWeeklyTrend` | 3 | Fix 1: 주차 분리, W0 아님, 빈 데이터 |
| `TestPendingStopCount` | 3 | Fix 2: 역순 ID, STOP 없음, actioned 제외 |
| `TestBatchUpdateOperatorAction` | 3 | Fix 3: 최신 기간 대상, 과거 무시, 빈 입력 |
| `TestCrossValidation` | 1 | summary vs pending_count 일관성 |
| `TestAutoConfirmZeroSales` | 3 | 자동확인, 판매 있으면 스킵, STOP 없으면 빈 리스트 |

### Regression

- 전체 테스트 3159개 통과 (기존 3개 실패는 본 작업과 무관)

---

## 6. Verification (실제 실행 결과)

### 46513 매장

| 항목 | 값 |
|------|-----|
| 전체 판단 | 174개 |
| KEEP | 49 |
| WATCH | 9 |
| STOP_RECOMMEND | 116 |
| 자동확인 (30일 미판매) | **102건** → CONFIRMED_STOP |
| 미확인 (운영자 확인 필요) | **14건** |

### 46704 매장

| 항목 | 값 |
|------|-----|
| 전체 판단 | 174개 |
| STOP_RECOMMEND | 113 |
| 자동확인 | **103건** |
| 미확인 | **10건** |

### 대시보드 UI

- 기본 뷰: 72개 표시 (미확인만) ✅
- 토글 "처리됨 포함": 174개 전체 표시 ✅
- 탭 뱃지: 14 (미확인 수) ✅
- STOP 카드: "미확인 14건" 서브텍스트 ✅

---

## 7. Gap Analysis

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 100% | PASS |
| **Overall** | **100%** | **PASS** |

38개 비교 항목 전수 검증 완료. 설계 대비 구현 차이 0건.

---

## 8. Impact Analysis

| 영역 | 영향 |
|------|------|
| DB 스키마 | 변경 없음 (v52 유지) |
| API | 동일 엔드포인트, 응답값 정확도 향상 |
| OrderFilter | 자동확인된 CONFIRMED_STOP → 즉시 발주 차단 |
| 신규 매장 | 코드 레벨 store-agnostic, 추가 설정 불필요 |
| 기존 테스트 | 영향 없음 (3159개 통과) |

---

## 9. Lessons Learned

### 9.1 `%%` vs `%` in SQLite strftime

Python의 `conn.execute(sql, params)` 에서 `?` 파라미터 바인딩 사용 시 SQL 문자열 내 `%`는 특수문자가 아니다. `string % (args)` 포맷팅이 아니므로 `%%`로 이스케이프할 필요 없음. `%%W`는 SQLite에 리터럴 `%%W`로 전달되어 결과가 항상 0.

### 9.2 MAX(id) vs MAX(judgment_period_end)

`AUTO_INCREMENT` ID는 삽입 순서를 보장하지만, 시간순 최신 레코드를 보장하지 않는다. 과거 데이터를 나중에 삽입하면 최신 ID가 과거 기간을 가리킨다. 비즈니스 의미가 있는 컬럼(`judgment_period_end`)을 기준으로 사용해야 한다.

### 9.3 자동확인으로 운영자 부담 경감

116건 STOP 중 102건(88%)이 30일 미판매로 자동확인 → 운영자는 14건만 확인. 명확한 기준의 자동화가 운영 효율을 크게 향상시킨다.

---

## 10. PDCA Cycle Summary

```
[Plan] ✅ → [Design] ✅ → [Do] ✅ → [Check] ✅ → [Report] ✅
                                     100%
```

| Phase | Date | Duration | Output |
|-------|------|----------|--------|
| Plan | 2026-03-04 | ~10min | `dessert-data-fix.plan.md` |
| Design | 2026-03-04 | ~15min | `dessert-data-fix.design.md` |
| Do | 2026-03-04 | ~30min | 3 fixes + 13 tests + auto-confirm + UI filter |
| Check | 2026-03-04 | ~10min | 100% match rate, 38/38 items |
| Report | 2026-03-04 | - | `dessert-data-fix.report.md` |

**Total PDCA Duration**: ~65min

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-03-04 | Initial report | report-generator |
