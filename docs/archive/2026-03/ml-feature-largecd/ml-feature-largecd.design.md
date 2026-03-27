# Design: ML Feature - large_cd (대분류 그룹 피처)

## 1. 피처 설계

### 1.1 슈퍼그룹 정의

`LARGE_CD_SUPERGROUPS` 상수로 large_cd 18종을 5개 슈퍼그룹으로 매핑:

```python
LARGE_CD_SUPERGROUPS = {
    "lcd_food":      ["01", "02"],                    # 간편식사, 신선식품
    "lcd_snack":     ["11", "12", "13", "14"],        # 과자, 제과, 아이스크림, 빵
    "lcd_grocery":   ["21", "22", "23"],              # 가공식품, 조미료, 면류
    "lcd_beverage":  ["31", "33", "34"],              # 음료, 유제품, 커피
    "lcd_non_food":  ["41", "42", "43", "44", "45", "91"],  # 생활용품, 잡화, 기타
}
```

역매핑 딕셔너리 `_LARGE_CD_LOOKUP`: large_cd -> 슈퍼그룹명

### 1.2 FEATURE_NAMES 추가 (5개)

인덱스 36~40에 추가:

```python
# 대분류 슈퍼그룹 원핫 (2026-03-01 추가)
"is_lcd_food",       # 간편식사/신선 (large_cd: 01,02)
"is_lcd_snack",      # 과자/제과/빵 (large_cd: 11,12,13,14)
"is_lcd_grocery",    # 가공식품/면류 (large_cd: 21,22,23)
"is_lcd_beverage",   # 음료/유제품 (large_cd: 31,33,34)
"is_lcd_non_food",   # 생활용품/잡화 (large_cd: 41~45,91)
```

### 1.3 unknown 처리

- large_cd가 NULL이거나 매핑 없는 경우: **all-zero 벡터** (어떤 그룹에도 속하지 않음)
- 기존 mid_cd 그룹 원핫은 유지 (2계층 카테고리 표현)

## 2. 코드 변경 상세

### 2.1 feature_builder.py

**변경 1: 상수 추가**
```python
LARGE_CD_SUPERGROUPS = {
    "lcd_food": ["01", "02"],
    "lcd_snack": ["11", "12", "13", "14"],
    "lcd_grocery": ["21", "22", "23"],
    "lcd_beverage": ["31", "33", "34"],
    "lcd_non_food": ["41", "42", "43", "44", "45", "91"],
}

_LARGE_CD_LOOKUP = {}
for _sg_name, _lg_codes in LARGE_CD_SUPERGROUPS.items():
    for _lg_code in _lg_codes:
        _LARGE_CD_LOOKUP[_lg_code] = _sg_name

def get_large_cd_supergroup(large_cd: Optional[str]) -> Optional[str]:
    """large_cd로 슈퍼그룹 반환 (없으면 None)"""
    if not large_cd:
        return None
    return _LARGE_CD_LOOKUP.get(large_cd.zfill(2))
```

**변경 2: FEATURE_NAMES에 5개 추가** (인덱스 36~40)

**변경 3: build_features() 시그니처 확장**
```python
def build_features(
    ...,
    large_cd: Optional[str] = None,   # 신규 파라미터
) -> Optional[np.ndarray]:
```

**변경 4: build_features() 내부 원핫 인코딩 로직**
```python
# 대분류 슈퍼그룹 원핫
_lcd_sg = get_large_cd_supergroup(large_cd)
_is_lcd_food = 1.0 if _lcd_sg == "lcd_food" else 0.0
_is_lcd_snack = 1.0 if _lcd_sg == "lcd_snack" else 0.0
_is_lcd_grocery = 1.0 if _lcd_sg == "lcd_grocery" else 0.0
_is_lcd_beverage = 1.0 if _lcd_sg == "lcd_beverage" else 0.0
_is_lcd_non_food = 1.0 if _lcd_sg == "lcd_non_food" else 0.0
```

**변경 5: features 배열 끝에 5개 값 추가**

### 2.2 data_provider.py

