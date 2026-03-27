# Analysis: new-product-smallcd

## 설계 대비 구현 비교

### 1. `src/application/services/new_product_monitor.py`

| 설계 항목 | 구현 상태 | 일치 |
|-----------|----------|------|
| `calculate_similar_avg()` small_cd 파라미터 추가 | `small_cd: Optional[str] = None` 추가 | O |
| small_cd 우선 조회 SQL (product_details JOIN) | `_query_similar_avg_by_smallcd()` 별도 메서드 | O |
| small_cd 내 상품 < 3개 시 mid_cd 폴백 | `SMALL_CD_MIN_SIMILAR = 3` 상수 + 폴백 분기 | O |
| `_query_similar_avg_by_midcd()` 기존 로직 분리 | 별도 메서드로 추출 | O |
| `_get_small_cd()` 신규 private 메서드 | `DBRouter.get_common_connection()` + product_details 조회 | O |
| `update_lifecycle_status()` 호출부 변경 | `small_cd = self._get_small_cd(item_cd)` 후 전달 | O |
| 로깅 (small_cd 기반 매칭 성공/폴백) | INFO + DEBUG 레벨 로그 추가 | O |

### 2. `src/prediction/improved_predictor.py`

| 설계 항목 | 구현 상태 | 일치 |
|-----------|----------|------|
| `_load_new_product_cache()` small_cd 보강 호출 | `_enrich_cache_with_small_cd()` 호출 추가 | O |
| `_enrich_cache_with_small_cd()` 신규 메서드 | product_details 배치 조회 → 캐시 병합 | O |
| `_apply_new_product_boost()` 로그에 small_cd 포함 | `small_cd={small_cd}` 추가 | O |
| 보정 로직 자체 변경 없음 | `max(similar_avg * 0.7, prediction)` 유지 | O |

### 3. `tests/test_new_product_smallcd.py`

| 설계 테스트 | 구현 상태 | 일치 |
|-------------|----------|------|
| test_smallcd_similar_avg | 구현 완료 | O |
| test_smallcd_fallback_insufficient | 구현 완료 | O |
| test_smallcd_fallback_null | 구현 완료 | O |
| test_smallcd_boost_applied | 구현 완료 | O |
| test_smallcd_boost_with_midcd_fallback | 구현 완료 | O |
| test_smallcd_cache_enrichment | 구현 완료 | O |
| test_smallcd_mixed_scenario | 구현 완료 | O |
| test_get_small_cd_exists | 구현 완료 | O |
| test_get_small_cd_not_found | 구현 완료 | O |
| test_smallcd_only_same_category | 구현 완료 | O |
| test_smallcd_empty_string | 구현 완료 | O |
| test_boost_log_includes_smallcd | 구현 완료 | O |

## Match Rate 계산

- 설계 항목 총 19건 (구현 변경 7 + predictor 4 + 테스트 12 = 23건 중 중복 제거)
- 구현 일치 19건
- **Match Rate: 100% (19/19)**

## 추가 구현 (설계 대비)

- 설계에 없었으나 추가한 항목: 없음

## 누락 항목

- 없음

## 기존 테스트 호환성

- `tests/test_new_product_lifecycle.py`: 20개 전체 통과 (regression 없음)
- `calculate_similar_avg()`의 `small_cd` 파라미터 기본값 `None` → 기존 호출 무변경

## 리스크 대응 검증

| 리스크 | 대응 | 검증 |
|--------|------|------|
| small_cd NULL 상품 | mid_cd 폴백 | test_smallcd_fallback_null 통과 |
| small_cd "" 상품 | mid_cd 폴백 | test_smallcd_empty_string 통과 |
| small_cd 내 상품 부족 | mid_cd 폴백 | test_smallcd_fallback_insufficient 통과 |
| 기존 테스트 호환 | 기본값 None | 20개 기존 테스트 통과 |
| 다른 small_cd 혼입 | SQL WHERE 필터 | test_smallcd_only_same_category 통과 |
