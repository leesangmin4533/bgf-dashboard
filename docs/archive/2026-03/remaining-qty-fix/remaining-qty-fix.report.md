# Completion Report: remaining-qty-fix

> **Feature**: order_tracking remaining_qty FIFO 차감 + expiry_time 유통기한 보정
> **Period**: 2026-03-30
> **Match Rate**: 97%

## Summary

order_tracking의 remaining_qty가 입고 확인 시 덮어쓰여 FIFO 차감이 무효화되는 버그와, expiry_time이 유통기한 1일 기준 하드코딩으로 2~3일 상품이 오계산되는 버그를 수정.

## Changes

### Commit 1: 599ac29
- **파일**: order_tracking_repo.py
- **내용**: update_receiving()에서 remaining_qty 덮어쓰기 제거 → actual_receiving_qty만 업데이트

### Commit 2: 2974cb4
- **파일**: delivery_utils.py, expiry_checker.py, manual_order_detector.py, order_tracker.py
- **내용**: shelf_life_hours에 개별 expiration_days 보정 (base_hours + (exp-1)*24)

## Data Corrections
- 4매장 remaining_qty 일괄 보정 (FIFO 재계산)
- 4매장 expiry_time 22건 보정 (유통기한 > 1일 상품)

## Test Results
- 243개 테스트 통과 (241 expiry/delivery + 2 order_tracking)
- 기존 실패 7개는 pre-existing (feature count 변경 등)

## Impact
- 46513점 3/30 14시 폐기: 7건 → 2건 (정확도 향상)
- 유통기한 2~3일 도시락/주먹밥 38개 상품의 폐기시간 정상화
