# Design: 발주-입고 불일치 감지 및 알림

## 1. 리드타임 계산 함수

```python
# src/alert/receiving_matcher.py (신규)

def calc_max_lead_days(orderable_day: str) -> int:
    """발주가능요일 기반 최대 입고 리드타임 계산

    발주일 → 다음 발주가능일까지 최대 간격 + 입고 1일(배송)

    Args:
        orderable_day: "일월화수목금토" 형태 문자열

    Returns:
        최대 리드타임 (일)

    예시:
        "일월화수목금토" → 2 (매일, 1~2일)
        "월화수목금토"   → 2 (주6일, 일요일 1일 간격)
        "화목토"        → 3 (화→목 2일 + 배송 1일)
        "월수금"        → 3 (금→월 2일 + 배송 1일)
        "수금"          → 4 (금→수 4일 + 배송 1일)
    """
    if not orderable_day:
        return 3  # 기본값

    days_kr = ['일','월','화','수','목','금','토']
    available = [i for i, d in enumerate(days_kr) if d in orderable_day]

    if not available:
        return 3

    if len(available) >= 7:
        return 2  # 매일

    # 최대 간격 계산 (순환)
    max_gap = 0
    for i in range(len(available)):
        next_i = (i + 1) % len(available)
        gap = (available[next_i] - available[i]) % 7
        max_gap = max(max_gap, gap)

    return max_gap + 1  # 간격 + 입고 1일
```

## 2. 발주-입고 대조 클래스

```python
# src/alert/receiving_matcher.py (신규)

class ReceivingMatcher:
    """발주(OT) vs 센터매입(receiving_history) 대조기

    Usage:
        matcher = ReceivingMatcher(store_id='46513', store_name='호반점')
        result = matcher.check_mismatches()
        # result = {
        #     'undelivered': [...],    # 미입고 (발주O 센터X, 리드타임 초과)
        #     'qty_mismatch': [...],   # 수량 불일치
        #     'unexpected': [...],     # 미발주 입고 (발주X 센터O)
        #     'pending': [...],        # 대기 중 (리드타임 이내)
        # }
    """

    def __init__(self, store_id, store_name=None):
        self.store_id = store_id
        self.store_name = store_name or store_id
```

### 2.1 대조 로직

```python
def check_mismatches(self, lookback_days=7) -> dict:
    """발주-입고 불일치 검사

    Args:
        lookback_days: 과거 N일 발주 대상 (기본 7)

    Returns:
        {undelivered, qty_mismatch, unexpected, pending}
    """
    conn = DBRouter.get_store_connection_with_common(self.store_id)
    cursor = conn.cursor()

    today = datetime.now().date()

    # 1) 최근 N일 발주 목록 (OT)
    orders = cursor.execute("""
        SELECT ot.item_cd, ot.item_nm, ot.mid_cd, ot.order_date,
               ot.order_qty, ot.delivery_type, ot.status,
               pd.orderable_day
        FROM order_tracking ot
        LEFT JOIN common.product_details pd ON ot.item_cd = pd.item_cd
        WHERE ot.order_date >= date('now', '-' || ? || ' days')
          AND ot.store_id = ?
        ORDER BY ot.order_date
    """, (lookback_days, self.store_id))

    # 2) 같은 기간 센터매입 목록
    receivings = cursor.execute("""
        SELECT item_cd, receiving_date, SUM(receiving_qty) as total_qty
        FROM receiving_history
        WHERE receiving_date >= date('now', '-' || ? || ' days')
          AND store_id = ?
        GROUP BY item_cd, receiving_date
    """, (lookback_days, self.store_id))

    # 3) 대조
    recv_map = {}  # {(item_cd, date): qty}
    for r in receivings:
        key = (r['item_cd'], r['receiving_date'])
        recv_map[key] = r['total_qty']

    undelivered = []   # 미입고
    qty_mismatch = []  # 수량 불일치
    pending = []       # 대기 중

    matched_items = set()  # 대조된 item_cd

    for order in orders:
        item_cd = order['item_cd']
        order_date = datetime.strptime(order['order_date'], '%Y-%m-%d').date()
        lead_days = calc_max_lead_days(order['orderable_day'])
        deadline = order_date + timedelta(days=lead_days)

        # 리드타임 범위 내 입고 찾기
        found_recv = None
        for delta in range(lead_days + 1):
            check_date = (order_date + timedelta(days=delta)).strftime('%Y-%m-%d')
            key = (item_cd, check_date)
            if key in recv_map:
                found_recv = (check_date, recv_map[key])
                matched_items.add((item_cd, check_date))
                break

        if found_recv:
            recv_date, recv_qty = found_recv
            if recv_qty != order['order_qty']:
                qty_mismatch.append({
                    'item_cd': item_cd,
                    'item_nm': order['item_nm'],
                    'order_date': order['order_date'],
                    'order_qty': order['order_qty'],
                    'recv_qty': recv_qty,
                    'diff': recv_qty - order['order_qty'],
                })
        elif today > deadline:
            # 리드타임 초과 + 센터매입에 없음 → 미입고
            undelivered.append({
                'item_cd': item_cd,
                'item_nm': order['item_nm'],
                'order_date': order['order_date'],
                'order_qty': order['order_qty'],
                'lead_days': lead_days,
                'days_overdue': (today - deadline).days,
            })
        else:
            # 아직 대기 중
            pending.append({
                'item_cd': item_cd,
                'item_nm': order['item_nm'],
                'order_date': order['order_date'],
                'deadline': deadline.strftime('%Y-%m-%d'),
            })

    # 4) 미발주 입고 (센터매입에 있는데 OT에 없는 것)
    unexpected = []
    # ... (센터매입 전체에서 matched_items 제외)

    return {
        'undelivered': undelivered,
        'qty_mismatch': qty_mismatch,
        'unexpected': unexpected,
        'pending': pending,
    }
```

