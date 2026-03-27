# Completion Report: new-product-3day-auto

> **Date**: 2026-03-24 | **Match Rate**: 97% | **Status**: Completed

## Summary

신상품 3일발주 미달성(mids) 자동 발주가 3매장 전부 0건이던 문제를 해결.
코드는 이미 완성돼 있었으나 **DB 데이터 결함(sta_dd=NULL)** 으로 tracking 테이블에 데이터가 생성되지 않아 작동 불능 상태였음.

## Root Cause

```
new_product_status 테이블 (3월 W9~W13)
  sta_dd = NULL, end_dd = NULL  ← BGF 수집 시 미파싱

_sync_to_tracking() L665:
  if not sta_dd or not end_dd:
      continue  ← 3월 mids 전량 스킵

결과: tracking 0건 → Phase B "활성 항목 없음" → 3일발주 0건
```

## Changes

| # | 파일 | 변경 | 줄 |
|:-:|------|------|:--:|
| 1 | `src/collectors/new_product_collector.py` | `from datetime import datetime` → `datetime, timedelta` | L24 |
| 2 | `src/collectors/new_product_collector.py` | sta_dd NULL 시 오늘~14일 폴백 기간 부여 (continue 제거) | L665-672 |
| 3 | `src/domain/new_product/convenience_order_scheduler.py` | shelf_life_days None/0 guard → 기본 30일 | L91-92 |

## Impact

### Before (3매장 전부)
```
[신상품3일발주] 활성 항목 없음 — 스킵
tracking: 0건, 자동발주: 0건
```

### After
| 매장 | tracking | active | 예상 효과 |
|:----:|:--------:|:------:|:---------:|
| 46513 | **45건** | 45 | 달성률 14%→70%+, 지원금 +50,000 |
| 46704 | **47건** | 47 | 달성률 57%→80%+, 지원금 +110,000 |
| 47863 | **57건** | 57 | 달성률 14%→50%+, 지원금 +40,000 |

### 예상 월간 지원금 증가: **~200,000원/월**

## Verification

| 항목 | 결과 |
|------|:----:|
| 테스트 192개 | ✅ PASS |
| tracking 데이터 생성 (수동 실행) | ✅ 149건 |
| next_order_date 설정 | ✅ 오늘 (즉시 발주 가능) |
| bgf_order_count 파싱 | ✅ ds_yn "1/3"→placed=1 |
| base_name 그룹핑 | ✅ 1/2차 상품 통합 |
| shelf_life_days None guard | ✅ TypeError 방지 |

## Lessons Learned

1. **코드 완성 ≠ 작동**: 로직은 완벽했지만 데이터 결함(sta_dd NULL)으로 전혀 작동 안 함
2. **`continue` guard의 위험**: 데이터 품질 문제를 silently 무시 → 폴백 또는 로깅 필수
3. **수집 데이터 검증 부재**: BGF 수집 시 sta_dd/end_dd NULL 체크 미비

## PDCA Cycle

```
[Plan] ✅ → [Design] ⏭ → [Do] ✅ → [Check] ✅ (97%) → [Report] ✅
```

Design 단계 스킵 (blocker 해소 수준의 2줄 수정이라 plan→do 직행)
