# batch-grid-validation: Batch Grid 발주 불가 상태 검증 추가

## Context

3/27(금) 07:00 스케줄 실행 시 3개 매장 전부 발주가 실패했으나, 시스템은 "성공"으로 리포트했다.

**원인**: BGF 서버가 `ordYn=''` (발주 불가 상태)였는데:
- L1 (Direct API): `ordYn` 체크 → 정상 차단 → L2로 폴백
- L2 (Batch Grid): `ordYn` 체크 없음 → 그리드 입력+저장 버튼 클릭 → "103건 성공" 거짓 리포트
- 결과: 실제 BGF 반영 0건, 시스템 리포트 103건 성공 (거짓 성공)

## 수정 대상

### 1. `src/order/order_executor.py` — `_try_batch_grid_input()` (L:2468~2508)

Batch Grid 실행 전에 `ordYn` 검증 추가:

```python
def _try_batch_grid_input(self, items, order_date):
    # ... 기존 import/grid_status 체크 ...

    # [추가] 발주 가능 여부 확인 (Direct API와 동일한 JS 사용)
    avail = self._check_order_availability()
    if not avail.get('available', True):
        ord_yn = avail.get('ordYn', '')
        ord_close = avail.get('ordClose', '')
        logger.warning(
            f"[BatchGrid] 발주 불가 상태: ordYn='{ord_yn}', ordClose='{ord_close}'"
        )
        from src.order.direct_api_saver import SaveResult
        return SaveResult(
            success=False, method='batch_grid',
            message=f'form_not_available (ordYn={ord_yn}, ordClose={ord_close})',
        )

    # ... 기존 input_batch 호출 ...
```

### 2. `src/order/order_executor.py` — `_check_order_availability()` 신규 메서드

`direct_api_saver.py`의 `CHECK_ORDER_AVAILABILITY_JS` (L:77~149)를 재사용하는 공유 메서드:

```python
def _check_order_availability(self) -> dict:
    """발주 가능 여부 확인 (ordYn/ordClose 폼 변수 검사)"""
    try:
        from src.order.direct_api_saver import CHECK_ORDER_AVAILABILITY_JS
        result_str = self.driver.execute_script(f"return {CHECK_ORDER_AVAILABILITY_JS}")
        return json.loads(result_str) if result_str else {}
    except Exception as e:
        logger.debug(f"[ordYn체크] 확인 실패 (무시): {e}")
        return {}  # 확인 실패 시 통과 (기존 동작 유지)
```

### 3. `src/order/order_executor.py` — L3 Selenium 폴백에도 동일 검증 추가 (L:2206 부근)

L2가 `ordYn` 차단으로 실패 시 L3로 넘어가는데, L3에서도 동일하게 검증:

```python
# ── Level 3: 기존 Selenium 발주 (폴백) ──
# [추가] 발주 가능 여부 확인
avail = self._check_order_availability()
if not avail.get('available', True):
    logger.warning(f"[Selenium] 발주 불가 상태 → 발주 건너뜀")
    for item in items:
        results.append({...success: False, method: "selenium_blocked"...})
        total_fail += len(items)
    continue
```

## 수정 파일 요약

| 파일 | 변경 | 위치 |
|------|------|------|
| `src/order/order_executor.py` | `_check_order_availability()` 신규 메서드 | 클래스 내 |
| `src/order/order_executor.py` | `_try_batch_grid_input()` 에 ordYn 체크 추가 | L:2485 부근 |
| `src/order/order_executor.py` | L3 Selenium 진입 전 ordYn 체크 추가 | L:2206 부근 |

## 검증 방법

1. `python scripts/run_auto_order.py --run --store-id 46513 --max-items 3` 실행
2. 발주 불가 시간에 실행하면 L1/L2/L3 모두 `발주 불가` 로그 출력 확인
3. "성공: 0건" 정확히 리포트되는지 확인
4. 발주 가능 시간에는 기존과 동일하게 동작하는지 확인
