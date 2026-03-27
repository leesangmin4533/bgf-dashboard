# Design: 폐기율 목표 소분류(small_cd) 세분화

> **Feature**: waste-rate-smallcd
> **Created**: 2026-03-01
> **Plan**: [waste-rate-smallcd.plan.md](../../01-plan/features/waste-rate-smallcd.plan.md)

---

## 1. 아키텍처 개요

```
constants.py                    food_waste_calibrator.py
+--------------------------+    +-----------------------------------+
| FOOD_WASTE_RATE_TARGETS  |    | FoodWasteRateCalibrator            |
|   mid_cd -> target       |    |   calibrate()                     |
| SMALL_CD_TARGET_RATES    |    |     +-- calibrate_for_small_cd()  |  NEW
|   (mid_cd,small_cd)->tgt |    |     +-- _calibrate_mid_cd()       |  기존
| SMALL_CD_MIN_PRODUCTS    |    |   _get_waste_stats_by_small_cd()  |  NEW
|   최소 상품 수 (폴백기준)|    |   _get_small_cd_target()          |  NEW
+--------------------------+    +-----------------------------------+
                                          |
                                          v
                                food_waste_calibration (DB)
                                +-- small_cd TEXT (NEW, nullable)
                                +-- UNIQUE(store_id, mid_cd, small_cd, date)
```

## 2. 상수 설계 (constants.py)

### SMALL_CD_TARGET_RATES

mid_cd 목표 기준 +/-3%p 범위 내에서 소분류별 차등 목표를 설정한다.
키 형식: `(mid_cd, small_cd)` 튜플.

```python
SMALL_CD_TARGET_RATES = {
    # mid_cd=001 (도시락, 기본 20%)
    ("001", "001"): 0.18,   # 정식도시락: 안정수요, 18%
    ("001", "268"): 0.22,   # 단품요리: 변동수요, 22%
    ("001", "269"): 0.20,   # 세트도시락: 기본값 유지
    ("001", "273"): 0.23,   # 프리미엄도시락: 고단가 허용

    # mid_cd=002 (주먹밥, 기본 20%)
    ("002", "002"): 0.18,   # 삼각김밥: 안정수요

    # mid_cd=003 (김밥, 기본 20%)
    ("003", "003"): 0.19,   # 일반김밥

    # mid_cd=004 (샌드위치, 기본 15%)
    ("004", "004"): 0.14,   # 일반샌드위치

    # mid_cd=005 (햄버거, 기본 18%)
    ("005", "005"): 0.17,   # 일반햄버거

    # mid_cd=012 (빵, 기본 12%)
    ("012", "012"): 0.11,   # 식빵류
    ("012", "270"): 0.13,   # 조리빵
}

SMALL_CD_MIN_PRODUCTS = 5   # 소분류 내 상품 수 이 미만이면 mid_cd 폴백
```

### 목표 폐기율 결정 우선순위

1. `SMALL_CD_TARGET_RATES[(mid_cd, small_cd)]` 직접 매핑
2. 매핑 없으면: `FOOD_WASTE_RATE_TARGETS[mid_cd]` (기존 mid_cd 목표) 폴백

## 3. food_waste_calibrator.py 수정 설계

### 3.1 CalibrationResult 확장

```python
@dataclass
class CalibrationResult:
    mid_cd: str
    small_cd: Optional[str] = None     # NEW: 소분류 코드
    # ... (기존 필드 유지)
```

### 3.2 calibrate() 메서드 확장

기존 `calibrate()`에 소분류 보정 루프 추가:

```python
def calibrate(self):
    # 기존: mid_cd별 보정 (유지)
    for mid_cd in FOOD_CATEGORIES:
        result = self._calibrate_mid_cd(mid_cd, target)
        ...

    # NEW: small_cd별 보정
    for mid_cd in FOOD_CATEGORIES:
        small_cds = self._get_active_small_cds(mid_cd)
        for small_cd in small_cds:
            target = self._get_small_cd_target(mid_cd, small_cd)
            if target is None:
                continue
            product_count = self._count_products_in_small_cd(mid_cd, small_cd)
            if product_count < SMALL_CD_MIN_PRODUCTS:
                continue  # 폴백: mid_cd 보정이 커버
            result = self._calibrate_small_cd(mid_cd, small_cd, target)
            ...
```

### 3.3 _get_small_cd_target()

```python
def _get_small_cd_target(self, mid_cd: str, small_cd: str) -> Optional[float]:
    """소분류 목표 폐기율 조회. 매핑 없으면 None."""
    return SMALL_CD_TARGET_RATES.get((mid_cd, small_cd))
```

### 3.4 _calibrate_small_cd()

`_calibrate_mid_cd()`와 동일한 로직이지만 쿼리 범위가 small_cd로 좁아짐:
- `_get_waste_stats()` -> `_get_waste_stats_by_small_cd(mid_cd, small_cd)`
- 결과의 `small_cd` 필드 설정
- 히스테리시스 체크에서 `small_cd` 포함 조회

### 3.5 _get_waste_stats_by_small_cd()

