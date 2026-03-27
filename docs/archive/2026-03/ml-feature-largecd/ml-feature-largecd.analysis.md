# Analysis: ML Feature - large_cd (대분류 그룹 피처)

## 1. 구현 vs 설계 비교

| 항목 | 설계 | 구현 | 일치 |
|------|------|------|------|
| 슈퍼그룹 정의 (5개) | lcd_food/snack/grocery/beverage/non_food | 동일 | O |
| large_cd 18종 매핑 | 01,02/11~14/21~23/31,33,34/41~45,91 | 동일 | O |
| FEATURE_NAMES 수 | 36 -> 41 | 41개 | O |
| 피처명 접두사 | is_lcd_* | is_lcd_food/snack/grocery/beverage/non_food | O |
| 피처 위치 | 인덱스 36~40 | 인덱스 36~40 | O |
| NULL/unknown 처리 | all-zero 벡터 | all-zero (get_large_cd_supergroup -> None) | O |
| zfill(2) 패딩 | large_cd "1" -> "01" | zfill(2) 적용 | O |
| build_features() 시그니처 | large_cd: Optional[str] = None | 동일 | O |
| data_provider.py SQL 수정 | pd.large_cd 추가 | row[9] -> "large_cd" | O |
| data_pipeline.py meta 수정 | large_cd 포함 | "large_cd": row["large_cd"] | O |
| trainer.py sample 전달 | "large_cd": meta.get("large_cd") | 동일 | O |
| trainer.py build_features 호출 | large_cd=s.get("large_cd") | 동일 | O |
| improved_predictor.py 전달 | large_cd=product.get("large_cd") | 동일 | O |
| build_batch_features 전달 | large_cd=item.get("large_cd") | 동일 | O |
| 기존 모델 호환성 | feature_hash 변경 -> stale -> 재학습 필요 | 동일 | O |
| 테스트 수 (설계) | 18개 | 31개 (13개 추가) | O+ |

## 2. Match Rate

**설계 항목 18개 중 18개 일치 = Match Rate 100%**

추가 구현:
- 기존 테스트 3건 수정 (36->41 피처 수 반영)
- 설계보다 13개 더 많은 테스트 (31개)

## 3. 수정 파일 목록

| 파일 | 변경 유형 | 변경 내용 |
|------|----------|----------|
| `src/prediction/ml/feature_builder.py` | 수정 | LARGE_CD_SUPERGROUPS, get_large_cd_supergroup(), FEATURE_NAMES 5개 추가, build_features() large_cd 파라미터+원핫, build_batch_features() large_cd 전달 |
| `src/prediction/data_provider.py` | 수정 | get_product_info() SQL에 pd.large_cd 추가, 반환 딕셔너리에 large_cd 포함 |
| `src/prediction/ml/data_pipeline.py` | 수정 | get_items_meta() SQL에 large_cd 추가, 반환 딕셔너리에 large_cd 포함 |
| `src/prediction/ml/trainer.py` | 수정 | _prepare_training_data() sample에 large_cd 추가, train_all_groups() build_features 호출 시 large_cd 전달 |
| `src/prediction/improved_predictor.py` | 수정 | _apply_ml_ensemble()에서 build_features 호출 시 large_cd=product.get("large_cd") 추가 |
| `tests/test_ml_feature_largecd.py` | 신규 | 31개 테스트 (매핑 10 + 피처빌더 14 + 배치 1 + 모델 2 + 데이터 4) |
| `tests/test_ml_predictor.py` | 수정 | 피처 수 36->41 반영 (3건) |

## 4. 테스트 결과

```
tests/test_ml_feature_largecd.py: 31 passed
tests/test_ml_predictor.py: 27 passed
tests/test_ml_daily_training.py: 11 passed
--------------------------------------------
총 69 tests: 69 passed, 0 failed
```

## 5. 리스크 분석

| 리스크 | 상태 | 완화 조치 |
|--------|------|----------|
| 기존 모델 무효화 | 예상대로 작동 | feature_hash 변경 -> 자동 감지 -> 재학습 전까지 규칙 기반 폴백 |
| large_cd NULL 상품 | 안전 | all-zero 벡터 (기본값) |
| 피처 수 증가로 과적합 | 낮음 | 5개만 추가, 트리 기반 모델이므로 희소 피처 내성 높음 |
| 기존 테스트 호환 | 해결 | 3건 수정 (하드코딩된 36 -> 41) |
