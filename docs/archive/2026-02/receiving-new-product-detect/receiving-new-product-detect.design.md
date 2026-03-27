# Design: receiving-new-product-detect

> 센터매입조회 입고 확정 시 DB 미등록 상품을 신제품으로 자동 감지 및 등록

## 1. 감지 조건 (핵심 규칙)

```
신제품 감지 조건 (모두 충족):
  ① 센터매입조회(ReceivingCollector) 경유 데이터
  ② receiving_qty > 0 (NAP_QTY > 0, 입고 확정)
  ③ common.db.products 테이블에 item_cd 미존재

제외 조건:
  - plan_qty > 0 AND receiving_qty == 0 → 입고 예정/미확정 → 무시
  - products에 이미 존재 → 기존 상품 → 무시
```

## 2. 데이터 흐름

```
collect_receiving_data()
  │ 상품별 반복: _get_mid_cd(item_cd) 호출
  │   ├─ products 조회 성공 → 기존 상품 (기존 로직)
  │   └─ products 조회 실패 → self._new_product_candidates에 축적
  │                            (item_cd, item_nm, mid_cd추정, cust_nm)
  ▼
collect_and_save()
  │ ① repo.save_bulk_receiving(data)                    -- 기존: 입고 이력 저장
  │ ② ★ _detect_and_register_new_products(data)         -- 신규: 신제품 감지+등록
  │   ├─ candidates 중 recv_qty > 0인 것만 필터
  │   ├─ products 테이블 INSERT (common.db)
  │   ├─ product_details 기본값 INSERT (common.db)
  │   ├─ realtime_inventory INSERT (store DB)
  │   └─ detected_new_products INSERT (store DB, 이력)
  │ ③ _create_batches_from_receiving(data)               -- 기존: 배치 생성
  │ ④ update_stock_from_receiving(data)                   -- 기존: 재고 갱신
  │    └─ 이제 신제품도 ②에서 등록되었으므로 skipped_no_data 감소
  ▼
  반환: stats에 new_products_detected, new_products_registered 추가
```

## 3. DB 스키마

### 3.1 새 테이블: `detected_new_products` (store DB)

```sql
CREATE TABLE IF NOT EXISTS detected_new_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    mid_cd_source TEXT DEFAULT 'fallback',  -- 'fallback' | 'unknown'
    first_receiving_date TEXT NOT NULL,
    receiving_qty INTEGER DEFAULT 0,
    order_unit_qty INTEGER DEFAULT 1,
    center_cd TEXT,
    center_nm TEXT,
    cust_nm TEXT,
    registered_to_products INTEGER DEFAULT 0,
    registered_to_details INTEGER DEFAULT 0,
    registered_to_inventory INTEGER DEFAULT 0,
    detected_at TEXT NOT NULL,
    store_id TEXT,
    UNIQUE(item_cd, first_receiving_date)
);
CREATE INDEX IF NOT EXISTS idx_detected_new_products_date
    ON detected_new_products(first_receiving_date);
CREATE INDEX IF NOT EXISTS idx_detected_new_products_item
    ON detected_new_products(item_cd);
```

### 3.2 스키마 버전
- `DB_SCHEMA_VERSION = 44 → 45`
- `SCHEMA_MIGRATIONS[45]`: `detected_new_products` 테이블 + 인덱스

## 4. 수정 파일 상세

### 4.1 `src/collectors/receiving_collector.py` — 핵심 변경

#### 변경 1: `__init__` — 신제품 후보 리스트 초기화
```python
def __init__(self, driver, store_id):
    ...
    self._new_product_candidates: List[Dict] = []  # 신제품 후보 축적용
```

#### 변경 2: `_get_mid_cd()` — 미등록 상품 후보 축적
```python
def _get_mid_cd(self, item_cd, cust_nm="", item_nm=""):
    # ... 기존 products 조회 로직 ...
    if row and row[0]:
        return row[0]  # 기존 상품

    # ★ products 미등록 → 신제품 후보로 축적
    estimated_mid = self._fallback_mid_cd(cust_nm, item_nm)
    self._new_product_candidates.append({
        "item_cd": item_cd,
        "item_nm": item_nm,
        "cust_nm": cust_nm,
        "mid_cd": estimated_mid,
        "mid_cd_source": "fallback" if estimated_mid else "unknown",
    })

    return estimated_mid
```

