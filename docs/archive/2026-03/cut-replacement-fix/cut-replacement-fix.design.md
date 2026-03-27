# Design: cut-replacement-fix

## 1. 현재 코드 구조 (AS-IS)

### pre_order_evaluator.py `evaluate_all()` (lines 1130-1190)

```
1130  # CUT 상품 제외
1131  results = {}
1133  cut_conn = self._get_conn()          # ← 커넥션 열기
1138    cut_items = {is_cut_item=1}         # ← CUT 목록 조회
1160  cut_conn.close()                      # ← 커넥션 닫기

1163  if cut_items:
1164    cut_in_candidates = [...]
1166    for cd in cut_in_candidates:
1167      results[cd] = PreOrderEvalResult(
1168        item_cd=cd, item_nm="", mid_cd="",   # ★ 빈 값
1173        daily_avg=0, trend_score=0,           # ★ 수요 0
1174      )
1175    item_codes = [filter out cut]

1181  conn = self._get_conn()               # ← 새 커넥션
1183    all_daily_avgs = ...                 # ← 여기서야 daily_avg 로딩
1184    products_map = ...                   # ← 여기서야 mid_cd 로딩
```

**문제**: CUT SKIP 결과에 `mid_cd=""`, `daily_avg=0` → 수요 정보 완전 유실

### auto_order.py CUT 보충 호출 (lines 953-973)

```
957  if cut_replacement_cfg.get("enabled") and self._cut_lost_items:
       # _cut_lost_items는 _refilter_cut_items()에서만 채워짐
       # _refilter_cut_items()는 order_list에서 CUT 찾기
       # → 예측 단계에서 이미 제거된 CUT은 order_list에 없음
       # → _cut_lost_items = [] → 조건 False → 미호출
```

## 2. 수정 설계 (TO-BE)

### Fix A: pre_order_evaluator.py — CUT 수요 데이터 보존

**위치**: `evaluate_all()` lines 1133-1176

**변경 전략**: `cut_conn` 닫기 전에 CUT 상품의 daily_avg와 mid_cd를 조회

```python
# === 현재 (line 1133~1161) ===
cut_conn = self._get_conn()
try:
    cut_cursor = cut_conn.cursor()
    # ... cut_items 조회 ...
    # ... stale_cut_suspects 조회 ...
finally:
    cut_conn.close()

# === 수정 후 ===
cut_conn = self._get_conn()
try:
    cut_cursor = cut_conn.cursor()
    # ... cut_items 조회 (기존 그대로) ...
    # ... stale_cut_suspects 조회 (기존 그대로) ...

    # ★ 신규: CUT 후보의 수요 데이터 배치 조회
    cut_in_candidates = [cd for cd in item_codes if cd in cut_items]
    cut_demand_map = {}  # {item_cd: {"mid_cd": str, "daily_avg": float, "item_nm": str}}
    if cut_in_candidates:
        days = int(self.config.daily_avg_days.value)
        store_filter = "AND ds.store_id = ?" if self.store_id else ""
        store_params = [self.store_id] if self.store_id else []
        for chunk in self._chunked(cut_in_candidates):
            placeholders = ",".join("?" * len(chunk))
            # daily_avg 조회 (daily_sales)
            cut_cursor.execute(f"""
                SELECT ds.item_cd,
                       CAST(SUM(ds.sale_qty) AS REAL) / ? as daily_avg
                FROM daily_sales ds
                WHERE ds.item_cd IN ({placeholders})
                  AND ds.sales_date >= date('now', '-' || ? || ' days')
                  {store_filter}
                GROUP BY ds.item_cd
            """, [days] + list(chunk) + [days] + store_params)
            for row in cut_cursor.fetchall():
                cut_demand_map[row[0]] = {"daily_avg": row[1] or 0}

            # mid_cd, item_nm 조회 (products — ATTACH 경유)
            cut_cursor.execute(f"""
                SELECT item_cd, item_nm, mid_cd FROM products
                WHERE item_cd IN ({placeholders})
            """, chunk)
            for row in cut_cursor.fetchall():
                entry = cut_demand_map.get(row[0], {"daily_avg": 0})
                entry["mid_cd"] = row[1] if len(row) > 2 else ""
                entry["item_nm"] = row[1] if len(row) > 1 else ""
                # 수정: row 인덱스 정확히
                entry["mid_cd"] = row[2] if row[2] else ""
                entry["item_nm"] = row[1] if row[1] else ""
                cut_demand_map[row[0]] = entry
finally:
    cut_conn.close()
```

