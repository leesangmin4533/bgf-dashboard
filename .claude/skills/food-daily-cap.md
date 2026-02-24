# 푸드류 요일별 총량 상한 (food_daily_cap)

## When to Use

- 푸드류(001~005, 012) 발주 총량 제한 로직을 확인하거나 수정할 때
- 폐기량 목표를 조정할 때
- 탐색/활용 상품 선별 비율을 변경할 때
- 새로운 푸드 카테고리를 추가할 때

## Common Pitfalls

- ❌ `waste_buffer`를 비율(%)로 변경 → 설계 의도 파괴
- ✅ `waste_buffer`는 **절대 수량** (폐기 허용 개수). 비율이 아님

- ❌ `fallback_daily_avg`를 매장 규모에 맞춰 변경 → 데이터 축적되면 자동 해결
- ✅ fallback은 데이터 없을 때만 사용됨. 21일 데이터 쌓이면 실제 평균으로 대체

- ❌ `explore_ratio`를 0으로 설정 → 신상품 검증 기회 소멸
- ✅ 탐색 슬롯은 신상품이 proven으로 승격되는 유일한 경로

---

## 핵심 설계 원칙

### 공식

```
cap = round(요일별_평균_판매량) + waste_buffer(3)
```

### 이 공식이 어떤 매장에든 적용 가능한 이유

`get_weekday_avg_sales()`가 **해당 매장의 해당 요일 실제 판매 데이터**를 DB에서 조회한다.
따라서 매장 규모·입지·고객층과 무관하게 자동 적응한다.

```
매장A (소형, 일평균 5개):  cap = 5 + 3 = 8   → 폐기 ~3개
매장B (중형, 일평균 14개): cap = 14 + 3 = 17  → 폐기 ~3개
매장C (대형, 일평균 35개): cap = 35 + 3 = 38  → 폐기 ~3개
매장D (초대형, 일평균 60개): cap = 60 + 3 = 63 → 폐기 ~3개
```

**어떤 매장이든 폐기가 최대 ~3개로 일정하다.**
`waste_buffer=3`은 "몇 개까지 폐기를 허용할 것인가"라는 절대 수량 기준이지, 비율이 아니다.

### 절대로 변경하면 안 되는 것

| 항목 | 현재값 | 변경 금지 사유 |
|------|--------|---------------|
| `waste_buffer` 타입 | 절대 수량 (개) | 비율(%)로 바꾸면 소형 매장 폐기 0개(결품), 대형 매장 폐기 9개(과다)로 왜곡 |
| `get_weekday_avg_sales` 데이터 소스 | 해당 매장 DB (`daily_sales`) | 외부 평균이나 고정값으로 대체 시 매장 특성 소멸 |
| 요일 필터링 방식 | Python `datetime.weekday()` | SQLite `strftime('%w')` 사용 시 로케일 의존성 발생 |

### 조정 가능한 값

| 항목 | 현재값 | 조정 범위 | 의미 |
|------|--------|-----------|------|
| `waste_buffer` | 3 | 1~5 | 일일 폐기 허용 수량. 3=폐기 3개 허용 |
| `explore_ratio` | 0.25 | 0.10~0.35 | 신상품 탐색 비율. 높을수록 신상품 많이 시도 |
| `lookback_days` | 21 | 14~42 | 평균 계산 기간. 짧으면 최근 트렌드 반영, 길면 안정적 |
| `new_item_max_data` | 5 | 3~7 | 신상품 판별 기준. 데이터 N일 미만이면 신상품 |

---

## 모듈 구조

### 파일: `src/prediction/categories/food_daily_cap.py`

### 설정 상수

```python
FOOD_DAILY_CAP_CONFIG = {
    "enabled": True,
    "target_categories": ['001', '002', '003', '004', '005', '012'],
    "waste_buffer": 3,           # 폐기 허용 수량 (절대값, 비율 아님)
    "lookback_days": 21,         # 요일별 평균 계산 기간
    "min_data_weeks": 2,         # 최소 데이터 주 수
    "explore_ratio": 0.25,       # 탐색 슬롯 비율 (25%)
    "new_item_days": 7,          # 신상품 판별 기준 (첫 등장 N일 이내)
    "new_item_max_data": 5,      # 신상품 데이터 일수 상한
    "fallback_daily_avg": 15,    # 데이터 부족 시 기본값
}
```

### 함수 흐름

```
apply_food_daily_cap(order_list, target_date)     ← 메인 진입점
  │
  ├─ 비푸드류 → 그대로 통과
  │
  └─ 푸드류 mid_cd별:
       ├─ get_weekday_avg_sales(mid_cd, weekday)  ← DB 조회
       │    └─ daily_sales에서 최근 21일, 해당 요일 평균
       │
       ├─ cap = round(avg) + waste_buffer
       │
       ├─ 상품 수 ≤ cap → 전부 유지
       │
       └─ 상품 수 > cap → select_items_with_cap()
            ├─ classify_items() → proven / new 분류
            ├─ exploit_slots = cap × 0.75 (검증 상품)
            └─ explore_slots = cap × 0.25 (신상품)
```

### 상품 분류 기준

```
data_days >= 6  → proven (검증 상품) : 판매 실적 충분, 우선 발주
data_days < 5   → new (신상품)       : 데이터 부족, 탐색 슬롯에 배치
data_days = 5   → proven 처리        : 중간 영역, 검증으로 분류
```

### 엣지 케이스 처리

| 상황 | 처리 |
|------|------|
| 해당 요일 데이터 없음 (2주 미만) | 전체 일평균 사용 (요일 구분 없이) |
| 전체 데이터 없음 | `fallback_daily_avg=15` 사용 |
| 상품 수 < cap | cap 적용하지 않음 (전부 발주) |
| new 상품 부족 | proven에서 추가 채움 |
| proven 상품 부족 | new에서 추가 채움 |
| `enabled=False` | 원본 그대로 반환 |

---

## 적용 위치 (auto_order.py)

### 1차 적용: `get_recommendations()` — 초기 발주 목록 생성 후

```python
order_list.sort(...)
order_list = apply_food_daily_cap(order_list, target_date=target_date)
```

### 2차 적용: `_apply_pending_and_stock_to_order_list()` — 재고/미입고 반영 후

```python
adjusted_list.sort(...)
adjusted_list = apply_food_daily_cap(adjusted_list, target_date=None)
```

2차 적용이 필요한 이유: 미입고/재고 반영으로 일부 상품이 제거된 후에도
총량 상한이 유지되어야 하므로 재적용한다.

---

## food.py와의 관계

| 모듈 | 역할 | 적용 시점 |
|------|------|-----------|
| `food.py` | **개별 상품** 안전재고 (유통기한 기반) | 예측 단계 (상품별) |
| `food_daily_cap.py` | **중분류 전체** 총량 상한 (요일 평균 기반) | 후처리 단계 (그룹별) |

두 모듈은 독립적으로 작동한다.
`food.py`가 개별 상품의 발주량을 결정하고, `food_daily_cap.py`가 전체 수량을 제한한다.

---

## 실제 효과 예시

```
[적용 전] 주먹밥(002) 금요일
  37개 상품 × 1개 = 37개 발주
  일 판매 ~14개, 폐기 ~23개

[적용 후] 주먹밥(002) 금요일
  cap = round(17.0) + 3 = 20개
  proven 15개 + new 5개 = 20개 발주
  일 판매 ~17개, 폐기 ~3개
```
