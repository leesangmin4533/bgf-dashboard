# Design: 폐기 알림 입고 기반 전환 (expiry-alert-receiving-based)

> 작성일: 2026-04-02
> Plan 참조: `docs/01-plan/features/expiry-alert-receiving-based.plan.md`
> 상태: Design

---

## 1. 변경 범위

| 파일 | 변경 유형 | 설명 |
|------|:---:|------|
| `src/alert/expiry_checker.py` | 수정 | `get_items_expiring_at()` 내부 로직 교체 |

### 변경하지 않는 파일
- `run_scheduler.py`: 3단계 스케줄 그대로 (pre_collect → judge → confirm)
- `src/alert/config.py`: EXPIRY_CONFIRM_SCHEDULE, DELIVERY_CONFIG 그대로
- `src/alert/delivery_utils.py`: 유틸 함수 그대로 (폐기시간 계산에 재활용)
- `_send_confirm_alert()`: 알림 형식 그대로

---

## 2. 현재 구조 (AS-IS)

### `get_items_expiring_at(expiry_hour)` 호출 지점 (2곳)

```
1) expiry_pre_collect_wrapper → ExpiryChecker.send_expiry_alert(expiry_hour)
   → generate_expiry_alert_message(expiry_hour)
      → get_items_expiring_at(expiry_hour)         ← 예고 알림
      → get_items_expiring_at_legacy(expiry_hour)   ← 보충

2) expiry_confirm_wrapper → checker.get_items_expiring_at(expiry_hour)
   → pre_items로 waste_slip_items와 대조             ← 컨펌 알림
```

### 현재 데이터 소스 3단계

```
0순위: inventory_batches (status='active', remaining_qty > 0, expiry_date 매칭)
1순위: order_tracking (expiry_time LIKE, status='arrived', remaining_qty > 0)
교차:  daily_sales.stock_qty > 0 (이미 판매된 것 제외)
보충:  legacy (daily_sales 기반 + delivery_type 추정)
```

### 문제점
- order_tracking: 수동 발주(site)는 `expiry_time=''`, `delivery_type=''`
- inventory_batches: 사전수집 시 stock 동기화로 조기 `consumed` 전환
- legacy: `sales_date`를 `order_date`로 사용 (부정확)

---

## 3. 변경 설계 (TO-BE)

### `get_items_expiring_at(expiry_hour)` — receiving_history 기반

**핵심 변경**: 0순위를 `receiving_history`로 교체, 기존 로직은 보충용으로 유지

```python
def get_items_expiring_at(self, expiry_hour: int) -> List[Dict[str, Any]]:
    today = datetime.now().strftime("%Y-%m-%d")

    # ★ 1순위: receiving_history 기반 (발주 주체 무관)
    recv_items = self._get_receiving_items_expiring_at(expiry_hour, today)

    # 2순위: inventory_batches 보충 (receiving에 없는 것만)
    recv_codes = {item['item_cd'] for item in recv_items}
    batch_items = self._get_batch_items_expiring_at(expiry_hour, today)
    for bi in batch_items:
        if bi['item_cd'] not in recv_codes:
            recv_items.append(bi)

    return recv_items
```

### `_get_receiving_items_expiring_at()` — 신규 메서드

```python
def _get_receiving_items_expiring_at(
    self, expiry_hour: int, target_date: str
) -> List[Dict[str, Any]]:
    """receiving_history에서 특정 폐기 시간 대상 조회

    expiry_hour → (delivery_type, mid_cd 목록, receiving_date) 역매핑 후 조회.
    daily_sales.stock_qty > 0 교차검증으로 이미 판매된 상품 제외.
    """
```

---

## 4. expiry_hour 역매핑 테이블

`_get_receiving_items_expiring_at`에서 사용할 **expiry_hour → 입고 조건** 매핑:

```python
# expiry_hour → [(delivery_type, mid_cd_list, receiving_date_offset)]
# receiving_date_offset: 폐기일(today) 기준 입고(도착)일이 며칠 전인지
#
# ★ delivery_utils.py 실제 계산 결과로 검증 완료 (2026-04-02):
#   1차 001 도시락: order D-2 → arrival D-2 20:00 → expiry D 02:00
#   2차 001 도시락: order D-2 → arrival D-1 07:00 → expiry D 14:00
#   1차 004 샌드위치: order D-3 → arrival D-3 20:00 → expiry D 22:00
#   2차 004 샌드위치: order D-4 → arrival D-3 07:00 → expiry D 10:00
#
EXPIRY_HOUR_TO_RECEIVING = {
    2:  [("1차", ["001", "002", "003"], -2)],  # D-2 1차 입고 → 오늘 02:00 폐기
    10: [("2차", ["004", "005"], -3)],          # D-3 2차 입고 → 오늘 10:00 폐기
    14: [("2차", ["001", "002", "003"], -1)],   # D-1 2차 입고 → 오늘 14:00 폐기
    22: [("1차", ["004", "005"], -3)],           # D-3 1차 입고 → 오늘 22:00 폐기
    # 0: 빵(012) → 별도 처리 (expiry_days 기반, 기존 get_bread_items_expiring_today)
}
```

**이 테이블은 `delivery_utils.get_expiry_time_for_delivery()` 역산 결과.**
`ALERT_CATEGORIES.shelf_life_hours` + `DELIVERY_CONFIG`에서 파생된 값.

### 매핑 검증 (실제 계산)

