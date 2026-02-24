# 푸드 과소발주 알고리즘 수정 (food-underorder-fix)

## Context

46513 매장의 푸드 발주량이 예측값 대비 45~52%로 심각한 과소발주 발생.
근본 원인: `get_dynamic_disuse_coefficient()`의 공격적 감량 공식 + inventory_batches 쿼리에 날짜 필터 부재 + 소표본 편향 + 캘리브레이터 극단값.

### 근본 원인 분석

| 원인 | 현재 | 문제 |
|------|------|------|
| inventory_batches 날짜 필터 없음 | 전체 히스토리 누적 | 오래된 폐기 데이터 과대 반영 |
| 승수 1.5 + 하한 0.5 | `max(0.5, 1.0 - rate*1.5)` | 33%↑ 폐기율이면 50% 감량 (과도) |
| 표본 임계값 7 (배치 수) | `item_data_days >= 7` | 배치 21건 = "21일"로 오판, 소표본에 80% 가중치 |
| 캘리브레이터 극단값 | 김밥 safety=0.2, gap=0.1 | 현재 허용 범위(0.35, 0.2) 미만 |

## 구현 계획 (5 Step)

### Step 1: constants.py에 설정 가능 상수 추가
**파일**: `src/settings/constants.py` (line 352 뒤)

```python
# ── 동적 폐기 계수 파라미터 (get_dynamic_disuse_coefficient) ──
DISUSE_COEF_FLOOR = 0.65             # 최소 계수 (최대 35% 감량, was 0.5=50%)
DISUSE_COEF_MULTIPLIER = 1.2         # 폐기율 승수 (was 1.5)
DISUSE_MIN_BATCH_COUNT = 14          # 상품별 블렌딩 최소 배치 수 (was 7)
DISUSE_IB_LOOKBACK_DAYS = 30         # inventory_batches 조회 기간
```

### Step 2: food.py `get_dynamic_disuse_coefficient()` 수정 (4건)
**파일**: `src/prediction/categories/food.py` (lines 332-520)

**2a. 상수 import 추가** (파일 상단):
```python
from src.settings.constants import (
    DISUSE_COEF_FLOOR, DISUSE_COEF_MULTIPLIER,
    DISUSE_MIN_BATCH_COUNT, DISUSE_IB_LOOKBACK_DAYS,
)
```

**2b. inventory_batches 4개 쿼리에 날짜 필터 추가** (lines 384-424):
- 기존: `WHERE item_cd = ? AND store_id = ? AND status IN (...)`
- 변경: `AND receiving_date >= date('now', '-' || ? || ' days')` 추가 + 파라미터에 `DISUSE_IB_LOOKBACK_DAYS` 바인딩
- 4개 쿼리 모두 동일 패턴 (item+store_id, item만, mid+store_id, mid만)

**2c. 변수명 + 임계값 수정** (lines 379, 405, 491):
```python
# 변수명: item_data_days → item_batch_count
item_batch_count = 0                     # line 379
item_batch_count = row[2] or 0           # line 405

# 블렌딩 조건 (inventory_batches vs daily_sales 경로 구분):
sample_sufficient = False
if ib_item_found:
    sample_sufficient = item_batch_count >= DISUSE_MIN_BATCH_COUNT
elif item_data_days >= 7:
    sample_sufficient = True

if item_rate is not None and sample_sufficient:
    ...  # 80/20 블렌딩
```

**2d. 공식 상수화** (line 505):
```python
# 기존: coef = max(0.5, 1.0 - blended_rate * 1.5)
coef = max(DISUSE_COEF_FLOOR, 1.0 - blended_rate * DISUSE_COEF_MULTIPLIER)
```

### Step 3: food.py `calculate_food_dynamic_safety()` 공식 동기화
**파일**: `src/prediction/categories/food.py` (line 676)

```python
# 기존: disuse_coef = max(0.5, 1.0 - disuse_rate * 1.5)
disuse_coef = max(DISUSE_COEF_FLOOR, 1.0 - disuse_rate * DISUSE_COEF_MULTIPLIER)
```

### Step 4: 캘리브레이터 극단값 클램프
**파일**: `src/prediction/food_waste_calibrator.py`

`FoodWasteRateCalibrator`에 `_clamp_stale_params()` 메서드 추가.
`calibrate()` 시작 시 호출하여, DB의 기존 보정값이 현재 안전 범위 하한 미만이면 하한으로 클램프.

예: 김밥(003) safety_days=0.2 → 0.35 (ultra_short 하한), gap_coef=0.1 → 0.2

### Step 5: 테스트 (~25개)
**새 파일**: `tests/test_food_underorder_fix.py`

| 그룹 | 테스트 수 | 범위 |
|------|:---------:|------|
| 날짜 필터 | 6 | IB 쿼리에 날짜 필터, 오래된 배치 제외, store_id 유무, daily_sales 폴백 |
| 상수/공식 | 6 | 하한 0.65, 승수 1.2, 다양한 폐기율, 상수 import |
| 표본 임계값 | 5 | 배치 14 미만→mid 사용, 14 이상→블렌딩, daily_sales 경로 7일 |
| 캘리브레이터 클램프 | 4 | 극단값 클램프, 범위 내 미변경, calibrate() 시 호출 |
| 통합/회귀 | 4 | 46513 시나리오, 함수 시그니처 호환, 연속 함수 수학 검증 |

## 변경 파일 목록

| 파일 | 변경 |
|------|------|
| `src/settings/constants.py` | 상수 4개 추가 (~5줄) |
| `src/prediction/categories/food.py` | IB 날짜 필터, 변수명, 공식 상수화 (~30줄 수정) |
| `src/prediction/food_waste_calibrator.py` | `_clamp_stale_params()` 메서드 추가 (~40줄) |
| `tests/test_food_underorder_fix.py` | **신규** — 25개 테스트 (~350줄) |

## 수학적 검증: 46513 시나리오

| 항목 | 현재 (Old) | 수정 후 (New) |
|------|-----------|-------------|
| IB 전체 item_rate | 42% (all-time) | ~25% (최근 30일) |
| 블렌딩 | 42%×0.8 + 15%×0.2 = 36.6% | 25%×0.8 + 15%×0.2 = 23% |
| 공식 | max(0.5, 1.0-0.366×1.5) = 0.500 | max(0.65, 1.0-0.23×1.2) = 0.724 |
| 예측 감량률 | 50% | 27.6% |
| 예측값 10개 기준 | 5.0 | 7.2 |

> 72% 증가 → 과소발주 해소, 캘리브레이터가 자연스럽게 재조정

## 검증
```bash
python -m pytest tests/test_food_underorder_fix.py -v
python -m pytest tests/ -x -q   # 전체 회귀 (1702+25 = ~1727개)
```