**CUT SKIP 결과 생성 수정** (line 1163-1176):

```python
# === 현재 ===
results[cd] = PreOrderEvalResult(
    item_cd=cd, item_nm="", mid_cd="",
    decision=EvalDecision.SKIP,
    reason="CUT(발주중지) 상품",
    exposure_days=0, stockout_frequency=0,
    popularity_score=0, current_stock=0,
    pending_qty=0, daily_avg=0, trend_score=0,
)

# === 수정 후 ===
demand_info = cut_demand_map.get(cd, {})
results[cd] = PreOrderEvalResult(
    item_cd=cd,
    item_nm=demand_info.get("item_nm", ""),
    mid_cd=demand_info.get("mid_cd", ""),
    decision=EvalDecision.SKIP,
    reason="CUT(발주중지) 상품",
    exposure_days=0, stockout_frequency=0,
    popularity_score=0, current_stock=0,
    pending_qty=0,
    daily_avg=demand_info.get("daily_avg", 0),
    trend_score=0,
)
```

### Fix B: auto_order.py — eval_results에서 CUT 수요 복원

**위치**: CUT 보충 호출 직전 (line 953 부근)

**변경**: eval_results의 CUT SKIP 항목을 `_cut_lost_items`에 추가

```python
# ★ CUT 대체 보충 (발주중지 상품 수요를 동일 mid_cd 대체 상품에 배분)
try:
    from src.prediction.prediction_config import PREDICTION_PARAMS
    cut_replacement_cfg = PREDICTION_PARAMS.get("cut_replacement", {})
    if cut_replacement_cfg.get("enabled", False):
        # ★ 신규: eval_results에서 CUT SKIP 수요 데이터 복원
        if eval_results:
            from src.prediction.pre_order_evaluator import EvalDecision
            existing_cut_cds = {item.get("item_cd") for item in self._cut_lost_items}
            for cd, r in eval_results.items():
                if (r.decision == EvalDecision.SKIP
                        and "CUT" in r.reason
                        and r.mid_cd in FOOD_CATEGORIES
                        and r.daily_avg > 0
                        and cd not in existing_cut_cds):
                    self._cut_lost_items.append({
                        "item_cd": cd,
                        "mid_cd": r.mid_cd,
                        "predicted_sales": r.daily_avg,
                        "item_nm": r.item_nm,
                    })

        if self._cut_lost_items:
            from src.order.cut_replacement import CutReplacementService
            # ... 기존 보충 로직 그대로 ...
```

## 3. 수정 파일 목록

| # | 파일 | 변경 | 라인 |
|---|------|------|------|
| 1 | `src/prediction/pre_order_evaluator.py` | CUT 수요 배치 조회 추가 | 1133-1161 (try 블록 내) |
| 2 | `src/prediction/pre_order_evaluator.py` | PreOrderEvalResult 필드 채우기 | 1167-1174 |
| 3 | `src/order/auto_order.py` | eval_results → _cut_lost_items 변환 | 953-957 |

## 4. 테스트 설계

### 4.1 pre_order_evaluator 테스트

