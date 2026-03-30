# Plan: remaining-qty-realtime-fix

> **Feature**: realtime_inventory 기준 remaining_qty 보정
> **Created**: 2026-03-30
> **Level**: Dynamic
> **Priority**: High (발주 정확도 직접 영향)

---

## 1. 문제 정의

### 배경
`update_receiving` 버그(599ac29에서 수정 완료)로 수개월간 remaining_qty가 과다 누적됨.
이전 보정(총발주-총판매)은 수집 누락/수동폐기/반품 등을 반영하지 못해 부정확.

### 현황 (46513점, 보정 전 기준)

| 구분 | 건수 | 비율 |
|------|:---:|:---:|
| 정상 (stock ≤ ot_remain ≤ stock+pending) | 4,052 | 76% |
| 과다 (ot_remain > stock + pending) | 201 | 4% |
| 부족 (ot_remain < stock) | 1,074 | 20% |

- **과다 201건**: remaining_qty가 실재고보다 많음 → pending 과대 → 발주 차단
- **부족 1,074건**: OT에 추적되지 않는 재고 (수동발주 등) → 정상, 보정 불필요

### 영향
- pending_qty 과대 산출 → 발주량 과소 → 품절
- ot_fill pending_source 차단율 90.5% (정상 대비 과도)

---

## 2. 보정 공식

### 기준값
```
실재고 = max(0, realtime_inventory.stock_qty)   ← BGF 시스템이 진실
미도착 = SUM(remaining_qty) WHERE status='ordered'  ← 아직 안 온 것
매장재고 = 실재고 + 미도착
```

### 보정 대상
```
도착분 잔량 = SUM(remaining_qty) WHERE status='arrived'
과다분 = 도착분 잔량 - 실재고

과다분 > 0 이면:
    status='arrived'인 가장 오래된 건부터 FIFO 차감
```

### 핵심 원칙
1. **status='ordered' (미도착)은 건드리지 않음** — 아직 배송 중
2. **status='arrived' (도착)만 차감** — 도착했는데 remaining이 과다 = 판매 차감 누락
3. **ri.stock_qty가 진실** — BGF 시스템에서 매일 수집하는 실재고

---

## 3. 변경 파일

| 파일 | 변경 | 유형 |
|------|------|------|
| (없음) | 1회성 보정 스크립트 실행 | 데이터 보정 |

코드 변경 없음. 근본 원인(update_receiving 버그)은 이미 수정 완료.

---

## 4. 검증 계획

| 단계 | 방법 |
|------|------|
| dry-run | 보정 대상 건수/수량 미리 출력 |
| 보정 후 검증 | ri.stock_qty vs OT remaining 재비교 — 과다 0건 확인 |
| 발주 영향 확인 | 3/31 발주 시 pending 차단 건수 감소 확인 |

---

## 5. 리스크

| 리스크 | 대응 |
|--------|------|
| ri.stock_qty 수집 시점 차이 (07시 수집, 이후 판매 발생) | 보정은 과다분만 차감하므로 안전 방향 |
| ri.stock_qty < 0 (11건, 종량봉투 등) | max(0, stock_qty) 처리 |
| status='arrived'가 없는 상품 (전부 ordered) | 차감 대상 없음 → 스킵 |

---

## 6. 이전 보정(총발주-총판매) 처리

이전 보정은 이미 실행됨. 이번 ri 기준 보정은 그 위에 **추가 보정**으로 실행.
이전 보정이 과도하게 차감한 경우는 ri 기준으로 잡히지 않음 (부족 케이스 = 보정 불가, 자연 회복 대기).
