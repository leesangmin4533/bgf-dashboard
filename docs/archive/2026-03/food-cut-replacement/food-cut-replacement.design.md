# Design: food-cut-replacement

> 푸드 CUT(발주중지) 상품 발생 시 동일 mid_cd 대체 보충

**Plan 참조**: `docs/01-plan/features/food-cut-replacement.plan.md`

## 1. 아키텍처 개요

### 파이프라인 내 위치

```
generate_order_list_new() 플로우:

  예측 → 필터링(order_filter.py) → FORCE보충 → 신상품 → 스마트오버라이드
  → food_daily_cap
  → ★ CUT 대체 보충 (신규: cut_replacement.CutReplacementService.supplement_cut_shortage)
  → CategoryFloor(mid_cd) → LargeCdFloor(large_cd)
```

### 삽입 위치 근거

| 조건 | 설명 |
|------|------|
| food_daily_cap **이후** | cap이 적용된 최종 리스트에서 CUT 손실분만 보충 |
| CategoryFloor **이전** | CUT 보충분이 order_list에 반영된 상태로 Floor가 총량 판단 → 이중 보충 방지 |
| CUT 재필터와 **분리** | CUT 재필터(line 1053-1059, 1110-1115)는 제거만, 보충은 별도 단계 |

## 2. 수정 파일 상세

### 2.1 신규 모듈: `src/order/cut_replacement.py` — `CutReplacementService` 클래스

> **설계 근거 (W-10)**: OrderAdjuster는 DB 접근 없는 순수 계산 클래스(SRP 준수). DB 조회가 필요한 CUT 대체 보충 로직은 별도 서비스 클래스로 분리.

```python
class CutReplacementService:
    """CUT 대체 보충 서비스 — DB 조회 + 후보 선정 + 분배 담당"""

    def __init__(self, store_id: str):
        self.store_id = store_id

    def supplement_cut_shortage(
        self,
        order_list: List[Dict[str, Any]],
        cut_lost_items: List[Dict[str, Any]],
        eval_results: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """CUT 탈락 상품의 수요를 동일 mid_cd 내 대체 상품으로 보충

        Args:
            order_list: 현재 발주 목록
            cut_lost_items: CUT 탈락 상품 목록
                [{item_cd, mid_cd, predicted_sales, item_nm}, ...]
            eval_results: 사전 평가 결과 (SKIP 제외용)

        Returns:
            보충 반영된 발주 목록
        """
```

**수정 대상 파일 목록**

| 파일 | 변경 종류 | 내용 |
|------|----------|------|
| `src/order/cut_replacement.py` | 신규 생성 | `CutReplacementService` 클래스 |
| `src/order/auto_order.py` | 수정 | CUT 탈락 캡처(2곳) + 보충 호출 삽입 |
| `src/prediction/prediction_config.py` | 수정 | `cut_replacement` 설정 블록 추가 |

#### 알고리즘 상세

**Step 1: mid_cd별 손실 수요 집계**
```python
lost_demand = {}  # {mid_cd: float}
for item in cut_lost_items:
    mid_cd = item.get("mid_cd", "")
    if mid_cd not in target_mid_cds:
        continue
    # C-1: 실제 dict 키는 predicted_sales (auto_order.py _convert_prediction_result_to_dict 기준)
    pred = item.get("predicted_sales", 0) or item.get("final_order_qty", 0)
    if pred > 0:
        lost_demand[mid_cd] = lost_demand.get(mid_cd, 0) + pred
```

- `cut_lost_items`는 CUT 재필터에서 제거된 상품 중 predicted_sales > 0인 것만 포함
- target_mid_cds: `["001", "002", "003", "004", "005"]` (config에서 로드)
- `# 참고: 012(빵)는 유통기한 3일로 CUT 리스크가 낮아 기본 제외. 필요 시 target_mid_cds에 추가 가능`

**Step 2: mid_cd별 대체 후보 선정 (DB 조회)**

```sql
SELECT ds.item_cd,
       COUNT(DISTINCT CASE WHEN ds.sale_qty > 0 THEN ds.sales_date END) as sell_days,
       SUM(ds.sale_qty) as total_sale,
       COALESCE(ri.stock_qty, 0) as current_stock,
       COALESCE(ri.pending_qty, 0) as pending_qty
FROM daily_sales ds
LEFT JOIN realtime_inventory ri ON ds.item_cd = ri.item_cd
WHERE ds.mid_cd = ?
  AND ds.sales_date >= date('now', '-7 days')
  AND ds.sales_date < date('now')
GROUP BY ds.item_cd
HAVING sell_days >= ?
ORDER BY sell_days DESC, total_sale DESC
```

