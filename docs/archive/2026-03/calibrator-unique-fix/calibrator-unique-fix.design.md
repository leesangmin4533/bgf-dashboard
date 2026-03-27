# calibrator-unique-fix Design

> **Feature**: 폐기율 캘리브레이터 UNIQUE 제약 버그 수정
> **Plan**: [calibrator-unique-fix.plan.md](../../01-plan/features/calibrator-unique-fix.plan.md)
> **Created**: 2026-03-09

---

## 1. 수정 개요

| # | 파일 | 수정 | 영향도 |
|---|------|------|--------|
| A | `schema.py:755` | UNIQUE 제약 변경 (신규 DB용) | 낮음 |
| B | `schema.py` | `_fix_calibration_unique()` 마이그레이션 함수 추가 (기존 DB용) | 중간 |
| C | `schema.py:1121` | `_apply_store_column_patches`에서 마이그레이션 호출 | 낮음 |

총 수정: **1파일**, 약 50줄 추가

---

## 2. 상세 설계

### 2-A. 스키마 변경 (schema.py:755)

**Before:**
```python
        UNIQUE(store_id, mid_cd, calibration_date)
```

**After:**
```python
        UNIQUE(store_id, mid_cd, small_cd, calibration_date)
```

- 신규 생성 DB에만 적용 (CREATE TABLE IF NOT EXISTS)
- 기존 DB는 2-B 마이그레이션으로 처리

### 2-B. 마이그레이션 함수 (신규)

`_fix_calibration_unique(cursor)` — `_fix_promotions_unique` 패턴 동일:

```python
def _fix_calibration_unique(cursor) -> None:
    """food_waste_calibration UNIQUE 제약을
    (store_id, mid_cd, calibration_date) → (store_id, mid_cd, small_cd, calibration_date)로 보정.

    기존 UNIQUE에 small_cd가 없는 경우에만 재생성한다.
    오염 데이터(Phase2가 Phase1을 덮어쓴 행)도 함께 정리한다.
    """
```

**로직:**

1. `sqlite_master`에서 현재 CREATE SQL 조회
2. 이미 `small_cd` 포함 UNIQUE면 스킵 (멱등성)
3. 오염 행 삭제: `small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0` (Phase2가 Phase1을 덮어쓴 무의미한 행)
4. 테이블 재생성 (UNIQUE에 small_cd 추가)
5. 데이터 복사 (`INSERT OR IGNORE` — 충돌 시 무시)
6. 원본 삭제 + 리네임

**재생성 CREATE SQL:**
```sql
CREATE TABLE IF NOT EXISTS food_waste_calibration_fixed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    mid_cd TEXT NOT NULL,
    calibration_date TEXT NOT NULL,
    actual_waste_rate REAL NOT NULL,
    target_waste_rate REAL NOT NULL,
    error REAL NOT NULL,
    sample_days INTEGER NOT NULL,
    total_order_qty INTEGER,
    total_waste_qty INTEGER,
    total_sold_qty INTEGER,
    param_name TEXT,
    old_value REAL,
    new_value REAL,
    current_params TEXT,
    created_at TEXT NOT NULL,
    small_cd TEXT DEFAULT '',
    UNIQUE(store_id, mid_cd, small_cd, calibration_date)
)
```

**데이터 복사:**
```sql
INSERT OR IGNORE INTO food_waste_calibration_fixed
    (store_id, mid_cd, calibration_date,
     actual_waste_rate, target_waste_rate, error,
     sample_days, total_order_qty, total_waste_qty, total_sold_qty,
     param_name, old_value, new_value,
     current_params, created_at, small_cd)
SELECT store_id, mid_cd, calibration_date,
       actual_waste_rate, target_waste_rate, error,
       sample_days, total_order_qty, total_waste_qty, total_sold_qty,
       param_name, old_value, new_value,
       current_params, created_at, COALESCE(small_cd, '')
FROM food_waste_calibration
WHERE NOT (small_cd != '' AND sample_days = 0 AND actual_waste_rate = 0)
```

> `WHERE NOT (...)` 조건으로 오염 행 제외하면서 복사

### 2-C. 호출 위치 (schema.py:1121)

```python
def _apply_store_column_patches(cursor) -> None:
    ...
    _fix_promotions_unique(cursor)
    _fix_calibration_unique(cursor)    # ← 추가
```

---

## 3. 멱등성 보장

| 실행 횟수 | 동작 |
|----------|------|
| 1회차 | UNIQUE 보정 실행, 오염 행 제거, 테이블 재생성 |
| 2회차+ | `small_cd` 이미 UNIQUE에 포함 → 스킵 |

판별 조건: `"store_id, mid_cd, small_cd, calibration_date" in create_sql`

---

## 4. 변경하지 않는 것

- `food_waste_calibrator.py` — 코드 변경 없음 (INSERT OR REPLACE 로직은 새 UNIQUE에서 정상 동작)
- `get_calibrated_food_params()` — 조회 로직 변경 없음 (기존 `WHERE small_cd=''` 필터 유지)
- 인덱스 — 기존 `idx_food_waste_cal_small_cd` 인덱스 유지 (UNIQUE와 중복이나 무해)

---

## 5. 구현 순서

```
Step 1. schema.py:755 UNIQUE 수정 (1줄)
Step 2. schema.py에 _fix_calibration_unique() 추가 (~40줄)
Step 3. schema.py:1121 _apply_store_column_patches에 호출 추가 (1줄)
Step 4. 테스트 작성 및 실행
Step 5. 46704/47863 검증 (마이그레이션 후 캘리브레이터 재실행)
```

---

## 6. 테스트 설계

### 6.1 단위 테스트

| # | 테스트명 | 검증 |
|---|---------|------|
| T1 | `test_unique_constraint_allows_different_small_cd` | 동일 (store_id, mid_cd, date)에 small_cd='', '273' 두 행 공존 |
| T2 | `test_migration_preserves_valid_data` | 마이그레이션 후 유효 행(sample_days>0) 보존 |
| T3 | `test_migration_removes_contaminated_rows` | 오염 행(small_cd!='', sample_days=0) 제거 |
| T4 | `test_migration_idempotent` | 2회 실행 시 오류 없음 |
| T5 | `test_get_calibrated_params_after_migration` | mid_cd 조회 시 small_cd='' 행 반환 |
| T6 | `test_calibrate_saves_both_phases` | calibrate() 후 mid_cd + small_cd 행 공존 |

### 6.2 통합 테스트

| # | 테스트명 | 검증 |
|---|---------|------|
| T7 | `test_init_store_db_creates_correct_unique` | 신규 DB에서 UNIQUE 제약 확인 |
| T8 | `test_existing_store_db_migrated` | 기존 DB 마이그레이션 후 UNIQUE 확인 |
