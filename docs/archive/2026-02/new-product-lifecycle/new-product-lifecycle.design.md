# Design: new-product-lifecycle

> 신제품 초기 모니터링 및 라이프사이클 관리 — 상세 설계

**Plan Reference**: `docs/01-plan/features/new-product-lifecycle.plan.md`

---

## 1. 데이터 흐름

```
Phase 1.1 ReceivingCollector
  └─ _detect_and_register_new_products()
       └─ detected_new_products 저장 (lifecycle_status='detected')
              │
Phase 1.35 NewProductMonitor.run()  ← 신규 Phase
  ├─ 1) get_active_monitoring_items() → detected/monitoring 상품 목록
  ├─ 2) collect_daily_tracking() → new_product_daily_tracking 저장
  │     └─ daily_sales.sell_qty + realtime_inventory.stock_qty + auto_order_items.order_qty
  ├─ 3) update_lifecycle_status() → 상태 전환
  │     ├─ detected → monitoring (첫 추적 시)
  │     ├─ monitoring → stable (14일 + sold_days >= 3)
  │     ├─ monitoring → no_demand (14일 + sold_days == 0)
  │     └─ monitoring → slow_start (14일 + 0 < sold_days < 3)
  └─ 4) calculate_similar_avg() → stable 전환 시 유사 상품 일평균 저장
              │
Phase 1.7 ImprovedPredictor.predict()
  └─ _apply_new_product_boost() ← 신규 단계
       ├─ monitoring 상태 + data_days < 7 → 보정 적용
       ├─ similar_avg = detected_new_products.similar_item_avg
       └─ boosted = max(similar_avg * 0.7, base_prediction)
              │
Phase 2.0 AutoOrder → 보정된 예측값으로 발주
```

## 2. DB 스키마 변경 (v46)

### 2.1 detected_new_products 컬럼 추가 (ALTER TABLE)

```sql
-- v46: 신제품 라이프사이클 관리 컬럼 추가
ALTER TABLE detected_new_products ADD COLUMN lifecycle_status TEXT DEFAULT 'detected';
ALTER TABLE detected_new_products ADD COLUMN monitoring_start_date TEXT;
ALTER TABLE detected_new_products ADD COLUMN monitoring_end_date TEXT;
ALTER TABLE detected_new_products ADD COLUMN total_sold_qty INTEGER DEFAULT 0;
ALTER TABLE detected_new_products ADD COLUMN sold_days INTEGER DEFAULT 0;
ALTER TABLE detected_new_products ADD COLUMN similar_item_avg REAL;
ALTER TABLE detected_new_products ADD COLUMN status_changed_at TEXT;
```

**lifecycle_status 값**: `detected`, `monitoring`, `stable`, `slow_start`, `no_demand`, `normal`

### 2.2 신규 테이블: new_product_daily_tracking (store DB)

```sql
CREATE TABLE IF NOT EXISTS new_product_daily_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_cd TEXT NOT NULL,
    tracking_date TEXT NOT NULL,
    sales_qty INTEGER DEFAULT 0,
    stock_qty INTEGER DEFAULT 0,
    order_qty INTEGER DEFAULT 0,
    store_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(item_cd, tracking_date, store_id)
);
CREATE INDEX IF NOT EXISTS idx_np_tracking_item ON new_product_daily_tracking(item_cd);
CREATE INDEX IF NOT EXISTS idx_np_tracking_date ON new_product_daily_tracking(tracking_date);
```

### 2.3 schema.py / models.py 반영

- `STORE_SCHEMA`: `new_product_daily_tracking` 추가
- `STORE_INDEXES`: 2개 인덱스 추가
- `SCHEMA_MIGRATIONS[46]`: ALTER TABLE 7개 + CREATE TABLE + CREATE INDEX 2개
- `constants.py`: `DB_SCHEMA_VERSION = 46`

## 3. 컴포넌트 상세 설계

### 3.1 NewProductMonitor (`src/application/services/new_product_monitor.py`)