| expiry_hour | delivery | mid | arrival | shelf_hours | expiry | recv offset |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 2 | 1차 | 001,002,003 | D-2 20:00 | +30h | D 02:00 | **-2** |
| 14 | 2차 | 001,002,003 | D-1 07:00 | +31h | D 14:00 | **-1** |
| 22 | 1차 | 004,005 | D-3 20:00 | +74h | D 22:00 | **-3** |
| 10 | 2차 | 004,005 | D-3 07:00 | +75h | D 10:00 | **-3** |

> **주의**: offset은 **receiving_date(=도착일)** 기준이지 order_date(발주일) 기준이 아님.
> 1차: arrival = order_date 당일 20:00 → receiving_date = order_date
> 2차: arrival = order_date+1일 07:00 → receiving_date = order_date + 1

---

## 5. SQL 쿼리 설계

### `_get_receiving_items_expiring_at(expiry_hour, target_date)`

```sql
-- expiry_hour=14 예시: 2차 + 001,002,003 + receiving_date=D-1(어제)
SELECT
    rh.item_cd,
    p.item_nm,
    p.mid_cd,
    SUM(rh.receiving_qty) as receiving_qty,
    rh.delivery_type,
    rh.receiving_date
FROM receiving_history rh
JOIN {common_prefix}products p ON rh.item_cd = p.item_cd
WHERE rh.delivery_type = ?          -- '2차'
  AND p.mid_cd IN (?, ?, ?)         -- '001','002','003'
  AND rh.receiving_date = ?          -- date('now', '-1 day') (D-1)
  AND rh.receiving_qty > 0
  AND rh.store_id = ?
GROUP BY rh.item_cd                  -- 같은 상품 중복 제거 (입고 수량 합산)
```

### daily_sales stock_qty 교차검증

```sql
-- 이미 전량 판매된 상품 제외
SELECT stock_qty FROM daily_sales
WHERE item_cd = ? AND store_id = ?
ORDER BY sales_date DESC LIMIT 1
```

`stock_qty = 0` → 제외 (이미 판매됨)
`stock_qty > 0` → 알림 대상 (재고 남아있음)
`조회 결과 없음` → 포함 (daily_sales 미수집 상태이므로 안전하게 포함)

---

## 6. 반환값 형식

기존 `get_items_expiring_at`와 **동일한 dict 구조** 유지 (호출자 변경 불필요):

```python
{
    'item_cd': str,
    'item_nm': str,
    'mid_cd': str,
    'category_name': str,        # ALERT_CATEGORIES 기반
    'remaining_qty': int,        # receiving_qty (입고 수량)
    'delivery_type': str,        # '1차' or '2차'
    'expiry_time': datetime,     # 계산된 폐기 시간
    'order_date': str,           # receiving_date (호환용)
    'arrival_time': str,         # delivery_utils.get_arrival_time 계산
}
```

`remaining_qty` 주의: 기존에는 order_tracking의 remaining_qty였으나,
receiving 기반에서는 `receiving_qty`를 사용. **실제 남은 수량은 daily_sales.stock_qty**가 더 정확하므로, stock_qty > 0인 경우 `stock_qty`를 `remaining_qty`로 사용.

---

## 7. `generate_expiry_alert_message` 변경

**변경 없음**. 이 함수는 `get_items_expiring_at()` 결과를 받아서 메시지를 만들 뿐.
단, legacy 보충 호출은 제거:

```python
def generate_expiry_alert_message(self, expiry_hour: int) -> Optional[str]:
    items = self.get_items_expiring_at(expiry_hour)
    # ★ 제거: legacy 보충 (receiving 기반이 모든 케이스 커버)
    # legacy_items = self.get_items_expiring_at_legacy(expiry_hour)
    ...
```

---

## 8. 기존 코드 영향 분석

| 함수 | 변경 | 이유 |
|------|:---:|------|
| `get_items_expiring_at()` | **교체** | receiving 1순위 + batch 보충 |
| `_get_batch_items_expiring_at()` | 유지 | 2순위 보충용으로 존속 |
| `get_items_expiring_at_legacy()` | 유지 (호출 제거) | 코드 삭제 안 함, 호출만 제거 |
| `generate_expiry_alert_message()` | 최소 수정 | legacy 보충 호출 제거 |
| `send_expiry_alert()` | 없음 | `generate_expiry_alert_message` 결과 사용 |
| `get_food_stock_status()` | 없음 | 대시보드용 별도 함수 |
| `get_bread_items_expiring_today()` | 없음 | expiry_hour=0 별도 처리 유지 |

### run_scheduler.py 영향

| 함수 | 변경 | 이유 |
|------|:---:|------|
| `expiry_pre_collect_wrapper` | 없음 | `checker.get_items_expiring_at()` 호출 유지 |
| `expiry_judge_wrapper` | 없음 | batch 판정은 독립 기능 |
| `expiry_confirm_wrapper` | 없음 | `checker.get_items_expiring_at()` 결과로 대조 |
| `_send_confirm_alert` | 없음 | 결과 형식 동일 |

---

## 9. 구현 체크리스트

- [ ] `EXPIRY_HOUR_TO_RECEIVING` 매핑 딕셔너리 추가 (expiry_checker.py 상단)
- [ ] `_get_receiving_items_expiring_at()` 신규 메서드 구현
- [ ] `get_items_expiring_at()` → receiving 1순위 + batch 2순위로 교체
- [ ] `generate_expiry_alert_message()` → legacy 보충 호출 제거
- [ ] daily_sales.stock_qty 교차검증 (stock_qty=0 제외, 미조회=포함)
- [ ] 반환값 dict 기존 형식과 호환 확인
- [ ] 테스트: Plan 시나리오 6개 커버
