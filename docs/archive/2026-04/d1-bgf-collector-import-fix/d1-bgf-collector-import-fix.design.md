# Design: D-1 부스트 발주 bgf_collector import 재발 수정 (d1-bgf-collector-import-fix)

> 작성일: 2026-04-07
> 상태: Design
> Plan: docs/01-plan/features/d1-bgf-collector-import-fix.plan.md
> 이슈체인: expiry-tracking.md#d-1-부스트-발주-execute-single-order-누락-scheduler-모듈-캐시

---

## 1. 설계 개요

**1-라인 메서드명 수정 + 회귀 테스트 1개 + scheduler 재시작 가이드.**

- 정적 버그(원인 A): `second_delivery_adjuster.py:441` `execute_single_order` → `execute_order`
- target_date 파라미터: **미지정(None)** 결정 — `select_order_day`의 기본 동작(가용 첫 날짜 자동 선택)에 위임
- 회귀 테스트: mock OrderExecutor + boost_orders 1개 → `execute_order` 호출 검증
- 운영 캐시(원인 B): scheduler 재시작 가이드 (코드 수정만으로는 안 됨)

---

## 2. Design 결정

### 결정 1: target_date = None (미지정)

#### 컨텍스트
- `select_order_day(target_date=None, select_first_if_not_found=True)` (order_executor.py:423)
  - `target_date is None` → 오늘 날짜 사용
  - 오늘 날짜가 발주 가능 리스트에 없으면 → **첫 번째 가용 날짜 자동 선택**
- docstring: "발주 마감은 오전 10시. 10시 이후에는 오늘 날짜가 리스트에 없을 수 있음"
- D-1 부스트 실행 시각: **14:00** → 발주 마감 지남 → 오늘 슬롯 닫힘 가능성 높음

#### 옵션 비교

| 옵션 | 동작 | 장단점 |
|------|------|--------|
| **A. None (권장)** | 오늘 시도 → 실패 시 첫 가용 날짜 자동 선택 | BGF 화면이 보여주는 가능 리스트를 그대로 신뢰. 단순. |
| B. `result.today` 명시 | 오늘 명시 | None과 동일 동작 (select_order_day가 어차피 None 처리와 같음). 코드 노이즈만 추가 |
| C. `(today+1)` 명시 | 내일 지정 | 14:00 시점에서 직관적이지만 D-1 부스트 의도(2차 배송 당일 보충)와 충돌 가능. 운영 가정 변경 |

→ **옵션 A 채택**. `execute_order(item_cd=bo.item_cd, qty=bo.delta_qty)` (target_date 인자 생략)

#### 근거
1. `select_first_if_not_found=True`가 기본값 → BGF가 제공하는 가용 날짜 중 첫 번째 자동 선택
2. 기존 단품별 발주 경로(execute_order)가 같은 패턴으로 동작 중 → 검증된 동작 재사용
3. D-1 부스트는 "지금 가능한 가장 빠른 슬롯에 추가 발주" 의미 → 옵션 A와 일치
4. result.today는 D-1 판단 단계의 기준 날짜이지 발주 슬롯이 아님 → 발주 화면 자체에 위임

### 결정 2: AttributeError 회귀 방지 — 단위 테스트 1개

#### 케이스
**test_execute_boost_orders_calls_execute_order**
- mock `OrderExecutor` (spec=실제 클래스로 AttributeError 검증 강제)
- `boost_orders` 1개 포함된 mock `AdjustmentResult`
- `execute_boost_orders(result, mock_driver)` 호출
- assert: `mock_executor.execute_order` 정확히 1회 호출 + 인자 일치

#### 핵심 포인트
- `Mock(spec=OrderExecutor)` 사용 → `execute_single_order` 같은 존재하지 않는 메서드 접근 시 AttributeError 자동 발생
- 이 패턴이 회귀 방지의 핵심: 메서드명 오기를 컴파일 단계에서 못 잡는 Python에서 spec mock으로 잡는다

```python
from unittest.mock import MagicMock, patch
from src.analysis.second_delivery_adjuster import (
    execute_boost_orders, AdjustmentResult, BoostOrder
)
from src.order.order_executor import OrderExecutor


def test_execute_boost_orders_calls_execute_order():
    """회귀: execute_single_order(없음) 대신 execute_order 호출"""
    mock_executor = MagicMock(spec=OrderExecutor)
    mock_executor.execute_order.return_value = {"success": True}

    result = AdjustmentResult(
        run_at="2026-04-07T14:00:00",
        store_id="49965",
        today="2026-04-07",
        total_second_items=1,
        morning_data_available=True,
        boost_targets=1,
        reduce_logged=0,
        boost_orders=[BoostOrder(item_cd="A001", delta_qty=2)],
    )

    with patch("src.analysis.second_delivery_adjuster.OrderExecutor", return_value=mock_executor), \
         patch("src.analysis.second_delivery_adjuster._get_conn") as mock_conn, \
         patch("src.analysis.second_delivery_adjuster._ensure_log_table"):
        mock_conn.return_value = MagicMock()
        ret = execute_boost_orders(result, driver=MagicMock())

    assert ret["executed"] == 1
    assert ret["failed"] == 0
    mock_executor.execute_order.assert_called_once_with(item_cd="A001", qty=2)
    # spec mock이라 execute_single_order 호출했으면 AttributeError로 실패했을 것
```

