# prediction-ml-consistency Analysis Report

> **Analysis Type**: Gap Analysis (Plan vs Implementation)
>
> **Project**: BGF Auto Order System
> **Analyst**: gap-detector
> **Date**: 2026-02-22
> **Plan Doc**: [prediction-ml-consistency.plan.md](../01-plan/features/prediction-ml-consistency.plan.md)

---

## 1. Analysis Overview

### 1.1 Analysis Purpose

Plan 문서(prediction-ml-consistency.plan.md)에서 정의한 3개 Fix 항목이 실제 코드에 올바르게 구현되었는지 검증한다.

### 1.2 Analysis Scope

- **Plan Document**: `docs/01-plan/features/prediction-ml-consistency.plan.md`
- **Implementation Files**:
  - `src/prediction/prediction_config.py` (Fix 1 + Fix 3)
  - `src/prediction/improved_predictor.py` (Fix 2)
  - `src/web/services/rule_registry.py` (Fix 1)
  - `tests/test_food_prediction_fix.py` (Tests)
- **Analysis Date**: 2026-02-22

---

## 2. Gap Analysis (Plan vs Implementation)

### 2.1 Fix 1: FOOD_EXPIRY_SAFETY_CONFIG Duplication Removal (P1)

| Plan Item | Expected | Actual | Status |
|-----------|----------|--------|--------|
| prediction_config.py: FOOD_EXPIRY_SAFETY_CONFIG dict 삭제 | dict 삭제, import re-export | Line 443: `from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG` | Match |
| prediction_config.py: food.py에서 import | import re-export | Line 443: import 확인 | Match |
| rule_registry.py:199-203 import 경로 | prediction_config 경유 (re-export이므로 변경 불필요) | Line 199-203: `from src.prediction.prediction_config import ... FOOD_EXPIRY_SAFETY_CONFIG` | Match |
| rule_registry.py:689-694 import 경로 | prediction_config 경유 (re-export이므로 변경 불필요) | Line 689-694: `from src.prediction.prediction_config import ... FOOD_EXPIRY_SAFETY_CONFIG` | Match |
| rule_registry.py:253 source_file 라벨 | `"src/prediction/categories/food.py"` | Line 253: `source_file="src/prediction/categories/food.py"` | Match |

**Verification Detail**:

- `prediction_config.py` line 442-443:
  ```python
  # 푸드류 유통기한 기반 안전재고: food.py의 정의를 사용 (중복 제거)
  from src.prediction.categories.food import FOOD_EXPIRY_SAFETY_CONFIG  # noqa: E402
  ```
  구 버전 dict (safety_days 0.3/0.5)가 완전 삭제되고, food.py의 신 버전(0.5/0.7)을 re-export. 이로써 `prediction_config.FOOD_EXPIRY_SAFETY_CONFIG is food.FOOD_EXPIRY_SAFETY_CONFIG` 동일 객체 참조가 보장됨.

- `rule_registry.py`의 두 import 지점(line 199-203, line 689-694) 모두 `prediction_config`에서 import하므로, 내부적으로 food.py의 값을 사용. **라벨** line 253이 `"src/prediction/categories/food.py"`로 정확히 변경됨.

**Fix 1 Score: 5/5 (100%)**

---

### 2.2 Fix 2: promo_active Inference Delivery (P1)

| Plan Item | Expected | Actual | Status |
|-----------|----------|--------|--------|
| _apply_ml_ensemble()에서 행사 정보 조회 | promo_manager.get_promotion_status() 호출 | Line 1907-1910: 조회 로직 존재 | Match |
| self._promo_adjuster 존재 체크 | hasattr 가드 | Line 1907: `if self._promo_adjuster and hasattr(self._promo_adjuster, 'promo_manager')` | Match |
| PromotionStatus.current_promo 사용 | bool(promo_status.current_promo) | Line 1910: `_promo_active = bool(promo_status and promo_status.current_promo)` | Match |
| Exception 안전 처리 | try/except pass | Line 1908-1912: `try: ... except Exception: pass` | Match |
| build_features() 호출 시 promo_active= 전달 | 파라미터 추가 | Line 1933: `promo_active=_promo_active,` | Match |

