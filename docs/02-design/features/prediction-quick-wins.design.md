# Design: 예측 정확도 Quick Win 3가지

> Plan 참조: `docs/01-plan/features/prediction-quick-wins.plan.md`

## 1. QW-2: Stacking 임계값 조정 + 0행 원인 해결

### 현재 문제
- `stacking_predictor.py` L48: `MIN_TRAIN_SAMPLES = 200`
- 46513: 0행, 46704: 0행, 47863: 184행 → 전 매장 미달
- 학습 조건: `pl.predicted_qty > 0 AND pl.ml_order_qty > 0` (L179-180)
- **ml_order_qty가 NULL/0인 것이 0행의 원인** — ML 자체가 미작동이라 ml_order_qty가 기록 안 됨

### 해결: 2단계

**Step 1-A: 임계값 조정**
```python
# stacking_predictor.py L48
MIN_TRAIN_SAMPLES = 200  →  MIN_TRAIN_SAMPLES = 100
```

**Step 1-B: 학습 쿼리 완화 (ml_order_qty 조건 제거)**
```python
# stacking_predictor.py L178-180 (현재)
AND pl.ml_order_qty IS NOT NULL
AND pl.ml_order_qty > 0
AND pl.predicted_qty > 0

# 변경: ml_order_qty 조건 제거 (rule 예측만으로도 학습 가능)
AND pl.predicted_qty IS NOT NULL
AND pl.predicted_qty > 0
```

**근거**: Stacking은 `rule_pred`와 `ml_pred` 2개를 블렌딩하는데, ml_pred가 없어도 rule_pred만으로 메타 학습 가능. ml_pred=0으로 폴백하면 Stacking이 "rule에만 의존" 패턴을 학습함.

### 수정 파일

| 파일 | 라인 | 변경 |
|------|------|------|
| `src/analysis/stacking_predictor.py` | L48 | 200→100 |
| `src/analysis/stacking_predictor.py` | L178-180 | ml_order_qty 조건 완화 |

---

## 2. QW-1: Rolling Bias Multiplier

### 구조

```
prediction_logs.predicted_qty (7시 예측)
  + daily_sales.sale_qty (실제 판매)
  → bias_ratio = median(actual / predicted) 최근 14일, 매장×mid_cd별
  → blended *= clamp(bias_ratio, 0.7, 1.5)
```

### 삽입 지점

**파일**: `src/prediction/improved_predictor.py` L2758-2759

```python
# 현재 (L2758-2759)
blended = (1 - ml_weight) * rule_order + ml_weight * ml_order
order_qty = max(0, round(blended))

# 변경
blended = (1 - ml_weight) * rule_order + ml_weight * ml_order

# QW-1: Rolling Bias 보정
bias = self._get_rolling_bias(mid_cd)
if bias != 1.0:
    blended *= bias
    logger.debug(f"[Bias] {item_cd} mid={mid_cd}: bias={bias:.2f}")

order_qty = max(0, round(blended))
```

### `_get_rolling_bias()` 메서드 추가

**파일**: `src/prediction/improved_predictor.py`에 메서드 추가

```python
def _get_rolling_bias(self, mid_cd: str, window: int = 14) -> float:
    """매장×카테고리별 최근 N일 예측 편향 비율

    bias > 1.0 → 과소예측 경향 (실제가 예측보다 높음)
    bias < 1.0 → 과다예측 경향

    Returns:
        중앙값 기반 bias ratio (clamp 0.7~1.5, 데���터 부족 시 1.0)
    """
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_store_connection(self.store_id)
        cutoff = (datetime.now() - timedelta(days=window)).strftime("%Y-%m-%d")

        rows = conn.execute("""
            SELECT ds.sale_qty, pl.predicted_qty
            FROM prediction_logs pl
            JOIN daily_sales ds
              ON pl.item_cd = ds.item_cd
              AND pl.target_date = ds.sales_date
            WHERE pl.prediction_date >= ?
              AND pl.mid_cd = ?
              AND pl.predicted_qty > 0
              AND ds.sale_qty > 0
        """, (cutoff, mid_cd)).fetchall()
        conn.close()

        if len(rows) < 10:  # 최소 10개 샘플
            return 1.0

        ratios = [r[0] / r[1] for r in rows if r[1] > 0]
        if not ratios:
            return 1.0

        import statistics
        bias = statistics.median(ratios)
        return max(0.7, min(1.5, bias))

    except Exception:
        return 1.0
```

### 성능 고려
- 이 쿼리는 **예측 시 매 카테고리마다 1회** 실행 (상품마다 X)
- mid_cd 15개 × 1쿼리 = 15회/매장 → 부담 없음
- 결과를 인스턴스 캐시: `self._bias_cache = {}` (같은 mid_cd 반복 조회 방지)

```python
def _get_rolling_bias(self, mid_cd: str, window: int = 14) -> float:
    if mid_cd in self._bias_cache:
        return self._bias_cache[mid_cd]
    # ... 쿼리 실행 ...
    self._bias_cache[mid_cd] = bias
    return bias
```

### 수정 파일

| 파일 | 변경 |
|------|------|
| `src/prediction/improved_predictor.py` | `_get_rolling_bias()` 추가 + L2758에 적용 |
| `src/prediction/improved_predictor.py` | `__init__`에 `self._bias_cache = {}` 추가 |

---

