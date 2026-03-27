# Plan: waste-slip-migration

> 매장 DB 스키마 정합성 복구 — waste_slips 누락 + promotions UNIQUE 불일치

## 1. 배경 및 문제

### 현상 (3/7 07:00 스케줄 로그)
1. **47863 매장**: `waste_slips` 테이블에 `nap_plan_ymd` 컬럼 없음 → WasteSlipRepo 저장 실패
2. **47863 매장**: `promotions` 테이블 UNIQUE 제약이 `(item_cd, promo_type, start_date)` 3컬럼 → `ON CONFLICT(store_id, ...)` 4컬럼과 불일치 → 행사 저장 전체 실패 (~50건)

### 근본 원인
- `STORE_SCHEMA` (schema.py)에 `waste_slips` 테이블과 `waste_slip_items` 테이블이 **누락**
- `STORE_SCHEMA`의 `promotions` 테이블 UNIQUE가 `(item_cd, promo_type, start_date)` 3컬럼인데, `promotion_repo.py`는 `ON CONFLICT(store_id, item_cd, promo_type, start_date)` 4컬럼을 기대
- 46513/46704는 레거시 `models.py` SCHEMA_MIGRATIONS(v33, v28)으로 올바르게 생성됨
- 47863은 나중에 추가된 매장이라 `STORE_SCHEMA`만 적용 → 누락 발생

### DB 상태 비교
| 매장 | waste_slips 컬럼수 | nap_plan_ymd | promotions UNIQUE |
|------|-------------------|-------------|------------------|
| 46513 | 17 | O | (store_id, item_cd, promo_type, start_date) |
| 46704 | 17 | O | (store_id, item_cd, promo_type, start_date) |
| 47863 | 13 | X | (item_cd, promo_type, start_date) |

## 2. 목표

1. `STORE_SCHEMA`에 `waste_slips` + `waste_slip_items` 테이블 정의 추가
2. `STORE_SCHEMA`의 `promotions` UNIQUE 제약을 `(store_id, item_cd, promo_type, start_date)`로 수정
3. `_STORE_COLUMN_PATCHES`에 47863 같은 기존 DB 보정 로직 추가
4. 3개 매장 모두 동일 스키마로 정합성 확보

## 3. 수정 범위

### 파일 목록
| 파일 | 수정 내용 |
|------|-----------|
| `src/infrastructure/database/schema.py` | STORE_SCHEMA에 waste_slips/waste_slip_items 추가, promotions UNIQUE 수정, _STORE_COLUMN_PATCHES 추가 |

### 수정 상세

#### A. STORE_SCHEMA에 waste_slips 테이블 추가
```python
# waste_slips (폐기 전표 — v33)
"""CREATE TABLE IF NOT EXISTS waste_slips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    chit_date TEXT NOT NULL,
    chit_no TEXT NOT NULL,
    chit_flag TEXT,
    chit_id TEXT,
    chit_id_nm TEXT,
    item_cnt INTEGER DEFAULT 0,
    center_cd TEXT,
    center_nm TEXT,
    wonga_amt REAL DEFAULT 0,
    maega_amt REAL DEFAULT 0,
    nap_plan_ymd TEXT,
    conf_id TEXT,
    cre_ymdhms TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    UNIQUE(store_id, chit_date, chit_no)
)""",
```

#### B. STORE_SCHEMA에 waste_slip_items 테이블 추가
- models.py v34 정의 참조하여 동일 스키마 추가

#### C. promotions UNIQUE 수정
```python
# 변경 전
UNIQUE(item_cd, promo_type, start_date)
# 변경 후
UNIQUE(store_id, item_cd, promo_type, start_date)
```

#### D. _STORE_COLUMN_PATCHES 보정 추가
```python
# waste_slips 누락 컬럼 보정 (47863 등 기존 DB)
"ALTER TABLE waste_slips ADD COLUMN nap_plan_ymd TEXT",
"ALTER TABLE waste_slips ADD COLUMN conf_id TEXT",
"ALTER TABLE waste_slips ADD COLUMN cre_ymdhms TEXT",
"ALTER TABLE waste_slips ADD COLUMN updated_at TEXT",
```

#### E. promotions 테이블 재생성 (UNIQUE 변경은 ALTER로 불가)
- `_apply_store_column_patches`에 promotions 테이블 UNIQUE 검사+재생성 로직 추가
- 또는 별도 `_fix_promotions_unique()` 함수

#### F. STORE_INDEXES에 waste_slips 인덱스 추가
```python
"CREATE INDEX IF NOT EXISTS idx_waste_slips_store_date ON waste_slips(store_id, chit_date)",
```

## 4. 리스크

- promotions 테이블 재생성 시 기존 데이터 보존 필요 (INSERT INTO ... SELECT 패턴)
- 47863 waste_slips 테이블이 13컬럼으로 이미 존재 → CREATE IF NOT EXISTS가 스킵되므로 ALTER TABLE로 보정 필요
- 다른 매장 추가 시에도 동일 문제 없도록 STORE_SCHEMA 자체를 올바르게 유지

## 5. 검증 방법

1. 3개 매장 DB 컬럼 비교 스크립트 실행
2. `python run_scheduler.py --now` 또는 개별 Phase 1.15 실행하여 waste_slips 저장 성공 확인
3. promotions 저장 시 ON CONFLICT 에러 없음 확인
4. 기존 데이터 무손실 확인

## 6. 예상 소요

- 수정 파일: 1개 (schema.py)
- 테스트: DB 스키마 검증 스크립트
