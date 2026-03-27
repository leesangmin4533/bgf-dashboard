# Plan: ML Feature - large_cd (대분류 그룹 피처)

## 1. 목적

ML 앙상블 예측 모델에 **large_cd(대분류, 18종)** 기반 피처를 추가하여
카테고리 대분류별 계절/날씨/요일 반응 패턴 차이를 모델이 학습할 수 있게 한다.

### 배경
- 현재 FEATURE_NAMES **36개** (31 기본 + 5 입고패턴)
- 기존 카테고리 피처는 **mid_cd 기반 5개 그룹** 원핫 (food/alcohol/tobacco/perishable/general)
- 문제: general_group에 스낵, 음료, 잡화 등 이질적인 카테고리가 모두 포함됨
- large_cd(대분류)를 활용하면 더 세밀한 카테고리 구분 가능

### 기대 효과
- 간편식사(01) vs 과자류(12) vs 음료(31)의 계절/날씨/요일 반응 차이 반영
- general_group 내부의 이질성 해소
- 모델 정확도 향상 (특히 음료/스낵 등 계절성 강한 카테고리)

## 2. 현재 피처 구조

### FEATURE_NAMES (36개)
| 인덱스 | 피처명 | 설명 |
|--------|--------|------|
| 0-4 | daily_avg_7/14/31, stock_qty, pending_qty | 기본 통계 |
| 5-6 | trend_score, cv | 트렌드/변동 |
| 7-12 | weekday_sin/cos, month_sin/cos, is_weekend, is_holiday | 시간 |
| 13 | promo_active | 행사 |
| 14-18 | is_food/alcohol/tobacco/perishable/general_group | 카테고리 원핫 |
| 19-21 | expiration_days, disuse_rate, margin_rate | 비즈니스 |
| 22-23 | temperature, temperature_delta | 외부 요인 |
| 24-26 | lag_7, lag_28, week_over_week | Lag |
| 27 | association_score | 연관 |
| 28-30 | holiday_period_days, is_pre_holiday, is_post_holiday | 연휴 |
| 31-35 | lead_time_avg/cv, short_delivery_rate, delivery_frequency, pending_age_days | 입고 |

### DB 현황
- `product_details.large_cd` (TEXT, 18종, 5,214개 수집 완료)
- `mid_categories.large_cd` (TEXT, 중분류-대분류 매핑)
- 인덱스: `idx_product_details_large`, `idx_mid_categories_large`

## 3. large_cd 피처 추가 방안

### 선택지 비교

| 방안 | 피처 수 | 장점 | 단점 |
|------|---------|------|------|
| A. 5개 슈퍼그룹 원핫 | +5 (41개) | mid_cd 그룹보다 세밀, 과적합 방지 | 18종을 5개로 축소 시 정보 손실 |
| B. 18종 원핫 | +18 (54개) | 완전한 구분 | 희소 피처, 과적합 위험 |
| C. 1개 연속 피처 (대분류별 평균 매출 비율) | +1 (37개) | 피처 최소 | 카테고리 고유 특성 표현 불가 |

### 채택: 방안 A (5개 슈퍼그룹 원핫)
- large_cd 18종을 5개 슈퍼그룹으로 매핑
- 기존 mid_cd 그룹(5개)과 직교 → 카테고리 특성 2계층 표현
- FEATURE_NAMES: 36 -> 41개

### 슈퍼그룹 매핑
| 슈퍼그룹 | large_cd | 설명 |
|---------|----------|------|
| food | 01, 02 | 간편식사, 신선식품 |
| snack | 11, 12, 13, 14 | 과자, 제과, 아이스크림, 빵 |
| grocery | 21, 22, 23 | 가공식품, 조미료, 면류 |
| beverage | 31, 33, 34 | 음료, 유제품, 커피 |
| non_food | 41, 42, 43, 44, 45, 91 | 생활용품, 잡화, 기타 |

## 4. 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/prediction/ml/feature_builder.py` | FEATURE_NAMES 5개 추가, build_features()에 large_cd 파라미터/로직 |
| `src/prediction/ml/model.py` | feature_hash 자동 갱신 (코드 변경 불필요) |
| `src/prediction/ml/trainer.py` | _prepare_training_data()에서 large_cd 전달 |
| `src/prediction/ml/data_pipeline.py` | get_items_meta()에 large_cd 포함 |
| `src/prediction/data_provider.py` | get_product_info()에 large_cd 포함 |
| `src/prediction/improved_predictor.py` | _apply_ml_ensemble()에서 large_cd 전달 |
| `tests/test_ml_feature_largecd.py` | 신규 테스트 |

## 5. 기존 모델 호환성

- FEATURE_NAMES 변경 시 `feature_hash`가 변경됨
- `MLPredictor._load_from_dir()`: hash 불일치 모델을 stale로 표시, 로드 스킵
- 자동 재학습 시 새 feature 구조로 학습된 모델로 교체
- 재학습 전까지 ML 앙상블 비활성 (규칙 기반만 사용) -> 안전한 폴백

## 6. 리스크

| 리스크 | 완화 |
|--------|------|
| large_cd NULL 상품 | 슈퍼그룹 "unknown"으로 매핑 (all-zero 원핫) |
| 기존 모델 무효화 | feature_hash 기반 자동 감지, 재학습 전 규칙 기반 폴백 |
| 피처 수 증가로 과적합 | 5개 그룹으로 축소, 트리 기반 모델이라 희소 피처 내성 있음 |
