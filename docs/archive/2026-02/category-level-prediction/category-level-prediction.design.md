# Design: category-level-prediction

> Plan 참조: `docs/01-plan/features/category-level-prediction.plan.md`

## 1. 아키텍처

```
auto_order.py::get_recommendations()
  ├── pre_order_evaluator.evaluate_all()       # FORCE/SKIP 분류
  ├── improved_predictor.get_order_candidates() # 개별 품목 예측
  │     └── predict_single()
  │           └── calculate_weighted_average()  # ★ 수정1: None imputation
  ├── FORCE_ORDER 보충
  ├── 정렬
  └── ★ 수정2: CategoryDemandForecaster.supplement_orders() # 카테고리 floor
```

## 2. 수정1: WMA None-day Imputation (신선식품)

### 2.1 수정 위치
`src/prediction/improved_predictor.py` `calculate_weighted_average()` (line ~854)

### 2.2 현재 로직
```python
# stock_qty is None인 날(레코드 없음)은 imputation 대상에서 제외
available = [(d, qty, stk) for d, qty, stk in sales_history
             if stk is not None and stk > 0]
stockout = [(d, qty, stk) for d, qty, stk in sales_history
            if stk is not None and stk == 0]
```

### 2.3 변경 로직
```python
FRESH_FOOD_MID_CDS = {"001", "002", "003", "004", "005"}

# 신선식품: stock_qty is None도 품절로 취급 (매장에 상품 없었던 날)
if mid_cd in FRESH_FOOD_MID_CDS:
    available = [(d, qty, stk) for d, qty, stk in sales_history
                 if stk is not None and stk > 0]
    stockout = [(d, qty, stk) for d, qty, stk in sales_history
                if stk is not None and stk == 0]
    none_days = [(d, qty, stk) for d, qty, stk in sales_history
                 if stk is None]
    # None일도 stockout에 합산
    stockout = stockout + none_days
else:
    # 비식품: 기존 로직 유지
    available = [(d, qty, stk) for d, qty, stk in sales_history
                 if stk is not None and stk > 0]
    stockout = [(d, qty, stk) for d, qty, stk in sales_history
                if stk is not None and stk == 0]
```

imputation 로직은 동일: `stockout` 날의 sale_qty를 `avg_available_sales`로 대체

### 2.4 안전장치
- `min_available_days >= 3`: 비품절 데이터 최소 3일 확보 (기존 조건 유지)
- 비품절일이 0일이면 imputation 포기 → 기존 로직 폴백

### 2.5 예상 효과
주)고추장삼겹삼각1 (30일 중 10일 레코드, 3일 재고보유):
- 현재: WMA ≈ 0.27 (None일 = sale 0)
- 변경: WMA ≈ 0.85 (None일 = 비품절일 평균 1.0으로 impute)

## 3. 수정2: CategoryDemandForecaster

### 3.1 신규 파일
`src/prediction/category_demand_forecaster.py`

### 3.2 클래스 설계

```python
class CategoryDemandForecaster:
    """카테고리 총량 예측 및 개별 발주 보충"""

    FRESH_FOOD_MID_CDS = {"001", "002", "003", "004", "005"}

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._config = PREDICTION_PARAMS.get("category_floor", {})

    def supplement_orders(
        self,
        order_list: List[Dict[str, Any]],
        eval_results: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        카테고리 총량 대비 부족분 보충

        Args:
            order_list: 기존 발주 추천 목록 (get_recommendations 결과)
            eval_results: pre_order_evaluator 결과 (FORCE/SKIP 등)

        Returns:
            보충된 발주 목록
        """

    def _get_category_daily_totals(
        self, mid_cd: str, days: int = 7
    ) -> List[Tuple[str, int]]:
        """카테고리별 일별 총매출 시계열 조회"""

    def _calculate_category_forecast(
        self, daily_totals: List[Tuple[str, int]]
    ) -> float:
        """카테고리 총량 WMA 계산"""

    def _get_supplement_candidates(
        self, mid_cd: str, existing_items: Set[str],
        eval_results: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """보충 대상 품목 선정 (최근 판매 빈도순)"""

    def _distribute_shortage(
        self, shortage: int, candidates: List[Dict],
        max_per_item: int = 1
    ) -> List[Dict[str, Any]]:
        """부족분을 후보 품목에 분배"""
```

### 3.3 supplement_orders() 상세 플로우

```
for mid_cd in FRESH_FOOD_MID_CDS:
  1. 카테고리 총량 WMA 계산
     daily_totals = _get_category_daily_totals(mid_cd, days=7)
     category_forecast = _calculate_category_forecast(daily_totals)

  2. 현재 발주 목록에서 해당 카테고리 합산
     current_sum = sum(order_qty for item in order_list if item.mid_cd == mid_cd)

  3. 부족 여부 판단
     threshold = config["threshold"]  # 0.7
     if current_sum >= category_forecast * threshold:
         continue  # 충분

  4. 부족분 계산
     shortage = int(category_forecast * threshold) - current_sum
     shortage = max(0, shortage)

  5. 보충 대상 선정
     candidates = _get_supplement_candidates(
         mid_cd, existing_items, eval_results
     )
     # 조건: SKIP이 아닌 상품, CUT가 아닌 상품, 최근 7일 내 판매 이력 있음
     # 정렬: sell_day_ratio DESC (자주 팔리는 품목 우선)

  6. 분배
     supplements = _distribute_shortage(shortage, candidates, max_per_item=1)

  7. order_list에 추가 (기존 항목이면 qty 증가, 새 항목이면 추가)
```

### 3.4 SQL: 카테고리 일별 총매출 조회