#### 변경 3: `collect_and_save()` — 신제품 처리 단계 추가
```python
def collect_and_save(self, dgfw_ymd=None):
    self._new_product_candidates = []  # 매 호출마다 초기화

    data = self.collect_receiving_data(dgfw_ymd)
    if not data:
        return {"total": 0, ...}

    stats = self.repo.save_bulk_receiving(data, store_id=self.store_id)

    # ★ 신제품 감지 및 등록 (입고 확정 상품만)
    new_product_stats = self._detect_and_register_new_products(data)
    stats.update(new_product_stats)

    batches_created = self._create_batches_from_receiving(data)
    stock_stats = self.update_stock_from_receiving(data)
    # ... 기존 후처리 ...
    return stats
```

#### 변경 4: `_detect_and_register_new_products()` — 신규 메서드
```python
def _detect_and_register_new_products(self, receiving_data: List[Dict]) -> Dict[str, int]:
    """입고 확정 상품 중 DB 미등록 상품을 신제품으로 등록

    핵심 규칙:
    - receiving_qty > 0 (입고 확정)인 후보만 신제품으로 확정
    - plan_qty만 있는 미확정 상품은 무시
    """
    stats = {"new_products_detected": 0, "new_products_registered": 0}

    if not self._new_product_candidates:
        return stats

    # 입고 확정된 item_cd 집합 생성
    confirmed_items = {}
    for record in receiving_data:
        item_cd = record.get("item_cd")
        recv_qty = self._to_int(record.get("receiving_qty", 0))
        if item_cd and recv_qty > 0:
            if item_cd not in confirmed_items:
                confirmed_items[item_cd] = record

    # 후보 중 입고 확정 + products 미등록 필터
    new_products = []
    seen = set()
    for candidate in self._new_product_candidates:
        item_cd = candidate["item_cd"]
        if item_cd in seen:
            continue
        if item_cd not in confirmed_items:
            continue  # 입고 예정/미확정 → 무시
        seen.add(item_cd)
        # 입고 데이터 병합
        recv_record = confirmed_items[item_cd]
        candidate["receiving_qty"] = self._to_int(recv_record.get("receiving_qty", 0))
        candidate["receiving_date"] = recv_record.get("receiving_date")
        candidate["order_unit_qty"] = self._to_int(recv_record.get("order_unit_qty", 1)) or 1
        candidate["center_cd"] = recv_record.get("center_cd")
        candidate["center_nm"] = recv_record.get("center_nm")
        new_products.append(candidate)

    if not new_products:
        return stats

    stats["new_products_detected"] = len(new_products)
    logger.info(f"[신제품 감지] {len(new_products)}건 (입고 확정 기준)")

    # 일괄 등록
    for product in new_products:
        try:
            self._register_single_new_product(product)
            stats["new_products_registered"] += 1
        except Exception as e:
            logger.warning(f"신제품 등록 실패 ({product['item_cd']}): {e}")

    logger.info(
        f"[신제품 등록] {stats['new_products_registered']}/{stats['new_products_detected']}건 완료"
    )
    return stats
```

