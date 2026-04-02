# Plan: 폐기 알림 입고 기반 전환 (expiry-alert-receiving-based)

> 작성일: 2026-04-02
> 상태: Plan

---

## 1. 문제 정의

### 현상
46513점포에서 04-02 14시 폐기 3건(소고기고추장삼각2, 한돈불백정식2, 청양참치마요삼각2)에 대해 **예고 알림도, 컨펌 알림도 발송되지 않음**.

### 근본 원인
현재 폐기 알림의 대상 조회 함수 `get_items_expiring_at()`가 **order_tracking + inventory_batches** 기반:

| 데이터 소스 | 사용자 수동 발주 | 문제점 |
|------------|:---:|------|
| order_tracking | delivery_type='', expiry_time='' | AI 발주만 자동 설정, 수동 발주는 빈값 |
| inventory_batches | status='consumed', remaining_qty=0 | 사전수집 시 stock=0으로 보고 조기 소진 처리 |

**사용자 수동 발주(order_source='site') 상품은 폐기 알림 시스템의 완전한 사각지대.**

### 해결 방향
`receiving_history`를 1순위 데이터 소스로 전환:
- 발주 주체 무관 (AI든 수동이든 입고되면 기록)
- `delivery_type`이 정확 ('1차', '2차', 'ambient' — BGF 시스템 직접 수집)
- `mid_cd` + `delivery_type` + `receiving_date`로 폐기 시간 100% 계산 가능

---

## 2. 폐기 시간 계산 규칙

### 입고 → 폐기 시간 매핑

```
receiving_history.delivery_type + mid_cd → expiry_hour

1차 배송 (전날 20시 도착):
  001/002/003 (도시락/주먹밥/김밥): 다음날 02:00 폐기
  004/005 (샌드위치/햄버거):       다음날 22:00 폐기

2차 배송 (당일 07시 도착):
  001/002/003: 당일 14:00 폐기
  004/005:     다음날 10:00 폐기

ambient (상온):
  012 (빵): product_details.expiration_days 기반 (자정 만료)
```

### 구체적 계산

| delivery_type | mid_cd | 입고일(D) | 폐기일시 | expiry_hour |
|:---:|:---:|:---:|:---:|:---:|
| 1차 | 001,002,003 | D | D+1 02:00 | 2 |
| 1차 | 004,005 | D | D+1 22:00 | 22 |
| 2차 | 001,002,003 | D | D 14:00 | 14 |
| 2차 | 004,005 | D | D+1 10:00 | 10 |
| ambient | 012 | D | D+expiry_days 00:00 | 0 |

---

## 3. 변경 대상

| 파일 | 변경 유형 | 설명 |
|------|:---:|------|
| `src/alert/expiry_checker.py` | 수정 | `get_items_expiring_at()` → receiving_history 기반 전환 |

### 변경하지 않는 것
- `run_scheduler.py` 3단계 스케줄: 그대로 유지 (13:50/14:00/14:10 등)
- `EXPIRY_CONFIRM_SCHEDULE`: 그대로 유지
- `_send_confirm_alert()`: 그대로 유지 (데이터 소스만 바뀜)
- `inventory_batches` 관련 로직: 보조로 유지 (배치 만료 처리는 독립 기능)

---

## 4. 상세 설계 (개요)

### `get_items_expiring_at(expiry_hour)` 변경

**현재**: order_tracking 1순위 → inventory_batches 보충 → daily_sales 교차검증

**변경**: receiving_history 1순위 → daily_sales.stock_qty > 0 필터 (이미 판매된 것 제외)

```
receiving_history에서 조회:
  1. 해당 expiry_hour에 폐기되는 (delivery_type, mid_cd) 조합 계산
  2. 폐기일 = today인 입고 건 조회
  3. daily_sales.stock_qty > 0 필터 (재고 있는 것만)
  4. 결과 반환
```

### 예시: `get_items_expiring_at(14)` 호출 시

```sql
-- expiry_hour=14 → 2차 배송 + mid_cd IN (001,002,003) + receiving_date = today
SELECT rh.item_cd, p.item_nm, p.mid_cd, rh.receiving_qty, rh.delivery_type
FROM receiving_history rh
JOIN products p ON rh.item_cd = p.item_cd
WHERE rh.delivery_type = '2차'
  AND p.mid_cd IN ('001','002','003')
  AND rh.receiving_date = '2026-04-02'  -- 당일 입고 → 당일 14:00 폐기
  AND rh.receiving_qty > 0
  AND rh.store_id = ?
```

+ daily_sales stock_qty > 0 교차 검증 (이미 전량 판매된 상품 제외)

---

## 5. expiry_hour → 입고 조건 역매핑

`get_items_expiring_at(expiry_hour)` 호출 시 **어떤 입고 건이 해당 시간에 폐기되는지** 역산:

| expiry_hour | delivery_type | mid_cd | receiving_date (도착일) |
|:---:|:---:|:---:|:---:|
| 2 | 1차 | 001,002,003 | **D-2** (이틀 전 입고) |
| 10 | 2차 | 004,005 | **D-3** (3일 전 입고) |
| 14 | 2차 | 001,002,003 | **D-1** (어제 입고) |
| 22 | 1차 | 004,005 | **D-3** (3일 전 입고) |
| 0 | ambient | 012 | expiry_days 역산 |

> ★ delivery_utils.get_expiry_time_for_delivery() 역산 결과로 검증 완료

핵심: **receiving_date가 오늘인지 어제인지**는 배송차수 + 카테고리에 따라 결정됨.

---

## 6. 기존 동작과의 호환

| 기존 기능 | 영향 |
|-----------|------|
| 3단계 스케줄 (pre_collect → judge → confirm) | 없음 (스케줄 그대로) |
| 예고 알림 (send_expiry_alert) | 데이터 소스만 변경, 출력 형식 동일 |
| 컨펌 알림 (_send_confirm_alert) | 데이터 소스만 변경 |
| 폐기전표 대조 (confirm_task) | get_items_expiring_at 결과와 waste_slip_items 대조 — 동일 로직 |
| 배치 만료 처리 (check_and_expire_batches) | 독립 기능, 영향 없음 |
| 대시보드 (get_food_stock_status) | 별도 함수, 변경 안 함 |

---

## 7. 테스트 시나리오

| # | 시나리오 | 기대 결과 |
|---|----------|-----------|
| 1 | AI 발주 → 2차 입고 → 14:00 폐기 시간 | 13:30 예고 알림 발송 |
| 2 | **사용자 수동 발주 → 2차 입고 → 14:00 폐기 시간** (핵심) | 13:30 예고 알림 발송 |
| 3 | 이미 전량 판매된 상품 (stock_qty=0) | 알림 대상에서 제외 |
| 4 | 1차 입고 도시락 → 다음날 02:00 폐기 | 01:30 예고 알림 발송 |
| 5 | ambient 입고 빵(012) | 별도 expiry_days 계산 (기존 로직 유지) |
| 6 | 해당 시간에 입고 건 없음 | 알림 대상 0건, 정상 스킵 |

---

## 8. 기대 효과

- **사각지대 해소**: 발주 주체(AI/수동) 무관하게 모든 입고 상품 폐기 알림 대상
- **데이터 정확도 향상**: order_tracking의 빈값/오류 의존 제거
- **간결한 로직**: receiving_history 단일 소스 → 복잡한 3소스 교차검증 불필요
- **46513 문제 해결**: 수동 발주 3건도 정상 알림 대상 포함
