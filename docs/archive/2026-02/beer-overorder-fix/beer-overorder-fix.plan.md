# 맥주 과발주 수정 (beer-overorder-fix)

## Context

46513 매장 자동발주(2026-02-25 07:00) 후 맥주(049) 카테고리가 대부분 과발주.
샘플: 4901777153325 (산토리 맥주캔 500ml) — 재고 19개인데 추가 4개(1배수) 발주.

### 근본 원인 분석

| 원인 | 위치 | 현재 동작 | 문제 |
|------|------|-----------|------|
| **stale RI 재고 폴백 로직 버그** | improved_predictor.py:1433-1448 | ri=0, ds=19일 때 `ds < ri` (19 < 0) → False → ri=0 채택 | 0은 어떤 값보다 작으므로 ds가 항상 탈락. 재고 19개를 0으로 인식 |
| **prefetch 한도 초과** | auto_order.py:1064, 1193-1198 | max_pending_items=200, 후보 449개 | 200개 이후 상품은 BGF 실시간 재고 미조회 → stale RI 값 그대로 사용 |
| **demand_pattern 불일치** | product_details DB | demand_pattern='frequent' | 실제 sell_day_ratio=10% (60일 중 6일 판매) → 'slow'여야 함 |

### 영향 범위

- **직접 영향**: 맥주(049) 전체 — stale RI가 0인 상품은 모두 동일 패턴
- **간접 영향**: 049 외 모든 카테고리에서 stale RI=0인 상품 전부 (근본 원인 #1은 카테고리 무관)
- **실측 데이터**: prediction_logs에서 맥주 다수가 stock=19~24인데 order=1~2로 기록

### 재현 과정 (4901777153325)

```
1. realtime_inventory: stock_qty=0, queried_at=2026-02-23T08:02:46 (2일 전 stale)
2. _resolve_stock_and_pending():
   - inv_data['_stale'] = True (TTL 초과)
   - ri_stock = 0, ds_stock = 19
   - ds_stock(19) < ri_stock(0) → False
   - current_stock = ri_stock = 0  ← 버그! 실재고 19개를 0으로
3. beer safety_stock = 3.67 * 2 = 7.33
4. need_qty = 0 + 0 + 7.33 - 0 - 0 = 7.33
5. DiffFeedback 감량 (penalty=0.5): 7.33 * 0.5 = 3.67
6. order_qty = ceil(3.67) = 4 → order_unit_qty=4로 맞춤 → 4개 발주
7. 실제: 재고 19 >> safety 7.33이므로 발주 불필요 (need = -11.67)
```

## 구현 계획 (4 Step)

### Step 1: `_resolve_stock_and_pending()` stale RI 폴백 로직 수정
**파일**: `src/prediction/improved_predictor.py` (lines 1433-1448)

**현재 (버그)**:
```python
if ds_stock < ri_stock:
    current_stock = ds_stock      # ds가 더 작으면 ds
else:
    current_stock = ri_stock      # ri가 더 작으면 ri (ri=0이면 항상 여기)
```

**수정**:
```python
if ri_stock == 0 and ds_stock > 0:
    # stale RI가 0이고 ds에 재고가 있으면 → ds 채택 (유령 소멸 방지)
    current_stock = ds_stock
    stock_source = "ri_stale_ds_nonzero"
elif ds_stock < ri_stock:
    current_stock = ds_stock      # ds가 더 작으면 ds (보수적)
    stock_source = "ri_stale_ds"
else:
    current_stock = ri_stock
    stock_source = "ri_stale_ri"
```

**핵심 원리**: stale 상태에서 ri=0은 "재고 0"이 아니라 "조회 안 됨/만료됨"을 의미. ds에 양수 재고가 있으면 ds를 신뢰.

### Step 2: prefetch 한도 증가 + 카테고리 우선순위
**파일**: `src/order/auto_order.py`

**2a. max_pending_items 기본값 증가**:
- `execute()` 파라미터: `max_pending_items: int = 200` → `max_pending_items: int = 500`
- 이유: 449개 후보 중 200개만 조회 → 249개 누락. 500이면 전체 커버 가능

**2b. (선택) 카테고리 우선 정렬**:
- prefetch 전 candidate_items를 `need_qty` 내림차순 정렬
- 높은 발주량 상품 우선 조회 → 한도 초과 시에도 중요 상품은 조회됨

### Step 3: beer 카테고리 추가 안전장치
**파일**: `src/prediction/categories/beer.py`

**3a. analyze_beer_pattern에 max_stock 스킵 강화**:
- 현재: `current_stock + pending_qty >= max_stock`일 때만 skip
- 문제: current_stock이 0으로 잘못 전달되면 skip 안 됨
- 해결: Step 1에서 근본 수정하므로, beer.py는 로깅 강화만 추가

### Step 4: 테스트 작성
**파일**: `tests/test_beer_overorder_fix.py` (신규)

| 테스트 | 설명 |
|--------|------|
| stale_ri_zero_ds_positive | ri=0(stale), ds=19 → ds=19 채택 |
| stale_ri_positive_ds_lower | ri=15(stale), ds=10 → ds=10 채택 (기존 동작 유지) |
| stale_ri_positive_ds_higher | ri=10(stale), ds=15 → ri=10 채택 (기존 동작 유지) |
| stale_ri_zero_ds_zero | ri=0(stale), ds=0 → 0 채택 (진짜 재고 없음) |
| fresh_ri_zero | ri=0(fresh) → ri=0 그대로 (stale 아님) |
| beer_skip_with_correct_stock | 맥주: 재고 19, safety 7.33 → 발주 스킵 |
| prefetch_limit_default_500 | execute() 기본 max_pending_items=500 확인 |

## 수정 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `src/prediction/improved_predictor.py` | _resolve_stock_and_pending() stale RI=0 폴백 수정 |
| `src/order/auto_order.py` | max_pending_items 200→500 |
| `tests/test_beer_overorder_fix.py` | 신규 테스트 7+개 |

## 기대 효과

- **맥주 과발주 해소**: stale RI=0 → ds 재고 정확 반영 → 불필요한 발주 차단
- **전 카테고리 영향**: 049 외에도 stale RI=0 상품 전체에 동일 효과
- **prefetch 커버리지**: 200→500으로 거의 전체 후보 조회 가능
- **역호환성**: stale가 아닌 경우(fresh RI) 기존 동작 변경 없음

## 위험 요소

| 위험 | 대응 |
|------|------|
| ds_stock도 부정확할 수 있음 | ds는 최근 daily_sales 기록이므로 ri=0보다 항상 나음 |
| prefetch 500개 시 시간 증가 | 상품당 ~1.5초 → 750초(12.5분). 기존 300초(5분) 대비 +7.5분. 허용 범위 |
| stale RI=0이 진짜 재고 0인 경우 | ds_stock=0이면 어차피 0 채택 → 문제 없음 |