```python
class NewProductMonitor:
    """신제품 라이프사이클 모니터링 서비스"""

    MONITORING_DAYS = 14          # 모니터링 기간
    STABLE_THRESHOLD_DAYS = 3     # stable 전환 최소 판매일수
    NORMAL_DAYS_AFTER_STABLE = 30 # stable→normal 전환 일수

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.detect_repo = DetectedNewProductRepository(store_id=store_id)

    def run(self) -> Dict:
        """일일 모니터링 실행 (Phase 1.35)

        Returns:
            stats: {
                "active_items": int,      # 모니터링 대상 상품 수
                "tracking_saved": int,    # 일별 추적 저장 건수
                "status_changes": List,   # 상태 변경 내역
            }
        """

    def get_active_monitoring_items(self) -> List[Dict]:
        """모니터링 대상 상품 조회 (detected + monitoring 상태)"""

    def collect_daily_tracking(self, items: List[Dict], today: str) -> int:
        """일별 판매/재고/발주 데이터 수집 및 저장

        Sources:
            - daily_sales: sell_qty (해당일 판매수량)
            - realtime_inventory: stock_qty (현재 재고)
            - auto_order_items: order_qty (해당일 발주수량)
        """

    def update_lifecycle_status(self, items: List[Dict], today: str) -> List[Dict]:
        """상태 전환 로직 실행

        전환 규칙:
            detected → monitoring: 첫 추적 시
            monitoring → stable: 14일 경과 + sold_days >= 3
            monitoring → no_demand: 14일 경과 + sold_days == 0
            monitoring → slow_start: 14일 경과 + 0 < sold_days < 3
            stable → normal: monitoring_start_date + 30일 경과
        """

    def calculate_similar_avg(self, item_cd: str, mid_cd: str) -> Optional[float]:
        """유사 상품(같은 mid_cd) 일평균 판매량 계산

        Logic:
            1. products에서 같은 mid_cd 상품 조회 (자기 자신 제외)
            2. daily_sales에서 최근 30일 일평균 계산 (각 상품별)
            3. 중위값 반환 (이상치 방지)
            4. mid_cd가 '999' 또는 유사 상품 없으면 None
        """
```

### 3.2 DetectedNewProductRepository 확장

기존 메서드 유지 + 아래 메서드 추가:

```python
def get_by_lifecycle_status(
    self, statuses: List[str], store_id: Optional[str] = None
) -> List[Dict]:
    """lifecycle_status 기준 조회
    Args:
        statuses: ['detected', 'monitoring'] 등
    """

def update_lifecycle(
    self, item_cd: str, status: str,
    monitoring_start: Optional[str] = None,
    monitoring_end: Optional[str] = None,
    total_sold_qty: Optional[int] = None,
    sold_days: Optional[int] = None,
    similar_item_avg: Optional[float] = None,
    store_id: Optional[str] = None,
) -> None:
    """lifecycle 상태 + 집계 업데이트"""

def get_monitoring_summary(self, store_id: Optional[str] = None) -> Dict:
    """모니터링 현황 요약 (상태별 건수)
    Returns:
        {"detected": 2, "monitoring": 5, "stable": 3, "no_demand": 1, ...}
    """
```

### 3.3 NewProductDailyTrackingRepository (`src/infrastructure/database/repos/np_tracking_repo.py`)

```python
class NewProductDailyTrackingRepository(BaseRepository):
    """신제품 일별 추적 데이터 저장소"""
    db_type = "store"

    def save(self, item_cd: str, tracking_date: str,
             sales_qty: int, stock_qty: int, order_qty: int,
             store_id: Optional[str] = None) -> int:
        """일별 추적 UPSERT"""

    def get_tracking_history(
        self, item_cd: str, store_id: Optional[str] = None
    ) -> List[Dict]:
        """상품별 추적 이력 (차트 데이터)"""

    def get_sold_days_count(
        self, item_cd: str, store_id: Optional[str] = None
    ) -> int:
        """판매일수 (sales_qty > 0인 날짜 수)"""

    def get_total_sold_qty(
        self, item_cd: str, store_id: Optional[str] = None
    ) -> int:
        """총 판매수량"""
```

