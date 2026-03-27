# Plan: order-unit-alignment (발주단위 배수 정합성 수정)

## 문제
라면류 등에서 order_unit_qty 배수가 아닌 수량이 order_tracking에 기록됨.
예: 8801043022262 — order_unit_qty=16, order_qty=12. 3월 기준 14건.

## 근본 원인
`improved_predictor.py`의 `predict()` 메서드에서 `_round_to_order_unit()`으로 배수 정렬 후
3개 후처리(Diff 피드백, 잠식 감지, max cap)가 정렬을 깨뜨림.

## 수정
1. **Fix A**: 후처리 순서 변경 — 페널티/잠식/cap 먼저 → `_round_to_order_unit()` 마지막
2. **Fix B**: order_executor.py — actual_qty를 `multiplier × order_unit_qty`로 계산

## 수정 파일
- `src/prediction/improved_predictor.py:1522-1571`
- `src/order/order_executor.py:2104-2131`
- `tests/test_order_unit_alignment.py` (신규)
