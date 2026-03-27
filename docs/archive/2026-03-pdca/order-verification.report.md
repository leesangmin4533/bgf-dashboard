# order-verification Completion Report

> **Feature**: 발주 저장 검증 강화 + 발주현황 기반 pending 보정
> **Date**: 2026-03-26
> **Match Rate**: 84% (설계 차이는 구현이 더 합리적 → 설계 업데이트 권장)
> **Test**: 2293 passed, 0 new failures

---

## 1. 문제 요약

### 발생 사건 (2026-03-25~26, 46513 매장)

| 시점 | 이벤트 |
|------|--------|
| 3/25 07:38 | 세션1: Direct API/Batch Grid/Selenium 모두 실패 (no dataset) |
| 3/25 08:06 | 세션2: Direct API 전송 → errCd=99999 (성공 코드) |
| 3/25 08:06 | 검증: matched=0, missing=89 → **grid_replaced로 스킵 → false positive** |
| 3/26 07:30 | order_prep_collector: 미입고=0 반환 (단품별 화면 dsOrderSale 범위 한계) |
| 3/26 07:30 | adjuster: pending 1→0 차이 감지 → 재계산 → **qty=2 중복 발주** |

### 크롬 확장으로 확인된 실제 원인

BGF 발주현황 화면에서 3/25 발주가 **정상 접수**됨을 확인 (PYUN_QTY=1, ORD_INPUT_ID=단품별(재택)).
문제는 `order_prep_collector`가 사용하는 **단품별 발주 화면의 dsOrderSale 범위가 좁아서** 어제 발주 이력이 누락된 것.

---

## 2. 구현 내용

### Layer 1: Direct API 검증 강화

| 항목 | 내용 |
|------|------|
| 파일 | `src/order/direct_api_saver.py` |
| 변경 | `_verify_grid_after_save()`: missing>50% + 빈 그리드 → 실패 처리 |
| 효과 | false positive 차단 → Selenium 폴백으로 안전하게 전환 |

### Layer 2: ordYn 빈값 차단

| 항목 | 내용 |
|------|------|
| 파일 | `src/order/direct_api_saver.py` |
| 변경 | JS: `!ordYn \|\| ordYn.trim()===''` → available=false, Python: 즉시 SaveResult(False) 반환 |
| 효과 | 비정상 폼 상태에서 발주 시도 자체를 차단 |

### Layer 3: 발주현황 기반 pending 보정 (조정됨)

원래 설계는 "허위 기록 무효화"였으나, 크롬 확장 검증 결과 BGF에 정상 접수된 것으로 확인.
**양방향 대조 + pending 보정**으로 조정:

| 파일 | 변경 |
|------|------|
| `order_status_collector.py` | `collect_yesterday_orders()`: BGF 발주현황에서 어제 발주 수집 |
| `order_tracking_repo.py` | `reconcile_with_bgf_orders()`: BGF 확인→pending_confirmed, 미접수→무효화 |
| `order_tracking_repo.py` | `get_confirmed_pending()`: BGF 확인 미입고 수량 조회 |
| `daily_job.py` | Phase 1.96: 발주현황 재수집 + 양방향 대조 |
| `auto_order.py` | adjuster 전 confirmed pending 병합 (pending=0 보정) |

### 보정 흐름

```
Phase 1.96: BGF 발주현황 재수집
  → BGF에 있는 건: pending_confirmed=1
  → BGF에 없는 건: 무효화 (status='invalidated')

Phase 2 (발주):
  → order_prep_collector: pending=0 (단품별 화면 한계)
  → [NEW] confirmed pending 보정: BGF확인 pending으로 덮어쓰기
  → adjuster: 보정된 pending 사용 → 원래 qty 유지 → 중복 발주 방지
```

---

## 3. 수정 파일 목록

| # | 파일 | Layer | 변경 유형 |
|---|------|-------|----------|
| 1 | `src/order/direct_api_saver.py` | L1+L2 | 검증 강화 + ordYn 차단 |
| 2 | `src/collectors/order_status_collector.py` | L3 | `collect_yesterday_orders()` 신규 |
| 3 | `src/infrastructure/database/repos/order_tracking_repo.py` | L3 | `reconcile_with_bgf_orders()` + `get_confirmed_pending()` 신규 |
| 4 | `src/scheduler/daily_job.py` | L3 | Phase 1.96 통합 |
| 5 | `src/order/auto_order.py` | L3 | confirmed pending 병합 |

---

## 4. 테스트 결과

| 항목 | 결과 |
|------|------|
| 전체 테스트 | 2293 passed |
| 신규 실패 | 0건 |
| 기존 실패 | 7건 (ML feature count, schema version 등 — 수정 무관) |
| import 검증 | 전 모듈 정상 |

---

## 5. 추가 발견 사항 (별도 처리 필요)

### 5-1. 푸드류 Cap에 입고예정 미반영

`food_daily_cap`의 `total_cap` 계산 시 입고예정(pending) 수량을 차감하지 않음.
예: 햄버거 Cap=5, 입고예정=2 → 5개 발주 → 총 7개 → 과잉.

**상태**: 별도 수정으로 진행 예정.

### 5-2. order_prep_collector dsOrderSale 범위 한계

단품별 발주 화면의 dsOrderSale이 발주현황 화면보다 좁은 날짜 범위를 반환.
어제 발주가 포함되지 않아 pending=0으로 잘못 계산됨.

**상태**: Layer 3 pending 보정으로 우회 해결됨. 근본 해결은 향후 검토.

---

## 6. 방어 체계 요약

```
[발주 시점 방어]
  Layer 2: ordYn='' → 발주 차단 (비정상 폼 진입 방지)
  Layer 1: missing>50% + 빈 그리드 → 실패 (false positive 차단)

[다음날 방어]
  Layer 3: BGF 발주현황 재수집 → pending_confirmed 마킹
           → adjuster pending 보정 → 중복 발주 방지
           → BGF 미접수 건 무효화 → 허위 기록 제거
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-03-26 | Initial report |
