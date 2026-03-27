# dryrun-accuracy Planning Document

> **Summary**: 드라이런(run_full_flow.py)과 7시 스케줄러(execute())의 미입고/재고 조정 경로를 통일하여 드라이런 결과의 정확도를 실제 발주와 동일 수준으로 향상
>
> **Project**: BGF 자동 발주 시스템
> **Author**: AI
> **Date**: 2026-03-09
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

드라이런(`run_full_flow.py`)의 최종 발주량이 실제 7시 스케줄러(`execute()`)와 차이가 나는 문제를 해결한다.
예측 파이프라인(`get_recommendations()`)은 동일하지만, **미입고/재고 조정 단계의 데이터 소스**가 달라서 최종 발주량에 차이가 발생한다.

### 1.2 Background

| 항목 | 7시 스케줄러 (execute) | 드라이런 (run_dryrun_and_export) |
|---|---|---|
| 미입고/재고 | BGF Selenium **실시간** 조회 | DB 캐시 (realtime_inventory) |
| CUT 의심 재조회 | Selenium으로 stale 상품 재확인 | **생략** (driver=None) |
| 자동/스마트발주 | 사이트 조회 가능 | 항상 DB 캐시만 |
| CUT 실시간 감지 | prefetch에서 새 CUT 발견 | DB 시점의 CUT만 |

**실제 영향 사례**:
- realtime_inventory의 queried_at이 수일 전 → stale 재고로 발주량 과소/과다
- 실제로 CUT된 상품이 DB에 미반영 → 드라이런에서 CUT 상품 발주
- 미입고 상품이 이미 도착했는데 DB에 pending으로 남아있음 → 드라이런 발주량 과소

### 1.3 Related Documents

- `scripts/run_full_flow.py`: 드라이런 엔트리포인트 (run_dryrun_and_export, L535~700)
- `src/order/auto_order.py`: execute() (L1120~1445), get_recommendations() (L775~1049)
- `src/order/order_adjuster.py`: apply_pending_and_stock() (L108~296)
- `src/order/order_data_loader.py`: prefetch_pending() (L184~260)

---

## 2. Scope

### 2.1 In Scope

- [x] run_dryrun_and_export()의 미입고/재고 데이터 소스를 execute()와 최대한 동일하게 통일
- [x] DB 캐시 데이터의 stale 경고 표시
- [x] CUT/미취급 상태 DB 반영 시점 표시
- [x] 드라이런 결과에 "실제 스케줄러와의 차이점" 요약 표시

### 2.2 Out of Scope

- Selenium 드라이버를 드라이런에서 실행 (드라이런의 핵심 가치는 오프라인 실행)
- execute() 자체의 로직 변경
- 예측 파이프라인(get_recommendations) 변경

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | RI stale 경고: realtime_inventory.queried_at 기준 N시간 이상 경과 상품 경고 | High | Pending |
| FR-02 | order_tracking 미입고 병합 강화: RI + OT + daily_sales 3중 소스 교차 검증 | High | Pending |
| FR-03 | CUT 상태 stale 경고: CUT 판정이 N일 이상 미확인된 상품 경고 | Medium | Pending |
| FR-04 | 드라이런 Excel에 data_freshness 컬럼 추가 (RI queried_at 기준) | Medium | Pending |
| FR-05 | 드라이런 요약에 "스케줄러 차이 경고" 섹션 추가 | Low | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 정확도 | 드라이런 vs 실제 발주의 발주량 차이 10% 이내 | 실제 발주 로그 비교 |
| 성능 | 드라이런 실행 시간 증가 5초 이내 | 실행 시간 측정 |
| 호환성 | 기존 3077개 테스트 전부 통과 | pytest 실행 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] RI stale 경고 로직 구현 및 Excel에 freshness 표시
- [ ] order_tracking 미입고 데이터 교차 검증 강화
- [ ] CUT 상태 신선도 경고 구현
- [ ] 드라이런 요약에 차이 경고 섹션 추가
- [ ] 테스트 추가 및 기존 테스트 통과

### 4.2 Quality Criteria

