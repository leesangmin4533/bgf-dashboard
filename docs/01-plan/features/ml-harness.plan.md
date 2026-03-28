# ML 하네스: 성능 모니터링 + 자동 가중치 조정

**Feature**: ml-harness
**Created**: 2026-03-28
**Status**: Plan
**Priority**: High

---

## 1. 배경 및 목적

### 문제 정의

현재 ML 앙상블 가중치(ML_MAX_WEIGHT)가 하드코딩되어 있어:
- food_group=0.15, perishable=0.15, alcohol/tobacco/general=0.25
- MAE 변동에 따른 자동 조정 없음
- ML 성능 악화 시 감지/알림 메커니즘 없음
- Rule vs ML 어느 쪽이 더 정확한지 일상적 확인 불가

### 전제 조건 (2026-03-28 완료)

- prediction_logs에 `rule_order_qty`, `ml_order_qty`, `ml_weight_used` 컬럼 존재 (v55)
- 46513/46704 `stock_source` 컬럼 누락 버그 수정 완료 → 3개 매장 모두 ML 컬럼 저장 정상화
- AccuracyTracker에 `get_rule_vs_ml_accuracy()`, `get_ml_weight_effectiveness()` 이미 구현
- model_meta.json에 그룹별 MAE 저장 중 (일별 증분학습 23:45, 주간 전체 Sun 03:00)

### 데이터 축적 상태 (계획 시점)

| 매장 | rule_order_qty 저장 시작 | 예상 충분 시점 |
|------|------------------------|---------------|
| 46513 | 2026-03-28 (수정 후) | 2026-04-11 (14일) |
| 46704 | 2026-03-28 (수정 후) | 2026-04-11 (14일) |
| 47863 | 2026-03-10 (기존 정상) | 즉시 사용 가능 (18일+) |

---

## 2. 목표

### 핵심 목표 (Must Have)

1. **ML 성능 모니터링 자동화**
   - 일별 Rule MAE vs Blended MAE 추이 추적
   - 카테고리 그룹별(5개) ML 기여도 측정
   - ML 가중치 구간별 효과성 분석

2. **보수적 자동 가중치 조정**
   - 14일 실측 MAE 기반 ML_MAX_WEIGHT 자동 갱��
   - ±0.02 step, 안전 범위 내에서만 (food: 0.05~0.30, general: 0.10~0.40)
   - ML이 Rule보다 나빠지면 자동 롤백 (가중치 하향)
   - FoodWasteRateCalibrator와 동일한 보수적 패턴

3. **이상 감지 + 카카오 알림**
   - ML MAE가 Rule MAE 대비 10%+ 악화 시 경고
   - 가중치 자동 조정 이력 일일 리포트 포함
   - 롤백 발생 시 즉시 알림

4. **대시보드 웹 UI (ML 성능 탭)**
   - 기존 Flask 대시보드에 탭 추가
   - Rule vs ML MAE 일별 차트
   - 카테고리별 가중치 현황 + 조정 이력
   - 가중치 구간별 효과성 히트맵

### 비목표 (Out of Scope)

- ML 모델 자체 변경 (RF/GB 앙상블 유지)
- A/B 테스트 인프라
- 피처 자동 선택/제거
- 실시간 가중치 변경 (일 1회 배치 조정)

---

## 3. 구현 범위

### Phase A: ML 성능 분석 서비스 (Core)

| 구성요소 | 파일 | 역할 |
|---------|------|------|
| MLPerformanceAnalyzer | `src/analysis/ml_performance_analyzer.py` | Rule vs ML MAE 일별/그룹별 분석 |
| MLWeightCalibrator | `src/prediction/ml_weight_calibrator.py` | 보수적 자동 가중치 조정 |
| ml_weight_history (DB) | `schema.py` store DB | 가중치 조정 이력 테이블 |

### Phase B: 스케줄러 통합 + 알림

| 구성요소 | 파일 | 역할 |
|---------|------|------|
| daily_job.py Phase 1.59 | `src/scheduler/daily_job.py` | 일별 ML 성능 분석 + 가중치 조정 |
| KakaoNotifier 확장 | `src/notification/kakao_notifier.py` | ML 섹션 추가 |