### 3.4 ImprovedPredictor 보정 단계

**위치**: `predict()` 메서드 내 **DiffFeedback 전**, 발주 단위 맞춤 전

```python
# improved_predictor.py predict() 내부 (line ~1990 부근)
# --- 신제품 초기 보정 (monitoring 상태만) ---
if order_qty > 0:
    order_qty = self._apply_new_product_boost(
        item_cd, mid_cd, order_qty, adjusted_prediction
    )

def _apply_new_product_boost(self, item_cd, mid_cd, order_qty, prediction):
    """신제품 초기 발주량 보정

    조건:
        1. detected_new_products에 존재
        2. lifecycle_status == 'monitoring'
        3. 판매 데이터 < 7일
    보정:
        max(similar_item_avg * 0.7, prediction) → order_qty 재계산
    """
    if not self._new_product_cache:
        return order_qty  # 캐시 없으면 skip

    np_info = self._new_product_cache.get(item_cd)
    if not np_info or np_info.get("lifecycle_status") != "monitoring":
        return order_qty

    similar_avg = np_info.get("similar_item_avg")
    if similar_avg is None or similar_avg <= 0:
        return order_qty

    boosted = max(similar_avg * 0.7, prediction)
    if boosted > prediction:
        # 보정값이 기존 예측보다 높을 때만 적용
        new_order = max(1, round(boosted - current_stock - pending + safety))
        if new_order > order_qty:
            logger.info(f"[신제품보정] {item_cd}: {order_qty}→{new_order} "
                        f"(유사avg={similar_avg:.1f})")
            return new_order
    return order_qty
```

**캐시 로딩**: `__init__` 또는 `predict()` 첫 호출 시 `detected_new_products` 전체를 dict로 로딩

```python
# __init__ 에 추가
self._new_product_cache: Dict[str, Dict] = {}

# predict() 시작부에 1회 로딩
if not self._new_product_cache:
    try:
        repo = DetectedNewProductRepository(store_id=self.store_id)
        items = repo.get_by_lifecycle_status(
            ["monitoring"], store_id=self.store_id
        )
        self._new_product_cache = {
            item["item_cd"]: item for item in items
        }
    except Exception:
        self._new_product_cache = {}
```

### 3.5 daily_job.py Phase 1.35 추가

```python
# Phase 1.3 (NewProductCollector) 뒤, Phase 1.5 (EvalCalibrator) 앞
# ★ Phase 1.35: 신제품 라이프사이클 모니터링
if collection_success:
    try:
        logger.info("[Phase 1.35] New Product Lifecycle Monitoring")
        logger.info(LOG_SEPARATOR_THIN)
        from src.application.services.new_product_monitor import NewProductMonitor
        monitor = NewProductMonitor(store_id=self.store_id)
        monitor_stats = monitor.run()
        if monitor_stats["active_items"] > 0:
            logger.info(
                f"[Phase 1.35] 모니터링: {monitor_stats['active_items']}건, "
                f"추적저장: {monitor_stats['tracking_saved']}건, "
                f"상태변경: {len(monitor_stats['status_changes'])}건"
            )
    except Exception as e:
        logger.warning(f"신제품 모니터링 실패 (발주 플로우 계속): {e}")
```

## 4. Web API 확장

### 4.1 모니터링 현황 API

**`GET /api/receiving/new-products/monitoring`** (api_receiving.py 추가)

```python
@receiving_bp.route("/new-products/monitoring")
def monitoring_new_products():
    """모니터링 중인 신제품 현황

    Query params:
        store_id: 매장 코드 (기본 DEFAULT_STORE_ID)

    Returns:
        {
            "summary": {"detected": 2, "monitoring": 5, "stable": 3, ...},
            "items": [...],
            "count": int
        }
    """
```

### 4.2 일별 추적 API

**`GET /api/receiving/new-products/<item_cd>/tracking`** (api_receiving.py 추가)

