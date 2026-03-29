# Plan: 예측 정확도 Quick Win 3가지

## 1. 배경 및 문제 정의

### 현재 문제
- 46704(2호점): MAE=1.21, 예측/판매 비율 69% (**31% 과소**)
- 47863(신규점): MAE=1.19, 예측/판매 비율 67% (**33% 과소**)
- ML 앙상블(RF+GB)이 **전 매장 미작동** — Stacking 임계값 200행 미달
- 음료류(010,046,049) 공통적으로 과소 — 카테고리 기온 민감도 미반영

### 근거 데이터
- 예측 vs 실제판매 60일 분석 (3매장)
- 사용자 수정 패턴 분석 (자동발주의 74% 수량 유지, 19% 증량, 7% 감량)
- 전문가 토론 3회 (DS/운영/ML) 합의

### 목표
- 전체 MAE **20% 이상 개선** (1.21→0.97 수준)
- 46704/47863 과소편향 **31~33% → 10% 이내**
- ML 앙상블 **최소 1개 매장 이상 활성화**

## 2. Quick Win 3가지

### QW-1: Rolling Bias Multiplier (매장별 편향 자동 보정)

**문제**: 46704/47863이 전 카테고리에서 과소 → 매장 수준 고정 편향 존재
**해법**: 최근 14일 median(실제/예측) 비율로 예측값을 자동 보정
**삽입 지점**: `improved_predictor.py` L2758 (`blended` 계산 후, `order_qty` 결정 전)

```python
# 의사코드
bias_ratio = median(actual_sale / predicted_qty) for last 14 days  # 매장×카테고리별
blended *= clamp(bias_ratio, 0.7, 1.5)  # 과도한 보정 방지
```

**데이터 소스**: `prediction_logs.predicted_qty` + `daily_sales.sale_qty` JOIN
**예상 효과**: 46704 과소 31% → 5% 이내 (2주 수렴)

### QW-2: ML 앙상블 활성화 (Stacking 임계값 조정)

**문제**: Stacking MIN_TRAIN_SAMPLES=200 → 전 매장 미달 → ML 미작동
**해법**: 임계값 200→100 하향 + 4매장 합산 학습 옵션 추가

| 매장 | 현재 행수 | 200 기준 | 100 기준 |
|------|:---:|:---:|:---:|
| 46513 | 0 | ❌ | ❌ (데이터 구조 문제?) |
| 46704 | 0 | ❌ | ❌ |
| 47863 | 184 | ❌ | ✅ |

**추가 조치**: Stacking 학습 데이터가 0인 원인 조사 필요 (매장 데이터는 있는데 왜 0행?)
**삽입 지점**: `stacking_predictor.py` L48
**예상 효과**: 47863 즉시 활성화 + 나머지 매장 데이터 문제 해결 시 전체 활성화

### QW-3: 음료 기온 계수 강화

**문제**: 010(제조음료) -74%, 046(요구르트) -53%, 049(맥주) -34% 과소
**해법**: 기존 WEATHER_COEFFICIENTS에 음료 카테고리 민감도 상향
**삽입 지점**: `coefficient_adjuster.py` L336-385 (이미 카테고리 분기 있음, 값만 조정)

```python
# 현재: 전 카테고리 동일 기온 계수
# 변경: 음료 카테고리 별도 강화
BEVERAGE_TEMP_BOOST = {
    "010": 1.3,  # 제조음료: 기온 효과 30% 증폭
    "046": 1.2,  # 요구르트: 20% 증폭
    "049": 1.4,  # 맥주: 40% 증폭 (여름 피크 대비)
}
```

**예상 효과**: 음료류 MAE 1.1~4.66 → 0.8~2.5 (기온 상승기에 효과 극대)

## 3. 수정 파일 목록

| QW | 파일 | 변경 |
|----|------|------|
| 1 | `src/prediction/improved_predictor.py` | bias_ratio 계산 + 적용 (~30줄) |
| 1 | `src/infrastructure/database/repos/prediction_log_repo.py` | get_bias_ratio() 메서드 추가 |
| 2 | `src/analysis/stacking_predictor.py` | MIN_TRAIN_SAMPLES 200→100 |
| 2 | (조사) stacking 학습 데이터 0행 원인 | 데이터 파이프라인 디버깅 |
| 3 | `src/prediction/coefficient_adjuster.py` | BEVERAGE_TEMP_BOOST 상수 + 적용 |
| 3 | `src/settings/constants.py` | BEVERAGE_TEMP_BOOST 상수 정의 |

## 4. 구현 순서

```
Step 1: Stacking 학습 데이터 0행 원인 조사 + 임계값 조정 (QW-2)
Step 2: Rolling Bias Multiplier 구현 (QW-1)
Step 3: 음료 기온 계수 강화 (QW-3)
Step 4: 테스트 실행 + 효과 측정
Step 5: 커밋
```

**Step 1을 먼저 하는 이유**: ML이 작동해야 QW-1의 bias 보정도 의미 있고, QW-3의 기온 계수도 ML 피처와 연동됨

## 5. 리스크

| 리스크 | 완화 |
|--------|------|
| Bias Multiplier가 과보정 | clamp(0.7, 1.5)로 제한 |
| Stacking 임계값 낮추면 과적합 | holdout 7일 검증 유지 |
| 기온 계수 과다 → 여름 과다발주 | 시즌별 A/B 테스트 후 조정 |

## 6. 성공 지표

- 전체 MAE: 0.86~1.21 → **0.7~0.97** (20% 개선)
- 46704/47863 편향: 31~33% → **10% 이내**
- Stacking 활성 매장: 0개 → **1개 이상**
- 음료류 MAE: 1.1~4.66 → **0.8~2.5**
