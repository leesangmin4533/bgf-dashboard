# PDCA Report: d1-bgf-collector-import-fix

> 완료일: 2026-04-07
> Match Rate: 100% — PASS
> 커밋: 85307b3
> 이슈체인: expiry-tracking.md#d-1-부스트-발주-execute-single-order-누락

---

## 핵심 요약

`second_delivery_adjuster.py:441`이 존재하지 않는 `OrderExecutor.execute_single_order()`를 호출 → 49965 매장 D-1 부스트 발주 매일 실패 → 폐기 직전 상품 보충 누락. 1줄 수정(`execute_order`) + spec mock 회귀 테스트 3개. 04-06 1차 fix(`bgf-collector-import-fix`)가 완결되지 못한 이유는 (1) 메서드명 오기 (2) scheduler 미재시작 두 가지였음.

---

## PDCA 사이클 요약

| 단계 | 결과 |
|------|------|
| **Plan** | 정적 버그(원인 A) + 운영 캐시(원인 B) 분리, 옵션 A(target_date=None) 채택 |
| **Design** | spec mock 패턴 도입 결정, 회귀 테스트 3 케이스 정의, 운영 캐시는 비범위 |
| **Do** | 1줄 수정 + 테스트 3개 + patch target lazy import 디버깅 |
| **Check** | Match Rate 100% (Gap 없음) |
| **Act** | 불필요 (≥90%) |

---

## 변경 사항

### 코드
- `src/analysis/second_delivery_adjuster.py:441` — `execute_single_order` → `execute_order` (1줄)
- `tests/test_second_delivery_adjuster.py` (신규) — `TestExecuteBoostOrders` 3개:
  - `test_calls_execute_order_not_execute_single_order` (회귀 핵심, `Mock(spec=OrderExecutor)`)
  - `test_handles_failure_increments_failed`
  - `test_empty_boost_orders_skips_executor_init`

### 문서
- `docs/05-issues/expiry-tracking.md` — `[OPEN]→[WATCHING]`, 시도 1, 교훈 3가지
- `CLAUDE.md` — 활성 이슈 테이블 갱신

---

## 검증 결과

### 자동 테스트
- 3/3 통과 (`pytest tests/test_second_delivery_adjuster.py`)

### 디버깅 발견
- patch target 첫 시도 `src.analysis.second_delivery_adjuster.OrderExecutor` 실패 → lazy import이라 모듈에 attribute 없음 → `src.order.order_executor.OrderExecutor`로 즉시 수정

### 잔여 라이브 검증
- [ ] scheduler 재시작 (운영자 수동)
- [ ] 다음 14:00 D-1 작업에서 49965 ModuleNotFoundError 소멸
- [ ] d1_adjustment_log executed=1 또는 BOOST 완료 로그 1건 이상

---

## 교훈

1. **`Mock(spec=Class)` 사용 강제**: Python은 메서드명 오기를 컴파일에서 못 잡음. spec mock으로 단위 테스트에서 잡아야 함. 이 패턴 없으면 prod에서 처음 발견됨
2. **운영 캐시 인지**: 코드 수정만으로 fix 완결 아님. long-running scheduler는 모듈 메모리 캐시 → 재시작 필수. 04-06 1차 fix가 무력화된 진짜 이유
3. **에러 메시지의 함정**: `ModuleNotFoundError: No module named 'src.collectors.bgf_collector'` 메시지가 1차 fix 후에도 떴지만 실제 원인은 메서드명 오기였음. 같은 메시지가 다른 원인을 가릴 수 있음
4. **lazy import 패턴 디버깅**: 함수 내부 import는 모듈 네임스페이스에 없으므로 patch target은 원 모듈 경로 사용

---

## 후속 작업 후보
- `scheduler-auto-reload`: 코드 변경 시 scheduler 자동 재시작 (watchdog/inotify 등) — Plan 작성 후보
- 폐기 추적 모듈 점검 시 발견된 다른 3건 (#1 ExpiryMgmt 프레임 접근 실패 / #3 49965 날짜 파싱 / #5 46704 gap=239)

---

## 관련 문서
- Plan: `docs/01-plan/features/d1-bgf-collector-import-fix.plan.md`
- Design: `docs/02-design/features/d1-bgf-collector-import-fix.design.md`
- Analysis: `docs/03-analysis/d1-bgf-collector-import-fix.analysis.md`
- Issue: `docs/05-issues/expiry-tracking.md#d-1-부스트-발주-execute-single-order-누락`
- 선행 작업: `docs/archive/2026-04/bgf-collector-import-fix/` (1차 fix, daily_job.py L934)
