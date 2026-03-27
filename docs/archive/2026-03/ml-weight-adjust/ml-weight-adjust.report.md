# PDCA Report: ml-weight-adjust

## 개요

| 항목 | 내용 |
|------|------|
| Feature | ml-weight-adjust |
| 시작일 | 2026-03-01 |
| Match Rate | 100% |
| Iteration | 0 (1회 통과) |
| 수정 파일 | 3개 |
| 테스트 | 62/62 통과 (관련), 2800+ 전체 통과 |

## 배경

prediction_logs 분석 결과 ML 앙상블(ensemble_50)이 규칙 모델(rule)보다 대부분의 카테고리에서 성능이 열위:
- 46513: rule MAE=0.36 vs ensemble_50 MAE=0.37
- 46704: rule MAE=0.45 vs ensemble_50 MAE=0.80
- 푸드: rule MAE=0.26 vs ensemble_50 MAE=0.70 (2.7배 악화)

근본 원인: Zero-inflated 타겟(actual=0이 98.8%), ML 발주 변환이 Rule의 12단계 후처리보다 단순, 최대 가중치 0.5가 과도.

## 수정 내용

### `src/prediction/improved_predictor.py`

1. **ML_MAX_WEIGHT 상수 추가**: 카테고리별 최대 가중치 차등 적용
   - food_group: 0.15, perishable_group: 0.15
   - alcohol/tobacco/general_group: 0.25
   - 기본값: 0.20

2. **_get_ml_weight() 로직 변경**:
   - 최대 가중치: 0.5 → 카테고리별 0.15~0.25
   - 최소 가중치: 0.1 → 0.05
   - 메타 없음 기본값: 0.15 → 0.10
   - 그룹 모델 폴백: 0.1 → 0.05
   - MAE→가중치 매핑 공식: `max(0.05, min(max_w, max_w - (mae-0.5) * (max_w/1.5)))`

### `tests/test_ml_improvement.py`

- 7개 테스트 기대값 업데이트 (새 가중치 범위 반영)
- 2개 테스트 추가 (food max 0.15, general max 0.25 분리 검증)

### `tests/test_food_ml_dual_model.py`

- 1개 테스트: group fallback weight 0.1 → 0.05

## 가중치 변경 요약

| 시나리오 | 이전 | 이후 |
|----------|:---:|:---:|
| 푸드 MAE=0.5 (최적) | 0.50 | **0.15** |
| 일반 MAE=0.5 (최적) | 0.50 | **0.25** |
| MAE=2.0 (최악) | 0.10 | **0.05** |
| 메타 없음 | 0.15 | **0.10** |
| 푸드 그룹 폴백 (data<30일) | 0.10 | **0.05** |
| data_days<60 감쇄 | ×0.6 | ×0.6 (유지) |

## 예상 효과

- ML이 Rule보다 열위인 환경에서 Rule 모델의 정확도를 보존
- ensemble_15(food) / ensemble_25(general) 등 model_type 레이블로 추적 가능
- 향후 ML 모델 개선 시(Zero-inflated 대응) 가중치 재상향 용이 (상수 분리)

## 검증 결과

- ML 관련 테스트: 62/62 통과
- 전체 테스트: 2800+ 통과 (기존 알려진 실패 6건 제외)
- Gap Analysis: Match Rate 100%
