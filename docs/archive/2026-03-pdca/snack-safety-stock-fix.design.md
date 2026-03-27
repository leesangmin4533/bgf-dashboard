# Design: Phase 1.68 — DirectAPI 실시간 재고 갱신 (예측 전)

> **Date**: 2026-03-24 | **Feature**: snack-safety-stock-fix (C안 확장)

## 1. Overview

Phase 1.65(유령재고 정리) 후, Phase 1.7(예측) 전에 **DirectAPI selSearch로 stale 상품의 실시간 재고(NOW_QTY)를 조회하여 RI를 갱신**. 예측 파이프라인이 정확한 재고 기반으로 동작하게 함.

## 2. Current Flow → New Flow

```
현재:
  Phase 1.65  유령재고 정리 (stale → stock=0)
  Phase 1.66  배치 FIFO 동기화
  Phase 1.67  데이터 무결성 검증
  Phase 1.7   예측 (stale된 stk=0으로 과잉발주)

변경:
  Phase 1.65  유령재고 정리 (active 배치 보호 ← 구현 완료)
  Phase 1.66  배치 FIFO 동기화
  Phase 1.67  데이터 무결성 검증
  Phase 1.68  [NEW] DirectAPI 실시간 재고 갱신
              1) stale RI 대상 추출
              2) 단품별 발주 메뉴 이동 + 템플릿 캡처
              3) selSearch 배치 조회 (NOW_QTY)
              4) RI.stock_qty + queried_at 갱신
  Phase 1.7   예측 (정확한 재고 기반)
```

## 3. Phase 1.68 상세 설계

### 3.1 대상 추출

```python
# stale 판정: queried_at이 유통기한 기반 TTL보다 오래된 상품
# cleanup_stale_stock과 동일한 기준 (중복 로직 없이 메서드 재활용)
stale_items = inventory_repo.get_stale_item_codes(store_id)
```

`get_stale_item_codes()`: 기존 `cleanup_stale_stock`의 stale 판정 루프를 분리하여 item_cd 목록만 반환하는 메서드. stock_qty > 0이고 queried_at이 TTL 초과인 상품.

**예상 건수**: 44~92개/매장 (active 배치 보호 후 미보호분)

### 3.2 화면 이동 + 템플릿 캡처

```python
# order_prep_collector의 기존 인프라 재활용
collector = OrderPrepCollector(driver=driver, store_id=store_id)

# 1) XHR 인터셉터 설치
collector._direct_api.install_interceptor()

# 2) 단품별 발주 메뉴 이동
collector.navigate_to_menu()

# 3) 날짜 선택 (오늘 또는 내일)
collector.select_order_date()

# 4) 첫 번째 상품 Selenium 검색 → selSearch 요청 캡처
collector.collect_for_item(stale_items[0])
collector._direct_api.capture_request_template()
```

### 3.3 배치 조회 + RI 갱신

```python
# DirectAPI 배치 조회 (0.1초/건)
results = collector._direct_api.batch_fetch(stale_items[1:])

# RI 갱신
for item_cd, data in results.items():
    now_qty = data.get('current_stock', 0)
    inventory_repo.save(
        item_cd=item_cd,
        stock_qty=now_qty,
        pending_qty=data.get('pending_qty', 0),
        order_unit_qty=data.get('order_unit_qty', 1),
        is_available=True,
        item_nm=data.get('item_nm', ''),
        store_id=store_id,
        preserve_stock_if_zero=True,  # NOW_QTY=0이면 기존 보존
    )
```

### 3.4 Phase 2와의 관계

Phase 1.68에서 이미 단품별 발주 화면에 진입 + 템플릿 캡처가 완료됨.
→ Phase 2의 `order_prep_collector._collect_via_direct_api()`는 **템플릿 재사용** 가능.
→ Phase 2에서 메뉴 이동/캡처 단계 스킵 → **Phase 2 속도 향상** 부수 효과.

## 4. 구현 파일

| # | File | Change |
|:-:|------|--------|
| 1 | `src/infrastructure/database/repos/inventory_repo.py` | `get_stale_item_codes()` 메서드 추가 |
| 2 | `src/scheduler/daily_job.py` | Phase 1.68 블록 추가 (L749 뒤) |
| 3 | (없음) | order_prep_collector, direct_api_fetcher — 기존 코드 그대로 재활용 |

