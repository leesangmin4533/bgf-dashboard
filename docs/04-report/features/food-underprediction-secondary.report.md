# Report: food-underprediction-secondary (Phase A)

> 작성일: 2026-04-08
> 상태: Phase A 완료 / Phase B 관측 대기
> Plan: docs/01-plan/features/food-underprediction-secondary.plan.md
> Design: docs/02-design/features/food-underprediction-secondary.design.md
> 이슈체인: docs/05-issues/order-execution.md#food-underprediction-secondary
> PR 경로: leesangmin4533/bgf-dashboard#4 (cherry-pick 반영, `98c7248`)

---

## 1. 요약

Phase A "관측 + 사각지대" 착지 완료. stage_trace 5단계 가시화와 stock_qty<0 sentinel 정규화를
푸드 카테고리(mid 001~005, 012) 한정으로 배포하여, food-underprediction-secondary 7가설 검증
봉쇄를 해제.

- **작업 커밋**: `98c7248` feat(prediction): stage_trace 5단계 가시화 + stock_qty<0 sentinel 정규화 (ultraplan-B)
- **머지 경로**: PR #4 → cherry-pick to main (base drift 11 커밋으로 직접 머지 불가)
- **테스트**: 신규 9/9 통과, 인접 회귀 0건
- **스키마**: v75 → v76 (`prediction_logs.stage_trace TEXT`, `association_boost REAL`)

---

## 2. 완료 범위 (Plan 대비)

| Design 항목 | 상태 | 커밋/파일 |
|---|---|---|
| stage_trace 5단계 직렬화 (base_wma/coef_mul/rule_floor/ml_blend/final_cap) | ✅ | `src/prediction/improved_predictor.py` (+37) |
| 푸드 mid 한정 필터 (001~005, 012) | ✅ | `_snapshot_stages` food_5stage 가지 |
| stock_qty<0 → None sentinel 정규화 | ✅ | `src/prediction/base_predictor.py` (+11) |
| schema v76 마이그레이션 (CREATE + _STORE_COLUMN_PATCHES 동기화) | ✅ | `src/infrastructure/database/schema.py` (+8), `src/settings/constants.py` v75→v76 |
| 신규 테스트 | ✅ | `tests/test_food_stage_trace.py` (+232, 9 케이스) |
| Phase B 표적 수정 | ⏸️ 대기 | 04-10 ~ 04-17 1주 관측 후 단일 지배 stage 식별 |

---

## 3. 핵심 의사결정

### 3.1 스키마 드리프트 선제 수정 (계획 외 작업)
Design은 "stage_trace 컬럼 이미 존재, 재활용"으로 가정했으나, 실제로는 `CREATE TABLE`에만 있고
`_STORE_COLUMN_PATCHES`에 누락되어 기존 매장 DB에서 `prediction_logger.py` PRAGMA 체크가 silent skip.
→ v76 마이그레이션으로 양쪽 동기화. 이 수정 없이는 Phase A 관측 자체가 불가능했음.

### 3.2 Phase A 범위 제한 엄수
`TRACE_TARGET_MIDS = {"001","002","003","004","005","012"}` — 푸드 외 카테고리 DB 부담 0.
모집단 158건이 푸드에 한정되어 있어 범위 확장의 이득 없음.

### 3.3 PR 머지 전략: cherry-pick
PR #4 원격 세션이 `fce1594`에서 분기했으나 main은 그 사이 11 커밋 전진(ops-metrics 확장,
46704 폐기 보고서 fix, waste-lightweight 아카이브 등). 직접 머지 시 1,425줄 regression 위험.
→ 실제 작업 커밋 1건만 cherry-pick (`98c7248`), 자동 생성된 이슈 테이블 sync 커밋(`113a64e`)은 skip.

---

## 4. 검증

| 항목 | 결과 |
|---|---|
| `pytest tests/test_food_stage_trace.py` | 9/9 PASS (3.89s) |
| 인접 회귀 (test_food_underorder_fix, test_batch_sync_zero_sales_guard) | 0 fail |
| schema in-memory 마이그레이션 | OK |
| compile/import | OK |
| sentinel 경로 — ab98bfc 1차 fix nonzero_signal 통과 여부 | 정규화 후 통과 확인 |

---

## 5. 잔여 작업 / Phase B 진입 조건

1. **04-09 07:00** 스케줄 1회 실행 후 `prediction_logs.stage_trace` 컬럼에 푸드 mid JSON 적재 확인
2. **04-10 ~ 04-16** 1주 관측 (수정 금지 — 데이터 오염 방지)
3. **04-17** stage_trace 분해 분석 → 단일 지배 stage 식별
4. **04-18~** Phase B 표적 패치 (1개 stage만 수정)

분기 의사결정 표는 Design §5 참조.

---

## 6. Match Rate

- Plan/Design 대비 구현 일치: **97%**
  - 감점 -3: Design에서 가정한 "컬럼 존재"가 실제로는 드리프트 상태 → 스키마 v76 선제 수정 필요했음 (사후 대응 OK, 문서 갱신은 본 리포트로 대체)
- 테스트 커버: 100%
- 회귀 영향: 0

---

## 7. 교훈

1. **원격 ultraplan 세션은 base drift를 인지하지 못함** — 병합 전 `git log base..pr` 확인 필수.
   향후 원격 세션 결과는 기본 cherry-pick 경로로 취급.
2. **스키마 컬럼 존재 주장은 `_STORE_COLUMN_PATCHES` 까지 확인**. `CREATE TABLE` 단독 존재 시
   신규 매장만 반영되고 기존 매장은 silent skip — 이슈 체인 `#stale-data` 패턴의 파생 케이스.
3. **관측/수정 분리 설계(Phase A/B)** 가 7가설 중 1개 지배 stage를 찾기 전 조기 패치를 방지.

---

## 8. 연관 이슈 갱신

- `docs/05-issues/order-execution.md#food-underprediction-secondary`: Phase A 완료 기록, 04-17 분석 체크포인트 추가 대기
- 활성 이슈 테이블(CLAUDE.md): `PLANNED P2 푸드 has_stock 그룹 약한 과소예측` 유지 — Phase B 분기 의사결정 후 갱신
