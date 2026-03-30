# Plan: remaining-qty-fix

> **Feature**: order_tracking remaining_qty FIFO 차감 덮어쓰기 버그 수정
> **Created**: 2026-03-30
> **Level**: Dynamic
> **Priority**: Critical (운영 데이터 정합성)

---

## 1. 문제 정의

### 현상
- order_tracking의 remaining_qty가 실제 판매를 반영하지 않아, 폐기 대상 목록에 이미 판매된 상품이 남아있음
- 46513점 3/30 14시 폐기 주먹밥 5건 중 다수가 실제로는 판매 완료 상태

### 근본 원인
`order_tracking_repo.update_receiving()` (line 254-255)이 입고 확인 시 `remaining_qty`를 `receiving_qty`(입고수량)로 **무조건 덮어씀**.

```python
# 버그 코드 (order_tracking_repo.py:254-255)
UPDATE order_tracking
SET arrival_time = ?, remaining_qty = ?, updated_at = ?
WHERE id = ?
```

### 경합 시나리오 (실증)
```
23:50 매출 수집 → _upsert_daily_sale → FIFO 차감 (remaining_qty: 1→0)
23:51 입고 확인 → update_receiving   → 덮어쓰기 (remaining_qty: 0→1) ← 1분 후 리셋!
```

### 영향 범위
- **호출처**: `receiving_collector.py:997` (food 카테고리 입고 확인 시)
- **영향 테이블**: order_tracking
- **영향 기능**: 폐기 알림, 잔량 기반 모든 조회

---

## 2. 수정 방안

### 변경 파일 (1개)
| 파일 | 변경 내용 |
|------|----------|
| `src/infrastructure/database/repos/order_tracking_repo.py` | `update_receiving()`: remaining_qty 덮어쓰기 제거, actual_receiving_qty + status 업데이트로 변경 |

### 수정 내용

**Before:**
```python
UPDATE order_tracking
SET arrival_time = ?, remaining_qty = ?, updated_at = ?
WHERE id = ?
```

**After:**
```python
UPDATE order_tracking
SET arrival_time = ?, actual_receiving_qty = ?, status = 'arrived', updated_at = ?
WHERE id = ?
```

- `remaining_qty`는 FIFO 차감 로직(`_upsert_daily_sale`)만 관리
- `actual_receiving_qty`에 실제 입고수량 기록 (기존 컬럼, 현재 미사용)
- `status`를 'arrived'로 변경하여 입고 완료 표시

### 기존 잔량 보정 (1회성)
- 현재 remaining_qty가 잘못된 레코드를 daily_sales 판매 데이터 기반으로 재계산
- 스크립트로 일괄 보정 (운영 DB 직접 수정)

---

## 3. 검증 계획

| 단계 | 방법 |
|------|------|
| 단위 테스트 | update_receiving 호출 후 remaining_qty 미변경 확인 |
| 통합 테스트 | 매출 수집 → 입고 확인 순서로 실행 시 FIFO 차감 유지 확인 |
| 데이터 검증 | 보정 후 remaining_qty 합 = 발주합 - 판매합 일치 확인 |

---

## 4. 리스크

| 리스크 | 대응 |
|--------|------|
| actual_receiving_qty 컬럼 미존재 | order_tracking 스키마 확인 (이미 존재 확인됨) |
| 기존 코드에서 remaining_qty를 입고수량으로 참조하는 곳 | Grep으로 전수 확인 |
| 보정 스크립트 오류 | dry-run 먼저 실행, 변경 전 백업 |

---

## 5. 예상 작업

1. `update_receiving()` 수정 (5분)
2. remaining_qty 참조처 전수 확인 (10분)
3. 테스트 작성/실행 (10분)
4. 기존 데이터 보정 스크립트 (15분)
5. 커밋 + 푸시
