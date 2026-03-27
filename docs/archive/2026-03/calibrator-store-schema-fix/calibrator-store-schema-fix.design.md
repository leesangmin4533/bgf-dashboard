# Design: calibrator-store-schema-fix

> STORE_SCHEMA에 누락된 테이블 2개 추가 + silent failure 경고 로그

## 1. 수정 대상 파일

| # | 파일 | 변경 유형 | 설명 |
|---|------|---------|------|
| 1 | `src/infrastructure/database/schema.py` | MODIFY | STORE_SCHEMA에 2개 테이블 DDL 추가 |
| 2 | `src/infrastructure/database/schema.py` | MODIFY | STORE_INDEXES에 3개 인덱스 추가 |
| 3 | `src/prediction/food_waste_calibrator.py` | MODIFY | line 175 silent failure → 경고 로그 |

## 2. 상세 변경 사항

### 2-1. STORE_SCHEMA 추가 (schema.py)

`substitution_events` 뒤(line 723 `,` 뒤)에 2개 테이블 DDL 추가:

```python
# food_waste_calibration (폐기율 자동 보정 — v32, small_cd v48)
"""CREATE TABLE IF NOT EXISTS food_waste_calibration (
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
    UNIQUE(store_id, mid_cd, calibration_date)
)""",

# waste_verification_log (폐기 전표 검증 — v33)
"""CREATE TABLE IF NOT EXISTS waste_verification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    verification_date TEXT NOT NULL,
    slip_count INTEGER DEFAULT 0,
    slip_item_count INTEGER DEFAULT 0,
    daily_sales_disuse_count INTEGER DEFAULT 0,
    gap INTEGER DEFAULT 0,
    gap_percentage REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    details TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, verification_date)
)""",
```

**Note**: `food_waste_calibration`은 v48에서 추가된 `small_cd TEXT DEFAULT ''` 컬럼을 DDL에 바로 포함한다. STORE_SCHEMA는 버전 관리가 없으므로 최신 스키마를 한 번에 정의해야 한다.

### 2-2. STORE_INDEXES 추가 (schema.py)

`STORE_INDEXES` 리스트 끝(`idx_substitution_small_cd` 뒤)에 3개 인덱스 추가:

```python
# food_waste_calibration
"CREATE INDEX IF NOT EXISTS idx_food_waste_cal_store_mid ON food_waste_calibration(store_id, mid_cd, calibration_date)",
"CREATE INDEX IF NOT EXISTS idx_food_waste_cal_small_cd ON food_waste_calibration(store_id, mid_cd, small_cd, calibration_date)",
# waste_verification_log
"CREATE INDEX IF NOT EXISTS idx_waste_verify_store_date ON waste_verification_log(store_id, verification_date)",
```

### 2-3. food_waste_calibrator.py silent failure → 경고 로그

**현재 코드** (line 175):
```python
except sqlite3.OperationalError:
    # 테이블 미존재 (마이그레이션 전)
    return None
```

**변경 후**:
```python
except sqlite3.OperationalError as e:
    if "no such table" in str(e):
        logger.warning(
            f"[폐기율보정] food_waste_calibration 테이블 누락 "
            f"(store={store_id}) — init_store_db() 재실행 필요"
        )
    return None
```

## 3. 구현 순서

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | STORE_SCHEMA에 2개 DDL 추가 | schema.py |
| 2 | STORE_INDEXES에 3개 인덱스 추가 | schema.py |
| 3 | silent failure → 경고 로그 | food_waste_calibrator.py |
| 4 | 테스트 작성 및 실행 | tests/test_calibrator_schema_fix.py |
| 5 | 전체 테스트 확인 | 기존 테스트 통과 |

## 4. 안전성

- `CREATE TABLE IF NOT EXISTS`: 기존 테이블 있으면 무시 → 46513 안전
- `CREATE INDEX IF NOT EXISTS`: 기존 인덱스 있으면 무시 → 중복 없음
- `small_cd` 컬럼을 DDL에 직접 포함하므로 v48 마이그레이션 불필요
- `init_store_db()`는 앱 시작 시 호출되므로 다음 실행 시 자동 테이블 생성

## 5. 테스트 시나리오

| # | 시나리오 | 검증 내용 |
|---|---------|---------|
| 1 | STORE_SCHEMA에 food_waste_calibration DDL 포함 확인 | DDL 문자열 검사 |
| 2 | STORE_SCHEMA에 waste_verification_log DDL 포함 확인 | DDL 문자열 검사 |
| 3 | STORE_INDEXES에 3개 인덱스 포함 확인 | 인덱스 이름 검사 |
| 4 | init_store_db()로 빈 DB에 테이블 생성 확인 | 임시 DB 생성 → 테이블 존재 확인 |
| 5 | init_store_db() 중복 실행 시 에러 없음 | 2번 호출 → 에러 없음 |
| 6 | food_waste_calibration에 small_cd 컬럼 포함 확인 | PRAGMA table_info 검사 |
| 7 | 테이블 없을 때 get_calibrated_food_params()가 warning 로그 출력 | 로그 캡처 |
