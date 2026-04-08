# 발주 실행 이슈 체인

> 최종 갱신: 2026-04-09
> 현재 상태: 묶음 가드 우회 해결 + 상품별 신뢰도 모델 계획 중 + 푸드 과소예측 조사 중

---

## [PLANNED] 상품별 묶음 신뢰도 모델 (P2, 04-09 ~)

**문제**: 카테고리 단위 bundle_pct 분류(bundle-suspect-dynamic-master)는 개별 상품 위험을 카테고리 평균으로 희석함. mid=023 bundle_pct=5.5% 이지만 실제 과발주 상품은 unit>1 이었다.
**사용자 비판 (04-08)**: "동적 마스터 재계산보다 product_details 직접 참조가 더 단순/정확"
**결론**: 카테고리 set 접근 폐기 → 상품별 신뢰도 모델(4개 신호 가중 합산)로 전환
**우선순위**: P2
**설계 의도**: 발주 시 item_cd 단위로 order_unit_qty 신뢰도를 직접 계산 → 임계값 미달 시 BLOCK
**기여 KPI**: K3 (발주 실패율)
**Plan 문서**: docs/01-plan/features/bundle-confidence-model.plan.md
**선행 조건**: bundle-suspect-dynamic-master SUPERSEDED (04-09 검증 통과 확인 완료)

### 신뢰도 신호 (4개)
1. cross-store 일치성 — 4매장 product_details unit_qty 비교
2. sibling 일치성 — 같은 mid 형제 상품 median unit_qty 비교
3. 가격대 — 1,500원 미만 묶음 가능성 낮음
4. BGF API NULL 비율 — 수집 결함 신호

### Design 단계 결정 항목 (6개)
→ `/discuss bundle-confidence-model` 로 토론 권장

### 검증 체크포인트
- [ ] 04-08 사고 상품(8801392060632) 신뢰도 < 임계값 확인
- [ ] 정적 fallback 회귀 없음 확인
- [ ] false positive < 5% 확인 (담배/맥주 정상 묶음 BLOCK 여부)

Issue-Chain: order-execution#bundle-confidence-model

---

## [SUPERSEDED] 묶음 가드 정적 리스트 → 동적 마스터 전환 (P2, 04-08 ~ 04-09)

**04-09 대체**: 카테고리 분류 접근 자체를 폐기하고 상품별 신뢰도 모델로 전환 결정.
bundle-suspect-dynamic-master 에서 구현된 BundleStatsRepo 는 신뢰도 모델에서 재사용.
bundle_classifier.py 는 폐기 후보 (Design 단계 확정 후).

## [PAUSED → SUPERSEDED] 묶음 가드 정적 리스트 → 동적 마스터 전환 (P2, 04-08 ~)

**04-08 일시 중단**: Step 1~3 완료 후 사용자 비판 — "상품별 직접 참조 / 신뢰도 모델이 더 단순/정확하지 않은가?" — 본질적 통찰. 1차 수정(190b24f)으로 당장 사고는 차단된 상태이므로 04-09 검증 통과 확인 후 **상품별 신뢰도 모델 (cross-store + sibling 비교)** 로 방향 전환 예정. Step 1~3 의 BundleStatsRepo 는 신뢰도 모델에서 재사용 가능, classifier 는 폐기 후보.



**문제**: `BUNDLE_SUSPECT_MID_CDS` 가 정적 set 으로 관리됨. 04-06~04-08 사고 5단계가 모두 반응형 패치(사고 → 카테고리 추가)였고, 04-08 mid=023 누락도 동일 패턴.
**우선순위**: P2
**설계 의도**: BGF DB 의 `order_unit_qty` 분포를 동적으로 읽어 가드 대상을 산출하여 반응형 패치 사이클 종료
**기여 KPI**: K3 (발주 실패율)

### 메타 원인 (정밀분석 결과)
data/discussions/20260408-bundle-analysis/정밀분석.md §4 참조.
- 반응형 패치 사이클: 음료/주류 사고 → 그 카테고리만 추가 → 식육가공 누락
- 카테고리 마스터 부재: BGF 묶음발주 표준 mid 정리 안 됨
- mid 별 회귀 테스트 부재
- DB 자동 점검 잡 부재

### 후속 작업
- `/pdca plan bundle-suspect-dynamic-master` 진행 중

