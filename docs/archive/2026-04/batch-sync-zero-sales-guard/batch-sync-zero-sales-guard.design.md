# Design: BatchSync Zero-Sales 가드 (batch-sync-zero-sales-guard)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/batch-sync-zero-sales-guard.plan.md

---

## 1. 변경 위치 (1곳)

`src/infrastructure/database/repos/inventory_batch_repo.py:1106-1149` `sync_remaining_with_stock` 의 FIFO 차감 루프

**옵션 C(ExpiryChecker 안전망)는 비범위로 연기**: 가드 A로도 핵심 케이스가 차단됨. C는 follow-up 작업에서.

---

## 2. 핵심 알고리즘

### 변경 전
```python
for item_cd, batch_total in batch_totals.items():
    stock_qty = stock_map.get(item_cd, 0)
    if batch_total <= stock_qty:
        continue
    to_consume = batch_total - stock_qty
    # FIFO ASC로 차감 (오래된 것부터)
    SELECT id, remaining_qty FROM inventory_batches
    WHERE ... ORDER BY receiving_date ASC, id ASC
```

### 변경 후
```python
for item_cd, batch_total in batch_totals.items():
    stock_qty = stock_map.get(item_cd, 0)
    if batch_total <= stock_qty:
        continue
    to_consume = batch_total - stock_qty

    # ★ 가드: 만료 24h 이내 active 배치 잔량 합 (보호 대상)
    cursor.execute("""
        SELECT COALESCE(SUM(remaining_qty), 0) AS protected_qty
        FROM inventory_batches
        WHERE item_cd = ? AND store_id = ? AND status = ?
          AND remaining_qty > 0
          AND expiry_date IS NOT NULL
          AND julianday(expiry_date) - julianday('now') < 1.0
    """, (item_cd, store_id, BATCH_STATUS_ACTIVE))
    protected_qty = int(cursor.fetchone()[0] or 0)

    if protected_qty >= to_consume:
        # 모두 만료 임박분 → ExpiryChecker에 위임, 보류
        protected_skipped += 1
        continue

    if protected_qty > 0:
        to_consume -= protected_qty  # 만료 임박 분만큼 보호

    adjusted += 1
    # FIFO 차감 (만료 임박 배치는 끝으로 밀기 위해 expiry_date DESC 우선)
    SELECT id, remaining_qty FROM inventory_batches
    WHERE item_cd=? AND store_id=? AND status=? AND remaining_qty>0
    ORDER BY
        CASE WHEN expiry_date IS NOT NULL
             AND julianday(expiry_date) - julianday('now') < 1.0
             THEN 1 ELSE 0 END,  -- 만료 임박은 마지막
        receiving_date ASC, id ASC
```

### 결정 요지
1. **24h 임계**: julianday 차이 < 1.0 (1일)
2. **전부 보호되면 skip**: 어차피 ExpiryChecker가 처리할 거니까 BatchSync는 손대지 않음
3. **부분 보호**: 만료 임박 분만 보호, 나머지는 정상 차감
4. **FIFO 정렬 보강**: 보호 대상이 마지막 순서로 밀려서 강제 차감되지 않게

---

## 3. 회귀 테스트 5개 (`tests/test_batch_sync_zero_sales_guard.py`)

| # | 케이스 | setup | expected |
|---|---|---|---|
| 1 | 정상 판매 | batch=1 expiry=+3d, stock=0 | consumed (정상) |
| 2 | **0판매 + 만료 임박** | batch=1 expiry=+12h, stock=0 | active 유지 (보호) |
| 3 | 부분 판매 + 여유 | batch=2 expiry=+3d, stock=1 | 1개 consumed |
| 4 | 부분 판매 + 일부 임박 | batch=2 (1개+12h, 1개+3d), stock=1 | 임박 분 보호, 여유 분 consumed |
| 5 | 만료 임박만 + 정상 배치 혼재 | batch=2 (1개+12h, 1개+3d), stock=0 | 여유 분 consumed, 임박 분 보호 |

---

## 4. 구현 순서

| # | 작업 |
|---|---|
| 1 | inventory_batch_repo.py 가드 추가 (~25줄) |
| 2 | test_batch_sync_zero_sales_guard.py 신규 (5 케이스) |
| 3 | pytest 통과 |
| 4 | 4매장 수동 시뮬레이션 (drop) |
| 5 | 이슈체인 [WATCHING] + 시도 1 |
| 6 | 커밋 + 푸시 (scheduler-auto-reload가 자동 적용) |

---

## 5. 성공 기준

- [ ] 가드 코드 25줄 이내
- [ ] 5/5 회귀 테스트 통과
- [ ] 정상 판매 시나리오 회귀 없음
- [ ] 만료 임박 보호 시나리오 active 유지

## 6. 다음 단계

`/pdca do batch-sync-zero-sales-guard`
