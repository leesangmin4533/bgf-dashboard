# Design: 수동 발주 추적 기능 (manual-order-tracking)

> **생성일**: 2026-02-05
> **Plan 참조**: [manual-order-tracking.plan.md](manual-order-tracking.plan.md)
> **상태**: Completed

---

## 1. 아키텍처 개요

### 1.1 시스템 컨텍스트

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BGF 자동 발주 시스템                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐      ┌─────────────────────┐                      │
│  │ BGF 사이트    │      │  receiving_history  │                      │
│  │ (입고 데이터) │─────→│  (입고 수집 완료)    │                      │
│  └──────────────┘      └──────────┬──────────┘                      │
│                                   │                                 │
│                                   ▼                                 │
│                    ┌──────────────────────────┐                     │
│                    │  ManualOrderDetector     │  ← 신규 모듈        │
│                    │  ─────────────────────── │                     │
│                    │  • 입고 vs 자동발주 대조  │                     │
│                    │  • 수동발주 식별          │                     │
│                    │  • order_tracking 저장   │                     │
│                    └──────────────┬───────────┘                     │
│                                   │                                 │
│            ┌──────────────────────┼──────────────────────┐          │
│            ▼                      ▼                      ▼          │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│  │ order_tracking  │   │inventory_batches│   │ ExpiryChecker   │   │
│  │ (source=manual) │   │ (비푸드 배치)    │   │ (폐기 알림)     │   │
│  └─────────────────┘   └─────────────────┘   └─────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 데이터 흐름

```
[08:00 / 21:00 스케줄 트리거]
           │
           ▼
┌─────────────────────────────────────┐
│ 1. receiving_history 조회           │
│    WHERE receiving_date = today     │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 2. order_history 조회 (자동발주)    │
│    WHERE order_date = today - 1     │
│    + order_date = today (1차 입고)  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 3. 매칭 로직                        │
│    received - auto_ordered          │
│    = manual_orders                  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 4. 도착/폐기 시간 계산              │
│    - 푸드류: 차수별 폐기시간        │
│    - 비푸드: 입고일 + 유통기한      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 5. order_tracking 저장              │
│    source = 'manual'                │
└──────────────┬──────────────────────┘
               │
               ▼ (비푸드만)
┌─────────────────────────────────────┐
│ 6. inventory_batches 저장           │
│    FIFO 배치 등록                   │
└─────────────────────────────────────┘
```

---

## 2. DB 스키마 설계

### 2.1 Migration v19

**파일**: `src/db/models.py`

```python
19: """
-- order_tracking 테이블에 발주 소스 컬럼 추가
ALTER TABLE order_tracking ADD COLUMN order_source TEXT DEFAULT 'auto';
-- 값: 'auto' (자동발주), 'manual' (수동발주)

CREATE INDEX IF NOT EXISTS idx_order_tracking_source
    ON order_tracking(order_source);
""",
```

### 2.2 테이블 변경 상세

#### order_tracking (수정)

| 컬럼 | 타입 | 설명 | 변경 |
|------|------|------|------|
| id | INTEGER PK | 레코드 ID | 기존 |
| order_date | TEXT | 발주일 | 기존 |
| item_cd | TEXT | 상품코드 | 기존 |
| item_nm | TEXT | 상품명 | 기존 |
| mid_cd | TEXT | 중분류 | 기존 |
| delivery_type | TEXT | 배송차수 | 기존 |
| order_qty | INTEGER | 발주수량 | 기존 |
| remaining_qty | INTEGER | 잔여수량 | 기존 |
| arrival_time | TEXT | 도착시간 | 기존 |
| expiry_time | TEXT | 폐기시간 | 기존 |
| status | TEXT | 상태 | 기존 |
| alert_sent | INTEGER | 알림발송 | 기존 |
| **order_source** | TEXT | **발주소스** | **신규** |
| created_at | TEXT | 생성일 | 기존 |
| updated_at | TEXT | 수정일 | 기존 |

**order_source 값**:
- `'auto'` - 자동발주 (기본값, 기존 데이터 호환)
- `'manual'` - 수동발주

---

## 3. 구현 완료 항목

### 3.1 ManualOrderDetector

**파일**: `src/collectors/manual_order_detector.py`

- 입고 데이터 조회
- 자동발주 대조 (1차/2차 모두)
- 도착/폐기 시간 계산 (배송 스케줄 반영)
- order_tracking 저장
- inventory_batches 저장 (비푸드)

### 3.2 OrderTrackingRepository 확장

- `save_order_manual()` - 수동발주 저장 (중복 체크 포함)
- `get_by_source()` - 소스별 조회
- `get_existing_tracking_items()` - 중복 방지용 조회

### 3.3 스케줄링

| 시간 | 목적 |
|------|------|
| 08:00 | 2차 입고(07:00) 완료 후 감지 |
| 21:00 | 1차 입고(20:00) 완료 후 감지 |

---

**아카이브 완료**: 2026-02-05
