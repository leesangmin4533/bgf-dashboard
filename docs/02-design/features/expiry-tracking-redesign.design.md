# 폐기 추적 재설계 — 입고 확인 기반 (confirmed_orders + delivery_match)

> 작성일: 2026-04-05
> 커밋: 9aca17d
> DB 스키마: v72

## 1. 배경: 왜 재설계가 필요했나

### order_tracking.remaining_qty 기반의 근본 문제

| 단계 | 시도 | 문제 |
|------|------|------|
| 1단계 | OT remaining_qty 기반 | remaining_qty 미갱신 (FR-01이 sale_qty_diff=0이면 차감 안 함) |
| 2단계 | inventory_batches 보충 | expiry_date 99.9% date-only → length>10 필터로 전부 스킵 |
| 3단계 | OT 교차검증 (52d24ae) | remaining_qty > 0인데 이미 팔린 상품 → batches+stock으로 걸러냄 (패치) |
| 4단계 | receiving_history 1순위 (442b1d8) | 수동 발주 사각지대 해소, 하지만 배치 소진 구분 불가 |
| 5단계 | stock<=0 방어, FIFO 교차검증 (1e0a1b5) | recv_qty 폴백이 수개월 전 데이터도 통과 → 폴백 자체 제거 |

### remaining_qty가 안 줄어드는 3가지 이유

1. **보정 장치 없음**: FR-01은 sale_qty_diff > 0일 때만 차감. 한 번 놓치면 영원히 미갱신
2. **사용자 변경 미반영**: AI가 5개 발주 → 사용자가 3개로 수정 → OT는 여전히 5개
3. **site 동기화 레코드 expiry_time 빈값**: order_status_collector가 delivery_type='', expiry_time='' 저장

### 시나리오로 본 문제

```
AI: 삼각김밥 2개 발주 → OT: order_qty=2, remaining_qty=2
사용자: BGF에서 1개로 수정 → OT: 변경 안 됨 (여전히 2)
10:30 sync: site 레코드 별도 생성 (order_qty=1, expiry_time='')
→ OT에 3개분 추적 중 (auto 2 + site 1), 실제 1개
→ 새 상품 수동 추가 시 expiry_time 없어서 폐기 시점 추적 불가
```

## 2. 새 설계: 2단계 검증 구조

기존 스케줄을 그대로 활용하여 추가 BGF 접속 없이 구현.

```
10:00  발주마감 (사용자 수정 확정)

10:30  pending_sync (BGF 접속 — 기존 스케줄)
  ├─ [기존 유지] pending 마킹 (004/005만, 발주 과소 방지)
  └─ [추가] confirmed_orders 스냅샷 저장
       → 001~005 전체 (001~003 skip 전에 저장)
       → BGF 확정 수량 (ord_qty × unit_qty, 사용자 수정 반영)
       → delivery_type, ord_input_id(발주방법) 포함

20:00  1차 배송 도착

20:30  receiving_collect (BGF 접속 — 기존 스케줄)
  ├─ [기존 유지] 센터매입 수집 → receiving_history
  └─ [추가] 1차 매칭
       → confirmed_orders(오늘, 1차) vs receiving_history(오늘)
       → 매칭 OK → inventory_batches(active) 확정 + FOOD_EXPIRY_CONFIG 시간 계산
       → 미입고 → 배치 미생성 + 로그
       → 스냅샷 없음 → 기존 배치 생성 로직 폴백

07:00  2차 배송 도착 (익일)

07:00  daily job Phase 1.1 (BGF 접속 — 기존 스케줄)
  ├─ [기존 유지] 어제+오늘 입고수집
  └─ [추가] 2차 매칭
       → confirmed_orders(어제, 2차) vs receiving_history(오늘)
       → 동일 로직
```

### 핵심 원칙

- **입고 확인 = 배치 생성 기준**: 입고 안 됐으면 배치 없음 → 오알림 불가
- **BGF 확정 수량이 ground truth**: 10:30은 발주마감 후 → 사용자 변경 완전 반영
- **graceful degradation**: 10:30 실패해도 기존 배치 생성 유지

## 3. confirmed_orders 테이블

```sql
CREATE TABLE confirmed_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    order_date TEXT NOT NULL,       -- 발주일
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    ord_qty INTEGER NOT NULL,       -- BGF 확정 수량
    delivery_type TEXT,             -- '1차'/'2차'
    ord_input_id TEXT,              -- 발주방법 (자동발주/단품별/스마트발주 등)
    matched INTEGER DEFAULT 0,     -- 입고 매칭 완료 여부
    matched_qty INTEGER DEFAULT 0, -- 실제 입고 수량
    confirmed_at TEXT NOT NULL,
    UNIQUE(store_id, order_date, item_cd)
);
```

## 4. FOOD_EXPIRY_CONFIG (폐기시간 계산)

| mid_cd | 1차 (20:00 도착) | 2차 (07:00 도착) |
|--------|:---:|:---:|
| 001 도시락 | D+2 02:00 | D+1 14:00 |
| 002 주먹밥 | D+2 02:00 | D+1 14:00 |
| 003 김밥 | D+2 02:00 | D+1 14:00 |
| 004 샌드위치 | D+3 22:00 | D+2 10:00 |
| 005 햄버거 | D+3 22:00 | D+2 10:00 |

## 5. expiry_checker 단순화

```
Before (4단계 폴백):
  0순위: inventory_batches → 1순위: order_tracking → 2순위: receiving_history → 3순위: 계산

After (2단계):
  1순위: inventory_batches(status='active') — 입고 확정 배치
  2순위: delivery_utils 계산 — 배치 없을 때 폴백

get_items_expiring_at():
  Before: _get_receiving_items_expiring_at() 1순위 + _get_batch_items_expiring_at() 보충
  After:  _get_batch_items_expiring_at() only
```

## 6. 폴백 안전장치

| 실패 상황 | 동작 |
|----------|------|
| 10:30 실패 (스냅샷 없음) | 20:30/07:00 매칭 스킵, 기존 FR-03/receiving_collector 배치 생성 유지 |
| 20:30 실패 (입고수집 실패) | 07:00 daily job에서 어제 입고분 재수집 |
| 매칭 로직 예외 | try/except로 감싸고, 기존 배치 생성 계속 |

## 7. 수정 파일

| 파일 | 변경 |
|------|------|
| `src/infrastructure/database/schema.py` | confirmed_orders 테이블 + 인덱스 |
| `src/settings/constants.py` | DB_SCHEMA_VERSION 71→72 |
| `src/db/models.py` | v72 마이그레이션 |
| `src/collectors/order_status_collector.py` | post_order_pending_sync에 스냅샷 저장 |
| `src/application/use_cases/delivery_match_flow.py` | **신규** 매칭 로직 |
| `src/scheduler/phases/collection.py` | Phase 1.1 후 2차 매칭 호출 |
| `run_scheduler.py` | receiving_collect_wrapper에 1차 매칭 호출 |
| `src/alert/expiry_checker.py` | OT/receiving_history 폴백 제거 |

## 8. 검증 계획

- **D+2**: 001~003 첫 정확한 폐기 알림 (1차 02:00, 2차 14:00)
- **D+3**: 004~005 첫 정확한 폐기 알림 (2차 10:00, 1차 22:00)
- **4/7 Gap 분석 예약**: 스케줄 태스크 `expiry-tracking-gap-analysis`
