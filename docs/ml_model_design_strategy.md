# BGF 프로젝트 ML 모델 설계 전략

**작성일**: 2026-02-06
**목적**: 편의점 자동 발주 시스템에 최적화된 ML 모델 설계
**현재 상태**: 규칙 기반 예측기 (ImprovedPredictor v9)

---

## 1. 현재 시스템 분석

### 1.1 ImprovedPredictor (규칙 기반)

```python
발주량 = 일평균 × 요일계수 × 리드타임 + 안전재고 - 현재재고 - 미입고
```

**특징:**
- ✅ 비즈니스 규칙 명확히 반영
- ✅ 해석 가능성 높음 (점주가 이해 가능)
- ✅ 카테고리별 특수 로직 (유통기한, 담배 보루 패턴 등)
- ✅ 빠른 실행 속도
- ❌ 복잡한 패턴 학습 불가
- ❌ 외부 요인(날씨, 이벤트) 반영 제한적
- ❌ 수동 파라미터 튜닝 필요

---

## 2. ML 모델 선택 기준

### 2.1 BGF 프로젝트 특성

| 특성 | 설명 | ML 영향 |
|------|------|---------|
| 데이터 규모 | 2개 점포, 6개월, 약 4,000개 상품 | 중간 규모 |
| 시계열 패턴 | 요일별, 계절별, 이벤트별 변동 | 시계열 모델 필요 |
| 카테고리 다양성 | 푸드(유통기한 1~3일), 담배, 주류, 생필품 등 | 카테고리별 모델 |
| 해석 필요성 | 점주가 발주 이유를 이해해야 함 | 높은 해석성 필요 |
| 실행 환경 | 로컬 PC, 매일 07:00 자동 실행 | 경량 모델 |
| 정확도 요구 | 품절/과다재고 최소화 | 높은 정확도 |
| 비즈니스 제약 | 유통기한, 발주 단위, 점포 규모 | 규칙 통합 필요 |

### 2.2 모델 선택 매트릭스

| 모델 유형 | 정확도 | 해석성 | 속도 | 데이터 요구량 | 비즈니스 규칙 | 적합도 |
|----------|--------|--------|------|---------------|---------------|--------|
| 규칙 기반 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 75% |
| ARIMA/Prophet | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | 70% |
| LightGBM/XGBoost | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | **85%** |
| LSTM/GRU | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐ | ⭐⭐ | 60% |
| Transformer | ⭐⭐⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐ | 40% |

---

## 3. 권장 전략: 하이브리드 모델

### 3.1 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                    하이브리드 예측 시스템                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  [규칙 기반 레이어]               [ML 레이어]                │
│  ├─ 비즈니스 규칙                 ├─ LightGBM 예측기        │
│  ├─ 안전재고 계산                 ├─ 시계열 피처            │
│  ├─ 유통기한 처리                 ├─ 외부 요인 학습        │
│  └─ 발주 단위 변환                └─ 패턴 학습             │
│                                                               │
│                    ↓ 통합 ↓                                  │
│                                                               │
│              [앙상블 의사결정 레이어]                         │
│              - 규칙 가중치: 40%                              │
│              - ML 가중치: 60%                                │
│              - 신뢰도 기반 동적 조정                         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Phase별 구현 로드맵

### Phase 1: 현재 (규칙 기반 최적화)

**목표**: 기존 ImprovedPredictor 개선

**작업:**
1. ✅ 멀티 점포 지원
2. 점포별 파라미터 자동 튜닝 (Bayesian Optimization)
3. 카테고리별 파라미터 최적화
4. 성과 추적 시스템 강화

**예상 정확도**: 75~80%

---

### Phase 2: 통계 모델 추가 (3~4주)

**목표**: 시계열 패턴 학습

**모델**: Prophet or ARIMA

**장점:**
- 요일/계절 패턴 자동 학습
- 트렌드 감지
- 이상치 처리
- 해석 가능성 유지

**구현:**

