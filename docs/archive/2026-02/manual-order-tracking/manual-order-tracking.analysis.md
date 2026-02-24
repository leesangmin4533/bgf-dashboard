# Analysis: 수동 발주 추적 기능 (manual-order-tracking)

> **분석일**: 2026-02-05
> **Design 참조**: [manual-order-tracking.design.md](manual-order-tracking.design.md)
> **상태**: ✅ 완료

---

## 1. Gap Analysis 결과

### 1.1 Match Rate

| 항목 | 설계 | 구현 | 일치 |
|------|------|------|------|
| DB 스키마 v19 | order_source 컬럼 | ✅ 구현됨 | ✅ |
| constants.py | DB_SCHEMA_VERSION=19, ORDER_SOURCE_* | ✅ 구현됨 | ✅ |
| OrderTrackingRepository.save_order_manual() | 수동발주 저장 | ✅ 구현됨 | ✅ |
| OrderTrackingRepository.get_by_source() | 소스별 조회 | ✅ 구현됨 | ✅ |
| OrderTrackingRepository.get_existing_tracking_items() | 중복 방지 | ✅ 구현됨 | ✅ |
| ManualOrderDetector 클래스 | 수동발주 감지기 | ✅ 구현됨 | ✅ |
| daily_job.py 통합 | run_manual_order_detect() | ✅ 구현됨 | ✅ |
| run_scheduler.py CLI | --detect-manual | ✅ 구현됨 | ✅ |
| run_scheduler.py 스케줄 | 08:00, 21:00 | ✅ 구현됨 | ✅ |

**Match Rate: 100%** (9/9 완전 일치)

---

## 2. 수정 이력 (Iteration 1)

### 2.1 ✅ Critical 버그 수정 완료

**위치**: `src/collectors/manual_order_detector.py:137-138`

**문제**:
- `order_date` 변수 미정의로 `NameError` 발생
- 1차/2차 입고 자동발주 목록이 덮어쓰기됨

**수정 내용**: 불필요한 중복 코드 2줄 삭제

```diff
  # 두 날짜의 자동발주 기록 모두 조회
  auto_orders_same = self.order_history_repo.get_orders_by_date(order_date_same_day)
  auto_orders_prev = self.order_history_repo.get_orders_by_date(order_date_prev_day)
  auto_order_items = {o['item_cd'] for o in auto_orders_same}
  auto_order_items.update({o['item_cd'] for o in auto_orders_prev})
-
- auto_orders = self.order_history_repo.get_orders_by_date(order_date)
- auto_order_items = {o['item_cd'] for o in auto_orders}
```

### 2.2 ✅ Minor 수정 완료

**위치**: `src/config/constants.py:170`

**수정 내용**: 스케줄 시간 상수를 실제 스케줄과 일치시킴

```diff
- MANUAL_ORDER_DETECT_TIMES = ['08:00', '13:00']  # 수동 발주 감지 스케줄
+ MANUAL_ORDER_DETECT_TIMES = ['08:00', '21:00']  # 수동 발주 감지 스케줄 (1차: 20:00 입고 후, 2차: 07:00 입고 후)
```

---

## 3. 구현 완료 항목

### 3.1 DB 스키마 (v19) ✅

```sql
ALTER TABLE order_tracking ADD COLUMN order_source TEXT DEFAULT 'auto';
CREATE INDEX IF NOT EXISTS idx_order_tracking_source ON order_tracking(order_source);
```

### 3.2 Repository 메서드 ✅

| 메서드 | 설명 | 상태 |
|--------|------|------|
| `save_order_manual()` | 수동발주 저장 (중복 체크 포함) | ✅ |
| `get_by_source()` | 소스별 조회 | ✅ |
| `get_existing_tracking_items()` | 중복 방지용 조회 | ✅ |

### 3.3 ManualOrderDetector ✅

- 입고 데이터 조회
- 자동발주 대조 (1차/2차 모두)
- 도착/폐기 시간 계산 (배송 스케줄 반영)
- order_tracking 저장
- inventory_batches 저장 (비푸드)

### 3.4 스케줄링 ✅

| 시간 | 목적 |
|------|------|
| 08:00 | 2차 입고(07:00) 완료 후 감지 |
| 21:00 | 1차 입고(20:00) 완료 후 감지 |

---

## 4. 테스트 검증 상태

| 테스트 | 상태 | 비고 |
|--------|------|------|
| 구문 오류 | ✅ | 버그 수정 완료 |
| 코드 로직 검증 | ✅ | 1차/2차 입고 정상 처리 |
| 상수 일치 | ✅ | constants.py ↔ run_scheduler.py 일치 |

---

## 5. 결론

### Match Rate: **100%**

### 수정 완료 사항:
1. ✅ **Critical 버그 수정** → 137-138줄 삭제 완료
2. ✅ **Minor 수정** → constants.py 스케줄 값 일치 완료

---

**분석 완료**: 2026-02-05
**수정 완료**: 2026-02-05
**아카이브 완료**: 2026-02-05
