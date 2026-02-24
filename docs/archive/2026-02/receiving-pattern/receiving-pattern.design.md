# Design: receiving-pattern (ML 입고 패턴 피처 추가)

> Plan 참조: `docs/01-plan/features/receiving-pattern.plan.md`

---

## 1. 변경 파일 및 상세 설계

---

### 변경 1: `src/infrastructure/database/repos/receiving_repo.py`

#### 1-A. `get_receiving_pattern_stats_batch()` 메서드 추가

매장 전체 상품의 입고 패턴 통계를 **1회 쿼리**로 조회합니다.

```python
def get_receiving_pattern_stats_batch(
    self,
    store_id: Optional[str] = None,
    days: int = 30
) -> Dict[str, Dict[str, float]]:
    """
    매장 전체 상품의 입고 패턴 통계 일괄 조회

    Returns:
        {item_cd: {
            "lead_time_avg": float,   # 평균 리드타임 (일)
            "lead_time_std": float,   # 리드타임 표준편차
            "short_delivery_rate": float,  # 숏배송율 (0~1)
            "delivery_frequency": int,     # 최근 14일 입고 횟수
            "total_records": int,          # 총 레코드 수
        }}
    """
```

**SQL 쿼리**:
```sql
-- (A) 리드타임 + 숏배송 (최근 30일)
SELECT
    item_cd,
    AVG(julianday(receiving_date) - julianday(order_date)) as lead_time_avg,
    CASE
        WHEN COUNT(*) >= 3 AND AVG(julianday(receiving_date) - julianday(order_date)) > 0
        THEN SQRT(
            SUM(POWER(julianday(receiving_date) - julianday(order_date)
                - AVG(julianday(receiving_date) - julianday(order_date)), 2)) / COUNT(*)
        )
        ELSE 0
    END as lead_time_std,
    SUM(CASE WHEN receiving_qty < order_qty AND order_qty > 0 THEN 1 ELSE 0 END) * 1.0
        / MAX(COUNT(*), 1) as short_delivery_rate,
    COUNT(*) as total_records
FROM receiving_history
WHERE receiving_date >= date('now', '-' || ? || ' days')
    AND store_id = ?
GROUP BY item_cd;

-- (B) 입고 빈도 (최근 14일)
SELECT
    item_cd,
    COUNT(DISTINCT receiving_date) as delivery_frequency
FROM receiving_history
WHERE receiving_date >= date('now', '-14 days')
    AND store_id = ?
GROUP BY item_cd;
```

**반환값 구조**:
```python
{
    "8800279678694": {
        "lead_time_avg": 1.2,
        "lead_time_std": 0.4,
        "short_delivery_rate": 0.15,
        "delivery_frequency": 5,
        "total_records": 8
    },
    ...
}
```

**주의사항**:
- `POWER()` 함수 미지원 시 Python에서 후처리 (SQLite 버전 의존)
- SQLite에서 `POWER`가 없을 수 있으므로, 안전하게 Python에서 std 계산

**실제 구현 (SQLite POWER 미지원 대비)**:
```python
# SQL에서는 avg, count, sum(receiving_qty < order_qty) 만 조회
# std는 Python에서 개별 lead_time 리스트로 계산

-- 개별 lead_time 조회
SELECT
    item_cd,
    julianday(receiving_date) - julianday(order_date) as lead_time,
    CASE WHEN receiving_qty < order_qty AND order_qty > 0 THEN 1 ELSE 0 END as is_short
FROM receiving_history
WHERE receiving_date >= date('now', '-' || ? || ' days')
    AND store_id = ?
ORDER BY item_cd;
```
Python에서 `defaultdict`로 item_cd별 리스트 수집 → `np.mean()`, `np.std()` 계산.

---

### 변경 2: `src/infrastructure/database/repos/order_tracking_repo.py`

#### 2-A. `get_pending_age_batch()` 메서드 추가

현재 미입고(ordered/arrived) 상태인 상품의 가장 오래된 발주 경과일 조회.

```python
def get_pending_age_batch(
    self,
    store_id: Optional[str] = None
) -> Dict[str, int]:
    """
    매장 전체 미입고 상품의 pending 경과일 일괄 조회

    Returns:
        {item_cd: pending_age_days(int)}
        pending 없는 상품은 dict에 포함하지 않음
    """
```

**SQL 쿼리**:
```sql
SELECT
    item_cd,
    MIN(order_date) as oldest_order_date
FROM order_tracking
WHERE status IN ('ordered', 'arrived')
    AND remaining_qty > 0
    AND store_id = ?
GROUP BY item_cd;
```
Python에서 `(today - oldest_order_date).days` 계산.

---

### 변경 3: `src/prediction/ml/feature_builder.py`

#### 3-A. FEATURE_NAMES 확장 (31 → 36)

