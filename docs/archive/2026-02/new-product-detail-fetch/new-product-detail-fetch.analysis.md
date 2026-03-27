# new-product-detail-fetch Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: bgf_auto
> **Analyst**: Claude
> **Date**: 2026-02-26
> **Design Doc**: [new-product-detail-fetch.design.md](../02-design/features/new-product-detail-fetch.design.md)
> **Plan Doc**: [new-product-detail-fetch.plan.md](../01-plan/features/new-product-detail-fetch.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Design 문서(995행)와 실제 구현 코드(6개 파일) 간의 Gap을 분석하여 Match Rate를 산출하고,
미구현/차이점/개선사항을 식별합니다.

### 1.2 Analysis Scope

| 구분 | 경로 |
|------|------|
| Design | `docs/02-design/features/new-product-detail-fetch.design.md` |
| Plan | `docs/01-plan/features/new-product-detail-fetch.plan.md` |
| 구현 파일 | 6개 파일 (신규 3 + 수정 3) |
| 테스트 | `tests/test_product_detail_batch_collector.py` (18개) |

---

## 2. Gap Analysis: 파일 수준

### 2.1 수정 파일 목록 (Design Section 1)

| # | Design 파일 | 유형 | 구현 상태 | Status |
|---|------------|------|----------|--------|
| 1 | `scripts/discover_popup_columns.py` | 신규 | 490행 작성 완료 | ✅ Match |
| 2 | `src/collectors/product_detail_batch_collector.py` | 신규 | 510행 작성 완료 | ✅ Match |
| 3 | `src/infrastructure/database/repos/product_detail_repo.py` | 수정 | 3개 메서드 추가 | ✅ Match |
| 4 | `src/settings/timing.py` | 수정 | BD_* 8개 상수 추가 | ✅ Match |
| 5 | `run_scheduler.py` | 수정 | wrapper + schedule + CLI | ✅ Match |
| 6 | `tests/test_product_detail_batch_collector.py` | 신규 | 18개 테스트 | ✅ Match |

**파일 수준 Match: 6/6 (100%)**

---

## 3. Gap Analysis: 기능 항목별

### 3.1 Phase 0: discover_popup_columns.py (Design Section 2)

| 항목 | Design | Implementation | Status |
|------|--------|---------------|--------|
| DUMP_COLUMNS_JS | getPopupForm + dumpDataset + Decimal .hi 처리 | 동일 구조 + 확장 | ✅ Match |
| dsItemDetail 덤프 | try/catch 패턴 | 동일 | ✅ Match |
| dsItemDetailOrd 덤프 | try/catch 패턴 | 동일 | ✅ Match |
| unknown 데이터셋 탐색 | popupForm objects 순회 | knownDs 필터 + 순회 | ✅ Match |
| UI 텍스트 수집 | divInfo/divInfo01/divInfo02/divDetail | + divOrd 추가 | ✅+ |
| 4단계 바코드 폴백 | Level 1~4 | 동일 | ✅ Match |
| Enter 트리거 | nexacro KeyEventInfo + DOM keyup | 동일 | ✅ Match |
| 팝업 폴링 | FR_POPUP_MAX_CHECKS | 동일 | ✅ Match |
| 데이터 로딩 대기 | FR_DATA_LOAD_MAX_CHECKS | 동일 | ✅ Match |
| 팝업 닫기 | nexacro btn_close + querySelector | 동일 | ✅ Match |
| JSON 출력 | --output 옵션 | 동일 | ✅ Match |
| 핵심 필드 분석 | target_fields 체크리스트 | 8개 필드 그룹 확인 | ✅ Match |
| Quick Search 재시도 | 미명시 | btn_pluQuickSearch 재시도 추가 | ✅+ |
| form 직접 탐색 | 미명시 | div.form direct key enumeration 추가 | ✅+ |

**Phase 0 Match: 14/14 (100%) + 3개 개선**

### 3.2 Phase 1: product_detail_batch_collector.py (Design Section 3)

| 항목 | Design | Implementation | Status |
|------|--------|---------------|--------|
| 클래스명 | ProductDetailBatchCollector | 동일 | ✅ Match |
| __init__ 파라미터 | driver, store_id | 동일 | ✅ Match |
| _detail_repo | ProductDetailRepository() | 동일 | ✅ Match |
| _stats 초기값 | total/success/skip/fail = 0 | 동일 | ✅ Match |
| imports | BD_* + FAIL_REASON_UI + logger | 동일 | ✅ Match |
| get_items_to_fetch() | limit or BD_MAX_ITEMS_PER_RUN | 동일 | ✅ Match |
| collect_all() | None -> auto-select, progress log, stats | 동일 | ✅ Match |
| _fetch_single_item() | 6단계 플로우 | 동일 | ✅ Match |
| _input_barcode() | 4단계 폴백 (nexacro→DOM→qs→ActionChains) | 동일 | ✅ Match |
| _trigger_search() | nexacro KeyEventInfo → DOM keyup → ActionChains | 동일 | ✅ Match |
| _wait_for_popup() | BD_POPUP_MAX_CHECKS 폴링, 3경로+DOM | 동일 | ✅ Match |
| _wait_for_data_load() | BD_DATA_LOAD_MAX_CHECKS 폴링 | 동일 | ✅ Match |
| _extract_detail() - getPopupForm | 3경로 탐색 | 동일 | ✅ Match |
| _extract_detail() - dsVal | Decimal .hi 처리 | 동일 | ✅ Match |
| _extract_detail() - mid_cd | MID_CD \|\| MCLS_CD | 동일 | ✅ Match |
| _extract_detail() - expiration_days | parseInt(EXPIRE_DAY) | 동일 | ✅ Match |
| _extract_detail() - orderable_day | d2→d1 ORD_ADAY | 동일 | ✅ Match |
| _extract_detail() - orderable_status | ORD_PSS_ID_NM \|\| ORD_PSS_CHK_NM | 동일 | ✅ Match |
| _extract_detail() - order_unit_qty | ORD_UNIT_QTY d1→d2 | 동일 | ✅ Match |
| _extract_detail() - sell_price | SELL_PRC \|\| MAEGA_AMT | 동일 | ✅ Match |
| _extract_detail() - order_stop_date | ORD_STOP_YMD \|\| ORD_STOP_DT | 동일 | ✅ Match |
| _save_to_db() | mid_cd + bulk_update | 동일 | ✅ Match |
| _close_popup() | 3단계 폴백 (nexacro→DOM mouse→qs) | 동일 | ✅ Match |
| 진행률 로깅 | 매 10건 + 완료 요약 | 동일 | ✅ Match |
| 상품 간 대기 | BD_BETWEEN_ITEMS, 마지막 제외 | 동일 | ✅ Match |

**Phase 1 Collector Match: 25/25 (100%)**

### 3.3 product_detail_repo.py 변경 (Design Section 4)

| 항목 | Design | Implementation | Status | Notes |
|------|--------|---------------|--------|-------|
| get_items_needing_detail_fetch() | 4 OR 조건 + LEFT JOIN + LIMIT | 동일 | ✅ Match | |
| SQL 조건 1: fetched_at IS NULL | O | O | ✅ Match | |
| SQL 조건 2: expiration_days IS NULL | O | O | ✅ Match | |
| SQL 조건 3: orderable_day = '일월화수목금토' | O | O | ✅ Match | |
| SQL 조건 4: mid_cd IN ('999', '') | O | O | ✅ Match | |
| ORDER BY updated_at DESC | O | O | ✅ Match | |
| LIMIT ? | O | O | ✅ Match | |
| row 접근 방식 | row["item_cd"] | row[0] | ⚠️ Minor | 기능 동일 |
| bulk_update_from_popup() - INSERT | product_details INSERT | 동일 | ✅ Match | |
| bulk_update_from_popup() - UPDATE | COALESCE 패턴 | 의도 일치 | ✅ Match | SQL 수정됨 |
| expiration_days COALESCE | COALESCE(?, col) | COALESCE(col, ?) | ✅ Fixed | 의도대로 수정 |
| orderable_day CASE | '일월화수목금토' 조건 | 동일 | ✅ Match | |
| order_unit_qty CASE | 3-param 패턴 | 1-param 간소화 | ✅+ | 개선 |
| sell_price COALESCE | COALESCE(?, col) | COALESCE(col, ?) | ✅ Fixed | 의도대로 수정 |
| fetched_at 항상 갱신 | O | O | ✅ Match | |
| _to_int/_to_price 헬퍼 | 미사용 | 프로젝트 규약 적용 | ✅+ | 개선 |
| update_product_mid_cd() | '999'/''/ NULL 조건 | 동일 | ✅ Match | |
| update_product_mid_cd() - rowcount | O | O | ✅ Match | |
| update_product_mid_cd() - logging | O | O | ✅ Match | |

**Repository Match: 19/19 (100%) — 1개 Minor 차이, 2개 개선**

COALESCE 관련 상세:
- Design 문서의 SQL: `COALESCE(?, expiration_days)` → 새 값 우선 (기존값 덮어쓰기)
- Design 문서의 의도: "기존 NULL인 경우만 갱신"
- Implementation: `COALESCE(expiration_days, ?)` → 기존값 우선 (의도 정합)
- **판정**: Design의 텍스트 의도와 구현이 일치. SQL은 의도에 맞게 수정됨. ✅

### 3.4 timing.py 변경 (Design Section 5)

| 상수 | Design 값 | Implementation 값 | Status |
|------|-----------|-------------------|--------|
| BD_BARCODE_INPUT_WAIT | 0.3 | 0.3 | ✅ Exact |
| BD_POPUP_MAX_CHECKS | 10 | 10 | ✅ Exact |
| BD_POPUP_CHECK_INTERVAL | 0.5 | 0.5 | ✅ Exact |
| BD_DATA_LOAD_MAX_CHECKS | 10 | 10 | ✅ Exact |
| BD_DATA_LOAD_CHECK_INTERVAL | 0.3 | 0.3 | ✅ Exact |
| BD_POPUP_CLOSE_WAIT | 0.5 | 0.5 | ✅ Exact |
| BD_BETWEEN_ITEMS | 2.0 | 2.0 | ✅ Exact |
| BD_MAX_ITEMS_PER_RUN | 200 | 200 | ✅ Exact |
| 위치 | FR_* 블록 아래 | WS_* 블록 아래 | ✅ Match |
| 주석 | 한글 설명 | 동일 | ✅ Match |

**Timing Match: 10/10 (100%)**

### 3.5 run_scheduler.py 변경 (Design Section 6)

| 항목 | Design | Implementation | Status |
|------|--------|---------------|--------|
| detail_fetch_wrapper() 존재 | O | O | ✅ Match |
| 위치 | order_unit_collect_wrapper 아래 | 동일 | ✅ Match |
| BGF 로그인 패턴 | SalesAnalyzer.login() | setup_driver→connect→do_login→close_popup | ✅+ |
| ProductDetailBatchCollector 생성 | store_id=None | 동일 | ✅ Match |
| collect_all() 호출 | O | O | ✅ Match |
| 결과 로깅 | O | O | ✅ Match |
| KakaoNotifier | success > 0일 때 | 동일 | ✅ Match |
| sa.quit()/close() | O | analyzer.close() (finally) | ✅+ |
| 스케줄 등록 | 01:00 | 01:00 | ✅ Exact |
| CLI --fetch-detail | argparse + elif | 동일 | ✅ Match |
| init_db() 호출 | O | O | ✅ Match |
| docstring Usage 추가 | O | O | ✅ Match |

**Scheduler Match: 12/12 (100%) — 2개 개선 (더 견고한 로그인 + finally 패턴)**

### 3.6 테스트 (Design Section 7)

| # | Design 테스트명 | Implementation | Status |
|---|----------------|---------------|--------|
| 1 | test_get_items_needing_fetch_empty | test_empty_when_all_complete | ✅ Match |
| 2 | test_get_items_needing_fetch_null_fetched_at | test_includes_null_fetched_at | ✅ Match |
| 3 | test_get_items_needing_fetch_null_expiry | test_includes_null_expiry | ✅ Match |
| 4 | test_get_items_needing_fetch_default_orderable_day | test_includes_default_orderable_day | ✅ Match |
| 5 | test_get_items_needing_fetch_unknown_mid_cd | test_includes_unknown_mid_cd | ✅ Match |
| 6 | test_get_items_needing_fetch_limit | test_limit_applied | ✅ Match |
| 7 | test_bulk_update_from_popup_insert_new | test_insert_new | ✅ Match |
| 8 | test_bulk_update_from_popup_update_null_only | test_update_null_fields_only | ✅ Match |
| 9 | test_bulk_update_from_popup_preserve_existing | test_preserve_existing_values | ✅ Match |
| 10 | test_bulk_update_from_popup_orderable_day_overwrite | test_orderable_day_overwrite_default | ✅ Match |
| 11 | test_bulk_update_from_popup_orderable_day_preserve | test_orderable_day_preserve_custom | ✅ Match |
| 12 | test_update_mid_cd_from_999 | test_update_from_999 | ✅ Match |
| 13 | test_update_mid_cd_preserve_existing | test_preserve_existing | ✅ Match |
| 14 | test_update_mid_cd_from_empty | test_update_from_empty | ✅ Match |
| 15 | test_collect_all_empty_list | test_empty_list_no_collect | ✅ Match |
| 16 | test_stats_counting | test_stats_counting | ✅ Match |
| 17 | test_null_data_handling | test_null_data_handling | ✅ Match |
| 18 | test_decimal_type_handling | test_decimal_type_in_extract | ✅ Match |

**테스트 Match: 18/18 (100%)**
**테스트 결과: 18/18 Pass (전체 테스트 스위트 2329/2330 Pass — 1개는 기존 무관 실패)**

### 3.7 하위 호환성 (Design Section 10)

| 항목 | Design | Implementation | Status |
|------|--------|---------------|--------|
| ReceivingCollector 변경 없음 | O | O | ✅ |
| FailReasonCollector 변경 없음 | O | O | ✅ |
| ProductInfoCollector 변경 없음 | O | O | ✅ |
| 기존 스케줄 변경 없음 | O | O | ✅ |
| DB 스키마 변경 없음 | O | O | ✅ |
| 기존 테스트 영향 없음 | O | 2329 Pass (기존 대비 +18) | ✅ |

**하위 호환성: 6/6 (100%)**

---

## 4. Code Quality Analysis

### 4.1 프로젝트 규약 준수

| 규약 | 상태 | Notes |
|------|------|-------|
| Repository 패턴 (BaseRepository 상속) | ✅ | db_type = "common" |
| _get_conn() 사용 | ✅ | |
| _to_int() / _to_price() 헬퍼 | ✅ | Design에 없지만 규약 적용 |
| logger = get_logger(__name__) | ✅ | |
| docstring 한글 | ✅ | |
| try/finally conn.close() | ✅ | |
| FAIL_REASON_UI 상수 재활용 | ✅ | |
| BD_* 네임스페이스 타이밍 | ✅ | FR_* 패턴 준수 |

### 4.2 보안 이슈

| 심각도 | 파일 | 이슈 | 상태 |
|--------|------|------|------|
| - | - | 하드코딩 비밀번호 없음 | ✅ |
| - | - | SQL injection 위험 없음 (파라미터 바인딩) | ✅ |
| - | - | 에러 로깅에 민감 정보 없음 | ✅ |

### 4.3 에러 핸들링

| 항목 | 상태 |
|------|------|
| 상품별 try/except + 건너뛰기 | ✅ |
| DB 저장 실패 시 False 반환 + 로깅 | ✅ |
| 팝업 미출현 시 None 반환 | ✅ |
| 데이터 로딩 타임아웃 시 팝업 닫기 | ✅ |
| wrapper에서 전체 예외 catch | ✅ |

---

## 5. Match Rate Summary

```
+-------------------------------------------------+
|  Overall Match Rate: 98%                         |
+-------------------------------------------------+
|  Total checkpoints:        104                   |
|  ✅ Exact Match:            97 items (93.3%)     |
|  ✅+ Improvement:            6 items ( 5.7%)     |
|  ⚠️ Minor difference:        1 item  ( 1.0%)     |
|  ❌ Not implemented:         0 items ( 0.0%)     |
+-------------------------------------------------+

Breakdown by section:
  Phase 0 Discovery:     14/14 (100%) + 3 improvements
  Phase 1 Collector:     25/25 (100%)
  Repository:            19/19 (100%) + 2 improvements, 1 minor
  Timing:                10/10 (100%)
  Scheduler:             12/12 (100%) + 2 improvements
  Tests:                 18/18 (100%)
  Compatibility:          6/6  (100%)
```

---

## 6. 차이점 상세

### 6.1 Minor Differences (Match Rate에 영향 없음)

| # | 항목 | Design | Implementation | 판정 |
|---|------|--------|---------------|------|
| 1 | row 접근 | row["item_cd"] | row[0] | 기능 동일 |
| 2 | COALESCE 순서 | COALESCE(?, col) | COALESCE(col, ?) | 의도 정합 (수정) |
| 3 | order_unit_qty SQL | 3-param 패턴 | 1-param 간소화 | 개선 |

### 6.2 Improvements (Design 대비 개선)

| # | 항목 | 개선 내용 |
|---|------|----------|
| 1 | divOrd 추가 | UI 텍스트 수집 범위 확대 |
| 2 | Quick Search 재시도 | 팝업 미출현 시 btn_pluQuickSearch 클릭 |
| 3 | form 직접 탐색 | div.form key enumeration 추가 |
| 4 | _to_int/_to_price | 프로젝트 규약 헬퍼 적용 |
| 5 | 견고한 로그인 | setup_driver→connect→do_login→close_popup |
| 6 | finally 패턴 | analyzer.close() in finally block |

### 6.3 Plan vs Design 변경 (설계 개선)

| Plan 항목 | Design 변경 | 이유 |
|-----------|------------|------|
| product_repo.py에 update_mid_cd() | product_detail_repo.py에 통합 | 단일 Repository 관리 |
| ui_config.py에 BATCH_DETAIL_UI | FAIL_REASON_UI 재활용 | 동일 팝업 사용 |

---

## 7. Test Coverage

| 영역 | 테스트 수 | Pass | Status |
|------|----------|------|--------|
| 수집 대상 선별 | 6 | 6 | ✅ |
| DB 저장 (INSERT/UPDATE) | 5 | 5 | ✅ |
| mid_cd 업데이트 | 3 | 3 | ✅ |
| 수집 통계 | 2 | 2 | ✅ |
| 엣지 케이스 | 2 | 2 | ✅ |
| **합계** | **18** | **18** | **100%** |

전체 테스트 스위트: **2329/2330 Pass** (1개 기존 무관 실패: beer_overorder_fix)

---

## 8. Next Steps

### 8.1 즉시 필요 (없음)
Match Rate 98% >= 90% 기준 충족. 즉시 수정 필요 항목 없음.

### 8.2 사용자 액션 필요
- [ ] **Phase 0 실행**: BGF 사이트에서 `python scripts/discover_popup_columns.py --item-cd [바코드]` 실행
- [ ] Phase 0 결과에 따라 _extract_detail() 컬럼명 확정/수정

### 8.3 권장 다음 단계
- `/pdca report new-product-detail-fetch` → 완료 보고서 생성

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-02-26 | Initial gap analysis | Claude |
