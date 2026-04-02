# Design: 신제품 감지 보완 (new-product-detection)

> 작성일: 2026-04-02
> Plan 참조: `docs/01-plan/features/new-product-detection.plan.md`
> 상태: Design

---

## 1. 변경 대상 및 구현 순서

| 순서 | 파일 | 변경 유형 | 설명 |
|:---:|------|:---:|------|
| 1 | `detected_new_product_repo.py` | 수정 | common.db 등록/조회 메서드 추가 |
| 2 | `receiving_collector.py` | 수정 | 감지 로직 보완 |
| 3 | `scripts/migrate_missing_new_products.py` | 신규 | 기존 누락분 소급 등록 |

---

## 2. 상세 설계

### 2.1 DetectedNewProductRepository 확장

**파일**: `src/infrastructure/database/repos/detected_new_product_repo.py`

#### 2.1.1 `exists()` 메서드 추가

```python
def exists(self, item_cd: str, store_id: Optional[str] = None) -> bool:
    """해당 상품이 detected_new_products에 존재하는지 확인

    Args:
        item_cd: 상품코드
        store_id: 매장코드 (None이면 store_id 무관하게 존재 여부)
    Returns:
        존재 여부
    """
    conn = self._get_conn()
    try:
        cursor = conn.cursor()
        if store_id:
            cursor.execute(
                "SELECT 1 FROM detected_new_products WHERE item_cd = ? AND store_id = ? LIMIT 1",
                (item_cd, store_id)
            )
        else:
            cursor.execute(
                "SELECT 1 FROM detected_new_products WHERE item_cd = ? LIMIT 1",
                (item_cd,)
            )
        return cursor.fetchone() is not None
    finally:
        conn.close()
```

#### 2.1.2 `exists_in_common()` 메서드 추가

```python
def exists_in_common(self, item_cd: str) -> bool:
    """common.db의 detected_new_products에 존재하는지 확인

    Returns:
        존재 여부
    """
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_common_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM detected_new_products WHERE item_cd = ? LIMIT 1",
                (item_cd,)
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return False
```

#### 2.1.3 `get_from_common()` 메서드 추가

```python
def get_from_common(self, item_cd: str) -> Optional[Dict[str, Any]]:
    """common.db에서 신제품 정보 조회 (다른 매장 참조용)

    Returns:
        신제품 정보 dict 또는 None
    """
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_common_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM detected_new_products WHERE item_cd = ? LIMIT 1",
                (item_cd,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None
```

#### 2.1.4 `save_to_common()` 메서드 추가

```python
def save_to_common(
    self,
    item_cd: str,
    item_nm: str,
    mid_cd: str,
    mid_cd_source: str,
    first_receiving_date: str,
    receiving_qty: int,
    order_unit_qty: int = 1,
    center_cd: Optional[str] = None,
    center_nm: Optional[str] = None,
    cust_nm: Optional[str] = None,
    store_id: Optional[str] = None,
) -> bool:
    """common.db에 신제품 등록 (매장 간 공유)

    UPSERT: item_cd 기준. 이미 등록된 경우 첫 매장 정보 유지.
    common.db에는 registered_to_* 플래그 불필요 (매장별 상태이므로).

    Returns:
        성공 여부
    """
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_common_connection()
        try:
            cursor = conn.cursor()
            now = self._now()
            cursor.execute(
                """
                INSERT INTO detected_new_products
                (item_cd, item_nm, mid_cd, mid_cd_source,
                 first_receiving_date, receiving_qty, order_unit_qty,
                 center_cd, center_nm, cust_nm,
                 registered_to_products, registered_to_details, registered_to_inventory,
                 detected_at, store_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
                ON CONFLICT(item_cd, first_receiving_date) DO NOTHING
                """,
                (
                    item_cd, item_nm, mid_cd, mid_cd_source,
                    first_receiving_date, receiving_qty, order_unit_qty,
                    center_cd, center_nm, cust_nm,
                    now, store_id,
                )
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"common.db 신제품 등록 실패 ({item_cd}): {e}")
        return False
```

