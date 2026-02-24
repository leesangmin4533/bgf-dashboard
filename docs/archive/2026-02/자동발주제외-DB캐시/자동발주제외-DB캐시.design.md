# Design: 자동발주제외 DB 캐시 (Auto-Order Exclusion DB Cache)

> Plan 참조: `docs/01-plan/features/자동발주제외-DB캐시.plan.md`

---

## 1. 설계 개요

사이트 조회 성공 시 자동발주 상품을 DB에 캐싱하고, 사이트 접속 실패 시 DB 캐시를 fallback으로 사용하는 레이어를 추가한다.

### 변경 범위

| # | 파일 | 변경 유형 | 설명 |
|---|------|----------|------|
| 1 | `src/config/constants.py` | 수정 | `DB_SCHEMA_VERSION` 14 → 15 |
| 2 | `src/db/models.py` | 수정 | `SCHEMA_MIGRATIONS[15]` — `auto_order_items` 테이블 |
| 3 | `src/db/repository.py` | 수정 | `AutoOrderItemRepository` 클래스 추가 (파일 끝) |
| 4 | `src/collectors/order_status_collector.py` | 수정 | `collect_auto_order_items_detail()` 메서드 추가 |
| 5 | `src/order/auto_order.py` | 수정 | `load_auto_order_items()` DB fallback/캐시 통합 |

---

## 2. DB 스키마: Schema v15

**파일**: `src/db/models.py` (line 444, `SCHEMA_MIGRATIONS` 딕셔너리 끝)

**파일**: `src/config/constants.py` (line 163)

### 2-1. constants.py 변경

```python
# 변경 전
DB_SCHEMA_VERSION = 14

# 변경 후
DB_SCHEMA_VERSION = 15
```

### 2-2. models.py — SCHEMA_MIGRATIONS[15]

```python
15: """
-- 자동발주 상품 캐시 테이블
CREATE TABLE IF NOT EXISTS auto_order_items (
    item_cd TEXT PRIMARY KEY,
    item_nm TEXT,
    mid_cd TEXT,
    detected_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auto_order_items_updated
    ON auto_order_items(updated_at);
""",
```

삽입 위치: `SCHEMA_MIGRATIONS` 딕셔너리의 `14: """...""",` 뒤, `}` 닫는 괄호 전.

### 2-3. 테이블 설계

```
auto_order_items
├── item_cd TEXT PRIMARY KEY   -- 상품코드 (유일키)
├── item_nm TEXT               -- 상품명 (디버그/로그용)
├── mid_cd TEXT                -- 중분류 코드
├── detected_at TEXT           -- 최초 감지 시점
└── updated_at TEXT            -- 마지막 사이트 확인 시점
```

- `active` 컬럼 불필요: 사이트 조회 성공 시 전체 교체 (DELETE + INSERT)
- `updated_at`: 캐시 신선도 판단용 (오래된 캐시 경고)

---

## 3. AutoOrderItemRepository 설계

**파일**: `src/db/repository.py` (파일 끝에 추가, line 3483 이후)

### 3-1. 클래스 정의

```python
class AutoOrderItemRepository(BaseRepository):
    """자동발주 상품 캐시 저장소

    사이트 '발주 현황 조회 > 자동' 탭의 상품 목록을 캐싱.
    사이트 조회 성공 시 refresh()로 전체 교체, 실패 시 DB 캐시 사용.
    """

    def get_all_item_codes(self) -> List[str]:
        """캐싱된 자동발주 상품코드 목록"""

    def get_all_detail(self) -> List[Dict[str, Any]]:
        """상세 정보 포함 전체 목록"""

    def refresh(self, items: List[Dict[str, str]]) -> int:
        """사이트 조회 결과로 전체 교체 (트랜잭션)"""

    def get_count(self) -> int:
        """캐시된 상품 수"""

    def get_last_updated(self) -> Optional[str]:
        """마지막 캐시 갱신 시각"""
```

### 3-2. 메서드 상세

#### `get_all_item_codes(self) -> List[str]`

```python
def get_all_item_codes(self) -> List[str]:
    """캐싱된 자동발주 상품코드 목록 반환"""
    conn = self._get_conn()
    try:
        rows = conn.execute(
            "SELECT item_cd FROM auto_order_items"
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()
```

#### `get_all_detail(self) -> List[Dict[str, Any]]`

