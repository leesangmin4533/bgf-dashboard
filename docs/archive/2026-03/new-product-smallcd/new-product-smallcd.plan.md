# Plan: new-product-smallcd

## 목적

신제품 초기발주 시 **small_cd(소분류)** 기반 유사상품 매칭으로 정밀화하여,
초기발주 부스트의 정확도를 향상시킨다.

## 현재 문제

1. **과대한 매칭 범위**: `calculate_similar_avg()`가 `mid_cd(중분류, 72종)` 전체를 대상으로
   유사상품 중위값을 계산 → 정식도시락 신상품에 김밥/주먹밥 판매량이 혼입
2. **부정확한 초기발주**: `similar_avg * 0.7` 보정값이 실제 해당 소분류의 판매 패턴과 괴리
3. **예시**: mid_cd=001(도시락) 하위에 정식도시락(small_cd=001), 한끼도시락(small_cd=002),
   용기도시락(small_cd=003) 등이 혼재 → 정식도시락 신상품에 한끼도시락 판매량이 섞임

## 해결 방안

1. `calculate_similar_avg()`에 `small_cd` 파라미터 추가
2. **small_cd 우선 조회**: product_details.small_cd 기준으로 유사상품 필터링
3. **폴백 로직**: small_cd 내 유사상품 수 < 3개이면 기존 mid_cd 전체 폴백
4. `_apply_new_product_boost()`에서 small_cd 전달
5. `_load_new_product_cache()`에서 small_cd 정보 포함

## 영향 범위

| 파일 | 변경 내용 |
|------|----------|
| `src/application/services/new_product_monitor.py` | `calculate_similar_avg()` small_cd 우선 조회 |
| `src/prediction/improved_predictor.py` | `_apply_new_product_boost()` small_cd 전달, 캐시에 small_cd 포함 |
| `tests/test_new_product_smallcd.py` | 신규 테스트 파일 |

## 리스크

| 리스크 | 대응 |
|--------|------|
| small_cd NULL/미수집 상품 | mid_cd 폴백으로 기존 동작 유지 |
| small_cd 내 상품 수 부족(< 3개) | mid_cd 폴백으로 통계적 의미 보존 |
| product_details에 small_cd 컬럼 없는 DB | 이미 v32 스키마에 존재, 5,214건 수집 완료 |
| 기존 테스트 호환성 | calculate_similar_avg의 small_cd 기본값 None → 기존 호출 무변경 |

## 의존성

- `product_details.small_cd` 컬럼 (이미 존재)
- `idx_product_details_small` 인덱스 (이미 존재)
- `products.mid_cd` (폴백용, 이미 사용 중)

## 성공 기준

- small_cd 기반 유사상품 매칭 시 동일 소분류 상품만 집계됨
- small_cd 내 상품 < 3개일 때 mid_cd 폴백 정상 동작
- small_cd=NULL인 상품은 기존 mid_cd 로직 그대로 적용
- 기존 20개 test_new_product_lifecycle.py 테스트 모두 통과
- 신규 테스트 전체 통과
