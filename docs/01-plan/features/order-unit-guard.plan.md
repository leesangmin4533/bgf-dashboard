# Plan — order-unit-guard

**Feature**: order-unit-guard (발주입수 실시간 교차검증)
**Priority**: P1 (실물 과발주 진행 중)
**Created**: 2026-04-10
**Supersedes**: `order-unit-qty-integrity-v2` (카테고리 기반 → **전수 실시간 검증**으로 전환)
**Related Issue Chain**: `docs/05-issues/order-execution.md`
**Triggering Incident**: 2026-04-10 07:00 발주
- 46704 `8801094962104` 스프라이트제로P500 — DB입수=1, 실제=6 → PYUN=14 × 6 = **84개** (4일간 384개 누적)
- 46704 `2202000037309` 배달봉투특대 — DB입수=1, 실제=100 → PYUN=22 × 100 = **2,200개**
- 두 상품 mid_cd(044, 900)가 `BUNDLE_SUSPECT_MID_CDS`에 미포함 → 가드 무효

---

## 1. 배경 (Why)

v2는 "카테고리 기반 의심 목록(`BUNDLE_SUSPECT_MID_CDS`)"으로 접근. 목록에 없는 카테고리에서 입수 불일치가 발생하면 무방비. 실제로 mid=044(탄산), mid=900(소모품)에서 과발주 발생.

**근본 원인**: DB값만 신뢰하는 구조. BGF가 빈값을 주면 DB에 1이 고착 → 이후 검증 수단 없음.

## 2. 목표 (What)

**푸드(001~005, 012) 제외 전 상품에 대해, 발주 직전 BGF 실시간 입수와 DB 입수를 교차검증. 불일치 시 발주 차단 + 로그.**

### DoD
- [ ] order_prep에서 읽은 site_unit을 발주 dict에 `_site_order_unit_qty`로 전달
- [ ] 발주 제출 직전 `db_unit` vs `site_unit` 교차검증
- [ ] 낱개(1)/묶음(2~30)/Box(31+) 분류 로그
- [ ] 불일치 시: 발주 차단 + WARNING 로그 + site값으로 DB 자동 갱신
- [ ] site도 빈값이면: 발주 보류 + ERROR 로그
- [ ] 일치 시: 정상 발주 + DEBUG 로그
- [ ] 푸드 카테고리(001~005, 012)는 검증 제외

### 비목표
- BUNDLE_SUSPECT_MID_CDS 목록 확장 (이 feature로 대체)
- BGF API 빈값 반환 원인 조사
- product_details 전수 재수집 (별도 작업)

## 3. 범위 (Scope)

| # | 파일 | 변경 |
|---|------|------|
| 1 | `src/collectors/order_prep_collector.py` | site_unit을 반환 dict에 `_site_order_unit_qty`로 추가 |
| 2 | `src/order/auto_order.py` | order_prep 결과 → 발주 dict로 site_unit 전달 |
| 3 | `src/order/direct_api_saver.py` | `_verify_order_unit()` 교차검증 함수 신설 |
| 4 | `src/order/order_executor.py` | L3 Selenium 경로에도 동일 검증 |
| 5 | `src/settings/constants.py` | `FOOD_MID_CDS`, `ORDER_UNIT_VERIFY_ENABLED` 상수 |

## 4. 접근

order_prep 단계에서 **이미 BGF dsItem.ORD_UNIT_QTY를 읽고 있음**. 이 값을 발주 dict까지 전달하면 추가 API 호출 없이 교차검증 가능.

## 5. 리스크

| 리스크 | 대응 |
|--------|------|
| site_unit도 빈값일 때 발주 누락 | 로그+알림 후 다음날 재시도 |
| 푸드 외 낱개 상품 오차단 | 푸드 제외 + unit 양쪽 모두 1이면 통과 |
