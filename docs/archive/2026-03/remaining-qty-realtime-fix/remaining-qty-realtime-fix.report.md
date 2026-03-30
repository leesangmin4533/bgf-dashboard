# Completion Report: remaining-qty-realtime-fix

> **Feature**: realtime_inventory 기준 remaining_qty 보정
> **Period**: 2026-03-30
> **Match Rate**: 100%

## Summary
이전 보정(총발주-총판매)의 부정확성을 해소하기 위해 BGF 실재고(realtime_inventory.stock_qty) 기준으로 remaining_qty 재보정. status='arrived'(도착분)만 대상으로 FIFO 차감, 미도착(ordered)은 보호.

## Results
- 46513: 170건, 703개 초과 차감
- 46704: 171건, 712개 초과 차감
- 47863/49965: 이전 보정으로 이미 해소
- 보정 후 4매장 과다 0건

## Key Insight
remaining_qty 보정은 "총발주-총판매" 같은 누적 계산이 아닌, BGF 시스템의 실재고(realtime_inventory)를 ground truth로 사용해야 정확함.
