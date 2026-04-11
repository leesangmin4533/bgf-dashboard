# Plan — batch-sale-time-consume

**Feature**: batch-sale-time-consume (판매 시점 배치 차감)
**Priority**: P1 (폐기 감지 정확도 근본 개선)
**Created**: 2026-04-11
**Issue-Chain**: expiry-tracking#batch-sale-time-consume

---

## 1. 배경 (Why)

현재 BatchSync는 **stock_qty(상품 전체 재고) 역산**으로 FIFO 차감.
stock_qty는 "이 상품의 전체 재고"이고, 배치는 "입고일별 개별 단위".
이 둘을 직접 매핑하면 필연적으로 오차 발생.

| 날짜 | 문제 | 원인 |
|------|------|------|
| 04-10 | remaining=3 실물=0 (과다) | 가드가 차감 차단 |
| 04-11 | remaining=0 실물=1 (과소) | 가드 면제 후 역산 오차 |

**근본 원인**: "어떤 배치에서 팔렸는지" 정보가 없음.

## 2. 목표 (What)

**판매 수집 시점에 sale_qty만큼 즉시 FIFO 차감** → 배치 remaining이 실물과 항상 일치.

### DoD
- [ ] save_daily_sales() 내에서 sale_qty 발생 시 가장 오래된 active 배치부터 차감
- [ ] BatchSync는 **보정 역할만** (오차 누적 방지용 2차 안전망)
- [ ] 폐기 시간대(02/10/14/22시) 직전 수집 시 remaining이 실재고와 ±1 이내 일치
- [ ] 기존 가드(batch-sync-zero-sales-guard) 제거 또는 최소화

### 비목표
- hourly_sales_detail 시간대별 배치 귀속 (향후 정밀화, 이번 스코프 아님)
- 실시간 POS 연동 (BGF 시스템 한계)

## 3. 범위 (Scope)

| 파일 | 변경 |
|------|------|
| `src/infrastructure/database/repos/sales_repo.py` | save_daily_sales() 내 판매 차감 FIFO 로직 추가 |
| `src/infrastructure/database/repos/inventory_batch_repo.py` | _consume_fifo_by_sale() 신규 메서드 |
| `src/infrastructure/database/repos/inventory_batch_repo.py` | sync_remaining_with_stock() 보정 전용으로 축소 |

## 4. 접근

### 핵심 변경: 판매 차감 시점 이동

```
[현재]
  save_daily_sales(sale_qty=3) → DB 저장만
  ...시간 경과...
  BatchSync → stock_qty 역산 → FIFO 차감 (부정확)

[개선]
  save_daily_sales(sale_qty=3)
    → DB 저장
    → 즉시 _consume_fifo_by_sale(item_cd, sale_qty=3)
      → 가장 오래된 active 배치부터 3개 차감
      → remaining 갱신
  ...
  BatchSync → stock_qty vs batch_total 비교 → 오차분만 보정 (2차 안전망)
```

### 차감 규칙

```python
def _consume_fifo_by_sale(item_cd, sale_qty, store_id):
    """판매 발생 시 즉시 FIFO 차감
    
    1. active 배치를 expiry_date ASC 정렬 (가장 먼저 만료되는 것부터)
    2. sale_qty만큼 순차 차감
    3. remaining=0이 되면 consumed 처리
    """
    batches = SELECT * FROM inventory_batches
              WHERE item_cd=? AND store_id=? AND status='active' AND remaining_qty>0
              ORDER BY expiry_date ASC, id ASC
    
    remain = sale_qty
    for batch in batches:
        if remain <= 0:
            break
        deduct = min(batch.remaining_qty, remain)
        batch.remaining_qty -= deduct
        remain -= deduct
        if batch.remaining_qty == 0:
            batch.status = 'consumed'
```

### BatchSync 역할 변경

```
[현재] 주도적 차감 (stock 역산 → FIFO)
[개선] 보정만 (판매 차감 누적 오차 ±N개 보정)
  - batch_total vs stock_qty 차이가 2개 이내 → 스킵 (허용 오차)
  - 3개 이상 → 보정 + WARNING 로그
```

## 5. 리스크

| 리스크 | 대응 |
|--------|------|
| save_daily_sales에서 sale_qty가 전일 대비 **증분**인지 **누적**인지 | 현재 코드 확인 필요 — 증분이면 그대로, 누적이면 차이 계산 |
| 같은 날 2번 수집 시 중복 차감 | 이미 차감한 수량 추적 (last_consumed_sale_qty 컬럼 또는 플래그) |
| 배치 없는 상품에서 판매 발생 | 차감 스킵 + WARNING (배치 자동생성은 기존 로직 유지) |

## 6. 검증

- [ ] 46513 참치소고기고추장더블2: 입고 2개 → 판매 1개 → remaining=1 확인
- [ ] 14:00 ExpiryChecker: remaining=1 → 폐기 대상 1개로 정확히 감지
- [ ] BatchSync: 보정 0건 (판매 차감으로 이미 정합)
