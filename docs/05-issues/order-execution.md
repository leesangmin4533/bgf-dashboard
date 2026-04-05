# 발주 실행 이슈 체인

> 최종 갱신: 2026-04-06
> 현재 상태: 입수 데이터 불일치 → 과발주 조사 중

---

## [OPEN] product_details order_unit_qty 불일치 → 과발주 (04-06 ~)

**문제**: DB `product_details.order_unit_qty=1`인데 BGF 실제 입수가 16인 상품 존재. AI가 2개 예측 → PYUN_QTY=2, ORD_UNIT_QTY=1로 전송 → BGF가 최소 배수(3)로 올림 → 48개 발주.
**영향**: 49965점 농심)멸치칼국수사발컵 — 일 판매 1.1개인데 48개 발주 (43일치 과발주)
**설계 의도**: `_finalize_order_unit_qty`가 common.db에서 최신값 보정하지만, DB 자체가 부정확하면 무의미
**기여 KPI**: K2 (폐기율), K3 (발주 실패율)

### 범위 조사 (04-06)
- 면류(032) order_unit_qty=1: **51건** (전체 면류 중)
- 오늘 면류 발주 6건 중 2건 이상(멸치칼국수, 새우탕큰사발, 열라면컵)이 DB_unit=1
- product_details 수집 시 BGF `ORD_UNIT_QTY` 필드가 실제 입수와 다르게 수집되는 것으로 추정

### 근본 원인 후보
1. **수집 시점 문제**: product_detail_batch_collector가 selSearch API에서 ORD_UNIT_QTY를 읽을 때 묶음발주 상품은 1로 반환?
2. **BGF 데이터 구조**: 묶음발주 상품은 ORD_UNIT_QTY가 아닌 다른 필드(CASE_QTY, ORD_MUL_QTY 등)에 입수 저장?
3. **그리드 vs API 차이**: Selenium 그리드에서는 16이 보이지만 selSearch API에서는 1 반환?

### 대응 방향
1. **즉시**: 49965점 48개 발주 취소 또는 수량 조정 (수동)
2. **단기**: BGF 그리드에서 읽은 입수값과 DB값 비교 → 불일치 로깅 + DB 보정
3. **중기**: 수집 로직에서 묶음발주 상품의 입수를 정확히 가져오도록 수정

---

## [WATCHING] 행사 종료 임박 상품 발주 감량 자동화 (04-05 ~)

**목표**: promo_end_date - today <= 5일인 상품의 발주량을 자동 감소 또는 0 처리
**동기**: 행사(1+1 등) 종료 후 재고가 남으면 폐기 직결. 냉장고 사진 토론(03-30) 교훈: "1+1 종료 5일 전 감소" 규칙이 수동 판단에 의존 중
**설계 의도**: Stage 3(PromotionAdjuster)의 END_ADJUSTMENT 범위를 D-3→D-5로 확장
**기여 KPI**: K2 (폐기율)

### 시도 1: END_ADJUSTMENT D-5 확장 + 조건 상수화 (커밋 대기, 04-05)
- **왜**: 기존 D-3은 재고 소진에 시간 부족. D-5(85%), D-4(70%)로 점진 감량
- **결과**: 테스트 37개 통과 (기존 31 + 신규 6)
- **발견**: Plan에서 Stage 7(PROMO FLOOR)이 감량 덮어쓴다고 추정했으나, 실제로는 미동작 (qty>0 and qty<1 = 정수 불가)

### 교훈
- PROMO FLOOR(Stage 7) 코드가 사실상 dead code → 향후 정리 대상
- 케이스 1 조건의 매직넘버 3을 상수화(PROMO_END_REDUCTION_DAYS)하여 변경 용이

### 해결 검증
- [ ] 다음 행사 종료 상품에서 D-5~D-4 감량 로그 확인 (수동)
- [ ] 1주 운영 후 폐기 건수 비교

---