```python
def get_all_detail(self) -> List[Dict[str, Any]]:
    """상세 정보 포함 전체 목록"""
    conn = self._get_conn()
    try:
        rows = conn.execute(
            "SELECT item_cd, item_nm, mid_cd, detected_at, updated_at "
            "FROM auto_order_items ORDER BY item_cd"
        ).fetchall()
        return [
            {
                "item_cd": r[0], "item_nm": r[1], "mid_cd": r[2],
                "detected_at": r[3], "updated_at": r[4]
            }
            for r in rows
        ]
    finally:
        conn.close()
```

#### `refresh(self, items: List[Dict[str, str]]) -> int`

핵심 메서드. 사이트 조회 결과로 전체 교체.

```python
def refresh(self, items: List[Dict[str, str]]) -> int:
    """사이트 조회 결과로 전체 교체

    0건 보호: items가 빈 리스트이면 기존 캐시 유지 (삭제하지 않음).

    Args:
        items: [{"item_cd": "...", "item_nm": "...", "mid_cd": "..."}, ...]

    Returns:
        저장된 상품 수
    """
    if not items:
        return 0

    conn = self._get_conn()
    now = self._now()
    try:
        cursor = conn.cursor()
        # 기존 데이터 삭제
        cursor.execute("DELETE FROM auto_order_items")

        # 신규 데이터 일괄 삽입
        cursor.executemany(
            """INSERT INTO auto_order_items (item_cd, item_nm, mid_cd, detected_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    item.get("item_cd"),
                    item.get("item_nm"),
                    item.get("mid_cd"),
                    now,
                    now
                )
                for item in items if item.get("item_cd")
            ]
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
```

#### `get_count(self) -> int`

```python
def get_count(self) -> int:
    """캐시된 상품 수"""
    conn = self._get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) FROM auto_order_items").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
```

#### `get_last_updated(self) -> Optional[str]`

```python
def get_last_updated(self) -> Optional[str]:
    """마지막 캐시 갱신 시각"""
    conn = self._get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM auto_order_items"
        ).fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()
```

---

## 4. OrderStatusCollector 확장

**파일**: `src/collectors/order_status_collector.py`

### 4-1. `collect_auto_order_items_detail()` 추가

기존 `collect_auto_order_items()` (Set[str] 반환)을 유지하면서, 상세 데이터를 반환하는 메서드를 추가한다.

삽입 위치: `collect_auto_order_items()` (line 423) 뒤, `close_menu()` (line 475) 전.

```python
def collect_auto_order_items_detail(self) -> List[Dict[str, str]]:
    """자동발주 상품 상세 목록 수집 (DB 캐시용)

    collect_auto_order_items()와 동일한 플로우이나,
    ITEM_CD 외에 ITEM_NM, MID_CD도 함께 반환.

    Returns:
        [{"item_cd": "...", "item_nm": "...", "mid_cd": "..."}, ...]
        실패 시 빈 리스트
    """
    if not self.driver:
        return []

    # 자동 라디오 클릭
    if not self.click_auto_radio():
        logger.warning("자동 라디오 클릭 실패 — 빈 목록 반환")
        return []

    # 데이터 갱신 대기
    time.sleep(OS_RADIO_CLICK_WAIT)

    # dsResult에서 ITEM_CD, ITEM_NM, MID_CD 추출
    result = self.driver.execute_script(f"""
        try {{
            const app = nexacro.getApplication();
            const wf = app.mainframe.HFrameSet00.VFrameSet00.FrameSet
                .{self.FRAME_ID}.form.{self.DS_PATH};
            const ds = wf?.dsResult;
            if (!ds) return {{error: 'dsResult not found'}};

            function getVal(row, col) {{
                let val = ds.getColumn(row, col);
                if (val && typeof val === 'object' && val.hi !== undefined) val = val.hi;
                return val ? String(val) : '';
            }}

            const items = [];
            for (let i = 0; i < ds.getRowCount(); i++) {{
                const cd = getVal(i, 'ITEM_CD');
                if (cd) {{
                    items.push({{
                        item_cd: cd,
                        item_nm: getVal(i, 'ITEM_NM'),
                        mid_cd: getVal(i, 'MID_CD')
                    }});
                }}
            }}
            return {{items: items, total: ds.getRowCount()}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)

    if not result or result.get('error'):
        logger.warning(f"자동발주 상품 상세 조회 실패: {result}")
        return []

    items = result.get('items', [])
    logger.info(f"자동발주 상품 상세: {len(items)}개 (dsResult 총 {result.get('total', 0)}행)")
    return items
```

