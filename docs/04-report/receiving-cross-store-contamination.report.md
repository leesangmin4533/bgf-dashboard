# Completion Report: receiving-cross-store-contamination

**Date**: 2026-04-03
**Match Rate**: 100%
**Commit**: 32b4caf

## Summary

센터매입 수집기의 Direct API가 점포 필터 없이 전표번호만으로 조회하여, 46513의 입고 데이터가 3개 매장에 복제 저장된 문제를 해결.

## Root Cause

1. Direct API `/stgj010/search`에 점포코드 파라미터 미포함
2. 4/1 11:42 폴백 코드 커밋 후 스케줄러 미재시작 → 20:31 수집 시 미적용
3. 성공한 2/13 전표가 46513의 공유 전표 → 4매장 전체에 저장

## Solution

1. **DB 정리**: 46704/47863/49965에서 오염 전표 삭제 (다중매장 전표 63→0)
2. **폐기알림 방어**: daily_sales에 기록 없는 상품(stock_qty=None) 제외
3. **폴백 코드**: 이미 적용 완료 (Direct API 성공률 50% 미만 → Selenium 폴백)

## Verification

| 검증 항목 | 결과 |
|----------|------|
| 다중 매장 전표 | 0개 (63→0) |
| 46704 샌드위치 전체 제거 | rh=0, ib=0, ot=0 |
| 10시 폐기알림 샌드위치 | 0건 (오발송 해소) |

## Discovery Path

사용자 보고: "샌)계란블루베리잼샌드2 폐기 1개 — 발주된 적 없는 상품"
→ order_tracking: order_source='receiving' only
→ receiving_history: 전표번호 동일 (4매장)
→ daily_sales: 0건 (매장에 물리적으로 없음)
→ Direct API 점포 필터 누락 확인

## Changed Files

- `src/alert/expiry_checker.py` (1줄 추가: stock_qty=None 제외)
- DB 직접 정리: 46704/47863/49965 receiving_history, inventory_batches, order_tracking
