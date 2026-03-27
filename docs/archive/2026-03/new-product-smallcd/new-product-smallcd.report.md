# Report: new-product-smallcd

## 요약

신제품 초기발주 부스트에서 **small_cd(소분류)** 기반 유사상품 매칭을 우선 적용하고,
데이터 부족 시 기존 **mid_cd(중분류)** 폴백하는 2단계 매칭 로직을 구현하였다.

- **Match Rate: 100%** (설계 19항목 전체 일치)
- **신규 테스트: 12건** 전체 통과
- **기존 테스트: 20건** regression 없음 (test_new_product_lifecycle.py)

## 변경 파일

| 파일 | 변경 유형 | 변경 내용 |
|------|----------|----------|
| `src/application/services/new_product_monitor.py` | 수정 | `calculate_similar_avg()` small_cd 우선 조회 + 폴백, `_get_small_cd()` 추가, `_query_similar_avg_by_smallcd()` / `_query_similar_avg_by_midcd()` 분리 |
| `src/prediction/improved_predictor.py` | 수정 | `_load_new_product_cache()` small_cd 보강, `_enrich_cache_with_small_cd()` 추가, `_apply_new_product_boost()` 로그 개선 |
| `tests/test_new_product_smallcd.py` | 신규 | 12개 테스트 (4 class, 12 methods) |
| `docs/01-plan/features/new-product-smallcd.plan.md` | 신규 | 계획 문서 |
| `docs/02-design/features/new-product-smallcd.design.md` | 신규 | 설계 문서 |
| `docs/03-analysis/features/new-product-smallcd.analysis.md` | 신규 | 분석 문서 |
| `docs/04-report/features/new-product-smallcd.report.md` | 신규 | 본 문서 |

## 핵심 로직

### 2단계 유사상품 매칭

```
1. small_cd 유효 (not None, not "")
   ├─ small_cd 내 유사상품 >= 3개 → small_cd 기반 중위값 반환
   └─ small_cd 내 유사상품 < 3개 → mid_cd 폴백
2. small_cd 무효 → mid_cd 전체 기반 중위값 반환 (기존 동작)
```

### 캐시 보강

`_load_new_product_cache()` 실행 시 product_details에서 배치 조회하여
각 캐시 아이템에 `small_cd` 필드를 병합. 이후 `_apply_new_product_boost()`에서
로그에 small_cd 정보를 포함하여 추적성 향상.

## 테스트 결과

```
tests/test_new_product_smallcd.py  12 passed  (10.05s)
tests/test_new_product_lifecycle.py  20 passed  (9.88s)
```

### 테스트 목록

| # | 테스트 | 결과 |
|---|--------|------|
| 1 | test_smallcd_similar_avg | PASSED |
| 2 | test_smallcd_fallback_insufficient | PASSED |
| 3 | test_smallcd_fallback_null | PASSED |
| 4 | test_smallcd_only_same_category | PASSED |
| 5 | test_smallcd_empty_string | PASSED |
| 6 | test_get_small_cd_exists | PASSED |
| 7 | test_get_small_cd_not_found | PASSED |
| 8 | test_smallcd_boost_applied | PASSED |
| 9 | test_smallcd_boost_with_midcd_fallback | PASSED |
| 10 | test_boost_log_includes_smallcd | PASSED |
| 11 | test_smallcd_cache_enrichment | PASSED |
| 12 | test_smallcd_mixed_scenario | PASSED |

## DB 변경

없음. `product_details.small_cd` 컬럼과 `idx_product_details_small` 인덱스는 이미 존재.

## 후속 작업

- 운영 환경에서 small_cd 기반 매칭 비율 모니터링 (로그 `[유사상품]` 태그)
- small_cd 수집률 향상 시 SMALL_CD_MIN_SIMILAR 임계값 조정 검토
