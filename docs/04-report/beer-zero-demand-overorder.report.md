# Completion Report: beer-zero-demand-overorder

**Date**: 2026-04-03
**Match Rate**: 100%
**Commit**: 6211a1e

## Summary

맥주/소주 카테고리 저빈도 상품의 무수요 반복발주 문제를 해결했습니다.

## Problem

47863, 49965 매장에서 삿포로, 필굿, 크로넨버그 등 저빈도 맥주가 수요 없이 30일간 43+20회 발주 (390+개 불필요 발주).

## Root Cause

`beer.py`/`soju.py`의 `daily_avg = total_sales / data_days` (판매일 기준) 계산 버그.
30일 중 1일만 팔아도 그 날의 판매량이 daily_avg가 되어 safety_stock 과대 산출.

## Solution

- `daily_avg` 분모를 `config["analysis_days"]`(30일, 달력일)로 변경 (beer 3곳, soju 1곳)
- `min_data_days` 미달 시 `safety_stock = max(safety_stock, 1.0)` 하한 (신규 도입 결품 방지)

## Verification

| 상품 | Before | After | 결과 |
|------|:------:|:-----:|------|
| 삿포로350 (저빈도) | safety=12.0 | safety=1.0 | 과발주 해소 |
| 카스500 (고빈도) | safety=15.1 | safety=13.1 | 영향 미미 |
| 필굿 (저빈도) | safety=18.0 | safety=1.0 | 과발주 해소 |

## Expert Review

악마의 변호인 + 실용주의자 멀티 에이전트 토론으로 리스크 검증:
- 고빈도 맥주 결품 위험 → 허용 범위 (~17% 감소, 2일분 충분)
- food 카테고리 파급 → 미수정 (보류)
- 신규 도입 결품 → 하한 1로 방지

## Changed Files

- `src/prediction/categories/beer.py` (+6줄)
- `src/prediction/categories/soju.py` (+4줄)

## Next Steps

- 다음 07:00 자동발주 로그에서 삿포로/필굿 발주 중단 확인
- 소주 카테고리 동일 패턴 모니터링
- DemandClassifier sell_day_ratio 이슈는 별도 작업으로 분리 (현재 직접 영향 없음)
