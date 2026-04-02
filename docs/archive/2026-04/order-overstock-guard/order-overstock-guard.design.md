# Design: 안전재고 기반 과잉발주 방지

> Plan: `docs/01-plan/features/order-overstock-guard.plan.md`

## 1. 원인 체인 (확정)

```
beer.py:analyze_beer_pattern()
  └─ DB 직접 조회: 30일간 SUM(sale_qty)/COUNT(DISTINCT sales_date)
  └─ daily_avg = 37/8 ≈ 4.6 (3/7에 18개 대량판매 이상치 포함)
  └─ safety_days = 3 (금요일 → 주말 대비)
  └─ safety_stock = 4.6 × 3 ≈ ... → 실제 로그 24.7

improved_predictor.py:1344-1357 (beer 분기)
  └─ daily_avg = adjusted_prediction = WMA(0.0) × weekday_coef(1.37) = 0.0
  └─ BUT safety_stock = beer.py 반환값 = 24.7 (beer.py 내부 daily_avg 사용)

improved_predictor.py:1716-1718 (need 계산)
  └─ need = 0.0(adj) + 0.0(lead) + 24.7(safe) - 19(stk) - 0(pnd) = 5.7

improved_predictor.py:3185-3189 (friday_boost)
  └─ 5.7 × 1.15 = 6.6 → rules_friday_boost(0→7)
  └─ 가드 없음: WMA=0 체크 안 함

improved_predictor.py:3200 (overstock_prevention)
  └─ daily_avg = 0.0 → if daily_avg > 0: → 전체 스킵!
  └─ stock=19인데 과잉재고 감지 실패

→ ml_ensemble(7→8) → round_floor(8→6) = 6개 발주
```

**핵심 불일치**: `safety_stock`은 beer.py의 DB daily_avg(≈8.23)로 계산되지만, `overstock_prevention`과 `friday_boost`는 `pipe["daily_avg"]`(= WMA × weekday_coef = 0.0)을 사용. 두 daily_avg가 서로 다른 값을 참조.

---

## 2. 수정 설계

### 2.1 수정 A: friday_boost WMA 가드

**파일**: `src/prediction/improved_predictor.py`
**위치**: line 3185-3189

```python
# === Before ===
if rules["friday_boost"]["enabled"]:
    if weekday == 4 and product["mid_cd"] in rules["friday_boost"]["categories"]:
        order_qty *= rules["friday_boost"]["boost_rate"]
        _applied_stage = "rules_friday_boost"
        _applied_reason = f"boost={rules['friday_boost']['boost_rate']}"

# === After ===
if rules["friday_boost"]["enabled"]:
    if weekday == 4 and product["mid_cd"] in rules["friday_boost"]["categories"]:
        if daily_avg > 0:  # WMA 기반 수요 없으면 부스트 불필요
            order_qty *= rules["friday_boost"]["boost_rate"]
            _applied_stage = "rules_friday_boost"
            _applied_reason = f"boost={rules['friday_boost']['boost_rate']}"
```

**변경량**: 1줄 추가 (if 조건)
**영향**: WMA=0인 상품만 부스트 제거. WMA>0인 정상 상품은 기존 동일.

---

### 2.2 수정 D: overstock_prevention WMA=0 가드

**파일**: `src/prediction/improved_predictor.py`
**위치**: line 3198-3209

```python
# === Before ===
if rules["overstock_prevention"]["enabled"]:
    if daily_avg > 0:
        effective_stock = current_stock + pending_qty
        stock_days = effective_stock / daily_avg
        if stock_days >= rules["overstock_prevention"]["stock_days_threshold"]:
            if rules["overstock_prevention"]["skip_order"]:
                return RuleResult(
                    qty=0, delta=-need_qty,
                    reason=f"stock_days={stock_days:.1f}",
                    stage="rules_overstock",
                )

# === After ===
if rules["overstock_prevention"]["enabled"]:
    if daily_avg > 0:
        effective_stock = current_stock + pending_qty
        stock_days = effective_stock / daily_avg
        if stock_days >= rules["overstock_prevention"]["stock_days_threshold"]:
            if rules["overstock_prevention"]["skip_order"]:
                return RuleResult(
                    qty=0, delta=-need_qty,
                    reason=f"stock_days={stock_days:.1f}",
                    stage="rules_overstock",
                )
    elif current_stock + pending_qty > 0 and need_qty > 0:
        # WMA=0(수요 없음) + 재고 있음 → safety_stock만으로 발주 불필요
        return RuleResult(
            qty=0, delta=-need_qty,
            reason=f"zero_demand, stock={current_stock}+pnd={pending_qty}",
            stage="rules_overstock_zero_demand",
        )
```