후보 제외 조건:
- `item_cd in cut_items` (CUT 상품)
- `item_cd in skip_codes` (SKIP 평가)
- 재고+미입고 >= 안전재고 AND 이미 발주 목록에 있는 상품 (수량 증가 대상은 별도)
- `max_candidates`(기본 5)개까지만 사용

**Step 3: 정규화된 스코어 계산**

```python
# 후보 풀 내 min-max 정규화
daily_avgs = [c["daily_avg"] for c in candidates]
min_avg, max_avg = min(daily_avgs), max(daily_avgs)

for c in candidates:
    # 정규화: 0~1 범위
    norm_daily = (c["daily_avg"] - min_avg) / (max_avg - min_avg + 1e-9)
    norm_sell = c["sell_day_ratio"]  # 이미 0~1

    # 유통기한 1일 이하: 어제 재고 = 폐기 → effective_stock=0
    if c.get("expiration_days", 99) <= 1:
        effective_stock = 0
    else:
        effective_stock = c["current_stock"]

    stock_ratio = effective_stock / max(1, c.get("safety_stock", 1))
    norm_stock = 1 - min(1.0, stock_ratio)  # 재고 낮을수록 높은 점수

    c["score"] = norm_daily * 0.5 + norm_sell * 0.3 + norm_stock * 0.2
```

가중치 근거:
- `daily_avg × 0.5`: 매출 기여도 (대체 효과) 최우선
- `sell_day_ratio × 0.3`: 판매 안정성 (간헐 판매 배제)
- `(1 - stock_ratio) × 0.2`: 재고 부족 우선 보충

**Step 4: 보충 분배**

```python
candidates.sort(key=lambda c: -c["score"])
remaining = lost_demand[mid_cd] * replacement_ratio  # 0.8
total_added = 0

for cand in candidates:
    if remaining <= 0:
        break

    # 상품당 최대 보충량
    max_add = min(
        config["max_add_per_item"],       # 기본 2
        max(1, round(cand["daily_avg"])), # 일평균 이하
    )
    add_qty = min(max_add, int(remaining + 0.5))

    if add_qty <= 0:
        continue

    remaining -= add_qty
    total_added += add_qty

    # order_list에 병합
    existing = item_map.get(cand["item_cd"])
    if existing:
        existing["final_order_qty"] += add_qty
    else:
        order_list.append({
            "item_cd": cand["item_cd"],
            "item_nm": cand.get("item_nm", ""),
            "mid_cd": mid_cd,
            "final_order_qty": add_qty,
            "predicted_qty": 0,
            "order_qty": add_qty,
            "current_stock": cand.get("current_stock", 0),
            "source": "cut_replacement",
        })
```

**Step 5: 로깅**
```
[CUT보충] mid=002: CUT 6건(수요합=3.12) → 후보 8건 → 보충 3건(2.50/3.12, 80%)
[CUT보충]   주)뉴치킨마요삼각2: +1 (score=0.82, daily_avg=0.66)
```

### 2.2 `src/order/auto_order.py` — CUT 보충 호출

#### 변경 0: `__init__` 및 `__getattr__` — `_cut_lost_items` 초기화 (W-8)

`__getattr__` defaults에 추가:
```python
# __getattr__ lazy init defaults (기존 항목에 추가)
"_cut_lost_items": list,   # CUT 탈락 상품 누적 (execute() 시작 시 초기화)
```

`execute()` 메서드 시작 부분에 추가:
```python
def execute(self, ...):
    # W-8: 매 실행마다 초기화 (이전 실행의 CUT 목록 오염 방지)
    self._cut_lost_items = []
    ...
```

#### 변경 1: CUT 탈락 정보 캡처 — `_refilter_cut_items()` 헬퍼 도입 (W-7)

> **설계 근거 (W-7)**: CUT 재필터는 line 1053과 line 1110 두 곳에 존재. 동일 패턴을 헬퍼로 추출해 중복 제거.

