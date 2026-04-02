# Plan: sqlite3.connect 직접 호출 → DBRouter 통합

## 1. 개요

### 문제
`src/prediction/categories/` 15개 파일에서 **35건**의 `sqlite3.connect()` 직접 호출이 DBRouter를 우회.

```python
# 현재 (위험) — 15개 파일 × 35건
db_path = _get_db_path(store_id)
conn = sqlite3.connect(db_path, timeout=30)  # WAL 없음, row_factory 없음

# DBRouter (정상) — base_repository.py 등
conn = DBRouter.get_store_connection(store_id)  # WAL + busy_timeout + row_factory 자동
```

### 영향
- **WAL 모드 누락**: 병렬 실행(4매장 동시) 시 "database is locked" 가능
- **busy_timeout 불일치**: DBRouter는 30초, 카테고리는 `timeout=30` (sqlite3 파라미터, 다른 의미)
- **row_factory 누락**: `sqlite3.Row` 미설정 → dict-like 접근 불가 (현재는 tuple index 사용)
- **코드 중복**: 동일한 `_get_db_path()` 함수가 15개 파일에 반복

### 목표
1. 15개 카테고리 파일의 DB 접속을 DBRouter로 통합
2. WAL + busy_timeout 일관 적용
3. `_get_db_path()` 중복 제거
4. 기존 동작 100% 보존 (row_factory 주의)

### 범위
- **이번 작업**: `src/prediction/categories/` 15개 파일, 35건
- **후속 작업**: `src/prediction/` 비카테고리 (~23건), `src/web/routes/` (~15건), 기타

---

## 2. 현행 분석

### _get_db_path() 패턴 (15개 파일 동일)

```python
def _get_db_path(store_id: str = None) -> str:
    from src.infrastructure.database.connection import resolve_db_path
    return resolve_db_path(store_id=store_id)
```

### sqlite3.connect() 호출 패턴

```python
conn = sqlite3.connect(db_path, timeout=30)
cursor = conn.cursor()
cursor.execute("SELECT ... FROM daily_sales WHERE ...")
row = cursor.fetchone()
conn.close()
```

**특징**: `cursor.fetchone()`이 **tuple** 반환 → `row[0]`, `row[1]` 인덱스 접근.
DBRouter는 `row_factory = sqlite3.Row` 설정 → dict-like 접근 가능하지만 **tuple 인덱스도 호환**.

### 대상 파일 및 건수

| 파일 | connect 수 | 비고 |
|------|-----------|------|
| food.py | 6 | 최다 |
| frozen_ice.py | 4 | |
| dessert.py | 3 | |
| beverage.py | 3 | |
| perishable.py | 3 | |
| instant_meal.py | 3 | |
| tobacco.py | 2 | |
| alcohol_general.py | 2 | |
| snack_confection.py | 2 | |
| daily_necessity.py | 2 | |
| food_daily_cap.py | 2 | |
| beer.py | 1 | |
| soju.py | 1 | |
| ramen.py | 1 | |
| general_merchandise.py | 1 | |
| **합계** | **35** | |

---

## 3. 수정 방안

### 공통 헬퍼 도입

**신규 파일**: `src/prediction/categories/_db.py`

```python
"""카테고리 Strategy 공용 DB 커넥션 헬퍼"""
from src.infrastructure.database.connection import DBRouter

def get_conn(store_id=None):
    """매장 DB 연결 (WAL + busy_timeout 자동 적용)

    DBRouter.get_store_connection() 래핑.
    store_id 없으면 공통 DB 반환.
    """
    if store_id:
        return DBRouter.get_store_connection(store_id)
    return DBRouter.get_common_connection()
```

### 각 카테고리 파일 수정

```python
# Before
from src.infrastructure.database.connection import resolve_db_path

def _get_db_path(store_id: str = None) -> str:
    return resolve_db_path(store_id=store_id)

def analyze_beer_pattern(item_cd, db_path=None, ...):
    if db_path is None:
        db_path = _get_db_path(store_id)
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()
    ...
    conn.close()

# After
from src.prediction.categories._db import get_conn

def analyze_beer_pattern(item_cd, db_path=None, ...):
    if db_path is None:
        conn = get_conn(store_id)
    else:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=30)  # 테스트 호환
    cursor = conn.cursor()
    ...
    conn.close()
```

### row_factory 호환성

DBRouter는 `conn.row_factory = sqlite3.Row`를 설정. `sqlite3.Row`는 **tuple 인덱스 접근을 지원**하므로:
- `row[0]`, `row[1]` → 기존대로 동작
- `row["column_name"]` → 추가로 가능

**호환성 문제 없음** — `sqlite3.Row`는 tuple의 상위 호환.

---

## 4. 구현 순서

```
Step 1: _db.py 헬퍼 생성
Step 2: 1건 파일(beer.py) 먼저 수정 + 테스트 검증
Step 3: 나머지 14개 파일 일괄 수정
Step 4: _get_db_path() 함수 제거 (미사용 확인 후)
Step 5: 전체 테스트 + 커밋
```

---

## 5. 테스트 계획

### 호환성 검증
```bash
# 카테고리 관련 테스트
pytest tests/ -k "beer or food or dessert or beverage or category" -x

# 전체 회귀
pytest tests/ -x -q
```

### WAL 적용 확인
```python
from src.prediction.categories._db import get_conn
conn = get_conn("46513")
print(conn.execute("PRAGMA journal_mode").fetchone())  # ('wal',)
conn.close()
```

---

## 6. 위험 분석

| 위험 | 확률 | 완화 |
|------|------|------|
| row_factory 호환 깨짐 | 극히 낮음 | sqlite3.Row는 tuple 인덱스 지원 |
| db_path 파라미터 테스트 깨짐 | 낮음 | db_path 명시 시 기존 방식 유지 |
| import 순환 | 없음 | _db.py → connection.py 단방향 |
| 성능 변화 | 없음 | WAL PRAGMA는 첫 호출 시만 실행, 이후 캐시 |
