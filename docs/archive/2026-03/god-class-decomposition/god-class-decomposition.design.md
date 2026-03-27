# Design: god-class-decomposition

## 아키텍처

### Facade 패턴
```
ImprovedPredictor (Facade, ~700줄)
  ├─ BasePredictor          (기본 예측)
  ├─ CoefficientAdjuster    (계수 적용)
  ├─ InventoryResolver      (재고 해결)
  └─ PredictionCacheManager (캐시 관리)

AutoOrderSystem (Facade, ~600줄)
  ├─ OrderDataLoader  (데이터 로드)
  ├─ OrderFilter      (필터링)
  ├─ OrderAdjuster    (발주량 조정)
  └─ OrderTracker     (추적/저장)
```

## 파일 구조

### 신규 파일 (8개)
```
src/prediction/
  ├─ base_predictor.py         # WMA, Croston, Feature 블렌딩
  ├─ coefficient_adjuster.py   # 연휴/기온/요일/계절/연관 계수
  ├─ inventory_resolver.py     # 재고/미입고 조회+TTL
  └─ prediction_cache.py       # 배치 캐시 통합 관리

src/order/
  ├─ order_data_loader.py      # 미취급/CUT/자동발주 로드
  ├─ order_filter.py           # 제외 필터+수동발주 차감
  ├─ order_adjuster.py         # 미입고/재고 반영 조정
  └─ order_tracker.py          # 발주 추적 저장
```

### 수정 파일 (2개)
```
src/prediction/improved_predictor.py  # 3470→~700줄 (Facade)
src/order/auto_order.py               # 2609→~600줄 (Facade)
```

## 클래스 설계

### 1. BasePredictor
```python
class BasePredictor:
    def __init__(self, data_provider, feature_calculator, store_id):
        self._data = data_provider
        self._feature_calculator = feature_calculator
        self.store_id = store_id

    def compute(self, item_cd, product, target_date, cache_manager):
        """기본 예측 수행"""
        # Returns: (base_prediction, data_days, wma_days, feat_result, sell_day_ratio, intermittent_adjusted)

    def calculate_weighted_average(self, sales_data, target_date, ...):
        """가중 이동 평균 계산"""

    def _compute_wma(self, item_cd, product, target_date, ...):
        """WMA + Feature 블렌딩"""

    def _compute_croston(self, item_cd, product, target_date, ...):
        """Croston/TSB 간헐수요 예측"""
```

### 2. CoefficientAdjuster
```python
class CoefficientAdjuster:
    def __init__(self, data_provider, store_id):
        self._data = data_provider
        self.store_id = store_id
        self._association_adjuster = None  # lazy

    def apply(self, base_prediction, item_cd, product, target_date, feat_result, cache_manager):
        """모든 계수 통합 적용"""
        # Returns: (base_prediction, adjusted_prediction, weekday_coef, assoc_boost)

    def _apply_multiplicative(self, ...): ...
    def _apply_additive(self, ...): ...
    def _get_holiday_coefficient(self, ...): ...
    def _get_weather_coefficient(self, ...): ...
    def _get_temperature_for_date(self, ...): ...
```

### 3. InventoryResolver
```python
class InventoryResolver:
    def __init__(self, data_provider, store_id):
        self._data = data_provider
        self.store_id = store_id
        self._pending_cache = {}
        self._stock_cache = {}

    def resolve(self, item_cd, product, pending_qty_override=None):
        """재고/미입고 조회"""
        # Returns: (current_stock, pending_qty, stock_source, pending_source, is_stale)

    def set_pending_cache(self, cache): ...
    def set_stock_cache(self, cache): ...
    def clear_caches(self): ...
```

### 4. PredictionCacheManager
```python
class PredictionCacheManager:
    def __init__(self, data_provider, store_id):
        self._data = data_provider
        self.store_id = store_id
        self.demand_pattern = {}
        self.food_weekday = {}
        self.receiving_stats = {}
        self.smallcd_peer = {}
        self.item_smallcd_map = {}
        self.lifecycle = {}
        self.ot_pending = {}
        self.new_product = {}

    def load_batch(self, item_codes, target_date):
        """배치 캐시 일괄 로드"""

    def load_demand_patterns(self, item_codes): ...
    def load_food_weekday(self, item_codes): ...
    def load_receiving_stats(self): ...
    def load_group_contexts(self): ...
    def load_new_products(self): ...
```

### 5. OrderDataLoader
```python
class OrderDataLoader:
    def __init__(self, store_id, driver=None):
        self.store_id = store_id
        self.driver = driver
        self.unavailable_items = set()
        self.cut_items = set()
        self.auto_order_items = set()
        self.smart_order_items = set()

    def load_unavailable(self): ...
    def load_cut_items(self): ...
    def load_auto_order_items(self, skip_site_fetch=False): ...
    def load_inventory_cache(self, predictor): ...
    def prefetch_pending(self, collector, ...): ...
```

### 6. OrderFilter
```python
class OrderFilter:
    def __init__(self, store_id, loader: OrderDataLoader):
        self.store_id = store_id
        self._loader = loader

    def exclude_filtered(self, order_list, exclusion_records): ...
    def deduct_manual_food(self, order_list, min_qty, collector): ...
    def warn_stale_cut(self): ...
```

### 7. OrderAdjuster
```python
class OrderAdjuster:
    def __init__(self, store_id):
        self.store_id = store_id

    def apply_pending_and_stock(self, order_list, stock_data, ...): ...
    def recalculate_need_qty(self, item, ...): ...
```

### 8. OrderTracker
```python
class OrderTracker:
    def __init__(self, store_id, tracking_repo, exclusion_repo):
        self.store_id = store_id
        self._tracking_repo = tracking_repo
        self._exclusion_repo = exclusion_repo

    def save_tracking(self, order_list, results): ...
    def update_eval_results(self, order_list, results, calibrator): ...
```

## Facade 위임 패턴

### ImprovedPredictor.predict() 변경 전/후
```python
# 변경 전: 3470줄 내부에서 직접 실행
def predict(self, item_cd, target_date, pending_qty):
    product = self.get_product_info(item_cd)
    base, ... = self._compute_base_prediction(...)    # 내부 메서드
    base, adj, ... = self._apply_all_coefficients(...) # 내부 메서드
    stock, pending, ... = self._resolve_stock_and_pending(...) # 내부 메서드
    ...

# 변경 후: 추출 클래스에 위임
def predict(self, item_cd, target_date, pending_qty):
    product = self._data.get_product_info(item_cd)
    base, ... = self._base.compute(...)           # BasePredictor
    base, adj, ... = self._coef.apply(...)        # CoefficientAdjuster
    stock, pending, ... = self._inventory.resolve(...)  # InventoryResolver
    ...  # 나머지는 기존과 동일
```

## 구현 순서
1. BasePredictor 추출 + ImprovedPredictor 위임
2. CoefficientAdjuster 추출 + 위임
3. InventoryResolver 추출 + 위임
4. PredictionCacheManager 추출 + 위임
5. OrderDataLoader 추출 + AutoOrderSystem 위임
6. OrderFilter 추출 + 위임
7. OrderAdjuster 추출 + 위임
8. OrderTracker 추출 + 위임

## 테스트 전략
- 기존 테스트 2838개 그대로 통과 (Facade 유지로 Mock 변경 불필요)
- 각 Step 후 `pytest tests/` 실행
- 최종 줄 수 확인: improved_predictor ~700줄, auto_order ~600줄
