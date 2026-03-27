# prediction-accuracy-fix Completion Report

> **Status**: Complete
>
> **Project**: BGF Retail Auto-Order System
> **Version**: v1.0.0 (2026-02-26)
> **Analyst**: report-generator
> **Completion Date**: 2026-02-26
> **PDCA Cycle**: #1

---

## 1. Executive Summary

### 1.1 Feature Overview

| Item | Details |
|------|---------|
| **Feature** | prediction-accuracy-fix |
| **Type** | Dashboard Analytics & Metric Improvement |
| **Scope** | SMAPE/wMAPE 지표 추가, MAPE SQL 버그 수정, 대시보드 UI 개선 |
| **Owner** | Prediction Analytics Team |
| **Start Date** | 2026-02-26 |
| **Completion Date** | 2026-02-26 |
| **Duration** | 1 day |
| **Effort** | 4 files modified, 1 file created, 18 tests added |

### 1.2 Results Summary

```
┌────────────────────────────────────────────────┐
│  Completion Rate: 100%                         │
├────────────────────────────────────────────────┤
│  ✅ All Requirements:     4 / 4 implemented     │
│  ✅ All Deliverables:     5 / 5 complete        │
│  ✅ All Tests:           18 / 18 passing        │
│  ✅ Full Suite:        2312 / 2312 passing      │
│  ✅ Design Match:        100% (73 items)        │
│  ❌ Deferred Items:        1 / 1 (by design)    │
└────────────────────────────────────────────────┘
```

---

## 2. Related Documents

| Phase | Document | Status | Notes |
|-------|----------|--------|-------|
| **Plan** | [prediction-accuracy-fix.plan.md](../../01-plan/features/prediction-accuracy-fix.plan.md) | ✅ Approved | 3 문제점, 5 수정 계획 |
| **Design** | [prediction-accuracy-fix.design.md](../../02-design/features/prediction-accuracy-fix.design.md) | ✅ Approved | 10 구현 단계, 코드 상세 명세 |
| **Check** | [prediction-accuracy-fix.analysis.md](../../03-analysis/prediction-accuracy-fix.analysis.md) | ✅ Complete | 100% Match Rate, 73 항목 검증 |
| **Report** | Current document | ✅ Complete | Completion Report |

---

## 3. PDCA Cycle Summary

### 3.1 Plan Phase

**Goal**: 대시보드 예측정확도 MAPE 계산 불일치 해소 및 지표 개선

**근본 원인 분석**:
- 요약 카드 MAPE(149%) vs 일별 차트 MAPE(1.6%) → **93배 차이**
- prediction_logs의 **97.9%**가 actual=0 (매출 없는 상품)
- SQL `ELSE 0 END` 버그: actual=0을 MAPE=0으로 처리 → AVG 희석
- MAPE 자체가 소량 판매(0~3개) 편의점 상품에 부적합

**계획된 수정 (4건)**:
1. MAPE 계산 통일 (Python/SQL 간 actual=0 처리 일치)
2. SMAPE 지표 추가 (대칭 MAPE, actual=0 처리 가능)
3. wMAPE 지표 추가 (가중 MAPE, 대량판매 중심)
4. 대시보드 UI에 새 지표 반영 (요약카드/차트/테이블)

### 3.2 Design Phase

**설계 범위**: 10개 구현 단계, 5개 파일 대상

| 단계 | 내용 | 파일 |
|------|------|------|
| 1 | AccuracyMetrics에 smape/wmape 필드 추가 | tracker.py |
| 2 | calculate_metrics() SMAPE/wMAPE 계산 추가 | tracker.py |
| 3 | _empty_metrics() 수정 | tracker.py |
| 4 | get_daily_mape_trend() SQL 버그 수정 + SMAPE | tracker.py |
| 5 | get_worst/best_items() SQL 통일 + SMAPE | tracker.py |
| 6 | 테스트 18개 작성 | test_accuracy_tracker.py |
| 7 | API 응답에 smape/wmape 포함 | api_prediction.py |
| 8 | 프론트엔드 UI 개선 | prediction.js |
| 9 | HTML 테이블 헤더 수정 | index.html |
| 10 | NORMAL_ORDER 판정 개선 (선택) | eval_calibrator.py |

### 3.3 Do Phase (Implementation)

**수정된 파일 (4개)**:

| 파일 | 변경 유형 | 주요 내용 |
|------|----------|----------|
| `src/prediction/accuracy/tracker.py` | 수정 | SMAPE/wMAPE 계산, SQL MAPE 버그 수정 |
| `src/web/routes/api_prediction.py` | 수정 | API 응답에 smape/wmape 필드 추가 |
| `src/web/static/js/prediction.js` | 수정 | 요약카드/차트/테이블 UI 개선 |
| `src/web/templates/index.html` | 수정 | 카테고리 테이블 헤더 SMAPE 추가 |

