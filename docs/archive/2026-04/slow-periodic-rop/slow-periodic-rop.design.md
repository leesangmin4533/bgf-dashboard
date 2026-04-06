# Design: SLOW 주기 판매 상품 ROP 유지 (slow-periodic-rop)

> 작성일: 2026-04-06
> Plan 참조: `docs/01-plan/features/slow-periodic-rop.plan.md`

---

## 1. 변경 파일

```
변경 (2개):
  src/prediction/improved_predictor.py   # ROP 스킵 조건에 60일 주기 판매 확인
  src/settings/constants.py              # SLOW_PERIODIC_MIN_SALES 상수
```

---

## 2. 상세 설계

### 2.1 constants.py

```python
SLOW_PERIODIC_MIN_SALES = 2  # 60일 내 N회 이상 판매 → 주기적 SLOW (ROP 유지)
```

### 2.2 improved_predictor.py — `_stage_rop()` 수정

현재 코드 (lines 1993-2012):

```python
if rop_enabled and sell_day_ratio < 0.3:
    if effective_stock_for_need == 0 and order_qty == 0 and pending_qty == 0:
        _has_recent_sales = False
        if data_days < DATA_MIN_DAYS_FOR_LARGE_UNIT and not _is_new_product:
            _recent = self._get_daily_sales_history(item_cd, days=30)
            _has_recent_sales = any(s.get("sale_qty", 0) > 0 for s in (_recent or []))

        if (data_days < DATA_MIN_DAYS_FOR_LARGE_UNIT
                and not _is_new_product
                and not _has_recent_sales):
            # ROP 스킵
        else:
            order_qty = 1  # ROP 발동
```

변경:

```python
if rop_enabled and sell_day_ratio < 0.3:
    if effective_stock_for_need == 0 and order_qty == 0 and pending_qty == 0:
        _has_recent_sales = False
        _has_periodic_sales = False

        if data_days < DATA_MIN_DAYS_FOR_LARGE_UNIT and not _is_new_product:
            _recent = self._get_daily_sales_history(item_cd, days=30)
            _has_recent_sales = any(s.get("sale_qty", 0) > 0 for s in (_recent or []))

            # 30일 내 판매 없어도 60일 내 주기적 판매 확인
            if not _has_recent_sales:
                _sales_60d = self._count_sales_days(item_cd, days=60)
                _has_periodic_sales = _sales_60d >= SLOW_PERIODIC_MIN_SALES

        if (data_days < DATA_MIN_DAYS_FOR_LARGE_UNIT
                and not _is_new_product
                and not _has_recent_sales
                and not _has_periodic_sales):   # ← 추가
            # ROP 스킵
            logger.info(f"[ROP Skip] ... → 60일 판매 {_sales_60d if '_sales_60d' in dir() else '?'}회, 주기 판매 아님")
        else:
            order_qty = 1
            _rop_reason = "periodic_slow" if _has_periodic_sales else "recent_sales"
            logger.info(f"[ROP] ... → ROP=1 ({_rop_reason})")
```

### 2.3 _count_sales_days 헬퍼

기존 `_get_daily_sales_history`가 30일 이력을 반환하므로, 60일 판매일수만 세는 경량 쿼리 추가:

```python
def _count_sales_days(self, item_cd: str, days: int = 60) -> int:
    """N일 내 판매일수 (sale_qty > 0인 날 수)"""
    conn = self._get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM daily_sales "
            "WHERE item_cd = ? AND sales_date >= date('now', ?) AND sale_qty > 0",
            (item_cd, f"-{days} days")
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()
```

---

## 3. 테스트 설계

```python
# 주기적 SLOW: 60일 내 3회 판매, stock=0 → ROP=1
def test_rop_periodic_slow_triggers():

# 사장상품: 60일 내 0회 판매, stock=0 → ROP 스킵
def test_rop_dead_stock_skips():

# 경계: 60일 내 1회 판매 → ROP 스킵 (MIN_SALES=2 미달)
def test_rop_single_sale_skips():

# 30일 내 판매 있으면 기존 로직대로 ROP=1
def test_rop_recent_sales_still_works():
```

---

## 4. 하위호환

| 변경 | 영향 |
|------|------|
| ROP 스킵 조건 추가 | 기존 스킵 대상 중 "60일 2회+ 판매"만 ROP=1로 전환. 나머지 동일 |
| 새 상수 | 기존 상수 변경 없음 |
| _count_sales_days | 신규 메서드, 기존 코드 영향 없음 |
