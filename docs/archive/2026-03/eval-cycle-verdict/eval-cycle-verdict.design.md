# Design: eval-cycle-verdict

> NORMAL_ORDER 판정 기준 개선 - 판매주기 기반 판정

## 1. 수정 대상 파일

| 파일 | 변경 내용 | 영향도 |
|------|----------|--------|
| `src/prediction/eval_calibrator.py` | `_judge_outcome()` NORMAL_ORDER 분기 교체, `_judge_normal_order()` 신규 | 핵심 |
| `src/settings/constants.py` | `MIN_DISPLAY_QTY`, `EVAL_CYCLE_MAX_DAYS`, 푸드 제외 Set | 낮음 |
| `tests/test_eval_calibrator.py` | 판정 로직 테스트 20개+ | 낮음 |

## 2. 상수 추가 (`constants.py`)

```python
# eval-cycle-verdict: NORMAL_ORDER 판정 기준
MIN_DISPLAY_QTY = 2                    # 비행사 최소 진열 개수
EVAL_CYCLE_MAX_DAYS = 7                # 판매주기 상한 (일)
LOW_TURNOVER_THRESHOLD = 1.0           # 저회전 기준 (일평균)
NORMAL_ORDER_EXCLUDE_MID_CDS = (       # 판정 개선 제외 중분류 (푸드류)
    FOOD_CATEGORIES                    # 001~005, 012
)
```

## 3. 핵심 로직 설계 (`_judge_normal_order`)

### 3.1 전체 흐름

```
_judge_outcome(decision="NORMAL_ORDER", ...)
  └─ _judge_normal_order(actual_sold, next_day_stock, was_stockout, record)
       │
       ├─ 푸드류 제외? → 기존 로직 (actual_sold > 0)
       │
       ├─ 최소 진열 체크
       │   ├─ 행사: PROMO_MIN_STOCK_UNITS (1+1→2, 2+1→3)
       │   └─ 비행사: MIN_DISPLAY_QTY (2)
       │   └─ next_day_stock < min_display → UNDER_ORDER
       │
       ├─ 저회전 (daily_avg < 1.0): 판매주기 판정
       │   ├─ 주기 = min(ceil(1/daily_avg), EVAL_CYCLE_MAX_DAYS)
       │   ├─ 최근 주기일 내 판매 합계 > 0 → CORRECT
       │   ├─ 주기일 내 판매 = 0 + 품절 → UNDER_ORDER
       │   └─ 주기일 내 판매 = 0 + 재고충분 → OVER_ORDER
       │
       └─ 고회전 (daily_avg >= 1.0): 안전재고 판정
           ├─ next_day_stock >= daily_avg → CORRECT (1일치 이상 유지)
           ├─ was_stockout → UNDER_ORDER
           └─ next_day_stock > 0 but < daily_avg → CORRECT (재고 존재)
```

### 3.2 저회전 판매주기 판정 — 다일 룩백 구현

```python
def _get_recent_sales_sum(self, item_cd: str, eval_date: str, lookback_days: int) -> int:
    """eval_outcomes에서 최근 N일간 actual_sold_qty 합계 조회"""
    # eval_outcomes 테이블 자체에서 조회 (추가 테이블 불필요)
    # WHERE item_cd = ? AND eval_date BETWEEN (eval_date - lookback) AND eval_date
    #   AND actual_sold_qty IS NOT NULL
    # RETURN SUM(actual_sold_qty)
```

- 추가 DB 테이블 불필요: `eval_outcomes.actual_sold_qty`에서 직접 조회
- 성능: item_cd + eval_date 인덱스 활용, 최대 7일 × 1상품 = 경량 쿼리

### 3.3 고회전 안전재고 판정

```python
# daily_avg >= 1.0인 상품
if next_day_stock >= record_daily_avg:
    return "CORRECT"     # 1일치 이상 재고 유지됨
if was_stockout:
    return "UNDER_ORDER" # 품절 발생
return "CORRECT"         # 재고 존재 (0 < stock < daily_avg)
```

- 기존 `actual_sold > 0` 대비: 재고 유지 관점으로 전환
- 품절만 UNDER_ORDER, 나머지는 재고 유지 = 성공

### 3.4 최소 진열 기준

