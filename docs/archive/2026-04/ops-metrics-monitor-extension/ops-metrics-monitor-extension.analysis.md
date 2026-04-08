# Analysis: ops-metrics-monitor-extension

> Check 단계 (gap-detector 결과)
> 작성일: 2026-04-08
> Plan: `docs/01-plan/features/ops-metrics-monitor-extension.plan.md`
> Design: `docs/02-design/features/ops-metrics-monitor-extension.design.md`
> 구현 커밋: 8dd9090

---

## Match Rate: **99%** ✅

설계 명세의 모든 핵심 요소가 정확히 구현됨. 차이는 모두 방어 코드 및 테스트 보강 방향(positive gap).

---

## 검증 항목 vs 결과

| 항목 | 설계 | 구현 | 일치 |
|---|---|---|:---:|
| #6 SQL 정의 (24h 윈도우 + julianday < 1.0) | 명시 | `src/analysis/ops_metrics.py:70-110` | ✅ |
| #6 expiration_days <= 7 | 명시 | `COALESCE(expiration_days, 999) <= 7` (NULL 방어 강화) | ✅+ |
| #7 collect_system 정적 메서드 | 명시 | `OpsMetrics.collect_system()` | ✅ |
| #7 missing_count/missing_stores 키 | 명시 | 일치 + insufficient_data 폴백 추가 | ✅+ |
| detector 시스템 1회 호출 | 3줄 | 6줄 (try/except 견고성 강화) | ✅+ |
| METRIC_TO_FILE 매핑 2개 | 명시 | 일치 | ✅ |
| collect_all 키 추가 | 명시 | 일치 | ✅ |
| 회귀 테스트 케이스 수 | 7+ | 15 (강화) | ✅+ |

---

## Gap List

| # | 등급 | 항목 | 권장 |
|---|---|---|---|
| 1 | minor (positive) | SQL NULL 방어 (`COALESCE`) | 그대로 유지 |
| 2 | minor (positive) | `collect_system` insufficient_data 폴백 | 그대로 유지 |
| 3 | minor (positive) | detector try/except 강화 | 그대로 유지 |
| 4 | minor (positive) | 테스트 케이스 초과 (7→15) | 그대로 유지 |

**critical/major 갭 0건.** 모든 차이가 운영 안전성 방향의 보강이라 fix 불필요.

---

## 회귀 테스트 결과

- **신규 14건 + 기존 ops_anomaly 29건 = 43 passed** (`pytest -v`, 4.42s)
- 신규 실패 0건, pre-existing 실패와 무관

---

## 라이브 검증 결과 (가동 1분 만에 발견)

| 발견 | 내용 | 후속 |
|---|---|---|
| 라이브 1 | 4매장 false consumed 131건 (46513=18, 46704=44, 47863=12, 49965=57) | scheduler 38624 재기동 완료 (13:17), 24h 후 재측정 → CLAUDE.md OPEN P1 |
| 라이브 2 | 46704 04-07 검증 로그 누락 | 별도 조사 → CLAUDE.md OPEN P3 |

→ **모니터의 즉각적 가치 입증** — 가동 1분 만에 진짜 운영 신호 2개 포착.

---

## 미세 개선 제안 (선택, 100%로 끌어올리려면)

`docs/02-design/features/ops-metrics-monitor-extension.design.md` Section 3 SQL 코드 블록에 1줄 주석 추가:
```sql
AND COALESCE(expiration_days, 999) <= 7  -- NULL 방어 (구현 보강)
```

→ Match Rate 99% → 100%. 다만 운영 영향 0이라 **선택 사항**.

---

## 다음 단계

✅ **Match Rate 99% ≥ 90% 충족** → iterate 불필요

| 명령 | 비고 |
|---|---|
| `/pdca report ops-metrics-monitor-extension` | 완료 보고서 생성 (PLAN+DESIGN+DO+CHECK 통합) |
| `/pdca next` | 자동 추천 (= report 제안 예상) |

또는 **04-09 23:55 첫 자동 실행 결과를 본 뒤 report**로 가는 것도 좋습니다 (실제 운영에서 알림 발송 검증까지 포함한 보고서가 더 의미 있음).
