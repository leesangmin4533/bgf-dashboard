# Design: 슬롯 기반 폐기 추적 검증 (waste-verification-slot-based)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/waste-verification-slot-based.plan.md

---

## 1. 핵심 알고리즘

### 슬롯 분류 (cre_ymdhms 기준)
```
HH = int(cre_ymdhms[8:10])
if 2 <= HH <= 13:
    slot = 'slot_2am'   # 02:00 ~ 13:59 → 1차 폐기
elif 14 <= HH <= 23 or HH < 2:
    slot = 'slot_2pm'   # 14:00 ~ 다음날 01:59 → 2차 폐기
else:  # 파싱 실패 등
    slot = 'unclassified'
```

### Tracking base (사각지대 해소)
```sql
-- slot_2am: 02:00 만료 + active 아닌 모든 배치
SELECT item_cd FROM inventory_batches
WHERE date(expiry_date) = ?
  AND time(expiry_date) = '02:00:00'
  AND status != 'active'

-- slot_2pm: 14:00 만료 + active 아닌 모든 배치
SELECT item_cd FROM inventory_batches
WHERE date(expiry_date) = ?
  AND time(expiry_date) = '14:00:00'
  AND status != 'active'
```

### Slip-side (BGF 폐기)
```sql
-- 헤더 cre_ymdhms 시각으로 슬롯 분류한 후 매칭
SELECT wsi.item_cd, ws.cre_ymdhms
FROM waste_slip_items wsi
JOIN waste_slips ws USING (store_id, chit_date, chit_no)
WHERE wsi.chit_date = ?
```

### 매칭
```python
for slot in ['slot_2am', 'slot_2pm']:
    tracking_set = set(IB items where ...)
    slip_set = set(slip_items where _classify_slot(cre_ymdhms) == slot)
    matched = tracking_set & slip_set
    slip_only = slip_set - tracking_set
    tracking_only = tracking_set - slip_set
```

---

## 2. 신규 메서드 시그니처

### `WasteVerificationService.verify_date_by_slot`
```python
def verify_date_by_slot(self, target_date: str) -> Dict[str, Any]:
    """슬롯별(02시/14시) 폐기 추적 검증.

    Returns:
        {
          "date": str,
          "store_id": str,
          "slot_2am": {tracking_base, slip_count, matched, slip_only,
                       tracking_only, match_rate, ontime_rate},
          "slot_2pm": {...},
          "unclassified": int,
          "summary": {overall_match_rate, false_negative, false_positive}
        }
    """
```

### `_classify_slot` 헬퍼 (waste_verification_reporter)
```python
def _classify_slot(cre_ymdhms: str) -> str:
    """cre_ymdhms 14자리 → 'slot_2am' / 'slot_2pm' / 'unclassified'"""
    if not cre_ymdhms or len(cre_ymdhms) != 14:
        return 'unclassified'
    try:
        hh = int(cre_ymdhms[8:10])
    except ValueError:
        return 'unclassified'
    if 2 <= hh <= 13:
        return 'slot_2am'
    if 14 <= hh <= 23 or hh < 2:
        return 'slot_2pm'
    return 'unclassified'
```

---

## 3. 변경 코드 미리보기

### `waste_verification_reporter.py` `_get_tracking_inventory_batches` (사각지대 fix)
```python
# Before
WHERE expiry_date = ? AND status = 'expired'

# After (사각지대 해소)
WHERE date(expiry_date) = ? AND status != 'active'
```

### 신규 메서드 (`waste_verification_reporter`)
```python
def get_slot_comparison_data(self, target_date: str) -> Dict[str, Any]:
    """슬롯별 비교 데이터 반환"""
    # 1) waste_slips JOIN waste_slip_items → cre_ymdhms 포함 폐기 목록
    # 2) inventory_batches → 02:00/14:00 만료 + status!=active 추적 목록
    # 3) _classify_slot으로 슬롯 분류
    # 4) 슬롯별 set 비교
```

---

## 4. 회귀 테스트 5개

1. **slot_2am 매칭**: 02:00 만료 1개 + 새벽 3시 BGF 입력 → matched=1
2. **slot_2pm 매칭**: 14:00 만료 1개 + 15시 BGF 입력 → matched=1
3. **slot_2am tracking_only**: 02:00 만료 추적, BGF 입력 없음 → tracking_only=1
4. **slot_2pm 새벽 윈도우**: 14:00 만료 추적, 새벽 1시 BGF 입력 → slot_2pm 매칭
5. **사각지대 해소**: tracking에 consumed 포함, BGF 폐기 매칭 → matched

---

## 5. 단계

| # | 작업 |
|---|---|
| 1 | `waste_verification_reporter.py`: `_classify_slot` + `get_slot_comparison_data` 추가, 기존 tracking 쿼리 `status != 'active'` 변경 |
| 2 | `waste_verification_service.py`: `verify_date_by_slot` 추가 |
| 3 | `tests/test_waste_verification_slot.py` 5개 |
| 4 | pytest |
| 5 | 04-07 4매장 수동 검증 |
| 6 | 이슈체인 + commit + push |

---

## 6. 다음 단계

`/pdca do waste-verification-slot-based`
