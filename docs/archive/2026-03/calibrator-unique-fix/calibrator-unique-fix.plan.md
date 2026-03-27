# calibrator-unique-fix Plan

> **Feature**: 폐기율 캘리브레이터 UNIQUE 제약 버그 수정 — Phase2(small_cd)가 Phase1(mid_cd) 덮어쓰기
>
> **Priority**: High
> **Created**: 2026-03-09

---

## 1. 문제 정의

### 1.1 현상
- `food_waste_calibration` 테이블의 UNIQUE 제약이 `(store_id, mid_cd, calibration_date)`
- `small_cd` 컬럼이 UNIQUE에 포함되지 않아 `INSERT OR REPLACE` 시 Phase 2(small_cd별) 보정이 Phase 1(mid_cd별) 보정을 덮어씀
- 46704/47863: mid_cd 보정 이력 **전부 소멸** (0건), small_cd 이력만 남음 (actual_waste_rate=0.0, sample_days=0)
- 46513: 우연히 Phase 1 이력 일부 생존 (90건), 보정이 정상 동작 중

### 1.2 현재 스키마 (schema.py:755)
```sql
UNIQUE(store_id, mid_cd, calibration_date)  -- small_cd 누락!
```

### 1.3 영향 범위
- **46704/47863**: `get_calibrated_food_params(mid_cd)` 호출 시 small_cd 이력만 조회됨 → 잘못된 파라미터 적용
- `food.py:188` (gap_coefficient), `food.py:604` (safety_days), `food_daily_cap.py:460` (총량 상한) 3개 소비 지점 모두 영향
- 실질적으로 2개 매장의 폐기율 자동 조정이 비활성 상태

### 1.4 데이터 증거
| 점포 | mid_cd 이력 | small_cd 이력 | 보정 동작 |
|------|------------|--------------|----------|
| 46513 | 90건 | 24건 | 정상 |
| 46704 | **0건** | 42건 | **비정상** |
| 47863 | **0건** | 36건 | **비정상** |

---

## 2. 근본 원인

`_save_calibration()` (food_waste_calibrator.py:938-963)에서:

```python
cursor.execute("""
    INSERT OR REPLACE INTO food_waste_calibration (
        store_id, mid_cd, small_cd, calibration_date, ...
    ) VALUES (?, ?, ?, ?, ...)
""", (self.store_id, result.mid_cd, small_cd_val, today, ...))
```

1. Phase 1 실행: `(46704, '001', '', '2026-03-03')` 저장 → OK
2. Phase 2 실행: `(46704, '001', '273', '2026-03-03')` 저장 → UNIQUE `(46704, '001', '2026-03-03')` 충돌 → Phase 1 행 **REPLACE**

---

## 3. 해결 방안

### 3.1 UNIQUE 제약 수정
```sql
UNIQUE(store_id, mid_cd, small_cd, calibration_date)
```

### 3.2 기존 DB 마이그레이션
- SQLite는 UNIQUE 제약 변경을 직접 지원하지 않음
- `_STORE_COLUMN_PATCHES`에 마이그레이션 로직 추가:
  1. 기존 테이블을 `_old`로 리네임
  2. 새 UNIQUE 제약으로 테이블 재생성
  3. 데이터 복사
  4. `_old` 삭제

### 3.3 오염 데이터 복구
- 46704/47863: small_cd 이력 중 mid_cd 보정이 덮어쓰인 행 식별 → 삭제 또는 small_cd='' 복원
- 즉시 `FoodWasteRateCalibrator.calibrate()` 재실행으로 올바른 mid_cd 보정 생성

---

## 4. 수정 대상 파일

| 파일 | 수정 내용 |
|------|----------|
| `src/infrastructure/database/schema.py` | UNIQUE 제약 수정: `(store_id, mid_cd, calibration_date)` → `(store_id, mid_cd, small_cd, calibration_date)` |
| `src/infrastructure/database/schema.py` | `_STORE_COLUMN_PATCHES`에 마이그레이션 추가 |
| (오염 복구) | 46704/47863 DB에서 잘못된 이력 정리 후 캘리브레이터 재실행 |

---

## 5. 테스트 계획

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | Phase 1 + Phase 2 동일 날짜 저장 | mid_cd행(small_cd='')과 small_cd행이 공존하는지 확인 |
| 2 | get_calibrated_food_params(mid_cd) 조회 | small_cd='' 행만 반환하는지 확인 |
| 3 | get_calibrated_food_params(mid_cd, small_cd='273') 조회 | small_cd='273' 행 반환 확인 |
| 4 | 마이그레이션 후 기존 데이터 보존 | 46513의 90건 mid_cd 이력 무손실 |
| 5 | 보정 재실행 후 46704/47863 mid_cd 이력 생성 | sample_days > 0, actual_waste_rate > 0 |

---

## 6. 리스크

- **마이그레이션 실패**: 테이블 리네임→재생성 중 오류 시 데이터 손실 → 트랜잭션으로 보호
- **인덱스 충돌**: 이미 `idx_food_waste_cal_small_cd(store_id, mid_cd, small_cd, calibration_date)` 인덱스 존재 → UNIQUE와 중복되지만 무해

---

## 7. 작업 순서

1. schema.py UNIQUE 제약 수정
2. schema.py 마이그레이션 패치 추가
3. 테스트 작성 및 실행
4. 46704/47863 오염 데이터 정리
5. 캘리브레이터 재실행으로 올바른 보정값 생성