### Phase C: 대시보드 UI

| 구성요소 | 파일 | 역할 |
|---------|------|------|
| api_ml_performance.py | `src/web/routes/api_ml_performance.py` | REST API (5개 엔드포인트) |
| ml_performance.html | `src/web/templates/ml_performance.html` | ML 성능 탭 UI |

---

## 4. 핵심 알고리즘: 보수적 가중치 조정

```
매일 Phase 1.59 실행:

1. 14일 prediction_logs에서 Rule MAE vs Blended MAE 계산 (그룹별)
2. 각 그룹에 대해:
   a. ml_contribution = (rule_mae - blended_mae) / rule_mae
   b. IF ml_contribution > 0.03 (ML이 3%+ 개선):
        → ML_MAX_WEIGHT += 0.02 (상향, 상한 이내)
   c. ELIF ml_contribution < -0.02 (ML이 2%+ 악화):
        → ML_MAX_WEIGHT -= 0.02 (하향, 하한 이내)
        → 카카오 경고 알림
   d. ELSE:
        → 유지 (불감대)
3. 변경 시 ml_weight_history에 이력 기록
4. improved_predictor.py ML_MAX_WEIGHT 딕셔너리 DB 오버라이드
```

### 안전 범위

| 그룹 | 현재 값 | 하한 | 상한 | 비고 |
|------|--------|------|------|------|
| food_group | 0.15 | 0.05 | 0.30 | 유통기한 짧아 보수적 |
| perishable_group | 0.15 | 0.05 | 0.30 | food과 유사 |
| alcohol_group | 0.25 | 0.10 | 0.40 | 요일 패턴 뚜렷 |
| tobacco_group | 0.25 | 0.10 | 0.40 | 안정 수요 |
| general_group | 0.25 | 0.10 | 0.40 | 가장 다양 |

### 보호 장치

- **최소 데이터**: 14일 이상 rule_order_qty 데이터 있어야 조정 시작
- **불감대**: ml_contribution -0.02 ~ +0.03 구간은 변경 안 함
- **스텝 제한**: 1회 조정 ±0.02, 하루 1회만
- **롤백**: 3일 연속 Blended MAE > Rule MAE이면 이전 값으로 복원
- **토글**: ML_WEIGHT_AUTO_CALIBRATION_ENABLED (기본 True)

---

## 5. DB 스키마

### ml_weight_history (store DB, 신규)

```sql
CREATE TABLE IF NOT EXISTS ml_weight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    calibration_date TEXT NOT NULL,
    category_group TEXT NOT NULL,       -- food_group, alcohol_group, ...
    previous_weight REAL NOT NULL,
    new_weight REAL NOT NULL,
    rule_mae REAL,
    blended_mae REAL,
    ml_contribution REAL,               -- (rule-blended)/rule
    action TEXT NOT NULL,               -- 'increase', 'decrease', 'rollback', 'hold'
    reason TEXT,
    sample_count INTEGER,               -- 분석에 사용된 레코드 수
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(store_id, calibration_date, category_group)
);
```

### ml_weight_overrides (store DB, 신규)

```sql
CREATE TABLE IF NOT EXISTS ml_weight_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    category_group TEXT NOT NULL,
    max_weight REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(store_id, category_group)
);
```

---

## 6. API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/ml/performance?days=14` | Rule vs ML MAE 일별 추이 |
| GET | `/api/ml/contribution` | 그룹별 ML 기여도 현황 |
| GET | `/api/ml/weight-history?days=30` | 가중치 조정 이력 |
| GET | `/api/ml/effectiveness` | 가중치 구간별 효과성 |
| GET | `/api/ml/current-weights` | 현재 적용 중인 가중치 (하드코딩 vs DB 오버라이드) |

---

## 7. 대시보드 UI 구성

### ML 성능 탭 레이아웃