#### 변경 5: `_register_single_new_product()` — 3개 테이블 등록
```python
def _register_single_new_product(self, product: Dict) -> None:
    """신제품 1건을 products + product_details + realtime_inventory에 등록"""
    item_cd = product["item_cd"]
    item_nm = product.get("item_nm", "")
    mid_cd = product.get("mid_cd", "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    store_id = self.store_id or DEFAULT_STORE_ID

    registered = {"products": False, "details": False, "inventory": False}

    # 1) products 테이블 (common.db)
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_common_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR IGNORE INTO products (item_cd, item_nm, mid_cd, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (item_cd, item_nm, mid_cd or "999", now, now)
            )
            conn.commit()
            registered["products"] = cursor.rowcount > 0
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"products 등록 실패 ({item_cd}): {e}")

    # 2) product_details 테이블 (common.db) — 기본값
    try:
        from src.infrastructure.database.repos import ProductDetailRepository
        detail_repo = ProductDetailRepository()
        expiry_days = self._get_expiry_days(mid_cd)
        detail_repo.save(item_cd, {
            "item_nm": item_nm,
            "expiration_days": expiry_days,
            "order_unit_qty": product.get("order_unit_qty", 1),
        })
        registered["details"] = True
    except Exception as e:
        logger.warning(f"product_details 등록 실패 ({item_cd}): {e}")

    # 3) realtime_inventory (store DB) — 입고 수량으로 초기 재고
    try:
        inventory_repo = RealtimeInventoryRepository(store_id=self.store_id)
        inventory_repo.save(
            item_cd=item_cd,
            stock_qty=product.get("receiving_qty", 0),
            pending_qty=0,
            order_unit_qty=product.get("order_unit_qty", 1),
            is_available=True,
            item_nm=item_nm,
            store_id=store_id,
        )
        registered["inventory"] = True
    except Exception as e:
        logger.warning(f"realtime_inventory 등록 실패 ({item_cd}): {e}")

    # 4) detected_new_products (store DB) — 이력 기록
    try:
        from src.infrastructure.database.repos import DetectedNewProductRepository
        detect_repo = DetectedNewProductRepository(store_id=self.store_id)
        detect_repo.save(
            item_cd=item_cd,
            item_nm=item_nm,
            mid_cd=mid_cd,
            mid_cd_source=product.get("mid_cd_source", "unknown"),
            first_receiving_date=product.get("receiving_date", now[:10]),
            receiving_qty=product.get("receiving_qty", 0),
            order_unit_qty=product.get("order_unit_qty", 1),
            center_cd=product.get("center_cd"),
            center_nm=product.get("center_nm"),
            cust_nm=product.get("cust_nm"),
            registered_to_products=registered["products"],
            registered_to_details=registered["details"],
            registered_to_inventory=registered["inventory"],
            store_id=store_id,
        )
    except Exception as e:
        logger.warning(f"detected_new_products 기록 실패 ({item_cd}): {e}")

    logger.info(
        f"[신제품] {item_cd} ({item_nm}) 등록: "
        f"products={'O' if registered['products'] else 'X'}, "
        f"details={'O' if registered['details'] else 'X'}, "
        f"inventory={'O' if registered['inventory'] else 'X'}"
    )
```

### 4.2 `src/infrastructure/database/repos/detected_new_product_repo.py` — 신규

```python
class DetectedNewProductRepository(BaseRepository):
    """신제품 감지 이력 저장소"""
    db_type = "store"

    def save(self, item_cd, item_nm, mid_cd, mid_cd_source,
             first_receiving_date, receiving_qty, order_unit_qty,
             center_cd, center_nm, cust_nm,
             registered_to_products, registered_to_details,
             registered_to_inventory, store_id) -> int:
        """UPSERT (item_cd + first_receiving_date 기준)"""
        ...

    def get_by_date_range(self, start_date, end_date, store_id=None) -> List[Dict]:
        """기간별 조회"""
        ...

    def get_unregistered(self, store_id=None) -> List[Dict]:
        """등록 미완료 상품 조회"""
        ...

    def get_recent(self, days=30, store_id=None) -> List[Dict]:
        """최근 N일 감지 이력"""
        ...

    def mark_registered(self, item_cd, field, store_id=None) -> None:
        """등록 완료 마킹 (field: 'products' | 'details' | 'inventory')"""
        ...
```

### 4.3 `src/infrastructure/database/repos/__init__.py` — re-export 추가

```python
from .detected_new_product_repo import DetectedNewProductRepository
# __all__에도 추가
```

### 4.4 `src/infrastructure/database/schema.py` — STORE_SCHEMA 추가

```python
# detected_new_products (입고 시 감지된 신제품 이력)
"""CREATE TABLE IF NOT EXISTS detected_new_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT,
    mid_cd_source TEXT DEFAULT 'fallback',
    first_receiving_date TEXT NOT NULL,
    receiving_qty INTEGER DEFAULT 0,
    order_unit_qty INTEGER DEFAULT 1,
    center_cd TEXT,
    center_nm TEXT,
    cust_nm TEXT,
    registered_to_products INTEGER DEFAULT 0,
    registered_to_details INTEGER DEFAULT 0,
    registered_to_inventory INTEGER DEFAULT 0,
    detected_at TEXT NOT NULL,
    store_id TEXT,
    UNIQUE(item_cd, first_receiving_date)
)""",
```