```python
from prophet import Prophet

class TimeSeriesPredictor:
    """시계열 예측기 (Prophet 기반)"""

    def __init__(self, store_id: str, item_cd: str):
        self.store_id = store_id
        self.item_cd = item_cd
        self.model = None

    def train(self, history_days: int = 180):
        """과거 데이터로 모델 학습"""
        df = self._get_sales_history(history_days)

        # Prophet 데이터 형식 변환
        df_prophet = pd.DataFrame({
            'ds': pd.to_datetime(df['sales_date']),
            'y': df['sale_qty']
        })

        # 요일 효과 추가
        self.model = Prophet(
            weekly_seasonality=True,
            yearly_seasonality=True,
            changepoint_prior_scale=0.05
        )

        # 외부 변수 추가
        df_prophet['holiday'] = df['is_holiday']
        df_prophet['weather'] = df['temperature']

        self.model.fit(df_prophet)

    def predict(self, days_ahead: int = 3) -> float:
        """N일 후 예측"""
        future = self.model.make_future_dataframe(periods=days_ahead)
        forecast = self.model.predict(future)

        return forecast['yhat'].iloc[-1]
```

**예상 정확도**: 80~85%

---

### Phase 3: LightGBM 앙상블 (4~6주)

**목표**: 복잡한 패턴 학습 + 비즈니스 규칙 통합

**모델**: LightGBM (추천 ⭐)

**이유:**
- ✅ 빠른 학습/예측 속도
- ✅ 메모리 효율적
- ✅ 카테고리 변수 자동 처리
- ✅ Feature Importance로 해석 가능
- ✅ 과적합 방지 기능 강력
- ✅ 로컬 PC에서도 실행 가능

**피처 설계:**

```python
# 1. 시계열 피처
- lag_1, lag_7, lag_14, lag_30  # 1일 전, 7일 전, ...
- rolling_mean_7, rolling_mean_30  # 이동평균
- ewm_7, ewm_30  # 지수이동평균

# 2. 요일/계절 피처
- weekday (0~6)
- is_weekend
- day_of_month
- month
- season (봄/여름/가을/겨울)

# 3. 점포 피처
- store_id (categorical)
- store_size (large/medium/small)
- store_location_type (역세권/주택가/대학가)

# 4. 상품 피처
- mid_cd (중분류)
- has_expiry_date (유통기한 유무)
- expiry_days (유통기한 일수)
- is_promotion (행사 여부)
- promotion_type (1+1, 2+1)

# 5. 외부 요인
- is_holiday
- temperature
- rainfall
- special_event (축제, 지역행사)

# 6. 재고 피처
- current_stock
- pending_qty (미입고)
- stockout_days_30 (최근 30일 품절 일수)

# 7. 판매 패턴
- daily_avg_7, daily_avg_14, daily_avg_30
- sell_day_ratio_30 (판매일 비율)
- max_sale_30, min_sale_30
- sale_volatility (판매량 변동성)
```

**LightGBM 구현:**

```python
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit

class LGBMOrderPredictor:
    """LightGBM 기반 발주량 예측기"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.model = None
        self.feature_names = []

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """피처 생성"""

        # 시계열 피처
        df['lag_1'] = df.groupby('item_cd')['sale_qty'].shift(1)
        df['lag_7'] = df.groupby('item_cd')['sale_qty'].shift(7)
        df['rolling_mean_7'] = df.groupby('item_cd')['sale_qty'].rolling(7).mean().reset_index(0, drop=True)
        df['rolling_mean_30'] = df.groupby('item_cd')['sale_qty'].rolling(30).mean().reset_index(0, drop=True)

        # 요일 피처
        df['weekday'] = pd.to_datetime(df['sales_date']).dt.dayofweek
        df['is_weekend'] = df['weekday'].isin([5, 6]).astype(int)
        df['month'] = pd.to_datetime(df['sales_date']).dt.month

        # 카테고리 피처
        df['mid_cd'] = df['mid_cd'].astype('category')
        df['store_id'] = df['store_id'].astype('category')

        return df

    def train(self, train_data: pd.DataFrame):
        """모델 학습"""

        # 피처 준비
        train_data = self.prepare_features(train_data)

        # 타겟 변수
        y = train_data['sale_qty']

        # 피처 선택
        feature_cols = [
            'lag_1', 'lag_7', 'rolling_mean_7', 'rolling_mean_30',
            'weekday', 'is_weekend', 'month',
            'mid_cd', 'store_id',
            'current_stock', 'pending_qty',
            'daily_avg_7', 'sell_day_ratio_30'
        ]

        X = train_data[feature_cols]
        self.feature_names = feature_cols

        # Time Series Split (시계열 교차 검증)
        tscv = TimeSeriesSplit(n_splits=5)

        # LightGBM 파라미터
        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.9,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1,
            'seed': 42
        }

        # 학습
        train_set = lgb.Dataset(X, label=y)
        self.model = lgb.train(
            params,
            train_set,
            num_boost_round=100,
            valid_sets=[train_set],
            callbacks=[lgb.early_stopping(10)]
        )

    def predict(self, item_cd: str, date: str) -> float:
        """발주량 예측"""

        # 피처 생성
        features = self._get_features(item_cd, date)

        # 예측
        pred = self.model.predict(features)[0]

        # 음수 방지
        return max(0, pred)

    def get_feature_importance(self) -> pd.DataFrame:
        """피처 중요도 반환"""

        importance = self.model.feature_importance(importance_type='gain')

        df = pd.DataFrame({
            'feature': self.feature_names,
            'importance': importance
        }).sort_values('importance', ascending=False)

        return df
```

