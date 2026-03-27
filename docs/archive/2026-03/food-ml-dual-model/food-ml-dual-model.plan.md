# Plan: food-ml-dual-model

## 배경

### 문제
- 푸드(001~005,012) 상품의 ML 학습 가능 비율이 **26.8%** (145/542)로 전 카테고리 최저
- 유통기한 1~3일, 신제품 진입/퇴출이 빈번 → 14일+ 데이터 축적 전에 수십 번 발주 완료
- 현재 ML은 **상품별 개별 학습**만 지원 → 데이터 부족 상품은 ML 혜택 없음
- `_get_ml_weight()`에서 data_days < 30이면 ML 가중치 = 0.0 (완전 미사용)

### 현재 구조
```
상품별 daily_sales → 31개 피처 → 그룹 모델(food_group) → predict
                                    ↑
                          개별 상품의 이력만 학습
```

### 제안: 이중 모델 (Individual + Group)
```
모델1(개별): 상품 A의 이력 → food_group 모델 → pred_individual
모델2(그룹): 동일 small_cd 전체 이력 → group 모델 → pred_group

블렌딩: data_confidence = min(1.0, data_days / 60)
  → final = confidence × pred_individual + (1-confidence) × pred_group

효과: 데이터 0일 신제품도 small_cd 그룹 패턴으로 즉시 ML 예측 가능
```

## 목표

1. small_cd 기반 **그룹 모델** 추가 (기존 개별 모델과 병행)
2. data_confidence 기반 자동 블렌딩 (데이터 적으면 그룹 모델 비중 UP)
3. 푸드 ML 최소 data_days 요건 완화 (30일 → 7일, 그룹 모델 폴백)
4. 기존 improved_predictor.py 블렌딩 로직 **변경 없음** (ML 내부에서 처리)

## 성공 기준

| 기준 | 목표 |
|------|------|
| 푸드 ML 적용 가능 상품 비율 | 26.8% → **60%+** |
| 기존 모델 성능 | 악화 없음 (성능 게이트 통과) |
| 그룹 모델 cold start | data_days=0 상품도 그룹 모델로 예측 |
| 전체 테스트 | 기존 2794개 통과 + 신규 테스트 |
| food_group MAE | 현재 수준 유지 또는 개선 |

## 수정 대상

### 1. `src/prediction/ml/feature_builder.py` — 그룹 모델용 피처 추가

**추가 피처 (4개, FEATURE_NAMES 31→35)**:
```python
# 그룹 컨텍스트 피처 (food-ml-dual-model)
"data_days_ratio",      # 데이터 충분도: actual_days / 60 (cap 1.0)
"smallcd_peer_avg",     # 동일 small_cd 7일 평균 매출
"relative_position",    # 내 매출 / 피어 평균 (1.0=평균, >1=인기상품)
"lifecycle_stage",      # 0=신제품, 0.5=모니터링, 1.0=안정
```

**변경 사항**:
- `FEATURE_NAMES` 리스트에 4개 추가 (인덱스 31~34)
- `build_features()` 파라미터 추가: `data_days`, `smallcd_peer_avg`, `relative_position`, `lifecycle_stage`
- `build_features()` 내부에서 정규화 계산
- `build_batch_features()`에 동일 파라미터 전달

### 2. `src/prediction/ml/trainer.py` — 그룹 모델 학습 추가

**새 메서드**: `_prepare_group_training_data(days)`
- small_cd 기반 그룹 데이터 구성
- 동일 small_cd의 모든 상품 이력을 하나의 학습 풀로 합침
- 그룹 컨텍스트 피처 (peer_avg, relative_position) 계산
- small_cd당 최소 50 샘플 충족 시 학습, 미충족 시 mid_cd로 폴백

**새 메서드**: `train_group_models(days)`
- food_group 내 small_cd별 그룹 모델 학습
- small_cd당 상품 수 부족 시 mid_cd 그룹핑으로 폴백
- 모델 저장: `data/models/{store_id}/group_{small_cd}.joblib`
- 메타데이터: `model_meta.json`에 `group_models` 섹션 추가