**생성된 파일 (1개)**:

| 파일 | 유형 | 내용 |
|------|------|------|
| `tests/test_accuracy_tracker.py` | 신규 | 18개 테스트 (5 SMAPE + 3 wMAPE + 3 MAPE통일 + 3 API + 4 경계) |

### 3.4 Check Phase (Gap Analysis)

**결과**: Match Rate **100%**

| 단계 | 항목수 | 일치 | 변경 | 누락 | 추가 |
|------|:------:|:----:|:----:|:----:|:----:|
| 1. AccuracyMetrics 필드 | 5 | 5 | 0 | 0 | 0 |
| 2. calculate_metrics() | 12 | 12 | 0 | 0 | 0 |
| 3. _empty_metrics() | 2 | 2 | 0 | 0 | 0 |
| 4. daily_mape_trend SQL | 6 | 6 | 0 | 0 | 0 |
| 5. worst/best SQL | 8 | 8 | 0 | 0 | 0 |
| 6. 테스트 | 17 | 17 | 0 | 0 | 1 |
| 7. API 응답 | 5 | 5 | 0 | 0 | 0 |
| 8. 프론트엔드 | 15 | 13 | 2 | 0 | 0 |
| 9. HTML 헤더 | 3 | 3 | 0 | 0 | 0 |
| **합계** | **73** | **71** | **2** | **0** | **1** |

- **2건 변경**: 차트 borderColor 폴백 간소화, count 점선 스타일 `[2,2]` (시각적 차이만, 기능 동일)
- **1건 추가**: `test_smape_wmape_required_fields` 보너스 테스트

---

## 4. Technical Details

### 4.1 SMAPE (Symmetric MAPE) 계산

```
SMAPE = |pred - actual| / ((|pred| + |actual|) / 2) × 100

특성:
- actual=0, pred=0 → 0% (둘 다 0이면 정확)
- actual=0, pred=1 → 200% (명확한 과대)
- actual=1, pred=2 → 66.7% (MAPE 100%보다 합리적)
- 방향 대칭: pred↔actual 교환해도 동일
- 범위: 0% ~ 200%
```

### 4.2 wMAPE (Weighted MAPE) 계산

```
wMAPE = SUM(|pred - actual|) / SUM(actual) × 100

특성:
- 대량 판매 상품에 더 큰 가중치
- 소량 상품(actual=0~1)의 극단 MAPE 왜곡 해소
- SUM(actual)=0이면 wMAPE=0
```

### 4.3 SQL MAPE 버그 수정

```sql
-- 수정 전 (버그): actual=0이 MAPE=0으로 AVG 희석
AVG(CASE WHEN actual_qty > 0
    THEN ... ELSE 0 END) as mape    -- 97.9%가 0 → 평균 1.6%

-- 수정 후: actual=0은 NULL로 AVG에서 제외
AVG(CASE WHEN actual_qty > 0
    THEN ... END) as mape            -- actual>0만 → ~149%
```

적용 위치 3곳: `get_daily_mape_trend()`, `get_worst_items()`, `get_best_items()`

### 4.4 UI 변경 사항

| UI 요소 | 변경 전 | 변경 후 |
|---------|---------|---------|
| 요약 카드 서브텍스트 | `MAPE 149%` | `MAE 0.36개 \| SMAPE 42% \| N건` |
| 일별 차트 메인 라인 | MAPE (단일) | SMAPE(실선, fill) + MAPE(점선) |
| 카테고리 테이블 | 5열 (MAPE) | 6열 (SMAPE + MAPE) |
| Best/Worst 테이블 | MAPE 기준 | SMAPE + MAE 기준 |

---

## 5. Test Results

### 5.1 신규 테스트 (18개)

| 클래스 | 테스트 | 검증 항목 |
|--------|--------|----------|
| TestSmapeCalculation | test_smape_both_zero | actual=0, pred=0 → 0% |
| | test_smape_actual_zero_pred_one | actual=0, pred=1 → 200% |
| | test_smape_one_to_two | actual=1, pred=2 → 66.67% |
| | test_smape_symmetric | 방향 대칭성 검증 |
| | test_smape_perfect | pred=actual → 0% |
| TestWmapeCalculation | test_wmape_basic | 기본 wMAPE 계산 |
| | test_wmape_heavy_weight | 대량판매 가중치 지배 |
| | test_wmape_all_zero_actual | SUM(actual)=0 → 0% |
| TestMapeUnification | test_mape_excludes_zero_actual | actual=0 제외 검증 |
| | test_daily_mape_trend_excludes_zero | SQL actual=0 제외 |
| | test_worst_items_excludes_zero | worst SQL actual=0 제외 |
| TestApiResponseFields | test_qty_accuracy_has_smape_wmape | API smape/wmape 반환 |
| | test_accuracy_metrics_dataclass_fields | dataclass 필드 검증 |
| | test_daily_trend_has_smape | trend에 smape 키 존재 |
| TestEdgeCases | test_empty_predictions | 빈 데이터 → 0 |
| | test_all_actual_zero | 전부 actual=0 처리 |
| | test_mixed_zero_nonzero | 편의점 전형 혼합 데이터 |
| | test_smape_wmape_required_fields | 필수 필드 검증 |