### 4.5 `src/db/models.py` — v45 마이그레이션

```python
DB_SCHEMA_VERSION = 45  # v45: detected_new_products 테이블

SCHEMA_MIGRATIONS[45] = """
    -- v45: 입고 시 감지된 신제품 이력
    CREATE TABLE IF NOT EXISTS detected_new_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_cd TEXT NOT NULL,
        item_nm TEXT,
        mid_cd TEXT,
        mid_cd_source TEXT DEFAULT 'fallback',
        first_receiving_date TEXT NOT NULL,
        receiving_qty INTEGER DEFAULT 0,
        order_unit_qty INTEGER DEFAULT 1,
        center_cd TEXT,
        center_nm TEXT,
        cust_nm TEXT,
        registered_to_products INTEGER DEFAULT 0,
        registered_to_details INTEGER DEFAULT 0,
        registered_to_inventory INTEGER DEFAULT 0,
        detected_at TEXT NOT NULL,
        store_id TEXT,
        UNIQUE(item_cd, first_receiving_date)
    );
    CREATE INDEX IF NOT EXISTS idx_detected_new_products_date
        ON detected_new_products(first_receiving_date);
    CREATE INDEX IF NOT EXISTS idx_detected_new_products_item
        ON detected_new_products(item_cd);
"""
```

### 4.6 `src/settings/constants.py`

```python
DB_SCHEMA_VERSION = 45  # v45: detected_new_products
```

### 4.7 `src/web/routes/api_new_product_detect.py` — 웹 API (신규)

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/api/new-products/detected` | GET | 감지 이력 조회 (기간 필터) |
| `/api/new-products/detected/recent` | GET | 최근 30일 감지 목록 |
| `/api/new-products/detected/unregistered` | GET | 등록 미완료 목록 |

## 5. 에러 처리 원칙

- 신제품 감지/등록 실패 시 **기존 입고 수집 플로우에 영향 없음**
- 개별 상품 등록 실패: `logger.warning()` + 다음 상품 계속
- 전체 감지 실패: `except Exception` + 기존 `collect_and_save()` 정상 진행
- 3개 테이블(products/details/inventory) 중 일부만 성공해도 이력에 부분 성공 기록

## 6. 테스트 계획

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | `test_detect_confirmed_receiving_only` | recv_qty > 0만 감지, plan_qty만 있는 건 무시 |
| 2 | `test_skip_existing_product` | products에 이미 있는 상품은 감지 안 함 |
| 3 | `test_register_to_products` | common.db.products에 INSERT 확인 |
| 4 | `test_register_to_product_details` | product_details 기본값 INSERT 확인 |
| 5 | `test_register_to_inventory` | realtime_inventory에 입고수량으로 등록 |
| 6 | `test_detected_new_products_history` | 이력 테이블 기록 확인 |
| 7 | `test_duplicate_detection_prevention` | 동일 상품 동일 날짜 중복 감지 방지 |
| 8 | `test_multiple_new_products` | 한 번에 여러 신제품 감지 |
| 9 | `test_partial_registration_failure` | 일부 등록 실패 시 이력에 부분 성공 기록 |
| 10 | `test_no_impact_on_existing_flow` | 감지 실패 시 기존 플로우 정상 진행 |
| 11 | `test_mid_cd_fallback_estimation` | mid_cd 추정값 저장 확인 |
| 12 | `test_stats_return` | 반환 stats에 new_products_detected/registered 포함 |
| 13 | `test_web_api_detected_list` | API 조회 응답 확인 |
| 14 | `test_web_api_unregistered` | 미등록 목록 API 확인 |
| 15 | `test_schema_migration_v45` | v45 마이그레이션 정상 적용 |

## 7. 구현 순서

```
Step 1: DB 스키마 (schema.py, models.py, constants.py)
Step 2: DetectedNewProductRepository (신규 파일)
Step 3: repos/__init__.py re-export
Step 4: ReceivingCollector 수정 (감지 + 등록 로직)
Step 5: 웹 API 엔드포인트
Step 6: 테스트 작성
Step 7: 기존 테스트 전체 통과 확인
```
