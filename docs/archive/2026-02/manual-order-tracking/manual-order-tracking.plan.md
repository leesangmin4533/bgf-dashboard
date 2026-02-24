# Plan: 수동 발주 추적 기능 (manual-order-tracking)

> **생성일**: 2026-02-05
> **상태**: Draft
> **우선순위**: High

---

## 1. 개요

### 1.1 문제 정의

현재 폐기 역추적 시스템은 **자동발주만 추적**하고 있어, 사용자가 수동으로 발주한 상품은 폐기 관리에서 누락됨.

```
[현재 시스템 Gap]
                    ┌─────────────────┐
자동발주 ──────────→│ order_tracking  │──→ 폐기 알림 ✅
                    └─────────────────┘
                              ↑
수동발주 ─────── X ───────────┘         폐기 알림 ❌ (누락)
```

### 1.2 영향 범위

| 영향 항목 | 현재 | 예상 손실 |
|----------|------|----------|
| 수동발주 폐기 알림 | 미발송 | 폐기 손실 증가 |
| 폐기 보고서 정확도 | ~70% | 데이터 신뢰성 저하 |
| 재고 추적 정확도 | 불완전 | 발주 의사결정 오류 |

### 1.3 목표

- 수동 발주 상품도 `order_tracking`에 등록
- 자동/수동 발주 구분 가능하도록 스키마 확장
- 동일한 폐기 알림 및 보고서 생성 보장

---

## 2. 요구사항

### 2.1 기능 요구사항 (FR)

| ID | 요구사항 | 우선순위 |
|----|---------|---------|
| FR-01 | 수동 발주 감지: 입고 데이터에서 자동발주와 매칭되지 않는 항목 식별 | P0 |
| FR-02 | order_tracking에 수동 발주 저장 (source='manual') | P0 |
| FR-03 | 푸드류 수동 발주: 도착시간/폐기시간 자동 계산 | P0 |
| FR-04 | 비-푸드류 수동 발주: inventory_batches에도 배치 등록 | P1 |
| FR-05 | 기존 폐기 알림/보고서에 수동 발주 포함 | P0 |
| FR-06 | 대시보드에서 자동/수동 발주 구분 표시 | P2 |

### 2.2 비기능 요구사항 (NFR)

| ID | 요구사항 | 기준 |
|----|---------|------|
| NFR-01 | 감지 지연 | 입고 후 1시간 이내 |
| NFR-02 | 중복 등록 방지 | UNIQUE 제약 |
| NFR-03 | 기존 플로우 영향 없음 | 자동발주 정상 작동 |

---

## 3. 구현 방안

### 3.1 권장 방안: 입고 데이터 역추적 (방안 A)

```
[제안 플로우]

receiving_collector 실행
         ↓
receiving_history 저장
         ↓
┌────────────────────────────────────┐
│  ManualOrderDetector.detect()      │  ← 신규 모듈
│  ─────────────────────────────────│
│  1. 오늘 입고된 상품 조회           │
│  2. order_history 자동발주 대조     │
│  3. 매칭 안되면 → 수동발주 판정     │
│  4. order_tracking 저장            │
│     (source='manual')              │
└────────────────────────────────────┘
         ↓
ExpiryChecker → 폐기 알림 (자동+수동 통합)
```

### 3.2 대안 비교

| 방안 | 장점 | 단점 | 선택 |
|------|------|------|------|
| **A. 입고 역추적** | 기존 수집기 활용, 구현 간단 | 입고 후에만 감지 | ✅ 권장 |
| B. 발주현황 수집 | 실시간 감지 | 새 Collector 필요, BGF 화면 의존 | - |
| C. ord_qty 비교 | 데이터 활용 | 차이 발생 시에만 감지 | - |

### 3.3 수정 대상 파일

```
bgf_auto/
├── src/
│   ├── db/
│   │   ├── models.py              # 스키마 v19 추가
│   │   └── repository.py          # 수동발주 메서드 추가
│   │
│   ├── collectors/
│   │   └── manual_order_detector.py  # 신규 모듈
│   │
│   ├── scheduler/
│   │   └── daily_job.py           # 수동발주 감지 스케줄 추가
│   │
│   └── config/
│       └── constants.py           # DB_SCHEMA_VERSION 증가
│
└── docs/
    └── 01-plan/features/manual-order-tracking.plan.md  # 현재 문서
```

---

## 4. DB 스키마 변경

### 4.1 Migration v19

```sql
-- order_tracking 테이블에 발주 소스 컬럼 추가
ALTER TABLE order_tracking ADD COLUMN order_source TEXT DEFAULT 'auto';
-- 값: 'auto' (자동발주), 'manual' (수동발주)

CREATE INDEX IF NOT EXISTS idx_order_tracking_source
    ON order_tracking(order_source);
```

### 4.2 영향 분석