**Verification Detail**:

- `improved_predictor.py` line 1905-1933:
  ```python
  # 행사 정보 조회
  _promo_active = False
  if self._promo_adjuster and hasattr(self._promo_adjuster, 'promo_manager'):
      try:
          promo_status = self._promo_adjuster.promo_manager.get_promotion_status(item_cd)
          _promo_active = bool(promo_status and promo_status.current_promo)
      except Exception:
          pass

  features = MLFeatureBuilder.build_features(
      ...
      promo_active=_promo_active,
  )
  ```
  Plan에서 명시한 API (`promo_manager.get_promotion_status`, `PromotionStatus.current_promo`)를 정확히 사용. fallback(False) 및 예외 처리가 Plan의 pseudo-code와 일치.

**Fix 2 Score: 5/5 (100%)**

---

### 2.3 Fix 3: prediction_config.py Dead Code Removal (P2)

| Plan Item | Expected | Actual | Status |
|-----------|----------|--------|--------|
| FoodExpiryResult dataclass 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| is_food_category() 함수 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| get_food_expiry_group() 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| get_food_expiration_days() 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| get_food_disuse_coefficient() 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| analyze_food_expiry_pattern() 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| calculate_food_dynamic_safety() 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |
| get_safety_stock_with_food_pattern() 삭제 | 존재하지 않음 | grep 결과: 미발견 | Match |

**Verification Detail**:

- `prediction_config.py`에서 위 8개 항목(1 dataclass + 1 함수 + 6 함수)을 grep 검색한 결과 **0건 hit**. 모두 삭제됨.
- 대신 line 1252-1259에 주석으로 이관 기록이 남아있음:
  ```python
  # 8-3. 푸드류 유통기한 기반 동적 안전재고 분석 함수
  # -> food.py로 이관 완료. categories/__init__.py에서 re-export.
  #   FoodExpiryResult, is_food_category, get_food_expiry_group, ...
  ```
  주석을 통해 이관 이력을 추적 가능하므로 유지보수에 적합.

- 파일 총 라인: 1477행. Plan에서 예상한 ~260행 삭제에 부합(이전 추정 ~1700행에서 삭제 후 1477행).

**Fix 3 Score: 8/8 (100%)**

---

### 2.4 Tests

| Plan Item | Expected | Actual | Status |
|-----------|----------|--------|--------|
| Fix 1 테스트: 동일 객체 참조 | `config_fesc is food_fesc` | `TestFoodExpirySafetyConfigUnified.test_same_object` (line 457-461) | Match |
| Fix 1 테스트: ultra_short safety_days 값 | `== 0.5` | `test_ultra_short_safety_days_is_05` (line 463-466) | Match |
| Fix 1 테스트: short safety_days 값 | `== 0.7` | `test_short_safety_days_is_07` (line 468-471) | Match |
| Fix 2 테스트: 행사 중 promo_active=True | mock 검증 | `TestPromoActiveInference.test_promo_active_with_active_promo` (line 480-530) | Match |
| Fix 2 테스트: adjuster None 시 False | fallback 검증 | `test_promo_active_without_adjuster` (line 532-572) | Match |
| Fix 2 테스트: 예외 시 False | exception fallback | `test_promo_active_exception_fallback` (line 574-615) | Match |
| Fix 3 테스트: FoodExpiryResult 위치 | food.py에만 존재 | `test_no_food_expiry_result_class` (line 624-633) | Match |
| Fix 3 테스트: is_food_category 부재 | prediction_config에 없음 | `test_no_duplicate_is_food_category` (line 635-640) | Match |

**Tests Score: 8/8 (100%)**