### 5.2 전체 테스트 스위트

```
테스트 결과: 2312 passed (2294 기존 + 18 신규)
실패: 0
```

---

## 6. Backward Compatibility

| 항목 | 상태 | 설명 |
|------|:----:|------|
| AccuracyMetrics.mape 필드 | ✅ 유지 | 기존 코드 참조 영향 없음 |
| API 응답 기존 필드 | ✅ 유지 | smape/wmape는 추가 필드 |
| DB 스키마 | ✅ 변경 없음 | prediction_logs 동일 |
| eval_outcomes | ✅ 변경 없음 | NORMAL_ORDER 판정 미변경 |
| 프론트엔드 기존 기능 | ✅ 유지 | 표시 우선순위만 변경 |

---

## 7. Deferred Items

| # | 항목 | 이유 | 우선순위 |
|---|------|------|---------|
| 1 | NORMAL_ORDER 판정 개선 (리드타임 유예) | Plan에서 "우선순위 낮음" 명시, Design에서 "선택" 표기 | LOW |

**향후 고려**: PENDING outcome 추가 시 eval_outcomes SQL 필터 변경 필요, `days_since_order` 전달 로직 검증 필요

---

## 8. Lessons Learned

### 8.1 기술적 교훈

| # | 교훈 | 적용 |
|---|------|------|
| 1 | SQL `ELSE 0`과 `END`(NULL)의 AVG 차이는 극단적 | actual=0 비율이 높을수록 영향 심각 |
| 2 | MAPE는 소량 판매 도메인에 부적합 | 편의점(0~3개)에서는 SMAPE/wMAPE 병행 필수 |
| 3 | Python dataclass 필드 순서: default 없는 필드가 먼저 | smape/wmape에 default 값 제거로 해결 |
| 4 | 동일 지표의 Python/SQL 이중 구현은 불일치 위험 | SQL 로직도 테스트에 포함해야 함 |

### 8.2 프로세스 교훈

| # | 교훈 | 적용 |
|---|------|------|
| 1 | 지표 93배 차이는 계산 로직 차이로 발생 | 같은 지표가 여러 곳에 있으면 통일 검증 필수 |
| 2 | 편의점 도메인 특성(actual=0 대다수)을 지표 선택에 반영 | SMAPE를 기본 지표로 승격 |

---

## 9. Impact Assessment

### 9.1 사용자 영향

- 대시보드 예측정확도 탭의 **지표 신뢰도 대폭 향상**
- 93배 불일치 제거 → MAPE가 Python/SQL 간 동일 값
- SMAPE로 소량 판매 상품의 정확도를 합리적으로 파악 가능
- wMAPE로 매출 규모별 가중치 반영된 정확도 제공

### 9.2 시스템 영향

- 성능: SQL 쿼리 1개당 SMAPE 컬럼 1개 추가 (무시 가능)
- 메모리: AccuracyMetrics 인스턴스당 float 2개 추가 (무시 가능)
- DB: 변경 없음
- 호환성: 하위 호환 100% 유지

---

## 10. Conclusion

**prediction-accuracy-fix** PDCA 사이클이 성공적으로 완료되었습니다.

핵심 성과:
1. **MAPE 93배 불일치 해소**: SQL `ELSE 0` 버그 수정으로 Python/SQL 간 계산 통일
2. **SMAPE/wMAPE 도입**: 편의점 소량판매 도메인에 적합한 보조 지표 제공
3. **대시보드 UI 개선**: 요약카드, 차트, 카테고리 테이블, Best/Worst에 SMAPE 반영
4. **100% 설계 일치**: 73개 검증 항목 전체 일치 (2건 미세 시각 차이만)
5. **18개 테스트**: SMAPE/wMAPE/MAPE 통일/경계케이스 전체 커버

```
[Plan] ✅ → [Design] ✅ → [Do] ✅ → [Check] ✅ (100%) → [Report] ✅
```

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-26 | Initial completion report | report-generator |