```python
@receiving_bp.route("/new-products/<item_cd>/tracking")
def new_product_tracking(item_cd):
    """신제품 일별 판매/재고/발주 추이

    Returns:
        {
            "item_cd": "...",
            "tracking": [
                {"date": "2026-02-26", "sales_qty": 3, "stock_qty": 5, "order_qty": 6},
                ...
            ]
        }
    """
```

## 5. lifecycle_status 상태 전환 상세

```
                  ┌──────────┐
         감지 시 →│ detected │
                  └────┬─────┘
                       │ NewProductMonitor 첫 실행
                       ▼
                  ┌────────────┐
                  │ monitoring │ ← 14일 모니터링
                  └────┬───────┘
                       │ 14일 경과 판정
           ┌───────────┼───────────┐
           ▼           ▼           ▼
     ┌─────────┐ ┌───────────┐ ┌───────────┐
     │ stable  │ │slow_start │ │ no_demand │
     │≥3일판매 │ │1~2일판매  │ │ 0일판매   │
     └────┬────┘ └───────────┘ └───────────┘
          │ +30일 경과
          ▼
     ┌─────────┐
     │ normal  │ ← 일반 상품으로 편입
     └─────────┘
```

## 6. 테스트 설계

### 6.1 NewProductMonitor 테스트 (8건)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | test_collect_daily_tracking | 일별 판매/재고/발주 정확히 저장 |
| 2 | test_detected_to_monitoring | detected → monitoring 전환 |
| 3 | test_monitoring_to_stable | 14일 + sold_days>=3 → stable |
| 4 | test_monitoring_to_no_demand | 14일 + sold_days==0 → no_demand |
| 5 | test_monitoring_to_slow_start | 14일 + 1<=sold_days<3 → slow_start |
| 6 | test_stable_to_normal | stable + 30일 → normal |
| 7 | test_similar_avg_calculation | mid_cd 기반 중위값 정확성 |
| 8 | test_similar_avg_no_match | mid_cd=999 → None 반환 |

### 6.2 NewProductOrderBooster 테스트 (5건)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | test_boost_applied | monitoring + data<7일 → 보정 적용 |
| 2 | test_boost_skip_stable | stable 상태 → 보정 미적용 |
| 3 | test_boost_skip_no_similar | similar_avg=None → 보정 미적용 |
| 4 | test_boost_cap | 보정값이 기존보다 낮으면 기존 유지 |
| 5 | test_boost_cache_loading | _new_product_cache 정상 로딩 |

### 6.3 Repository 테스트 (4건)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | test_lifecycle_status_query | get_by_lifecycle_status 정확성 |
| 2 | test_update_lifecycle | update_lifecycle 필드 업데이트 |
| 3 | test_tracking_save_and_get | 일별 추적 UPSERT + 조회 |
| 4 | test_monitoring_summary | 상태별 건수 집계 |

### 6.4 API + 스키마 테스트 (3건)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | test_monitoring_api | /new-products/monitoring 응답 |
| 2 | test_tracking_api | /new-products/<item_cd>/tracking 응답 |
| 3 | test_schema_v46 | SCHEMA_MIGRATIONS[46] 존재 + 실행 |

**총 테스트: 20건**

## 7. 구현 순서

| Step | 파일 | 내용 |
|------|------|------|
| 1 | constants.py, models.py, schema.py | DB v46 (ALTER + CREATE TABLE) |
| 2 | np_tracking_repo.py | NewProductDailyTrackingRepository 신규 |
| 3 | detected_new_product_repo.py | lifecycle 쿼리 3개 추가 |
| 4 | repos/__init__.py | re-export 추가 |
| 5 | new_product_monitor.py | NewProductMonitor 서비스 |
| 6 | improved_predictor.py | _apply_new_product_boost + 캐시 |
| 7 | daily_job.py | Phase 1.35 추가 |
| 8 | api_receiving.py | monitoring + tracking API |
| 9 | conftest.py | 테스트 DB 테이블 추가 |
| 10 | test_new_product_lifecycle.py | 20개 테스트 |
| 11 | 전체 테스트 | 기존 2274개 + 신규 20개 통과 확인 |