**변경량**: 6줄 추가 (elif 분기)
**영향**: WMA=0 + 재고>0 → 무조건 발주 스킵. 재고=0+미입고=0이면 여전히 통과(안전재고 기반 최소 발주 허용).

**주의**: 간헐적(INTERMITTENT) 패턴은 Croston/TSB 모델 경로를 탐. WMA 기반 파이프라인에서 WMA=0인 상품은 실제로 수요가 없는 상품이므로 이 가드는 안전.

---

### 2.3 수정 C: beer.py daily_avg 이상치 제거 (Winsorize)

**파일**: `src/prediction/categories/beer.py`
**위치**: line 170-198

IQR 대신 **percentile cap (Winsorize)** 방식 채택 — numpy 의존 없이 구현 가능.

```python
# === Before ===
data_days = row[0] or 0
total_sales = row[1] or 0
daily_avg = (total_sales / data_days) if data_days > 0 else 0.0

# === After ===
data_days = row[0] or 0
total_sales = row[1] or 0

# 이상치 제거: 일별 판매량 조회 후 상위 5% cap
if data_days >= 5 and total_sales > 0:
    if store_id:
        cursor.execute("""
            SELECT sale_qty FROM daily_sales
            WHERE item_cd = ? AND store_id = ?
            AND sales_date >= date('now', '-' || ? || ' days')
            AND sale_qty > 0
            ORDER BY sale_qty
        """, (item_cd, store_id, config["analysis_days"]))
    else:
        cursor.execute("""
            SELECT sale_qty FROM daily_sales
            WHERE item_cd = ?
            AND sales_date >= date('now', '-' || ? || ' days')
            AND sale_qty > 0
            ORDER BY sale_qty
        """, (item_cd, config["analysis_days"]))

    daily_sales = [r[0] for r in cursor.fetchall()]
    if len(daily_sales) >= 5:
        # 상위 5% percentile → cap (대량 일괄판매 방지)
        cap_idx = max(0, int(len(daily_sales) * 0.95) - 1)
        cap_value = sorted(daily_sales)[cap_idx]
        capped_sales = [min(s, cap_value) for s in daily_sales]
        capped_total = sum(capped_sales)
        daily_avg = capped_total / data_days  # 전체 일수로 나눠 일평균 유지
        if capped_total != total_sales:
            logger.info(
                f"[Beer] {item_cd} 이상치 cap: "
                f"원본합={total_sales} → cap후={capped_total} "
                f"(cap={cap_value}, 판매일={len(daily_sales)}일)"
            )
    else:
        daily_avg = (total_sales / data_days) if data_days > 0 else 0.0
else:
    daily_avg = (total_sales / data_days) if data_days > 0 else 0.0
```

**산토리나마비어 검증**:
- 판매량 정렬: [1, 2, 2, 4, 4, 4, 18] (7일)
- cap_idx = int(7 × 0.95) - 1 = 5
- sorted[5] = 4 → cap=4
- capped: [1, 2, 2, 4, 4, 4, 4] → sum=21
- daily_avg = 21/8 = 2.63 (data_days=8, 판매0일 포함)
- safety_stock = 2.63 × 3 = 7.9
- need = 0 + 7.9 - 19 = **-11.1 → 발주 0** (수정 A/D 없이도 해결)

**변경량**: ~25줄
**영향**: 맥주 카테고리 전체의 daily_avg 계산 변경. 이상치 없는 상품은 동일값.

---

## 3. 구현 순서

```
Step 1: improved_predictor.py — friday_boost 가드 (수정 A)
Step 2: improved_predictor.py — overstock_prevention 가드 (수정 D)
Step 3: beer.py — daily_avg 이상치 제거 (수정 C)
Step 4: 테스트 실행
Step 5: 드라이런 검증
```

---

## 4. 테스트 설계

### 4.1 단위 테스트 (신규)

**파일**: `tests/prediction/test_order_overstock_guard.py`