**변경: get_product_info() SQL에 pd.large_cd 추가**
```sql
SELECT p.item_cd, p.item_nm, p.mid_cd,
       pd.expiration_days, pd.order_unit_qty, pd.sell_price,
       pd.margin_rate, pd.lead_time_days, pd.orderable_day,
       pd.large_cd   -- 추가
FROM products p
LEFT JOIN product_details pd ON p.item_cd = pd.item_cd
WHERE p.item_cd = ?
```

반환 딕셔너리에 `"large_cd": row[9]` 추가.

### 2.3 data_pipeline.py

**변경: get_items_meta()에서 large_cd 포함**

`product_details` 쿼리에 `large_cd` 컬럼 추가,
반환 딕셔너리에 `"large_cd": row["large_cd"]` 포함.

### 2.4 trainer.py

**변경: _prepare_training_data()에서 sample에 large_cd 전달**
```python
sample = {
    ...
    "large_cd": meta.get("large_cd"),  # 추가
}
```

**변경: train_all_groups()에서 build_features 호출 시 large_cd 전달**
```python
feat = MLFeatureBuilder.build_features(
    ...,
    large_cd=s.get("large_cd"),
)
```

### 2.5 improved_predictor.py

**변경: _apply_ml_ensemble()에서 large_cd 전달**
```python
features = MLFeatureBuilder.build_features(
    ...,
    large_cd=product.get("large_cd"),  # 추가
)
```

### 2.6 model.py

변경 없음. `_feature_hash()`가 FEATURE_NAMES 변경을 자동 감지.

## 3. 기존 모델 호환성

### 흐름도
```
FEATURE_NAMES 변경 (36→41)
  ↓
feature_hash 변경 (md5 8자리)
  ↓
MLPredictor._load_from_dir(): stale_groups에 추가
  ↓
모델 로드 스킵 → predict() returns None
  ↓
ImprovedPredictor: ML 앙상블 비활성 → 규칙 기반만 사용
  ↓
다음 학습 스케줄에서 41개 피처로 재학습
  ↓
새 모델 저장 (feature_hash 일치) → ML 앙상블 재활성
```

### 안전성
- 모델 파일은 삭제되지 않음 (_prev 백업 유지)
- 재학습 전까지 규칙 기반만 동작 (기존과 동일 품질)
- 수동 즉시 재학습 가능: `python run_scheduler.py --ml-train`

## 4. 테스트 설계

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | test_large_cd_supergroup_mapping | 슈퍼그룹 매핑 정확성 |
| 2 | test_large_cd_unknown_returns_none | NULL/미매핑 large_cd -> None |
| 3 | test_feature_names_count_41 | FEATURE_NAMES 길이 41 |
| 4 | test_build_features_with_large_cd | large_cd="01" -> food 원핫 활성 |
| 5 | test_build_features_with_snack_large_cd | large_cd="12" -> snack 원핫 활성 |
| 6 | test_build_features_with_beverage_large_cd | large_cd="31" -> beverage 원핫 활성 |
| 7 | test_build_features_without_large_cd | large_cd=None -> all-zero |
| 8 | test_build_features_unknown_large_cd | large_cd="99" -> all-zero |
| 9 | test_feature_array_length_41 | 생성된 배열 길이 41 |
| 10 | test_feature_hash_changes | FEATURE_NAMES 변경 시 hash 변경 |
| 11 | test_build_batch_features_with_large_cd | batch 생성 시 large_cd 전달 |
| 12 | test_large_cd_zero_padded | large_cd="1" -> "01" 패딩 처리 |
| 13 | test_mutual_exclusion_lcd_groups | 하나의 large_cd에 하나의 그룹만 활성 |
| 14 | test_lcd_and_midcd_groups_independent | lcd 원핫과 mid_cd 원핫 독립 |
| 15 | test_model_predict_with_41_features | 41개 피처로 모델 예측 |
| 16 | test_trainer_passes_large_cd | trainer가 large_cd 전달 확인 |
| 17 | test_data_provider_returns_large_cd | get_product_info()에 large_cd 포함 |
| 18 | test_data_pipeline_meta_includes_large_cd | get_items_meta()에 large_cd 포함 |
