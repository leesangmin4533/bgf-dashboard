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

### 근본 원인 (04-06 조사 완료)

**3단계 잠금 패턴**:
1. `direct_api_fetcher.py:138` — `_safe_int(row.get('ORD_UNIT_QTY', '1')) or 1` → 빈값→0→1
2. `product_detail_repo.py:479` — SQL CASE가 기존값 <=1이면 새값으로 교체하지만, 새값도 1이면 무의미
3. 한번 1이 들어가면 모든 수집 경로가 1을 "정상"으로 취급 → 복구 불가

**영향 범위**: 면류(032) 51건 + 다른 카테고리 미확인

### 시도 1: 수집 폴백 제거 + DB 리셋 (04-06)
- **왜**: `or 1` 폴백이 빈값→0→1로 변환하여 잘못된 값을 DB에 잠금
- **수정**:
  - `direct_api_fetcher.py:138`: `_safe_int(...) or 1` → `_safe_int(...) if > 0 else None`
  - `order_prep_collector.py:703`: JS `|| 1` → `|| null`
  - `order_prep_collector.py:526`: 신규 저장 시 `order_unit_qty: None`
  - 면류(032) 51건 → NULL 리셋
  - 짐빔하이볼(605) 4건 → NULL 리셋
  - **카테고리 내 이상치 일괄 519건** → NULL 리셋 (대다수 unit>1인데 unit=1인 상품)
- **결과**: 검증 대기 (스케줄: noodle-unit-qty-verify 04-07)

### 해결 검증
- [x] 내일(04-07) 07:00 발주에서 면류 상품 PYUN_QTY/ORD_UNIT_QTY 로그 확인 (완료: 04-07, 8801043017022 ORD_UNIT_QTY=16 AUDIT 확인)
- [~] 멸치칼국수사발컵 order_unit_qty가 NULL→16으로 갱신되었는지 확인 (04-07: 오늘 미발주, unit=1 유지 — BGF API가 1 반환 또는 수요 없음)

### 시도 1 부분 성공 결과 (04-07)
- NULL 51건 → 23건 (28건 갱신 성공, 57% 해결)
- 신라면툼바큰사발면(8801043017022): ORD_UNIT_QTY=16 → 배수 정상 발주 확인
- 멸치칼국수사발컵(8801043066556): unit=1 유지 (오늘 발주 없음 → BGF가 이 상품에 1을 반환하거나 수요 무발주)
- 잔여 NULL 23건: BGF API가 해당 필드를 반환하지 않는 상품이거나 아직 발주 미실행 상품

### 현황 (→ [WATCHING] 전환)
수정 코드는 정상 동작 중. 발주 발생 시 자동으로 갱신. 잔여 NULL은 지속 모니터링.

---

## [WATCHING] SLOW 주기 판매 상품 ROP=1 발주 (04-07 ~)

**검증 결과**: 2026-04-07 01:58~07:12, 4매장에서 periodic_slow(60d≥2회)+재고=0 상품이 ROP=1로 정상 발주됨
- 예: 면)콘버터야끼소바, 조이)헤이즐넛쫀득마카롱, 앱솔루트라즈베리375ml 등 다수

### 해결 검증
- [x] 04-07 발주에서 `[ROP] ...: periodic_slow(60d=N회) → 발주 1개` 로그 확인 (완료: 04-07, 다수 상품 확인)

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
