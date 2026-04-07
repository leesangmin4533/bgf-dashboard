# 발주 실행 이슈 체인

> 최종 갱신: 2026-04-08
> 현재 상태: 묶음 가드 우회 + 푸드 체계적 과소예측 조사 중

---

## [WATCHING] 묶음 가드 우회 — 49965 햄/소시지·라면 과발주 (04-08 ~)

**문제**: 2026-04-08 07:00 49965점에서 2건 과발주
  - `8801392060632` CJ아삭한비엔나70g (mid=023 햄/소시지) — order_tracking `order_qty=5`
  - `8801043016049` 농심)짜파게티큰사발면 (mid=032 라면) — order_tracking `order_qty=1` (BGF 전송 후 3개 입고로 보고)
  46513 product_details 기준 두 상품 모두 `order_unit_qty=1` (49965 행은 미수집)

**설계 의도**: order-unit-qty-integrity-v2 긴급 가드(51cd670)가 묶음 의심 카테고리(`BUNDLE_SUSPECT_MID_CDS`)에서 unit≤1 발주를 차단하여 BGF 빈값 응답으로 인한 과발주를 막는 것
**기여 KPI**: K2 (폐기율), K3 (발주 실패율)

### 의심 원인
1. **mid=023 가드 누락**: 햄/소시지(023)는 묶음발주가 표준이지만 `BUNDLE_SUSPECT_MID_CDS`(constants.py:262)에 미포함 → 가드 미작동 → unit=1 그대로 전송
2. **L3 Selenium 경로 가드 미적용**: `_calc_order_result` 가드는 L1(Direct API)/L2(Batch Grid)에만 적용되고, L3(Selenium 그리드)은 line 1071에서 grid 읽기 실패 시 `actual_order_unit_qty=1` 폴백 → mid=032(suspect 포함)도 우회
3. **49965 product_details 미수집**: 두 상품 모두 `product_details`에 49965 store_id 행이 없음 → `_finalize_order_unit_qty`가 다른 매장 값(1)을 그대로 사용했을 가능성

### 검증 필요
- [ ] 04-08 07:00 49965 daily_job 로그에서 두 item_cd의 발주 경로(L1/L2/L3) + AUDIT 라인 확인
- [ ] `[BLOCK/unit-qty]` 알림 카톡 미수신 확인 (= 가드 안 탄 증거)
- [ ] BGF 그리드에서 두 상품 실제 ORD_UNIT_QTY 값 확인
- [ ] 49965 product_details에 두 item_cd 행이 없는지 확인

### 시도 1: BUNDLE_SUSPECT 식육가공 추가 + L3 가드 (04-08)
- **왜**: mid=023(햄/소시지) 가드 누락, L3 셀레니움 경로는 _calc_order_result 가드 미적용
- **수정**:
  - `constants.py:262` BUNDLE_SUSPECT_MID_CDS에 023, 024, 025 추가
  - `order_executor.py:input_product` actual_order_unit_qty 계산 직후 L3 가드 분기 추가 — products 테이블에서 mid_cd 조회 후 suspect+unit≤1 조건 만족 시 발주 거부 + 알림
- **결과**: pytest 71건 통과, syntax OK

### 해결 검증
- [ ] 04-09 07:00 49965 발주에서 8801392060632, 8801043016049 미발주 또는 unit>1 확인
- [ ] `[BLOCK/unit-qty L3]` 알림 카톡 수신 확인 (49965 product_details 미수집 상태가 유지되면 차단 발생해야 함)
- [ ] 49965 누락 상품 product_details 재수집 (별도 조치)

### 후속 조치 후보
- 49965 누락 상품 product_details 재수집 트리거
- product_detail_batch_collector에 매장별 누락 감지 추가

Issue-Chain: order-execution#bundle-guard-bypass-49965

---

## [WATCHING] 푸드 체계적 과소예측 — 도시락/김밥/샌드위치/햄버거 (04-08 ~)

**문제**: 2026-04-08 49965점 푸드 카테고리 예측이 7일 평균 판매 대비 일관되게 낮음
  - 001 도시락: 18건 중 13건 under, 평균 bias **-0.36**
  - 002 주먹밥: 27건 중 24건 under, 평균 bias **-0.42**
  - 003 김밥: 30건 중 **29건** under, 평균 bias **-0.63**
  - 004 샌드위치: 18건 중 17건 under, 평균 bias **-0.67**
  - 005 햄버거: 14건 중 **14건 전부** under, 평균 bias **-0.54**
  - 012 빵: 22건 중 16건 under, 평균 bias **-0.11**

**영향**: 푸드는 유통기한 1~2일 → 과소발주 시 즉시 품절. 고객 이탈 + 매출 손실 + sell_qty=0 누적으로 SLOW 오분류 악순환 (참고: food-stockout-misclassify)
**설계 의도**: FoodStrategy + AdditiveAdjuster + DemandClassifier가 일평균 판매를 따라잡아야 함. ML 앙상블·DiffFeedback이 추세 보정해야 함
**기여 KPI**: K1 (서비스율), K3 (발주 실패율)