기존 31개 뒤에 5개 append (순서 유지):

```python
FEATURE_NAMES = [
    # ... 기존 31개 유지 ...
    # 입고 패턴 (receiving-pattern 추가)
    "lead_time_avg",        # 평균 리드타임 (정규화: /3.0, cap 1.0)
    "lead_time_cv",         # 리드타임 안정성 (CV, 정규화: /2.0, cap 1.0)
    "short_delivery_rate",  # 숏배송율 (0~1, 정규화 불필요)
    "delivery_frequency",   # 14일 입고 빈도 (정규화: /14.0)
    "pending_age_days",     # 미입고 경과일 (정규화: /5.0, cap 1.0)
]
```

#### 3-B. `build_features()` 파라미터 추가

```python
@staticmethod
def build_features(
    daily_sales: List[Dict[str, Any]],
    target_date: str,
    mid_cd: str,
    # ... 기존 파라미터 유지 ...
    # 입고 패턴 (receiving-pattern 추가)
    receiving_stats: Optional[Dict[str, float]] = None,
) -> Optional[np.ndarray]:
```

#### 3-C. 피처 계산 및 정규화 로직

```python
# 입고 패턴 피처 계산
_recv = receiving_stats or {}
_lt_avg = _recv.get("lead_time_avg", 0.0)
_lt_std = _recv.get("lead_time_std", 0.0)
_lt_mean = max(_lt_avg, 0.001)  # division by zero 방지
_lt_cv = (_lt_std / _lt_mean) if _lt_avg > 0 else 0.5  # 데이터 없으면 0.5

features = np.array([
    # ... 기존 31개 ...
    # 입고 패턴
    min(float(_lt_avg) / 3.0, 1.0),                          # lead_time_avg
    min(float(_lt_cv) / 2.0, 1.0),                           # lead_time_cv
    float(_recv.get("short_delivery_rate", 0.0)),             # short_delivery_rate
    float(_recv.get("delivery_frequency", 0)) / 14.0,        # delivery_frequency
    min(float(_recv.get("pending_age_days", 0)) / 5.0, 1.0), # pending_age_days
], dtype=np.float32)
```

#### 3-D. `build_batch_features()` 호환성

```python
# items_data dict에서 receiving_stats 읽기 (없으면 None)
features = MLFeatureBuilder.build_features(
    # ... 기존 파라미터 ...
    receiving_stats=item.get("receiving_stats"),
)
```

---

### 변경 4: `src/prediction/improved_predictor.py`

#### 4-A. `_apply_ml_ensemble()` 내 receiving_stats 조회 및 전달

호출 위치: `_apply_ml_ensemble()` (약 line 1884-1945)

```python
# 기존 features 빌드 호출 전에 receiving_stats 조회
# 성능: self._receiving_stats_cache에서 배치 캐시 조회

_recv_stats = self._receiving_stats_cache.get(item_cd, {})

features = MLFeatureBuilder.build_features(
    # ... 기존 파라미터 유지 ...
    receiving_stats=_recv_stats,
)
```

#### 4-B. 배치 캐시 초기화 (predict_all 또는 get_predictions 진입점)

```python
# predict_all() 또는 _run_predictions() 시작 시 1회 캐시 로드
def _load_receiving_stats_cache(self):
    """입고 패턴 통계 배치 캐시 로드"""
    try:
        from src.infrastructure.database.repos.receiving_repo import ReceivingRepository
        from src.infrastructure.database.repos.order_tracking_repo import OrderTrackingRepository

        recv_repo = ReceivingRepository(store_id=self.store_id)
        ot_repo = OrderTrackingRepository(store_id=self.store_id)

        # 입고 패턴 (30일)
        pattern_stats = recv_repo.get_receiving_pattern_stats_batch(
            store_id=self.store_id, days=30
        )

        # pending 경과일
        pending_ages = ot_repo.get_pending_age_batch(store_id=self.store_id)

        # 머지
        self._receiving_stats_cache = {}
        all_items = set(pattern_stats.keys()) | set(pending_ages.keys())
        for item_cd in all_items:
            stats = pattern_stats.get(item_cd, {})
            stats["pending_age_days"] = pending_ages.get(item_cd, 0)
            self._receiving_stats_cache[item_cd] = stats

        logger.info(f"[입고패턴] 캐시 로드: {len(self._receiving_stats_cache)}개 상품")
    except Exception as e:
        logger.warning(f"[입고패턴] 캐시 로드 실패 (무시): {e}")
        self._receiving_stats_cache = {}
```

**호출 시점**: `_run_predictions()` 시작 부분에서 1회 호출.

---

### 변경 5: `tests/test_receiving_pattern_features.py`

#### 테스트 구조

