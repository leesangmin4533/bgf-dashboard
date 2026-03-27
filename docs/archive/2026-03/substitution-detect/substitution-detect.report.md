# substitution-detect Completion Report

> **Status**: Complete
>
> **Project**: BGF Retail Auto-Order System
> **Version**: v1.0.0 (2026-03-01)
> **Analyst**: report-generator
> **Completion Date**: 2026-03-01
> **PDCA Cycle**: #1

---

## 1. Executive Summary

### 1.1 Feature Overview

| Item | Details |
|------|---------|
| **Feature** | substitution-detect |
| **Type** | Prediction Enhancement / Analysis |
| **Scope** | 소분류 내 상품 대체/잠식(cannibalization) 감지 및 발주량 자동 조정 |
| **Owner** | Prediction & Analysis Team |
| **Start Date** | 2026-03-01 |
| **Completion Date** | 2026-03-01 |
| **Duration** | 1 day |
| **Effort** | 4 files created, 4 files modified, 24 tests added |

### 1.2 Results Summary

```
Completion Rate: 100%
  All Requirements:     4 / 4 implemented
  All Deliverables:     8 / 8 complete
  All Tests:           24 / 24 passing
  Design Match:        100% (66 items)
  Deferred Items:        0 / 0
```

---

## 2. Related Documents

| Phase | Document | Status | Notes |
|-------|----------|--------|-------|
| **Plan** | [substitution-detect.plan.md](../../01-plan/features/substitution-detect.plan.md) | Approved | 4 goals, algorithm overview |
| **Design** | [substitution-detect.design.md](../../02-design/features/substitution-detect.design.md) | Draft | 9 sections, 66 design items |
| **Check** | [substitution-detect.analysis.md](../../03-analysis/features/substitution-detect.analysis.md) | Complete | 100% Match Rate, 0 gaps |
| **Act** | Current document | Complete | Completion Report |

---

## 3. PDCA Cycle Summary

### 3.1 Plan Phase

**Goal**: 같은 소분류(small_cd) 내 상품 간 수요 이동(잠식)을 자동 감지하여 발주량 조정.

**Main Objectives**:
1. 잠식 감지 알고리즘 (14일 이동평균 비교, 소분류 총량 검증)
2. 발주 조정 계수 적용 (0.7~0.9)
3. substitution_events 테이블 이력 기록
4. improved_predictor.py 파이프라인 통합

**Key Decisions**:
- 소분류 총량 변화 20% 이내만 잠식으로 판정 (외부 요인 배제)
- 감소율에 따른 3단계 계수 (mild=0.9, moderate=0.8, severe=0.7)
- 피드백 유효기간 14일 (자동 만료)

### 3.2 Design Phase

**Architecture Decisions**:

| Component | Design Decision | Rationale |
|-----------|-----------------|-----------|
| SubstitutionDetector | `src/analysis/` 배치 | WasteCauseAnalyzer와 동일 패턴 |
| SubstitutionEventRepository | BaseRepository 상속, db_type="store" | 매장별 DB 격리 |
| 파이프라인 통합 | lazy-load + sentinel 패턴 | WasteFeedbackAdjuster와 동일 패턴 |
| 배치 프리로드 | predict_all에서 1회 DB 조회 | 성능 최적화 |

**File Structure** (8 files):

1. `src/settings/constants.py` -- 11 substitution constants + DB_SCHEMA_VERSION (MODIFIED)
2. `src/db/models.py` -- SCHEMA_MIGRATIONS v49 (MODIFIED)
3. `src/infrastructure/database/schema.py` -- STORE_SCHEMA + STORE_INDEXES (MODIFIED)
4. `src/infrastructure/database/repos/substitution_repo.py` -- Repository (NEW)
5. `src/infrastructure/database/repos/__init__.py` -- re-export (MODIFIED)
6. `src/analysis/substitution_detector.py` -- SubstitutionDetector (NEW)
7. `src/prediction/improved_predictor.py` -- 잠식 계수 적용 (MODIFIED)
8. `tests/test_substitution_detector.py` -- 24 tests (NEW)

### 3.3 Do Phase (Implementation)

**Implementation Status**: 100% Complete

**Files Implemented**:

| File | Status | Change Summary |
|------|--------|-----------------|
| `src/settings/constants.py` | MODIFIED | DB_SCHEMA_VERSION=49, 11 SUBSTITUTION_* constants |
| `src/db/models.py` | MODIFIED | SCHEMA_MIGRATIONS v49: substitution_events table |
| `src/infrastructure/database/schema.py` | MODIFIED | STORE_SCHEMA table + 3 STORE_INDEXES |
| `src/infrastructure/database/repos/substitution_repo.py` | NEW | 5 methods: upsert, query, batch, expire, by_small_cd |
| `src/infrastructure/database/repos/__init__.py` | MODIFIED | import + __all__ entry |
| `src/analysis/substitution_detector.py` | NEW | 10+1 methods: detect_all, detect_cannibalization, _get_active_small_cds, _get_items_in_small_cd, _get_sales_data, _calculate_moving_averages, _classify_items, _compute_adjustment_coefficient, _calculate_confidence, get_adjustment, preload |
| `src/prediction/improved_predictor.py` | MODIFIED | __init__ attribute, lazy loader, predict_item block, predict_all preload |
| `tests/test_substitution_detector.py` | NEW | 24 tests across 8 test classes |

### 3.4 Check Phase (Gap Analysis)

**Analysis Results**:

```
Total Design Items Checked:  66
  Exact Match:             64  (97.0%)
  Trivial Change:           2  (3.0%) -- v48->v49 version number
  Missing Items:            0  (0%)
  Contradictory Changes:    0  (0%)
  Additive Enhancements:    3  (positive)

FINAL MATCH RATE: 100% PASS
```

**Detailed Validation**:

| Section | Items | Match Rate | Status |
|---------|:-----:|:----------:|:------:|
| 2. Constants | 12 | 100% | PASS |
| 3. DB Schema | 7 | 100% | PASS |
| 4. Repository | 7 | 100% | PASS |
| 5. SubstitutionDetector | 12 | 100% | PASS |
| 6. improved_predictor.py | 4 | 100% | PASS |
| 7. repos/__init__.py | 2 | 100% | PASS |
| 8. Tests | 22 | 100% | PASS (+ 2 additive) |
| **TOTAL** | **66** | **100%** | **PASS** |

### 3.5 Act Phase (Closure)

**Completion Status**: All 4 main goals achieved in single iteration.

---

## 4. Requirement Completion

### 4.1 Functional Requirements

| Req ID | Requirement | Implementation | Status |
|--------|-------------|:---------------:|:------:|
| **FR-01** | 잠식 감지 | SubstitutionDetector.detect_cannibalization() | Done |
| **FR-02** | 발주 조정 | improved_predictor.py 잠식 계수 적용 block | Done |
| **FR-03** | 이벤트 기록 | substitution_events table + SubstitutionEventRepository | Done |
| **FR-04** | 파이프라인 통합 | lazy-load + predict_item + predict_all preload | Done |

### 4.2 Non-Functional Requirements

| Item | Target | Achieved | Status |
|------|:------:|:--------:|:------:|
| **Test Coverage** | 22 tests | 24 tests (109%) | Done |
| **Architecture** | Analysis 계층 배치 | `src/analysis/` | Done |
| **Convention** | snake_case/PascalCase/UPPER_SNAKE | 100% compliant | Done |
| **Backward Compat** | 기존 코드 변경 없음 | lazy-load, sentinel 패턴 | Done |
| **Performance** | 배치 프리로드 | predict_all에서 1회 DB 쿼리 | Done |

---

## 5. Quality Metrics

### 5.1 Test Results

```
Test Summary
Total Tests:            24
  Passed:              24 (100%)
  Failed:               0 (0%)
  Skipped:              0 (0%)

Test Classes:
  TestSubstitutionRepository:     4 tests
  TestMovingAverage:              3 tests
  TestClassifyItems:              3 tests
  TestAdjustmentCoefficient:      4 tests
  TestConfidence:                 2 tests
  TestDetectCannibalization:      4 tests
  TestPredictorIntegration:       2 tests
  TestConstants:                  2 tests
```

### 5.2 Code Quality

| Metric | Target | Achieved |
|--------|:------:|:--------:|
| **New Files** | 3 | 3 (substitution_detector, substitution_repo, tests) |
| **Modified Files** | 5 | 5 (constants, models, schema, __init__, improved_predictor) |
| **Docstring Coverage** | 100% | 100% |
| **Exception Handling** | No silent pass | All logged |

### 5.3 Algorithm Summary

```
Cannibalization Detection Algorithm
1. 소분류(small_cd) 내 상품 목록 조회
2. 각 상품의 30일 판매 데이터 수집
3. 전반 16일 vs 후반 14일 이동평균 비교
4. 증가 상품(ratio >= 1.3) = gainer
5. 감소 상품(ratio <= 0.7) = loser
6. 소분류 총량 변화 <= 20% 검증 (외부 요인 배제)
7. gainer x loser 조합별 이벤트 생성
8. 감소율 기반 조정 계수:
   - 70%+ 감소: 0.7
   - 50~70% 감소: 0.8
   - 30~50% 감소: 0.9
9. 14일 유효기간 후 자동 만료
```