신규 헬퍼 메서드:
```python
def _refilter_cut_items(
    self, order_list: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """CUT 상품을 order_list에서 제거하고 탈락 정보를 반환.

    Returns:
        (filtered_list, cut_lost_items)
        cut_lost_items: 푸드 카테고리이며 predicted_sales > 0인 CUT 탈락 상품
    """
    if not self._cut_items:
        return order_list, []

    cut_lost_items = [
        item for item in order_list
        if item.get("item_cd") in self._cut_items
        and item.get("mid_cd", "") in FOOD_CATEGORIES
        and (item.get("predicted_sales", 0) or item.get("final_order_qty", 0)) > 0
    ]
    filtered = [
        item for item in order_list
        if item.get("item_cd") not in self._cut_items
    ]
    cut_removed = len(order_list) - len(filtered)
    if cut_removed > 0:
        logger.info(f"[CUT 재필터] prefetch 실시간 감지 포함 {cut_removed}개 CUT 상품 제외")
    return filtered, cut_lost_items
```

호출 위치 1 (line 1053-1059 부근):
```python
# 변경 전:
if self._cut_items:
    before_cut = len(order_list)
    order_list = [item for item in order_list if item.get("item_cd") not in self._cut_items]
    cut_removed = before_cut - len(order_list)
    if cut_removed > 0:
        logger.info(f"[CUT 재필터] prefetch 실시간 감지 포함 {cut_removed}개 CUT 상품 제외")

# 변경 후:
order_list, _lost = self._refilter_cut_items(order_list)
self._cut_lost_items.extend(_lost)
```

호출 위치 2 (line 1110-1115 부근, 동일 패턴):
```python
order_list, _lost = self._refilter_cut_items(order_list)
self._cut_lost_items.extend(_lost)
```

#### 변경 2: CUT 보충 단계 삽입 (line 857 부근, food_daily_cap 이후)

```python
# 푸드류 요일별 총량 상한 적용
try:
    ...  # 기존 food_daily_cap 코드 유지
except Exception as e:
    ...

# ★ CUT 대체 보충 (신규)
try:
    from src.prediction.prediction_config import PREDICTION_PARAMS
    cut_replacement_cfg = PREDICTION_PARAMS.get("cut_replacement", {})
    if cut_replacement_cfg.get("enabled", False) and self._cut_lost_items:
        from src.order.cut_replacement import CutReplacementService
        svc = CutReplacementService(store_id=self.store_id)
        before_qty = sum(item.get('final_order_qty', 0) for item in order_list)
        order_list = svc.supplement_cut_shortage(
            order_list=order_list,
            cut_lost_items=self._cut_lost_items,
            eval_results=eval_results,
        )
        after_qty = sum(item.get('final_order_qty', 0) for item in order_list)
        if after_qty > before_qty:
            logger.info(
                f"[CUT보충] 보충: {before_qty}개 → {after_qty}개 "
                f"(+{after_qty - before_qty}개)"
            )
except Exception as e:
    logger.warning(f"CUT 대체 보충 실패 (원본 유지): {e}")

# ★ 카테고리 총량 floor 보충 (신선식품) — 기존 유지
```

> **C-3 수정**: `self._adjuster`(AutoOrderSystem의 실제 속성명)가 아닌 `CutReplacementService` 인스턴스를 직접 생성. OrderAdjuster SRP 보존.

### 2.3 `src/prediction/prediction_config.py` — 설정 추가

```python
# CUT 대체 보충: 발주중지 상품의 수요를 동일 mid_cd 내 대체 상품에 배분
# 참고: 012(빵)는 유통기한 3일로 CUT 리스크 낮아 기본 미포함. 필요 시 목록에 추가 가능.
"cut_replacement": {
    "enabled": True,
    "target_mid_cds": ["001", "002", "003", "004", "005"],
    "replacement_ratio": 0.8,      # CUT 수요의 80%까지 보충
    "max_add_per_item": 2,         # 상품당 최대 +2개
    "max_candidates": 5,           # mid_cd당 최대 후보 5개
    "min_sell_days": 1,            # 최근 7일 중 최소 판매일수
},
```

## 3. 데이터 흐름

```
                        ┌──────────────────────┐
                        │   예측 파이프라인      │
                        │ (improved_predictor)  │
                        └──────────┬───────────┘
                                   │
                    order_list (item_cd, mid_cd, predicted_sales, ...)
                                   │
                        ┌──────────▼───────────┐
                        │ order_filter.py       │
                        │ CUT 1차 필터 (DB)     │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │ prefetch_pending      │
                        │ DirectAPI CUT 감지    │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │ _refilter_cut_items() │
                        │ ★ cut_lost_items 캡처 │
                        │ (2곳 모두 동일 헬퍼)  │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │ 미입고+재고 조정      │
                        │ (order_adjuster)      │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │ food_daily_cap        │
                        └──────────┬───────────┘
                                   │
                  ┌────────────────▼────────────────┐
                  │  ★ CutReplacementService        │
                  │    .supplement_cut_shortage()   │
                  │  CUT 대체 보충 (신규)            │
                  │  - lost_demand 집계              │
                  │  - 후보 DB 조회 + 스코어          │
                  │  - 분배 → order_list 병합         │
                  └────────────────┬────────────────┘
                                   │
                        ┌──────────▼───────────┐
                        │ CategoryFloor(mid_cd) │
                        │ LargeCdFloor(large_cd)│
                        └──────────┬───────────┘
                                   │
                              최종 order_list
```