## 3. QW-3: 음료 기온 계수 강화

### 현재 구조 (coefficient_adjuster.py L34~50)

```python
WEATHER_COEFFICIENTS = {
    "hot_boost": {
        "categories": ["010", "034", "039", "043", "045", "048", "021", "100"],
        "temp_threshold": 30,
        "coefficient": 1.15,  # ← 30도 이상일 ��� 15% 증가
    },
    ...
}
```

### 문제
- 010(제조음료)이 -74% 과소 → 1.15배로는 부족
- 30도 임계값 → 25~30도 구간에서도 음료 수요 증가하는데 미반영
- 전 음료 카테고리 동일 계수 → 맥주(049)는 더 민감

### 변경: 구간별 + 카테고리별 세분화

**파일**: `src/settings/constants.py`에 상수 추가

```python
# 음료 기온 민감도 강화 (QW-3)
BEVERAGE_TEMP_SENSITIVITY = {
    # mid_cd: {temp_range: multiplier}
    "010": {"25_30": 1.15, "30_plus": 1.30},  # 제조음료
    "039": {"25_30": 1.10, "30_plus": 1.25},  # 과일야채음료
    "043": {"25_30": 1.10, "30_plus": 1.20},  # 차음료
    "046": {"25_30": 1.10, "30_plus": 1.20},  # 요구르트
    "049": {"25_30": 1.20, "30_plus": 1.40},  # 맥주 (가장 민감)
}
```

**파일**: `src/prediction/coefficient_adjuster.py` L350~359

```python
# 현재: 단일 임계값 (30도)
for rule_name, rule in self.WEATHER_COEFFICIENTS.items():
    if mid_cd in rule["categories"]:
        if temp >= rule["temp_threshold"]:
            coef = rule["coefficient"]
            break

# 변경: BEVERAGE_TEMP_SENSITIVITY 우선 적용
from src.settings.constants import BEVERAGE_TEMP_SENSITIVITY
if mid_cd in BEVERAGE_TEMP_SENSITIVITY:
    sens = BEVERAGE_TEMP_SENSITIVITY[mid_cd]
    if temp >= 30:
        coef = sens.get("30_plus", 1.15)
    elif temp >= 25:
        coef = sens.get("25_30", 1.10)
    # 25도 미만은 기존 로직으로 폴백
else:
    # 기존 WEATHER_COEFFICIENTS 로직 유지
    for rule_name, rule in self.WEATHER_COEFFICIENTS.items():
        ...
```

### 수정 파일

| 파일 | 변경 |
|------|------|
| `src/settings/constants.py` | `BEVERAGE_TEMP_SENSITIVITY` 상수 추가 |
| `src/prediction/coefficient_adjuster.py` | L350~359 음료 우선 분기 추가 |

---

## 4. 전문가 토론 결과 반영 (2026-03-30)

### QW-1 수정사항
- bias 계산 시 `sale_qty > 0` (품절일 제외) + `promotions` 행사기간 제외
- **합산 clamp**: `final = min(blended, base_pred * 2.0)` — 어떤 조합이든 원래의 2배 초과 방지
- Feature Flag: `ROLLING_BIAS_ENABLED = True`

### QW-2 수정사항
- `ml_order_qty > 0` 조건 **유지** (feature leakage 방지)
- `COALESCE(pl.ml_order_qty, pl.predicted_qty)` 로 NULL 폴백
- 임계값 200→100 유지

### QW-3 수정사항
- **25도 이상 구간만 적용** (겨울 과다발주 방지, 5도 이하 구간 건드리지 않음)
- AdditiveAdjuster 경로에서도 적용되도록 위치 확인
- Feature Flag: `BEVERAGE_TEMP_PRIORITY_ENABLED = True`

## 5. 구현 순서 (토론 반영)

```
Step 1: constants.py — Feature Flag 2개 + BEVERAGE_TEMP_SENSITIVITY 추가
Step 2: stacking_predictor.py — 임계값 100 + COALESCE 폴백
Step 3: improved_predictor.py — _get_rolling_bias() (품절/행사 제외, 캐시, clamp)
Step 4: coefficient_adjuster.py — 음료 기온 우선 분기 (25도+만)
Step 5: 테스트 실행
Step 6: 커밋
```

## 6. 수정 파일 요약

| Step | 파일 | 변경 유형 |
|------|------|----------|
| 1 | `src/settings/constants.py` | Flag 2개 + 음료 계수 상수 |
| 2 | `src/analysis/stacking_predictor.py` | 임계값 + COALESCE |
| 3 | `src/prediction/improved_predictor.py` | 메서드 추가 + bias 적용 + clamp |
| 4 | `src/prediction/coefficient_adjuster.py` | 음료 우선 분기 (25도+) |

## 6. 검증 계획

### 단위 검증
1. Stacking 임계값 변경 후: 47863에서 학습 데이터 100행+ 확인
2. Rolling Bias: 46704 mid_cd=010에서 bias_ratio > 1.0 반환 확인
3. ��료 계수: 기온 27도일 때 010 계수 = 1.15 확인

### 통합 검증
4. 스케줄러 실행 후 prediction_logs에 stacking_used=True 기록 확인
5. 발주량이 bias 보정 전후로 변화했는지 로그 확인

### 회귀 검증
6. pytest 전체 실행
7. 46513(MAE 0.86) 정확도가 악화되지 않았는지 확인
