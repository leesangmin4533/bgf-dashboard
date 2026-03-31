# Design: 비푸드 과자 카테고리 유통기한 7일 전 알림

## 1. ExpiryChecker — 과자 만료 메시지 생성

### 새 메서드: generate_nonfood_expiry_message()

```python
# 대상 카테고리 (확장 가능)
NONFOOD_ALERT_CATEGORIES = {
    '015': '비스켓/쿠키',
    '016': '스낵류',
}

def generate_nonfood_expiry_message(self, days_ahead: int = 7) -> Optional[str]:
    """비푸드 카테고리 유통기한 만료 알림 메시지 생성

    Args:
        days_ahead: N일 이내 만료 (기본 7)

    Returns:
        알림 메시지 (대상 없으면 None)
    """
    conn = self._get_connection()
    cursor = conn.cursor()

    store_filter = "AND ib.store_id = ?" if self.store_id else ""
    store_params = (self.store_id,) if self.store_id else ()
    mid_codes = tuple(NONFOOD_ALERT_CATEGORIES.keys())

    cursor.execute(f"""
        SELECT ib.item_cd, p.item_nm, p.mid_cd, ib.remaining_qty,
               ib.expiry_date, ib.receiving_date
        FROM inventory_batches ib
        JOIN products p ON ib.item_cd = p.item_cd
        LEFT JOIN product_details pd ON ib.item_cd = pd.item_cd
        WHERE ib.status = 'active'
          AND ib.remaining_qty > 0
          AND p.mid_cd IN ({','.join('?' * len(mid_codes))})
          AND ib.expiry_date <= date('now', '+' || ? || ' days')
          AND ib.expiry_date > date('now')
          AND COALESCE(ib.expiration_days, 0) NOT IN (9999, 999)
          AND COALESCE(pd.orderable_status, '가능') != '불가'
          {store_filter}
        ORDER BY ib.expiry_date ASC
    """, mid_codes + (days_ahead,) + store_params)

    items = [dict(row) for row in cursor.fetchall()]
    if not items:
        return None

    store_prefix = f"[{self.store_name}] " if self.store_name else ""
    today = datetime.now().strftime('%m/%d')

    lines = [f"{store_prefix}과자류 유통기한 알림 ({today})", ""]
    lines.append(f"D-{days_ahead} 이내 만료 {len(items)}건:")

    for item in items[:10]:
        nm = item['item_nm'][:18]
        exp = item['expiry_date'][:10]
        qty = item['remaining_qty']
        mid_nm = NONFOOD_ALERT_CATEGORIES.get(item['mid_cd'], '')
        lines.append(f"  {nm}  만료 {exp[5:]}  {qty}개")

    if len(items) > 10:
        lines.append(f"  ...외 {len(items) - 10}건")

    lines.append("")
    total_qty = sum(i['remaining_qty'] for i in items)
    lines.append(f"총 {len(items)}건 {total_qty}개 — 할인/소진 검토 필요")

    return "\n".join(lines)
```

## 2. ExpiryChecker — 과자 만료 알림 발송

```python
def send_nonfood_expiry_alert(self, days_ahead: int = 7) -> bool:
    """비푸드 과자류 유통기한 알림 발송 (나에게 보내기만)"""
    msg = self.generate_nonfood_expiry_message(days_ahead)
    if not msg:
        logger.info(f"과자류 {days_ahead}일 이내 만료 대상 없음")
        return True

    notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
    if not notifier.access_token:
        return False

    result = notifier.send_message(msg, category="food_expiry")
    if result:
        logger.info(f"과자류 유통기한 알림 발송 완료")
    # 단톡방: 비활성 (추후 활성화 시 아래 주석 해제)
    # notifier.send_to_group(msg, store_id=self.store_id)
    return result
```

## 3. run_scheduler.py — 스케줄 등록

```python
def nonfood_expiry_alert_wrapper() -> None:
    """비푸드 과자류 유통기한 7일 전 알림 (매일 07:30)"""
    logger.info("=" * 60)
    logger.info(f"Non-food expiry alert at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    def alert_task(ctx):
        from src.alert.expiry_checker import ExpiryChecker
        checker = ExpiryChecker(store_id=ctx.store_id, store_name=ctx.store_name)
        try:
            result = checker.send_nonfood_expiry_alert(days_ahead=7)
            return {"success": True, "sent": bool(result)}
        finally:
            checker.close()

    _run_task(alert_task, "NonFoodExpiryAlert")

# 스케줄 등록 (run_scheduler 함수 내)
schedule.every().day.at("07:30").do(nonfood_expiry_alert_wrapper)
```

## 4. 메시지 예시

```
[이천호반베르디움점] 과자류 유통기한 알림 (04/11)

D-7 이내 만료 3건:
  크라운)산도딸기       만료 04/18  2개
  해태)후렌치파이사과    만료 04/19  1개
  오리온)통크          만료 04/20  3개

총 3건 6개 — 할인/소진 검토 필요
```

## 5. 구현 순서

| # | 작업 | 파일 |
|---|------|------|
| 1 | `NONFOOD_ALERT_CATEGORIES` 상수 추가 | expiry_checker.py |
| 2 | `generate_nonfood_expiry_message()` 메서드 | expiry_checker.py |
| 3 | `send_nonfood_expiry_alert()` 메서드 | expiry_checker.py |
| 4 | `nonfood_expiry_alert_wrapper()` 래퍼 | run_scheduler.py |
| 5 | 07:30 스케줄 등록 | run_scheduler.py |
| 6 | 테스트 (46513 과자 조회 확인) | - |

## 6. 확장 설정

```python
# 추후 카테고리 추가 시 NONFOOD_ALERT_CATEGORIES에 추가만 하면 됨
NONFOOD_ALERT_CATEGORIES = {
    '015': '비스켓/쿠키',
    '016': '스낵류',
    # 추후 확장:
    # '039': '과일야채음료',
    # '040': '기능건강음료',
    # '042': '커피음료',
    # '044': '탄산음료',
    # '032': '면류',
}
```