**설계 포인트:**
- `ON CONFLICT DO NOTHING`: 먼저 등록한 매장의 정보 보존 (멱등성)
- `registered_to_*`는 0 고정: common.db에서는 매장별 등록 상태가 무의미
- 실패해도 bool 반환만, 예외 전파 안 함 (store DB 등록과 독립)

#### 2.1.5 `get_all_item_cds()` 메서드 추가 (배치 캐싱용)

```python
def get_all_item_cds(self, store_id: Optional[str] = None) -> set:
    """현재 매장의 detected_new_products에 등록된 모든 item_cd 반환

    성능 최적화: _is_already_detected에서 상품마다 DB 조회 대신
    한 번에 전체 로딩 후 set lookup 사용

    Returns:
        item_cd set
    """
    conn = self._get_conn()
    try:
        cursor = conn.cursor()
        query = "SELECT item_cd FROM detected_new_products"
        params = []
        if store_id:
            query += " WHERE store_id = ?"
            params.append(store_id)
        cursor.execute(query, params)
        return {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()

def get_all_item_cds_from_common(self) -> set:
    """common.db의 모든 item_cd 반환 (배치 캐싱용)"""
    try:
        from src.infrastructure.database.connection import DBRouter
        conn = DBRouter.get_common_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT item_cd FROM detected_new_products")
            return {row[0] for row in cursor.fetchall()}
        finally:
            conn.close()
    except Exception:
        return set()
```

---

### 2.2 ReceivingCollector 변경

**파일**: `src/collectors/receiving_collector.py`

#### 2.2.1 캐시 초기화 (`__init__` 또는 `collect_and_save` 시작부)

```python
# 기존
self._new_product_candidates: List[Dict] = []

# 추가
self._detected_item_cds: Optional[set] = None       # store DB 캐시
self._detected_common_cds: Optional[set] = None      # common.db 캐시
```

#### 2.2.2 캐시 로딩 메서드 (fail-safe)

```python
def _load_detected_cache(self) -> None:
    """detected_new_products 캐시 한 번만 로딩 (배치 최적화)

    fail-safe 설계: 캐시 로딩 실패 시 None 유지
    → _get_mid_cd에서 None이면 신제품 후보 추가 안 함
    → 그날 신제품 감지 불가지만 오감지(대량 등록) 방지
    """
    if self._detected_item_cds is not None:
        return  # 이미 로딩됨 (성공 or 실패 후 재시도 방지)

    try:
        from src.infrastructure.database.repos import DetectedNewProductRepository
        repo = DetectedNewProductRepository(store_id=self.store_id)
        self._detected_item_cds = repo.get_all_item_cds(store_id=self.store_id)
        self._detected_common_cds = repo.get_all_item_cds_from_common()
    except Exception as e:
        logger.warning(f"detected_new_products 캐시 로딩 실패 → 신제품 감지 스킵: {e}")
        # ★ fail-safe: None 유지 (set()으로 초기화하지 않음)
        # _get_mid_cd에서 None 체크로 신제품 후보 추가 방지
```

#### 2.2.3 `_get_mid_cd` 변경 (핵심)

**현재 (line 642-672):**
```python
if row and row[0]:
    return row[0]           # ← products에 있으면 바로 리턴
```

**변경:**
```python
if row and row[0]:
    mid_cd = row[0]
    # ★ products에 있어도 detected_new_products에 없으면 신제품 후보 추가
    self._load_detected_cache()
    # fail-safe: 캐시 로딩 실패(None)이면 신제품 감지 스킵
    if (self._detected_item_cds is not None
            and item_cd not in self._detected_item_cds):
        self._new_product_candidates.append({
            "item_cd": item_cd,
            "item_nm": item_nm,
            "cust_nm": cust_nm,
            "mid_cd": mid_cd,
            "mid_cd_source": "products",
            "already_in_products": True,
        })
    return mid_cd
```

