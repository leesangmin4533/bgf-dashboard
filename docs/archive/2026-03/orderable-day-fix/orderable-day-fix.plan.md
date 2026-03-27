# Plan: orderable-day-fix

## Context

2026-03-01 스케줄러 라이브 실행에서 발견된 문제:
- 일요일 실행 시 `orderable_day=월화수목금토` 상품들이 예측 단계에서 `need_qty=0`으로 스킵
- Phase 2에서 BGF 사이트 확인 결과 대부분 `일월화수목금토`(매일)로 교정 → 복구 발주
- **근본 원인**: `orderable_day`는 **배송 스케줄**이지 **발주 제한**이 아님. 발주는 어느 요일이든 가능
- 46513: 초기 46건 → 복구 47건 = 93건, 46704: 비슷한 패턴으로 104건
- 비효율: 예측 스킵 → DB 교정(상품당 3~5초 Selenium) → 복구 발주라는 불필요한 2단계 과정

## 수정 대상 (5개 파일)

### 1. `src/prediction/improved_predictor.py` (라인 2024-2054)
- **제거**: 비발주일 스킵 로직
  - `ORDERABLE_DAY_EXEMPT_MIDS` 상수 제거
  - `_is_orderable_today()` 호출 제거
  - `ctx["orderable_day_skip"] = True` → `need_qty = 0` 경로 제거
- **유지**: 없음 (safety stock은 이 위치가 아닌 카테고리별 모듈에서 계산)

### 2. `src/prediction/categories/ramen.py` (라인 198-201)
- **제거**: `if not is_today_orderable: skip_order = True` 로직
- **유지**: `order_interval = _calculate_order_interval(orderable_day)` → safety stock 계산에 사용

### 3. `src/prediction/categories/snack_confection.py` (라인 342-345)
- **제거**: `if not today_orderable: skip_order = True` 로직
- **유지**:
  - `_is_orderable_today()` 함수 자체 (auto_order에서 아직 사용 가능성)
  - `_calculate_order_interval()` 함수 (safety stock 계산)
  - `order_interval` 기반 safety stock 계산

### 4. `src/order/auto_order.py` (라인 1400-1452, 1902-1983)
- **제거**:
  - Phase A/B 분리 로직 (orderable_today_list / skipped_for_verify 분류)
  - `_verify_and_rescue_skipped_items()` 메서드 전체
- **변경**: `execute_orders()`에서 전체 order_list를 바로 실행 (분류 없이)
- **유지**: 없음 (DB 교정은 별도 스케줄로 이미 존재: 매일 00시 order_unit_collect)

### 5. `tests/test_orderable_day_all.py` (34개 테스트)
- **수정**: 비발주일 스킵 관련 테스트 → "비발주일에도 발주 진행" 테스트로 전환
- **제거**: split/verify/rescue 테스트
- **추가**: orderable_day가 safety stock에만 영향주는 테스트

## 수정하지 않는 것

- `_calculate_order_interval()` (ramen.py, snack_confection.py): safety stock 계산용
- `_is_orderable_today()` 함수 자체: 다른 곳에서 참조 가능성
- `collect_product_info_only()` (order_executor.py): 다른 용도로도 사용
- `update_orderable_day()` (product_detail_repo.py): DB 교정 자체는 유효
- food 카테고리 관련 로직: 이미 면제되어 있어 변경 없음

## 구현 순서

1. `improved_predictor.py` 비발주일 스킵 블록 제거
2. `ramen.py` skip_order 비발주일 분기 제거
3. `snack_confection.py` skip_order 비발주일 분기 제거
4. `auto_order.py` Phase A/B 분리 → 단일 실행으로 통합, `_verify_and_rescue_skipped_items()` 제거
5. `test_orderable_day_all.py` 테스트 업데이트
6. 전체 테스트 실행 (`pytest tests/`)

## 검증

1. `pytest tests/test_orderable_day_all.py` - 수정된 테스트 통과
2. `pytest tests/` - 전체 테스트 통과 (기존 2588개)
3. 예상 결과: 일요일에도 예측 단계에서 `need_qty > 0` 생성, Phase 2에서 분류 없이 직접 발주
