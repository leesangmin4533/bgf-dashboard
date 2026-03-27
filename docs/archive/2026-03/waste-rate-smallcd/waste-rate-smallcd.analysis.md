# Analysis: 폐기율 목표 소분류(small_cd) 세분화

> **Feature**: waste-rate-smallcd
> **Created**: 2026-03-01
> **Design**: [waste-rate-smallcd.design.md](../02-design/features/waste-rate-smallcd.design.md)

---

## 1. 구현 vs 설계 비교

| # | 설계 항목 | 구현 상태 | 비고 |
|---|----------|----------|------|
| 1 | SMALL_CD_TARGET_RATES 딕셔너리 | 구현 완료 | 11개 (mid_cd, small_cd) 매핑 |
| 2 | SMALL_CD_MIN_PRODUCTS 상수 | 구현 완료 | 5개 |
| 3 | CalibrationResult.small_cd 필드 | 구현 완료 | Optional[str], 기본 None |
| 4 | calibrate() Phase 2 소분류 보정 | 구현 완료 | mid_cd 보정 후 실행 |
| 5 | _calibrate_small_cd() | 구현 완료 | _calibrate_mid_cd()와 동일 로직 |
| 6 | _count_products_in_small_cd() | 구현 완료 | product_details JOIN |
| 7 | _get_waste_stats_by_small_cd() | 구현 완료 | daily_sales JOIN product_details |
| 8 | get_calibrated_food_params(small_cd=) | 구현 완료 | small_cd 우선 -> mid_cd 폴백 |
| 9 | get_effective_params(small_cd=) | 구현 완료 | 폴백 체인 유지 |
| 10 | _save_calibration() small_cd 포함 | 구현 완료 | v48 이전 스키마 폴백 포함 |
| 11 | _check_consistent_direction(small_cd=) | 구현 완료 | 소분류 독립 히스테리시스 |
| 12 | _attach_common_db() 테스트 모드 | 구현 완료 | db_path 직접 지정 시 ATTACH 건너뜀 |
| 13 | DB 마이그레이션 v48 | 구현 완료 | small_cd 컬럼 + 유니크 인덱스 |
| 14 | 기존 테스트 호환 | 구현 완료 | 3개 기존 테스트 필터 조건 수정 |

### 설계 대비 추가 구현

| 항목 | 이유 |
|------|------|
| `_attach_common_db()` 메서드 | product_details가 common.db에 있어 ATTACH 필요 |
| db_path 모드 시 ATTACH 건너뛰기 | 테스트에서 단일 DB 사용 시 올바른 동작 보장 |
| `_save_calibration()` 이중 폴백 | v48 마이그레이션 전 스키마와 호환 유지 |
| `_clamp_stale_params()` 이중 폴백 | small_cd 컬럼 유무에 따른 INSERT 분기 |

## 2. Match Rate

### 설계 항목 매칭

| 카테고리 | 설계 항목 수 | 구현 항목 수 | Match |
|---------|------------|------------|-------|
| 상수 | 2 | 2 | 100% |
| CalibrationResult 확장 | 1 | 1 | 100% |
| 보정 로직 | 4 | 4 | 100% |
| 데이터 조회 | 2 | 2 | 100% |
| DB 저장/조회 | 3 | 3 | 100% |
| 히스테리시스 | 1 | 1 | 100% |
| DB 마이그레이션 | 1 | 1 | 100% |
| **합계** | **14** | **14** | **100%** |

### 테스트 매칭

| 카테고리 | 설계 테스트 수 | 구현 테스트 수 | Match |
|---------|-------------|-------------|-------|
| 범위 검증 (1-3) | 3 | 3 | 100% |
| 기본 동작 (4-6) | 3 | 3 | 100% |
| 방향별 보정 (7-9) | 3 | 3 | 100% |
| 폴백 (10-12) | 3 | 3 | 100% |
| 조회 (13-16) | 4 | 4 | 100% |
| DB 저장 (17-18) | 2 | 2 | 100% |
| 쿼리 정확성 (19) | 1 | 1 | 100% |
| 히스테리시스 (20) | 1 | 1 | 100% |
| 데이터 부족 (21-22) | 2 | 2 | 100% |
| 독립 보정 (23) | 1 | 1 | 100% |
| 클램프 (24) | 1 | 1 | 100% |
| get_effective (25-26) | 2 | 2 | 100% |
| CalibrationResult (27-28) | 2 | 2 | 100% |
| **합계** | **28** | **28** | **100%** |

### 종합 Match Rate: **100%**

## 3. 테스트 결과

```
tests/test_waste_rate_smallcd.py: 28 passed (18.28s)
tests/test_food_waste_calibrator.py: 34 passed (12.06s)
합계: 62 passed, 0 failed
```

### 기존 테스트 회귀

- `test_food_waste_calibrator.py`: 3개 테스트 필터 조건 수정 (small_cd가 None인 mid_cd 레벨만)
- `test_waste_cause_analyzer.py`: 51 passed (변경 없음)
- `test_waste_disuse_sync.py`: 통과 (변경 없음)
- `test_waste_cause_viz.py`: 통과 (변경 없음)

## 4. 위험 분석

| 위험 | 발생 여부 | 완화 결과 |
|------|----------|----------|
| small_cd 데이터 부족 | 테스트 검증 완료 | SMALL_CD_MIN_PRODUCTS=5 폴백 정상 동작 |
| DB 마이그레이션 실패 | 발생 안 함 | small_cd DEFAULT '' + 이중 폴백 |
| product_details ATTACH 실패 | 테스트 모드에서 발생 | db_path 감지 로직으로 해결 |
| 기존 API 호환 깨짐 | 발생 안 함 | small_cd 미지정 시 기존 동작 100% 유지 |

## 5. 성능 영향

- calibrate() 실행 시 small_cd 보정 루프 추가: SMALL_CD_TARGET_RATES에 등록된 11개 쌍 추가 처리
- 각 small_cd에 대해 _count_products_in_small_cd() + _get_waste_stats_by_small_cd() 쿼리 2회
- 총 추가 쿼리: 최대 22회 (11 * 2) -- daily_job Phase 1.56에서 1회 실행이므로 영향 미미