### 시도 1 (04-08, Step 1~3): Domain/Infra/Test 신규 — 안전 영역
- **변경**: bundle_classifier.py + bundle_stats_repo.py + test_bundle_classifier.py 신규
- **결과**: pytest 30 PASS, 라이브 smoke test 정상 동작 (74 mid 분류)
- **dynamic 결과**: STRONG 16, WEAK 5, UNKNOWN 12, NORMAL 41
- **합집합 (dynamic STRONG/WEAK ∪ static fallback)**: 28 mid (정적 23 + 신규 10 - 중복 5)

### ★ Step 1~3 중 발견된 설계 결함 (04-08)

**bundle_pct 단독 기준이 진짜 위험을 놓침**

| mid | total | bundle | unit1 | bundle_pct | 현재 분류 | 실제 위험 |
|---|---|---|---|---|---|---|
| **023 햄/소시지** | 183 | 10 | **173** | **5.5%** | NORMAL ❌ | unit1=94.5% (BGF 빈값 다수) |
| 006 라면 | 44 | 0 | 1 | 0% | UNKNOWN | NULL=91% |
| 014 과자 | 281 | 47 | 11 | 17% | UNKNOWN | NULL=66% |

→ 정적 fallback (190b24f 추가한 023~025) 이 없었다면 dynamic 만으로 023 사고 재발 가능.
→ 정적 fallback 영구 유지(토론 결정 5 A안) 의 가치가 데이터로 강력 입증됨.
→ **Design 보강 필요**: BundleClassifier 에 `unit1_ratio` 기반 의심 신호 추가 검토 (04-09 검증 후)

Issue-Chain: order-execution#bundle-suspect-dynamic-master

---

## [PLANNED] 8801043016049 site 발주 출처 추적 (P3, 04-08 ~)

**문제**: 49965 04-08 8801043016049 가 `order_source='site'`, `created_at='07:04:57'` 로 자동 시스템 시작 전에 발주됨. 자동 시스템(07:26)은 BLOCK 으로 정상 차단했음에도 3개 입고 발생.
**우선순위**: P3
**설계 의도**: site 채널 발주의 출처(점주 수동 vs 본부 시스템)를 명확히 구분하여 책임 소재 규명
**기여 KPI**: 없음 (조사)

### 사실
- order_tracking id=3947, order_source='site'
- created_at=07:04:57 (daily_job 자동 발주는 07:26부터)
- BGF 묶음 unit=3 적용으로 3개 도착 추정

### 검증 필요
- [ ] BGF 사이트 STBJ030 ord_input_id 로 입력자 식별 가능 여부
- [ ] manual_order_detector 로그에 동시각 기록 있는지
- [ ] 점주 인터뷰 (정말 1을 입력했는지)
- [ ] 본부 일괄 발주 시스템에서 같은 시각 푸시 있었는지

Issue-Chain: order-execution#site-channel-attribution

---

## [RESOLVED] 묶음 가드 우회 — 49965 햄/소시지·라면 과발주 (04-08 ~ 04-09)

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
- [x] 04-09 07:00 49965 발주에서 8801392060632, 8801043016049 미발주 확인
  - 8801392060632: 재고충분(17개) → order_tracking 04-09 미생성, 발주 0 (L3 도달 전 필터)
  - 8801043016049: SmartOverride qty=0 주입(재고충분 14개) → order_tracking 04-09 미생성
  - L3 [BLOCK/unit-qty] 가드: 동일 날짜 mid=023(하림마늘후랑크4입, 하림참맛후랑크4입, 득템핫바2종), mid=032(오뚜기참깨라면), mid=014, mid=015, mid=019에서 정상 발동 확인
- [ ] `[BLOCK/unit-qty L3]` 알림 카톡 수신 확인 (재고 충분으로 L3 미도달 — 다음 재고 소진 시 재검증 필요)
- [ ] 49965 누락 상품 product_details 재수집 (별도 조치, 잔여 P3)

**결론 (04-09)**: 두 상품 모두 04-09 미발주 확인. 가드는 mid=023/032 내 다른 상품에서 정상 동작. 과발주 사고 재발 없음 → RESOLVED.

### 후속 조치 후보
- 49965 누락 상품 product_details 재수집 트리거
- product_detail_batch_collector에 매장별 누락 감지 추가

Issue-Chain: order-execution#bundle-guard-bypass-49965

---

## [WATCHING] 푸드 has_stock 그룹 약한 과소예측 — 2차 원인 (04-08 ~)

