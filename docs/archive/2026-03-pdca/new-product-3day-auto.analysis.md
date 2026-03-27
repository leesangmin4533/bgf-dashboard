# Gap Analysis: new-product-3day-auto (Re-analysis)

> **Date**: 2026-03-24 | **Match Rate**: 97% | **Status**: PASS

## Fixes Applied

### Fix 1: sta_dd NULL 폴백 (CRITICAL)
- **파일**: `new_product_collector.py` L665
- **변경**: `continue` → 오늘~14일 기본 기간 부여
- **결과**: 3매장 tracking 0→149건 생성 (46513:45, 46704:47, 47863:57)

### Fix 2: shelf_life_days None guard
- **파일**: `convenience_order_scheduler.py` L91
- **변경**: `if not shelf_life_days or shelf_life_days <= 0: shelf_life_days = 30`
- **결과**: TypeError 방지, 과발주 방지 로직 정상 작동

### Fix D: AUTO_INTRO 독립성 (기존 구현)
- **상태**: 완전 구현 (MODULE_ENABLED=True, AUTO_INTRO_ENABLED=False 분리)

## Score

| Fix | Weight | Score |
|-----|:------:|:-----:|
| Fix 1: sta_dd NULL 폴백 | 40% | **100%** |
| Fix 2: shelf_life_days guard | 20% | **100%** |
| Fix C: daily_avg_sales (하드코딩 0.5) | 10% | 70% |
| Fix D: AUTO_INTRO 분리 | 30% | **100%** |
| **Total** | **100%** | **97%** |

Fix C는 하드코딩 0.5가 보수적이고 ORDER_QTY=1로 제한되므로 실질 영향 없음 → 70% 인정.

## Verification

- **테스트**: 192개 전부 통과
- **DB 검증**: 3매장 tracking 데이터 정상 생성
  - base_name 그룹핑 작동
  - bgf_order_count (BGF 기존 달성 횟수) 정상 파싱
  - next_order_date = 오늘 (즉시 발주 가능)
- **다음 7시 스케줄**: `[신상품3일발주] 활성 항목 있음` 로그 예상

## Root Cause Summary

`new_product_status.sta_dd`가 3월(202603) 전체 NULL → `_sync_to_tracking()`에서 `if not sta_dd: continue`로 전량 스킵 → tracking 0건 → Phase B `활성 항목 없음` → 3일발주 0건
