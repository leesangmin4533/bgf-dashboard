# Design: category-total-prediction-largecd

## 1. 아키텍처

```
auto_order.py
  └─ get_recommendations()
       ├─ ... (기존 파이프라인)
       ├─ apply_food_daily_cap()              # 푸드 총량 상한
       ├─ CategoryDemandForecaster            # mid_cd level floor (기존)
       └─ LargeCategoryForecaster             # large_cd level floor (신규) ★
            ├─ forecast_large_cd_total()       # large_cd WMA 총량
            ├─ get_mid_cd_ratios()             # mid_cd 비율 계산
            ├─ distribute_to_mid_cd()          # mid_cd별 예상 수요
            └─ apply_floor_correction()        # 부족분 보충
```

## 2. 클래스 설계

### LargeCategoryForecaster

```python
class LargeCategoryForecaster:
    """대분류(large_cd) 기반 카테고리 총량 예측 + mid_cd 배분 보충"""

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._config = PREDICTION_PARAMS.get("large_category_floor", {})

    @property
    def enabled(self) -> bool
    @property
    def target_large_cds(self) -> Set[str]

    def supplement_orders(
        self, order_list, eval_results=None, cut_items=None
    ) -> List[Dict]

    def forecast_large_cd_total(
        self, large_cd: str, days: int = 14
    ) -> float

    def get_mid_cd_ratios(
        self, large_cd: str, days: int = 14
    ) -> Dict[str, float]

    def distribute_to_mid_cd(
        self, total_forecast: float, ratios: Dict[str, float]
    ) -> Dict[str, float]

    def apply_floor_correction(
        self, order_list, mid_cd_targets, eval_results, cut_items
    ) -> List[Dict]
```

## 3. 메서드 상세

### 3.1 forecast_large_cd_total(large_cd, days=14)

**목적**: large_cd에 속하는 모든 상품의 daily_sales 합계로 WMA 총량 예측

**SQL 패턴** (store DB + common DB ATTACH):
```sql
-- store DB에서 실행, common DB ATTACH 필요
SELECT ds.sales_date, SUM(ds.sale_qty) as total_sale
FROM daily_sales ds
JOIN common.mid_categories mc ON ds.mid_cd = mc.mid_cd
WHERE mc.large_cd = ?
  AND ds.sales_date >= date('now', '-' || ? || ' days')
  AND ds.sales_date < date('now')
GROUP BY ds.sales_date
ORDER BY ds.sales_date DESC
```

**WMA 계산**: 선형 가중 (최신 = n, 최고 = 1)

**Fallback**: mid_categories에 large_cd 없으면 LARGE_CD_TO_MID_CD 상수 매핑 사용

### 3.2 get_mid_cd_ratios(large_cd, days=14)

**목적**: large_cd 내 각 mid_cd의 매출 비율 계산

```sql
SELECT ds.mid_cd, SUM(ds.sale_qty) as mid_total
FROM daily_sales ds
JOIN common.mid_categories mc ON ds.mid_cd = mc.mid_cd
WHERE mc.large_cd = ?
  AND ds.sales_date >= date('now', '-' || ? || ' days')
  AND ds.sales_date < date('now')
GROUP BY ds.mid_cd
```

ratio[mid_cd] = mid_total / sum(all_mid_totals)

### 3.3 distribute_to_mid_cd(total_forecast, ratios)

**목적**: 총량을 mid_cd 비율로 배분

```python
targets = {}
for mid_cd, ratio in ratios.items():
    targets[mid_cd] = total_forecast * ratio
return targets
```

### 3.4 apply_floor_correction(order_list, mid_cd_targets, ...)

**목적**: 개별 상품 예측 합계가 mid_cd 예상 수요의 threshold 미만이면 보충

```
for each mid_cd in targets:
    current_sum = sum of order_qty for items with this mid_cd
    floor_qty = targets[mid_cd] * threshold
    if current_sum < floor_qty:
        shortage = floor_qty - current_sum
        candidates = recent sellers in this mid_cd not in order_list
        distribute shortage to candidates (max_add_per_item limit)
```

### 3.5 supplement_orders (메인 진입점)

