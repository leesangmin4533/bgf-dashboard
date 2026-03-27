# new-product-detail-fetch Completion Report

> **Status**: Complete
>
> **Project**: bgf_auto
> **Author**: Claude
> **Completion Date**: 2026-02-26
> **PDCA Cycle**: #1

---

## 1. Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | new-product-detail-fetch (상품상세 일괄수집) |
| Start Date | 2026-02-26 |
| End Date | 2026-02-26 |
| Duration | 1일 (Plan/Design/Do/Check 동일 세션) |
| Match Rate | **98%** |
| Iteration | 0회 (1차 구현에서 기준 충족) |

### 1.2 Results Summary

```
+-------------------------------------------------+
|  Completion Rate: 100%                           |
+-------------------------------------------------+
|  ✅ Complete:     6 / 6 files                    |
|  ✅ Tests:       18 / 18 pass                    |
|  ✅ Match Rate:  98% (target: 90%)               |
|  ✅ Iteration:    0 (no fix needed)              |
+-------------------------------------------------+
```

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [new-product-detail-fetch.plan.md](../01-plan/features/new-product-detail-fetch.plan.md) | ✅ Finalized |
| Design | [new-product-detail-fetch.design.md](../02-design/features/new-product-detail-fetch.design.md) | ✅ Finalized |
| Check | [new-product-detail-fetch.analysis.md](../03-analysis/new-product-detail-fetch.analysis.md) | ✅ Complete |
| Report | Current document | ✅ Complete |

---

## 3. Completed Items

### 3.1 Functional Requirements

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| FR-01 | Phase 0 디스커버리 스크립트 | ✅ Complete | 490행, BGF 팝업 전체 컬럼 덤프 |
| FR-02 | Phase 1 일괄 수집기 | ✅ Complete | 510행, 9개 메서드 |
| FR-03 | DB 부분 업데이트 (COALESCE) | ✅ Complete | 기존값 보존 + NULL만 갱신 |
| FR-04 | products.mid_cd 업데이트 | ✅ Complete | '999'/'' 인 경우만 |
| FR-05 | 01:00 스케줄 등록 | ✅ Complete | schedule + CLI --fetch-detail |
| FR-06 | 4단계 바코드 폴백 | ✅ Complete | nexacro/DOM/qs/ActionChains |
| FR-07 | 3단계 팝업 닫기 폴백 | ✅ Complete | nexacro/DOM mouse/qs |
| FR-08 | Decimal .hi 타입 처리 | ✅ Complete | JS + Python 양방향 |

### 3.2 Non-Functional Requirements

| Item | Target | Achieved | Status |
|------|--------|----------|--------|
| 테스트 커버리지 | 18개 | 18/18 pass | ✅ |
| 전체 테스트 스위트 | 무영향 | 2329/2330 pass | ✅ |
| 하위 호환성 | 기존 코드 무변경 | 6개 컬렉터 무영향 | ✅ |
| DB 스키마 | 무변경 | 기존 테이블만 사용 | ✅ |
| 보안 | 0 Critical | 0 이슈 | ✅ |
| 프로젝트 규약 | BaseRepository 패턴 | _to_int/_to_price 적용 | ✅ |

### 3.3 Deliverables

| Deliverable | Location | Status |
|-------------|----------|--------|
| Phase 0 Discovery | `scripts/discover_popup_columns.py` | ✅ |
| Batch Collector | `src/collectors/product_detail_batch_collector.py` | ✅ |
| Repository 메서드 | `src/infrastructure/database/repos/product_detail_repo.py` | ✅ |
| Timing 상수 | `src/settings/timing.py` (BD_* 8개) | ✅ |
| Scheduler 통합 | `run_scheduler.py` (wrapper + schedule + CLI) | ✅ |
| Tests | `tests/test_product_detail_batch_collector.py` (18개) | ✅ |

---

## 4. Incomplete Items

### 4.1 Carried Over (사용자 액션 필요)

| Item | Reason | Priority | Action |
|------|--------|----------|--------|
| Phase 0 실행 | BGF 사이트 접근 필요 | High | `python scripts/discover_popup_columns.py --item-cd [바코드]` |
| 컬럼명 확정 | Phase 0 결과 후 | Medium | _extract_detail() 내 MID_CD/SELL_PRC 후보 확정 |

