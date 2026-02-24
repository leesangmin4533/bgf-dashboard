# prediction-ml-consistency Plan

> **Feature**: prediction-ml-consistency
> **Priority**: High
> **Author**: code-analyzer + Explore agent
> **Created**: 2026-02-22
> **Status**: Plan

---

## 1. Background

BGF 자동 발주 시스템의 예측 파이프라인은 두 개의 독립적인 예측 경로를 앙상블로 결합한다:

1. **규칙 기반 예측**: WMA + 7개 계수 곱셈 (holiday, weather, food_wx, weekday, seasonal, association, trend)
2. **ML 예측**: sklearn 모델이 31개 feature로 판매량 예측

앙상블 가중 평균 (데이터 60일 미만: ML 30%, 60일 이상: ML 50%)으로 최종 발주량을 산출한다.

코드 분석 결과 **3가지 일관성 문제**가 확인되었다.

---

## 2. Issues

### Issue 1: FOOD_EXPIRY_SAFETY_CONFIG 중복 정의 + 값 불일치 (P1 Critical)

**위치**:
- `src/prediction/prediction_config.py:442-481` (구 버전)
- `src/prediction/categories/food.py:31-70` (신 버전, Priority 1.3 상향 반영)

**불일치 내용**:

| 그룹 | prediction_config.py | food.py | 차이 |
|------|---------------------|---------|------|
| ultra_short | safety_days: **0.3** | safety_days: **0.5** | 0.2일 차이 |
| short | safety_days: **0.5** | safety_days: **0.7** | 0.2일 차이 |
| medium | 1.0 | 1.0 | 동일 |
| long | 1.5 | 1.5 | 동일 |
| very_long | 2.0 | 2.0 | 동일 |

**import 경로 분석**:
- `improved_predictor.py:81` → `categories/__init__.py:55` → **food.py** (신 버전 0.5/0.7)
- `food_waste_calibrator.py:28` → **food.py** (신 버전)
- `rule_registry.py:199-203` → **prediction_config.py** (구 버전 0.3/0.5)
- `rule_registry.py:689-694` → **prediction_config.py** (구 버전, 2번째 import)

**영향**: 웹 대시보드의 규칙 조회 화면(`rule_registry`)은 구 버전 값(0.3/0.5)을 표시하지만, 실제 예측은 신 버전 값(0.5/0.7)을 사용. 사용자에게 잘못된 정보 표시. rule_registry.py에서 2곳 모두 수정 필요.

---

### Issue 2: ML 앙상블 추론 시 promo_active 누락 (P1 Critical)

**위치**:
- `improved_predictor.py:1905-1924` (build_features 호출부)
- `ml/feature_builder.py:92` (promo_active 파라미터, default=False)
- `ml/trainer.py:190,263` (학습 시 promo_active 포함)

**불일치**:
```
학습 시: promo_active = any(promotions)  ← 실제 행사 정보 반영
추론 시: promo_active = False (기본값)   ← 항상 비행사로 입력
```

**영향**: ML 모델이 행사 상품의 수요 증가 패턴을 학습했으나, 추론 시 항상 `promo_active=0.0`으로 입력되어 행사 효과를 전혀 반영하지 못함. 행사 중인 상품의 ML 예측이 과소.

---

### Issue 3: prediction_config.py 내 중복 함수 (P2 Major)

**위치**:
- `prediction_config.py:1331` → `FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]` 사용
- `prediction_config.py:1417, 1489, 1542, 1546, 1553` → 동일 CONFIG 사용
- `food.py:252, 519, 627, 932, 936, 944` → 자체 CONFIG 사용

**상황**: `prediction_config.py`에 정의된 `get_food_safety_stock()`, `get_food_expiry_group()` 등의 함수가 구 버전 CONFIG를 사용. `food.py`에도 동일 기능의 함수가 신 버전 CONFIG로 존재. 호출부에 따라 다른 결과가 나올 수 있음.

**영향**: 현재 `improved_predictor.py`는 `food.py` 경로를 사용하므로 실제 예측에는 문제 없음. 하지만 향후 유지보수 시 혼란 유발 가능.

---

## 3. Fix Plan

### Fix 1: FOOD_EXPIRY_SAFETY_CONFIG 중복 제거 (P1)

**파일**: `src/prediction/prediction_config.py:442-481`
**작업**:
1. `prediction_config.py`의 `FOOD_EXPIRY_SAFETY_CONFIG` dict 삭제
2. `from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG` 추가 (re-export)
3. `rule_registry.py:199-203` 및 `rule_registry.py:689-694` 두 곳 모두 import 경로 변경
4. `rule_registry.py:253` source_file 라벨도 `"src/prediction/categories/food.py"`로 변경