## 3. 알림 메시지 생성

```python
def generate_mismatch_message(self, result: dict) -> Optional[str]:
    """불일치 알림 메시지 생성"""
    undelivered = result['undelivered']
    qty_mismatch = result['qty_mismatch']
    unexpected = result['unexpected']

    if not undelivered and not qty_mismatch and not unexpected:
        return None

    store_prefix = f"[{self.store_name}] " if self.store_name else ""
    today = datetime.now().strftime('%m/%d')
    lines = [f"{store_prefix}입고 확인 알림 ({today})", ""]

    if undelivered:
        lines.append(f"미입고 {len(undelivered)}건 (리드타임 초과):")
        for item in undelivered[:5]:
            lines.append(f"  {item['item_nm'][:18]}  발주 {item['order_date'][5:]}  "
                        f"{item['days_overdue']}일 초과")
        if len(undelivered) > 5:
            lines.append(f"  ...외 {len(undelivered)-5}건")
        lines.append("")

    if qty_mismatch:
        lines.append(f"수량 불일치 {len(qty_mismatch)}건:")
        for item in qty_mismatch[:5]:
            sign = '+' if item['diff'] > 0 else ''
            lines.append(f"  {item['item_nm'][:18]}  발주 {item['order_qty']}→입고 {item['recv_qty']}  "
                        f"({sign}{item['diff']})")
        if len(qty_mismatch) > 5:
            lines.append(f"  ...외 {len(qty_mismatch)-5}건")
        lines.append("")

    if unexpected:
        lines.append(f"미발주 입고 {len(unexpected)}건:")
        for item in unexpected[:5]:
            lines.append(f"  {item['item_nm'][:18]}  {item['recv_qty']}개")
        if len(unexpected) > 5:
            lines.append(f"  ...외 {len(unexpected)-5}건")

    return "\n".join(lines)
```

## 4. OT 폐기 알림 수정 — ordered 제외

### 즉시 적용 (이 기능과 독립)

```python
# order_tracking_repo.py: get_items_expiring_at()
# Before
WHERE status IN ('ordered', 'arrived')

# After
WHERE status = 'arrived'
```

이렇게 하면 미입고 상품이 폐기 알림에서 자동 제외됩니다.

## 5. 스케줄

```
07:00  메인 수집 (Phase 1.0~1.35)
07:20  Phase 1.18: 발주-입고 대조 + 알림 (신규)
         ├── ReceivingMatcher.check_mismatches()
         ├── 미입고/수량불일치/미발주입고 알림 (나에게 보내기)
         └── category="receiving_mismatch" → ALLOWED_CATEGORIES에 추가
```

## 6. 메시지 예시

```
[이천호반베르디움점] 입고 확인 알림 (04/01)

미입고 2건 (리드타임 초과):
  오리온)포카칩66g    발주 03/28  3일 초과
  농심)새우깡90g     발주 03/29  2일 초과

수량 불일치 1건:
  롯데)칠성사이다캔    발주 6→입고 4  (-2)

미발주 입고 3건:
  매일)킨더초콜릿      24개
  아사히생맥주340ml   24개
  앰지)홀스비타C      20개
```

## 7. 구현 순서

| # | 작업 | 파일 | 난이도 |
|---|------|------|--------|
| 1 | `calc_max_lead_days()` | receiving_matcher.py (신규) | 하 |
| 2 | `ReceivingMatcher.check_mismatches()` | receiving_matcher.py | 중 |
| 3 | `generate_mismatch_message()` | receiving_matcher.py | 하 |
| 4 | `send_mismatch_alert()` | receiving_matcher.py | 하 |
| 5 | OT `status='arrived'` 필터 수정 | order_tracking_repo.py | 하 |
| 6 | ALLOWED_CATEGORIES에 `receiving_mismatch` 추가 | kakao_notifier.py | 하 |
| 7 | Phase 1.18 수집 플로우 삽입 또는 별도 스케줄 | collection.py 또는 run_scheduler.py | 하 |
| 8 | 테스트 | - | 중 |

## 8. 카테고리별 차등

| 카테고리 | 발주주기 | 리드타임 | 대조 빈도 |
|---------|---------|---------|----------|
| 푸드 (001-005) | 매일 | 2일 | 매일 |
| 비푸드 주6일 | 월~토 | 2일 | 매일 |
| 비푸드 주3일 (화목토) | 화목토 | 3일 | 매일 |
| 비푸드 주3일 (월수금) | 월수금 | 3일 | 매일 |
| 비푸드 주2일 | 수금 등 | 4일 | 매일 |