**예상 정확도**: 85~90%

---

### Phase 4: 하이브리드 앙상블 (2주)

**목표**: 규칙 + ML 통합

**앙상블 전략:**

```python
class HybridPredictor:
    """하이브리드 예측기 (규칙 + ML 앙상블)"""

    def __init__(self, store_id: str):
        self.store_id = store_id

        # 규칙 기반 예측기
        self.rule_predictor = ImprovedPredictor(store_id)

        # ML 예측기
        self.ml_predictor = LGBMOrderPredictor(store_id)

        # 가중치 (신뢰도 기반 동적 조정)
        self.rule_weight = 0.4
        self.ml_weight = 0.6

    def predict(self, item_cd: str) -> PredictionResult:
        """하이브리드 예측"""

        # 1. 규칙 기반 예측
        rule_result = self.rule_predictor.predict(item_cd)
        rule_qty = rule_result.order_qty

        # 2. ML 예측
        ml_qty = self.ml_predictor.predict(item_cd, date=today)

        # 3. 신뢰도 계산
        confidence = self._calculate_confidence(item_cd)

        # 4. 동적 가중치 조정
        if confidence == "high":
            # 데이터 충분 → ML 더 신뢰
            w_rule, w_ml = 0.3, 0.7
        elif confidence == "medium":
            w_rule, w_ml = 0.4, 0.6
        else:  # low
            # 데이터 부족 → 규칙 더 신뢰
            w_rule, w_ml = 0.6, 0.4

        # 5. 가중 평균
        final_qty = w_rule * rule_qty + w_ml * ml_qty

        # 6. 비즈니스 규칙 적용
        final_qty = self._apply_business_rules(final_qty, item_cd)

        return PredictionResult(
            order_qty=int(final_qty),
            rule_qty=rule_qty,
            ml_qty=ml_qty,
            confidence=confidence,
            weights={'rule': w_rule, 'ml': w_ml}
        )

    def _calculate_confidence(self, item_cd: str) -> str:
        """신뢰도 계산 (데이터 품질 기반)"""

        # 최근 30일 판매 데이터
        history = self.get_sales_history(item_cd, days=30)

        # 판매일 수
        sell_days = len([h for h in history if h['sale_qty'] > 0])

        if sell_days >= 20:
            return "high"
        elif sell_days >= 10:
            return "medium"
        else:
            return "low"

    def _apply_business_rules(self, qty: float, item_cd: str) -> float:
        """비즈니스 규칙 적용"""

        # 유통기한 체크
        if self.has_expiry_date(item_cd):
            max_qty = self.get_max_qty_by_expiry(item_cd)
            qty = min(qty, max_qty)

        # 발주 단위 적용
        order_unit = self.get_order_unit(item_cd)
        qty = round(qty / order_unit) * order_unit

        # 최소/최대 발주량
        qty = max(1, min(qty, 99))

        return qty
```

**예상 정확도**: 90~95%

---

## 5. 카테고리별 모델 전략

### 5.1 푸드류 (유통기한 1~3일)

**특성:**
- 짧은 유통기한 → 과다재고 위험 높음
- 요일별 변동 큼 (주말 ↑)
- 폐기 최소화가 최우선

**권장 모델:**
- **규칙 기반 + 안전재고 보수적 설정**
- ML 예측은 보조 역할
- 폐기 위험 높으면 규칙 가중치 ↑