**변경 범위**: `_get_mid_cd` 메서드의 `if row and row[0]:` 블록 내부에 6줄 추가만.
기존 `return row[0]`의 위치와 값은 동일 → **기존 동작(mid_cd 반환) 불변**.

#### 2.2.4 `_detect_and_register_new_products` 변경

**현재 (line 1244-1261)**: 후보 중 입고 확정 + products 미등록만 필터

**변경**: `already_in_products=True`인 후보도 통과시키되, 30일 기간 제한 적용

```python
for candidate in self._new_product_candidates:
    item_cd = candidate["item_cd"]
    if item_cd in seen:
        continue
    if item_cd not in confirmed_items:
        continue  # 입고 예정/미확정 → 무시

    # ★ 추가: already_in_products인 경우 30일 제한 체크
    if candidate.get("already_in_products"):
        recv_date = confirmed_items[item_cd].get("receiving_date", "")
        if not self._is_within_days(recv_date, 30):
            continue  # 30일 초과 → 오래된 상품, 스킵

    seen.add(item_cd)
    # ... 이하 기존 로직 동일
```

**`_is_within_days` 헬퍼:**
```python
def _is_within_days(self, date_str: str, days: int) -> bool:
    """date_str이 오늘 기준 N일 이내인지"""
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt >= datetime.now() - timedelta(days=days)
    except (ValueError, TypeError):
        return False
```

#### 2.2.5 `_register_single_new_product` 변경

기존 4단계 + 5단계(common.db) 추가:

```python
# 기존 1~4단계 그대로 유지

# ★ 추가: already_in_products인 경우 products 재등록 스킵
if product.get("already_in_products"):
    registered["products"] = True  # 이미 있으므로 스킵
    # product_details, realtime_inventory는 정상 진행 (없을 수 있으므로)

# 기존 1) products (INSERT OR IGNORE → already_in_products면 스킵)
# 기존 2) product_details
# 기존 3) realtime_inventory
# 기존 4) detected_new_products (store DB)

# ★ 5) detected_new_products (common.db) — 신규
try:
    from src.infrastructure.database.repos import DetectedNewProductRepository
    detect_repo = DetectedNewProductRepository(store_id=self.store_id)
    detect_repo.save_to_common(
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
        store_id=store_id,
    )
except Exception as e:
    logger.warning(f"common.db 신제품 등록 실패 ({item_cd}): {e}")
    # ★ common.db 실패해도 store DB 등록은 이미 완료 → 루프 없음
```

---

### 2.3 소급 마이그레이션 스크립트

**파일**: `scripts/migrate_missing_new_products.py`

```python
"""기존 누락 신제품 소급 등록

receiving_history에 입고 기록이 있지만 detected_new_products에 없는 상품을
store DB + common.db 양쪽에 등록합니다.

Usage:
    python scripts/migrate_missing_new_products.py --dry-run  # 미리보기
    python scripts/migrate_missing_new_products.py            # 실행
"""

로직:
1. 각 매장 DB에서:
   - receiving_history의 고유 item_cd 조회 (MIN(receiving_date) 포함)
   - detected_new_products의 item_cd set 조회
   - 차집합 = 누락 상품
2. 누락 상품 중 MIN(receiving_date)가 30일 이내인 것만 필터
3. DetectedNewProductRepository.save() → store DB 등록
4. DetectedNewProductRepository.save_to_common() → common.db 등록
5. 결과 출력 (매장별 등록 건수)
```

---

## 3. 루프 방지 설계 (상세)

### 3.1 데이터 흐름도