```python
def _get_min_display_qty(self, record: Dict) -> int:
    """행사/비행사에 따른 최소 진열 수량"""
    promo_type = record.get("promo_type")
    if promo_type:
        return PROMO_MIN_STOCK_UNITS.get(promo_type, MIN_DISPLAY_QTY)
    return MIN_DISPLAY_QTY  # 기본 2개
```

## 4. 메서드 시그니처

```python
# eval_calibrator.py — 신규 메서드

def _judge_normal_order(
    self, actual_sold: int, next_day_stock: int,
    was_stockout: bool, record: Dict[str, Any]
) -> str:
    """NORMAL_ORDER 전용 판정 (푸드 제외, 주기/안전재고 기반)"""

def _get_recent_sales_sum(
    self, item_cd: str, eval_date: str, lookback_days: int
) -> int:
    """eval_outcomes에서 최근 N일 판매 합계"""

def _get_min_display_qty(self, record: Dict[str, Any]) -> int:
    """행사/비행사에 따른 최소 진열 수량"""
```

## 5. `_judge_outcome` 변경

```python
# 변경 전 (lines 407-413)
elif decision == "NORMAL_ORDER":
    if actual_sold > 0:
        return "CORRECT"
    if was_stockout:
        return "UNDER_ORDER"
    return "OVER_ORDER"

# 변경 후
elif decision == "NORMAL_ORDER":
    return self._judge_normal_order(
        actual_sold, next_day_stock, was_stockout, record
    )
```

## 6. 테스트 설계

| # | 테스트 케이스 | 입력 | 기대 결과 |
|---|-------------|------|----------|
| 1 | 푸드류 → 기존 로직 유지 | mid_cd=001, sold=0 | OVER_ORDER (기존) |
| 2 | 푸드류 → 판매 있으면 적중 | mid_cd=003, sold=2 | CORRECT (기존) |
| 3 | 저회전 + 당일 판매 | avg=0.5, sold=1 | CORRECT |
| 4 | 저회전 + 주기 내 판매(룩백) | avg=0.33, 3일 내 sold_sum=1 | CORRECT |
| 5 | 저회전 + 주기 내 미판매 + 품절 | avg=0.5, 2일 sold=0, stockout | UNDER_ORDER |
| 6 | 저회전 + 주기 내 미판매 + 재고 | avg=0.5, 2일 sold=0, stock=3 | OVER_ORDER |
| 7 | 고회전 + 재고 충분 | avg=3.0, stock=5 | CORRECT |
| 8 | 고회전 + 품절 | avg=2.0, stockout | UNDER_ORDER |
| 9 | 고회전 + 재고 부족(비품절) | avg=5.0, stock=2 | CORRECT |
| 10 | 행사 1+1 + 재고 2 이상 | promo=1+1, stock=3 | CORRECT |
| 11 | 행사 1+1 + 재고 1 | promo=1+1, stock=1 | UNDER_ORDER |
| 12 | 행사 2+1 + 재고 3 이상 | promo=2+1, stock=4 | CORRECT |
| 13 | 행사 2+1 + 재고 2 | promo=2+1, stock=2 | UNDER_ORDER |
| 14 | 비행사 + 재고 2 이상 | promo=None, stock=2 | 진열 OK |
| 15 | 비행사 + 재고 1 | promo=None, stock=1 | UNDER_ORDER |
| 16 | 주기 상한 초과 (avg=0.1) | cycle=10→capped 7 | 7일 윈도우 |
| 17 | daily_avg=0 (데이터 없음) | avg=0 | 기존 로직 폴백 |
| 18 | 고회전 + 당일 판매>0 | avg=2.0, sold=3 | CORRECT |
| 19 | 저회전 + 행사 + 재고 부족 | avg=0.5, promo=1+1, stock=1 | UNDER_ORDER |
| 20 | verify_yesterday 통합 테스트 | 전체 흐름 | 정상 동작 |

## 7. 구현 순서

1. `constants.py` — 상수 추가
2. `eval_calibrator.py` — `_judge_normal_order()`, `_get_recent_sales_sum()`, `_get_min_display_qty()` 구현
3. `eval_calibrator.py` — `_judge_outcome()` NORMAL_ORDER 분기 교체
4. `tests/test_eval_calibrator.py` — 20개 테스트 작성
5. 기존 전체 테스트 실행 (2904개 통과 확인)