**기존 `train_all_groups()` 변경**:
- 마지막에 `train_group_models()` 호출 추가
- 그룹 모델 학습 결과도 results에 포함

**학습 윈도우 분리**:
```python
GROUP_TRAINING_DAYS = {
    "food_group": 30,       # 푸드 그룹 모델: 30일 (회전 빠름)
    "default": 90,          # 기타: 기존 유지
}
```

### 3. `src/prediction/ml/model.py` — 그룹 모델 로드/예측

**`MLPredictor` 확장**:
- `self.group_models: Dict[str, Any]` — small_cd별 그룹 모델 저장
- `load_group_models()` — `group_*.joblib` 파일 로드
- `predict_group(features, small_cd)` — 그룹 모델 예측
- `predict_dual(features, mid_cd, small_cd, data_days)` — **핵심 메서드**
  ```python
  def predict_dual(self, features, mid_cd, small_cd, data_days):
      # 개별 모델 예측
      pred_individual = self.predict(features, mid_cd)
      # 그룹 모델 예측
      pred_group = self.predict_group(features, small_cd)

      if pred_individual is None and pred_group is None:
          return None
      if pred_individual is None:
          return pred_group   # cold start → 그룹 모델만
      if pred_group is None:
          return pred_individual  # 그룹 모델 없음 → 개별만

      # 블렌딩
      confidence = min(1.0, data_days / 60.0)
      return confidence * pred_individual + (1 - confidence) * pred_group
  ```

**모델 파일 구조**:
```
data/models/{store_id}/
  ├── model_food_group.joblib        # 기존 개별 모델
  ├── model_food_group_prev.joblib   # 백업
  ├── group_001_A.joblib             # small_cd 그룹 모델 (예: 도시락)
  ├── group_003_B.joblib             # small_cd 그룹 모델 (예: 김밥)
  ├── group_mid_001.joblib           # mid_cd 폴백 그룹 모델
  └── model_meta.json                # 메타데이터 (기존 + group_models 섹션)
```

### 4. `src/prediction/improved_predictor.py` — ML 호출 경로 변경

**`_apply_ml_ensemble()` 수정** (라인 2248~2369):
- `self._ml_predictor.predict()` → `self._ml_predictor.predict_dual()` 호출
- `small_cd` 파라미터 추가 전달 (product dict에서 조회)
- `data_days` 전달 (이미 파라미터로 존재)
- 그룹 컨텍스트 피처 전달: `data_days`, `smallcd_peer_avg` 등

**`_get_ml_weight()` 수정** (라인 2384~2408):
- food_group에서 그룹 모델 사용 시 **data_days 최소 요건 완화**
  ```python
  if data_days < 30:
      if is_food_category(mid_cd) and self._ml_predictor.has_group_model(small_cd):
          return 0.1  # 그룹 모델 폴백 → 낮은 가중치로 참여
      return 0.0  # 그룹 모델 없으면 기존대로 미사용
  ```

**그룹 컨텍스트 피처 캐시**:
- `_smallcd_peer_cache: Dict[str, float]` — Phase 1.7 시작 시 일괄 계산
- `_init_smallcd_peer_cache()` — 예측 루프 전 1회 호출

### 5. `src/prediction/ml/data_pipeline.py` — 그룹 데이터 조회

**새 메서드**:
- `get_smallcd_group_stats(small_cd, days)` — small_cd별 상품 목록 + 일별 매출 집계
- `get_smallcd_peer_avg_batch(store_id)` — 전체 small_cd별 7일 평균 일괄 조회
- `get_product_lifecycle_stage(item_cd)` — detected_new_products 테이블에서 라이프사이클 단계 조회

### 6. `tests/test_food_ml_dual_model.py` — 신규 테스트

