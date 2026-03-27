# Design: directapi-verify-fix

## 개요

DirectAPI gfn_transaction 성공(errCd=99999) 후 그리드 검증이 매번 0건 일치로 실패하여 불필요한 BatchGrid 폴백이 발생하는 문제를 수정한다. Plan 문서의 Fix A/B/C 3단계를 상세 설계한다.

## 현재 코드 분석

### verify_save() 흐름 (direct_api_saver.py:1528-1651)
```
sleep(2초)
→ JS: gdList._binddataset에서 items 읽기
→ grid_count==0 ? → skipped (grid_cleared)
→ 비교 루프 (matched/mismatched/missing)
→ matched==0 AND mismatched==0 AND missing==len(orders) ? → skipped (grid_replaced)
→ 최종 검증 결과 반환
```

### order_executor._try_direct_api_save() (order_executor.py:2383-2457)
```
save_orders() → success
→ verify_save() 호출
→ verified==False AND skipped==False ?
  → matched==0 ? → ERROR + SaveResult(success=False) → 폴백
  → matched>0 ? → WARNING only
```

### 문제의 핵심
- 테스트에서 grid_replaced 조건은 정상 작동 (test_direct_api_saver.py:325-360)
- 운영에서 `불일치=0, 누락=70` (= grid_replaced 조건 충족)인데 스킵 안 됨
- 가능 원인: pyc 캐시 stale, Nexacro JS 환경에서 dataset 참조 변경, 또는 orders 객체 변형

## 상세 설계

### 수정 1: verify_save JS — dsGeneralGrid 직접 참조 + 진단 정보

**파일**: `src/order/direct_api_saver.py` — verify_save 내 JS 블록 (line 1539-1575)

**변경 전**:
```javascript
let ds = workForm.gdList._binddataset;
if (!ds || typeof ds.getRowCount !== 'function') {
    ds = workForm.gdList._binddataset_obj;
}
if (!ds) return {error: 'no_dataset'};
```

**변경 후**:
```javascript
// 1순위: dsGeneralGrid 직접 참조 (gfn_transaction이 사용하는 dataset)
let ds = workForm.dsGeneralGrid;
let dsSource = 'dsGeneralGrid';

// 2순위: gdList 바인딩 폴백
if (!ds || typeof ds.getRowCount !== 'function') {
    ds = workForm.gdList._binddataset;
    dsSource = 'gdList._binddataset';
    // _binddataset이 문자열(이름)인 경우 → 실제 객체 접근
    if (typeof ds === 'string') {
        dsSource = 'gdList._binddataset[' + ds + ']';
        ds = workForm[ds];
    }
}

// 3순위: _binddataset_obj 폴백
if (!ds || typeof ds.getRowCount !== 'function') {
    ds = workForm.gdList._binddataset_obj;
    dsSource = 'gdList._binddataset_obj';
}
if (!ds) return {error: 'no_dataset'};

var items = [];
for (var i = 0; i < ds.getRowCount(); i++) {
    items.push({
        item_cd: ds.getColumn(i, 'ITEM_CD') || '',
        ord_qty: parseInt(ds.getColumn(i, 'PYUN_QTY') || ds.getColumn(i, 'ORD_MUL_QTY') || '0'),
    });
}
return {
    items: items,
    count: ds.getRowCount(),
    dsSource: dsSource,
    sampleItems: items.slice(0, 3).map(function(x){ return x.item_cd; })
};
```

**근거**: `gfn_transaction` 호출 시 `outDS='dsGeneralGrid=dsGeneralGrid'`로 dsGeneralGrid를 직접 사용하므로, 검증도 동일 dataset을 참조해야 일관성 확보.

### 수정 2: verify_save Python — 진단 로깅 추가

**파일**: `src/order/direct_api_saver.py` — verify_save (line 1577-1633 사이)

grid_data 파싱 후, 비교 루프 후에 진단 로그 추가:

```python
# grid_data 파싱 직후 (line 1581 뒤)
ds_source = grid_data.get('dsSource', 'unknown')
sample_items = grid_data.get('sampleItems', [])
logger.info(
    f"[DirectApiSaver] 검증 그리드: count={grid_count}, "
    f"source={ds_source}, sample={sample_items}"
)
```