### 4-2. 기존 `collect_auto_order_items()` 변경 여부

변경 없음. 기존 `collect_auto_order_items()` → `Set[str]` 는 그대로 유지. `collect_auto_order_items_detail()`이 독립 메서드로 추가됨.

> **이유**: `collect_auto_order_items()`를 호출하는 곳이 `auto_order.py`의 `load_auto_order_items()` 한 곳인데, 이 메서드 자체를 `collect_auto_order_items_detail()` 호출로 변경할 것이므로, 기존 메서드는 호환성을 위해 유지.

---

## 5. auto_order.py 통합 설계

**파일**: `src/order/auto_order.py`

### 5-1. import 추가

```python
# 기존
from src.db.repository import (
    ...,
    RealtimeInventoryRepository,
)

# 추가
from src.db.repository import AutoOrderItemRepository
```

### 5-2. `__init__()` 변경 (line 117 부근)

```python
# 기존
self._inventory_repo = RealtimeInventoryRepository()

# 추가 (바로 아래)
self._auto_order_repo = AutoOrderItemRepository()
```

### 5-3. `load_auto_order_items()` 전면 변경 (line 141~175)

기존 사이트 전용 → DB fallback 통합:

```python
def load_auto_order_items(self) -> None:
    """
    자동발주 상품 목록 조회 (사이트 우선 + DB 캐시 fallback)

    1. 드라이버 있음 → 사이트 조회 시도
       - 성공 → _auto_order_items에 적용 + DB 캐시 갱신
       - 실패 → DB 캐시에서 로드 (fallback)
    2. 드라이버 없음 → DB 캐시에서 로드
    """
    site_success = False

    # --- 사이트 조회 시도 ---
    if self.driver:
        try:
            from src.collectors.order_status_collector import OrderStatusCollector

            collector = OrderStatusCollector(self.driver)

            if not collector.navigate_to_order_status_menu():
                logger.warning("발주 현황 조회 메뉴 이동 실패")
            else:
                # 상세 데이터 수집 (DB 캐시용)
                detail_items = collector.collect_auto_order_items_detail()

                if detail_items:
                    # 메모리에 적용
                    self._auto_order_items = {
                        item["item_cd"] for item in detail_items
                    }
                    # DB 캐시 갱신
                    saved = self._auto_order_repo.refresh(detail_items)
                    logger.info(
                        f"자동발주 상품 {len(self._auto_order_items)}개 "
                        f"사이트 조회 완료 (DB 캐시 {saved}건 갱신)"
                    )
                    site_success = True
                else:
                    logger.info("자동발주 상품 없음 (사이트 조회)")
                    site_success = True  # 0건도 성공 (캐시 삭제는 안 함)

                collector.close_menu()

        except Exception as e:
            logger.warning(f"자동발주 사이트 조회 실패: {e}")

    # --- DB 캐시 fallback ---
    if not site_success:
        cached_items = self._auto_order_repo.get_all_item_codes()
        if cached_items:
            self._auto_order_items = set(cached_items)
            last_updated = self._auto_order_repo.get_last_updated()
            logger.info(
                f"자동발주 상품 DB 캐시 {len(cached_items)}개 사용 "
                f"(마지막 갱신: {last_updated})"
            )
        else:
            logger.info("자동발주 상품: 사이트 조회 실패 + DB 캐시 없음 — 제외 없이 진행")
```

### 5-4. `execute()` 변경

변경 없음. 기존 line 654의 `self.load_auto_order_items()` 호출이 내부적으로 DB fallback을 사용하게 됨.

---

## 6. 전체 흐름도 (변경 후)