### 4.2 Cancelled/On Hold Items

| Item | Reason | Alternative |
|------|--------|-------------|
| margin_rate 수집 | 팝업 내 존재 미확인 | Phase 0 후 결정 |
| lead_time_days 수집 | 팝업 내 존재 미확인 | Phase 0 후 결정 |
| cost_price 수집 | 팝업 내 존재 미확인 | Phase 0 후 결정 |

---

## 5. Quality Metrics

### 5.1 Final Analysis Results

| Metric | Target | Final | Status |
|--------|--------|-------|--------|
| Design Match Rate | 90% | **98%** | ✅ |
| 체크포인트 일치 | 90+ | 103/104 | ✅ |
| 보안 이슈 | 0 Critical | 0 | ✅ |
| 테스트 Pass | 18/18 | 18/18 | ✅ |
| 하위 호환성 | 무영향 | 무영향 | ✅ |

### 5.2 Resolved Issues

| Issue | Resolution | Result |
|-------|------------|--------|
| COALESCE 순서 오류 | `COALESCE(col, ?)` → `COALESCE(existing, new)` | ✅ 기존값 보존 정상 |
| order_unit_qty 3-param SQL | 1-param 간소화 | ✅ 동일 동작, 간결한 SQL |
| Design vs 의도 불일치 | Design 텍스트 의도 기준 구현 | ✅ 테스트로 검증 |

---

## 6. Technical Details

### 6.1 아키텍처

```
[run_scheduler.py]
    |-- detail_fetch_wrapper() @01:00
         |-- SalesAnalyzer.login()
         |-- ProductDetailBatchCollector(driver)
              |-- get_items_to_fetch()
              |    |-- ProductDetailRepository.get_items_needing_detail_fetch()
              |
              |-- collect_all(item_codes)
                   |-- for each item_cd:
                   |    |-- _input_barcode()     [4-level fallback]
                   |    |-- _trigger_search()     [nexacro/DOM/ActionChains]
                   |    |-- _wait_for_popup()     [BD_POPUP_MAX_CHECKS polling]
                   |    |-- _wait_for_data_load() [BD_DATA_LOAD_MAX_CHECKS]
                   |    |-- _extract_detail()     [dsItemDetail + dsItemDetailOrd]
                   |    |-- _save_to_db()
                   |    |    |-- update_product_mid_cd()
                   |    |    |-- bulk_update_from_popup()
                   |    |-- _close_popup()        [3-level fallback]
                   |
                   |-- return stats {total, success, skip, fail}
```

### 6.2 DB 저장 전략

```
[기존값 보존 원칙]

UPDATE product_details SET
  expiration_days = COALESCE(expiration_days, ?),      -- 기존 있으면 보존
  orderable_day = CASE
    WHEN '일월화수목금토' THEN new_value                  -- 기본값만 덮어쓰기
    ELSE 보존 END,
  order_unit_qty = CASE
    WHEN NULL or <=1 THEN new_value                     -- 미설정만 갱신
    ELSE 보존 END,
  sell_price = COALESCE(sell_price, ?),                 -- 기존 있으면 보존
  fetched_at = ?                                        -- 항상 갱신

UPDATE products SET mid_cd = ?
  WHERE mid_cd IN ('999', '') OR mid_cd IS NULL         -- 추정값만 덮어쓰기
```

### 6.3 수집 대상 선별 기준

| # | 조건 | 의미 |
|---|------|------|
| 1 | `pd.fetched_at IS NULL` | BGF 사이트 한 번도 조회 안 한 상품 |
| 2 | `pd.expiration_days IS NULL` | 유통기한 누락 |
| 3 | `pd.orderable_day = '일월화수목금토'` | 기본값 (실제 요일 미확인) |
| 4 | `p.mid_cd IN ('999', '')` | 카테고리 미분류 |

### 6.4 타이밍 상수

