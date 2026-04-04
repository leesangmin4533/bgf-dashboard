# Plan: expiry-alert-accuracy — 폐기 알림 정확도 개선

## 1. 개요

### 문제 정의
46704 매장 14시 폐기 알림에서 3건 이상 발견:
1. `빅삼)전주비빔불고기삼각2 -1개` — **마이너스 수량** 표시
2. `삼)참치마요삼각2` — order_tracking에 발주=0/잔여=0이지만 알림 포함
3. `삼)치즈닭갈비볶음맛2` — 동일 패턴

### 원인 분석 (데이터 검증 완료)

**알림 수량 소스**: `daily_sales.stock_qty` (BGF 사이트에서 수집한 전체 재고)

| 이상 | 원인 | 코드 위치 |
|------|------|----------|
| 마이너스 수량 | `stock_qty < 0` 체크 없음. BGF 시스템에서 마이너스 재고 가능 | expiry_checker.py:529 |
| 소진 배치 포함 | `stock_qty > 0`이면 통과하지만, 이 재고는 **다른 배치**(4/4 입고분). 해당 만료 배치(4/3)는 이미 consumed | expiry_checker.py:563 |

### 데이터 검증 결과 (7일 136건)

| 수량 기준 | 정확도 | 비고 |
|----------|-------|------|
| **stock_qty (현재)** | **72%** | 실시간 판매 반영, 마이너스/배치혼동만 해결 필요 |
| batch_remaining (대안) | 67% | 배치 미등록(None)이 많아 과대 알림 → **채택 불가** |

**결론**: `batch_remaining`으로 교체하면 정확도가 하락. `stock_qty` 유지 + 방어 로직 추가가 올바른 방향.

## 2. 요구사항

### 필수 (Must)
- R1: `stock_qty < 0`인 경우 알림에서 제외 (마이너스 수량 방지)
- R2: `stock_qty > 0 AND batch_remaining = 0`인 경우 알림에서 제외 (소진 배치 방어)
  - 현재 코드에 이미 있으나(line 542), 작동 여부 재검증 필요
- R3: 알림 메시지에 `remaining_qty <= 0`인 상품 포함 방지 (최종 방어선)

### 선택 (Should)
- R4: `order_source='manual'` + `order_qty=0` 레코드가 order_tracking에 생성되는 원인 파악
- R5: 폐기 알림 발송 전 최종 요약 로그 (역추적용)

### 제외 (Won't)
- 수량 소스를 `batch_remaining`으로 교체 → 데이터 검증 결과 정확도 하락
- receiving_history 기반 알림 구조 변경 → 현재 구조가 stock_qty 기반으로는 72% 정확

## 3. 영향 범위

### 수정 대상
- `src/alert/expiry_checker.py` — `_get_receiving_items_expiring_at()` 내 stock_qty 필터

### 수정하지 않는 파일
- `src/alert/config.py` — 만료 시간 매핑 정상
- `src/collectors/receiving_collector.py` — 입고 수집 정상
- `src/infrastructure/database/repos/inventory_batch_repo.py` — 배치 관리 정상

### 테스트 방법
- 과거 7일 데이터로 수정 전후 정확도 비교
- 46704 매장 다음 14시 알림에서 마이너스/소진배치 상품 미포함 확인

## 4. 위험 요소

| 위험 | 영향 | 대응 |
|------|------|------|
| stock_qty < 0 제외 시 실제 폐기 필요 상품 놓침 | 낮음 (마이너스 재고는 이미 소진된 상태) | 로그로 제외 사유 기록 |
| batch_remaining=0 제외가 과도하면 알림 누락 | 중간 (배치 미등록 상품은 None이므로 영향 없음) | batch=0 + stock>0 경우만 제외 |