**가중치:** 규칙 60% + ML 40%

---

### 5.2 담배/전자담배

**특성:**
- 보루 단위 (10개, 20개) → 패턴 명확
- 소진 패턴 학습 가능
- 높은 회전율

**권장 모델:**
- **LightGBM 중심**
- 보루 소진 패턴 피처
- 최근 판매 추세 중시

**가중치:** 규칙 30% + ML 70%

---

### 5.3 주류 (맥주/소주)

**특성:**
- 요일 패턴 강함 (금/토 ↑↑)
- 계절성 (여름 ↑)
- 외부 요인 영향 큼 (날씨, 이벤트)

**권장 모델:**
- **Prophet + LightGBM**
- 요일/계절 피처 강화
- 외부 변수 통합 (온도, 강수량)

**가중치:** 규칙 35% + ML 65%

---

### 5.4 생필품/잡화

**특성:**
- 안정적 수요
- 트렌드 변동 거의 없음
- 품절 방지가 중요

**권장 모델:**
- **규칙 기반 중심**
- 단순 이동평균으로 충분
- ML은 과적합 위험

**가중치:** 규칙 70% + ML 30%

---

## 6. 성과 측정 (KPI)

### 6.1 정확도 지표

```python
# 1. RMSE (Root Mean Squared Error)
rmse = np.sqrt(mean_squared_error(y_true, y_pred))

# 2. MAE (Mean Absolute Error)
mae = mean_absolute_error(y_true, y_pred)

# 3. MAPE (Mean Absolute Percentage Error)
mape = np.mean(np.abs((y_true - y_pred) / y_true)) * 100

# 4. 적중률 (±20% 이내)
hit_rate = np.mean(np.abs(y_true - y_pred) / y_true <= 0.2) * 100
```

### 6.2 비즈니스 지표

```python
# 5. 품절률
stockout_rate = (품절 일수 / 전체 일수) * 100

# 6. 폐기율
waste_rate = (폐기 수량 / 입고 수량) * 100

# 7. 재고 회전율
inventory_turnover = 판매량 / 평균재고

# 8. 서비스 레벨
service_level = (1 - 품절률) * 100
```

### 6.3 목표 설정

| 지표 | 현재 | 목표 (6개월 후) |
|------|------|------------------|
| MAPE | 30% | 15% |
| 적중률 | 60% | 80% |
| 품절률 | 5% | 2% |
| 폐기율 | 3% | 1% |

---

## 7. 구현 우선순위

### 즉시 (1~2주)

1. ✅ 멀티 점포 지원 (Phase 1)
2. 점포별 파라미터 자동 튜닝
3. 성과 추적 대시보드

### 단기 (1~2개월)

4. Prophet 통합 (시계열 패턴)
5. 외부 변수 수집 (날씨, 이벤트)
6. A/B 테스트 프레임워크

### 중기 (3~6개월)

7. LightGBM 구현
8. 하이브리드 앙상블
9. 카테고리별 모델 최적화

---

## 8. 기술 스택

| 항목 | 추천 기술 |
|------|----------|
| 기본 예측 | ImprovedPredictor (규칙 기반) |
| 시계열 | Prophet or ARIMA |
| 머신러닝 | LightGBM |
| 피처 엔지니어링 | pandas, numpy |
| 실험 추적 | MLflow |
| 모델 서빙 | 로컬 파일 시스템 (pickle) |
| 모니터링 | Flask 대시보드 + SQLite |

---

## 9. 최종 권장사항

### 현재 단계에서는

**✅ 규칙 기반 최적화 우선**

이유:
1. 데이터 6개월 (ML 학습에 아직 부족)
2. 비즈니스 규칙이 명확함
3. 해석 가능성 중요
4. 빠른 성과 필요

### 3개월 후 검토

**점포가 5개 이상, 데이터 1년 이상 쌓이면:**
- LightGBM 도입
- 하이브리드 앙상블 전환

### 최종 목표 (1년 후)

```
규칙 기반 (30%) + LightGBM (70%) 하이브리드
↓
MAPE 15% 이하, 품절률 2% 이하, 폐기율 1% 이하
```

---

**작성자**: Claude
**검토**: ML 전문가, BGF 운영자
**다음 단계**: Phase 2 (규칙 기반 최적화) 착수
