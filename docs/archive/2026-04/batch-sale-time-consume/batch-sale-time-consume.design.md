# Design — batch-sale-time-consume

**Feature**: batch-sale-time-consume (판매 시점 배치 차감)
**Plan**: `docs/01-plan/features/batch-sale-time-consume.plan.md`
**Created**: 2026-04-11

---

## 1. 아키텍처 개요

```
save_daily_sales(sale_qty=3, prev_sale_qty=1)
  ↓
  sale_qty_diff = 3 - 1 = 2 (증분)
  ↓
  FR-01: order_tracking FIFO 차감 (기존, 유지)
  ↓
  FR-01.5: inventory_batches FIFO 차감 (★ 신규)
    → active 배치를 expiry_date ASC 정렬
    → sale_qty_diff(2)만큼 순차 차감
    → remaining=0 → consumed 처리
  ↓
  FR-02: BatchSync (기존, 보정 전용으로 축소)
    → batch_total vs stock_qty 비교
    → 차이 2개 이내 → 스킵
    → 3개 이상 → 보정 + WARNING
  ↓
  FR-03: 입고 시 배치 자동생성 (기존, 유지)
```

## 2. 핵심 변경: FR-01.5 추가

### 위치: `sales_repo.py` save_daily_sales() 내부

FR-01(order_tracking 차감) 직후, FR-02(BatchSync) 직전에 삽입.

```python
# FR-01.5: inventory_batches 판매 시점 FIFO 차감 (신규)
if sale_qty_diff > 0:
    cursor.execute(
        """
        SELECT id, remaining_qty FROM inventory_batches
        WHERE item_cd = ? AND store_id = ? AND status = 'active'
        AND remaining_qty > 0
        ORDER BY expiry_date ASC, id ASC
        """,
        (item_cd, store_id)
    )
    remain = sale_qty_diff
    for batch_row in cursor.fetchall():
        if remain <= 0:
            break
        b_id, b_remaining = batch_row[0], batch_row[1]
        deduct = min(b_remaining, remain)
        new_remaining = b_remaining - deduct
        new_status = 'consumed' if new_remaining == 0 else 'active'
        cursor.execute(
            """
            UPDATE inventory_batches
            SET remaining_qty = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_remaining, new_status, now, b_id)
        )
        remain -= deduct
    if sale_qty_diff > 0:
        logger.debug(
            f"[FR-01.5] {item_cd}: 판매 {sale_qty_diff}개 → "
            f"배치 FIFO 차감 완료 (미차감={remain})"
        )
```

## 3. FR-02 BatchSync 역할 축소

### 현재: 주도적 차감 (stock 역산 → FIFO)
### 변경: 보정 전용 (오차 ±2 허용)

```python
# sync_remaining_with_stock() 변경
to_consume = batch_total - stock_qty

# 허용 오차 (FR-01.5가 주도 차감하므로 작은 오차만 남음)
TOLERANCE = 2
if to_consume <= TOLERANCE:
    continue  # 허용 범위 → 보정 불필요

# 허용 초과 시 보정 + WARNING
logger.warning(
    f"[BatchSync] {item_cd}: 오차 {to_consume}개 보정 "
    f"(batch_total={batch_total}, stock={stock_qty})"
)
```

## 4. 가드 정리

FR-01.5가 판매 시점에 차감하므로, 기존 가드의 복잡한 로직 불필요.

| 가드 | 현재 | 변경 |
|------|------|------|
| batch-sync-zero-sales-guard | 만료 24h 이내 보호 + 판매 면제 | **제거** (FR-01.5가 판매 차감, BatchSync는 보정만) |
| 가드 면제 (04-10 추가) | sale_qty > 0이면 가드 면제 | **제거** (가드 자체 불필요) |

## 5. 검증 매트릭스

| 시나리오 | 입고 | 판매 | remaining | 폐기 감지 |
|---------|------|------|-----------|----------|
| 입고 3, 판매 2 | 3 | 2 | **1** | ✅ 1개 |
| 입고 3, 판매 3 | 3 | 3 | **0** (consumed) | 0개 (정상) |
| 입고 3, 판매 0 | 3 | 0 | **3** | 3개 |
| 배치 2개 (A:2, B:3), 판매 3 | 5 | 3 | A:0, **B:2** | B 2개 |

## 6. 영향받는 파일

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `src/infrastructure/database/repos/sales_repo.py` | 수정 | FR-01.5 판매 차감 로직 추가 |
| `src/infrastructure/database/repos/inventory_batch_repo.py` | 수정 | sync_remaining_with_stock() 허용 오차 도입, 가드 제거 |

## 7. 구현 순서

```
1. sales_repo.py — FR-01.5 판매 시점 FIFO 차감 추가
2. inventory_batch_repo.py — BatchSync 허용 오차 + 가드 제거
3. 테스트: 46513 참치소고기고추장더블2 시뮬레이션
4. 커밋 + 다음 07:00 발주에서 라이브 검증
```
