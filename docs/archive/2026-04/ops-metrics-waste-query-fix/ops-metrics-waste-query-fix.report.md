# PDCA Report: ops-metrics-waste-query-fix

> 완료일: 2026-04-07
> Match Rate: 98% — PASS
> 커밋: f47ba10
> 이슈체인: scheduling.md#ops-metrics-waste-rate-mid-cd-컬럼-부재

---

## 핵심 요약

`_waste_rate()`가 `waste_slip_items.mid_cd`(존재하지 않는 컬럼)를 GROUP BY해서 4매장 전부 `OperationalError` 발생 → K2 마일스톤 NO_DATA 지속. `common.products` INNER JOIN으로 mid_cd를 도출하도록 수정. 4매장 실데이터 재현 성공, 17/17 테스트 통과.

**발견 경로**: claude-auto-respond 자동 분석 보고서 (2026-04-07) — 진단 로깅 강화 직후 첫 자동 감지 사례.

---

## PDCA 사이클 요약

| 단계 | 결과 |
|------|------|
| **Plan** | 옵션 A(products JOIN) 채택, B(스키마 추가)/C(large_cd 집계) 기각 |
| **Design** | Plan 5대 구멍 메움: ATTACH 헬퍼 재사용, INNER JOIN, daily_sales 무수정, 회귀 3케이스, 성능 무시 가능 |
| **Do** | 1곳 커넥션 교체 + JOIN 2줄 + 매칭률 경고 15줄 + 테스트 3개 |
| **Check** | Match Rate 98% (cosmetic gap 2건) |
| **Act** | 불필요 (≥90%) |

---

## 변경 사항

### 코드
- `src/analysis/ops_metrics.py:134-230` — `_waste_rate()`
  - `DBRouter.get_store_connection()` → `get_store_connection_with_common()`
  - `JOIN common.products p ON wsi.item_cd = p.item_cd`, `GROUP BY p.mid_cd`
  - 매칭률 5% 초과 시 `logger.warning()` (신제품 동기화 모니터링 부수효과)
- `tests/test_ops_metrics_waste_rate.py` (신규) — `TestWasteRateProductsJoin` 3개
  - `test_waste_rate_joins_products_for_mid_cd`
  - `test_waste_rate_unmatched_items_trigger_warning`
  - `test_waste_rate_no_such_column_regression`

### 문서
- `docs/05-issues/scheduling.md` — `[WATCHING]` 등록, 시도 1 + 교훈 3가지
- `CLAUDE.md` — 활성 이슈 테이블 갱신

---

## 검증 결과

### 수동 재현 (4매장)
```
46513: {"categories": [{"mid_cd": "001", "rate_7d": 0.410, "rate_30d": 0.281}, ...]}
46704: {"categories": [{"mid_cd": "001", "rate_7d": 0.138, "rate_30d": 0.186}, ...]}
47863: {"categories": [{"mid_cd": "001", "rate_7d": 0.375, "rate_30d": 0.356}, ...]}
49965: {"categories": [{"mid_cd": "001", "rate_7d": 0.196, "rate_30d": 0.086}, ...]}
```
4매장 전부 categories 정상 반환 (mid_cd 001~005 폐기율 집계 성공).

### 자동 테스트
- 17/17 통과 (claude-responder 14 + waste_rate 3)

### 잔여 라이브 검증
- [ ] 다음 23:55 OpsMetricsCollector 실행에서 `waste_rate 실패` 로그 소멸
- [ ] 다음 milestone_snapshots에서 K2 NO_DATA 탈출

---

## 교훈

1. **스키마 확인 먼저**: 쿼리 작성 전 실제 컬럼을 schema.py에서 확인했다면 버그 자체가 발생하지 않았음
2. **정규화 원칙**: mid_cd는 products가 단일 원천. 다른 테이블은 JOIN으로 얻는다
3. **매칭률 경고 부수효과**: products에 없는 폐기 item_cd 발견 시 신제품 동기화 이슈 조기 감지
4. **claude-auto-respond 효과 입증**: 진단 로깅 강화 직후 첫 자동 분석이 실제 운영 버그를 발견 — PDCA 선순환 시작

---

## 관련 문서
- Plan: `docs/01-plan/features/ops-metrics-waste-query-fix.plan.md`
- Design: `docs/02-design/features/ops-metrics-waste-query-fix.design.md`
- Analysis: `docs/03-analysis/ops-metrics-waste-query-fix.analysis.md`
- Issue: `docs/05-issues/scheduling.md#ops-metrics-waste-rate-mid-cd-컬럼-부재`
- 상위 작업: `claude-respond-fix` (이 버그를 발견한 자동 분석 시스템)
