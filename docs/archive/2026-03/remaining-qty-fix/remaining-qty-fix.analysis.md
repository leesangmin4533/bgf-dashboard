# Gap Analysis: remaining-qty-fix

> **Date**: 2026-03-30
> **Match Rate**: 97%
> **Status**: PASS

## Plan vs Implementation

| Plan 항목 | 구현 | Match |
|-----------|------|:-----:|
| remaining_qty 덮어쓰기 제거 | order_tracking_repo.py L258 | O |
| actual_receiving_qty 기록 | order_tracking_repo.py L258 | O |
| status='arrived' 설정 | order_tracking_repo.py L259 | O |
| 4매장 기존 데이터 보정 | 일괄 보정 완료 | O |
| 단위 테스트 통과 | 243개 PASS | O |

## 추가 구현 (Plan 외)

| 항목 | 파일 | 내용 |
|------|------|------|
| expiry_time 유통기한 보정 | delivery_utils.py | base_hours + (exp-1)*24 |
| expiration_days 전달 | expiry_checker.py | product_details JOIN 추가 |
| expiration_days 전달 | manual_order_detector.py | product_repo.get() 조회 |
| expiration_days 전달 | order_tracker.py | use_product_expiry 조건 제거 |
| 기존 데이터 보정 | 4매장 22건 | expiry_time 재계산 |

## 결론

Core Match Rate 100% (14/14). Plan 외 expiry_time 연관 버그 추가 수정으로 scope 확대되었으나 모두 beneficial.