```sql
-- sales_repo.py에 추가
SELECT ds.sales_date, SUM(ds.sale_qty) as total_sale
FROM daily_sales ds
WHERE ds.mid_cd = ?
  AND ds.sales_date >= date('now', '-' || ? || ' days')
  AND ds.sales_date < date('now')
GROUP BY ds.sales_date
ORDER BY ds.sales_date DESC
```

### 3.5 SQL: 보충 후보 품목 조회

```sql
-- 최근 7일 내 판매 이력 있는 품목 (sell_day_ratio 순)
SELECT ds.item_cd,
       COUNT(DISTINCT ds.sales_date) as appear_days,
       SUM(CASE WHEN ds.sale_qty > 0 THEN 1 ELSE 0 END) as sell_days,
       SUM(ds.sale_qty) as total_sale
FROM daily_sales ds
WHERE ds.mid_cd = ?
  AND ds.sales_date >= date('now', '-7 days')
  AND ds.sales_date < date('now')
GROUP BY ds.item_cd
HAVING sell_days > 0
ORDER BY sell_days DESC, total_sale DESC
```

## 4. 호출 위치: auto_order.py

### 4.1 삽입 위치
`get_recommendations()` line ~958 (PredictionResult -> dict 변환 직후)

### 4.2 코드

```python
# PredictionResult -> dict 변환
order_list = [self._convert_prediction_result_to_dict(r) for r in candidates]

# ★ 카테고리 총량 floor 보충 (신선식품)
if self._category_forecaster and PREDICTION_PARAMS.get("category_floor", {}).get("enabled", False):
    before_count = len(order_list)
    before_qty = sum(item.get('final_order_qty', 0) for item in order_list)
    order_list = self._category_forecaster.supplement_orders(
        order_list, eval_results
    )
    after_count = len(order_list)
    after_qty = sum(item.get('final_order_qty', 0) for item in order_list)
    if after_qty > before_qty:
        logger.info(
            f"[카테고리Floor] 보충: {before_count}건/{before_qty}개 "
            f"→ {after_count}건/{after_qty}개 (+{after_qty - before_qty}개)"
        )
```

### 4.3 초기화
`AutoOrderSystem.__init__()` 에 추가:
```python
from src.prediction.category_demand_forecaster import CategoryDemandForecaster
self._category_forecaster = CategoryDemandForecaster(store_id=self.store_id)
```

## 5. prediction_config.py 설정

```python
"category_floor": {
    "enabled": True,
    "target_mid_cds": ["001", "002", "003", "004", "005"],
    "threshold": 0.7,        # 개별합이 총량의 70% 미만이면 보정
    "max_add_per_item": 1,   # 품목당 최대 추가 발주
    "wma_days": 7,           # 카테고리 총량 WMA 기간
    "min_candidate_sell_days": 1,  # 보충 후보 최소 판매일수
}
```

## 6. 테스트 설계

### 6.1 test_category_demand_forecaster.py (신규)

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | test_get_category_daily_totals | mid_cd별 일별 총매출 정상 조회 |
| 2 | test_category_forecast_wma | WMA 계산 정확성 |
| 3 | test_supplement_below_threshold | 개별합 < 70% → 보충 발생 |
| 4 | test_no_supplement_above_threshold | 개별합 >= 70% → 보충 없음 |
| 5 | test_supplement_max_per_item | 품목당 +1개 상한 준수 |
| 6 | test_skip_non_fresh_food | 비식품(016 등) 대상 아님 |
| 7 | test_skip_cut_items | CUT 상품 보충 대상 제외 |
| 8 | test_candidate_sort_by_frequency | 판매 빈도순 분배 |
| 9 | test_existing_item_qty_increase | 기존 항목 수량 증가 |
| 10 | test_new_item_added | 새 항목 추가 |
| 11 | test_disabled_config | enabled=False → 변화 없음 |
| 12 | test_empty_order_list | 빈 목록 → 전량 카테고리 floor |

### 6.2 test_wma_none_imputation.py (신규)

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 13 | test_none_imputation_fresh_food | 신선식품 None일 imputation |
| 14 | test_none_no_imputation_non_food | 비식품 None일 imputation 안함 |
| 15 | test_none_imputation_min_available | 가용일 < 3 → imputation 포기 |
| 16 | test_mixed_none_and_stockout | None + stock=0 혼합 처리 |

### 6.3 test_auto_order_integration.py (기존 파일에 추가)

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 17 | test_category_floor_integration | get_recommendations에서 보충 호출 |
| 18 | test_category_floor_disabled | 설정 비활성화 시 미호출 |

## 7. 구현 순서

1. `prediction_config.py`: category_floor 설정 추가
2. `improved_predictor.py`: WMA None imputation 수정 (FRESH_FOOD_MID_CDS)
3. `category_demand_forecaster.py`: 신규 클래스 구현
4. `auto_order.py`: CategoryDemandForecaster 초기화 + supplement_orders 호출
5. `tests/test_category_demand_forecaster.py`: 테스트 12개
6. `tests/test_wma_none_imputation.py`: 테스트 4개
7. 기존 테스트 2개 추가 (auto_order integration)
8. 전체 테스트 실행 (기존 2216개 + 신규 18개)

## 8. 영향 분석

| 기존 모듈 | 영향 | 비고 |
|-----------|------|------|
| improved_predictor.py | WMA 변경 (신선식품만) | mid_cd 조건 분기 |
| auto_order.py | 초기화 + 1줄 호출 추가 | get_recommendations 말미 |
| prediction_config.py | 설정 추가 | 기존 영향 없음 |
| pre_order_evaluator.py | 변경 없음 | eval_results 읽기만 |
| demand_classifier.py | 변경 없음 | |
| categories/*.py | 변경 없음 | |
