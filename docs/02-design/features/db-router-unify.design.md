# Design: sqlite3.connect → DBRouter 통합

> Plan: `docs/01-plan/features/db-router-unify.plan.md`

## 1. 신규 파일: `src/prediction/categories/_db.py`

```python
"""카테고리 Strategy 공용 DB 커넥션 헬퍼

15개 카테고리 파일의 중복 _get_db_path() + sqlite3.connect() 패턴을
DBRouter로 통합. WAL + busy_timeout + row_factory 자동 적용.
"""
import sqlite3
from typing import Optional
from src.infrastructure.database.connection import DBRouter, resolve_db_path


def get_conn(store_id: Optional[str] = None, db_path: Optional[str] = None) -> sqlite3.Connection:
    """매장/공통 DB 연결 (WAL + busy_timeout 자동 적용)

    Args:
        store_id: 매장 ID → stores/{id}.db
        db_path: 명시적 경로 (테스트/오버라이드) → 직접 연결

    Returns:
        sqlite3.Connection (WAL + busy_timeout=30s + row_factory=Row)
    """
    if db_path:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn
    if store_id:
        return DBRouter.get_store_connection(store_id)
    return DBRouter.get_common_connection()
```

**설계 포인트**:
- `db_path` 우선: 테스트에서 in-memory DB나 임시 파일 전달 시 호환
- `db_path` 경로에도 WAL+busy_timeout 적용 (일관성)
- `store_id`만 전달 시 DBRouter 사용

---

## 2. 15개 카테고리 파일 수정 패턴

### 공통 변경 (모든 파일)

```python
# === 삭제 ===
def _get_db_path(store_id: str = None) -> str:
    """DB 경로 반환"""
    from src.infrastructure.database.connection import resolve_db_path
    return resolve_db_path(store_id=store_id)

# === 삭제 (import) ===
import sqlite3  # ← 파일 상단에서 제거 (get_conn이 대체)

# === 추가 (import) ===
from src.prediction.categories._db import get_conn
```

### 변환 패턴 A: db_path 파라미터 있는 함수 (대부분)

```python
# Before
def analyze_beer_pattern(item_cd, db_path=None, ..., store_id=None):
    if db_path is None:
        db_path = _get_db_path(store_id)
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

# After
def analyze_beer_pattern(item_cd, db_path=None, ..., store_id=None):
    conn = get_conn(store_id=store_id, db_path=db_path)
    cursor = conn.cursor()
```

### 변환 패턴 B: common DB 직접 접속 (food.py:316)

```python
# Before (food.py _get_dessert_expiration_days)
common_path = str(DBRouter.get_common_db_path())
conn = sqlite3.connect(common_path, timeout=30)

# After
conn = get_conn()  # store_id=None → common DB
```

### 변환 패턴 C: timeout 없는 connect (3건)

```python
# Before (snack_confection.py:172, alcohol_general.py:117, daily_necessity.py:108)
conn = sqlite3.connect(db_path)  # timeout 누락!

# After
conn = get_conn(store_id=store_id, db_path=db_path)  # timeout=30 자동
```

---

## 3. 파일별 수정 명세

| 파일 | connect 수 | 패턴 | 특이사항 |
|------|-----------|------|---------|
| beer.py | 1 | A | 이상치 cap 로직 포함 (최근 수정) |
| soju.py | 1 | A | beer와 동일 구조 |
| ramen.py | 1 | A | |
| general_merchandise.py | 1 | A | |
| tobacco.py | 2 | A | |
| alcohol_general.py | 2 | A+C | line 117: timeout 누락 |
| snack_confection.py | 2 | A+C | line 172: timeout 누락 |
| daily_necessity.py | 2 | A+C | line 108: timeout 누락 |
| food_daily_cap.py | 2 | A | `_get_db_path()` 대신 별도 함수 |
| beverage.py | 3 | A | |
| perishable.py | 3 | A | |
| dessert.py | 3 | A | |
| instant_meal.py | 3 | A | |
| frozen_ice.py | 4 | A | 최다 (3+1) |
| food.py | 6 | A+B | line 316: common DB 직접 접속 |

---

## 4. `import sqlite3` 제거 여부

각 파일에서 `sqlite3`가 connect 이외에 사용되는지 확인 필요:
- `sqlite3.Row` 직접 참조 → 유지
- `sqlite3.IntegrityError` 등 예외 → 유지
- connect만 사용 → 제거 가능

**안전 전략**: `import sqlite3`는 제거하지 않음. 예외 처리 등에서 사용될 수 있으므로.

---

## 5. 구현 순서

```
Step 1: _db.py 헬퍼 생성
Step 2: beer.py 수정 + 테스트 (파일럿)
Step 3: 나머지 14개 파일 일괄 수정
Step 4: _get_db_path() 함수 제거 (15개 파일)
Step 5: 전체 테스트
Step 6: WAL 적용 확인 + 커밋
```

---

## 6. 테스트 설계

### WAL 적용 확인

```python
def test_get_conn_store_has_wal():
    conn = get_conn(store_id="46513")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()

def test_get_conn_db_path_has_wal(tmp_path):
    db = str(tmp_path / "test.db")
    conn = get_conn(db_path=db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()

def test_get_conn_common_has_wal():
    conn = get_conn()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()
```

### 기존 테스트 회귀

```bash
pytest tests/ -k "beer or food or dessert or beverage or category" -x
pytest tests/ -x -q  # 전체
```

---

## 7. 후행 덮어쓰기 체크

| 체크 | 결과 |
|------|------|
| row_factory=Row가 기존 tuple 접근 깨뜨리는가? | NO — `sqlite3.Row`는 `row[0]` 지원 |
| WAL 모드가 기존 읽기 쿼리 동작 바꾸는가? | NO — 읽기만 하는 카테고리 함수에 영향 없음 |
| busy_timeout 30초가 기존보다 긴가? | 동일 — 기존 `timeout=30` ≈ `busy_timeout=30000` |
| db_path 테스트 호환 유지? | YES — `get_conn(db_path=path)` 경로 유지 |
