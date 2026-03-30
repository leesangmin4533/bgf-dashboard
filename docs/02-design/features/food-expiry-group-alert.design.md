# Design: 동양점 단톡방 푸드 폐기 전용 알림

## 1. 변경 1: kakao_notifier.py — send_to_group 분리

### Before
```python
# send_message() 내부에서 자동 호출 (모든 메시지가 단톡방으로)
if response.status_code == 200:
    self._send_to_group(text)  # ← 제거
    return True
```

### After
```python
# send_message()는 카카오 API만 (기존 복원)
if response.status_code == 200:
    return True

# 공개 메서드: store_id 기반으로 해당 매장 단톡방에만 전송
def send_to_group(self, text: str, store_id: Optional[str] = None) -> dict:
    """특정 매장의 단톡방에 메시지 전송

    Args:
        text: 메시지
        store_id: 매장 ID (rooms dict에서 채팅방 이름 조회)
    """
    if not self._group_sender:
        return {"sent": [], "failed": [], "not_found": []}
    try:
        return self._group_sender.send(text, store_id=store_id)
    except Exception as e:
        logger.warning(f"단톡방 전송 오류: {e}")
        return {"sent": [], "failed": [], "not_found": []}
```

## 2. 변경 2: config/group_chat.json — 매장별 단톡방 매핑

```json
{
  "enabled": true,
  "rooms": {
    "46704": {"name": "CU동양점", "enabled": true},
    "46513": {"name": "CU호반베르디움", "enabled": false},
    "47863": {"name": "CU마평로드", "enabled": false},
    "49965": {"name": "CU원삼휴게소", "enabled": false}
  },
  "alert_types": ["food_expiry"],
  "_note": "rooms: { store_id: 채팅방 윈도우 타이틀 }. 매장별 폐기 알림이 해당 매장의 단톡방에만 전송됨."
}
```

**핵심**: `store_id → room_name` 매핑. 스케줄러가 46513 폐기를 실행하면 "CU호반베르디움" 단톡방에만 전송.

기존 `room_names` (리스트) → `rooms` (dict)로 변경. `KakaoGroupSender`도 store_id 기반으로 특정 채팅방만 전송하도록 수정.

## 3. 변경 3: expiry_checker.py — 매장명 표시 + 매장별 단톡방

### ExpiryChecker에 store_name 추가

```python
class ExpiryChecker:
    def __init__(self, store_id=None, store_name=None):
        self.store_id = store_id
        self.store_name = store_name or store_id  # 폴백: store_id
```

스케줄러에서 호출 시:
```python
checker = ExpiryChecker(store_id=ctx.store_id, store_name=ctx.store_name)
```

### 메시지 포맷 — 매장명 포함

**Before**:
```
⏰ 14:00 폐기 알림 (03/30 13:30)
30분 후 폐기 처리 필요!
```

**After**:
```
[이천동양점] ⏰ 14:00 폐기 알림 (03/30 13:30)
30분 후 폐기 처리 필요!
```

모든 메시지 생성 함수의 첫 줄에 `[{self.store_name}]` 접두사 추가:
- `generate_expiry_alert_message()` — 30분 전 알림
- `generate_alert_message()` — 범용 폐기 위험 알림
- 빵(012) 자정 만료 메시지

### send_expiry_alert() — 단톡방 전송 추가

```python
def send_expiry_alert(self, expiry_hour: int) -> bool:
    msg = self.generate_expiry_alert_message(expiry_hour)
    if not msg:
        return True

    notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
    result = notifier.send_message(msg)       # 나에게 보내기

    # 해당 매장의 단톡방에 전송
    if result:
        notifier.send_to_group(msg, store_id=self.store_id)

    return result
```

## 4. 변경 4: run_scheduler.py — 스케줄 통합 (3단계 → 3단계 유지, 역할 변경)

### Before (현재)

```
-60min  PRE_ALERT_COLLECTION     ← BGF 수집 (알림용)
-30min  EXPIRY_ALERT             ← 알림 발송
-10min  EXPIRY_CONFIRM step1     ← BGF 수집 (정밀폐기용)
  0min  EXPIRY_CONFIRM step2     ← 판정
+10min  EXPIRY_CONFIRM step3     ← 수집 + 폐기 확정
```

### After (합류)

```
-10min  step1 (pre_collect)      ← BGF 수집 + 예고 알림 발송 (합류!)
  0min  step2 (judge)            ← 판정
+10min  step3 (confirm)          ← 수집 + 폐기 확정 + 컨펌 알림
```

**제거**: PRE_ALERT_COLLECTION_SCHEDULE, EXPIRY_ALERT_SCHEDULE
**변경**: step1에 알림 발송 합류, step3에 컨펌 알림 추가

