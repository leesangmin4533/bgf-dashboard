# Plan: 20:30 미매칭 재시도 (delivery-rematch)

> 작성일: 2026-04-06
> 이슈체인: expiry-tracking.md#delivery_match-타이밍-불일치

---

## 1. 문제
2차 매칭(07:00): receiving_qty=0(미도착) → 매칭 실패 → 배치 미생성 → 폐기 알림 누락

## 2. 수정
20:30 receiving_collect(1차) 완료 후, **미매칭 건 전체(1차+2차) 재매칭** 추가.
이 시점이면 2차 배송도 도착해서 receiving_qty > 0.

## 3. 변경

| 파일 | 변경 |
|------|------|
| `delivery_match_flow.py` | `rematch_unmatched()` 함수 추가 — matched=0인 전체 재매칭 |
| `run_scheduler.py` | 20:30 receiving_collect_wrapper 내부에서 rematch 호출 |

## 4. rematch_unmatched 로직

```python
def rematch_unmatched(store_id: str) -> dict:
    """matched=0인 모든 confirmed_orders 재매칭 (delivery_type 무관)"""
    # 1. confirmed_orders에서 matched=0 전부 조회 (오늘+어제)
    # 2. receiving_history에서 해당 item_cd의 최신 receiving_qty 조회
    # 3. qty > 0이면 매칭 + 배치 생성
```

기존 `match_confirmed_with_receiving`는 단일 delivery_type만 처리하므로,
**delivery_type 무관하게 미매칭 전체**를 처리하는 별도 함수.
