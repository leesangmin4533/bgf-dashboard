# Plan: 교차날짜 미입고 과대평가 보정 (cross-date-pending-fix)

## 문제 정의

### 현상
부분발주 실행 시 11개 상품 → 0개로 전량 제외됨. 원인은 **미입고(pending_qty) 과대평가**.

### 근본 원인: 교차날짜 패턴 (Cross-Date Pattern)

BGF의 `dsOrderSale` 데이터셋은 날짜별 행으로 발주/입고 이력을 제공한다.
현재 코드(`order_prep_collector.py:700-722`)는 **각 행의 차이만 합산**하여 미입고를 계산:

```
pending_qty = Σ max(0, (ord_qty × unit) - buy_qty)   # 행별 독립 계산
```

**문제 시나리오**: 발주일과 입고일이 다른 경우

| 날짜 | 발주(배수×입수) | 입고 | 행별 차이 |
|------|---------------|------|----------|
| 02-01 | 2×6 = 12 | 0 | **+12** (발주O 입고X) |
| 02-02 | 0 | 12 | 0 (발주X 입고O → 무시) |
| **합계** | 12 | 12 | **pending = 12** (실제는 0) |

02-01에 발주한 12개가 02-02에 입고되었지만, 행별 독립 계산이므로:
- 02-01 행: 12 - 0 = 12 (미입고로 잡힘)
- 02-02 행: 0 - 12 → max(0, -12) = 0 (입고 초과분 무시)
- **결과**: pending_qty = 12 (실제로는 전량 입고 완료)

### 영향 범위

- `collect_for_item()` → pending_qty 과대 → DB 저장
- `_apply_pending_and_stock_to_order_list()` → pending_diff 과대 → 발주량 과다 차감
- 특히 **리드타임 1일인 푸드류**(도시락/김밥/샌드위치 등)에서 빈번 발생

---

## 해결 방안

### 핵심 아이디어: 총량 기반 미입고 계산

행별 독립 계산 대신, **전체 기간의 발주 총량 - 입고 총량**으로 계산:

```
total_ordered = Σ (ord_qty × unit)   # 전체 기간 발주 합계
total_received = Σ buy_qty           # 전체 기간 입고 합계
pending_qty = max(0, total_ordered - total_received)
```

| 날짜 | 발주 | 입고 |
|------|------|------|
| 02-01 | 12 | 0 |
| 02-02 | 0 | 12 |
| **총량** | **12** | **12** |
| **pending** | **max(0, 12-12) = 0** | (정확!) |

### 추가 안전장치: 오늘 발주분 보호

오늘(당일) 발주한 것은 아직 입고 불가능하므로 별도 처리:

```
# 과거 행: 총량 기반
past_ordered = Σ (ord_qty × unit) where date < today
past_received = Σ buy_qty where date < today
past_pending = max(0, past_ordered - past_received)

# 오늘 행: 발주 있으면 아직 입고 전이므로 그대로 미입고
today_ordered = Σ (ord_qty × unit) where date == today
today_received = Σ buy_qty where date == today
today_pending = max(0, today_ordered - today_received)

# 최종
pending_qty = past_pending + today_pending
```

---

## 수정 대상

### 1. `src/collectors/order_prep_collector.py` — `collect_for_item()` (line 700-722)

**변경 내용**: 행별 독립 계산 → 총량 기반 계산으로 교체

```python
# 현재 (문제)
pending_qty = 0
for h in history:
    ordered_actual = ord_qty * order_unit_qty
    diff = max(0, ordered_actual - buy_qty)
    pending_qty += diff  # 행별 합산 → 교차날짜 시 과대

# 변경 후 (보정)
today_str = datetime.now().strftime("%Y%m%d")

past_ordered_total = 0
past_received_total = 0
today_ordered_total = 0
today_received_total = 0

for h in history:
    ordered_actual = h.get('ord_qty', 0) * order_unit_qty
    buy_qty = h.get('buy_qty', 0)
    h_date = h.get('date', '')

    if h_date >= today_str:
        today_ordered_total += ordered_actual
        today_received_total += buy_qty
    else:
        past_ordered_total += ordered_actual
        past_received_total += buy_qty

past_pending = max(0, past_ordered_total - past_received_total)
today_pending = max(0, today_ordered_total - today_received_total)
pending_qty = past_pending + today_pending
```

**디버그 로그**: `pending_detail` 리스트는 기존대로 유지 (행별 데이터 기록). 추가로 보정 전/후 비교 로그:

```python
# 기존 방식 값도 계산 (비교용)
naive_pending = sum(max(0, d['ordered_actual'] - d['buy_qty']) for d in pending_detail)
if naive_pending != pending_qty:
    logger.info(f"[교차날짜 보정] {item_cd}: {naive_pending}개 → {pending_qty}개 (차이: {naive_pending - pending_qty}개)")
```

### 2. `_write_pending_debug_log()` — 로그 포맷 보강

교차날짜 보정이 적용된 상품 표시 추가:
- pending_log_entries에 `naive_pending` (보정 전), `corrected_pending` (보정 후) 필드 추가
- 로그 출력 시 보정 전/후 비교 표시

---

## 변경하지 않는 것

| 항목 | 이유 |
|------|------|
| `_apply_pending_and_stock_to_order_list()` | pending_qty 자체가 정확해지면 조정 로직은 그대로 유효 |
| DB 스키마 | pending_qty 컬럼 그대로 사용, 값만 정확해짐 |
| `save()` 호출 | 정확한 pending_qty가 저장됨 |
| JS 데이터 조회 | dsOrderSale 원본 데이터는 변경 없음 |

---

## 변경 파일 요약

| # | 파일 | 변경 | 라인 |
|---|------|------|------|
| 1 | `src/collectors/order_prep_collector.py` | `collect_for_item()` 미입고 계산 로직 교체 | 700-722 |
| 2 | `src/collectors/order_prep_collector.py` | `_write_pending_debug_log()` 보정 비교 표시 | 960+ |

---

## 검증 방법

1. **부분발주 dry_run**: `python scripts/run_auto_order.py --categories 049,050` (dry_run 기본)
   - 이전: 11개 → 0개 (과대평가로 전량 제외)
   - 기대: 합리적인 수량으로 발주 진행

2. **디버그 로그 확인**: `data/logs/pending_debug_YYYY-MM-DD.txt`
   - `[교차날짜 보정]` 로그로 보정 전/후 비교
   - 교차날짜 패턴 상품에서 보정 효과 확인

3. **DB 검증**:
   ```sql
   SELECT item_cd, pending_qty, queried_at
   FROM realtime_inventory
   WHERE pending_qty > 0
   ORDER BY queried_at DESC LIMIT 20;
   ```
   - 보정 전 대비 pending_qty 감소 확인

---

## 위험 요소

| 위험 | 대응 |
|------|------|
| 실제 미입고인데 과소평가 | 오늘 발주분 별도 처리로 방지. 과거분은 총량 차이로 정확 계산 |
| history 데이터 기간이 매우 짧아 1행만 있는 경우 | 1행이면 총량=행별이므로 동일 결과. 변경 없음 |
| BGF가 이미 받은 물량을 history에서 제거하는 경우 | 그 경우 기존 방식도 동일 이슈. 총량 방식이 더 나음 |
