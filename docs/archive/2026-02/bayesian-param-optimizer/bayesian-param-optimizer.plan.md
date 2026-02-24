# Plan: bayesian-param-optimizer

> BGF 리테일 자동 발주 시스템 - 베이지안 파라미터 자동 최적화

## 1. 개요

### 배경
현재 시스템에는 **~50개 이상의 튜닝 가능한 파라미터**가 3개 보정기(EvalCalibrator, FoodWasteRateCalibrator, DiffFeedback)에 분산되어 있다. 각 보정기는 독립적으로 동작하며, 파라미터 간 상호작용을 고려하지 않는다.

**기존 보정 시스템의 한계:**
1. **EvalCalibrator** (Phase 1.5): Pearson 상관계수 기반 — 가중치 3개만 보정, 다른 파라미터는 경험적 조정
2. **FoodWasteRateCalibrator** (Phase 1.56): 목표 폐기율 기반 — safety_days/gap_coefficient만 조정, 불감대(deadband) 내에서는 미동작
3. **DiffFeedback**: 패턴 기반 고정 감소율 — 적응적 학습 없음
4. **파라미터 간 교차 효과 미고려**: safety_days 변경이 폐기율에 미치는 영향, 가중치 변경이 품절율에 미치는 영향 등

### 목표
- **다목적 목적함수(Multi-Objective)** 기반 글로벌 파라미터 최적화
- 기존 보정기와의 **계층적 공존** (Bayesian = 상위 전략, 기존 보정기 = 일상 미세조정)
- 예측 정확도 향상 + 폐기율 감소 + 품절율 감소의 **파레토 최적** 탐색
- 매장별(store-specific) 최적화 지원

### 범위
| 포함 | 제외 |
|------|------|
| eval_params 17개 파라미터 최적화 | ML 모델 자체(feature_builder, sklearn) 재학습 |
| FOOD_EXPIRY_SAFETY_CONFIG 파라미터 | 카테고리별 Strategy 구조 변경 |
| 피드백 계수 (diff, waste cause) | 넥사크로 스크래핑 로직 변경 |
| 목적함수 설계 + A/B 평가 | 실시간 온라인 최적화 (배치만) |
| DB 기록 + 대시보드 API | 외부 최적화 서비스(SageMaker 등) 연동 |

## 2. 현재 상태 (As-Is)

### 2.1 파라미터 구조

#### A. eval_params (17개, EvalConfig 관리)
| 그룹 | 파라미터 | 현재값 | 범위 | max_delta |
|------|---------|--------|------|-----------|
| 가중치 | weight_daily_avg | 0.5876 | 0.2~0.55 | 0.05 |
| 가중치 | weight_sell_day_ratio | 0.2686 | 0.15~0.55 | 0.05 |
| 가중치 | weight_trend | 0.1438 | 0.1~0.45 | 0.05 |
| 노출 | exposure_urgent | 1.0 | 0.3~2.0 | 0.3 |
| 노출 | exposure_normal | 2.0 | 1.0~3.5 | 0.3 |
| 노출 | exposure_sufficient | 2.5 | 2.5~5.0 | 0.5 |
| 기간 | daily_avg_days | 14.0 | 7~30 | 7.0 |
| 임계 | stockout_freq_threshold | 0.25 | 0.05~0.25 | 0.03 |
| 보정 | target_accuracy | 0.6 | 0.4~0.85 | 0.05 |
| 보정 | calibration_decay | 0.7 | 0.3~1.0 | 0.1 |
| 보정 | calibration_reversion_rate | 0.1 | 0.0~0.3 | 0.05 |
| ... | (popularity, force_max 등 6개) | | | |

**제약**: weight_daily_avg + weight_sell_day_ratio + weight_trend = 1.0 (normalize_weights)

#### B. FOOD_EXPIRY_SAFETY_CONFIG (10개, food.py)
| 파라미터 | 현재값 | 역할 |
|---------|--------|------|
| ultra_short.safety_days | 0.5 | 당일폐기 안전재고일 |
| short.safety_days | 0.7 | 단기유통 안전재고일 |
| medium.safety_days | 1.0 | 중기유통 안전재고일 |
| long.safety_days | 1.5 | 장기유통 안전재고일 |
| very_long.safety_days | 2.0 | 초장기유통 안전재고일 |
| gap_coefficient (5그룹별) | 0.1~0.5 | 배송갭 계수 |

**관련 DB 오버라이드**: food_waste_calibration 테이블 (FoodWasteRateCalibrator 출력)