## 4. CategoryFloor와의 이중 보충 방지

### 현재 CategoryFloor 동작 (category_demand_forecaster.py:86-94)
```python
current_sum = existing_by_mid.get(mid_cd, 0)
floor_qty = category_forecast * threshold  # 0.7
if current_sum >= floor_qty:
    continue  # 충분하면 보충 안 함
```

### CUT 보충 반영 효과
- CUT 보충이 `order_list`에 먼저 반영됨 → CategoryFloor가 `current_sum`에 CUT 보충분 포함
- CUT 보충으로 floor_qty에 도달하면 CategoryFloor 보충 불필요 → 자동 스킵
- CUT 보충이 부족하면(replacement_ratio=0.8) → 나머지를 CategoryFloor가 추가 보충 가능
- **결론**: 파이프라인 순서만으로 이중 보충 자동 방지

### CategoryFloor 후보 중복 방지
- CategoryFloor의 `_get_supplement_candidates()`는 `existing_items`(이미 order_list에 있는 item_cd) 제외
- CUT 보충으로 추가된 상품은 `existing_items`에 포함됨 → CategoryFloor에서 자동 제외

## 5. 예외 처리

| 상황 | 처리 |
|------|------|
| CUT 상품 predicted_sales=0 (판매 없는 CUT) | lost_demand에 미포함 → 보충 불필요 |
| 대체 후보 0건 (전부 CUT/SKIP) | 경고 로그, 보충 스킵 |
| 후보가 이미 order_list에 있음 | 기존 상품 final_order_qty 증가 |
| replacement_ratio=0 (비활성) | 보충 0건 |
| DB 연결 실패 | try/except, 원본 order_list 반환 |
| food_daily_cap 초과 우려 | CUT 보충은 food_daily_cap **이후** → 소량(80%) 추가이므로 cap 크게 미초과 |
| execute() 재호출 시 _cut_lost_items 오염 | execute() 시작 시 `self._cut_lost_items = []` 초기화로 방지 |

## 6. 로깅 설계

### 정상 로그
```
[CUT보충] mid=002: CUT 6건(수요합=3.12) → 후보 8건 → 보충 3건(2.50/3.12, 80%)
[CUT보충]   주)뉴치킨마요삼각2: +1 (score=0.82, daily=0.66, sell=5/7, stk=0)
[CUT보충]   빅삼)참치마요삼각1: +1 (score=0.75, daily=0.70, sell=4/7, stk=0)
[CUT보충]   주)3XL뉴매콤돈까스삼각1: +1 (score=0.68, daily=0.53, sell=4/7, stk=1)
[CUT보충] 총 보충: +3개 (001=0, 002=3, 003=0, 004=0, 005=0)
```

### 경고 로그
```
[CUT보충] mid=001: CUT 2건(수요합=1.50) → 후보 0건 (대체 불가)
```

### 비활성 로그
```
[CUT보충] 비활성 (cut_replacement.enabled=False)
```

## 7. 테스트 설계

### 파일: `tests/test_cut_replacement.py`