```python
# 비교 루프 직후, grid_replaced 체크 직전 (line 1617 뒤)
logger.info(
    f"[DirectApiSaver] 검증 비교: matched={matched}, "
    f"mismatched={len(mismatched)}, missing={len(missing)}, "
    f"orders={len(orders)}, "
    f"grid_replaced_cond={matched == 0 and len(mismatched) == 0 and len(missing) == len(orders)}"
)
```

### 수정 3: order_executor — 검증 정책 보강

**파일**: `src/order/order_executor.py` — _try_direct_api_save (line 2434-2451)

order_executor 레벨에서 **이중 안전망** 추가. verify_save 내부의 grid_replaced 체크가 어떤 이유로든 실패하더라도, order_executor에서 동일 패턴(전체누락+불일치0)을 감지하면 성공으로 처리.

**변경 전**:
```python
verify = saver.verify_save(items)
if not verify.get('verified') and not verify.get('skipped'):
    matched = verify.get('matched', 0)
    total = verify.get('total', 0)
    if total > 0 and matched == 0:
        logger.error(...)
        result = SR(success=False, ...)
    else:
        logger.warning(...)
```

**변경 후**:
```python
verify = saver.verify_save(items)
if not verify.get('verified') and not verify.get('skipped'):
    matched = verify.get('matched', 0)
    total = verify.get('total', 0)
    mismatched_count = len(verify.get('mismatched', []))

    if total > 0 and matched == 0 and mismatched_count == 0:
        # 전체 누락 + 불일치 0건 = 그리드 교체 패턴
        # gfn_transaction errCd=99999 확인 완료 → 성공으로 간주
        logger.info(
            f"[DirectAPI] 검증: 전체 누락({total}건) + 불일치 0건 "
            f"→ 그리드 교체 추정, gfn_transaction 성공 신뢰"
        )
        # result 변경 없음 (success=True 유지)
    elif total > 0 and matched == 0:
        # 불일치 존재 = 수량이 다르게 저장됨 → 실제 문제
        logger.error(
            f"[DirectAPI] 검증 전체 실패 (0/{total}건 일치, "
            f"불일치={mismatched_count}건) → 폴백 트리거"
        )
        from src.order.direct_api_saver import SaveResult as SR
        result = SR(
            success=False,
            saved_count=0,
            method='direct_api',
            message=f'verification failed: 0/{total} matched, '
                    f'{mismatched_count} mismatched',
        )
    else:
        logger.warning(
            f"[DirectAPI] 저장은 성공했으나 검증 부분 실패: {verify}"
        )
```

**핵심 변경**: `matched == 0` 조건에 `mismatched_count == 0` 서브조건 추가
- `전체누락 + 불일치0` → 그리드 교체 (성공 유지)
- `전체누락 + 불일치>0` → 실제 저장 문제 (폴백 트리거)

### 수정 4: pyc 캐시 정리

**실행**: 배포 시 한 번

```bash
find bgf_auto/ -name '*.pyc' -delete
find bgf_auto/ -name '__pycache__' -type d -exec rm -rf {} +
```

## 수정 파일 요약

| # | 파일 | 수정 내용 | 라인 범위 |
|---|------|-----------|-----------|
| 1 | `src/order/direct_api_saver.py` | verify_save JS: dsGeneralGrid 직접참조 + dsSource/sampleItems 반환 | 1539-1575 |
| 2 | `src/order/direct_api_saver.py` | verify_save Python: 진단 로깅 2개소 | 1581, 1617 |
| 3 | `src/order/order_executor.py` | _try_direct_api_save: mismatched_count==0 이면 성공 유지 | 2434-2451 |

## 테스트 설계

### 신규 테스트 (test_order_executor_direct_api.py 확장)

**T1: 전체누락+불일치0 → 폴백 안 함**
```python
def test_verify_all_missing_no_mismatch_trusts_gfn(self, executor, sample_orders):
    """전체 누락 + 불일치 0건 → gfn_transaction 성공 신뢰, 폴백 안 함"""
    saver.save_orders.return_value = SaveResult(success=True, saved_count=3, method='direct_api')
    saver.verify_save.return_value = {
        'verified': False, 'matched': 0, 'total': 3,
        'mismatched': [], 'missing': ['A', 'B', 'C'],
    }
    result = executor._try_direct_api_save(sample_orders, '2026-03-11')
    assert result.success is True  # 폴백 없음
```