**문제**: food-systemic-underprediction 1차 수정(ab98bfc) 후에도 has_stock 그룹(재고 있던 날 존재) 158건이 여전히 평균 bias **-0.22 ~ -0.39** 로 음수. 1차의 절반 강도지만 시스템 전반에 잔존.
**우선순위**: P2
**설계 의도**: 재고가 있었던 날은 imputation 정상 작동 → 예측이 실수요 근처여야 함
**기여 KPI**: K1 (서비스율)

### 사전 조사 (04-08)

**예시 1: 8800279678588 (샌드위치, 49965)**
- 7일 윈도우: 5일 stock>0 + 2일 stock=0, sale 합 5
- 이론 imputation 후 WMA = 1.0 (avg_avail × 7일)
- 실제 `predicted_qty=0.78`, `adjusted_qty=1.21`, `weekday_coef=1.18`, `association_boost=1.08`
- → imputation 후에도 0.78 로 출력 → **WMA 후속 단계 또는 가중치에서 추가 -22% 발생**

**예시 2: 8800336392501 (도시락, 49965)**
- 7일 모든 day row 존재, **전부 `stock_qty=-1`** (음수 sentinel)
- imputation 코드는 `stk>0` available, `stk==0` stockout 만 처리 → -1 은 어느 그룹에도 속하지 않음 → 보정 미적용
- sum=10/7=1.43, predicted=1.23 → -14% bias
- 매장별 stock_qty<0 비율: 46513=2, 46704=24, 47863=26, 49965=45 (14일치)
  → 영향 적지만 명확한 사각지대

### 의심 원인 후보
1. **WMA 가중치** — 최근일 가중치가 더 크고, 최근일이 우연히 낮으면 평균 이하
2. **outlier_handler** 가 정상 sale 을 outlier 로 클립하여 하향
3. **holiday_wma_correction** — 비휴일에도 가중치 감쇄가 적용되는 경계 케이스
4. **food/dessert 곱셈 체인의 음의 계수** — base × holiday × weather × weekday × season × assoc × trend 어딘가에서 < 1.0
5. **weekday_coef** — 04 샌드위치 0.84 처럼 일부 카테고리/요일 조합이 -16% 까지 적용
6. **stock_qty=-1 sentinel** 미인식 — imputation 사각지대 (영향 작음)
7. **prediction_logs 컬럼 미채움** — `stage_trace`, `rule_order_qty`, `ml_order_qty`, `ml_weight_used` 가 NULL → 단계별 추적 불가가 디버깅 자체를 막음

### 검증 필요
- [x] stage_trace 컬럼 채우기 (예측 단계별 값 로깅 활성화) — 시도 1 (04-08, ultraplan-B)
- [ ] 8800279678588 에 대해 라이브 predict 실행하여 WMA→base→adjusted 단계별 값 추출 (04-10 1주 관측 시작)
- [ ] WMA 가중치 분포 확인 (최근일 weight 비중)
- [ ] outlier_handler 가 푸드 카테고리에 활성화돼 있는지 + clip 발동률
- [ ] mid_cd 별 weekday_coef 분포 (어느 카테고리/요일 조합이 < 1.0 인지)
- [x] stock_qty<0 sentinel 처리 정책 정의 — 시도 1 (04-08, ultraplan-B)

### 시도 1: stage_trace 5단계 가시화 + stock_qty<0 sentinel 정규화 (04-08, ultraplan-B)
**왜**: 04-08 토론 결정(B-C-B 조합) — 7가설 모두 데이터 부재로 검증 불가능한 봉쇄 상태.
관측성 인프라(stage_trace) 없이 표적 패치하면 효과 분리 불가 → A안(블라인드 다발 패치) 퇴행 위험.
**조치**:
1. **Schema 드리프트 복구 (v76)** — `prediction_logs.stage_trace TEXT`, `association_boost REAL`
   2개 컬럼이 `_STORE_COLUMN_PATCHES` 와 CREATE TABLE 양쪽에 누락. prediction_logger.py
   는 PRAGMA 체크로 silent skip 중이었음. (`schema.py` 양쪽 + `constants.py` v75 → v76)
2. **upstream stage 캡처** — `improved_predictor.predict()` 의 2단계(WMA) 와 3단계(계수)
   사이에 `self._stage_upstream` 인스턴스 변수로 `base_wma`, `wma_blended`, `coef_mul` 기록.
3. **food mid 한정 5단계 스냅샷** — `_compute_safety_and_order` 의 `_snapshot_stages`
   빌드 시점에 mid_cd ∈ ('001'~'005','012') 일 때만 upstream 키 + `food_5stage` 딕셔너리
   추가 (DB 부담 절감, 모집단 158건이 푸드에 한정).