```python
class TestFridayBoostGuard:
    """수정 A: friday_boost WMA 가드"""

    def test_skip_boost_when_daily_avg_zero(self):
        """WMA=0 → friday_boost 미적용"""
        # weekday=4(금), mid_cd=049(맥주), daily_avg=0
        result = predictor._apply_order_rules(
            need_qty=5.7, product={"mid_cd": "049"}, weekday=4,
            current_stock=19, daily_avg=0.0, pending_qty=0
        )
        assert result.stage != "rules_friday_boost"

    def test_apply_boost_when_daily_avg_positive(self):
        """WMA>0 → friday_boost 정상 적용"""
        result = predictor._apply_order_rules(
            need_qty=5.0, product={"mid_cd": "049"}, weekday=4,
            current_stock=5, daily_avg=3.0, pending_qty=0
        )
        assert result.stage == "rules_friday_boost"


class TestOverstockZeroDemand:
    """수정 D: overstock WMA=0 가드"""

    def test_skip_order_when_zero_demand_with_stock(self):
        """WMA=0 + 재고>0 → 발주 스킵"""
        result = predictor._apply_order_rules(
            need_qty=5.7, product={"mid_cd": "049"}, weekday=4,
            current_stock=19, daily_avg=0.0, pending_qty=0
        )
        assert result.qty == 0
        assert "zero_demand" in result.reason

    def test_allow_order_when_zero_demand_zero_stock(self):
        """WMA=0 + 재고=0 + 미입고=0 → 통과 (안전재고 최소 발주 허용)"""
        result = predictor._apply_order_rules(
            need_qty=3.0, product={"mid_cd": "049"}, weekday=2,
            current_stock=0, daily_avg=0.0, pending_qty=0
        )
        assert result.qty > 0 or result.stage != "rules_overstock_zero_demand"


class TestBeerDailyAvgOutlier:
    """수정 C: beer daily_avg 이상치 cap"""

    def test_cap_removes_spike(self):
        """대량 판매일(18개) cap 후 daily_avg 감소"""
        # 7일 판매: [1, 2, 2, 4, 4, 4, 18], data_days=8
        # cap=4 → capped sum=21 → daily_avg=21/8=2.625
        pattern = analyze_beer_pattern("8801021235240", store_id="46513")
        assert pattern.daily_avg < 5.0  # 이상치 제거 후 5 미만

    def test_no_cap_when_normal_sales(self):
        """이상치 없는 정상 판매 → 변화 없음"""
        # 균일 판매: [2, 3, 2, 3, 2, 3] → cap 적용해도 동일
```

### 4.2 기존 테스트 영향 확인

```bash
pytest tests/ -k "beer or friday or overstock or order_rules" -v
```

### 4.3 통합 드라이런

```bash
python run_scheduler.py --now --store 46513
# 확인: 8801021235240 order=0 (기존 6 → 0)
# 확인: 다른 맥주 상품 발주량 ±10% 이내
```

---

## 5. 파이프라인 영향 매핑

```
16단계 파이프라인에서 수정 영향:

 1. WMA예측         ← 변경 없음
 2. Feature블렌딩    ← 변경 없음
 3. 계수적용         ← 변경 없음
 4. 안전재고계산      ← 수정 C (beer daily_avg cap)
 5. Need계산         ← 수정 C 간접 영향 (safety_stock 감소)
 6. ★ OrderRules    ← 수정 A (friday_boost 가드) + 수정 D (overstock 가드)
 7. ROP             ← 변경 없음
 8. 프로모션         ← 변경 없음
 9. ML앙상블        ← 변경 없음 (입력 0이면 출력도 0)
10. DiffFeedback    ← 변경 없음
11. 폐기피드백       ← 변경 없음
12. Substitution    ← 변경 없음
13. CategoryFloor   ← 변경 없음
14. LargeFloor      ← 변경 없음
15. Cap             ← 변경 없음
16. Round           ← 변경 없음
```

---

## 6. 후행 덮어쓰기 체크

| 수정 | 이후 단계가 의도를 깰 수 있는가? | 결론 |
|------|-------------------------------|------|
| A: friday_boost 가드 | ML앙상블이 0을 양수로 올릴 수 있음 → BUT 수정 D가 먼저 0 반환하므로 안전 | OK |
| D: overstock 가드 | `return 0` → 이후 단계 진입 불가 | OK |
| C: beer daily_avg cap | need 감소 → 이후 단계에서 음수 need는 자동 0 처리 | OK |

---

## 7. 롤백 계획

모든 수정은 독립적이므로 개별 롤백 가능:
- A/D: `improved_predictor.py`의 if 조건만 제거
- C: `beer.py`의 이상치 로직만 제거 (원본 `daily_avg = total_sales / data_days` 복원)