### expiry_pre_collect_wrapper() 수정 — 예고 알림 합류

```python
def expiry_pre_collect_wrapper(expiry_hour: int) -> Callable[[], None]:
    """[step1] 폐기 10분 전: 수집 + 예고 알림 발송"""
    def wrapper() -> None:
        def pre_collect_task(ctx):
            # 1) BGF 사이트 수집 (기존)
            job = DailyCollectionJob(store_id=ctx.store_id)
            job.run_optimized(run_auto_order=False)

            # 2) order_tracking 상태 전이 (기존)
            # 3) 배치 FIFO 재동기화 (기존)

            # === 신규: 예고 알림 발송 (수집 직후, 최신 데이터) ===
            checker = ExpiryChecker(store_id=ctx.store_id, store_name=ctx.store_name)
            try:
                result = checker.send_expiry_alert(expiry_hour)
                # send_expiry_alert 내부에서:
                #   notifier.send_message(msg)              ← 나에게
                #   notifier.send_to_group(msg, store_id)   ← 해당 매장 단톡방
            finally:
                checker.close()

        _run_task(pre_collect_task, f"ExpiryPreCollect+Alert({expiry_hour:02d}:00)")
    return wrapper
```

### expiry_confirm_wrapper() 수정 — 컨펌 알림 추가

```python
def expiry_confirm_wrapper(expiry_hour: int) -> Callable[[], None]:
    """[step3] 폐기 10분 후: 수집 + 확정 + 컨펌 알림"""
    def wrapper() -> None:
        def confirm_task(ctx):
            # 1) 판매 수집 (기존)
            # 2) 판정 결과 가져오기 (기존)
            # 3) 폐기 확정 (기존)
            confirmed = batch_repo.confirm_expiry_batches(...)

            # === 신규: 컨펌 알림 발송 ===
            _send_confirm_alert(ctx, expiry_hour, confirmed)

            return {"success": True, "expired_count": len(confirmed)}

        _run_task(confirm_task, f"ExpiryConfirm({expiry_hour:02d}:00)")
    return wrapper


def _send_confirm_alert(ctx, expiry_hour: int, confirmed: list) -> None:
    """폐기 확정 후 컨펌 알림 (나에게 + 해당 매장 단톡방)"""
    if not confirmed:
        return

    # step1 예고 대상과 비교하여 누락건 식별
    checker = ExpiryChecker(store_id=ctx.store_id, store_name=ctx.store_name)
    try:
        pre_alert_items = checker.get_items_expiring_at(expiry_hour)
        pre_alert_codes = {item['item_cd'] for item in pre_alert_items}
    finally:
        checker.close()

    expected = [i for i in confirmed if i.get('item_cd') in pre_alert_codes]
    missed = [i for i in confirmed if i.get('item_cd') not in pre_alert_codes]

    msg = _format_confirm_message(ctx, expiry_hour, expected, missed)

    notifier = KakaoNotifier(DEFAULT_REST_API_KEY)
    notifier.send_message(msg)                          # 나에게
    notifier.send_to_group(msg, store_id=ctx.store_id)  # 해당 매장 단톡방
```

## 5. 변경 5: KakaoGroupSender — 매장별 채팅방 전송

### Before
```python
class KakaoGroupSender:
    def __init__(self, room_names: List[str]):
        self.room_names = room_names  # 전체 채팅방 리스트

    def send(self, text: str) -> dict:
        # 모든 room_names에 전송
```

### After
```python
class KakaoGroupSender:
    def __init__(self, rooms: Dict[str, str]):
        """rooms: {store_id: room_name}"""
        self.rooms = rooms  # 매장별 채팅방 매핑

    def send(self, text: str, store_id: Optional[str] = None) -> dict:
        """특정 매장의 채팅방에만 전송

        store_id 지정 시: rooms[store_id]의 enabled=true인 채팅방만 전송.
        store_id=None: 전체 enabled=true인 채팅방에 전송.
        """
        if store_id:
            room = self.rooms.get(store_id, {})
            if not room or not room.get("enabled", False):
                return {"sent": [], "failed": [], "not_found": []}
            target_rooms = [room["name"]]
        else:
            target_rooms = [r["name"] for r in self.rooms.values()
                           if r.get("enabled", False)]

        # target_rooms만 대상으로 윈도우 검색 + 전송
```

### KakaoNotifier._init_group_sender() 수정

```python
def _init_group_sender(self) -> None:
    config = json.load(open(GROUP_CHAT_CONFIG_FILE))
    if config.get("enabled"):
        rooms = config.get("rooms", {})
        if rooms:
            self._group_sender = KakaoGroupSender(rooms)
```

