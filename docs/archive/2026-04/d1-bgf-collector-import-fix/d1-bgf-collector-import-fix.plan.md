# Plan: D-1 부스트 발주 bgf_collector import 재발 수정 (d1-bgf-collector-import-fix)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: expiry-tracking.md (등록 예정)
> 마일스톤 기여: D-1 2차 배송 보정 정상화 — MEDIUM
> 발견 경로: 폐기 추적 모듈 점검 (2026-04-07)

---

## 1. 문제 정의

### 현상
매일 14:00 D-1 2차 배송 보정에서 49965 매장(부스트 대상이 있는 매장)이 동일 에러로 실패:

```
2026-04-06 14:00:24 [D1_49965] [D-1] Selenium 실행 실패 (store=49965): No module named 'src.collectors.bgf_collector'
2026-04-07 14:00:47 [D1_49965] [D-1] Selenium 실행 실패 (store=49965): No module named 'src.collectors.bgf_collector'
```

- 04-06: 47863, 49965 둘 다 실패 (boost_targets 1, 2)
- 04-07: 49965만 실패 (boost_targets=2). 47863은 boost_targets=0이라 Selenium 미진입

### 배경: 04-06 1차 수정과의 관계
이미 `bgf-collector-import-fix` 작업으로 `daily_job.py:931-934` 에서 BGFCollector → SalesCollector 교체 완료 (archive/2026-04). 그러나 **동일 에러가 04-07에 재현**.

### 근본 원인 (2가지)

#### 원인 A: `second_delivery_adjuster.py:441` 메서드명 오기 (정적 버그)
```python
order_result = executor.execute_single_order(item_cd=bo.item_cd, qty=bo.delta_qty)
```
- `OrderExecutor`에는 `execute_single_order()`가 **존재하지 않음** — 실제 메서드는 `execute_order(item_cd, qty, target_date=None)` (order_executor.py:1876)
- 호출 시 `AttributeError: 'OrderExecutor' object has no attribute 'execute_single_order'` 발생해야 함
- L460-462 `try/except`로 잡힘 → "BOOST 예외" 로그

#### 원인 B: 실행 중 scheduler가 옛 daily_job 모듈을 메모리에 캐시 (운영 이슈)
- `daily_job.py` L934는 이미 `from src.collectors.sales_collector import SalesCollector`로 수정됨 (04-06 1차 fix)
- 그런데 04-07 에러가 여전히 `bgf_collector` 메시지 → **현재 실행 중인 scheduler 프로세스가 04-06 1차 fix 이전 모듈을 메모리에 들고 있음**
- Python 모듈은 자동 reload되지 않음 → scheduler 재시작 없이 fix 미반영

→ **재현 메커니즘**: scheduler 메모리 모듈은 옛 BGFCollector import 시도 → ModuleNotFoundError → daily_job.py L951에서 catch → 메시지가 ModuleNotFoundError 그대로 표시

### 영향
- **49965 매장 D-1 부스트 발주 누락** (04-06부터 매일)
  - 04-06: boost_orders 1개 미실행
  - 04-07: boost_orders 2개 미실행
- 폐기/재고 부족 위험 (2차 배송 부스트가 폐기 직전 상품 보충 목적)
- 원인 A를 못 고치면 scheduler 재시작해도 동일 에러 재발 (다음번엔 진짜 AttributeError로)

---

## 2. 목표

### 1차 (정적 버그 수정)
- `second_delivery_adjuster.py:441` `execute_single_order` → `execute_order`로 수정
- target_date 파라미터 처리 검토 (D-1 부스트는 target_date 필요 여부)

### 2차 (운영 가이드)
- scheduler 재시작 절차 명문화
- code change → scheduler restart 자동화 검토 (선택)

### 3차 (검증)
- 49965 D-1 부스트가 실제로 발주 성공하는지 다음 14:00 또는 수동 재현으로 확인

---

## 3. 범위

### 대상 파일
- `src/analysis/second_delivery_adjuster.py` L441 — 메서드명 수정
- `tests/test_second_delivery_adjuster.py` (있는지 확인) — 회귀 테스트 추가
- `docs/05-issues/expiry-tracking.md` — 이슈 등록
- (선택) `docs/operations/scheduler-restart.md` 또는 README — 재시작 절차

### 비범위
- `OrderExecutor.execute_order` 자체의 동작 변경
- D-1 부스트 로직 재설계
- 다른 매장 사이드 이펙트 점검 (별도 작업)

---

## 4. 해결 방향

### 단계 1: 정적 버그 수정 (1줄)
```python
# Before
order_result = executor.execute_single_order(item_cd=bo.item_cd, qty=bo.delta_qty)
# After
order_result = executor.execute_order(item_cd=bo.item_cd, qty=bo.delta_qty)
```

### 단계 2: target_date 검토
- `execute_order(item_cd, qty, target_date=None)` — None이면 기본 동작
- D-1 부스트는 "오늘 추가 발주"이므로 target_date 미지정으로 충분 (선택일자=오늘)
- 만약 명시 필요하면 `target_date=result.today` 추가

### 단계 3: 회귀 테스트
- mock executor + boost_order 1개로 execute_order 호출 검증
- AttributeError 회귀 방지

### 단계 4: scheduler 재시작
- 운영 환경에서 `python run_scheduler.py` 프로세스 재시작 필요
- 14:00 작업 전 재시작 권장

---

## 5. 성공 조건

- [ ] `second_delivery_adjuster.py:441` 메서드명 수정
- [ ] 회귀 테스트 1개 추가 (execute_order 호출 검증)
- [ ] 17/17 + 1 = 18/18 테스트 통과
- [ ] scheduler 재시작 후 다음 14:00 D-1 작업에서 49965 success=True
- [ ] `data/d1_adjustment_log` 또는 logs에서 BOOST 완료 로그 확인

---

## 6. 리스크

- **scheduler 재시작 영향**: 진행 중인 작업 중단 가능. 14:00 작업 직전 재시작이 안전
- **target_date 미지정**: `select_order_day(None)`이 기본 오늘로 동작하는지 확인 필요 (Design 단계)
- **execute_order 사이드 이펙트**: 단품별 발주 메뉴 이동이 D-1 컨텍스트에서 정상 동작하는지 (이미 정상 발주 경로라 영향 미미 예상)

---

## 7. 이슈체인 등록 항목

`docs/05-issues/expiry-tracking.md`에 `[OPEN]` 블록 추가 (이번 Plan 작성과 동시):
- 문제 / 영향 / 근본 원인 A+B / 해결 방향 / 검증 체크포인트 4개

---

## 8. 다음 단계

`/pdca design d1-bgf-collector-import-fix` — Design 문서 작성 (target_date 파라미터 결정 + 회귀 테스트 케이스 정의)
