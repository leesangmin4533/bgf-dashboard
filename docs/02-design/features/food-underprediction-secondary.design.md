# Design: food-underprediction-secondary

> 작성일: 2026-04-08
> 상태: Design
> Plan: docs/01-plan/features/food-underprediction-secondary.plan.md
> 토론: data/discussions/20260408-food-underprediction-secondary/03-최종-리포트.md
> 이슈체인: order-execution.md#food-underprediction-secondary

---

## 1. 설계 개요

2-Phase 점진 수정 전략. 토론 결론: **"관측 먼저, 표적 수정 나중, 명백한 사각지대만 즉시 동반"**.

```
Phase A (관측 + 사각지대) ── 04-10 배포
   ├─ stage_trace 로깅 활성화 (푸드 한정, 5단계)
   └─ stock_qty<0 sentinel 정규화 (1~2줄)
        │
        │ 1주 데이터 수집 (수정 금지)
        ▼
Phase B (표적 패치) ── 04-17 분석 → 04-18~ 배포
   └─ stage_trace 분해로 단일 지배 stage 식별 후 1개만 수정
```

**Phase B 는 Phase A 데이터에 따라 분기**되므로, 본 Design은 **Phase A를 확정 설계**하고 **Phase B는 분기 의사결정 표**로 문서화한다.

### 차단점 (Step 0)
- 04-09 07:30 1차 수정(ab98bfc) 검증 통과 확인 후 Phase A 착수
- 1차 수정 실패 시 본 계획 일시 보류

---

## 2. Phase A-1: stage_trace 로깅 활성화

### 변경 대상
- `src/prediction/improved_predictor.py` — 단계별 값 수집/직렬화
- `src/db/repository.py` (또는 `prediction_log_repo`) — `stage_trace` 컬럼 저장 경로

### 데이터 모델
**기존 컬럼 재활용**: `prediction_logs.stage_trace TEXT NULL` (이미 존재) → JSON 문자열 저장.
DB schema 변경 없음 (v74 유지).

### JSON 스키마
```json
{
  "v": 1,
  "stages": {
    "base_wma":   {"value": 1.00, "days": 7, "imputed_count": 2, "method": "imputation_available"},
    "coef_mul":   {"value": 1.18, "weekday": 1.18, "holiday": 1.0, "weather": 1.0, "season": 1.0, "trend": 1.0},
    "rule_floor": {"value": 1.31, "rop": true, "safety_stock": 1.31},
    "ml_blend":   {"value": null, "weight": null, "active": false},
    "final_cap":  {"value": 1, "cap_applied": false, "diff_feedback": 0}
  }
}
```

### 적용 범위 (필터)
```python
TRACE_TARGET_MIDS = {"001", "002", "003", "004", "005", "012"}
if mid_cd in TRACE_TARGET_MIDS:
    stage_trace_dict = {...}
    save_with_stage_trace(...)
```

### 로깅 시점
각 stage 진입 직후/직전에 단순 dict 누적. 직렬화는 prediction_logs INSERT 직전 1회.

### 성능 영향
- 푸드 모집단: 4매장 × 약 200 푸드 상품 = 800 row/일
- JSON 평균 500 byte → 일 0.4 MB, 30일 12 MB
- INSERT 1회 + dict 누적 7회 → 단일 상품당 < 1ms 추가

### 회귀 방지
- TRACE_TARGET_MIDS 외 mid: 기존 경로 그대로 (stage_trace=NULL)
- 직렬화 실패 시 fallback: try/except → NULL 저장 + WARN 로그
- 신규 단위 테스트 파일: `tests/test_stage_trace_logging.py` (pre-existing 20 fail 격리)

---

## 3. Phase A-2: stock_qty<0 sentinel 정규화

### 변경 대상
`src/prediction/base_predictor.py:calculate_weighted_average` (1차 수정과 같은 함수)

### 변경 전 (현재)
```python
available = [(d, qty, stk) for d, qty, stk in sales_history
             if stk is not None and stk > 0]
stockout = [(d, qty, stk) for d, qty, stk in sales_history
            if stk is not None and stk == 0]
if include_none_as_stockout:
    none_days = [(d, qty, stk) for d, qty, stk in sales_history if stk is None]
    stockout = stockout + none_days
```