- [ ] Match Rate 95% 이상 (gap-detector)
- [ ] 기존 테스트 전부 통과
- [ ] 드라이런 결과에 신선도 정보 표시 확인

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| RI 데이터가 전부 stale이면 경고가 너무 많음 | Medium | High | stale 기준을 카테고리별로 차등 (푸드=6h, 비푸드=24h) |
| order_tracking 데이터도 stale일 수 있음 | Medium | Medium | OT created_at 기준으로 12h 이내만 유효 처리 |
| CUT 해제된 상품이 DB에 CUT로 남아있을 수 있음 | Low | Low | stale CUT 경고 + 수동 확인 권유 |

---

## 6. Architecture Considerations

### 6.1 Project Level

**Enterprise** (기존 프로젝트 레벨 유지)

### 6.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Stale 판정 기준 | 고정시간 / 카테고리별 차등 | 카테고리별 차등 | 푸드(1일유통)는 6h, 비푸드는 24h가 적절 |
| 데이터 보강 소스 | RI만 / RI+OT / RI+OT+daily_sales | RI+OT (기존) | daily_sales는 stock_qty 정확도 낮음 |
| 경고 표시 위치 | 콘솔만 / Excel만 / 양쪽 | 양쪽 | 콘솔 실시간 + Excel 기록용 |

### 6.3 수정 대상 파일

```
scripts/run_full_flow.py          # 드라이런 메인 — stale 경고, 교차검증
  ├ run_dryrun_and_export()       # 미입고/재고 조정 단계 강화
  └ create_dryrun_excel()         # freshness 컬럼 추가

src/order/order_adjuster.py       # (변경 불필요 — 동일 함수 사용)
```

---

## 7. 상세 구현 계획

### Fix A: RI stale 경고 (FR-01, FR-04)

**파일**: `scripts/run_full_flow.py` run_dryrun_and_export() L607~660

```python
# 기존: all_inv = inv_repo.get_all(store_id=store_id)
# 추가: stale 판정
from datetime import datetime, timedelta

STALE_HOURS = {"food": 6, "default": 24}
FOOD_MIDS = {"001","002","003","004","005","012","014"}

stale_items = []
for item in all_inv:
    queried_at = item.get("queried_at")
    if not queried_at:
        stale_items.append(item.get("item_cd"))
        continue
    try:
        qt = datetime.fromisoformat(queried_at)
        mid_cd = item.get("mid_cd", "")
        threshold_h = STALE_HOURS["food"] if mid_cd in FOOD_MIDS else STALE_HOURS["default"]
        if datetime.now() - qt > timedelta(hours=threshold_h):
            stale_items.append(item.get("item_cd"))
    except:
        pass

if stale_items:
    print(f"  [경고] RI 데이터 stale: {len(stale_items)}개 상품 "
          f"(푸드>{STALE_HOURS['food']}h, 비푸드>{STALE_HOURS['default']}h)")
```

### Fix B: Excel freshness 컬럼 (FR-04)

**파일**: `scripts/run_full_flow.py` create_dryrun_excel()

- order_list 각 항목에 `ri_queried_at` 필드 추가
- Excel에 "RI조회시각" 컬럼(AD열) 추가
- stale 항목은 빨간 배경색 표시

### Fix C: 드라이런 요약 차이 경고 (FR-05)

**파일**: `scripts/run_full_flow.py` run_dryrun_and_export() 말미

```
[차이 경고] 실제 7시 스케줄러와 다를 수 있는 항목:
  - RI stale 상품: 23개 (재고/미입고가 실제와 다를 수 있음)
  - CUT 미확인: 5개 (실제로 CUT 해제되었을 수 있음)
  - 자동/스마트발주: DB 캐시 사용 (사이트 최신 목록과 다를 수 있음)
```

---

## 8. Next Steps

1. [ ] Design 문서 작성 (`dryrun-accuracy.design.md`)
2. [ ] 구현 (Fix A → B → C 순서)
3. [ ] 테스트 추가
4. [ ] Gap Analysis

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-09 | Initial draft | AI |