#### C. PREDICTION_PARAMS (prediction_config.py)
| 파라미터 | 현재값 | 역할 |
|---------|--------|------|
| moving_avg_days | 7 | WMA 기간 |
| intermittent_threshold | 0.3 | 간헐수요 판정 |
| holiday_coefficient | 1.3 | 휴일계수 |
| association_boost | 0.1 | 연관분석 부스트 |

#### D. 피드백 계수 (diff_feedback, waste_cause)
| 파라미터 | 현재값 | 역할 |
|---------|--------|------|
| removal_penalty_factor | 설정값 | 제거패턴 감소율 |
| OVER_ORDER multiplier | 0.75 | 과잉발주 피드백 |
| EXPIRY_MGMT multiplier | 0.85 | 유통기한 관리 피드백 |
| DEMAND_DROP multiplier | 0.80→1.0 | 수요감소 피드백(7일감쇄) |

### 2.2 기존 보정기 실행 시점 (daily_job.py)
```
Phase 1.5  : EvalCalibrator.calibrate()       — eval_params 가중치 3개 조정
Phase 1.55 : WasteCauseAnalyzer.run()          — 폐기원인 분류
Phase 1.56 : FoodWasteRateCalibrator.run()     — food safety_days/gap_coef 조정
Phase 1.57 : [NEW] BayesianParameterOptimizer  — 글로벌 최적화 (주 1회)
```

### 2.3 성과 지표 현황 (AccuracyTracker)
- `eval_outcomes`: 예측 vs 실제 비교 (MAPE, MAE, RMSE)
- `calibration_history`: 보정 이력
- `food_waste_calibration`: 폐기율 보정 이력
- `prediction_logs`: 상세 예측 로그

## 3. 목표 상태 (To-Be)

### 3.1 아키텍처
```
┌────────────────────────────────────────────────────────────────┐
│                    Daily Execution Pipeline                     │
├────────────────────────────────────────────────────────────────┤
│ Phase 1.5   EvalCalibrator      (daily, micro-adjust)          │
│ Phase 1.56  FoodWasteCalibrator (daily, target-based)          │
│                                                                │
│ Phase 1.57  BayesianOptimizer   (weekly, global search)        │
│             ├─ Objective: accuracy + waste + stockout + cost    │
│             ├─ Search: scikit-optimize (GP) or optuna (TPE)    │
│             ├─ Constraints: ParamSpec min/max + cross-param     │
│             ├─ Safety: max_delta per iteration, rollback        │
│             └─ Output: eval_params.json + DB log               │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 목적함수 설계 (Multi-Objective Weighted Loss)
```python
L(params) = alpha * accuracy_error    # 예측 정확도 (1 - accuracy_rate)
          + beta  * waste_rate_error   # 폐기율 (actual - target)
          + gamma * stockout_rate      # 품절율
          + delta * over_order_ratio   # 과잉발주 비율

# 기본 가중치 (구성 가능)
alpha = 0.35  # 정확도 중심
beta  = 0.30  # 폐기율 최소화
gamma = 0.25  # 품절 방지
delta = 0.10  # 과잉발주 최소화
```

### 3.3 최적화 전략
- **탐색 공간**: eval_params 12개 + FOOD safety 5개 + PREDICTION 4개 = **~21개 핵심 파라미터**
  - (피드백 계수는 Phase 2로 보류 — 독립 모듈이라 연동 복잡)
- **알고리즘**: Gaussian Process (scikit-optimize) 또는 TPE (optuna)
  - GP: 소규모 파라미터 공간에 적합, 탐색-활용 균형 우수
  - TPE: 대규모 공간에 확장성, conditional 파라미터 지원
- **실행 주기**: 주 1회 (일요일 23:00) — 충분한 데이터 누적 후 최적화
- **평가 기간**: 최근 7일 실적 데이터로 목적함수 계산
- **탐색 예산**: 회당 최대 30회 시행 (GP surrogate로 실제 예측 없이 시뮬레이션)

### 3.4 안전 장치
1. **max_delta 제약**: ParamSpec.max_delta 존재 — 한 번에 극단적 변경 방지
2. **롤백 메커니즘**: 최적화 적용 후 3일간 모니터링, 성과 하락 시 이전 값 복원
3. **최소 데이터 요건**: 7일 이상 eval_outcomes 데이터 필요 (MIN_EVAL_DAYS=7)
4. **제약 조건**: 가중치 합=1.0, safety_days 순서(ultra_short < short < medium < long)
5. **점진적 적용**: 최적값과 현재값의 중간값으로 적용 (damping_factor=0.5)
6. **수동 잠금**: 특정 파라미터를 최적화 대상에서 제외 가능 (locked 플래그)

## 4. 기술 설계

### 4.1 새로 생성할 파일
```
src/prediction/bayesian_optimizer.py              # 핵심 최적화 엔진
src/infrastructure/database/repos/
    bayesian_optimization_repo.py                 # DB 저장소