| 테이블 | 변경 내용 | 기존 데이터 |
|--------|---------|------------|
| order_tracking | order_source 컬럼 추가 | DEFAULT 'auto'로 자동 채움 |
| inventory_batches | 변경 없음 (source 컬럼 이미 없음) | - |

---

## 5. 핵심 알고리즘

### 5.1 수동 발주 감지 로직

```python
def detect_manual_orders(target_date: str) -> List[Dict]:
    """
    수동 발주 감지

    1. receiving_history에서 오늘 입고 조회
    2. order_history에서 자동발주 기록 조회
    3. 매칭되지 않는 항목 = 수동 발주
    """

    # 1. 오늘 입고된 상품
    received = receiving_repo.get_by_date(target_date)

    # 2. 자동발주 기록 (발주일 = 입고일 - 1)
    order_date = (parse(target_date) - timedelta(days=1)).strftime("%Y-%m-%d")
    auto_orders = order_history_repo.get_by_date(order_date)
    auto_items = {o['item_cd'] for o in auto_orders}

    # 3. 수동 발주 = 입고 - 자동발주
    manual_orders = []
    for recv in received:
        if recv['item_cd'] not in auto_items:
            manual_orders.append({
                'item_cd': recv['item_cd'],
                'item_nm': recv['item_nm'],
                'mid_cd': recv['mid_cd'],
                'order_qty': recv['receiving_qty'],
                'receiving_date': target_date,
                'delivery_type': recv.get('delivery_type', '1차'),
            })

    return manual_orders
```

### 5.2 도착/폐기 시간 계산

```python
def calculate_expiry_for_manual(item: Dict, mid_cd: str) -> Tuple[datetime, datetime]:
    """
    수동 발주 상품의 도착시간/폐기시간 계산
    - 푸드류: 차수별 도착시간 + 카테고리별 유통시간
    - 비푸드: 입고일 + product_details.expiration_days
    """
    receiving_date = parse(item['receiving_date'])
    delivery_type = item.get('delivery_type', '1차')

    # 푸드류 (001-006, 012)
    if mid_cd in FOOD_CATEGORIES:
        arrival_time = get_arrival_time(delivery_type, receiving_date)
        expiry_time = get_expiry_time_for_delivery(delivery_type, mid_cd, arrival_time)
    else:
        # 비-푸드류
        arrival_time = receiving_date.replace(hour=6, minute=0)
        exp_days = product_details_repo.get_expiration_days(item['item_cd'])
        expiry_time = arrival_time + timedelta(days=exp_days or 30)

    return arrival_time, expiry_time
```

---

## 6. 스케줄링

### 6.1 실행 시점

| 시간 | 작업 | 설명 |
|------|------|------|
| 08:00 | `detect_manual_orders()` | 1차 입고 완료 후 수동발주 감지 |
| 13:00 | `detect_manual_orders()` | 2차 입고 완료 후 수동발주 감지 |

### 6.2 daily_job.py 추가 내용

```python
# 수동 발주 감지 (08:00, 13:00)
schedule.every().day.at("08:00").do(run_manual_order_detection)
schedule.every().day.at("13:00").do(run_manual_order_detection)
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 | 입력 | 기대 결과 |
|--------|------|----------|
| 수동발주 감지 | 입고 3건, 자동발주 2건 | 수동발주 1건 감지 |
| 푸드 폐기시간 | 1차 입고 도시락 | 14:00 폐기 |
| 비푸드 폐기시간 | 유통기한 30일 상품 | 입고일+30일 |
| 중복 방지 | 동일 상품 재실행 | 중복 등록 안됨 |

### 7.2 통합 테스트

```bash
# 1. 테스트 데이터 준비 (수동 입고 시뮬레이션)
python scripts/test_manual_order.py --setup

# 2. 감지 실행
python scripts/test_manual_order.py --detect

# 3. 폐기 알림 확인
python scripts/run_expiry_alert.py --preview
```

---

## 8. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 입고 데이터 누락 | 수동발주 미감지 | receiving_collector 안정성 확보 |
| 자동/수동 오분류 | 중복 알림 | order_date + item_cd 정확 매칭 |
| 스키마 마이그레이션 실패 | DB 오류 | 백업 후 마이그레이션, 롤백 스크립트 |

---

## 9. 일정 (예상)

| 단계 | 작업 | 산출물 |
|------|------|--------|
| Plan | 요구사항 정의 | 현재 문서 |
| Design | 상세 설계 | design.md |
| Do | 구현 | 코드 |
| Check | 테스트/검증 | analysis.md |
| Act | 버그 수정 | - |
| Report | 완료 보고 | report.md |

---

## 10. 승인

- [x] 기획 검토 완료
- [x] 기술 검토 완료
- [x] 구현 착수 승인

---

**아카이브 완료**: 2026-02-05
