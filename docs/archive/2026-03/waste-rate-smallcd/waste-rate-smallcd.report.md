# Report: 폐기율 목표 소분류(small_cd) 세분화

> **Feature**: waste-rate-smallcd
> **Created**: 2026-03-01
> **Status**: Complete
> **Match Rate**: 100%
> **Tests**: 28 passed (신규) + 34 passed (기존, 3개 수정)

---

## 1. 요약

FoodWasteRateCalibrator의 폐기율 목표를 **소분류(small_cd) 단위**로 세분화하여,
동일 중분류 내에서도 소분류별 특성에 맞는 차등 보정이 가능하도록 확장했다.

### 핵심 변경

- **SMALL_CD_TARGET_RATES**: 11개 (mid_cd, small_cd) 매핑 (mid_cd 기준 +-3%p 범위)
- **calibrate()**: Phase 2로 소분류별 보정 루프 추가
- **get_calibrated_food_params()**: small_cd 우선 조회 + mid_cd 폴백
- **DB v48**: food_waste_calibration 테이블에 small_cd 컬럼 추가

### 폴백 안전성

```
small_cd 보정값 있음  ->  소분류 보정값 사용
small_cd 보정값 없음  ->  mid_cd 보정값 폴백
상품 수 < 5          ->  소분류 보정 건너뜀 (mid_cd 커버)
매핑 없는 small_cd   ->  건너뜀 (mid_cd 커버)
v48 미적용 DB        ->  기존 스키마로 자동 폴백
```

## 2. 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/prediction/food_waste_calibrator.py` | small_cd 보정 로직, _attach_common_db(), 데이터 클래스 확장 |
| `src/settings/constants.py` | SMALL_CD_TARGET_RATES (11개), SMALL_CD_MIN_PRODUCTS (5) |
| `src/db/models.py` | v48 마이그레이션 (small_cd 컬럼 + 유니크 인덱스) |
| `tests/test_waste_rate_smallcd.py` | 28개 신규 테스트 |
| `tests/test_food_waste_calibrator.py` | 3개 기존 테스트 필터 조건 수정 |

## 3. 소분류별 목표 폐기율

| mid_cd | small_cd | 이름 | 목표 | mid_cd 기준 | 차이 |
|--------|----------|------|------|------------|------|
| 001 | 001 | 정식도시락 | 18% | 20% | -2%p |
| 001 | 268 | 단품요리 | 22% | 20% | +2%p |
| 001 | 269 | 세트도시락 | 20% | 20% | 0%p |
| 001 | 273 | 프리미엄도시락 | 23% | 20% | +3%p |
| 002 | 002 | 삼각김밥 | 18% | 20% | -2%p |
| 003 | 003 | 일반김밥 | 19% | 20% | -1%p |
| 004 | 004 | 일반샌드위치 | 14% | 15% | -1%p |
| 005 | 005 | 일반햄버거 | 17% | 18% | -1%p |
| 012 | 012 | 식빵류 | 11% | 12% | -1%p |
| 012 | 270 | 조리빵 | 13% | 12% | +1%p |

## 4. 기존 호환성

- `get_calibrated_food_params(mid_cd, store_id)` 기존 호출: 100% 호환 (small_cd 미지정 시 기존 동작)
- `food.py`, `food_daily_cap.py`에서 호출하는 기존 API: 변경 없음
- daily_job.py Phase 1.56: 변경 없음 (calibrate() 내부에서 자동 확장)
- DB 마이그레이션: 기존 행 영향 없음 (small_cd DEFAULT '')

## 5. 테스트 커버리지

| 테스트 분류 | 수 | 내용 |
|-----------|---|------|
| 범위 검증 | 3 | TARGET_RATES +-3%p 범위, 비어있지 않음, MIN_PRODUCTS 양수 |
| 기본 동작 | 3 | 보정 실행, mid+small 포함, disabled 동작 |
| 방향별 보정 | 3 | 높은 폐기율, 낮은 폐기율, 불감대 |
| 폴백 | 3 | 상품 수 부족, 경계값(5개), 매핑 없음 |
| 조회 API | 4 | small_cd 우선, mid_cd 폴백, 하위 호환, 데이터 없음 |
| DB 저장 | 2 | small_cd 기록, mid_cd는 빈 small_cd |
| 쿼리 정확성 | 1 | 소분류별 분리 집계 |
| 히스테리시스 | 1 | 소분류 독립 동작 |
| 데이터 부족 | 2 | 일수 부족, 발주 없음 |
| 독립 보정 | 1 | 동일 mid_cd 내 여러 small_cd |
| 클램프 | 1 | 극단값 클램프 소분류 지원 |
| get_effective | 2 | small_cd 보정값 사용, 기본값 폴백 |
| 데이터 클래스 | 2 | small_cd 필드 존재, None 기본값 |
| **합계** | **28** | |

## 6. 향후 과제

- food.py / food_daily_cap.py에서 small_cd 기반 보정값 직접 조회 (현재는 mid_cd만)
- 웹 대시보드에서 소분류별 보정 현황 시각화
- 소분류별 목표 폐기율 자동 산출 (과거 데이터 기반 최적화)
- SMALL_CD_TARGET_RATES를 DB/설정 파일 기반으로 전환 (하드코딩 제거)
