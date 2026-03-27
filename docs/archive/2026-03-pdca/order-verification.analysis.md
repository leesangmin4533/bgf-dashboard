# order-verification Gap Analysis Report

> **Date**: 2026-03-26
> **Design**: `docs/02-design/features/order-verification.design.md`
> **Match Rate**: 84%

---

## Overall Scores

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 82% | Warning |
| Architecture Compliance | 75% | Warning |
| Convention Compliance | 95% | Pass |
| **Overall** | **84%** | **Warning** |

---

## FR별 매칭 결과

| FR | Layer | 매칭률 | 주요 차이 |
|----|-------|:------:|----------|
| FR-01 | L1 검증 강화 | 70% | `missing>50%` 단독 → `missing>50% AND is_grid_empty` 복합 조건으로 변경 |
| FR-02 | L2 ordYn 차단 | 100% | 설계와 완전 일치 |
| FR-03 | L3-a 재수집 | 90% | 반환 필드 차이 (`delivery_type` 대신 `item_nm`, `ord_input_id`) |
| FR-04 | L3-b 무효화 | 90% | `reason` 필드 누락 (기능 영향 없음) |
| FR-05 | L3-c 통합 | 70% | `daily_order_flow.py` 대신 `daily_job.py`에 배치 |

---

## 주요 Gap 2건

### Gap 1: Layer 1 복합 조건 (FR-01)

- **설계**: `missing_ratio > 0.5` 단독으로 실패 처리
- **구현**: `missing_ratio > 0.5 AND is_grid_empty` (그리드도 비어야 실패)
- **이유**: 정상적 grid_replaced (다른 상품으로 리로드)에서 false negative 방지
- **판단**: 구현이 더 보수적, 3/25 사례는 정확히 커버됨. **설계 업데이트 권장**

### Gap 2: 통합 위치 (FR-05)

- **설계**: `daily_order_flow.py` (Application 계층)
- **구현**: `daily_job.py` Phase 1.96 (Scheduler 계층)
- **이유**: Phase 1.95에서 이미 발주현황 메뉴가 열려있어 세션 재사용이 효율적
- **판단**: 기능적으로 동일, 아키텍처 원칙상 차이. **설계 업데이트 권장**

---

## 권장 조치

**설계 문서를 구현에 맞춰 업데이트** (구현이 더 합리적인 선택):

1. Layer 1: 복합 조건 (`missing>50% AND is_grid_empty`) 반영
2. Layer 3-a: 반환 필드 (`item_nm`, `ord_input_id` 추가, `delivery_type` 제거) 반영
3. Layer 3-c: 통합 위치를 `daily_job.py` Phase 1.96으로 정정 + 사유 기록

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-03-26 | Initial analysis |
