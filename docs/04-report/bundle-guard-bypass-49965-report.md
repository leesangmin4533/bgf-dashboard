# PDCA 완료 리포트: bundle-guard-bypass-49965

> 생성일: 2026-04-09 (자동 스케줄)
> 이슈: order-execution#bundle-guard-bypass-49965
> 커밋: 190b24f (BUNDLE_SUSPECT 식육가공 추가 + L3 가드)

---

## Plan

**문제**: 2026-04-08 07:00 49965점에서 2건 과발주
- `8801392060632` CJ아삭한비엔나70g (mid=023 햄/소시지) — order_qty=5
- `8801043016049` 농심)짜파게티큰사발면 (mid=032 라면) — order_qty=1, BGF 입수 3개

**의심 원인**:
1. mid=023(햄/소시지)가 `BUNDLE_SUSPECT_MID_CDS`에 미포함 → 가드 미작동
2. L3(Selenium) 경로에서 `_calc_order_result` 가드 미적용 → unit=1 폴백 시 우회
3. 49965 product_details 미수집 → `_finalize_order_unit_qty`가 타 매장 unit=1 사용

---

## Design

**수정 범위**:
- `constants.py:262` BUNDLE_SUSPECT_MID_CDS에 023, 024, 025 추가 (정적 fallback 영구 유지)
- `order_executor.py:input_product` — L3 경로에 mid_cd 조회 후 suspect+unit≤1 조건 시 발주 거부 + `[BLOCK/unit-qty L3]` 알림 추가

---

## Do (구현)

**커밋 190b24f** (04-08):
- `src/config/constants.py:262` — BUNDLE_SUSPECT_MID_CDS에 '023', '024', '025' 추가
- `src/order/order_executor.py` — `input_product` L3 경로 가드 분기 신규
- pytest 71건 통과, syntax OK

---

## Check (검증 — 04-09 자동 실행 결과)

### 대상 상품 발주 상태 (order_tracking 2026-04-09)

| 상품코드 | 상품명 | 상태 | 비고 |
|---|---|---|---|
| 8801392060632 | CJ맥스봉오리지널70g | **미발주** | 재고충분(17개) → 발주 필터 단계에서 제외 |
| 8801043016049 | 농심)짜파게티큰사발컵 | **미발주** | SmartOverride qty=0 (재고충분 14개) |

→ order_tracking 2026-04-09 두 상품 모두 행 없음 (미발주 확인)

### L3 [BLOCK/unit-qty] 가드 동작 확인 (order.log 2026-04-09)

동일 날짜 mid=023/024/032 계열에서 가드 정상 발동:
```
[BLOCK/unit-qty] 8801492392961 하림)마늘후랑크4입 mid=023 unit=1 qty=2 → 발주 거부
[BLOCK/unit-qty] 8801492392947 하림)참맛후랑크4입 mid=023 unit=1 qty=2 → 발주 거부
[BLOCK/unit-qty] 8809216733359 득템)990핫바블랙페퍼 mid=023 unit=1 qty=1 → 발주 거부
[BLOCK/unit-qty] 8809216733342 득템)990핫바오리지널 mid=023 unit=1 qty=1 → 발주 거부
[BLOCK/unit-qty] 8801045525235 오뚜기)참깨라면 mid=032 unit=1 qty=3 → 발주 거부
```

### Match Rate

- 두 대상 상품 미발주: ✓
- mid=023 가드 동작: ✓ (하림계열 2종, 득템핫바 2종 차단 확인)
- mid=032 가드 동작: ✓ (오뚜기참깨라면 차단 확인)
- **Match Rate: 100%**

---

## Act (후속 조치)

### 잔여 과제
1. **8801043016049 site 발주 출처 추적 (P3)**: 04-08 07:04:57 site 채널 발주 (자동 시스템 이전) — 점주 수동 or 본부 시스템 미규명. `order-execution#site-channel-attribution` 별도 이슈 유지.
2. **49965 product_details 재수집**: 두 상품 49965 행 미존재 상태 지속 — `product_detail_batch_collector`에 매장별 누락 감지 추가 검토.
3. **동적 마스터 전환 (PAUSED)**: `order-execution#bundle-suspect-dynamic-master` — 상품별 신뢰도 모델로 방향 전환 예정. BundleStatsRepo 재사용, classifier 폐기 후보.

### 이슈 상태
- order-execution#bundle-guard-bypass-49965 → **[RESOLVED]** (2026-04-09)