4. **stock_qty<0 sentinel 정규화** — `base_predictor.calculate_weighted_average` 진입 직후
   `stk < 0 → None` 로 정규화. 기존 imputation 분기(`stk > 0`, `stk == 0`)는 음수를 보정 사각지대로
   남겼고, 4매장 14일치 97 row 가 영향. 정규화 후 food mid 는 ab98bfc 1차 fix 의 nonzero_signal
   경로를 통과 (예시 2: 8800336392501 케이스).
5. **회귀 테스트** — `tests/test_food_stage_trace.py` 신규 9개. 사전 존재 fail 격리.
   * 부분 -1 → None 처리 ✓
   * 전체 -1 + nonzero_signal → 1차 fix 트리거 ✓
   * 비푸드 mid 안전 폴백 ✓
   * sentinel 없는 정상 데이터 회귀 없음 ✓
   * food mid 5단계 스냅샷 키 6개 ✓
   * 비푸드 mid upstream 키 미포함 ✓

**결과**:
- 9/9 신규 테스트 통과
- 인접 푸드 테스트(test_food_underorder_fix, test_batch_sync_zero_sales_guard 등) 회귀 0건
- 사전 존재 fail (test_improved_predictor / test_cold_start_fix `realtime_inventory` 픽스처 누락,
  test_food_prediction_fix `bgf_sales.db` 경로 가정) 은 본 수정과 무관
- compile/import OK, schema migration in-memory 검증 OK
**실패 패턴**: (해소) `#schema-drift` `#observability-blocked-investigation`

### 해결 검증 (04-10 1주 관측 시작)
- [ ] 04-09 23:00 OneDrive 동기화 후 4매장 매장 DB 에 v76 마이그레이션 적용 확인
  (`PRAGMA table_info(prediction_logs)` 에 `stage_trace`, `association_boost` 존재)
- [ ] 04-10 07:00 daily_job 후 prediction_logs.stage_trace 가 푸드 mid 행에 NOT NULL
  (`SELECT COUNT(*) FROM prediction_logs WHERE mid_cd IN ('001'..'005','012') AND stage_trace IS NOT NULL`)
- [ ] 04-10~04-16 1주 관측 → has_stock 158건 그룹의 5단계 분포 추출
- [ ] 단일 지배 stage 식별 (target: bias 의 70% 이상을 설명하는 1개 stage)
- [ ] 8800336392501 (sentinel 케이스) 의 imputation 발동 → WMA -14% bias → 0% 이내 회복
- [ ] 04-17 표적 패치 plan 작성 (별도 PDCA)

Issue-Chain: order-execution#food-underprediction-secondary

---

## [RESOLVED] 푸드 체계적 과소예측 — 도시락/김밥/샌드위치/햄버거 (04-08 ~ 04-09)

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
- [x] 04-09 07:00 49965 푸드 카테고리 predicted_qty avg7d 근처 회복 확인
  | mid | n | under | bias(04-08) | bias(04-09) | 개선율 |
  |---|---|---|---|---|---|
  | 001 도시락 | 27 | 19 | -0.36 | **-0.02** | 94% ✓ |
  | 002 주먹밥 | 35 | 30 | -0.42 | **-0.15** | 64% ✓ |
  | 003 김밥 | 36 | 32 | -0.63 | **-0.21** | 67% ✓ |
  | 004 샌드위치 | 22 | 19 | -0.67 | **-0.13** | 81% ✓ |
  | 005 햄버거 | 25 | 21 | -0.54 | **-0.10** | 81% ✓ |
  | 012 빵 | 26 | 19 | -0.11 | -0.15 | -36% ✗ (소폭 악화, n↑ 영향) |
  - 전체 평균 bias: 0.455 → 0.127 (72% 개선, 목표 50% 초과) → **Match Rate: ~90%**
- [x] 8800271904593 predicted_qty=1.66 (이전 0) — 발주량 ≥1 ✓
- [x] 8800271905408 predicted_qty=0.87 (이전 0.34) — 회복 ✓
- [x] [stockout-all-window] DEBUG 로그: 생산 로그에서 미감지 (운영 로그 레벨 INFO 이상, DEBUG 미출력 — 예상 정상)

**결론 (04-09)**: 5/6 mid에서 bias 50%+ 감소, 전체 72% 개선. 만성 품절 imputation 사각지대 해소 확인 → RESOLVED.
012 빵 소폭 악화는 n 증가(22→26) 및 신규 nonzero_signal 상품 mix 변화로 추정, 추가 관측 필요.

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
