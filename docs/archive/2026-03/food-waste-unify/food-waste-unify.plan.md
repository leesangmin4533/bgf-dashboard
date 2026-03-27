# food-waste-unify Plan

> **Phase**: Plan
> **Feature**: food-waste-unify
> **Created**: 2026-03-01
> **Status**: Draft

---

## 1. Problem Statement

### 1.1 현상
- 2026-03-01 07:17 실행 결과, 전 푸드 카테고리에서 `[과소발주주의]` 경고 발생
- 예측→조정 감량이 35~53%로 과도 (avg_pred → avg_adj)
- 46513: 7일 일평균 발주 10.3개 vs 일평균 매출 35.6개 (29%)
- 46704: 7일 일평균 발주 14.2개 vs 일평균 매출 45.2개 (31%)

### 1.2 근본 원인
**같은 현상(폐기)을 4개 독립 메커니즘이 각각 감량 적용** → 복합 효과로 과도한 감소

```
① 동적 폐기 계수 (disuse_coef): 하한 0.65 → 최대 35% 감량
② 차수별 폐기 (delivery_waste): 하한 0.50 → 최대 50% 감량
   └ min(①, ②) = 나쁜 쪽 채택 → 최대 50% 감량
③ 캘리브레이터 (safety_days): 0.35까지 축소 가능
④ 폐기원인 피드백 (waste_feedback): × 0.75 추가 감량

최악 복합: 예측값의 ~13%까지 감소 가능
```

### 1.3 악순환 루프
```
과소발주 → 소량 입고 → 일부 폐기 → 폐기율 상승
 → ①②③④ 각각 더 감량 → 더 과소발주 → ...
```

## 2. Goal

**폐기 감량을 단일 지점으로 통합**하여:
1. 4중 독립 감량 → 1중 통합 감량으로 단순화
2. 악순환 루프 차단
3. 감량 범위를 예측 가능하게 제한 (최대 30%)
4. 기존 테스트 호환성 유지

## 3. Scope

### 3.1 수정 대상 (4개 파일)

#### 1) `src/prediction/improved_predictor.py` (핵심)
- **제거**: Stage 11-12 개별 적용 (lines 1787-1821)
  - `get_dynamic_disuse_coefficient()` 직접 호출 제거
  - `get_delivery_waste_adjustment()` 직접 호출 제거
  - `min(disuse, delivery)` 이중 처벌 제거
  - `_food_disuse_cache` / `_load_food_coef_cache()` 제거 (방금 PDCA로 추가했지만, 통합으로 불필요)
- **제거**: 후처리 폐기원인 피드백 (lines 2106-2122)
  - `waste_fb.get_adjustment()` 호출 제거 (캘리브레이터에 흡수)
- **추가**: 통합 폐기 계수 단일 적용
  ```python
  unified_waste_coef = get_unified_waste_coefficient(item_cd, mid_cd, store_id)
  # 범위: 0.70 ~ 1.0 (최대 30% 감량)
  adjusted_prediction *= unified_waste_coef
  ```

#### 2) `src/prediction/categories/food.py` (통합 함수)
- **추가**: `get_unified_waste_coefficient()` — 모든 폐기 신호를 단일 계수로 통합
  - inventory_batches 기반 폐기율 (기존 disuse_coef 로직 흡수)
  - order_tracking 기반 차수별 폐기 (기존 delivery_waste 로직 흡수)
  - 두 소스를 가중 평균 (IB 70% + OT 30%), 단일 연속함수로 변환
  - 하한: **0.70** (기존 0.50→0.70 상향, 최대 30% 감량)
- **유지**: `get_dynamic_disuse_coefficient()` 함수 자체 (다른 곳 참조 가능)
- **유지**: `get_delivery_waste_adjustment()` 함수 자체 (다른 곳 참조 가능)
- **유지**: `FOOD_DISUSE_COEFFICIENT`, `DELIVERY_WASTE_COEFFICIENT` 상수 (참조용)

#### 3) `src/prediction/food_waste_calibrator.py` (캘리브레이터 조정)
- **변경**: compound floor 0.1 → 0.15 (안전재고 최소 보장)
- **변경**: 회복 step 1.5x/max 0.08 → 2.0x/max 0.12 (심한 과소발주 시 빠른 회복)
- **흡수**: 폐기원인 피드백 역할 (waste_cause의 OVER_ORDER/DEMAND_DROP 정보를 calibrator 자체 조정에 반영)

#### 4) `tests/test_food_waste_unify.py` (신규 테스트)
- 통합 계수 범위 검증 (0.70 ~ 1.0)
- IB + OT 가중 평균 정확성
- 캘리브레이터 compound floor 0.15 검증
- 가속 회복 검증
- 기존 파이프라인 호환성 (need_qty 계산 동일 구조)
- 악순환 방지: 낮은 폐기율에서 감량 없음 확인

### 3.2 수정하지 않는 것

| 항목 | 이유 |
|------|------|
| `get_dynamic_disuse_coefficient()` 함수 | 다른 곳에서 참조 가능, 삭제하지 않음 |
| `get_delivery_waste_adjustment()` 함수 | 동일 |
| `WasteCauseAnalyzer` (Phase 1.55) | 분석 자체는 유효, 피드백 적용만 제거 |
| `WasteFeedbackAdjuster` 클래스 | 클래스 유지, improved_predictor에서 호출만 제거 |
| 비푸드 카테고리 로직 | 영향 없음 |
| Stage 1~10 (WMA~트렌드) | 정상 작동 중 |
| 안전재고 계산 구조 | `get_safety_stock_with_food_pattern()` 유지 |
| Stage 5(날씨) + Stage 6(기온×푸드) 중복 | 별도 PDCA로 분리 (이번은 폐기 통합에 집중) |

## 4. Implementation Order

1. `food.py`: `get_unified_waste_coefficient()` 함수 구현
2. `improved_predictor.py`: 4중 감량 → 단일 통합 계수 적용으로 교체
3. `food_waste_calibrator.py`: compound floor + 회복 속도 조정
4. `test_food_waste_unify.py`: 테스트 작성
5. 전체 테스트 실행

## 5. Expected Results

| 지표 | Before | After |
|------|--------|-------|
| 감량 소스 | 4개 독립 | 1개 통합 |
| 최대 감량 | ~87% (0.13) | 30% (0.70) |
| compound floor | 0.10 | 0.15 |
| 회복 속도 | 6~10일 | 3~5일 |
| 코드 복잡도 | 4개 분기+min() | 1개 함수 호출 |

## 6. Risk Assessment

| 리스크 | 대응 |
|--------|------|
| 폐기율 증가 가능 | 통합 계수 하한 0.70으로 여전히 30% 감량 가능 |
| 캘리브레이터가 추가 조정 | compound floor 0.15로 최소 안전재고 보장 |
| 기존 테스트 실패 | 함수 자체 유지, 호출 지점만 변경 |
