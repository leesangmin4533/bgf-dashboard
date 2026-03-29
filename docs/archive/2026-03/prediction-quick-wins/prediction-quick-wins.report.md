# Completion Report: prediction-quick-wins

> PDCA 완료 | 2026-03-30 | Match Rate: 97%

## 1. 요약

| 항목 | 값 |
|------|-----|
| Feature | 예측 정확도 Quick Win 3가지 |
| 시작일 | 2026-03-30 |
| 완료일 | 2026-03-30 |
| Match Rate | 97% (PASS) |
| 커밋 | `3375c19` |
| 테스트 | 269/269 통과 |

## 2. 변경 내용

### QW-1: Rolling Bias Multiplier
- 매장×카테고리별 14일 median(actual/predicted) 편향 자동보정
- 품절일+행사기간 제외, clamp(0.7~1.5), 합산 clamp(base×2.0)
- `improved_predictor.py`: `_get_rolling_bias()` + L2765에 적용

### QW-2: Stacking 임계값 조정
- MIN_TRAIN_SAMPLES 200→100 (47863 활성화 기대)
- ml_order_qty NULL→COALESCE(predicted_qty) 폴백
- `stacking_predictor.py` L48, L166

### QW-3: 음료 기온 계수 강화
- BEVERAGE_TEMP_SENSITIVITY: 5개 카테고리 25~30도/30도+ 구간별 계수
- 25도 미만은 기존 로직 유지 (겨울 과다발주 방지)
- `coefficient_adjuster.py` L350-379

## 3. 안전장치 (토론 반영)

| 위험 | 완화 |
|------|------|
| 이중증폭 (bias×기온) | 합산 clamp: base×2.0 초과 불가 |
| 피드백루프 (품절→과소) | sale_qty>0 + 행사 제외 필터 |
| Feature leakage | ml_order_qty 조건 유지, COALESCE만 |
| 겨울 과다발주 | 25도+ 구간만 적용 |
| 즉시 롤백 | Feature Flag 2개 (ROLLING_BIAS / BEVERAGE_TEMP_PRIORITY) |

## 4. 기대 효과

- 전체 MAE: 0.86~1.21 → **0.7~0.97** (20%+ 개선)
- 46704/47863 편향: 31~33% → **10% 이내**
- Stacking 활성 매장: 0개 → **1개+**
- 음료 MAE: 1.1~4.66 → **0.8~2.5**

## 5. 수정 파일

| 파일 | 변경 |
|------|------|
| `src/settings/constants.py` | Flag 2개 + BEVERAGE_TEMP_SENSITIVITY |
| `src/analysis/stacking_predictor.py` | 임계값 100 + COALESCE |
| `src/prediction/improved_predictor.py` | _get_rolling_bias() + bias적용 + clamp |
| `src/prediction/coefficient_adjuster.py` | 음료 기온 우선 분기 |

## 6. 전문가 토론 이력

- **토론 1** (예측율 개선 아이디어): DS/운영/ML 3명 → Quick Win 3개 도출
- **토론 2** (상권별 모델): DS/ML/실용 3명 → "4매장에서 과잉, Quick Win 먼저"
- **토론 3** (Design 리뷰): 악마/SRE 2명 → 이중증폭, 피드백루프, Feature Flag 합의