---

## 6. Completed Sections

### 6.1 SubstitutionDetector

**Location**: `src/analysis/substitution_detector.py`

**Core Logic**:
- `detect_all()`: 전체 소분류 일괄 분석
- `detect_cannibalization()`: 단일 소분류 잠식 감지
- `get_adjustment()`: 상품별 잠식 계수 조회
- `preload()`: 배치 프리로드 (predict_all 연동)

**Data Flow**:
```
daily_sales (store DB)
  -> _get_sales_data() per item
  -> _calculate_moving_averages() (prior vs recent)
  -> _classify_items() (gainer/loser split)
  -> _compute_adjustment_coefficient() (0.7~0.9)
  -> _calculate_confidence() (stability + strength)
  -> repo.upsert_event() -> substitution_events table
```

### 6.2 SubstitutionEventRepository

**Location**: `src/infrastructure/database/repos/substitution_repo.py`

**Methods**:
- `upsert_event()`: INSERT OR REPLACE
- `get_active_events()`: loser 기준 활성 이벤트 조회
- `get_active_events_batch()`: 배치 조회 (IN clause)
- `expire_old_events()`: 만료 이벤트 비활성화
- `get_events_by_small_cd()`: 소분류별 최근 이벤트

### 6.3 Pipeline Integration

**Location**: `src/prediction/improved_predictor.py`

**Integration Points**:
1. `__init__`: `self._substitution_detector = None`
2. `_get_substitution_detector()`: lazy loader with sentinel
3. `predict_item()`: 폐기 피드백 뒤, max cap 앞에 잠식 계수 적용
4. `predict_all()`: 프리로드 block

**Pipeline Position**:
```
... -> DiffFeedback -> WasteFeedback -> [SubstitutionDetect] -> Category Max Cap
```

---

## 7. Incomplete Items

**None** -- All requirements completed in single iteration.

---

## 8. Lessons Learned

### 8.1 What Went Well

1. **WasteCauseAnalyzer 패턴 재활용** -- 기존 분석 모듈 패턴(lazy-load, sentinel, preload)을 그대로 적용하여 일관성 유지
2. **DB 버전 충돌 즉시 해결** -- Design에서 v48로 계획했으나 기존 v48 존재 확인 후 v49로 조정
3. **24/22 테스트** -- 설계 이상의 테스트(상수 검증 2개 추가)로 품질 강화

### 8.2 Areas for Improvement

1. **소분류 총량 검증 고도화** -- 현재 단순 비율 비교이나, 계절성 보정 추가 가능
2. **웹 대시보드 시각화** -- 잠식 이벤트 시각화 대시보드 추가 (추후)
3. **다중 gainer-loser 가중 매칭** -- 현재 모든 gainer x loser 조합을 생성하나, 판매량 비율 기반 가중 매칭 가능

---

## 9. Future Enhancement Opportunities

### 9.1 Phase 2: 웹 대시보드 (Medium Priority)

- `/api/substitution/events` API 엔드포인트
- 소분류별 잠식 현황 시각화 (타임라인, 비율 차트)
- 잠식 계수 수동 조정 UI

### 9.2 Phase 3: 고급 분석 (Low Priority)

- 시계열 상관관계 분석 (Pearson correlation)
- 계절성 보정된 잠식 감지
- 중분류 레벨 잠식 분석

---

## 10. Changelog

### v1.0.0 (2026-03-01)

**Added**:
- SubstitutionDetector: 소분류 내 잠식 감지 모듈 (10+ methods)
- SubstitutionEventRepository: 잠식 이벤트 DB CRUD (5 methods)
- substitution_events 테이블 (DB v49, 18 columns, 3 indexes)
- 11 SUBSTITUTION_* 상수 정의
- improved_predictor.py 잠식 계수 적용 통합
- 24 unit tests (8 test classes)

**Changed**:
- `DB_SCHEMA_VERSION`: 47 -> 49
- `improved_predictor.py`: 잠식 계수 적용 단계 추가 (WasteFeedback -> SubstitutionDetect -> Max Cap)
- `repos/__init__.py`: SubstitutionEventRepository re-export

---

## 11. Sign-Off

### 11.1 Completion Verification

| Item | Status | Verified By | Date |
|------|:------:|-------------|------|
| All requirements implemented | Done | gap-detector | 2026-03-01 |
| 100% match rate achieved | Done | gap-detector | 2026-03-01 |
| All 24 tests passing | Done | pytest | 2026-03-01 |
| Documentation finalized | Done | report-generator | 2026-03-01 |

---

**Report Status**: FINAL -- Ready for Archive & Closure