```python
# test_cut_skip_preserves_demand
def test_cut_skip_preserves_demand():
    """CUT SKIP 처리 시 daily_avg와 mid_cd가 보존되는지 검증"""
    # Setup: item_cd="CUT001" is_cut_item=1, daily_sales avg=2.5, mid_cd="001"
    evaluator = PreOrderEvaluator(store_id="46513")
    results = evaluator.evaluate_all(item_codes=["CUT001", "NORMAL001"])

    cut_result = results["CUT001"]
    assert cut_result.decision == EvalDecision.SKIP
    assert cut_result.daily_avg > 0        # ★ 핵심: 0이 아님
    assert cut_result.mid_cd == "001"      # ★ 핵심: 빈 문자열 아님
    assert cut_result.item_nm != ""

# test_cut_skip_no_sales_zero_avg
def test_cut_skip_no_sales_zero_avg():
    """판매 이력 없는 CUT 상품은 daily_avg=0"""
    # Setup: item_cd="CUT_NOSALE" is_cut_item=1, no daily_sales
    results = evaluator.evaluate_all(item_codes=["CUT_NOSALE"])
    assert results["CUT_NOSALE"].daily_avg == 0
```

### 4.2 auto_order 테스트

```python
# test_eval_results_cut_to_lost_items
def test_eval_results_cut_to_lost_items():
    """eval_results CUT SKIP → _cut_lost_items 변환 검증"""
    # eval_results에 CUT SKIP (mid_cd="001", daily_avg=2.5) 포함
    # → _cut_lost_items에 추가되어야 함
    assert len(system._cut_lost_items) == 1
    assert system._cut_lost_items[0]["predicted_sales"] == 2.5
    assert system._cut_lost_items[0]["mid_cd"] == "001"

# test_non_food_cut_excluded
def test_non_food_cut_excluded():
    """비푸드 CUT은 _cut_lost_items에 미포함"""
    # eval_results에 CUT SKIP (mid_cd="042", daily_avg=3.0) — 음료
    # → FOOD_CATEGORIES 아님 → 미포함
    assert len(system._cut_lost_items) == 0

# test_duplicate_prevention
def test_duplicate_prevention():
    """refilter + eval 양쪽에서 동일 item_cd 중복 방지"""
    # _refilter_cut_items에서 이미 추가된 item_cd
    # → eval_results에서 같은 cd 발견 시 스킵
    assert len(system._cut_lost_items) == 1  # 중복 없음

# test_zero_avg_cut_excluded
def test_zero_avg_cut_excluded():
    """daily_avg=0 CUT은 보충 대상 아님"""
    # eval_results에 CUT SKIP (daily_avg=0)
    assert len(system._cut_lost_items) == 0
```

### 4.3 통합 테스트

```python
# test_cut_replacement_actually_called
def test_cut_replacement_actually_called():
    """CUT 보충 서비스가 실제 호출되는 E2E 검증"""
    # Setup: 3 CUT items in mid_cd="001", 2 alternative items available
    # → CutReplacementService.supplement_cut_shortage() 호출됨
    # → 보충 수량 > 0

# test_existing_refilter_still_works
def test_existing_refilter_still_works():
    """기존 prefetch 감지 CUT도 여전히 포착"""
    # prefetch에서 실시간 CUT 감지 → _refilter_cut_items 동작 유지
```

## 5. 변경하지 않는 파일

| 파일 | 이유 |
|------|------|
| `cut_replacement.py` | 보충 로직 자체는 정상, 입력만 채워주면 됨 |
| `_refilter_cut_items()` | prefetch 실시간 감지 역할 유지 |
| `prediction_config.py` | `cut_replacement.enabled=True` 이미 설정됨 |
| `PreOrderEvalResult` dataclass | 기존 필드로 충분 (mid_cd, daily_avg, item_nm 이미 존재) |

## 6. 구현 순서

1. **Fix A**: `pre_order_evaluator.py` — CUT 수요 배치 조회 + PreOrderEvalResult 채우기
2. **Fix B**: `auto_order.py` — eval_results → _cut_lost_items 변환
3. **테스트**: 8개 테스트 작성 + 기존 회귀 테스트 확인
