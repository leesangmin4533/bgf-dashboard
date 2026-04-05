# 데이터 수집 이슈 체인

> 최종 갱신: 2026-04-05
> 현재 상태: hourly 소급 수집 안정화 계획

---

## [PLANNED] hourly 시간대별 판매 소급 수집 안정화 (P3)

**목표**: hourly_sales_detail 소급 수집(backfill)의 안정성 확보 + 누락 자동 감지
**동기**: 시간대별 매출 비중(morning/lunch/evening_ratio)이 ML 피처로 사용 중. 수집 누락 시 피처 품질 저하
**선행조건**: 없음 (독립 작업)
**예상 영향**: collectors/hourly_sales_collector.py, run_scheduler.py backfill 로직

---