### 결정 3: 운영 캐시(원인 B) — Design 비범위, 가이드만

- code change → scheduler 자동 재시작은 별도 설계 필요 (관찰자 패턴, watchdog 등)
- 현 작업에서는 **수동 재시작 명문화**만:
  1. 코드 변경 후 `Get-Process | Where-Object {$_.ProcessName -eq "python"}` 로 scheduler PID 확인
  2. 종료 후 `python run_scheduler.py` 재시작
  3. 14:00 작업 전 재시작 권장
- 향후 작업: `scheduler-auto-reload` 별도 Plan으로 분리

### 결정 4: 영향 범위 한정
- `OrderExecutor.execute_order`는 단품별 발주의 표준 경로 → 사이드 이펙트 없음
- D-1 부스트가 정확히 단품별 발주 화면을 사용해야 하는지 확인 필요? → 이미 동일 경로(`SUBMENU_SINGLE_ORDER_TEXT`)로 동작 중인 것이 정상 발주 흐름과 동일함 (sales_collector → 단품별 발주 메뉴)

---

## 3. 변경 코드 요약

### Before (`second_delivery_adjuster.py:439-444`)
```python
for bo in result.boost_orders:
    try:
        order_result = executor.execute_single_order(
            item_cd=bo.item_cd,
            qty=bo.delta_qty,
        )
```

### After
```python
for bo in result.boost_orders:
    try:
        order_result = executor.execute_order(
            item_cd=bo.item_cd,
            qty=bo.delta_qty,
        )
```

**핵심 diff**: 1줄 (메서드명만)

---

## 4. 회귀 테스트 케이스 (총 3개)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `test_execute_boost_orders_calls_execute_order` | spec mock으로 메서드명 오기 회귀 방지 (핵심) |
| 2 | `test_execute_boost_orders_handles_failure` | execute_order가 `{"success": False}` 반환 시 failed += 1 |
| 3 | `test_execute_boost_orders_empty_skips` | boost_orders=[] 시 OrderExecutor 초기화 안 함, `{"executed":0,"failed":0}` 반환 |

파일: `tests/test_second_delivery_adjuster.py` (신규)

---

## 5. 구현 순서

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | `second_delivery_adjuster.py:441` 메서드명 수정 | second_delivery_adjuster.py |
| 2 | `tests/test_second_delivery_adjuster.py` 신규 생성 (3 케이스) | tests/ |
| 3 | `pytest tests/test_second_delivery_adjuster.py -v` 통과 확인 | — |
| 4 | 이슈체인 `[OPEN] → [WATCHING]` + 시도 1 기록 | scheduling.md/expiry-tracking.md |
| 5 | 커밋 + 푸시 | — |
| 6 | scheduler 재시작 (운영) | run_scheduler.py |
| 7 | 다음 14:00 D-1 작업 결과 확인 (49965 success=True) | logs/bgf_auto.log |

---

## 6. 검증 방법

### 단위 테스트
```bash
pytest tests/test_second_delivery_adjuster.py -v
```

### 통합 (운영)
- scheduler 재시작 후 다음 14:00 작업 대기
- `logs/bgf_auto.log`에서:
  - `[D-1] 부스트 대상 N개 → Selenium 세션 시작`
  - `[D-1] BOOST 완료 | {item_cd} +{qty}개` (성공)
  - 또는 `[D-1] BOOST 실패` (운영 사유 — 화면/네트워크 등, ModuleNotFoundError 아님)
- `data/d1_adjustment_log` 또는 d1_adjustment_log 테이블에서 `executed=1` 행 확인

### 성공 기준
1. 회귀 테스트 3개 통과
2. scheduler 재시작 후 14:00 작업에서 49965 ModuleNotFoundError 소멸
3. d1_adjustment_log에 executed=1 또는 BOOST 완료 로그 1건 이상

---

## 7. 롤백 계획

- 코드 1줄 수정이라 git revert로 즉시 롤백
- D-1 부스트가 실패해도 정상 발주(07:00) 자체에 영향 없음
- 폐기 위험은 부스트 미실행 = 기존 상태와 동일 (악화 없음)

---

## 8. 다음 단계

`/pdca do d1-bgf-collector-import-fix` — 구현 시작
