# Plan: 폐기율 목표 소분류(small_cd) 세분화

> **Feature**: waste-rate-smallcd
> **Created**: 2026-03-01
> **Status**: Approved

---

## 1. 배경

FoodWasteRateCalibrator는 현재 **중분류(mid_cd)** 단위로 폐기율 목표를 관리한다.
예를 들어 mid_cd=001(도시락) 전체가 목표 20%로 설정되어 있다.

그러나 동일 중분류 내에서도 소분류(small_cd)에 따라 폐기 특성이 크게 다르다:
- **정식도시락(001)**: 안정적 수요, 폐기율 낮음 -> 목표 18%가 적절
- **단품요리(268)**: 변동 수요, 폐기율 높음 -> 목표 22%가 적절
- **프리미엄도시락**: 고단가, 폐기 비용 크므로 더 보수적 목표 필요

중분류 단위 보정만으로는 소분류 간 특성 차이를 반영할 수 없어,
한쪽은 과잉발주(폐기 증가), 다른 쪽은 과소발주(품절 증가)가 발생한다.

## 2. 목표

| # | 항목 | 목표 |
|---|------|------|
| 1 | 소분류별 목표 폐기율 | mid_cd 목표 기준 +/-3%p 범위 내 차등화 |
| 2 | 폴백 안전성 | small_cd 데이터 부족(상품 수 < 5) 시 mid_cd 폴백 |
| 3 | DB 확장 | food_waste_calibration 테이블에 small_cd 컬럼 추가 |
| 4 | 기존 호환 | mid_cd 단위 보정 API 기존 동작 유지 |

## 3. 범위

### In Scope
- `src/prediction/food_waste_calibrator.py` 수정: small_cd 보정 로직 추가
- `src/settings/constants.py` 수정: SMALL_CD_TARGET_RATES 딕셔너리 추가
- `src/db/models.py` 수정: DB 마이그레이션 (food_waste_calibration에 small_cd 컬럼)
- `get_calibrated_food_params()` 확장: small_cd 우선 조회 + mid_cd 폴백
- 테스트 작성

### Out of Scope
- food.py / food_daily_cap.py 에서 small_cd 기반 보정값 직접 사용 (추후)
- 웹 대시보드 UI에서 small_cd별 보정 현황 표시 (추후)
- small_cd별 목표 폐기율 자동 산출 알고리즘 (현재는 수동 설정)

## 4. 예상 수정/신규 파일

| 파일 | 유형 | 내용 |
|------|------|------|
| `src/prediction/food_waste_calibrator.py` | 수정 | small_cd 보정 로직, CalibrationResult 확장, DB 쿼리 추가 |
| `src/settings/constants.py` | 수정 | SMALL_CD_TARGET_RATES, SMALL_CD_MIN_PRODUCTS 상수 |
| `src/db/models.py` | 수정 | v48 마이그레이션 (small_cd 컬럼 + 인덱스) |
| `tests/test_waste_rate_smallcd.py` | 신규 | 소분류 보정 테스트 (20개+) |

## 5. 위험 요소

| 위험 | 완화 |
|------|------|
| small_cd 데이터 부족 (상품 수 < 5) | mid_cd 폴백 자동 적용 |
| 보정값 발산 (small_cd 보정과 mid_cd 보정 충돌) | small_cd 우선, mid_cd 폴백으로 단일 경로 |
| DB 마이그레이션 실패 | small_cd 컬럼 NULL 허용, 기존 행 영향 없음 |
| product_details에 small_cd 미수집 상품 | daily_sales JOIN product_details로 매핑 |