```
TestReceivingPatternStatsBatch     # receiving_repo 배치 쿼리 (5개)
  - test_basic_stats_calculation
  - test_empty_receiving_history
  - test_short_delivery_detection
  - test_multiple_items_batch
  - test_days_filter

TestPendingAgeBatch                # order_tracking pending 경과일 (4개)
  - test_basic_pending_age
  - test_no_pending_returns_empty
  - test_multiple_pending_oldest
  - test_only_ordered_arrived_status

TestFeatureBuilderReceiving        # feature_builder 확장 (6개)
  - test_feature_count_36
  - test_receiving_stats_included
  - test_receiving_stats_none_defaults
  - test_lead_time_normalization
  - test_short_rate_passthrough
  - test_pending_age_cap

TestMLEnsembleReceivingIntegration # improved_predictor 연동 (3개)
  - test_cache_loads_on_predictions
  - test_features_passed_to_ml
  - test_backward_compatible_no_receiving
```

**총 18개 이상 테스트**

---

## 2. 피처 정규화 상세

| 피처 | 원본 범위 | 정규화 공식 | 결과 범위 | 폴백값 |
|------|-----------|------------|-----------|--------|
| `lead_time_avg` | 0~7+ 일 | `min(val / 3.0, 1.0)` | 0~1.0 | 0.0 |
| `lead_time_cv` | 0~3+ | `min((std/mean) / 2.0, 1.0)` | 0~1.0 | 0.25 |
| `short_delivery_rate` | 0~1.0 | 그대로 | 0~1.0 | 0.0 |
| `delivery_frequency` | 0~14 회 | `val / 14.0` | 0~1.0 | 0.0 |
| `pending_age_days` | 0~30+ 일 | `min(val / 5.0, 1.0)` | 0~1.0 | 0.0 |

---

## 3. 데이터 플로우

```
[매일 07:00 daily_job Phase 1.1]
receiving_history 테이블 적재 (ReceivingCollector)
order_tracking 테이블 갱신
           |
           v
[예측 Phase 2 시작]
improved_predictor._run_predictions()
  |
  +-- _load_receiving_stats_cache() [1회]
  |     |
  |     +-- receiving_repo.get_receiving_pattern_stats_batch(30일)
  |     |     -> {item_cd: {lead_time_avg, lead_time_std, short_rate, frequency}}
  |     |
  |     +-- order_tracking_repo.get_pending_age_batch()
  |     |     -> {item_cd: pending_age_days}
  |     |
  |     +-- 머지 -> self._receiving_stats_cache
  |
  +-- [상품별 루프]
        |
        +-- _apply_ml_ensemble(item_cd, ...)
              |
              +-- _recv_stats = self._receiving_stats_cache.get(item_cd, {})
              |
              +-- MLFeatureBuilder.build_features(
              |       ..., receiving_stats=_recv_stats
              |   )
              |
              +-- ML model.predict(features[0:36])
              |
              +-- 앙상블 블렌딩 (rule * 0.5 + ml * 0.5)
```

---

## 4. 역호환성 보장

| 상황 | 동작 |
|------|------|
| 기존 31-feature 모델 로드 | feature hash 불일치 → `model_type = "rule"` 자동 전환 (기존 로직) |
| `receiving_stats=None` 호출 | 모든 입고 피처 → 기본값 0.0 (폴백) |
| `receiving_history` 빈 테이블 | `get_receiving_pattern_stats_batch()` → 빈 dict |
| `build_batch_features()` | `receiving_stats` 키 없으면 None 전달 (기존 호환) |

---

## 5. 성능 분석

| 작업 | 쿼리 수 | 예상 소요 |
|------|---------|-----------|
| `get_receiving_pattern_stats_batch` | 1회 | ~50ms (300상품, 30일 이력) |
| `get_pending_age_batch` | 1회 | ~20ms |
| 캐시 머지 | Python | ~5ms |
| **총 추가 지연** | **2회** | **< 100ms** |

기존: 상품별 개별 쿼리 → 300회 (X)
설계: 배치 쿼리 2회 + dict lookup → O(1) per item (O)

---

## 6. 구현 순서 (우선순위)

| 순서 | 파일 | 작업 |
|------|------|------|
| 1 | `receiving_repo.py` | `get_receiving_pattern_stats_batch()` 추가 |
| 2 | `order_tracking_repo.py` | `get_pending_age_batch()` 추가 |
| 3 | `feature_builder.py` | FEATURE_NAMES 확장 + build_features 파라미터 + 정규화 |
| 4 | `feature_builder.py` | build_batch_features 호환성 |
| 5 | `improved_predictor.py` | `_load_receiving_stats_cache()` 추가 |
| 6 | `improved_predictor.py` | `_apply_ml_ensemble()`에서 receiving_stats 전달 |
| 7 | `tests/test_receiving_pattern_features.py` | 18+개 테스트 |
| 8 | 전체 테스트 | 회귀 검증 (1633+개) |