| # | 시나리오 | 검증 |
|---|---------|------|
| 1 | mid=002에서 3건 CUT(predicted_sales 합=2.4), 후보 5건 | 보충 2건 (2.4×0.8=1.92→2), source="cut_replacement" |
| 2 | mid=001에서 2건 CUT, 후보 0건 | 보충 0건, 경고 로그 출력 |
| 3 | CUT 상품 predicted_sales=0 (비판매 CUT) | lost_demand=0 → 보충 불필요 |
| 4 | 후보가 이미 order_list에 있음 | final_order_qty 증가, 새 항목 미추가 |
| 5 | 후보 재고+미입고 충분 (stock_ratio >=1) | score 낮아 후순위, 다른 후보 우선 |
| 6 | replacement_ratio=0 | 보충 0건 |
| 7 | CUT 0건 (정상 상황) | 보충 단계 스킵 |
| 8 | enabled=False | 원본 order_list 그대로 반환 |
| 9 | Floor 보충과 중복 미발생 | CUT 보충 후 CategoryFloor가 이중 추가 안 함 |
| 10 | 정규화 스코어 정확성 | daily_avg 스케일 차이 무관하게 0~1 범위 |
| 11 | 유통기한 1일 상품 effective_stock=0 | stock_ratio 계산 시 재고 무시 |
| 12 | max_add_per_item=2 제한 | 상품당 최대 2개 초과 방지 |
| 13 | execute() 2회 연속 호출 | _cut_lost_items 초기화로 2차 실행에 1차 CUT 잔류 없음 |
| 14 | _refilter_cut_items() 두 호출 위치 | 두 곳 모두 동일 헬퍼 사용, 결과 일치 |

### 테스트 구조

```python
class TestCutReplacementService:
    """CUT 대체 보충 단위 테스트"""

    @pytest.fixture
    def service(self):
        return CutReplacementService(store_id="46513")

    @pytest.fixture
    def mock_order_list(self):
        """mid=002 기존 발주 9건"""
        return [
            {"item_cd": f"item_{i}", "mid_cd": "002",
             "final_order_qty": 1, "predicted_sales": 0.8}
            for i in range(9)
        ]

    @pytest.fixture
    def cut_lost_items(self):
        """CUT 탈락 6건 (predicted_sales 합계 3.12)"""
        return [
            {"item_cd": f"cut_{i}", "mid_cd": "002",
             "predicted_sales": 0.52, "item_nm": f"CUT상품{i}"}
            for i in range(6)
        ]

    def test_basic_replacement(self, service, mock_order_list, cut_lost_items):
        """시나리오 1: 정상 CUT 보충"""
        # DB mock: 후보 5건 반환
        result = service.supplement_cut_shortage(
            order_list=mock_order_list,
            cut_lost_items=cut_lost_items,
        )
        added = [i for i in result if i.get("source") == "cut_replacement"]
        assert len(added) >= 1
        total_added = sum(i["final_order_qty"] for i in added)
        assert total_added <= 3.12 * 0.8 + 0.5  # replacement_ratio 범위 내

    def test_no_candidates(self, service, mock_order_list, cut_lost_items):
        """시나리오 2: 후보 없음"""
        # DB mock: 후보 0건
        result = service.supplement_cut_shortage(
            order_list=mock_order_list,
            cut_lost_items=cut_lost_items,
        )
        assert len(result) == len(mock_order_list)  # 변화 없음
```

## 8. 구현 순서

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | `prediction_config.py`에 `cut_replacement` 설정 추가 | prediction_config.py |
| 2 | `cut_replacement.py` 신규 생성 — `CutReplacementService.supplement_cut_shortage()` 구현 | src/order/cut_replacement.py |
| 3 | `auto_order.py` execute() 시작에 `self._cut_lost_items = []` 추가 | auto_order.py |
| 4 | `auto_order.py` `__getattr__` defaults에 `_cut_lost_items` 추가 | auto_order.py |
| 5 | `auto_order.py` `_refilter_cut_items()` 헬퍼 메서드 추가 | auto_order.py |
| 6 | `auto_order.py` CUT 재필터 2곳을 헬퍼 호출로 교체 (cut_lost_items 캡처) | auto_order.py |
| 7 | `auto_order.py` food_daily_cap 이후에 보충 호출 삽입 | auto_order.py |
| 8 | 테스트 14개 시나리오 작성 | tests/test_cut_replacement.py |
| 9 | 기존 테스트 전체 통과 확인 | 전체 |

## 9. 설정 토글

```python
# prediction_config.py PREDICTION_PARAMS 내
# 참고: 012(빵)은 유통기한 3일로 CUT 리스크 낮아 기본 미포함. 필요 시 target_mid_cds에 "012" 추가.
"cut_replacement": {
    "enabled": True,                                    # 전체 토글
    "target_mid_cds": ["001", "002", "003", "004", "005"],  # 대상 mid_cd
    "replacement_ratio": 0.8,                           # 손실 수요의 80%
    "max_add_per_item": 2,                              # 상품당 최대 +2개
    "max_candidates": 5,                                # mid_cd당 최대 후보
    "min_sell_days": 1,                                 # 최근 7일 중 최소 판매일
},
```

비활성화 시: `"enabled": False` → 기존 파이프라인과 완전 동일 동작.
