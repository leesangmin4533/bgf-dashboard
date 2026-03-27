# ML Improvement Plan

> ML 모델 실효성 검증 + 적응형 블렌딩 + 피처 정리 + 평가 체계 개선

## 1. 문제 정의

### 1.1 현황 (실제 메트릭 기반)

| 매장 | 그룹 | MAE | MAPE | 샘플수 | 테스트 |
|------|------|-----|------|--------|--------|
| 46513 | food | 0.55 | 93.4% | 1,480 | 239 |
| 46513 | alcohol | 1.66 | 79.6% | 967 | 147 |
| 46513 | tobacco | 1.62 | 88.9% | 1,383 | 212 |
| 46513 | perishable | 0.60 | 112.6% | 329 | 45 |
| 46513 | general | 0.92 | 90.9% | 12,569 | 1,833 |
| 46704 | food | 0.61 | 95.8% | 1,520 | 280 |
| 46704 | alcohol | 1.82 | 85.2% | 767 | 116 |
| 46704 | tobacco | 1.95 | 80.2% | 1,834 | 386 |
| 46704 | perishable | 1.04 | 82.7% | 216 | 38 |
| 46704 | general | 1.00 | 85.0% | 12,991 | 2,263 |

### 1.2 핵심 문제

1. **ML 기여도 불명** — rule-only vs ML-blended 비교 데이터 없음. ML이 발주를 개선하는지 악화시키는지 알 수 없음
2. **고정 블렌딩 가중치** — MAE 0.55(food)와 1.95(tobacco) 그룹이 동일 50% 가중치
3. **피처 중복** — 그룹별 모델인데 카테고리 원핫(10개) 포함, 실효 피처 ~30개
4. **부적합 평가 지표** — MAPE 80~112%는 편의점 저판매량에서 무의미. Accuracy@1이 실질적 지표
5. **Quantile alpha 역전** — 폐기 위험 높은 food(alpha=0.6)가 넉넉, 안전한 general(0.45)이 보수적
6. **perishable 샘플 부족** — 216~329개로 신뢰도 의문

## 2. 목표

| 지표 | 현재 | 목표 |
|------|------|------|
| ML 기여도 검증 | 불가 | rule vs blend 비교 가능 |
| 블렌딩 방식 | 고정 (30%/50%) | MAE 기반 적응형 |
| 피처 수 | 41개 (10개 중복) | ~31개 (중복 제거) |
| 주요 평가 지표 | MAPE | MAE + Accuracy@1 |
| Quantile alpha | 도메인 역전 | 도메인 정합 |

## 3. 범위

### 3.1 수정 대상 (5개 파일)

| 파일 | 변경 내용 |
|------|----------|
| `src/prediction/improved_predictor.py` | 적응형 블렌딩 + ML 기여도 로깅 |
| `src/prediction/ml/feature_builder.py` | 중복 피처 제거 (41→31) |
| `src/prediction/ml/trainer.py` | Quantile alpha 조정 + Accuracy@1 메트릭 추가 |
| `src/prediction/ml/model.py` | 모델 메타에 Accuracy@1 기록 |
| `tests/test_ml_improvement.py` | 신규 테스트 |

### 3.2 하지 않는 것

- 모델 알고리즘 변경 (RF+GB 유지, 적합함)
- 딥러닝/트랜스포머 도입
- 실시간 학습 (배치 유지)
- Cross-validation 도입 (복잡도 대비 효과 낮음)

## 4. 구현 상세

### 4.1 Phase A: ML 기여도 로깅 (improved_predictor.py)

**현재**: `_apply_ml_ensemble()`에서 블렌딩만 수행, 기여도 추적 없음

**변경**: 블렌딩 전후 차이를 prediction context에 기록

```python
# ctx에 추가할 필드
ctx["ml_delta"] = ml_order - rule_order      # ML이 바꾼 양 (+ 증가, - 감소)
ctx["ml_abs_delta"] = abs(ml_order - rule_order)
ctx["ml_changed_final"] = (blended != rule_order)  # 최종 정수 발주 변경 여부
```

이를 통해 이후 Accuracy 비교에서:
- `ml_changed_final=True`인 건만 필터링
- rule_order vs blended vs actual_qty 3자 비교 가능

### 4.2 Phase B: 적응형 블렌딩 (improved_predictor.py)

**현재**:
```python
ml_weight = 0.3 if data_days < 60 else 0.5  # 고정
```

**변경**: 그룹별 MAE 기반 동적 가중치

```python
def _get_ml_weight(self, mid_cd: str, data_days: int) -> float:
    """MAE 기반 적응형 ML 가중치

    원리: MAE가 낮을수록 ML에 더 높은 가중치.
    MAE >= 2.0이면 ML 가중치 0.1 (거의 무시).
    MAE <= 0.5이면 ML 가중치 0.5 (최대).
    """
    if data_days < 30:
        return 0.0  # 데이터 부족 → ML 미사용

    group = get_category_group(mid_cd)
    group_mae = self._get_group_mae(group)  # 모델 메타에서 조회

    if group_mae is None:
        return 0.15  # 메타 없으면 보수적

    # MAE → 가중치 선형 매핑: 0.5(MAE≤0.5) ~ 0.1(MAE≥2.0)
    weight = max(0.1, min(0.5, 0.5 - (group_mae - 0.5) * 0.267))

    # 데이터 부족 감쇄
    if data_days < 60:
        weight *= 0.6

    return round(weight, 2)
```