**T2: 전체누락+불일치>0 → 폴백**
```python
def test_verify_all_missing_with_mismatch_triggers_fallback(self, executor, sample_orders):
    """전체 누락 + 불일치 존재 → 폴백 트리거"""
    saver.verify_save.return_value = {
        'verified': False, 'matched': 0, 'total': 3,
        'mismatched': [{'item_cd': 'X', 'expected': 2, 'actual': 5}],
        'missing': ['B', 'C'],
    }
    result = executor._try_direct_api_save(sample_orders, '2026-03-11')
    assert result.success is False  # 폴백
```

**T3: dsGeneralGrid 직접 참조 (verify_save 단위)**
```python
def test_verify_uses_ds_general_grid(self, saver_with_template, mock_driver):
    """verify_save가 dsGeneralGrid를 직접 참조하는지 확인"""
    mock_driver.execute_script.return_value = {
        'items': [], 'count': 0, 'dsSource': 'dsGeneralGrid', 'sampleItems': [],
    }
    result = saver_with_template.verify_save([{'item_cd': 'X', 'final_order_qty': 1}])
    assert result['skipped'] is True  # grid_count==0 → grid_cleared
```

**T4: 진단 로깅 포함 검증**
```python
def test_verify_returns_ds_source(self, saver_with_template, mock_driver):
    """검증 결과에 dsSource 포함 확인"""
    mock_driver.execute_script.return_value = {
        'items': [{'item_cd': 'DIFF', 'ord_qty': 1}],
        'count': 1, 'dsSource': 'dsGeneralGrid', 'sampleItems': ['DIFF'],
    }
    orders = [{'item_cd': 'A', 'final_order_qty': 1, 'order_unit_qty': 1}]
    result = saver_with_template.verify_save(orders)
    assert result['skipped'] is True  # grid_replaced
```

### 기존 테스트 호환성

| 기존 테스트 | 영향 | 설명 |
|------------|------|------|
| test_verify_zero_match_returns_failure | **수정 필요** | mismatched=[] 포함 시 성공으로 변경됨 |
| test_verify_partial_match_warns | 영향 없음 | matched>0 이므로 기존 로직 동일 |
| test_verify_full_match_keeps_success | 영향 없음 | verified=True |
| test_verify_grid_replaced_after_save | 영향 없음 | 기존 스킵 로직 유지 |
| test_verify_grid_cleared_still_skipped | 영향 없음 | 기존 스킵 로직 유지 |
| test_verify_failure_triggers_batch_grid_fallback | 영향 없음 | API result가 success=False |

### test_verify_zero_match_returns_failure 수정

이 테스트는 `mismatched=[]`를 설정하는데, 새 로직에서는 이 경우 성공으로 간주됨. 테스트를 분기:
- 기존 테스트: mismatched에 1건 이상 추가 → 폴백 확인
- 새 테스트 T1: mismatched=[] → 성공 확인

## 구현 순서

1. `direct_api_saver.py` — JS 수정 (dsGeneralGrid 직접 참조 + dsSource)
2. `direct_api_saver.py` — Python 진단 로깅 2개소
3. `order_executor.py` — mismatched_count==0 분기 추가
4. `test_order_executor_direct_api.py` — T1~T4 추가 + 기존 테스트 수정
5. `test_direct_api_saver.py` — dsSource 포함 mock 반환값 업데이트
6. pyc 캐시 정리
7. 전체 테스트 실행 확인

## 예상 결과

수정 후 운영 로그:
```
[DirectApiSaver] 검증 그리드: count=150, source=dsGeneralGrid, sample=['8801234...', ...]
[DirectApiSaver] 검증 비교: matched=0, mismatched=0, missing=70, orders=70, grid_replaced_cond=True
[DirectApiSaver] 검증 스킵: 그리드 교체됨 (gfn_transaction 후 outDS 덮어쓰기+콜백 리로드 추정, grid_count=150, orders=70건)
```
또는 (grid_replaced가 여전히 트리거 안 될 경우):
```
[DirectApiSaver] 검증: 0/70건 일치, 불일치=0, 누락=70
[DirectAPI] 검증: 전체 누락(70건) + 불일치 0건 → 그리드 교체 추정, gfn_transaction 성공 신뢰
```

어느 경우든 **BatchGrid 폴백 발생하지 않음** → 매장당 6초 절감.
