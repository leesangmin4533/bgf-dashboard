# beer-overorder-fix Design

## 참조
- Plan: `docs/01-plan/features/beer-overorder-fix.plan.md`

## 변경 사항 상세

### 1. `_resolve_stock_and_pending()` stale RI=0 폴백 수정

**파일**: `src/prediction/improved_predictor.py`
**위치**: lines 1433-1448 (stale 분기)

**Before** (현재 코드):
```python
if inv_data.get('_stale', False):
    is_stale = True
    ds_stock = self.get_current_stock(item_cd)
    ri_stock = inv_data['stock_qty']
    # 오래된 ri 값보다 daily_sales가 더 작으면(=더 현실적) ds 채택
    if ds_stock < ri_stock:
        logger.debug(
            f"[{item_cd}] 오래된 재고 데이터 감지 "
            f"(ri={ri_stock}, ds={ds_stock}, queried={inv_data.get('queried_at', '?')}) "
            f"-> daily_sales 값 사용"
        )
        current_stock = ds_stock
        stock_source = "ri_stale_ds"
    else:
        current_stock = ri_stock
        stock_source = "ri_stale_ri"
```

**After** (수정 코드):
```python
if inv_data.get('_stale', False):
    is_stale = True
    ds_stock = self.get_current_stock(item_cd)
    ri_stock = inv_data['stock_qty']

    if ri_stock == 0 and ds_stock > 0:
        # stale RI=0은 "조회 안 됨"을 의미 → ds에 양수 재고 있으면 ds 채택
        logger.debug(
            f"[{item_cd}] stale RI=0, ds={ds_stock} "
            f"(queried={inv_data.get('queried_at', '?')}) "
            f"-> daily_sales 값 사용 (RI 무효)"
        )
        current_stock = ds_stock
        stock_source = "ri_stale_ds_nonzero"
    elif ds_stock < ri_stock:
        # ds가 더 작으면 보수적으로 ds 채택 (기존 동작 유지)
        logger.debug(
            f"[{item_cd}] 오래된 재고 데이터 감지 "
            f"(ri={ri_stock}, ds={ds_stock}, queried={inv_data.get('queried_at', '?')}) "
            f"-> daily_sales 값 사용"
        )
        current_stock = ds_stock
        stock_source = "ri_stale_ds"
    else:
        current_stock = ri_stock
        stock_source = "ri_stale_ri"
```

**변경 핵심**: `ri_stock == 0 and ds_stock > 0` 조건을 최상위에 추가. stale 상태에서 ri=0은 "재고 없음"이 아니라 "만료된 데이터"이므로, ds에 양수가 있으면 무조건 ds를 채택.

**stock_source 값 의미**:
| 값 | 의미 |
|----|------|
| `ri_stale_ds_nonzero` | (신규) stale RI=0, ds > 0 → ds 채택 |
| `ri_stale_ds` | (기존) stale RI > ds → ds 채택 |
| `ri_stale_ri` | (기존) stale RI <= ds → ri 채택 |

### 2. prefetch 한도 증가

**파일**: `src/order/auto_order.py`
**위치**: `execute()` 시그니처 (line 1064)

**Before**:
```python
max_pending_items: int = 200,
```

**After**:
```python
max_pending_items: int = 500,
```

**이유**: 46513 매장 후보 449개 중 200개만 조회 → 249개 누락. 500이면 전체 커버.

**시간 영향**: +4~5분 (상품당 ~1.5초 × 추가 300개). 전체 플로우 45분 → 50분 수준.

### 3. 테스트 설계

**파일**: `tests/test_beer_overorder_fix.py` (신규)

```python
class TestStaleRIFallback:
    """_resolve_stock_and_pending() stale RI 폴백 테스트"""

    def test_stale_ri_zero_ds_positive(self):
        """stale RI=0, ds=19 → ds=19 채택 + source='ri_stale_ds_nonzero'"""

    def test_stale_ri_zero_ds_zero(self):
        """stale RI=0, ds=0 → 0 채택 (진짜 재고 없음)"""

    def test_stale_ri_positive_ds_lower(self):
        """stale RI=15, ds=10 → ds=10 채택 (기존 동작)"""

    def test_stale_ri_positive_ds_higher(self):
        """stale RI=10, ds=15 → ri=10 채택 (기존 동작)"""

    def test_fresh_ri_not_affected(self):
        """fresh RI → stale 분기 안 탐 (기존 동작)"""

class TestBeerWithCorrectStock:
    """맥주 과발주 시나리오 e2e"""

    def test_beer_skip_when_stock_exceeds_safety(self):
        """재고 19 > safety 7.33 → need_qty < 0 → order=0"""

    def test_beer_order_when_stock_is_low(self):
        """재고 2 < safety 7.33 → need_qty > 0 → 정상 발주"""

class TestPrefetchLimit:
    """prefetch 한도 확인"""

    def test_default_max_pending_items_500(self):
        """execute() 기본 max_pending_items=500"""
```

## 구현 순서

1. `improved_predictor.py` — stale RI=0 폴백 수정 (핵심)
2. `auto_order.py` — max_pending_items 200→500
3. `tests/test_beer_overorder_fix.py` — 테스트 작성
4. 전체 테스트 실행 (`pytest`)

## 영향 범위 확인

| 컴포넌트 | 영향 |
|----------|------|
| improved_predictor._resolve_stock_and_pending() | **직접 수정** |
| auto_order.execute() | **파라미터 변경** |
| prediction_logs.stock_source | 신규 값 `ri_stale_ds_nonzero` 추가 |
| beer.py | 변경 없음 (stock 값이 정확해지면 자동 해소) |
| 기존 테스트 | 변경 없음 (fresh RI 동작 불변) |