```
execute()
  │
  ├─ load_unavailable_from_db()           # DB 전용
  ├─ load_cut_items_from_db()             # DB 전용
  │
  ├─ load_auto_order_items()              # ★ 변경
  │   ├─ [드라이버 있음]
  │   │   ├─ navigate_to_order_status_menu()
  │   │   ├─ collect_auto_order_items_detail()   ← 신규 (상세)
  │   │   │   ├─ click_auto_radio()
  │   │   │   └─ dsResult → [{item_cd, item_nm, mid_cd}, ...]
  │   │   ├─ _auto_order_items = {item_cd set}
  │   │   ├─ _auto_order_repo.refresh(detail)     ← 신규 (DB 캐시 갱신)
  │   │   └─ close_menu()
  │   │
  │   └─ [사이트 실패 또는 드라이버 없음]
  │       └─ _auto_order_repo.get_all_item_codes()  ← 신규 (DB fallback)
  │
  ├─ get_recommendations()
  │   ├─ _unavailable_items 제외
  │   ├─ _cut_items 제외
  │   └─ _auto_order_items 제외            # 기존과 동일
  │
  ├─ prefetch_pending_quantities()
  └─ executor.execute_orders()
```

---

## 7. 에러 핸들링 정책

| 시나리오 | 동작 |
|---------|------|
| 사이트 성공 + DB 저장 성공 | 사이트 데이터 사용, DB 캐시 갱신 |
| 사이트 성공 + DB 저장 실패 | 사이트 데이터 사용 (메모리), DB 오류 로그 |
| 사이트 성공 + 0건 반환 | info 로그, DB 캐시 삭제하지 않음 (0건 보호) |
| 사이트 실패 + DB 캐시 있음 | DB 캐시 사용, warning 로그 |
| 사이트 실패 + DB 캐시 없음 | 빈 set → 발주 진행, info 로그 |
| 드라이버 없음 + DB 캐시 있음 | DB 캐시 사용 (preview 모드 지원) |
| 드라이버 없음 + DB 캐시 없음 | 빈 set → 발주 진행 |
| DB 테이블 미존재 (마이그레이션 전) | sqlite3 OperationalError → 빈 리스트 반환 |

핵심: **자동발주 조회/캐시 관련 어떤 실패도 발주를 차단하지 않는다.**

---

## 8. 구현 순서

```
Step 1: src/config/constants.py
        — DB_SCHEMA_VERSION = 15

Step 2: src/db/models.py
        — SCHEMA_MIGRATIONS[15] 추가

Step 3: src/db/repository.py
        — AutoOrderItemRepository 클래스 추가 (파일 끝)
        — 5개 메서드: get_all_item_codes, get_all_detail, refresh, get_count, get_last_updated

Step 4: src/collectors/order_status_collector.py
        — collect_auto_order_items_detail() 메서드 추가

Step 5: src/order/auto_order.py
        — import AutoOrderItemRepository 추가
        — __init__(): self._auto_order_repo 초기화
        — load_auto_order_items(): 사이트+DB fallback 통합 로직으로 전면 교체
```

---

## 9. 검증 방법

### 9-1. 스키마 검증

```bash
cd bgf_auto
python -c "
from src.db.models import init_db
init_db()
print('Migration v15 OK')
"
```

### 9-2. Repository 단위 검증

```bash
python -c "
from src.db.repository import AutoOrderItemRepository
repo = AutoOrderItemRepository()

# refresh 테스트
items = [
    {'item_cd': 'TEST001', 'item_nm': '테스트상품1', 'mid_cd': '049'},
    {'item_cd': 'TEST002', 'item_nm': '테스트상품2', 'mid_cd': '050'},
]
saved = repo.refresh(items)
print(f'저장: {saved}건')

# 조회 테스트
codes = repo.get_all_item_codes()
print(f'코드 목록: {codes}')
print(f'상품 수: {repo.get_count()}')
print(f'마지막 갱신: {repo.get_last_updated()}')

# 0건 보호 테스트
repo.refresh([])  # 빈 리스트 → 기존 데이터 유지
assert repo.get_count() == 2, '0건 보호 실패!'
print('0건 보호 OK')

# 전체 교체 테스트
repo.refresh([{'item_cd': 'TEST003', 'item_nm': '교체상품', 'mid_cd': '001'}])
assert repo.get_count() == 1, '전체 교체 실패!'
print('전체 교체 OK')
"
```

### 9-3. 통합 검증

```bash
# preview 모드에서 DB 캐시 사용 확인
python scripts/run_auto_order.py --preview
# 로그에서 "DB 캐시 N개 사용" 또는 "사이트 조회 완료 (DB 캐시 N건 갱신)" 확인
```

### 9-4. py_compile 검증

```bash
python -m py_compile src/config/constants.py
python -m py_compile src/db/models.py
python -m py_compile src/db/repository.py
python -m py_compile src/collectors/order_status_collector.py
python -m py_compile src/order/auto_order.py
```