tests/test_bayesian_optimizer.py                  # 단위 테스트
```

### 4.2 수정할 파일
```
src/application/daily_job.py                      # Phase 1.57 추가
run_scheduler.py                                  # 주간 스케줄 추가
src/db/models.py                                  # DB 스키마 (새 테이블)
src/settings/constants.py                         # DB_SCHEMA_VERSION 업데이트
src/prediction/eval_config.py                     # locked 필드 추가
config/eval_params.default.json                   # locked 필드 추가
src/web/routes/api_prediction.py                  # 대시보드 API (선택)
```

### 4.3 DB 스키마 (bayesian_optimization_log)
```sql
CREATE TABLE IF NOT EXISTS bayesian_optimization_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    optimization_date TEXT NOT NULL,        -- 최적화 실행일
    iteration INTEGER NOT NULL,            -- 시행 번호 (1~30)

    -- 목적함수 결과
    objective_value REAL,                  -- 총 Loss
    accuracy_error REAL,
    waste_rate_error REAL,
    stockout_rate REAL,
    over_order_ratio REAL,

    -- 파라미터 스냅샷 (JSON)
    params_before TEXT,                    -- 최적화 전 파라미터 (JSON)
    params_after TEXT,                     -- 최적화 후 파라미터 (JSON)
    params_delta TEXT,                     -- 변경된 파라미터만 (JSON)

    -- 메타
    algorithm TEXT DEFAULT 'gp',           -- 'gp' or 'tpe'
    n_trials INTEGER,                      -- 총 시행 횟수
    best_trial INTEGER,                    -- 최적 시행 번호
    eval_period_start TEXT,                -- 평가 기간 시작
    eval_period_end TEXT,                  -- 평가 기간 종료
    applied INTEGER DEFAULT 0,             -- 적용 여부 (0/1)
    rolled_back INTEGER DEFAULT 0,         -- 롤백 여부 (0/1)
    rollback_reason TEXT,                  -- 롤백 사유

    created_at TEXT DEFAULT (datetime('now', 'localtime')),

    UNIQUE(store_id, optimization_date)
);

CREATE INDEX IF NOT EXISTS idx_bayesian_log_store_date
    ON bayesian_optimization_log(store_id, optimization_date);
```

### 4.4 BayesianParameterOptimizer 클래스 설계
```python
class BayesianParameterOptimizer:
    """베이지안 파라미터 자동 최적화 엔진

    weekly 실행:
    1. 최근 7일 eval_outcomes에서 성과 지표 수집
    2. 현재 파라미터를 초기점으로 GP/TPE 서로게이트 모델 구성
    3. 목적함수 최소화 탐색 (30회 시행)
    4. 최적 파라미터를 damping 적용하여 eval_params.json에 반영
    5. DB에 최적화 이력 저장
    """

    def __init__(self, store_id: str, config: EvalConfig = None):
        ...

    def optimize(self) -> Dict[str, Any]:
        """메인 최적화 실행"""
        ...

    def _collect_metrics(self, days: int = 7) -> Dict[str, float]:
        """최근 N일 성과 지표 수집"""
        ...

    def _build_search_space(self) -> List[Dimension]:
        """ParamSpec에서 탐색 공간 자동 구성"""
        ...

    def _objective(self, params: List[float]) -> float:
        """목적함수 계산 (히스토리 기반 시뮬레이션)"""
        ...

    def _apply_with_damping(self, best_params: Dict) -> Dict:
        """최적값을 damping 적용하여 반영"""
        ...

    def _check_rollback(self, days_after: int = 3) -> bool:
        """적용 후 성과 모니터링 + 롤백 판단"""
        ...