| 테스트 | 검증 내용 |
|--------|----------|
| test_group_feature_builder | 4개 추가 피처 정규화 검증 |
| test_feature_count_35 | FEATURE_NAMES 31→35 |
| test_predict_dual_both_available | 개별+그룹 모두 있을 때 블렌딩 |
| test_predict_dual_individual_only | 개별 모델만 → 개별 결과 반환 |
| test_predict_dual_group_only | 그룹 모델만 (cold start) → 그룹 결과 |
| test_predict_dual_neither | 모두 없음 → None |
| test_confidence_formula | data_days별 confidence 계산 검증 |
| test_confidence_0_days | 0일 → confidence=0 → 그룹 100% |
| test_confidence_60_days | 60일+ → confidence=1 → 개별 100% |
| test_confidence_30_days | 30일 → 50:50 블렌딩 |
| test_group_training_data_prep | small_cd 기반 학습 데이터 구성 |
| test_group_model_save_load | 그룹 모델 저장/로드 |
| test_group_model_fallback_to_mid | small_cd 샘플 부족 → mid_cd 폴백 |
| test_ml_weight_food_with_group | food + 그룹 모델 → data_days<30도 참여 |
| test_ml_weight_food_without_group | food + 그룹 모델 없음 → 기존 0.0 |
| test_existing_models_unchanged | 비푸드 그룹 기존 동작 보존 |
| test_peer_avg_cache | smallcd_peer_cache 일괄 조회 |
| test_lifecycle_stage_feature | 라이프사이클 단계 피처 값 |
| test_meta_includes_group | model_meta.json에 group_models 기록 |
| test_performance_gate_group | 그룹 모델도 성능 게이트 적용 |

### 7. 기존 테스트 수정 (피처 수 변경 31→35)

| 파일 | 수정 내용 |
|------|----------|
| `tests/test_ml_predictor.py` | shape (5, 31) → (5, 35) |
| `tests/test_receiving_pattern_features.py` | len(features)==31→35, 인덱스 유지 (0~30 변경 없음) |
| `tests/test_ml_improvement.py` | 피처 수 관련 assertion 업데이트 |

## 수정하지 않는 것

- `improved_predictor.py`의 통계↔ML 외부 블렌딩 공식 (`(1-w)*rule + w*ml`)
- 비푸드 그룹 모델 (alcohol, tobacco, perishable, general) — 변경 없음
- `GROUP_QUANTILE_ALPHA` — 그룹 모델도 동일 alpha 사용
- `_EnsembleModel` 구조 (RF + GB) — 그룹 모델도 동일 앙상블
- `ml_training_logs` 테이블 — 그룹 모델 학습도 동일 테이블에 기록

## 구현 순서

1. `feature_builder.py` — 4개 피처 추가 (FEATURE_NAMES 31→35)
2. `data_pipeline.py` — 그룹 데이터 조회 메서드 추가
3. `model.py` — 그룹 모델 로드/예측/`predict_dual()` 추가
4. `trainer.py` — 그룹 모델 학습 루틴 추가
5. `improved_predictor.py` — ML 호출 경로 변경 + 피어 캐시
6. `tests/test_food_ml_dual_model.py` — 신규 테스트 20개
7. 기존 테스트 피처 수 업데이트 (31→35)
8. `pytest tests/` — 전체 테스트 통과 확인

## 리스크

| 리스크 | 대응 |
|--------|------|
| small_cd당 상품 수 부족 (학습 불가) | mid_cd 폴백 그룹 모델 |
| 모델 파일 수 증가 (small_cd당 1개) | 푸드 small_cd ~30개 → 관리 가능 |
| 학습 시간 증가 | 그룹 모델은 소규모 → 수 초 추가 |
| 피처 수 변경 → 기존 모델 호환 깨짐 | feature_hash 변경 감지 → 자동 재학습 |
| 그룹 모델 과적합 | 성능 게이트 (MAE 20% + Acc@1 5%p) 적용 |