## 6. 컨펌 메시지 포맷

```python
def _format_confirm_message(ctx, expiry_hour, expected, missed):
    lines = []
    store_name = ctx.store_name or ctx.store_id
    now_str = datetime.now().strftime('%m/%d %H:%M')

    lines.append(f"[{store_name}] {expiry_hour:02d}:00 폐기 확정 ({now_str})")
    lines.append("")

    if expected:
        lines.append(f"[폐기 확정] {len(expected)}개")
        # 카테고리별 그룹핑
        by_cat = {}
        for item in expected:
            cat = item.get('category_name', '기타')
            by_cat.setdefault(cat, []).append(item)
        for cat, items in by_cat.items():
            lines.append(f"  {cat}:")
            for item in items[:5]:
                nm = item.get('item_nm', '')[:15]
                qty = item.get('adjusted_qty', item.get('remaining_qty', 0))
                lines.append(f"    {nm}  {qty}개")
            if len(items) > 5:
                lines.append(f"    ...외 {len(items)-5}개")
        lines.append("")

    if missed:
        lines.append(f"[누락건 추가] {len(missed)}개 (30분 전 미포함)")
        for item in missed[:5]:
            nm = item.get('item_nm', '')[:15]
            qty = item.get('adjusted_qty', item.get('remaining_qty', 0))
            lines.append(f"  {nm}  {qty}개")
        if len(missed) > 5:
            lines.append(f"  ...외 {len(missed)-5}개")
        lines.append("")

    total = len(expected) + len(missed)
    total_qty = sum(i.get('adjusted_qty', i.get('remaining_qty', 0)) for i in expected + missed)
    lines.append(f"총 {total}개 상품 {total_qty}개 폐기")

    return "\n".join(lines)
```

## 7. 시퀀스 다이어그램 (합류 후)

```
시간축  스케줄러               ExpiryChecker       KakaoNotifier      단톡방
──────────────────────────────────────────────────────────────────────────
-10min  step1 (pre_collect)
        → BGF 수집
        → 배치 동기화
        → send_expiry_alert()
          → generate_msg()     → send_message()   (나에게)
            [매장명] 포함        → send_to_group()  → [예고 알림]

 정각   step2 (judge)
        → 만료 배치 판정 + stock 스냅샷

+10min  step3 (confirm)
        → BGF 수집
        → stock 비교 → 폐기 확정
        → _send_confirm_alert()
          → 예고 vs 확정 비교   → send_message()   (나에게)
          → 누락건 분류          → send_to_group()  → [확정+누락건]
```

**제거된 스케줄**: PRE_ALERT_COLLECTION (-60분), EXPIRY_ALERT (-30분)

## 7. 구현 순서

| # | 작업 | 파일 |
|---|------|------|
| 1 | `send_message()`에서 `_send_to_group` 자동호출 제거 | kakao_notifier.py |
| 2 | `send_to_group(text, store_id)` 공개 메서드 추가 | kakao_notifier.py |
| 3 | `config/group_chat.json` → rooms dict (매장별 매핑, enabled 개별) | config/ |
| 4 | `KakaoGroupSender` → rooms dict 기반, `send(text, store_id)` | kakao_group_sender.py |
| 5 | `_init_group_sender()` → rooms dict 로드 | kakao_notifier.py |
| 6 | `ExpiryChecker.__init__`에 `store_name` 파라미터 추가 | expiry_checker.py |
| 7 | 메시지 생성 함수들에 `[매장명]` 접두사 추가 | expiry_checker.py |
| 8 | `send_expiry_alert()`에 `send_to_group(msg, store_id)` 추가 | expiry_checker.py |
| 9 | `expiry_pre_collect_wrapper()`에 예고 알림 합류 | run_scheduler.py |
| 10 | PRE_ALERT_COLLECTION_SCHEDULE, EXPIRY_ALERT_SCHEDULE 제거 | run_scheduler.py |
| 11 | `_format_confirm_message()` + `_send_confirm_alert()` 작성 | run_scheduler.py |
| 12 | `expiry_confirm_wrapper()`에 컨펌 알림 통합 | run_scheduler.py |
| 13 | 통합 테스트 (CU동양점 단톡방) | - |

## 8. 테스트 계획

1. **단위**: `send_to_group()` 호출 시 CU동양점 채팅방에만 전송되는지
2. **통합**: `send_message("일일 리포트")` 호출 시 단톡방에 안 가는지
3. **E2E**: 14:00 폐기 시뮬레이션
   - 13:30 예고 → 단톡방 + 나에게 발송 확인
   - 14:10 컨펌 → 확정 목록 + 누락건 분류 확인
