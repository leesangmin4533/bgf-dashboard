# Plan: prediction-accuracy-regression-4cats

> 작성일: 2026-04-08
> 상태: Plan
> 이슈체인: docs/05-issues/prediction.md#예측-정확도-하락-4카테고리
> 진행 방식: 로컬 작성 → 사용자 검토 후 `/ultraplan` 비교/병합
> 보드: ACTIVE (큐 #1, harness 완료 후 진행)

---

## 1. 목적

자동 감지된 4개 카테고리(015/039/040/048)의 예측 정확도 하락 근본원인을 식별하고 표적 패치 설계.

| mid | 예상 카테고리 | 예상 Strategy | 관측치 |
|---|---|---|---|
| 015 | 과자/제과 | SnackConfectionStrategy | 7일 MAE 악화 |
| 039 | 음료 | BeverageStrategy | 7일 MAE 악화 |
| 040 | 미확인 (CLAUDE.md 표 외 — 확인 필요) | DefaultStrategy 가능성 | 7일 MAE 악화 |
| 048 | 음료 | BeverageStrategy | **+61%** 최대 악화 |

> **검증 필요**: 040 매핑 실제 확인. SnackConfection/Beverage 외 Strategy 영향 가능성.

## 2. 문제 정의

- **자동 감지일**: 2026-04-06 prediction_accuracy 모니터
- **신호**: 7일 MAE > 14일 평균 MAE × 임계 (mid 048은 +61%)
- **봉쇄**: 단일 가설 없음. 4 카테고리가 동일/상이 원인인지 불명.
- **선행**: food-underprediction-secondary Phase A의 stage_trace는 푸드(001~005, 012) 한정 → 본 4개 카테고리는 stage_trace 미적용

## 3. 산출물

1. **근본원인 가설 트리** (3~5개 후보)
2. **카테고리별 분리 분석** (4개가 동일 원인인지, 분기인지 확정)
3. **검증 쿼리** (prediction_logs + eval_outcomes 기반)
4. **stage_trace 확장 결정** (Phase A 푸드 한정 → 본 4 mid 추가 여부)
5. **표적 패치 후보 1~2개** + 회귀 위험 평가

## 4. 비범위

- **푸드 카테고리(001~005, 012)**: food-underprediction Phase B (04-17~)에서 별도 처리
- **묶음 가드(bundle-suspect)**: 별도 PAUSED 작업
- **ML 앙상블 가중치 조정**: Phase 2로 분리 (P2 ML is_payday 검증과 함께)

## 5. 가설 트리 (사전 후보)

```
[관측] 4 카테고리 7일 MAE 악화
    │
    ├─ H1. 외부 환경 변화 (날씨/공휴일/봉급일)
    │   └─ 4 카테고리 공통이라면 external_factors 변화 의심
    │
    ├─ H2. 데이터 수집 결손
    │   └─ collection_logs에서 4월 첫째 주 누락/중복 확인
    │
    ├─ H3. Strategy 로직 회귀 (최근 코드 변경)
    │   └─ git log -- src/domain/prediction/strategies/{snack_confection,beverage}.py
    │   └─ 04-01 ~ 04-07 사이 커밋이 회귀 원인일 가능성
    │
    ├─ H4. ML 앙상블 weight drift
    │   └─ ml_weight_used 컬럼 4월 평균 vs 3월 평균
    │
    ├─ H5. 계절계수 7그룹 경계 효과
    │   └─ 4월 진입 시 계절 그룹 전환이 비현실적 계수 발생?
    │
    └─ H6. site 발주 출처 contamination (8801043016049 케이스 영향)
        └─ order_source='site' 비중 4월에 증가했는지
```

> 본 Plan은 가설 **나열만**. 1순위 검증 가설은 Design 단계에서 데이터 확인 후 선택.

## 6. 검증 데이터 소스 (모두 로컬 DB로 가능, BGF 사이트 무관)

| 소스 | 윈도우 | 추출 |
|---|---|---|
| `prediction_logs` | 최근 30일 | bias 분포, ml_weight_used, stock_source |
| `eval_outcomes` | 최근 30일 | actual vs predicted, 카테고리별 |
| `external_factors` | 최근 60일 | 4월 vs 3월 비교 |
| `collection_logs` | 최근 30일 | 4 카테고리 수집 결손 |
| `git log` | 04-01 ~ 04-07 | strategy 파일 변경 이력 |

## 7. 단계별 분해

| Phase | 작업 | 산출 | 완료 조건 |
|---|---|---|---|
| **0** | 040 mid 매핑 확정 | 실제 카테고리/Strategy 식별 | DB products 조회 1건 |
| **1** | 4 mid 분리 vs 통합 분석 | "공통 원인" or "개별 원인" 결정 | bias 시계열 4 line 그래프 (텍스트 표) |
| **2** | 가설 H1~H6 우선순위 | top 1 가설 선택 + 기각 사유 | Design 문서 §1 |
| **3** | 표적 패치 설계 | 1~2개 패치 후보 + 회귀 위험 | Design 문서 §2 |
| **4** | stage_trace 확장 결정 | 본 4 mid 포함/제외 | constants.py TRACE_TARGET_MIDS 변경 여부 |
| **5** | 구현 + 회귀 테스트 | 패치 PR | tests 통과 + 7일 관측 |

## 8. 위험

| 위험 | 완화 |
|---|---|
| 4 mid가 서로 다른 원인 → 4개 패치 필요 | Phase 1에서 분리/통합 결정. 분리 시 본 작업을 4개 sub-feature로 분해 |
| 04-06 자동 감지가 false positive (단순 표본 변동) | Phase 1에서 통계적 유의성 확인 (t-test 또는 7일 vs 14일 z-score) |
| 푸드 Phase B 관측 기간(04-10~04-16)과 충돌 | 본 4 mid는 푸드 외 → 무관. 다만 stage_trace 확장 시 04-17 이후로 미룸 |
| 회귀 패치가 다른 카테고리 악화 | 카테고리별 Strategy 격리 패턴 활용. Strategy 단위 회귀 테스트 추가 |

## 9. 검증 지표 (성공 조건)

- 4 카테고리 7일 MAE가 14일 평균 ±10% 이내로 회복
- 회귀 0건 (다른 카테고리 MAE 악화 없음)
- 가설 트리에서 1~2개 가설로 좁혀지고 나머지는 데이터로 기각

## 10. 의존성

- **선행**: 040 mid 카테고리 확정 (Phase 0, 5분 작업)
- **무관**: food-underprediction Phase B (04-10~04-16) — 푸드 외이므로 충돌 0
- **참고**: ML is_payday DB 반영 검증(P2)이 진행 중이면 H4 가설 분석 시 데이터 공유

## 11. /ultraplan 비교 체크포인트

본 Plan은 **나열형**으로 작성. 사용자가 `/ultraplan` 실행 시 다음 강점 활용 권장:

1. **다중 가설 평행 탐색** — Plan §5의 H1~H6을 동시 검증 (cloud 30분 한도 내)
2. **카테고리별 시계열 패턴 비교** — text-based 분석으로 통합/분리 결정
3. **회귀 커밋 후보 자동 식별** — `git log` + diff 의미 분석
4. **/ultraplan 강점**: 4 카테고리를 4개 sub-task로 병렬 전개 가능

비교 후 충돌 시 ultraplan 우선, 본 Plan은 fallback baseline.

## 12. 롤백

- 패치 미배포: Plan 단계 → 영향 0
- 패치 배포 후 회귀: Strategy 클래스 단위로 git revert 가능 (격리 패턴 덕분)
