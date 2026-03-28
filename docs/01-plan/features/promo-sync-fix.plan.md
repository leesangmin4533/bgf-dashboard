# Plan: promo-sync-fix

> 신제품 행사 수집 지연으로 인한 발주 누락 수정

## 1. 문제 정의

### 현상
- 신제품(예: 8801155745677 순하리화이트복숭아) 1+1 행사인데 1개만 발주
- 행사 시작(3/16) 후 12일간 행사 보정 미적용

### 근본 원인
- 예측 파이프라인이 `promotions` 테이블만 조회 (PromotionManager)
- 신제품은 Phase 2 prefetch에서 처음 수집될 때까지 `promotions`에 미등록
- `product_details.promo_type`에는 행사 있지만, 예측 시스템이 이를 참조 안 함

### 영향 범위
- 46513 기준: 295건 (유효 행사 있지만 promotions에 미등록)
- 전 매장 공통 패턴 (신제품 입고 시마다 발생)

## 2. 수정 방안

### 방안 C: Phase 1.68에 promotions 사전 수집 대상 확장

**핵심 아이디어**: Phase 1.68(DirectAPI Stock Refresh)의 조회 대상 목록에 "product_details에 행사가 있는데 promotions에 없는 상품"을 추가

### 장점
- 기존 API(selSearch) 그대로 사용, 새 API 불필요
- PromotionManager 코드 변경 없음 (promotions 테이블에 정상 등록)
- stale/이중적용 위험 없음
- Phase 순서 변경 없음

### 예상 비용
- 추가 ~295건 x 0.15초 / 5(동시) = **~9초**
- 일상 운영 시 신규 유입분만 → 수 건~수십 건 → **1~2초**

## 3. 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/infrastructure/database/repos/inventory_repo.py` | `get_promo_missing_item_codes()` 메서드 추가 |
| `src/scheduler/daily_job.py` | Phase 1.68에 promo_missing_items 병합 |

### 변경하지 않는 파일 (기존 로직 보존)
- `src/prediction/promotion/promotion_manager.py` — 그대로 promotions 조회
- `src/prediction/improved_predictor.py` — Stage 3/7/8 변경 없음
- `src/order/auto_order.py` — _build_order_item 변경 없음
- `src/prediction/eval_calibrator.py` — 변경 없음
- `src/collectors/order_prep_collector.py` — 변경 없음
- `src/collectors/direct_api_fetcher.py` — 변경 없음

## 4. 구현 상세

### 4-1. inventory_repo.py — 새 메서드

```python
def get_promo_missing_item_codes(self, store_id: str) -> List[str]:
    """product_details에 유효 행사가 있지만 promotions에 미등록인 상품 목록"""
    # 조건:
    #   1. product_details.promo_type IS NOT NULL (행사 있음)
    #   2. product_details.promo_end >= today (아직 유효)
    #   3. product_details.store_id = store_id (매장 일치)
    #   4. promotions에 해당 기간 활성 레코드 없음
    #   5. realtime_inventory.is_available = 1 (취급 상품)
```

### 4-2. daily_job.py — Phase 1.68 확장

```python
# 기존
stale_items = ri_repo.get_stale_stock_item_codes(store_id=store_id)

# 변경
stale_items = ri_repo.get_stale_stock_item_codes(store_id=store_id)
promo_missing = ri_repo.get_promo_missing_item_codes(store_id=store_id)
# 중복 제거 후 병합
refresh_items = list(set(stale_items + promo_missing))
logger.info(f"[Phase 1.68] stale={len(stale_items)}, promo_missing={len(promo_missing)}, total={len(refresh_items)}")
```

## 5. 기존 코드 충돌 분석

| 관점 | 결론 | 근거 |
|------|------|------|
| PromotionManager | 충돌 없음 | promotions 테이블에 정상 INSERT되므로 기존 조회 그대로 작동 |
| Stage 3 (PromotionAdjuster) | 충돌 없음 | promotions에 데이터가 있으므로 current_promo 정상 반환 |
| Stage 7 (promo_floor) | 충돌 없음 | ctx["_promo_current"] 정상 설정 |
| recalculate_need_qty | 충돌 없음 | product_details.promo_type도 동일 시점에 갱신 |
| eval_calibrator verdict | 개선됨 | promotions에 등록되므로 eval_outcomes.promo_type도 정상 기록 |
| DiffFeedback 상호작용 | 충돌 없음 | promo_floor_after_diff 패턴 그대로 유지 |
| Phase 순서 | 변경 없음 | Phase 1.68 내에서 조회 대상만 확장 |

## 6. 테스트 계획

| 테스트 | 내용 |
|--------|------|
| Unit: get_promo_missing_item_codes | 쿼리 정확성 (유효/만료/미등록 구분) |
| Unit: Phase 1.68 병합 | stale + promo_missing 중복 제거 |
| Integration: 신제품 행사 반영 | product_details에만 행사 → Phase 1.68 후 promotions 등록 확인 |
| Integration: 기존 상품 영향 없음 | 이미 promotions에 있는 상품은 대상에서 제외 |
| Regression: 기존 테스트 전체 통과 | pytest 전체 실행 |

## 7. 롤백 계획

- `PROMO_SYNC_ENABLED = True` 토글 추가
- False 시 기존 동작 (stale_items만 조회)
