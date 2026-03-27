# order-verification Design Document

> **Summary**: 발주 저장 검증 강화 + 발주현황 재수집을 통한 중복 발주 방지
>
> **Project**: BGF 자동 발주 시스템
> **Author**: AI
> **Date**: 2026-03-26
> **Status**: Draft
> **Plan Reference**: `docs/01-plan/features/order-verification.plan.md`

---

## 1. Design Overview

### 1.1 현재 문제 흐름

```
[3/25 세션1] Direct API 실패 → 브라우저 상태 오염
[3/25 세션2] 오염된 상태에서 Direct API 전송
  → ordYn='' (빈값) 인데 발주 가능으로 판단
  → gfn_transaction 호출 → errCd=99999 (성공 코드)
  → 검증: matched=0, missing=89 → grid_replaced 추정으로 스킵
  → order_tracking에 허위 기록 (remaining_qty=1)
[3/26] prediction: pending=1 vs BGF 실시간: pending=0
  → adjuster 재계산 → 중복 발주
```

### 1.2 수정 후 방어 흐름

```
[발주 시 - Layer 2] ordYn='' → 발주 불가 판정 → Direct API 중단 → Selenium 폴백
[발주 시 - Layer 1] 만약 통과해도 → matched=0, missing=89 → 실패 판정 → Selenium 폴백
[다음날 - Layer 3] BGF 발주현황 재수집 → order_tracking 대조 → 허위 기록 무효화
```

---

## 2. Layer별 상세 설계

### 2.1 Layer 1: Direct API 검증 강화

**수정 파일**: `src/order/direct_api_saver.py`
**수정 메서드**: `_verify_grid_after_save()` (L1656 부근)

#### 현재 로직 (L1656-1667)

```python
# matched=0, mismatched=0, missing=전체 → grid_replaced로 추정 → 성공 처리
if matched == 0 and len(mismatched) == 0 and len(missing) == len(orders):
    return {
        'verified': True,
        'skipped': True,
        'reason': 'grid_replaced_after_save',
        ...
    }
```

#### 변경 로직

```python
# missing 비율이 50% 초과면 무조건 실패 (grid_replaced 추정 무시)
missing_ratio = len(missing) / max(len(orders), 1)
if missing_ratio > 0.5:
    logger.warning(
        f"[DirectApiSaver] 검증 실패: missing={len(missing)}/{len(orders)} "
        f"({missing_ratio:.0%}) > 50% 임계치 (grid_replaced 추정 무시)"
    )
    return {
        'verified': False,
        'skipped': False,
        'reason': f'missing_ratio_{missing_ratio:.0%}_exceeds_threshold',
        'total': len(orders),
        'matched': matched,
        'missing': len(missing),
    }

# 기존 grid_replaced 로직 유지 (missing <= 50% 케이스)
if matched == 0 and len(mismatched) == 0 and len(missing) == len(orders):
    # 이 분기는 위 가드에서 이미 걸러지므로 도달 불가하지만 안전 유지
    ...
```

#### 호출 측 영향 (`_save_via_gfn_transaction`)

검증 실패 시 `SaveResult(success=False)` 반환 → `order_executor.py`에서 다음 레벨(Batch Grid/Selenium) 폴백.
**기존 폴백 경로 그대로 사용하므로 추가 수정 불필요.**

---

### 2.2 Layer 2: 발주 전 상태 검증 (ordYn)

**수정 파일**: `src/order/direct_api_saver.py`
**수정 위치**: `CHECK_ORDER_AVAILABILITY_JS` (L136) + `check_order_availability()` Python 핸들러

#### JS 수정 (L134-138)

```javascript
// 현재: ordYn='' → available=true (N이 아니니까)
// 변경: ordYn이 빈값이면 불가 (명시적 Y 계열만 가능)
var available = true;
if (!result.hasSession) available = false;

// [NEW] ordYn 빈값 = 폼 비정상 → 불가
if (!ordYn || ordYn.trim() === '') available = false;

if (ordYn === 'N' || ordYn === '0' || ordYn === 'false') available = false;
if (ordClose === 'Y' || ordClose === '1' || ordClose === 'true') available = false;
```