```

### 4.5 의존성
```
scikit-optimize >= 0.9.0    # Gaussian Process 기반 최적화
# 또는
optuna >= 3.0               # TPE 기반 최적화
```

**선택 기준**: scikit-optimize 우선 (API 단순, GP가 소규모 파라미터에 적합).
설치 실패 시 optuna 폴백.

## 5. 구현 순서

### Phase 1: 기반 구축 (핵심)
1. [ ] 의존성 설치 (scikit-optimize 또는 optuna)
2. [ ] DB 스키마 추가 (bayesian_optimization_log 테이블)
3. [ ] BayesianOptimizationRepository 구현
4. [ ] EvalConfig에 locked 필드 추가 (최적화 제외 파라미터)

### Phase 2: 핵심 엔진
5. [ ] BayesianParameterOptimizer 클래스 구현
   - 탐색 공간 자동 구성 (_build_search_space)
   - 목적함수 구현 (_objective)
   - 최적화 실행 (optimize)
6. [ ] 성과 지표 수집기 (_collect_metrics)
   - eval_outcomes에서 MAPE, accuracy_rate
   - daily_sales에서 waste_rate
   - order_tracking에서 stockout_rate, over_order_ratio

### Phase 3: 안전 장치
7. [ ] damping 적용 로직 (_apply_with_damping)
8. [ ] 롤백 메커니즘 (_check_rollback)
9. [ ] 파라미터 제약 조건 검증 (가중치 합=1, 순서 제약)

### Phase 4: 통합
10. [ ] daily_job.py Phase 1.57 추가 (주 1회 실행 조건)
11. [ ] run_scheduler.py 주간 스케줄 추가
12. [ ] CLI 수동 실행 지원 (--bayesian-optimize)

### Phase 5: 테스트 + 검증
13. [ ] 단위 테스트 (탐색 공간 생성, 목적함수, damping, 롤백)
14. [ ] 통합 테스트 (전체 파이프라인)
15. [ ] A/B 비교 (최적화 전후 1주일 성과 비교)

### Phase 6: 대시보드 (선택)
16. [ ] 웹 API: GET /api/prediction/bayesian-log
17. [ ] 최적화 이력 + 파라미터 변화 시각화

## 6. 검증 기준

### 성공 지표
| 지표 | 현재 | 목표 | 측정 방법 |
|------|------|------|----------|
| 예측 정확도 (accuracy@1) | ~60% | 65%+ | AccuracyTracker |
| 푸드 폐기율 | ~18% | 15%- | waste_verification_log |
| 품절율 | 미측정 | 5%- | order_tracking 기반 |
| 과잉발주율 | 미측정 | 20%- | order_diff 기반 |

### 테스트
```bash
# 1. 단위 테스트
python -m pytest tests/test_bayesian_optimizer.py -v

# 2. 수동 최적화 실행
python -c "
from src.prediction.bayesian_optimizer import BayesianParameterOptimizer
opt = BayesianParameterOptimizer(store_id='46513')
result = opt.optimize()
print(f'Best loss: {result[\"best_objective\"]:.4f}')
print(f'Params changed: {len(result[\"params_delta\"])}')
"

# 3. 롤백 테스트
python -c "
from src.prediction.bayesian_optimizer import BayesianParameterOptimizer
opt = BayesianParameterOptimizer(store_id='46513')
should_rollback = opt._check_rollback(days_after=3)
print(f'Rollback needed: {should_rollback}')
"
```

## 7. 리스크 및 완화

| 리스크 | 영향 | 완화 방법 |
|--------|------|----------|
| 과적합 (7일 데이터로 최적화) | 특수 상황에 맞춘 파라미터 | damping_factor=0.5, 롤백 모니터링 |
| 탐색 공간 폭발 (~21차원) | 수렴 느림, 지역 최적 | GP surrogate로 효율적 탐색, 30회 제한 |
| 기존 보정기와 충돌 | 파라미터 진동 | Bayesian=주간 전략, 기존=일일 미세조정, 실행순서 보장 |
| scikit-optimize 미설치 | 기능 비활성화 | optional import + graceful fallback |
| 폐기율/품절율 트레이드오프 | 한쪽 개선 시 다른쪽 악화 | Multi-Objective 가중치로 균형, 파레토 프론트 |

## 8. 타임라인

| 주차 | 작업 | 산출물 |
|------|------|--------|
| Week 1 | Phase 1~2 (기반 + 엔진) | bayesian_optimizer.py, DB 테이블 |
| Week 2 | Phase 3~4 (안전장치 + 통합) | daily_job 연동, 스케줄러 |
| Week 3 | Phase 5 (테스트 + A/B) | 테스트 코드, 성과 비교 |
| Week 4 | Phase 6 (대시보드) + 안정화 | 웹 UI, 문서화 |