### 4.1 inventory_repo.py — get_stale_item_codes()

```python
def get_stale_item_codes(self, store_id: Optional[str] = None) -> List[str]:
    """유통기한 TTL 초과 상품의 item_cd 목록 반환.

    cleanup_stale_stock과 동일한 stale 판정 기준.
    stock_qty > 0인 상품만 대상 (정리 대상과 동일).
    active 배치 보호 상품은 제외하지 않음 (갱신 대상이므로).
    """
```

### 4.2 daily_job.py — Phase 1.68

```python
# ★ Phase 1.68: DirectAPI 실시간 재고 갱신 (예측 전)
if run_auto_order and collection_success:
    try:
        from src.infrastructure.database.repos.inventory_repo import (
            RealtimeInventoryRepository
        )
        ri_repo = RealtimeInventoryRepository(store_id=self.store_id)
        stale_items = ri_repo.get_stale_item_codes(store_id=self.store_id)

        if stale_items:
            logger.info(f"[Phase 1.68] DirectAPI Stock Refresh: {len(stale_items)}개 stale 상품")
            driver = self.collector.get_driver()
            if driver:
                from src.collectors.order_prep_collector import OrderPrepCollector
                stock_collector = OrderPrepCollector(
                    driver=driver, store_id=self.store_id,
                    save_to_db=True,  # RI 자동 갱신
                )
                api_results = stock_collector._collect_via_direct_api(stale_items)

                if api_results:
                    refreshed = len([r for r in api_results.values() if r.get('success')])
                    logger.info(f"[Phase 1.68] RI 갱신 완료: {refreshed}/{len(stale_items)}건")
                else:
                    # DirectAPI 실패 → Selenium 폴백 (건수 제한)
                    max_selenium = min(20, len(stale_items))
                    logger.info(f"[Phase 1.68] DirectAPI 실패 → Selenium {max_selenium}건 폴백")
                    for item_cd in stale_items[:max_selenium]:
                        try:
                            stock_collector.collect_for_item(item_cd)
                        except Exception:
                            pass
            else:
                logger.warning("[Phase 1.68] 드라이버 없음 — 스킵")
        else:
            logger.info("[Phase 1.68] stale 상품 없음 — 스킵")
    except Exception as e:
        logger.warning(f"[Phase 1.68] 실시간 재고 갱신 실패 (발주 플로우 계속): {e}")
```

## 5. 시간 예측

| 단계 | 소요 시간 |
|------|:---------:|
| stale 목록 조회 (DB) | < 1초 |
| 메뉴 이동 + 날짜 선택 | ~5초 |
| 템플릿 캡처 (Selenium 1건) | ~3초 |
| DirectAPI 배치 조회 (50~90건) | 5~9초 |
| RI 갱신 (DB) | < 1초 |
| **총합** | **~15초** |

Phase 2에서 메뉴 이동+캡처 스킵 효과: **-8초** → 순증 **~7초**.

## 6. 실패 시 동작

| 실패 지점 | 동작 |
|-----------|------|
| stale 목록 없음 | 스킵 (정상) |
| 드라이버 없음 | 스킵 (경고 로그) |
| 메뉴 이동 실패 | 스킵 → Phase 2에서 재시도 |
| DirectAPI 실패 | Selenium 최대 20건 폴백 |
| 전체 예외 | 경고 로그 + 발주 플로우 계속 |

**Phase 1.68 실패가 발주 플로우를 중단하지 않음** (기존 패턴과 동일).

## 7. 토글

```python
# constants.py
DIRECTAPI_STOCK_REFRESH_ENABLED = True
DIRECTAPI_STOCK_REFRESH_MAX_SELENIUM_FALLBACK = 20
```

## 8. 검증 시나리오

1. **stale 상품 존재 시**: NOW_QTY로 RI 갱신 → Phase 1.7 예측에서 정확한 stk 사용
2. **stale 없을 시**: "stale 상품 없음 — 스킵" 로그만
3. **DirectAPI 실패 시**: Selenium 20건 폴백 → 일부 갱신
4. **Phase 2 템플릿 재사용**: Phase 1.68에서 캡처한 템플릿으로 Phase 2 바로 배치 조회
