# PDCA Completion Report: promo-sync-fix

> 신제품 행사 사전 동기화 — Phase 1.68 DirectAPI 조회 대상 확장

## 1. 개요

| 항목 | 내용 |
|------|------|
| Feature | promo-sync-fix |
| 날짜 | 2026-03-28 |
| Commit | 3a2464b |
| Match Rate | 97% |
| 테스트 | 13개 PASS, 기존 134개 행사테스트 PASS |

## 2. 문제

신제품(예: 8801155745677 순하리화이트복숭아)이 1+1 행사임에도 1개만 발주.
- **근본 원인**: 예측 파이프라인이 `promotions` 테이블만 조회하는데, 신제품은 Phase 2 prefetch 전까지 이 테이블에 미등록 (최대 12일 지연)
- **영향**: 46513 기준 295건의 행사 상품이 promotions에 미등록

## 3. 해결

Phase 1.68(DirectAPI Stock Refresh)의 조회 대상 확장:

```
기존: stale_items (재고 TTL 초과 상품만)
변경: stale_items + promo_missing_items (행사 미등록 상품 추가)
```

### 수정 파일

| 파일 | 변경 |
|------|------|
| `src/settings/constants.py` | `PROMO_SYNC_ENABLED = True` 토글 |
| `src/infrastructure/database/repos/inventory_repo.py` | `get_promo_missing_item_codes()` 메서드 추가 |
| `src/scheduler/daily_job.py` | Phase 1.68에 promo_missing 병합 + 중복제거 |

### 변경하지 않은 파일 (기존 로직 100% 보존)

promotion_manager.py, improved_predictor.py, auto_order.py, eval_calibrator.py, order_prep_collector.py, direct_api_fetcher.py

## 4. 검증

### 라이브 검증 (BGF 사이트)
- 8801155745677 상품 selSearch 조회 → MONTH_EVT="1+1" 정상 반환 확인
- 신제품(PITEM_ID_NM="신상품")도 API 정상 조회 가능 확인

### 테스트 (13개)

| 테스트 | 내용 |
|--------|------|
| test_basic_missing | 유효 행사 + promotions 미등록 → 대상 포함 |
| test_already_in_promotions | promotions에 이미 있는 상품 → 제외 |
| test_expired_promo_excluded | 만료된 행사 → 제외 |
| test_unavailable_excluded | is_available=0 → 제외 |
| test_no_promo_type_excluded | promo_type NULL → 제외 |
| test_empty_promo_type_excluded | promo_type='' → 제외 |
| test_mixed_scenario | 혼합 조건 7개 상품 → 2개만 대상 |
| test_exception_returns_empty | DB 오류 시 빈 리스트 (플로우 중단 방지) |
| test_merge_dedup | stale + promo_missing 중복 제거 |
| test_empty_promo_missing | promo_missing 비어있으면 stale만 |
| test_empty_stale | stale 비어있고 promo만 |
| test_both_empty | 둘 다 비어있으면 빈 리스트 |
| test_toggle_disabled | PROMO_SYNC_ENABLED=False 비활성 확인 |

### Gap 분석

| Gap | 심각도 | 내용 |
|-----|--------|------|
| G-1 | Low | ri.store_id로 필터 (pd.store_id 대신) — store DB 분리 구조상 안전 |
| G-2 | Cosmetic | 로그 라벨 promo_sync vs promo_missing |
| G-3 | Medium | 통합 테스트 1건 누락 (단위 13개로 커버) |

## 5. 성능 영향

| 항목 | 값 |
|------|---|
| 추가 API 호출 | 기존 selSearch 동일 (새 API 없음) |
| 추가 건수 | ~295건 (초회), 이후 일일 수 건 |
| 추가 소요시간 | ~5-10초 (동시처리 5개) |
| 롤백 | `PROMO_SYNC_ENABLED = False` |

## 6. 학습 사항

- **데이터 소스 불일치 주의**: product_details와 promotions에 같은 정보가 있지만 수집 시점이 다름
- **Phase 순서 의존성**: 예측이 prefetch 전에 실행되면 stale 데이터 사용
- **product_details.promo_type은 폴백에 부적합**: 만료 미정리(58%), store 격리 없음, next_month 블리딩 위험
- **기존 API 확장이 최선**: 새 API 추가 없이 조회 대상 목록만 확장하는 것이 가장 안전
