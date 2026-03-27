# beer-overorder-fix 완료 보고서

> **Feature**: beer-overorder-fix (맥주 과발주 수정)
> **Status**: COMPLETED
> **Match Rate**: 100%
> **Date**: 2026-02-25
> **Duration**: 1 session (~2시간)

---

## 1. 요약

46513 매장 자동발주(2026-02-25 07:00)에서 맥주(049) 카테고리 전반에 과발주 발생.
샘플 상품 4901777153325 (산토리 맥주캔 500ml)가 재고 19개임에도 추가 4개(1배수) 발주.

**근본 원인 2건 식별 및 수정 완료.**

---

## 2. 근본 원인

### 원인 1: stale RI=0 폴백 로직 버그 (Critical)

**위치**: `src/prediction/improved_predictor.py` `_resolve_stock_and_pending()`

| 항목 | 내용 |
|------|------|
| 현상 | realtime_inventory가 stale(TTL 초과)이고 stock_qty=0일 때, daily_sales에 재고 19개가 있어도 ri=0 채택 |
| 원인 | `ds_stock(19) < ri_stock(0)` 비교가 항상 False (양수는 0보다 클 수 없음의 역) |
| 영향 | 맥주 외 전 카테고리에서 stale RI=0인 상품 전부 |
| 심각도 | **Critical** — 재고가 있는데 없다고 판단 → 불필요한 발주 |

### 원인 2: prefetch 한도 부족 (Medium)

**위치**: `src/order/auto_order.py` `execute()`

| 항목 | 내용 |
|------|------|
| 현상 | max_pending_items=200인데 후보 449개 → 249개 상품이 BGF 실시간 재고 미조회 |
| 원인 | 기본값 200이 실제 발주 후보 수보다 적음 |
| 영향 | 미조회 상품은 stale RI 값 그대로 사용 → 원인 1과 결합 시 과발주 |
| 심각도 | **Medium** — 원인 1이 수정되면 영향 감소하나, 정확도 향상을 위해 증가 필요 |

---

## 3. 수정 내역

### 3.1 stale RI=0 폴백 수정

**파일**: `src/prediction/improved_predictor.py` (lines 1438-1458)

`ri_stock == 0 and ds_stock > 0` 조건을 최상위에 추가. stale 상태에서 ri=0은 "재고 없음"이 아니라 "만료된 데이터"이므로, daily_sales에 양수 재고가 있으면 ds를 채택.

| 시나리오 | 수정 전 | 수정 후 |
|----------|---------|---------|
| stale RI=0, ds=19 | ri=0 채택 (버그) | ds=19 채택 |
| stale RI=0, ds=0 | ri=0 채택 | ri=0 채택 (변경 없음) |
| stale RI=15, ds=10 | ds=10 채택 | ds=10 채택 (변경 없음) |
| stale RI=10, ds=15 | ri=10 채택 | ri=10 채택 (변경 없음) |
| fresh RI=5 | ri=5 채택 | ri=5 채택 (변경 없음) |

신규 `stock_source` 값: `"ri_stale_ds_nonzero"` (모니터링용)

### 3.2 prefetch 한도 증가

**파일**: `src/order/auto_order.py` (line 1064)

`max_pending_items: int = 200` → `max_pending_items: int = 500`

예상 시간 영향: +4~5분 (전체 플로우 45분 → 50분)

---

## 4. 테스트

### 신규 테스트 파일: `tests/test_beer_overorder_fix.py`

| 클래스 | 테스트 수 | 커버 영역 |
|--------|-----------|-----------|
| TestStaleRIFallback | 7 | stale RI 폴백 전체 시나리오 |
| TestStaleRIPendingBehavior | 2 | stale 상태 pending 처리 |
| TestBeerWithCorrectStock | 3 | 맥주 need_qty 계산 e2e |
| TestPrefetchLimit | 2 | prefetch 한도 기본값 + 실행 |
| **합계** | **14** | |

### 전체 테스트 결과

```
2130 passed, 0 failed (7분 36초)
```

기존 2116개 테스트 + 신규 14개 = 2130개, regression 없음.

---

## 5. PDCA 사이클

| Phase | 산출물 | 상태 |
|-------|--------|------|
| Plan | `docs/01-plan/features/beer-overorder-fix.plan.md` | 완료 |
| Design | `docs/02-design/features/beer-overorder-fix.design.md` | 완료 |
| Do | improved_predictor.py + auto_order.py + 테스트 14개 | 완료 |
| Check | `docs/03-analysis/beer-overorder-fix.analysis.md` — Match Rate 100% | 완료 |
| Act | N/A (100%이므로 iterate 불필요) | 해당 없음 |

---

## 6. 수정 파일 목록

| 파일 | 변경 유형 | LOC |
|------|-----------|-----|
| `src/prediction/improved_predictor.py` | 수정 (stale RI=0 분기 추가) | +8 |
| `src/order/auto_order.py` | 수정 (파라미터 기본값) | +0 (값만 변경) |
| `tests/test_beer_overorder_fix.py` | 신규 (14개 테스트) | +284 |
| `docs/01-plan/features/beer-overorder-fix.plan.md` | 신규 | Plan 문서 |
| `docs/02-design/features/beer-overorder-fix.design.md` | 신규 | Design 문서 |
| `docs/03-analysis/beer-overorder-fix.analysis.md` | 신규 | Gap 분석 |

---

## 7. 기대 효과

| 항목 | Before | After |
|------|--------|-------|
| stale RI=0 상품 재고 인식 | 0 (잘못됨) | daily_sales 실재고 반영 |
| 맥주 과발주 | 재고 19개에도 4개 추가 발주 | 발주 불필요 → 0 |
| prefetch 커버리지 | 200/449 (44.5%) | 500/449 (100%) |
| 전 카테고리 영향 | stale RI=0 상품 전부 과발주 위험 | 해소 |

---

## 8. 후속 모니터링

1. **즉시**: 다음 자동발주(내일 07:00)에서 맥주 과발주 해소 확인
2. **1주**: `stock_source="ri_stale_ds_nonzero"` 빈도 모니터링 (prediction_logs)
3. **1주**: 전체 플로우 실행 시간 50분 이내 유지 확인

---

## 9. 교훈

- **비교 연산의 함정**: `ds < ri`에서 ri=0이면 양수 ds는 항상 False. 0과의 비교는 별도 분기로 처리해야 함
- **stale 데이터의 의미**: stale 상태에서 값=0은 "없다"가 아니라 "모른다"를 의미. 다른 소스에 양수가 있으면 신뢰해야 함
- **prefetch 한도 설정**: 고정 한도(200)보다 실제 후보 수에 비례하는 동적 한도가 더 안전

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-25 | 초기 보고서 | report-generator |