---

## 3. Match Rate Summary

```
+---------------------------------------------+
|  Overall Match Rate: 100%                    |
+---------------------------------------------+
|  Fix 1 (FOOD_EXPIRY_SAFETY_CONFIG):  5/5    |
|  Fix 2 (promo_active):               5/5    |
|  Fix 3 (dead code removal):          8/8    |
|  Tests:                               8/8    |
+---------------------------------------------+
|  Total: 26/26 items verified                 |
+---------------------------------------------+
```

---

## 4. Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match (Plan vs Implementation) | 100% | Pass |
| Architecture Compliance | 100% | Pass |
| Convention Compliance | 100% | Pass |
| Test Coverage (Fix-specific) | 100% | Pass |
| **Overall** | **100%** | **Pass** |

---

## 5. Differences Found

### Missing Features (Plan O, Implementation X)

None.

### Added Features (Plan X, Implementation O)

| Item | Implementation Location | Description |
|------|------------------------|-------------|
| FOOD_EXPIRY_FALLBACK re-export | prediction_config.py:444 | Plan에서 FOOD_EXPIRY_SAFETY_CONFIG만 언급했으나, FOOD_EXPIRY_FALLBACK도 함께 re-export 처리됨 (일관성 향상) |
| 이관 주석 | prediction_config.py:1252-1259 | 삭제된 함수의 이관 기록 주석 추가 (Plan 미언급, 유지보수에 유익) |

### Changed Features (Plan != Implementation)

None.

---

## 6. Detailed Verification Log

### 6.1 prediction_config.py

- **Line 442-444**: FOOD_EXPIRY_SAFETY_CONFIG + FOOD_EXPIRY_FALLBACK을 food.py에서 import (re-export)
- **Line 1252-1259**: dead code 8개 함수 삭제, 이관 주석 유지
- **FoodExpiryResult, is_food_category 등**: grep 0건 확인

### 6.2 improved_predictor.py

- **Line 1905-1912**: promo_active 조회 로직 (`_promo_adjuster.promo_manager.get_promotion_status`)
- **Line 1933**: `build_features(... promo_active=_promo_active)` 전달 확인

### 6.3 rule_registry.py

- **Line 199-203**: `from src.prediction.prediction_config import ... FOOD_EXPIRY_SAFETY_CONFIG` (prediction_config가 food.py re-export이므로 신 버전 값 사용)
- **Line 253**: `source_file="src/prediction/categories/food.py"` 라벨 변경 완료
- **Line 689-694**: 동일 import 패턴 (prediction_config 경유)

### 6.4 tests/test_food_prediction_fix.py

- **TestFoodExpirySafetyConfigUnified**: Fix 1 검증 3개 테스트
- **TestPromoActiveInference**: Fix 2 검증 3개 테스트
- **TestPredictionConfigDeadCodeRemoved**: Fix 3 검증 2개 테스트

---

## 7. Recommended Actions

None required. All 3 Fix items are fully implemented as specified in the Plan document.

### Minor Observations (No Action Needed)

1. `rule_registry.py` line 689-694의 import가 `prediction_config` 경유인 점은 Plan과 일치하며, 향후 직접 food.py에서 import하도록 변경할 수도 있으나 현재 re-export 패턴이 일관성 있으므로 변경 불필요.
2. Plan에서 테스트 총 8개 예상 (Fix 1: 3, Fix 2: 3, Fix 3: 2) -- 실제 구현도 동일하게 8개.

---

## 8. Conclusion

Match Rate **100%**. Plan 문서의 3개 Fix (P1: FOOD_EXPIRY_SAFETY_CONFIG 중복 제거, P1: promo_active 추론 전달, P2: dead code 삭제)가 모두 정확히 구현되었으며, 8개 테스트가 각 Fix를 검증하고 있다. 설계-구현 간 격차 없음.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-02-22 | Initial analysis - 100% match rate | gap-detector |
