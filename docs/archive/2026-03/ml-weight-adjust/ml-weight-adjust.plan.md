# Plan: ml-weight-adjust

## 개요

ML 앙상블 가중치를 실측 데이터 기반으로 하향 조정하여 예측 정확도를 개선한다.

## 배경 및 문제

### 현상
- prediction_logs 분석 결과 ML 앙상블(ensemble_50)이 규칙 모델(rule)보다 **대부분의 카테고리에서 열위**
- 46513: rule MAE=0.36/Acc@1=90.7% vs ensemble_50 MAE=0.37/Acc@1=90.1%
- 46704: rule MAE=0.45/Acc@1=87.7% vs ensemble_50 MAE=0.80/Acc@1=79.9%
- 푸드: rule MAE=0.26 vs ensemble_50 MAE=0.70 (2.7배 악화)

### 근본 원인
1. **Zero-inflated 타겟**: actual_qty=0이 전체의 98.8% → ML이 "항상 0 예측"을 학습하려는 편향
2. **ML 발주 변환 단순**: `ml_pred + safety_stock - stock - pending` (단순 산술) vs Rule의 12단계 후처리
3. **현재 최대 가중치 0.5(50:50)이 과도**: ML이 Rule보다 나은 영역이 거의 없음에도 동등한 영향력

### 영향 범위
- `improved_predictor.py`의 `_get_ml_weight()` 메서드 1곳
- `tests/test_ml_*.py` 관련 테스트

## 수정 내용

### 파일: `src/prediction/improved_predictor.py`

#### `_get_ml_weight()` (라인 2399-2427)

**현재 값:**
```python
# MAE → 가중치 선형 매핑: 0.5(MAE≤0.5) ~ 0.1(MAE≥2.0)
weight = max(0.1, min(0.5, 0.5 - (group_mae - 0.5) * 0.267))
```

**변경 내용:**
1. 카테고리별 최대 가중치 차등 적용
2. 전체 상한을 0.5 → 카테고리에 따라 0.15~0.25로 하향

```python
# 카테고리별 ML 최대 가중치
ML_MAX_WEIGHT = {
    "food": 0.15,       # 푸드: Rule이 2.7배 우위
    "perishable": 0.15, # 유사 특성
    "alcohol": 0.25,    # 일반적 성능
    "tobacco": 0.25,    # 일반적 성능
    "general": 0.25,    # 일반적 성능
}
DEFAULT_ML_MAX_WEIGHT = 0.20

# MAE → 가중치 매핑 (max_w 기준)
max_w = ML_MAX_WEIGHT.get(group, DEFAULT_ML_MAX_WEIGHT)
weight = max(0.05, min(max_w, max_w - (group_mae - 0.5) * (max_w / 1.5)))
```

3. 데이터 부족 감쇄 유지 (`data_days < 60 → ×0.6`)
4. 메타 없음 기본값: 0.15 → 0.10

#### 추가 변경
- `data_days < 30` + food 그룹 모델 폴백: 0.1 → 0.05
- docstring 업데이트

### 파일: `tests/test_ml_ensemble.py` (또는 관련 테스트)

- 기존 가중치 0.5 기대값 → 새 최대값으로 업데이트
- 카테고리별 차등 가중치 테스트 추가
- 경계값 테스트 (MAE=0.5, MAE=2.0, data_days=29/30/59/60)

## 수정하지 않는 것

- ML 모델 학습 로직 (`trainer.py`): 학습 자체는 정상
- ML 발주 변환 공식 (`blended = (1-w)*rule + w*ml_order`): 공식은 유지, 가중치만 조정
- `_get_group_mae()`: MAE 조회 로직은 변경 없음
- 그룹 모델 (`predict_dual`): 듀얼 모델 구조 유지

## 예상 효과

- 푸드 카테고리: ML 영향 50% → 15% 축소 → Rule 정확도에 수렴
- 일반 카테고리: ML 영향 50% → 25% 축소 → Rule 주도 + ML 보조
- 전체 MAE 개선: ensemble이 rule 수준으로 회복 예상

## 리스크

- **낮음**: ML이 실제로 Rule보다 우수한 특정 상품에서 성능 저하 가능
- **대응**: 향후 ML 모델 개선 시(Zero-inflated 대응 등) 가중치 재상향 가능
- 가중치 상수를 명시적으로 분리하여 향후 조정 용이하게 구성

## 검증

1. `pytest tests/` - 전체 테스트 통과
2. 가중치 변경 후 기존 ensemble_50 → ensemble_15/ensemble_25 등 model_type 레이블 자동 반영 확인