### 변경 후
```python
# stock_qty<0 (수집기 sentinel) → None 으로 정규화하여 imputation 진입
def _normalize_stk(stk):
    return None if (stk is not None and stk < 0) else stk

normalized = [(d, qty, _normalize_stk(stk)) for d, qty, stk in sales_history]

available = [(d, qty, stk) for d, qty, stk in normalized
             if stk is not None and stk > 0]
stockout = [(d, qty, stk) for d, qty, stk in normalized
            if stk is not None and stk == 0]
if include_none_as_stockout:
    none_days = [(d, qty, stk) for d, qty, stk in normalized if stk is None]
    stockout = stockout + none_days
sales_history = normalized  # 후속 imputation 분기에서 사용
```

### 영향 범위
- 4매장 14일치 stock_qty<0 row: 합계 97건 (46513=2, 46704=24, 47863=26, 49965=45)
- 영향 카테고리: 푸드 + 비푸드 모두 (단, fresh food만 None을 stockout으로 인식 → 비푸드는 효과 없음)
- 1차 수정과 동일 함수 내 보강 → 논리 정합

### 회귀 방지
- 정규화는 새 리스트 생성 (원본 sales_history mutation 없음)
- 신규 테스트 케이스 6개 추가:
  1. 모든 day stk=-1 + 일부 sale>0 → imputation 적용 확인
  2. 혼합 stk=-1, 0, 양수 → 음수만 None 처리
  3. 비푸드(072) stk=-1 → 기존 경로 유지
  4. stk=NULL 그대로 → 변경 없음
  5. 1차 수정(ab98bfc) all-stockout 케이스 회귀 없음 확인
  6. 정상 상품 stk>0 케이스 회귀 없음

---

## 4. Phase A 배포 절차

### 04-09 (Step 0 차단점)
- [ ] 07:30 예약 태스크 결과 확인 (`verify-food-underprediction-fix`)
- [ ] 1차 수정 효과 측정: has_stock 그룹 bias 변화 확인
- [ ] 차단 통과 시 Phase A 진행, 실패 시 본 Plan 일시 보류

### 04-09~04-10 (Step 1~4)
- [ ] `improved_predictor.py` stage_trace 로깅 코드 추가
- [ ] `base_predictor.py` sentinel 정규화 추가 (1차 수정 직후 위치)
- [ ] `tests/test_stage_trace_logging.py` 신규 테스트 6+케이스
- [ ] 신규 테스트만 선별 실행 + 푸드 관련 스모크 테스트
- [ ] syntax check 후 commit (분할 커밋: stage_trace / sentinel)
- [ ] scheduler 자동 재시작 확인 (auto-reload wrapper)

### 04-10~04-16 (Step 5: 관측 1주, 수정 금지)
- [ ] 매일 prediction_logs.stage_trace 카운트 확인 (NULL 비율 0%)
- [ ] sentinel 적용 97건 추적 (over-prediction 발생 여부)
- [ ] 회귀 모니터링: 푸드 외 카테고리 발주량 변화 없음 확인
- [ ] 일일 카톡 이상치 알림 모니터링

---

## 5. Phase B: 표적 패치 (분기 의사결정)

### 04-17 분석 (Step 6)
4매장 푸드 158건의 stage_trace 를 다음 SQL 로 분해:

```sql
SELECT
  json_extract(stage_trace, '$.stages.base_wma.value') AS s1,
  json_extract(stage_trace, '$.stages.coef_mul.value') AS s2,
  json_extract(stage_trace, '$.stages.rule_floor.value') AS s3,
  json_extract(stage_trace, '$.stages.ml_blend.value') AS s4,
  json_extract(stage_trace, '$.stages.final_cap.value') AS s5,
  predicted_qty, mid_cd, item_cd
FROM prediction_logs
WHERE prediction_date >= date('now','-7 day') AND mid_cd IN ('001','002','003','004','005','012')
```

각 stage 가 다음 stage 대비 **얼마나 값을 누르는지** 계산 → bias 기여도 정량 산출.

### 의사결정 표

