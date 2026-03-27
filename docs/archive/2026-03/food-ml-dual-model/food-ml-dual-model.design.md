# Design: food-ml-dual-model

## 개요
푸드 카테고리 ML 이중 모델: 개별 모델(기존) + small_cd 그룹 모델(신규)을 data_confidence 기반으로 블렌딩하여 신제품 cold start 문제 해결.

## 아키텍처

```
improved_predictor._apply_ml_ensemble()
  │
  ├─ MLFeatureBuilder.build_features(35개 피처)
  │     [0~30] 기존 피처
  │     [31] data_days_ratio      ← 신규
  │     [32] smallcd_peer_avg     ← 신규
  │     [33] relative_position    ← 신규
  │     [34] lifecycle_stage      ← 신규
  │
  └─ MLPredictor.predict_dual(features, mid_cd, small_cd, data_days)
       ├─ predict(features, mid_cd)         → pred_individual
       ├─ predict_group(features, small_cd) → pred_group
       └─ confidence blend
            confidence = min(1.0, data_days / 60)
            final = confidence × individual + (1-confidence) × group
```

## 상세 설계

### 1. feature_builder.py

#### FEATURE_NAMES 확장 (31→35)
```python
FEATURE_NAMES = [
    # ... 기존 31개 유지 ...
    # 그룹 컨텍스트 (food-ml-dual-model)
    "data_days_ratio",      # [31] 데이터 충분도
    "smallcd_peer_avg",     # [32] 동일 소분류 평균 매출
    "relative_position",    # [33] 피어 대비 상대 위치
    "lifecycle_stage",      # [34] 라이프사이클 단계
]
```

#### build_features() 파라미터 추가
```python
def build_features(
    ...,  # 기존 파라미터 유지
    data_days: int = 0,
    smallcd_peer_avg: float = 0.0,
    lifecycle_stage: float = 1.0,
) -> Optional[np.ndarray]:
```

#### 정규화 규칙
| 피처 | 정규화 | 범위 |
|------|--------|------|
| data_days_ratio | data_days / 60, cap 1.0 | 0~1 |
| smallcd_peer_avg | peer_avg / 10.0, cap 1.0 | 0~1 (10개 이상이면 1.0) |
| relative_position | my_avg / peer_avg, cap 3.0, 0=정보없음 | 0~3 |
| lifecycle_stage | 직접값 (0=신제품, 0.5=모니터링, 1.0=안정) | 0~1 |

### 2. data_pipeline.py

#### 새 메서드 3개

```python
def get_smallcd_peer_avg_batch(self) -> Dict[str, float]:
    """전체 small_cd별 7일 평균 일괄 조회
    Returns: {small_cd: 7일 평균 판매량}
    """
    # common.db ATTACH → product_details.small_cd JOIN daily_sales
    # GROUP BY small_cd, AVG(sale_qty) WHERE sales_date >= -7 days

def get_item_smallcd_map(self) -> Dict[str, str]:
    """상품별 small_cd 매핑 일괄 조회
    Returns: {item_cd: small_cd}
    """
    # common.db product_details에서 조회

def get_lifecycle_stages_batch(self) -> Dict[str, float]:
    """상품별 라이프사이클 단계 일괄 조회
    Returns: {item_cd: stage_value}
    detected=0.0, monitoring=0.25, slow_start=0.5, stable=0.75, normal=1.0
    """
    # detected_new_products.lifecycle_status 조회
    # 없으면 1.0 (기존 상품 = 안정)
```

### 3. model.py — MLPredictor 확장

```python
class MLPredictor:
    def __init__(self, ...):
        self.models: Dict[str, Any] = {}        # 기존 그룹 모델
        self.group_models: Dict[str, Any] = {}   # 신규: small_cd/mid_cd 그룹 모델
        ...

    def load_group_models(self) -> int:
        """group_*.joblib 파일 로드. 반환: 로드된 수"""

    def predict_group(self, features: np.ndarray, small_cd: str) -> Optional[float]:
        """그룹 모델 예측. small_cd 모델 → mid_cd 폴백"""
        model = self.group_models.get(f"small_{small_cd}")
        if model is None:
            # mid_cd 폴백: small_cd 앞 3자리
            model = self.group_models.get(f"mid_{small_cd[:3]}")
        if model is None:
            return None
        X = features.reshape(1, -1)
        return max(0.0, float(model.predict(X)[0]))

    def predict_dual(
        self, features: np.ndarray, mid_cd: str,
        small_cd: Optional[str], data_days: int
    ) -> Optional[float]:
        """이중 모델 블렌딩 예측"""
        pred_ind = self.predict(features, mid_cd)
        pred_grp = self.predict_group(features, small_cd) if small_cd else None

        if pred_ind is None and pred_grp is None:
            return None
        if pred_ind is None:
            return pred_grp
        if pred_grp is None:
            return pred_ind

        confidence = min(1.0, data_days / 60.0)
        return confidence * pred_ind + (1.0 - confidence) * pred_grp

    def has_group_model(self, small_cd: Optional[str]) -> bool:
        """그룹 모델 존재 여부"""
        if not small_cd:
            return False
        return (f"small_{small_cd}" in self.group_models or
                f"mid_{small_cd[:3]}" in self.group_models)

    def save_group_model(self, key: str, model: Any, metrics=None) -> bool:
        """그룹 모델 저장: group_{key}.joblib"""
```