```
                    감지 판단                    등록
                ┌──────────────┐        ┌──────────────────────┐
입고 수집 →     │ store DB     │   OK   │ 1) store DB          │
상품 발견 →  →  │ detected     │ ──────→│ detected_new_products│──→ 완료
                │ 에 있나?     │        │                      │
                └──────┬───────┘        │ 2) common.db         │
                       │ NO             │ detected_new_products│
                       ▼                │ (실패해도 OK)         │
                ┌──────────────┐        └──────────────────────┘
                │ common.db    │
                │ detected     │   참조: common.db 정보를
                │ 에 있나?     │   store DB 등록 시 활용
                └──────┬───────┘   (예: similar_item_avg 복사)
                       │
            ┌──────────┴──────────┐
            │ YES                 │ NO
            │ 다른 매장에서       │ 어디에도 없음
            │ 이미 감지됨        │ + 30일 이내?
            ▼                     ▼
         store DB에            신제품 후보
         등록 (참조 활용)      → 양쪽 등록
```

### 3.2 핵심 불변식

| 불변식 | 설명 | 보장 방법 |
|--------|------|-----------|
| **감지 판단 독립성** | common.db 상태와 무관하게 store DB만으로 감지 판단 | `_detected_item_cds`(store DB)를 1순위로 체크 |
| **등록 독립성** | common.db 등록 실패가 store DB 등록을 차단하지 않음 | try/except 분리, common.db는 맨 마지막 단계 |
| **멱등성** | 같은 상품을 여러 번 감지해도 결과 동일 | UPSERT(store) + DO NOTHING(common) |
| **단방향 의존** | store DB → common.db 방향으로만 쓰기, 역방향 없음 | common.db는 읽기/참조만 |

---

## 4. 기존 동작 영향 분석

| 기존 기능 | 영향 | 이유 |
|-----------|:---:|------|
| products 미등록 상품 감지 | 없음 | 기존 `_get_mid_cd` 하단 로직 그대로 유지 |
| `_detect_and_register_new_products` | 최소 | 후보 필터에 `already_in_products` 조건만 추가 |
| `_register_single_new_product` | 최소 | 5단계(common.db) 추가만, 기존 1~4단계 불변 |
| `_apply_new_product_boost` (예측) | 없음 | store DB `detected_new_products` 읽기만 (변경 없음) |
| `new_product_settlement_service` | 없음 | store DB lifecycle 업데이트만 (변경 없음) |
| `prediction_cache.load_new_products` | 없음 | store DB에서 monitoring 조회 (변경 없음) |

---

## 5. 토론 결과: 멱등성/일관성/무결점

### 결정사항

| 주제 | 결정 | 이유 |
|------|------|------|
| **캐시 실패 시** | fail-safe (None 유지 → 감지 스킵) | 대량 오감지 방지가 우선. 다음 실행에서 재시도됨 |
| **common.db 누락 보정** | 불필요 (베스트 에포트) | common.db는 참조용, store DB가 권위 데이터 |
| **store↔common 비대칭** | 허용 | store=UPSERT(최신화), common=DO NOTHING(최초 보존). 역할이 다르므로 정합 |

### 무결점 엣지 케이스 대응

| 케이스 | 대응 |
|--------|------|
| receiving_date=NULL | `_is_within_days` → False → 안전 스킵 |
| 캐시 실패(DB 접근 불가) | None 유지 → `is not None` 가드로 전체 스킵 |
| 같은 상품 다른 날짜 2건 입고 | UNIQUE(item_cd, first_receiving_date) → 날짜별 기록, 캐시는 item_cd 기준 스킵 |
| common.db 등록 실패 | try/except로 격리, store DB는 이미 완료 |

---

## 6. 구현 체크리스트

- [ ] `detected_new_product_repo.py`: exists(), exists_in_common(), get_from_common(), save_to_common(), get_all_item_cds(), get_all_item_cds_from_common()
- [ ] `receiving_collector.py`: _detected_item_cds/_detected_common_cds 캐시, _load_detected_cache(), _get_mid_cd 분기 추가, _detect_and_register_new_products 30일 필터, _register_single_new_product common.db 등록, _is_within_days()
- [ ] `scripts/migrate_missing_new_products.py`: --dry-run 지원, 4매장 순회, 30일 필터
- [ ] 테스트: Plan 시나리오 6개 모두 커버