| 식별 결과 | 표적 패치 |
|---|---|
| `base_wma` 가 가장 큰 음의 기여 | WMA 가중치 분포 조정 (최근일 weight 완화) |
| `coef_mul` 이 가장 큰 음의 기여 | mid_cd 별 weekday_coef floor 도입 (예: 0.9 하한) |
| `rule_floor` 가 음의 기여 (드문 케이스) | safety_stock 계산식 재검토 |
| `ml_blend` 가 음의 기여 | ml_weight 가중 정책 재조정 |
| `final_cap` (food_daily_cap) 발동 | cap 계산식 buffer 확대 |
| 분산 (단일 지배 stage 없음) | 상위 2개 stage 순차 패치 |

### 04-18~04-24 (Step 7~8)
- [ ] 단일 stage 표적 패치 PR (1건만)
- [ ] 1주 검증
- [ ] 잔존 bias > -0.10 시 2차 표적 패치

---

## 6. 검증 계획

### Match Rate 산정
- Phase A 완료 시점 (04-10): stage_trace NULL=0% + sentinel 회귀 0건 = 100%
- Phase B 완료 시점 (04-24): has_stock bias |값| ≤ 0.10 = 90% 통과 기준

### 측정 SQL
```sql
WITH pred AS (
  SELECT pl.item_cd, pl.predicted_qty,
    (SELECT COUNT(*) FROM daily_sales d WHERE d.item_cd=pl.item_cd
      AND d.sales_date>=date('2026-04-09','-7 day') AND d.sales_date<'2026-04-09'
      AND d.stock_qty>0) AS days_stock,
    (SELECT AVG(sale_qty) FROM daily_sales d WHERE d.item_cd=pl.item_cd
      AND d.sales_date>=date('2026-04-09','-7 day') AND d.sales_date<'2026-04-09') AS avg7
  FROM prediction_logs pl
  WHERE pl.prediction_date='2026-04-09' AND pl.mid_cd IN ('001','002','003','004','005','012')
)
SELECT
  CASE WHEN days_stock=0 THEN 'all_stockout' ELSE 'has_stock' END AS grp,
  COUNT(*) n, ROUND(AVG(predicted_qty - avg7),2) bias
FROM pred WHERE avg7 IS NOT NULL GROUP BY grp;
```

---

## 7. 1차 수정과의 충돌 매트릭스

| 변경 항목 | 1차수정과의 관계 | 위험 |
|---|---|---|
| stage_trace 로깅 | 예측값 불변 (관측만) | 🟢 무관 |
| sentinel 정규화 | 같은 함수 보강, 97건 분리측정 | 🟡 중간 |
| Phase B 표적 패치 | 1차수정 검증 완료 후 진행 | 🟢 격리 |

**원칙**: 04-09 07:30 검증 결과를 받기 전에는 sentinel 정규화도 보류 가능 (안전 마진).

---

## 8. 롤백 계획

| Phase | 롤백 트리거 | 절차 |
|---|---|---|
| A-1 (stage_trace) | INSERT 성능 저하 30% 초과 | TRACE_TARGET_MIDS = set() 로 즉시 비활성화 |
| A-2 (sentinel) | over-prediction 알림 5건/일 초과 | 정규화 라인 1줄 주석 처리 + 재배포 |
| B (표적 패치) | 단일 카테고리 발주량 30% 이상 변동 | git revert 단일 커밋 |

모든 롤백은 schema 변경 없이 코드만 되돌리므로 < 5분 가능.

---

## 9. 산출물

| 파일 | 변경 유형 |
|---|---|
| `src/prediction/improved_predictor.py` | stage_trace 수집/직렬화 |
| `src/prediction/base_predictor.py` | sentinel 정규화 (1~2줄) |
| `tests/test_stage_trace_logging.py` | 신규 (6+ 케이스) |
| `docs/05-issues/order-execution.md` | [PLANNED] → [OPEN] 전환, 시도 1 기록 |
| `CLAUDE.md` 활성 이슈 | 동기화 |

---

## 10. Out of Scope

- 수집기 측 stock_qty<0 근원 수정 (별도 PDCA 백로그)
- DiffFeedback 영향도 (별도 이슈 후보)
- food_daily_cap 재검토 (Phase B 결과에 따라 조건부)
- ML 학습 윈도우 변경 (별도 이슈)

## Issue-Chain
order-execution#food-underprediction-secondary
