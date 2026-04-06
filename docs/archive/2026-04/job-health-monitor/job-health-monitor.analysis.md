# Gap Analysis — job-health-monitor

**Feature**: job-health-monitor
**Design**: `docs/02-design/features/job-health-monitor.design.md`
**Plan**: `docs/01-plan/features/job-health-monitor.plan.md`
**Date**: 2026-04-07
**Analyzed by**: gap-detector agent

## Overall Match Rate: **97.0%** (PASS, ≥90%)

| Section | Items | Match | Status |
|---|:---:|:---:|:---:|
| §2 Parameters (8) | 8 | 8 | ✅ |
| §3 DB Schema v74 | 6 | 6 | ✅ |
| §4 Components (4.1–4.5) | 5 | 5 | ✅ |
| §5 Integration (Path A / B / startup) | 3 | 2.5 | ⚠ PARTIAL |
| §6 12-step order | 12 | 11 | ⚠ PARTIAL |
| §7 Tests | 19 | 19 | ✅ |
| §8 Rollback | 4 | 4 | ✅ |

## Gaps

### Missing
| Item | Design | Impact | Fix |
|---|---|---|---|
| Path B: `@track_single_job` on `monthly_store_analysis_wrapper` | §5.1 경로 B | Medium — multi_store 아닌 잡 추적 누락 | 1줄 데코레이터 |
| daily_chain_report job_runs summary | §6 step 11 | Low — `summary_last_24h()` 존재하나 미호출 | ~10줄 추가 |

### Positive (Impl > Design)
- Tracker `__exit__`가 실패 시 알림 즉시 발송 + `alerted=1` 마킹 (Design은 기록만 명시)
- JobHealthChecker `_check_missed` 24h stale cap (방어적 추가)
- `logs/job_health_alerts.log` 전용 분리 (기존 alerts.log와 충돌 회피)

### Cosmetic
- Design §3 `v75`/`SCHEMA_MIGRATIONS[75]` 오타 — 구현은 정상 v74

## Recommended Actions
1. **(Medium)** `run_scheduler.py:1597` `monthly_store_analysis_wrapper`에 `@track_single_job` 적용
2. **(Low)** `daily_chain_report.py`에 job_runs summary 섹션 추가
3. **(Doc)** Design §3 v75 → v74 표기 정정

위 1+2 적용 후 예상 Match Rate: **99.5%**