```python
def supplement_orders(self, order_list, eval_results=None, cut_items=None):
    if not self.enabled:
        return order_list

    for large_cd in self.target_large_cds:
        total = self.forecast_large_cd_total(large_cd)
        if total <= 0:
            continue
        ratios = self.get_mid_cd_ratios(large_cd)
        if not ratios:
            continue
        targets = self.distribute_to_mid_cd(total, ratios)
        order_list = self.apply_floor_correction(
            order_list, targets, eval_results, cut_items
        )
    return order_list
```

## 4. 설정

### prediction_config.py 추가
```python
"large_category_floor": {
    "enabled": True,
    "target_large_cds": ["01", "02", "12"],
    "threshold": 0.75,           # mid_cd 예상 수요의 75% 미만이면 보정
    "max_add_per_item": 2,       # 품목당 최대 추가 발주
    "wma_days": 14,              # WMA 기간
    "ratio_days": 14,            # mid_cd 비율 계산 기간
    "min_candidate_sell_days": 2, # 보충 후보 최소 판매일수
}
```

### constants.py 추가
```python
LARGE_CD_TO_MID_CD = {
    "01": ["001", "002", "003", "004", "005"],
    "02": ["006", "007", "008", "009", "010", "011", "012"],
    "11": ["013", "014"],
    "12": ["015", "016", "017", "020", "021"],
    "13": ["018", "019"],
    ...
}
```

## 5. auto_order.py 통합 위치

```python
# 기존 코드 (get_recommendations 메서드 끝부분):
#   apply_food_daily_cap()
#   CategoryDemandForecaster.supplement_orders()  # mid_cd level
#   LargeCategoryForecaster.supplement_orders()   # large_cd level ★ 신규
```

기존 CategoryDemandForecaster(mid_cd level) 이후에 실행하므로,
mid_cd level에서 이미 보충된 수량이 large_cd level에 반영됨.

## 6. 테스트 계획 (17개)

### WMA 계산 (3개)
1. test_wma_uniform - 동일 값 → WMA = 같은 값
2. test_wma_weighted_recent - 최근일 가중 WMA 정확성
3. test_wma_empty - 빈 데이터 → 0

### mid_cd 비율 (3개)
4. test_ratios_single_mid - 단일 mid_cd → 비율 1.0
5. test_ratios_multiple_mid - 복수 mid_cd 비율 합 = 1.0
6. test_ratios_empty - 데이터 없으면 빈 dict

### 총량 배분 (2개)
7. test_distribute_proportional - 비율대로 정확히 배분
8. test_distribute_zero_total - 총량 0이면 모두 0

### floor 보충 (4개)
9. test_supplement_below_threshold - 부족 시 보충 발생
10. test_no_supplement_above_threshold - 충분하면 보충 없음
11. test_supplement_max_per_item - max_add_per_item 준수
12. test_supplement_cut_items_excluded - CUT 상품 제외

### 통합 (3개)
13. test_full_flow_single_large_cd - 01 단일 large_cd 전체 플로우
14. test_full_flow_multi_large_cd - 01+12 복수 large_cd
15. test_disabled_returns_unchanged - enabled=False → 원본 반환

### 엣지 케이스 (2개)
16. test_empty_order_list - 빈 발주목록 처리
17. test_fallback_mid_cd_mapping - large_cd DB 미등록 시 상수 매핑 사용

## 7. 기존 코드와의 관계

| 컴포넌트 | 역할 | 변경 |
|----------|------|------|
| CategoryDemandForecaster | mid_cd 단위 floor 보충 | 변경 없음 |
| LargeCategoryForecaster | large_cd 단위 floor 보충 | 신규 |
| auto_order.py | 오케스트레이션 | 통합 포인트 추가 |
| improved_predictor.py | 개별 상품 예측 | 변경 없음 |

## 8. 안전장치

- `enabled` 설정으로 on/off 가능
- `threshold` (기본 0.75)로 보충 민감도 조절
- `max_add_per_item` (기본 2)로 과잉 보충 방지
- 전체 보충량 로깅 (large_cd별, mid_cd별)
- Exception wrapper로 실패 시 원본 order_list 유지