#### Python 핸들러 영향

`check_order_availability()` (L729-762)는 결과의 `available` 플래그만 사용하므로 JS 수정만으로 충분.
호출 측(L1101)에서 `available=False` → 저장 중단은 기존 로직 그대로.

단, 현재 L1101 부근에서 `available=False`여도 경고만 찍고 진행하는지 확인 필요:

```python
# 현재 동작 확인 필요:
avail = self.check_order_availability()
if not avail.get('available', True):
    logger.warning(...)
    # return 하는가? 아니면 계속 진행하는가?
```

**설계 결정**: `available=False`이면 `SaveResult(success=False, message='form_not_available')` 반환하여 즉시 폴백.

---

### 2.3 Layer 3: 발주현황 재수집 + order_tracking 대조

**수정 파일 3개**:
- `src/collectors/order_status_collector.py` — 재수집 메서드 추가
- `src/infrastructure/database/repos/order_tracking_repo.py` — 무효화 메서드 추가
- `src/application/use_cases/daily_order_flow.py` — 발주 전 재수집 호출

#### 3.1 재수집 메서드

**파일**: `src/collectors/order_status_collector.py`
**새 메서드**: `collect_yesterday_orders()`

```python
def collect_yesterday_orders(self, yesterday: str) -> List[Dict[str, Any]]:
    """BGF 발주현황 화면에서 어제 발주 목록 수집

    Args:
        yesterday: 'YYYY-MM-DD' (어제 날짜)

    Returns:
        [{item_cd, order_qty, order_date, delivery_type}, ...]
        BGF에 실제 접수된 발주만 포함
    """
```

**구현 전략**:
- 기존 `collect_order_status()` (L191-240)의 BGF 화면 접근 로직을 재사용
- 발주현황조회 화면에서 날짜를 어제로 설정
- `dsResult` 그리드에서 전체 목록 추출
- 반환 데이터: `{item_cd, order_qty(PYUN_QTY × ORD_UNIT_QTY), order_date}`

#### 3.2 order_tracking 대조 + 무효화

**파일**: `src/infrastructure/database/repos/order_tracking_repo.py`
**새 메서드**: `invalidate_unconfirmed_orders()`

```python
def invalidate_unconfirmed_orders(
    self,
    order_date: str,
    bgf_confirmed_items: Set[str],
    store_id: str
) -> List[Dict[str, Any]]:
    """order_tracking에 있지만 BGF에 없는 발주를 무효화

    Args:
        order_date: 대조 대상 날짜 (어제)
        bgf_confirmed_items: BGF에서 확인된 item_cd set
        store_id: 매장 코드

    Returns:
        무효화된 항목 목록 [{item_cd, item_nm, order_qty, reason}, ...]
    """
    # 1. order_tracking에서 해당 날짜의 auto 발주 조회
    #    WHERE order_date=? AND order_source='auto' AND remaining_qty > 0
    #
    # 2. BGF 확인 목록에 없는 건 필터
    #    item_cd NOT IN bgf_confirmed_items
    #
    # 3. 무효화: remaining_qty=0, status='invalidated'
    #
    # 4. 로그 경고 + 무효화 목록 반환
```

#### 3.3 호출 위치

**파일**: `src/application/use_cases/daily_order_flow.py`
**위치**: `run_auto_order()` 내 pending_clear 직후, prediction 전