**효과 예시 (46513 기준)**:

| 그룹 | MAE | 기존 가중치 | 적응형 가중치 |
|------|-----|-----------|-------------|
| food | 0.55 | 0.50 | 0.49 |
| perishable | 0.60 | 0.50 | 0.47 |
| general | 0.92 | 0.50 | 0.39 |
| tobacco | 1.62 | 0.50 | 0.20 |
| alcohol | 1.66 | 0.50 | 0.19 |

→ MAE가 좋은 food/perishable은 가중치 유지, 나쁜 tobacco/alcohol은 대폭 감소

### 4.3 Phase C: 피처 정리 (feature_builder.py)

**제거 대상 (10개)**:

| 피처 | 제거 이유 |
|------|----------|
| `is_food_group` | 그룹별 별도 모델 → 항상 1.0 또는 0.0, 정보 없음 |
| `is_alcohol_group` | 동일 |
| `is_tobacco_group` | 동일 |
| `is_perishable_group` | 동일 |
| `is_general_group` | 동일 |
| `is_lcd_food` | 대분류 원핫도 그룹과 높은 상관 |
| `is_lcd_snack` | 동일 |
| `is_lcd_grocery` | 동일 |
| `is_lcd_beverage` | 동일 |
| `is_lcd_non_food` | 동일 |

**유지 피처 (31개)**:
- 기본 통계 5, 트렌드 2, 시간 6, 비즈니스 3, 외부 2, Lag 3, 연관 1, 연휴 3, 입고 5, promo 1

**주의**: feature_hash가 변경되므로 기존 모델과 호환 불가 → 다음 학습 시 자동 재학습

### 4.4 Phase D: Quantile Alpha 도메인 정합 (trainer.py)

**현재** (비직관적):
```python
GROUP_QUANTILE_ALPHA = {
    "food_group": 0.6,      # 넉넉 → 폐기 증가
    "perishable_group": 0.55,
    "alcohol_group": 0.55,
    "tobacco_group": 0.5,
    "general_group": 0.45,  # 보수적 → 재고 유지
}
```

**변경** (도메인 정합):
```python
GROUP_QUANTILE_ALPHA = {
    "food_group": 0.45,      # 보수적 (유통기한 짧음, 폐기비용 > 품절비용)
    "perishable_group": 0.48, # 약간 보수적
    "alcohol_group": 0.55,   # 유지 (유통기한 길고 품절 기회비용 높음)
    "tobacco_group": 0.55,   # 상향 (품절 시 고객 이탈 높음, 폐기 없음)
    "general_group": 0.50,   # 중립 (장기 유통)
}
```

**근거**:
- food/perishable: 유통기한 1~3일, 폐기 직결 → 보수적 예측이 재무적으로 유리
- tobacco: 유통기한 없음, 품절 시 고객 이탈 → 넉넉하게
- alcohol: 유통기한 길고 품절 기회비용 → 현행 유지
- general: 대칭 중립

### 4.5 Phase E: 평가 지표 개선 (trainer.py, model.py)

**Accuracy@N 메트릭 추가** (학습 평가 시):

```python
# y_test vs y_pred 비교
accuracy_at_1 = np.mean(np.abs(y_test - y_pred) <= 1.0)  # ±1개 이내
accuracy_at_2 = np.mean(np.abs(y_test - y_pred) <= 2.0)  # ±2개 이내

# 메타데이터에 저장
metrics["accuracy_at_1"] = round(accuracy_at_1, 3)
metrics["accuracy_at_2"] = round(accuracy_at_2, 3)
```

**성능 게이트 보완**: MAE 20% 악화 OR Accuracy@1이 5%p 이상 하락 시 롤백

## 5. 구현 순서

```
Phase A (기여도 로깅)  ← 기반 확보, 다른 변경 전 먼저
    ↓
Phase C (피처 정리)    ← feature_hash 변경 → 재학습 유발
    ↓
Phase D (Alpha 조정)   ← C와 함께 적용 (재학습 1회로 통합)
    ↓
Phase E (지표 개선)    ← 재학습 시 새 메트릭 기록
    ↓
Phase B (적응형 블렌딩) ← 새 메트릭 기반으로 가중치 산출
```

## 6. 성공 기준

- [ ] ML 기여도 로깅이 prediction context에 기록됨
- [ ] rule-only vs blend 비교가 로그/API로 확인 가능
- [ ] 그룹별 MAE 기반 적응형 가중치 적용
- [ ] 피처 41→31 (중복 10개 제거), 기존 테스트 통과
- [ ] Quantile alpha 도메인 정합 반영
- [ ] Accuracy@1/2 메트릭이 model_meta.json에 기록
- [ ] 성능 게이트에 Accuracy@1 조건 추가

## 7. 리스크

| 리스크 | 완화 |
|--------|------|
| 피처 제거 후 MAE 악화 | 기존 모델 _prev 백업 존재, 성능 게이트가 롤백 |
| Alpha 변경으로 food 품절 증가 | 0.45는 여전히 중앙 근처, 극단 아님 |
| 적응형 가중치 계산 오류 | 폴백: 메타 없으면 0.15 고정 |