```
┌─────────────────────────────────────────────────┐
│ [홈] [발주] [예측] [ML 성능] [보고서] [설정]      │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌── 요약 카드 ──────────────────────────────┐  │
│  │ Rule MAE: 1.23  │  Blended MAE: 1.15      │  │
│  │ ML 기여도: +6.5% │  조정 횟수(30d): 3회    │  │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  ┌── Rule vs ML MAE 일별 차트 ───────────────┐  │
│  │  (라인차트: Rule=빨강, Blended=파랑)       │  │
│  │  X축: 날짜, Y축: MAE                       │  │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  ┌── 카테고리별 가중치 현황 ─────────────────┐  │
│  │ food_group    [████░░] 0.15 (범위 0.05~0.30) │
│  │ alcohol_group [██████░] 0.25 (범위 0.10~0.40) │
│  │ ...                                        │  │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  ┌── 가중치 조정 이력 테이블 ────────────────┐  │
│  │ 날짜 | 그룹 | 이전→신규 | 사유 | MAE변화  │  │
│  └────────────────────────────────────────────┘  │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## 8. 실행 타이밍

```
daily_job.py 파이프라인:
  Phase 1.56  Food Waste Rate Calibration
  Phase 1.57  Bayesian Parameter Optimization
  Phase 1.58  Croston Parameter Optimization
  Phase 1.59  ★ ML Weight Calibration (NEW)  ← 여기
  Phase 1.6   Prediction Actual Sales Update
```

---

## 9. 기존 코드 활용

| 기존 모듈 | 활용 방법 |
|----------|---------|
| AccuracyTracker.get_rule_vs_ml_accuracy() | 그대로 사용 (이미 구현) |
| AccuracyTracker.get_ml_weight_effectiveness() | 그대로 사용 (이미 구현) |
| FoodWasteRateCalibrator 패턴 | MLWeightCalibrator 설계 참조 (보수적 조정, 불감대, 안전범위) |
| improved_predictor._get_ml_weight() | DB 오버라이드 조회 추가 (ML_MAX_WEIGHT 딕셔너리 대체) |
| KakaoNotifier | ML 섹션 추가 |
| Flask Blueprint 패턴 | api_ml_performance.py 신규 Blueprint |

---

## 10. 의존성

```
ml-harness
├── prediction_logs ML 컬럼 (v55) ✅ 완료
├── stock_source 컬럼 버그 수정 ✅ 완료 (2026-03-28)
├── AccuracyTracker Rule vs ML API ✅ 이미 구현
├── model_meta.json MAE 저장 ✅ 동작 중
└── 14일 데이터 축적 ⏳ 2026-04-11 예상
    └── 47863은 즉시 테스트 가능 (18일+ 데이터)
```

---

## 11. 테스트 계획

| 영역 | 테스트 | 예상 건수 |
|------|--------|---------|
| MLWeightCalibrator | 상향/하향/유지/롤백 로직 | 12개 |
| MLPerformanceAnalyzer | MAE 계산, 그룹 분류 | 8개 |
| API 엔드포인트 | 5개 API 정상 응답 | 5개 |
| 스케줄러 통합 | Phase 1.59 실행 | 3개 |
| 안전장치 | 범위 초과, 데이터 부족, 토글 | 6개 |
| **합계** | | **~34개** |

---

## 12. 구현 순서

1. **Phase A** (Core): MLWeightCalibrator + DB 테이블 + improved_predictor 연동
2. **Phase B** (Integration): daily_job Phase 1.59 + 카카오 알림
3. **Phase C** (UI): API + 대시보드 탭

---

## 13. 리스크 및 완화

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 가중치 과도 조정 | 예측 품질 저하 | ±0.02 step + 안전범위 + 3일 롤백 |
| 데이터 부족 | 잘못된 MAE 비교 | 14일 최소 데이터 요구 |
| DB 오버라이드 실패 | 하드코딩 값으로 폴백 | try/except + 폴백 패턴 |
| 카테고리 그룹 변경 | 이력 불연속 | category_group 키 유지 |

---

## 14. 성공 기준

- [ ] 3개 매장 모두 ML 가중치 자동 조정 동작
- [ ] 대시보드에서 Rule vs ML MAE 추이 확인 가능
- [ ] 가중치 조정 이력 30일 이상 축적
- [ ] ML 악화 시 카카오 알림 발송 확인
- [ ] 기존 3700+ 테스트 회귀 없음
