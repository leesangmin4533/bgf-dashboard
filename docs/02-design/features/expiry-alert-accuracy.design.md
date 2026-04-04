# Design: expiry-alert-accuracy — 폐기 알림 정확도 개선

## 1. 현재 코드 구조

### 알림 생성 흐름
```
send_expiry_alert(14)
  → get_items_expiring_at(14)
    → _get_receiving_items_expiring_at(14, today)        ← 1순위
    → _get_batch_items_expiring_at(14, today)            ← 2순위 (보충)
  → generate_expiry_alert_message(14)
    → 카테고리별 그룹핑 → 메시지 포맷 → 카카오 발송
```

### 수량 결정 로직 (expiry_checker.py:522-568)
```python
for row in receiving_history_rows:
    # (1) stock_qty 조회
    stock_qty, ds_date = _get_latest_stock_with_date(item_cd)

    # (2) stock=0 필터
    if stock_qty == 0:
        if ds_date < receiving_date: stock_qty = recv_qty  # 오판 방지
        else: continue  # 판매완료 → 제외
    if stock_qty is None: continue  # 미존재

    # (3) batch 교차검증
    batch_remaining = _get_batch_remaining_qty(item_cd, receiving_date)
    if batch_remaining is not None and batch_remaining <= 0:
        continue  # 소진됨

    # (4) ★ 수량 결정 — 여기가 문제
    remaining_qty = stock_qty  # 전체 재고를 그대로 사용
```

### 버그 위치

| # | 코드 | 문제 | 라인 |
|---|------|------|------|
| B1 | `if stock_qty == 0:` | **`stock_qty < 0` 미처리**. 마이너스 재고가 통과 | 529 |
| B2 | `remaining_qty: stock_qty` | stock > 0이지만 **해당 배치 잔여가 아닌 전체 재고** | 563 |
| B3 | `qty = item.get('remaining_qty', 0)` | 메시지 생성에서 **qty <= 0 체크 없음** | 985 |

## 2. 수정 설계 (토론 결과 반영)

### 수정 항목 (2건만 채택)

| # | 수정 | 채택 | 토론 근거 |
|---|------|------|----------|
| 1 | stock_qty `== 0` → `<= 0` (1줄) | ✅ | 두 전문가 합의: 이것만으로 3건 버그 모두 해결 |
| 2 | batch_remaining 부분 적용 | ❌ 제거 | 악마: stock/batch 의미 혼동, 실용: 복잡도 > 효과 |
| 3 | 메시지 qty <= 0 제거 | ❌ 제거 | 수정1이 원천 차단, 메시지 레벨 필터는 total/alert_sent 불일치 유발 |
| 4 | 발송 전 요약 로그 | ✅ | 부작용 없음, 역추적 유용 |

### 수정 1: stock_qty <= 0 필터 + ds_date 간격 제한 (B1 + B4)

**위치**: `expiry_checker.py:528-536`

**추가 발견 (사용자 검증)**: `ds_date < recv_date` 비교가 너무 느슨.
김)김밥의기본2의 ds_date=2026-01-21(3개월 전), recv_date=4/3 → True 통과 → 매장에 없는 상품이 알림 포함.

```python
# Before:
if stock_qty is not None and stock_qty == 0:
    if ds_date and ds_date < row['receiving_date']:
        stock_qty = recv_qty
    else:
        continue
if stock_qty is None:
    continue

# After:
if stock_qty is not None and stock_qty <= 0:
    if stock_qty < 0:
        logger.debug(f"[ExpiryAlert] {item_cd} 제외: stock={stock_qty} (마이너스)")
        continue
    # stock_qty == 0: 입고 후 미반영인지 판단
    if ds_date and ds_date < row['receiving_date']:
        days_gap = (datetime.strptime(row['receiving_date'], '%Y-%m-%d')
                    - datetime.strptime(ds_date, '%Y-%m-%d')).days
        if days_gap <= 3:
            stock_qty = recv_qty  # 최근 수집 → 입고 미반영 가능
        else:
            logger.debug(f"[ExpiryAlert] {item_cd} 제외: stock=0, ds_date={ds_date} ({days_gap}일 전)")
            continue  # 수집이 너무 오래전 → 미취급 상품
    else:
        continue  # stock=0, ds >= recv → 판매완료
if stock_qty is None:
    continue
```