### 4. trainer.py — 그룹 모델 학습

#### 새 상수
```python
GROUP_TRAINING_DAYS = {
    "food_group": 30,
    "default": 90,
}
GROUP_MIN_SAMPLES = 30  # 그룹 모델 최소 샘플 (개별보다 낮음)
```

#### 새 메서드: train_group_models()
```python
def train_group_models(self, days: int = 30) -> Dict[str, Any]:
    """small_cd 기반 그룹 모델 학습 (food_group 전용)"""
    # 1. food 상품 전체 조회 (min_days=3, 개별보다 낮은 기준)
    # 2. small_cd별 그룹화
    # 3. 그룹별 학습 데이터 구성:
    #    - 그룹 내 모든 상품의 daily_sales를 합침
    #    - 각 샘플에 data_days_ratio, peer_avg, relative_position 추가
    # 4. 그룹당 >= GROUP_MIN_SAMPLES이면 학습
    #    미충족 시 mid_cd로 합쳐서 폴백 모델 학습
    # 5. 성능 게이트 적용
    # 6. group_{small_cd}.joblib 저장
```

#### train_all_groups() 수정
```python
def train_all_groups(self, days=90, incremental=False):
    # ... 기존 로직 유지 ...
    # 마지막에 그룹 모델 학습 추가
    group_days = GROUP_TRAINING_DAYS.get("food_group", days)
    group_results = self.train_group_models(days=group_days)
    results["_group_models"] = group_results
    return results
```

### 5. improved_predictor.py

#### _apply_ml_ensemble() 수정 (라인 2248~2369)
```python
# 변경: predict() → predict_dual()
# 추가: small_cd, data_days 전달

small_cd = product.get("small_cd")  # product dict에서 조회
# ...기존 feature 빌드...

# 그룹 컨텍스트 피처 추가
_peer_avg = self._smallcd_peer_cache.get(small_cd, 0.0) if small_cd else 0.0
_lifecycle = self._lifecycle_cache.get(item_cd, 1.0)
_my_avg = daily_avg_7  # 이미 계산된 값

features = MLFeatureBuilder.build_features(
    ...,  # 기존 파라미터
    data_days=data_days,
    smallcd_peer_avg=_peer_avg,
    lifecycle_stage=_lifecycle,
)

if features is not None:
    ml_pred = self._ml_predictor.predict_dual(
        features, mid_cd, small_cd, data_days
    )
```

#### _get_ml_weight() 수정 (라인 2384~2408)
```python
def _get_ml_weight(self, mid_cd, data_days):
    if data_days < 30:
        # 그룹 모델 폴백: 푸드 + 그룹 모델 존재 시 낮은 가중치로 참여
        if is_food_category(mid_cd):
            return 0.1  # 그룹 모델 의존
        return 0.0
    # ... 기존 로직 유지 ...
```

#### 캐시 초기화 (predict_all 시작 시)
```python
def _init_group_context_caches(self):
    """그룹 컨텍스트 피처 캐시 초기화 (1회)"""
    pipeline = MLDataPipeline(self.db_path, store_id=self.store_id)
    self._smallcd_peer_cache = pipeline.get_smallcd_peer_avg_batch()
    self._item_smallcd_map = pipeline.get_item_smallcd_map()
    self._lifecycle_cache = pipeline.get_lifecycle_stages_batch()
    # ML 그룹 모델 로드
    if self._ml_predictor:
        self._ml_predictor.load_group_models()
```

### 6. 모델 파일 구조

```
data/models/{store_id}/
  ├── model_food_group.joblib          # 기존 개별 모델
  ├── model_alcohol_group.joblib       # 기존
  ├── model_tobacco_group.joblib       # 기존
  ├── model_perishable_group.joblib    # 기존
  ├── model_general_group.joblib       # 기존
  ├── group_small_001A.joblib          # 그룹: 도시락류
  ├── group_small_003B.joblib          # 그룹: 김밥류
  ├── group_mid_001.joblib             # 폴백: mid_cd=001 전체
  ├── group_mid_002.joblib             # 폴백: mid_cd=002 전체
  └── model_meta.json
```

### 7. model_meta.json 확장

```json
{
  "groups": {
    "food_group": { "trained_at": "...", "metrics": {...} }
  },
  "group_models": {
    "small_001A": {
      "trained_at": "...",
      "feature_hash": "abc12345",
      "items_count": 15,
      "metrics": { "mae": 1.2, "samples": 450 }
    },
    "mid_001": {
      "trained_at": "...",
      "items_count": 80,
      "metrics": { "mae": 1.5, "samples": 2400 }
    }
  }
}
```

## 구현 순서

1. `feature_builder.py` — 4개 피처 추가
2. `data_pipeline.py` — 3개 조회 메서드 추가
3. `model.py` — group_models, predict_dual, predict_group
4. `trainer.py` — train_group_models, GROUP_TRAINING_DAYS
5. `improved_predictor.py` — predict_dual 호출 + 캐시
6. `tests/test_food_ml_dual_model.py` — 20개 테스트
7. 기존 테스트 피처 수 31→35 수정
8. 전체 테스트 실행
