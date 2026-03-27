# Plan: force-order-fix

> FORCE_ORDER 오판 수정 - 재고 있는 상품에 불필요한 강제 발주 방지

## 1. 문제 정의

### 현상
- 호정)군고구마도나스(8804624073530): 재고 10개 있는데 FORCE_ORDER로 1배수(10개) 불필요 발주
- 2026-03-04 07:04~07:07 발생

### 근본 원인 (2가지 버그)

**버그 1: pre_order_evaluator 재고 불일치**
- eval 시점(07:04:32): DB 캐시 기준 `current_stock=0` → "현재 품절" → FORCE_ORDER 판정
- prefetch 시점(07:06:32): BGF 사이트 실제 재고 `stock_qty=10`
- DB의 stale 재고(갱신 안 된 재고)를 기준으로 품절 오판

**버그 2: FORCE 보충 생략 조건 불충분 (auto_order.py:799)**
```python
# 현재 코드 - pending > 0일 때만 생략
if r.pending_qty > 0 and r.current_stock + r.pending_qty > 0:
    continue  # 생략
```
- predict_batch 결과에서 `current_stock=10, pending=0`
- pending=0이라 조건 불충족 → 재고 10개 있어도 생략 안 됨
- `order_qty < 1 → order_qty = 1` 강제 최소화로 불필요한 1개 발주

### 영향 범위
- FORCE_ORDER로 판정된 모든 상품 중 실제 재고가 있는 상품에서 과잉발주 발생 가능
- 특히 DB 재고 갱신 주기가 긴 상품(비푸드, 일반 카테고리)에서 빈번

## 2. 해결 방안

### Fix 1: FORCE 보충 생략 조건 강화 (auto_order.py:799)
```python
# 수정: current_stock만으로도 충분하면 생략
if r.current_stock + r.pending_qty > 0:
    logger.info(
        f"[FORCE보충생략] {r.item_nm[:20]}: "
        f"stock={r.current_stock}+pending={r.pending_qty} "
        f"-> 재고/미입고분 충분"
    )
    continue
```
- `r.pending_qty > 0` 조건 제거 → `r.current_stock + r.pending_qty > 0`이면 생략
- predict_batch가 반환하는 current_stock은 prefetch 이후 실시간 값

### Fix 2: pre_order_evaluator에 실시간 재고 캐시 반영 (선택적 강화)
- evaluate_all() 시 set_stock_cache()로 주입된 실시간 재고가 있으면 DB 재고 대신 사용
- 현재는 DB 기준 stale 재고만 사용 → 실시간 캐시 우선 적용

## 3. 수정 대상 파일

| 파일 | 수정 내용 | 우선순위 |
|------|----------|---------|
| `src/order/auto_order.py` | FORCE 보충 생략 조건 강화 (799줄) | **필수** |
| `src/prediction/pre_order_evaluator.py` | 실시간 재고 캐시 반영 (선택) | 권장 |

## 4. 테스트 계획

| 테스트 | 검증 내용 |
|--------|----------|
| test_force_skip_with_stock | stock=10, pending=0 → FORCE 보충 생략 |
| test_force_skip_with_pending | stock=0, pending=5 → FORCE 보충 생략 |
| test_force_order_genuine_stockout | stock=0, pending=0 → FORCE 발주 정상 작동 |
| test_force_cap_applied | FORCE 발주 시 상한 정상 적용 |
| test_eval_stock_cache | 실시간 재고 캐시 있으면 DB 대신 사용 |

## 5. 리스크

- **낮음**: Fix 1은 조건 완화(생략 범위 확대)이므로 기존 정상 케이스에 영향 없음
- 진짜 품절(stock=0, pending=0)은 기존과 동일하게 FORCE_ORDER 작동
- Fix 2는 선택적이며, 캐시 없으면 기존 DB 로직 유지

## 6. 예상 효과

- 재고 있는 상품의 불필요한 FORCE 강제 발주 제거
- 과잉발주 감소 → 재고 관리 효율 향상
