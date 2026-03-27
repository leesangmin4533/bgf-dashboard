# Plan: directapi-verify-fix

## 문제 정의

DirectAPI gfn_transaction이 성공(errCd=99999)을 반환하지만, 직후 그리드 검증에서 **매번 0건 일치**로 판정되어 BatchGrid 폴백이 발생한다. 로그 분석 결과 3/10~3/11 이틀간 6회 연속, 전체 로그 기록에서 22회 모두 동일 패턴이다.

### 현상 (모든 매장, 매일 반복)
```
gfn_transaction 결과: success=True, added=70, errCd=99999
[DirectApiSaver] 검증: 0/70건 일치, 불일치=0, 누락=70
[DirectAPI] 검증 전체 실패 (0/70건 일치) → 폴백 트리거
[BatchGrid] 배치 저장 완료: 70건, 6239ms
```

### 영향
- **이중 저장 가능성**: gfn_transaction 성공 후 BatchGrid가 다시 저장 → 실질적으로는 outDS 덮어쓰기로 idempotent하지만 불필요한 6초 추가 소요
- **성능 낭비**: 매장당 6초 × 3매장 = 18초/일 불필요 소요
- **DirectAPI 무용화**: Level 1이 사실상 항상 실패 → 설계 의도와 불일치

## 근본 원인 분석

### 코드 흐름
1. `save_orders()` → gfn_transaction 호출 + 콜백 대기 → success=True 반환
2. `verify_save()` → 2초 대기 → gdList._binddataset 읽기 → 비교

### grid_replaced 안전장치 (line 1622)
```python
if matched == 0 and len(mismatched) == 0 and len(missing) == len(orders):
    return {'verified': True, 'skipped': True, 'reason': 'grid_replaced_after_save'}
```
**설계 의도**: gfn_transaction 콜백이 selSearch 리로드를 트리거하여 그리드 내용이 교체된 경우를 감지

**문제**: 로그 상 `불일치=0, 누락=70`이지만 이 조건이 트리거되지 않음

### 의심되는 원인 (우선순위순)

**A. 그리드 바인딩 불일치 (가장 유력)**
- verify_save JS: `workForm.gdList._binddataset` 읽기
- gfn_transaction 콜백 후 Nexacro가 gdList의 _binddataset을 다른 dataset으로 재바인딩
- 또는 _binddataset이 문자열(이름)을 반환 → _binddataset_obj 폴백 → 다른 dataset 참조
- 결과: verify가 읽는 dataset ≠ dsGeneralGrid

**B. selSearch 리로드 타이밍**
- origFnCallback이 비동기 selSearch 트리거 → 2초 내 미완료
- 그리드가 중간 상태 (부분 로딩, stale 데이터)
- grid_count > 0이지만 내용이 우리 주문과 무관

**C. dataset 참조 변경**
- outDS='dsGeneralGrid=dsGeneralGrid'로 서버 응답이 덮어쓴 후
- 콜백 체인에서 새 dsGeneralGrid 인스턴스 생성
- verify가 구(舊) 인스턴스 참조

## 수정 계획

### Fix A: 진단 로깅 추가 (direct_api_saver.py verify_save)
**목적**: grid_replaced 조건이 왜 트리거되지 않는지 정확히 파악

```python
# verify_save() 내부, grid_replaced 체크 직전에 추가
logger.info(
    f"[DirectApiSaver] 검증 상태: grid_count={grid_count}, "
    f"matched={matched}, mismatched={len(mismatched)}, "
    f"missing={len(missing)}, orders={len(orders)}, "
    f"grid_replaced_cond={matched == 0 and len(mismatched) == 0 and len(missing) == len(orders)}"
)
```

JS에 dataset 이름/타입 정보 추가:
```javascript
var dsName = workForm.gdList._binddataset;
var dsType = typeof dsName;
// ... items 추출 후
return {items: items, count: ds.getRowCount(), dsName: String(dsName), dsType: dsType};
```

### Fix B: dsGeneralGrid 직접 참조 방식으로 전환
**verify_save JS를 gdList._binddataset 대신 dsGeneralGrid 직접 참조로 변경**

```javascript
// 기존: let ds = workForm.gdList._binddataset;
// 변경: dsGeneralGrid 직접 접근
let ds = workForm.dsGeneralGrid;
if (!ds || typeof ds.getRowCount !== 'function') {
    ds = workForm.gdList._binddataset;
    if (typeof ds === 'string') {
        ds = workForm[ds];
    }
}
```

### Fix C: gfn_transaction 성공 시 검증 정책 개선
gfn_transaction이 errCd=99999를 반환하고 검증에서 `불일치=0` (즉, 수량 불일치가 아닌 순수 누락)인 경우, 이는 그리드 교체의 전형적 패턴이므로 성공으로 간주.

```python
# order_executor.py _try_direct_api_save 내부
verify = saver.verify_save(items)
if not verify.get('verified') and not verify.get('skipped'):
    matched = verify.get('matched', 0)
    total = verify.get('total', 0)
    mismatched_count = len(verify.get('mismatched', []))

    if total > 0 and matched == 0 and mismatched_count == 0:
        # 전체 누락 + 불일치 0 = 그리드 교체 패턴
        # gfn_transaction errCd=99999 이미 확인됨 → 성공 간주
        logger.info(
            f"[DirectAPI] 검증: 전체 누락({total}건) + 불일치 0건 "
            f"→ 그리드 교체 추정, gfn_transaction 성공 신뢰"
        )
        # result 유지 (success=True)
    elif total > 0 and matched == 0:
        # 불일치 존재 = 실제 저장 문제 가능성
        logger.error(
            f"[DirectAPI] 검증 전체 실패 (0/{total}건 일치, "
            f"불일치={mismatched_count}건) → 폴백 트리거"
        )
        result = SR(success=False, ...)
```

## 수정 대상 파일

| 파일 | 수정 내용 | 라인 |
|------|-----------|------|
| `src/order/direct_api_saver.py` | Fix A: 진단 로깅 + Fix B: dsGeneralGrid 직접 참조 | 1539-1580, 1618-1633 |
| `src/order/order_executor.py` | Fix C: 검증 정책 개선 (전체누락+불일치0 → 성공) | 2434-2451 |

## 구현 순서

1. **Fix A** (진단 로깅) — verify_save에 상세 로그 추가
2. **Fix B** (dsGeneralGrid 직접 참조) — JS 코드 수정
3. **Fix C** (검증 정책 개선) — order_executor.py 조건 분기 수정
4. 테스트 작성 — verify_save 반환값별 분기 테스트
5. 기존 테스트 회귀 확인

## 테스트 계획

### 단위 테스트 (신규)
- verify_save가 grid_count=0 → skipped 반환
- verify_save가 matched=0, mismatched=0, missing=all → skipped 반환
- verify_save가 matched=0, mismatched>0 → verified=False 반환
- order_executor: 전체누락+불일치0 → 폴백 안 함 (성공 유지)
- order_executor: 불일치 존재 → 폴백 트리거

### 회귀 테스트
- 기존 directapi-verify-fallback 테스트 15개 통과 확인
- 전체 테스트 스위트 통과

## 리스크

- **이중 저장 방지**: gfn_transaction 성공 신뢰 시 실제 저장이 안 됐을 경우 미발주 위험
  → **완화**: errCd=99999 확인 + `불일치=0` 조건으로 충분한 안전장치
- **BatchGrid 미실행**: DirectAPI가 실제 실패했는데 성공으로 처리
  → **완화**: mismatched > 0인 경우에만 폴백 유지 (수량 불일치 = 실제 문제)