```sql
-- daily_sales에 small_cd가 없으므로 product_details JOIN 필요
-- product_details는 common.db에 있으므로 ATTACH 사용
SELECT
    COUNT(DISTINCT ds.sales_date) as sample_days,
    COALESCE(SUM(ds.sale_qty), 0) as total_sold,
    COALESCE(SUM(ds.disuse_qty), 0) as total_waste_ds
FROM daily_sales ds
JOIN common.product_details pd ON ds.item_cd = pd.item_cd
WHERE ds.mid_cd = ? AND pd.small_cd = ?
AND ds.sales_date >= date('now', '-' || ? || ' days')
```

### 3.6 get_calibrated_food_params() 확장

```python
def get_calibrated_food_params(
    mid_cd: str,
    store_id: Optional[str] = None,
    db_path: Optional[str] = None,
    small_cd: Optional[str] = None,  # NEW
) -> Optional[CalibrationParams]:
    """
    우선순위:
    1. small_cd 보정값이 있으면 사용
    2. 없으면 mid_cd 보정값 폴백
    """
```

### 3.7 _save_calibration() 확장

DB 저장 시 `small_cd` 컬럼 포함:
```sql
INSERT OR REPLACE INTO food_waste_calibration (
    store_id, mid_cd, small_cd, calibration_date, ...
) VALUES (?, ?, ?, ?, ...)
```

## 4. DB 마이그레이션 (v48)

```sql
-- v48: food_waste_calibration에 small_cd 컬럼 추가
ALTER TABLE food_waste_calibration ADD COLUMN small_cd TEXT DEFAULT '';

-- 기존 UNIQUE 제약 변경을 위해 테이블 재생성
-- SQLite는 ALTER TABLE로 UNIQUE 변경 불가하므로,
-- 새 인덱스 추가로 대응
CREATE UNIQUE INDEX IF NOT EXISTS idx_fwc_store_mid_small_date
    ON food_waste_calibration(store_id, mid_cd, small_cd, calibration_date);
```

주의: 기존 `UNIQUE(store_id, mid_cd, calibration_date)` 제약은 유지.
small_cd='' (빈 문자열)인 행이 기존 mid_cd 보정 행이 된다.

## 5. 폴백 전략

```
get_calibrated_food_params(mid_cd, small_cd) 호출 시:
  1. small_cd가 주어지면:
     a. DB에서 (mid_cd, small_cd) 보정값 조회
     b. 있으면 반환
     c. 없으면 -> mid_cd 보정값 조회 (기존 로직)
  2. small_cd가 없으면:
     -> mid_cd 보정값 조회 (기존 동작 100% 호환)
```

## 6. 데이터 흐름

```
Phase 1.56 (daily_job.py)
  |
  v
FoodWasteRateCalibrator.calibrate()
  |
  +-- [기존] mid_cd별 보정 (FOOD_CATEGORIES 순회)
  |     |-- _calibrate_mid_cd() -> _get_waste_stats()
  |     +-- _save_calibration(result)  (small_cd='')
  |
  +-- [NEW] small_cd별 보정 (SMALL_CD_TARGET_RATES 순회)
        |-- _get_active_small_cds(mid_cd)
        |-- product_count >= SMALL_CD_MIN_PRODUCTS 체크
        |-- _calibrate_small_cd() -> _get_waste_stats_by_small_cd()
        +-- _save_calibration(result)  (small_cd='xxx')
```

## 7. 테스트 설계

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | test_small_cd_target_rates_range | 모든 소분류 목표가 mid_cd 기준 +/-3%p 이내 |
| 2 | test_calibrate_small_cd_basic | 소분류 보정 기본 동작 |
| 3 | test_calibrate_small_cd_high_waste | 폐기율 > 목표 -> 파라미터 감소 |
| 4 | test_calibrate_small_cd_low_waste | 폐기율 < 목표 -> 파라미터 증가 |
| 5 | test_calibrate_small_cd_deadband | 불감대 이내 -> 조정 없음 |
| 6 | test_calibrate_small_cd_fallback | 상품 수 < 5 -> mid_cd 폴백 |
| 7 | test_calibrate_small_cd_no_target | 매핑 없는 small_cd -> 건너뛰기 |
| 8 | test_get_calibrated_params_small_cd | small_cd 보정값 우선 조회 |
| 9 | test_get_calibrated_params_fallback_mid | small_cd 없으면 mid_cd 폴백 |
| 10 | test_get_calibrated_params_backward_compat | small_cd 미지정 시 기존 동작 |
| 11 | test_save_calibration_small_cd | DB 저장 시 small_cd 포함 |
| 12 | test_waste_stats_by_small_cd | 소분류 필터 쿼리 정확성 |
| 13 | test_calibrate_includes_both | calibrate() 결과에 mid_cd + small_cd 결과 포함 |
| 14 | test_hysteresis_small_cd | 소분류 히스테리시스 독립 동작 |
| 15 | test_data_insufficient_small_cd | 데이터 부족 시 조정 안 함 |
| 16 | test_clamp_stale_params_small_cd | 극단값 클램프 소분류 지원 |
| 17 | test_small_cd_min_products_boundary | 경계값 (정확히 5개) |
| 18 | test_calibrate_disabled | FOOD_WASTE_CAL_ENABLED=False 시 소분류 포함 비활성 |
| 19 | test_no_orders_small_cd | 발주 없는 소분류 처리 |
| 20 | test_multiple_small_cds_same_mid | 동일 mid_cd 내 여러 small_cd 독립 보정 |