**효과**:
- 마이너스 재고 제거 (B1)
- 수개월 전 stock=0 데이터로 recv_qty 오사용 방지 (B4, 5건 해결)
- 3일 이내 수집만 "입고 미반영" 허용

### ~~수정 2: batch_remaining 부분 적용~~ (토론 후 제거)
> 악마: stock_qty는 "전체 재고", batch_remaining은 "배치 잔량" — 의미가 다름. 점주는 전체 재고를 보고 행동하므로 stock_qty가 더 유용.
> 실용: 복잡도 대비 효과 낮음. batch는 제외 필터(line 542)로만 사용하는 현재 구조가 맞음.

### ~~수정 3: 메시지 최종 방어선~~ (토론 후 제거)
> 악마: 메시지 레벨 필터는 total 카운트, _pending_alert_ids와 불일치 유발. 수정 1이 items 원천에서 차단하므로 불필요.
> 실용: 수정 1이 잘 되면 도달하지 않는 코드. 방어적 코딩보다 근원 차단.

### 수정 4: 발송 전 요약 로그 (R5)

**위치**: `send_expiry_alert()` 내부, 메시지 발송 직전

```python
logger.info(
    f"[ExpiryAlert] {self.store_id} {expiry_hour:02d}시 알림: "
    f"{len(items)}건 ({sum(i.get('remaining_qty', 0) for i in items)}개)"
)
for item in items:
    logger.debug(
        f"  {item['item_nm'][:20]} qty={item['remaining_qty']} "
        f"src={'batch' if item.get('_qty_source') == 'batch' else 'stock'}"
    )
```

## 3. 수정하지 않는 것

| 항목 | 이유 |
|------|------|
| receiving_history 조회 쿼리 | 정상 작동 (receiving_qty > 0 필터 있음) |
| EXPIRY_HOUR_TO_RECEIVING 매핑 | 정상 (14시: 2차, D-1, 001/002/003) |
| _get_batch_remaining_qty | 정상 (receiving_date 기준 조회) |
| 2순위 batch_items 경로 | 정상 (status=active, remaining_qty > 0 필터) |
| 메시지 포맷 | 정상 (수량 표시 방식) |

## 4. 수정 영향 분석

### 후행 덮어쓰기 체크
- 알림 생성 → 카카오 발송: 단방향, 후행 없음 ✅
- batch_remaining 사용: 조회만, 수정 없음 ✅
- stock_qty 필터: 조회만, 수정 없음 ✅

### 기대 정확도 변화
- 마이너스 수량 제거: 136건 중 3건 해결 → +2%
- batch_remaining 부분 적용: batch 있는 경우만 → 기존 67% 상품에 적용, None은 72% 유지
- 최종 방어선: 0 이하 수량 메시지 제거 → 오표시 완전 방지

### 수정 파일
- `src/alert/expiry_checker.py` — 4곳 수정 (라인 529, 558-563, 982-990, send_expiry_alert)

## 5. 토론 검토 포인트

1. **수정 2의 안전성**: batch_remaining을 부분 적용하는 것이 stock_qty 단독보다 나은가?
   - batch 있고 remain > 0인 경우만 적용 → None 폴백 안전
   - BUT 7일 검증에서 batch 정확도 67% < stock 72% → 부분 적용이 전체를 악화시키지 않는지

2. **수정 1의 부작용**: stock < 0 제외 시 실제로 폐기해야 할 상품을 놓칠 가능성?
   - 마이너스 재고 = BGF 시스템에서 이미 소진 판정 → 폐기 불필요
   - 놓칠 위험 거의 없음

3. **수정 3의 부작용**: qty=0 상품을 메시지에서 제거하면 "0개 남음" 정보 손실?
   - 0개 남음 = 폐기할 것 없음 → 알림할 필요 없음
