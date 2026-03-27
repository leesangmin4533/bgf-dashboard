# Plan: CostOptimizer 활성화

## 1. 목적
- CostOptimizer가 코드 완성 상태이나 DB 경로 버그로 비활성 (`self._cost_optimizer = None`)
- 레거시 bgf_sales.db 직접 참조 → 매장 분할 DB 구조에서 products 테이블 접근 불가
- 버그 수정 후 활성화하여 마진×회전율 기반 발주 최적화 적용

## 2. 현재 문제점

### 버그 A: 레거시 DB 경로 (line 23)
```python
DB_PATH = Path(__file__).parent.parent.parent / "data" / "bgf_sales.db"
```
- bgf_sales.db는 레거시 단일 DB (v40 이후 폐기 검토)
- 현재: common.db(products) + stores/{id}.db(daily_sales) 분할 구조
- `_calculate_category_shares()`에서 daily_sales JOIN products 시 테이블 미발견

### 버그 B: SQL 쿼리 — 분할 DB 미대응 (line 277-289)
- daily_sales는 stores/{id}.db, products는 common.db → JOIN 불가능
- ATTACH DATABASE 미사용
- store_filter 사용하지만 매장 분할 DB에서는 store_id 컬럼 불필요 (파일 자체가 분리)

### 사용처 비활성
- `improved_predictor.py` line 246: `self._cost_optimizer = None` (명시적 비활성)
- `pre_order_evaluator.py` line 94: `CostOptimizer(store_id=...)` (정상 초기화)

## 3. 수정 계획

### 3-1. DB 경로 수정 (cost_optimizer.py)
- `DB_PATH` 제거 → `DBRouter.get_store_connection_with_common(store_id)` 사용
- `_calculate_category_shares()`: store_id별 매장 DB에서 ATTACH common 후 JOIN

### 3-2. SQL 쿼리 수정 (cost_optimizer.py)
- `store_filter()` 제거 (매장 DB 파일 자체가 분리이므로 불필요)
- `JOIN products p` → `JOIN common.products p` (ATTACH 후 접두사 사용)
- 또는 `attach_common_with_views()` 사용 시 접두사 없이 접근 가능

### 3-3. improved_predictor.py 활성화
- `self._cost_optimizer = None` → `CostOptimizer(store_id=self.store_id)`
- 주석 수정

### 3-4. 테스트 작성
- CostOptimizer DB 경로 테스트
- 판매비중 계산 테스트 (분할 DB)
- 2D 매트릭스 조회 테스트
- improved_predictor 통합 테스트

## 4. 영향 범위
- `src/prediction/cost_optimizer.py` — 버그 수정
- `src/prediction/improved_predictor.py` — 활성화
- `tests/test_cost_optimizer_activation.py` — 신규 테스트

## 5. 리스크
- CostOptimizer 활성화 후 발주량 변동 가능 (마진 높은 상품 ↑, 낮은 상품 ↓)
- 기존 테스트 2169개에 영향 없음 (CostOptimizer mock 또는 None 처리)