```python
# 기존: pending_clear → prediction 시작
# 변경: pending_clear → [NEW: 발주현황 재수집 + 대조] → prediction 시작

# pending_clear 완료 후
if self.order_status_collector:
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        bgf_orders = self.order_status_collector.collect_yesterday_orders(yesterday)
        bgf_item_set = {o['item_cd'] for o in bgf_orders}

        invalidated = self.tracking_repo.invalidate_unconfirmed_orders(
            order_date=yesterday,
            bgf_confirmed_items=bgf_item_set,
            store_id=self.store_id
        )
        if invalidated:
            logger.warning(
                f"[발주정합성] {len(invalidated)}건 무효화 "
                f"(order_tracking에 있으나 BGF에 없음)"
            )
            for inv in invalidated:
                logger.warning(
                    f"  - {inv['item_nm']} qty={inv['order_qty']} "
                    f"(3/25 발주 미접수 → remaining_qty=0)"
                )
    except Exception as e:
        logger.warning(f"[발주정합성] 재수집 실패 (무시, 기존 데이터 유지): {e}")
```

---

## 3. 파이프라인 변경 요약

```
변경 전:
  pending_clear → prediction → Floor/CUT/Cap → 미입고조정 → 발주실행(Direct API→검증→저장)

변경 후:
  pending_clear
  → [NEW] 발주현황 재수집 + order_tracking 대조 (Layer 3)
  → prediction
  → Floor/CUT/Cap → 미입고조정 → Cap재적용
  → 발주실행
      → [MOD] ordYn 빈값 차단 (Layer 2)
      → Direct API 전송
      → [MOD] missing>50% 실패 처리 (Layer 1)
      → 저장
```

---

## 4. 수정 파일 목록

| # | 파일 | 수정 유형 | Layer | 설명 |
|---|------|----------|-------|------|
| 1 | `src/order/direct_api_saver.py` | 수정 | L1 | `_verify_grid_after_save()`: missing>50% 실패 처리 |
| 2 | `src/order/direct_api_saver.py` | 수정 | L2 | `CHECK_ORDER_AVAILABILITY_JS`: ordYn 빈값 불가 |
| 3 | `src/collectors/order_status_collector.py` | 추가 | L3 | `collect_yesterday_orders()` 신규 메서드 |
| 4 | `src/infrastructure/database/repos/order_tracking_repo.py` | 추가 | L3 | `invalidate_unconfirmed_orders()` 신규 메서드 |
| 5 | `src/application/use_cases/daily_order_flow.py` | 수정 | L3 | 발주 전 재수집 호출 추가 |

---

## 5. 구현 순서

| 순서 | 작업 | 의존성 | 예상 영향 |
|------|------|--------|----------|
| **1** | Layer 1: `_verify_grid_after_save()` missing 임계치 | 없음 | false positive 차단 |
| **2** | Layer 2: `CHECK_ORDER_AVAILABILITY_JS` ordYn 검증 | 없음 | 비정상 폼 차단 |
| **3** | Layer 3-a: `collect_yesterday_orders()` | 없음 | BGF 발주현황 수집 |
| **4** | Layer 3-b: `invalidate_unconfirmed_orders()` | 3-a | DB 무효화 |
| **5** | Layer 3-c: `daily_order_flow.py` 호출 통합 | 3-a, 3-b | 파이프라인 연결 |
| **6** | 테스트 + 로그 검증 | 전체 | 기존 테스트 통과 확인 |

---

## 6. 테스트 전략

### 6.1 Layer 1 테스트

- missing=100% (89/89) → `verified=False` 확인
- missing=30% (26/89) → 기존 로직 유지 (grid_replaced 판단) 확인
- matched=89/89 → `verified=True` 확인 (정상 케이스)

### 6.2 Layer 2 테스트

- `ordYn=''` → `available=False` 확인
- `ordYn='Y'` → `available=True` 확인
- `ordYn='N'` → `available=False` 확인 (기존 동작 유지)

### 6.3 Layer 3 테스트

- BGF에 5건, order_tracking에 7건 → 2건 무효화 확인
- BGF 재수집 실패 → order_tracking 변경 없음 (보수적) 확인
- 당일 발주분은 대조 대상 아님 (어제 날짜만) 확인

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-26 | Initial design | AI |
