# directapi-verify-fallback 완료 리포트

> **상태**: 완료
>
> **프로젝트**: BGF 리테일 자동 발주 시스템
> **버전**: 1.0
> **완료 날짜**: 2026-03-05
> **PDCA 사이클**: #1

---

## 1. 개요

### 1.1 프로젝트 정보

| 항목 | 내용 |
|------|------|
| 기능명 | directapi-verify-fallback (DirectAPI 검증 실패 시 폴백 트리거) |
| 시작일 | 2026-03-05 |
| 완료일 | 2026-03-05 |
| 소요기간 | 1일 (긴급 버그 수정) |
| 우선순위 | Critical |

### 1.2 결과 요약

```
+-----------------------------------------+
|  Match Rate: 100%                       |
+-----------------------------------------+
|  수정파일:   1개 (order_executor.py)      |
|  테스트:     15개 전부 통과 (+5개 신규)     |
|  이터레이션: 0 (1회 통과)                  |
+-----------------------------------------+
```

---

## 2. 문제 분석 (Plan/Design 대체)

### 2.1 발견 경위

- **일시**: 2026-03-05 07:00 (46513 점포 7시 스케줄)
- **증상**: 서울)뻥이요스낵100g, 오리온)태양의맛썬80g, 해태)허니버터칩60g 3개 상품이 배수(16/12/12) 대신 낱개(2)로 발주됨
- **영향**: 3개 상품 과소 입고 → 재고 부족 위험

### 2.2 근본 원인

| 단계 | 상세 |
|------|------|
| DirectAPI 제출 | 121건 제출, errCd=99999 (성공 반환) |
| 검증 결과 | **0/121건 일치**, 121건 전부 누락 |
| 시스템 처리 | WARNING 로그만 기록, **폴백 미실행** |
| 최종 결과 | BGF 사이트의 기존 값(원발주=2)이 그대로 유지 |

**코드 위치**: `order_executor.py:2359-2367` (`_try_direct_api_save()`)
- `verify_save()` 결과가 `verified=False`여도 `result.success=True`를 그대로 반환
- `execute_orders()`에서 `api_result.success=True`이므로 Level 2/3 폴백 분기를 통과

### 2.3 추가 발견

- `ordYn=, ordClose=` 빈 값 반환 → 발주 가능 여부 확인 불능 (별도 이슈)
- 검증 실패 시 order_tracking DB에는 예측 수량(16/12/12)이 기록됨 → DB와 실제 발주 불일치

---

## 3. 구현 상세

### 3.1 수정 파일

| 파일 | 변경 | 설명 |
|------|------|------|
| `src/order/order_executor.py` | 수정 | `_try_direct_api_save()` 검증 실패 처리 |
| `tests/test_order_executor_direct_api.py` | 추가 | 5개 테스트 (TestDirectApiVerifyFallback) |

### 3.2 수정 로직

```
verify_save() 결과 처리:

BEFORE:
  verified=False → WARNING 로그 → result.success=True (폴백 없음)

AFTER:
  matched=0 & total>0 → ERROR 로그 → result.success=False (폴백 트리거)
  matched>0 & verified=False → WARNING 로그 → result.success=True (기존 유지)
```

### 3.3 폴백 흐름

```
Level 1: DirectAPI → 검증 0/N → success=False
    ↓
Level 2: Batch Grid → 시도
    ↓ (실패 시)
Level 3: Selenium → 개별 상품 입력
```

---

## 4. 테스트 결과

### 4.1 신규 테스트 (5개)

| # | 테스트 | 시나리오 | 결과 |
|---|--------|---------|------|
| 1 | test_verify_zero_match_returns_failure | 0/N 전체 실패 → success=False | PASS |
| 2 | test_verify_partial_match_keeps_success | 일부 일치 → success=True 유지 | PASS |
| 3 | test_verify_success_keeps_success | 검증 성공 → success=True 유지 | PASS |
| 4 | test_verify_failure_triggers_batch_grid_fallback | 전체 실패 → Batch Grid 폴백 | PASS |
| 5 | test_chunked_skips_verification | 청크 분할 → 검증 스킵 | PASS |

### 4.2 기존 테스트 호환

- 기존 10개 테스트: 전부 PASS (영향 없음)
- 총 15/15 테스트 통과

---

## 5. Gap 분석 결과

| # | 요구사항 | 구현 | 테스트 | 상태 |
|---|---------|:---:|:---:|:---:|
| R1 | matched=0 → success=False | L2368 | test_verify_zero_match | ✅ |
| R2 | 부분 실패 → WARNING+True | L2380 | test_verify_partial | ✅ |
| R3 | 청크 → 검증 스킵 | L2361 | test_chunked_skips | ✅ |
| R4 | 폴백 체인 통합 | L2098 | test_fallback_triggers | ✅ |

**Match Rate: 100%**

---

## 6. 향후 과제

| # | 과제 | 우선순위 | 비고 |
|---|------|---------|------|
| 1 | `ordYn` 빈 값 원인 조사 | Medium | 발주 가능 여부 판단 불능 → 별도 검증 필요 |
| 2 | 검증 실패 시 order_tracking 미기록 | Low | 현재 검증 전에 tracking 기록됨 → 순서 조정 검토 |
| 3 | 부분 일치 임계값 도입 | Low | matched/total < 50% 시에도 폴백 고려 |

---

## 7. MEMORY.md 추가 항목

```
- **directapi-verify-fallback**: DirectAPI 검증 전체 실패(0/N) 시 폴백 트리거 (matched=0→success=False→L2/L3 폴백), 부분 실패는 WARNING 유지, Match Rate 100%, 15개 테스트
```