### 의심 원인
1. **prediction-quick-wins(03-30) 부작용**: Rolling Bias + Stacking 100 도입 후 푸드 카테고리에 과도한 하향 편향
2. **DiffFeedback 페널티**: 최근 023/026/034/035/048 폐기율 상승(pending_issues.json) → DiffFeedback이 인접 카테고리까지 음의 보정
3. **food_waste_calibration 누적**: dessert-2week 단축 후 food 전체에 일괄 적용된 캘리브레이션 잔여 효과
4. **food_daily_cap 발동**: 요일평균+20% 버퍼 cap이 실수요보다 낮게 산출 (food-cap-qty-fix 이후 sum(qty) 기준으로 더 빠르게 도달)
5. **WMA 7일 가중**: 최근 며칠 폐기 우려로 ord_qty 자체가 줄어 sell_qty 상한이 낮아진 self-fulfilling 패턴

### 근본 원인 (04-08 조사 완료)

**WMA imputation 사각지대 — 만성 품절 푸드 상품**

49965 daily_sales 직접 분석:
- `8800271904593` (avg7=2.0, predicted=0): 14일 중 4일만 row 존재, **모든 row stock_qty=0**.
  4-05 sale=2 단 한 흔적 → WMA(7일)= 2/7 ≈ 0.28 → 발주 0
- `8800271905408` (avg7=1.0, predicted=0.34): 5일 row 모두 stock=0, sale=1.
  WMA = 5일 / 14일 ≈ 0.36

`base_predictor.py:314` 의 stockout imputation 코드는 `if available and stockout:` 조건이라
**윈도우 7일 전체가 품절(또는 미수집)인 만성 품절 상품은 imputation 미발동**.
원본 `(date,0,None)` 그대로 WMA에 들어가 거의 0이 되고 → 발주 0~1 → 추가 품절 → 악순환.

food-stockout-misclassify(이전 수정)는 "일부 품절"만 커버하고 "전체 품절"은 사각지대로 남음.

### 시도 1: WMA imputation 전체-품절 사각지대 처리 (04-08)
- **왜**: 윈도우 전체가 stockout인 만성 품절 상품도 부분 영업 중 판매 흔적(sale_qty>0 with stock=0)을
  수요 신호로 활용해 0일을 imputation 하면 WMA가 실제 수요에 근접
- **수정**: `base_predictor.py:calculate_weighted_average`
  - `available and stockout` → 기존 imputation 유지
  - `not available and stockout` 분기 신규: stockout 행 중 `sale_qty>0` 인 것을 nonzero_signal로 추출,
    평균을 0일 imputation 값으로 사용. nonzero_signal 없으면 보정 없이 0 유지(안전 폴백)
- **검증** (5 케이스 인라인 테스트):
  * A 만성품절 1sale=2 → WMA 2.000 (이전 0.28)
  * B fresh food None+nonzero → WMA 1.000 (이전 ≈0.4)
  * C mixed (기존 경로) → WMA 1.950 (회귀 없음)
  * D 비푸드 None → WMA 0.200 (회귀 없음, 기존 정책 유지)
  * E zero-signal → WMA 0.000 (안전 폴백)
- **회귀**: tests/ predictor·stockout·wma 키워드 = 20 fail / 166 pass.
  20 fail 모두 stash 기준 동일 (`promo_status` UnboundLocalError, `no such table: promotion_stats`) — 본 수정과 무관

### 해결 검증
- [ ] 04-09 07:00 49965 푸드 카테고리 predicted_qty가 avg7d 근처로 회복 확인
- [ ] 8800271904593, 8800271905408 발주량 ≥1 확인
- [ ] 1주 운영 후 푸드 mid 001~005 평균 bias 비교 (목표: -0.4 → 0 이내)
- [ ] [stockout-all-window] DEBUG 로그 발생 빈도 확인 (너무 많으면 만성 품절 상품 자체를 정리)

### 4매장 교차검증 (04-08, 사후)

가설 재검토를 위해 4매장 04-08 prediction_logs × daily_sales 그룹별 bias 측정:

| 매장 | all_stockout n | bias | has_stock n | bias |
|---|---|---|---|---|
| 46513 | 21 | **-0.54** | 21 | -0.26 |
| 46704 | 47 | **-0.54** | 31 | -0.39 |
| 47863 | 32 | **-0.49** | 39 | -0.22 |
| 49965 | 62 | **-0.71** | 67 | -0.22 |

- **all_stockout** (7일 전부 stock=0): 본 수정의 표적 그룹. 4매장 합계 162건, 평균 bias -0.49~-0.71.
  49965 62건 중 nonzero_signal 61건 → 패치가 정확히 표적에 작동.
- **has_stock** (1일 이상 재고): 본 수정 미적용 그룹. bias -0.22~-0.39 로 여전히 음수.
  → **2차 원인 별도 존재** (1차 원인의 절반 강도).

### 결론
- 본 이슈의 약 50% 비중(가장 심각한 부분)은 정확히 진단·수정됨
- 나머지 50% 잔존 underprediction 은 다른 메커니즘 (AdditiveAdjuster / DiffFeedback / outlier handler / ML 추세 / food_daily_cap 후보) → **별도 후속 이슈로 분리** 필요

### 후속
- [PLANNED] 푸드 has_stock 약한 음의 bias 잔존 — 별도 이슈로 등록 (2차 원인 조사)
- 만성 품절 상품 자동 정지(stop) 후보 리스트 별도 생성 검토

Issue-Chain: order-execution#food-systemic-underprediction

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
