# Gap Analysis: remaining-qty-realtime-fix

> **Date**: 2026-03-30
> **Match Rate**: 100%
> **Status**: PASS

## Plan vs Implementation

| Plan 항목 | 구현 | Match |
|-----------|------|:-----:|
| ri.stock_qty 기준 보정 | max(0, stock_qty) 사용 | O |
| status='arrived'만 차감 | arrived 조건 필터 | O |
| status='ordered' 미도착 보호 | ordered 제외 | O |
| FIFO(order_date ASC) 차감 | ORDER BY order_date ASC, id ASC | O |
| dry-run 먼저 실행 | 4매장 dry-run 완료 | O |
| 보정 후 과다 0건 확인 | 4매장 0건 확인 | O |

## 보정 결과

| 매장 | 보정 건수 | 초과 차감 |
|:---:|:---:|:---:|
| 46513 | 170 | 703개 |
| 46704 | 171 | 712개 |
| 47863 | 0 | 0 |
| 49965 | 0 | 0 |

## 결론
코드 변경 없음. 데이터 보정만 수행. 4매장 arrived 과다 0건 달성.
