# Plan: calibrator-store-schema-fix

> STORE_SCHEMA에 누락된 테이블 추가 + 기존 매장 DB 자동 마이그레이션

## 1. 문제 정의

### 현상
- Store **46704**에서 `FoodWasteRateCalibrator`가 전혀 작동하지 않음
- `food_waste_calibration` 테이블이 존재하지 않아 모든 쿼리가 `OperationalError`로 **조용히 실패**
- 46704 스키마 버전 **v30**, 46513은 **v34** (테이블 존재)

### 근본 원인
`STORE_SCHEMA` (schema.py)에 3개 테이블이 누락되어 있음:

| 누락 테이블 | legacy 마이그레이션 버전 | 용도 | 46704 영향 |
|---|---|---|---|
| `food_waste_calibration` | v32 | 폐기율 자동 보정 | **캘리브레이터 미작동** |
| `waste_verification_log` | v33 | 폐기 전표 검증 | 전표 검증 미작동 |
| `eval_outcomes_new` | - | (미사용, 0 rows) | 없음 |

### 발생 경위
- `init_db()` (models.py): legacy `bgf_sales.db`에만 v1~v50 마이그레이션 적용
- `init_store_db()` (schema.py): `STORE_SCHEMA` 리스트로 CREATE IF NOT EXISTS 실행 — **버전 관리 없음**
- 46513은 legacy DB 분할 시 테이블이 함께 복사됨 → 정상
- 46704는 분할 이후 생성되어 STORE_SCHEMA에 없는 테이블은 영원히 생성 안 됨

## 2. 해결 방안

### 수정 대상 파일

| # | 파일 | 작업 |
|---|------|------|
| 1 | `src/infrastructure/database/schema.py` | STORE_SCHEMA에 2개 테이블 + STORE_INDEXES에 인덱스 추가 |
| 2 | `src/infrastructure/database/schema.py` | `init_store_db()` 호출 시 기존 DB에도 안전 적용 (CREATE IF NOT EXISTS) |
| 3 | `src/prediction/food_waste_calibrator.py` | silent failure → 경고 로그 추가 |

### 추가할 테이블 (2개)

#### food_waste_calibration
```sql
CREATE TABLE IF NOT EXISTS food_waste_calibration (
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
    UNIQUE(store_id, mid_cd, calibration_date)
);
```

#### waste_verification_log
```sql
CREATE TABLE IF NOT EXISTS waste_verification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    verification_date TEXT NOT NULL,
    slip_count INTEGER DEFAULT 0,
    slip_item_count INTEGER DEFAULT 0,
    sales_disuse_count INTEGER DEFAULT 0,
    gap_count INTEGER DEFAULT 0,
    gap_rate REAL DEFAULT 0.0,
    status TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, verification_date)
);
```

### 추가할 인덱스

```sql
-- food_waste_calibration
CREATE INDEX IF NOT EXISTS idx_food_waste_cal_store_mid
    ON food_waste_calibration(store_id, mid_cd, calibration_date);

-- waste_verification_log
CREATE INDEX IF NOT EXISTS idx_waste_verify_store_date
    ON waste_verification_log(store_id, verification_date);
```

### silent failure 수정

`food_waste_calibrator.py`에서 `OperationalError` catch 시:
```python
# Before: silently return None
except Exception:
    return None

# After: warn once + return None
except sqlite3.OperationalError as e:
    if "no such table" in str(e):
        logger.warning(f"[Calibrator] 테이블 누락 ({e}) — init_store_db() 재실행 필요")
    return None
```

## 3. 미수정 사항 (의도적 제외)

- `eval_outcomes_new`: 46513에서도 0 rows → 미사용 테이블, 추가하지 않음
- `STORE_SCHEMA` 버전 관리 시스템: CREATE IF NOT EXISTS로 충분, 별도 마이그레이션 엔진은 over-engineering
- `small_cd` 컬럼 추가 (v48): food_waste_calibration에 small_cd 컬럼은 STORE_SCHEMA 정의에서 바로 포함

## 4. 검증 계획

| # | 시나리오 | 기대 결과 |
|---|---------|---------|
| 1 | init_store_db() 후 food_waste_calibration 존재 확인 | 테이블 생성됨 |
| 2 | 이미 테이블 있는 DB에서 init_store_db() 재실행 | 에러 없이 스킵 |
| 3 | 캘리브레이터 정상 실행 확인 | 보정 레코드 삽입 |
| 4 | 테이블 없을 때 경고 로그 출력 | logger.warning 발생 |
| 5 | 46704 실제 DB에 테이블 생성 확인 | 테이블 2개 생성 |

## 5. 리스크

- **낮음**: CREATE IF NOT EXISTS이므로 기존 데이터 손실 없음
- **낮음**: 인덱스도 IF NOT EXISTS이므로 중복 생성 없음
- 46704에서 캘리브레이터 시작 시 첫 21일간은 sample 부족으로 보정 효과 제한적

## 6. 예상 소요

- 수정: schema.py 2개소 + food_waste_calibrator.py 1개소
- 테스트: 5개 시나리오
- 복잡도: **Low** (순수 DDL 추가)
