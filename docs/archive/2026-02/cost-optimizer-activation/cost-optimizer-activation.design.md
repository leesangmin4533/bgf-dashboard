# Design: CostOptimizer 활성화

## 1. 수정 파일 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `src/prediction/cost_optimizer.py` | 수정 | DB 경로 제거, SQL 쿼리 수정 |
| `src/prediction/improved_predictor.py` | 수정 | CostOptimizer 활성화 (None→인스턴스) |
| `tests/test_cost_optimizer_activation.py` | 신규 | 15개 테스트 |

## 2. cost_optimizer.py 변경 상세

### 2-1. 임포트 변경
```python
# 제거
import sqlite3
from src.db.store_query import store_filter

# 추가
from src.infrastructure.database.connection import DBRouter, attach_common_with_views
```

### 2-2. 상수 제거
```python
# 제거
DB_PATH = Path(__file__).parent.parent.parent / "data" / "bgf_sales.db"
```

### 2-3. _calculate_category_shares() 리팩토링
```python
def _calculate_category_shares(self) -> Dict[str, float]:
    if self._category_share_cache is not None:
        return self._category_share_cache

    shares: Dict[str, float] = {}
    if not self.store_id:
        self._category_share_cache = shares
        return shares

    try:
        conn = DBRouter.get_store_connection_with_common(self.store_id)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.mid_cd, SUM(ds.sale_qty) as total_qty
                FROM daily_sales ds
                JOIN common.products p ON ds.item_cd = p.item_cd
                WHERE ds.sale_date >= date('now', '-30 days')
                GROUP BY p.mid_cd
            """)
            rows = cursor.fetchall()
            grand_total = sum(r["total_qty"] for r in rows) or 1
            shares = {r["mid_cd"]: r["total_qty"] / grand_total for r in rows}
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[비용최적화] 판매비중 계산 실패: {e}")

    self._category_share_cache = shares
    return shares
```

**핵심 변경점:**
- `sqlite3.connect(DB_PATH)` → `DBRouter.get_store_connection_with_common(store_id)`
- `store_filter()` 제거 (매장 DB 파일 자체가 분리, store_id 컬럼 필터 불필요)
- `JOIN products p` → `JOIN common.products p` (ATTACH된 common DB 접두사)
- `store_id` 없으면 빈 dict 반환 (에러 방지)

## 3. improved_predictor.py 변경 상세

### 3-1. 활성화
```python
# 변경 전 (line 245-246)
# 비용 최적화 모듈 (비활성화 - SQL 버그 및 레거시 DB 경로 이슈)
self._cost_optimizer = None

# 변경 후
# 비용 최적화 모듈 (마진×회전율 2D 매트릭스)
self._cost_optimizer = CostOptimizer(store_id=self.store_id)
```

## 4. 테스트 설계

### 4-1. CostOptimizer 단위 테스트 (10개)
| 테스트 | 검증 내용 |
|--------|----------|
| test_cost_info_disabled | enabled=False → 기본 CostInfo |
| test_cost_info_no_margin | margin_rate=None → enabled=False |
| test_cost_info_high_margin | margin_rate=50 → multiplier>1.0 |
| test_cost_info_low_margin | margin_rate=5 → multiplier<1.0 |
| test_2d_matrix_high_high | 고마진+고회전 → 최고 multiplier |
| test_2d_matrix_low_low | 저마진+저회전 → 최저 multiplier |
| test_turnover_unknown_fallback | daily_avg=0 → 1D 폴백 |
| test_category_shares_with_db | 분할 DB에서 판매비중 계산 |
| test_category_shares_no_store | store_id=None → 빈 dict |
| test_cache_works | 동일 item 2회 조회 → 캐시 히트 |

### 4-2. 통합 테스트 (5개)
| 테스트 | 검증 내용 |
|--------|----------|
| test_skip_offset_applied | PreOrderEvaluator에서 skip_offset 적용 |
| test_safety_stock_multiplied | improved_predictor에서 안전재고 계수 적용 |
| test_disuse_modifier_applied | improved_predictor에서 폐기계수 보정 |
| test_bulk_cost_info | 다수 상품 일괄 조회 |
| test_config_from_eval_params | eval_params.json에서 설정 로드 |

## 5. 데이터 흐름

```
eval_params.json → CostOptimizer._load_config()
                      ↓
product_details (common.db) → margin_rate, sell_price
                      ↓
daily_sales (store DB) + products (common DB) → category_shares
                      ↓
CostInfo {
  margin_multiplier  → improved_predictor: safety_stock *= composite_score
  disuse_modifier    → improved_predictor: food_disuse_coef *= modifier
  skip_offset        → pre_order_evaluator: th_sufficient += offset
}
```
