# batch-grid-validation 완료 보고서

## 개요

| 항목 | 내용 |
|------|------|
| 기능명 | Batch Grid/Selenium 발주 불가 상태 검증 추가 |
| 날짜 | 2026-03-27 |
| Match Rate | 100% |
| 수정 파일 | 1개 (order_executor.py) |
| 심각도 | Critical (3개 매장 전량 발주 미반영) |

## 문제 상황

3/27(금) 07:00 스케줄 실행 시 3개 매장(46513, 47863, 46704) 전부 발주 미반영.
- 시스템 리포트: "103건 성공, 0건 실패" (거짓 성공)
- 실제 BGF 반영: 0건

## 근본 원인

BGF 서버 `ordYn=''` (발주 불가 상태)에서:
- L1 (Direct API): ordYn 검증 → 정상 차단 → L2로 폴백
- L2 (Batch Grid): ordYn 검증 없음 → 그리드 입력+저장 → BGF 서버 무시 → "성공" 거짓 리포트
- L3 (Selenium): 동일하게 ordYn 미검증

## 수정 내용

### `src/order/order_executor.py`

1. **`_check_order_availability()` 신규 메서드**
   - `direct_api_saver.py`의 `CHECK_ORDER_AVAILABILITY_JS` 재사용
   - ordYn/ordClose 폼 변수 검사
   - 실패 시 빈 dict 반환 (기존 동작 유지)

2. **L2 Batch Grid 검증 추가** (`_try_batch_grid_input()`)
   - 그리드 입력 전 `_check_order_availability()` 호출
   - 발주 불가 시 `SaveResult(success=False)` 즉시 반환

3. **L3 Selenium 검증 추가**
   - 상품 입력 루프 진입 전 검증
   - 발주 불가 시 전체 items 실패 처리 + `total_fail` 카운트

## 발주 레벨별 ordYn 검증 현황

| 레벨 | 수정 전 | 수정 후 |
|------|--------|--------|
| L1 Direct API | ordYn 검증 있음 | 변경 없음 |
| L2 Batch Grid | **검증 없음** | ordYn 검증 추가 |
| L3 Selenium | **검증 없음** | ordYn 검증 추가 |

## Gap Analysis 결과

- Match Rate: 100%
- Gap: 0건
- 추가 개선: `avail and` 가드 (빈 dict 안전장치)