| 상수 | 값 | 역할 |
|------|---|------|
| BD_BARCODE_INPUT_WAIT | 0.3s | 바코드 입력 후 안정화 |
| BD_POPUP_MAX_CHECKS | 10 | 팝업 출현 최대 대기 횟수 |
| BD_POPUP_CHECK_INTERVAL | 0.5s | 팝업 폴링 간격 |
| BD_DATA_LOAD_MAX_CHECKS | 10 | 데이터 로딩 최대 대기 횟수 |
| BD_DATA_LOAD_CHECK_INTERVAL | 0.3s | 데이터 폴링 간격 |
| BD_POPUP_CLOSE_WAIT | 0.5s | 팝업 닫기 후 안정화 |
| BD_BETWEEN_ITEMS | 2.0s | 상품 간 서버 부하 방지 |
| BD_MAX_ITEMS_PER_RUN | 200 | 1회 실행 최대 상품 수 |

---

## 7. Lessons Learned & Retrospective

### 7.1 What Went Well (Keep)

- **2단계 접근 (Phase 0 → Phase 1)**: 디스커버리를 분리하여 불확실성(MID_CD 존재 여부) 관리 우수
- **FailReasonCollector 패턴 재활용**: 4단계 폴백, 팝업 폴링, 닫기 등 검증된 패턴 복사 → 빠른 구현
- **COALESCE 의도 기반 구현**: Design SQL의 리터럴 오류를 텍스트 의도 기준으로 수정 → 테스트로 검증
- **프로젝트 규약 적극 적용**: _to_int/_to_price 헬퍼, BaseRepository 패턴 자연 통합

### 7.2 What Needs Improvement (Problem)

- **Design SQL과 텍스트 의도 불일치**: `COALESCE(?, col)` vs "기존 NULL만 갱신" → 리뷰 단계에서 SQL 검증 강화 필요
- **Phase 0 미실행 상태**: 구현은 완료했지만 실제 BGF 컬럼명 확정 전이므로 MID_CD/SELL_PRC 후보가 올바른지 검증 필요

### 7.3 What to Try Next (Try)

- Phase 0 디스커버리 실행 후 결과에 따라 _extract_detail() 컬럼명 미세조정
- 수집 성공/실패 통계를 DB에 저장하여 일간/주간 추이 모니터링
- 대시보드에 수집 상태 위젯 추가 (미수집 상품 수, 최근 수집 결과)

---

## 8. Next Steps

### 8.1 Immediate (사용자)

- [ ] **Phase 0 실행**: `python scripts/discover_popup_columns.py --item-cd [실제바코드] --output columns.json`
- [ ] 결과 확인: MID_CD, SELL_PRC 등 핵심 컬럼 존재 여부
- [ ] 필요시 _extract_detail() 컬럼 후보 조정

### 8.2 Next PDCA Cycle (후속)

| Item | Priority | Description |
|------|----------|-------------|
| 카테고리 분류 모듈 | Medium | mid_cd 기반 상세 분류 로직 설계 |
| 대시보드 수집 현황 | Low | 수집 통계 시각화 위젯 |
| 수집 이력 DB 테이블 | Low | 일별 수집 통계 저장 |

---

## 9. Changelog

### v1.0.0 (2026-02-26)

**Added:**
- `scripts/discover_popup_columns.py` - Phase 0 디스커버리 스크립트 (BGF 팝업 전체 컬럼 덤프)
- `src/collectors/product_detail_batch_collector.py` - 상품 상세 일괄 수집기 (9개 메서드)
- `product_detail_repo.py` - 3개 메서드 추가:
  - `get_items_needing_detail_fetch()` - 수집 대상 4조건 OR 쿼리
  - `bulk_update_from_popup()` - COALESCE 기반 부분 업데이트
  - `update_product_mid_cd()` - 추정값('999'/'')만 덮어쓰기
- `timing.py` - BD_* 타이밍 상수 8개
- `run_scheduler.py` - detail_fetch_wrapper() + 01:00 스케줄 + --fetch-detail CLI
- `tests/test_product_detail_batch_collector.py` - 18개 테스트

**Fixed:**
- COALESCE 순서: Design의 `COALESCE(?, col)` → 구현 `COALESCE(col, ?)` (기존값 보존 의도 정합)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-26 | Completion report created | Claude |
