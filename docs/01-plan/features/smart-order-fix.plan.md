# 스마트발주 관리 전환 (smart-order-fix)

## 1. 개요

### 문제
- 46513: Override+Exclude 동시 ON (모순, cancel 미실행)
- 46704/47863/49965: Override OFF (cancel 미실행)
- order_tracker: cancel_smart(qty=0) 기록 누락

### 목표
전 매장에서 스마트발주를 우리 예측으로 관리 (A안)

## 2. 범위

### 수정 사항
1. 46513: EXCLUDE_SMART_ORDER → false
2. 46704, 47863, 49965: SMART_ORDER_OVERRIDE → true
3. order_tracker.py: qty=0 cancel_smart 기록 추가
4. order_tracker.py: order_source 하드코딩 → source 필드 활용

## 3. 성공 기준
- [ ] 4매장 SMART_ORDER_OVERRIDE=true
- [ ] 4매장 EXCLUDE_SMART_ORDER=false
- [ ] cancel_smart 항목이 order_tracking에 기록됨
- [ ] 기존 테스트 통과

## 4. 제약
- 내일 07:00 발주에 영향 → 오늘 완료 필수
- cancel_smart는 BGF에 qty=0 전송 → 실패해도 피해 없음

## 5. 토론 결과
- data/discussions/20260328-smart-order/03-최종-리포트.md 참조
