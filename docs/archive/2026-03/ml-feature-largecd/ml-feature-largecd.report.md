# Report: ML Feature - large_cd (대분류 그룹 피처)

## 요약

| 항목 | 값 |
|------|-----|
| 기능 | ML 앙상블 모델에 large_cd(대분류) 기반 슈퍼그룹 원핫 피처 추가 |
| FEATURE_NAMES | 36개 -> 41개 (+5개) |
| 슈퍼그룹 | lcd_food, lcd_snack, lcd_grocery, lcd_beverage, lcd_non_food |
| Match Rate | 100% (설계 18/18 항목 일치) |
| 테스트 | 31개 신규 + 38개 기존 = 69개 전체 통과 |
| 수정 파일 | 5개 기존 파일 수정 + 1개 테스트 신규 + 1개 테스트 수정 |

## 구현 범위

### 신규 코드
- `LARGE_CD_SUPERGROUPS`: large_cd 18종 -> 5개 슈퍼그룹 매핑 상수
- `get_large_cd_supergroup()`: large_cd -> 슈퍼그룹 변환 함수 (zfill 패딩, NULL 안전)
- FEATURE_NAMES 5개 추가: `is_lcd_food`, `is_lcd_snack`, `is_lcd_grocery`, `is_lcd_beverage`, `is_lcd_non_food`

### 데이터 파이프라인
- `data_provider.py`: `get_product_info()` SQL에 `pd.large_cd` 추가
- `data_pipeline.py`: `get_items_meta()` SQL에 `large_cd` 추가
- `trainer.py`: 학습 sample에 `large_cd` 전달, `build_features()` 호출 시 `large_cd` 전달
- `improved_predictor.py`: ML 앙상블 호출 시 `large_cd=product.get("large_cd")` 전달

### 호환성
- 기존 모델: feature_hash 자동 변경 -> stale 감지 -> 재학습 전까지 규칙 기반 폴백
- 기존 코드: `large_cd=None` 기본값으로 하위 호환 유지 (all-zero 원핫)
- 기존 테스트: 3건 피처 수 하드코딩값 수정 (36 -> 41)

## 슈퍼그룹 매핑 상세

| 슈퍼그룹 | large_cd | 설명 | 특성 |
|---------|----------|------|------|
| lcd_food | 01, 02 | 간편식사, 신선식품 | 유통기한 단기, 날씨/요일 민감 |
| lcd_snack | 11, 12, 13, 14 | 과자, 제과, 아이스크림, 빵 | 계절성, 프로모션 민감 |
| lcd_grocery | 21, 22, 23 | 가공식품, 조미료, 면류 | 안정 수요, 비상수요 패턴 |
| lcd_beverage | 31, 33, 34 | 음료, 유제품, 커피 | 기온 민감, 강한 계절성 |
| lcd_non_food | 41~45, 91 | 생활용품, 잡화, 기타 | 안정 수요, 프로모션 약감응 |

## 테스트 상세 (31개)

### TestLargeCdSupergroup (10개)
- 5개 슈퍼그룹 매핑 정확성
- None/empty/unknown -> None 반환
- zfill(2) 패딩 처리
- 중복 코드 없음 검증

### TestMLFeatureBuilderLargeCd (14개)
- FEATURE_NAMES 41개, 피처명 포함, 위치 확인
- 각 슈퍼그룹별 원핫 활성 검증 (food/snack/beverage/grocery/non_food)
- NULL/unknown large_cd -> all-zero
- 상호 배타성 (mutual exclusion)
- mid_cd 그룹과 독립성
- 하위 호환성 (large_cd 미지정)

### TestBatchFeaturesLargeCd (1개)
- 배치 빌드 시 large_cd 전달 + shape 검증

### TestModelCompatLargeCd (2개)
- feature_hash 변경 확인
- 41개 피처로 모델 예측 가능

### TestDataProviderLargeCd (2개)
- get_product_info()에 large_cd 포함 / NULL 안전

### TestDataPipelineLargeCd (2개)
- get_items_meta()에 large_cd 포함 / NULL 안전

## 후속 작업

1. **모델 재학습**: `python run_scheduler.py --ml-train` 실행하여 41개 피처로 재학습
2. **Feature Importance 모니터링**: 학습 후 lcd_* 피처의 importance 순위 확인
3. **예측 정확도 비교**: 재학습 전후 MAE/MAPE 변화 추적
