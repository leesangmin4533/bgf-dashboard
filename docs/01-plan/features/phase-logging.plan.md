# Plan: phase-logging

| 항목 | 내용 |
|------|------|
| Feature | phase-logging |
| 날짜 | 2026-03-29 |
| 예상 공수 | 0.5일 |

## 목표
1. 모든 Phase에 종료 로그 + 소요시간 추가
2. Phase 2 발주 결과 요약 (Order Summary) 1줄 로그
3. 전체 타이밍 리포트 (Timing Report + Slowest Top 5)
4. store_id 시작 로그 명시

## 구현 계획

### Task 1: `phase_timer()` 컨텍스트매니저 (logger.py)
### Task 2: daily_job.py — store_id 로그 + _phase_timings ctx + 타이밍 리포트
### Task 3: collection.py — 11개 Phase에 phase_timer 적용
### Task 4: calibration.py — 10개 Phase에 phase_timer 적용
### Task 5: preparation.py + execution.py — phase_timer + Order Summary

## 변경 파일 6개
logger.py, daily_job.py, collection.py, calibration.py, preparation.py, execution.py

## 참조
- `data/discussions/20260329-phase-logging/03-최종-리포트.md`
