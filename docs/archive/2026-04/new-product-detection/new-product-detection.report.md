# Completion Report: 신제품 감지 보완 (new-product-detection)

> 완료일: 2026-04-02
> Match Rate: 100%
> PDCA 사이클: Plan -> Design -> Do -> Check -> Report

---

## 1. 요약

| 항목 | 내용 |
|------|------|
| 문제 | SalesCollector가 products에 먼저 등록 -> ReceivingCollector가 신제품 감지 우회 |
| 해결 | products에 있어도 detected_new_products에 없으면 신제품으로 감지 + common.db 공유 |
| 변경 파일 | 2개 수정 + 1개 신규 + 1개 테스트 추가 |
| 테스트 | 6개 신규 테스트 전체 통과 |
| Match Rate | 88% -> 100% (테스트 추가 후) |

---

## 2. 근본 원인

```
02-16  SalesCollector -> products 등록 (판매 수집)
02-19  실제 입고 발생
02-20  ReceivingCollector -> products 조회 -> "이미 있음" -> 신제품 감지 스킵
```

`_get_mid_cd()`가 products 존재 여부만으로 신제품 판단 -> 판매 수집이 먼저 돌면 감지 불가

---

## 3. 구현 내용

### 3.1 DetectedNewProductRepository 확장 (6개 메서드)

| 메서드 | 용도 |
|--------|------|
| `exists()` | store DB에 신제품 존재 확인 |
| `get_all_item_cds()` | 배치 캐싱용 전체 item_cd set |
| `get_all_item_cds_from_common()` | common.db 배치 캐싱 |
| `exists_in_common()` | common.db 존재 확인 |
| `get_from_common()` | common.db 정보 조회 (매장 간 참조) |
| `save_to_common()` | common.db 등록 (ON CONFLICT DO NOTHING) |

### 3.2 ReceivingCollector 변경

| 변경 | 설명 |
|------|------|
| 캐시 변수 | `_detected_item_cds`, `_detected_common_cds` (None = 미로딩) |
| `_load_detected_cache()` | fail-safe: 실패 시 None 유지 -> 감지 스킵 |
| `_get_mid_cd()` 분기 | products에 있어도 detected에 없으면 후보 추가 |
| 30일 필터 | `already_in_products` 상품은 입고 30일 이내만 |
| 5단계 등록 | common.db에도 저장 (실패해도 store DB 독립) |
| `_is_within_days()` | 날짜 범위 체크 헬퍼 |

### 3.3 소급 마이그레이션 스크립트

`scripts/migrate_missing_new_products.py`
- `--dry-run`: 미리보기
- `--days N`: 기간 제한 조정 (기본 30일)
- `--store ID`: 특정 매장만
- products.created_at 필터: 기존 상품 제외

---

## 4. 설계 결정사항 (토론 결과)

| 주제 | 결정 | 이유 |
|------|------|------|
| 캐시 실패 시 | fail-safe (None -> 감지 스킵) | 대량 오감지 방지 우선 |
| common.db 누락 보정 | 불필요 (베스트 에포트) | store DB가 권위 데이터 |
| store/common 비대칭 | 허용 | store=UPSERT, common=DO NOTHING (역할 상이) |
| 시간 제한 | 30일 | 오래된 기존 상품 오감지 방지 |

---

## 5. 루프 방지 불변식

| 불변식 | 보장 방법 |
|--------|-----------|
| 감지 판단 독립성 | store DB만으로 판단, common.db는 참조만 |
| 등록 독립성 | common.db 실패가 store DB 차단 안 함 |
| 멱등성 | UPSERT(store) + DO NOTHING(common) |
| 단방향 의존 | store -> common 쓰기만 |

---

## 6. 테스트 커버리지

| 테스트 | 시나리오 |
|--------|----------|
| `test_already_in_products_added_as_candidate` | products에 있지만 detected에 없는 상품 -> 후보 추가 |
| `test_already_detected_skipped` | detected에 있는 상품 -> 스킵 |
| `test_common_db_failure_does_not_block_store_db` | common.db 실패 -> store DB 정상 |
| `test_30day_filter_skips_old_products` | 30일 초과/NULL/빈값 -> False |
| `test_failsafe_cache_none_skips_detection` | 캐시 None -> 감지 스킵 |
| `test_save_to_common_idempotent` | 2회 호출 -> 1건만 저장 |

---

## 7. 수동 조치 (완료)

- `8803622102594` (한양 오징어숏다리130g): 45일 전 입고로 30일 범위 밖이나 예외로 수동 등록 완료
  - store DB (46513): detected, id=124
  - common.db: 등록 완료

---

## 8. 후속 작업

- [ ] 소급 마이그레이션 스크립트 실제 실행 (`python scripts/migrate_missing_new_products.py`)
- [ ] lifecycle_status detected -> monitoring 전환 확인 (NewProductSettlementService 다음 실행 시)
- [ ] _apply_new_product_boost가 monitoring 상품에 유사상품 보정 적용하는지 확인