**검증**: `rule_registry.py`에서 표시하는 safety_days가 실제 사용 값(0.5/0.7)과 일치하는지 확인

---

### Fix 2: promo_active 추론 시 전달 (P1)

**파일**: `src/prediction/improved_predictor.py:1905-1924`
**작업**:
1. `_apply_ml_ensemble()` 내에서 행사 정보 조회
2. `build_features()` 호출 시 `promo_active=` 파라미터 추가

```python
# 행사 정보 조회 (기존 _promo_adjuster.promo_manager 활용)
_promo_active = False
if self._promo_adjuster and hasattr(self._promo_adjuster, 'promo_manager'):
    try:
        promo_status = self._promo_adjuster.promo_manager.get_promotion_status(item_cd)
        _promo_active = bool(promo_status and promo_status.current_promo)
    except Exception:
        pass

features = MLFeatureBuilder.build_features(
    ...
    promo_active=_promo_active,     # ← 추가
    ...
)
```

**API 참고**: `PromotionStatus` dataclass의 `current_promo` (Optional[str]: '1+1', '2+1', 또는 None)로 행사 여부 판별

**검증**: 행사 중인 상품에 대해 ML feature 14번째 값(promo_active)이 1.0인지 확인

---

### Fix 3: prediction_config.py 중복 함수 정리 (P2)

**파일**: `src/prediction/prediction_config.py:1321-1554`
**작업**:
1. prediction_config.py에서 FOOD_EXPIRY_SAFETY_CONFIG를 사용하는 함수들: `get_food_safety_stock()`, `get_food_expiry_group()` 등
2. **호출자 분석 완료**: 외부 호출자 없음 (dead code). 모든 호출부는 `categories/__init__.py`를 경유하여 `food.py`의 함수를 사용
3. prediction_config.py의 해당 함수들을 삭제

**근거**: `categories/__init__.py:55`에서 food.py의 함수를 re-export하며, improved_predictor.py를 포함한 모든 호출부가 이 경로를 사용

---

## 4. Implementation Order

1. **Fix 1** (FOOD_EXPIRY_SAFETY_CONFIG 중복 제거) - FOOD_EXPIRY_FALLBACK과 동일 패턴, 즉시 효과
2. **Fix 2** (promo_active 전달) - ML 예측 정확도 직접 개선
3. **Fix 3** (중복 함수 정리) - 코드 정리, 기능 변경 없음

## 5. Key Files

| File | Changes |
|------|---------|
| `src/prediction/prediction_config.py` | FOOD_EXPIRY_SAFETY_CONFIG 삭제, import 변경 |
| `src/prediction/improved_predictor.py` | promo_active 전달 추가 |
| `src/web/services/rule_registry.py` | import 경로 변경 (prediction_config -> food) |

## 6. Tests

### Fix 1 검증
- `prediction_config.FOOD_EXPIRY_SAFETY_CONFIG is food.FOOD_EXPIRY_SAFETY_CONFIG` 동일 객체 참조 확인
- `prediction_config.FOOD_EXPIRY_SAFETY_CONFIG["expiry_groups"]["ultra_short"]["safety_days"] == 0.5` 확인
- rule_registry에서 표시하는 safety_days가 0.5/0.7 (신 버전)인지 확인

### Fix 2 검증
- `_promo_adjuster`가 있고 행사 중인 상품에 대해 `promo_active=True`로 build_features 호출되는지 mock 검증
- `_promo_adjuster`가 None인 경우 `promo_active=False`로 폴백되는지 확인
- `promo_manager.get_promotion_status()` 예외 시 `promo_active=False`로 안전하게 처리되는지 확인

### Fix 3 검증
- 삭제된 함수가 외부에서 import되지 않는지 grep 확인
- 기존 테스트 중 prediction_config.py의 삭제 대상 함수를 직접 참조하는 테스트가 있으면 food.py로 경로 변경

### 전체
- 전체 테스트 스위트 통과 확인

## 7. Out of Scope

- ML 앙상블 가중치 튜닝 (30%/50%)
- 카테고리별 계수를 ML feature로 추가 (구조 변경 필요)
- ML 앙상블과 규칙 기반의 이중 계산 문제 (설계상 의도)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-22 | Initial plan (3 issues identified) | code-analyzer |
| 1.1 | 2026-02-22 | design-validator 피드백 반영: Fix2 API 수정, rule_registry 2nd import 추가, Fix3 dead code 확인, 테스트 구체화 | design-validator |
