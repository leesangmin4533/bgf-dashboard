# Analysis: schedule-optimization

## Match Rate: 100%

| # | 요구사항 | 상태 |
|---|---------|:----:|
| 1 | `run_optimized()` collect_only 파라미터 | MATCH |
| 2 | ctx에 collect_only 전달 | MATCH |
| 3 | calibration/preparation/execution 스킵 | MATCH |
| 4 | 로그인 전용 경로 (sales 미포함 시) | MATCH |
| 5 | `_full` 가드 (Phase 1.04 앞 정의) | MATCH |
| 6 | `_SkipPhase` 패턴 (Phase 1.05, 1.36) | MATCH |
| 7 | Phase 1.06/1.01/1.1/1.17/1.2/1.3/1.35 가드 | MATCH |
| 8 | Phase 1.15+1.16 가드 없음 | MATCH |
| 9 | confirm `collect_only=["waste_slip"]` | MATCH |
| 10 | pre_collect `collect_only=["sales","waste_slip"]` | MATCH |
| 11 | `consolidated_nightly_collect_wrapper` 존재 | MATCH |
| 12 | Phase A(발주단위) + Phase B(상품상세) 단일 세션 | MATCH |
| 13 | 00:00 → 통합 wrapper 등록 | MATCH |
| 14 | 01:00 스케줄 제거 | MATCH |
| 15 | 기존 wrapper CLI용 보존 | MATCH |
| 16 | bulk_collect 사전 대상 체크 | MATCH |
| 17 | 대상 0건 → Selenium 생략 + 로그 | MATCH |
| 18 | inventory_verify `_run_task` 병렬 | MATCH |
| 19 | `run_verification_single` import | MATCH |
| 20 | 통합 엑셀 리포트 생성 | MATCH |
| 21 | 테스트 320건 통과, 0건 신규 실패 | MATCH |

## 변경 파일

| 파일 | 변경 줄 수 |
|------|:--------:|
| `src/scheduler/daily_job.py` | +12 |
| `src/scheduler/phases/collection.py` | +22 |
| `run_scheduler.py` | +120/-25 |
