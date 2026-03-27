# Changelog

BGF 자동 발주 시스템 전체 변경 기록.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Critical] L1 실패 시 L2/L3 거짓 성공 방지 — saved_count 기반 차단 (2026-03-27)

### Root Cause (크롬 확장 Phase별 검증으로 확인)
- `fv_OrdYn` 폼 변수가 단품별 발주 화면에 **존재하지 않음** → ordYn 기반 차단 구조적 불가
- `dsSearch` 데이터셋은 상품 검색 API 호출 시에만 생성 → L2/L3 진입 시 없음
- `CHECK_ORDER_AVAILABILITY_JS`에서 ordYn='' (빈값 = falsy) → 조건 통과 → 항상 available=true

### Fixed
- **order_executor.py**: L1→L2/L3 차단 조건을 `saved_count==0` 기반으로 변경
  - 원인: `form_not_available` 메시지 매칭 방식은 Direct API 내부 흐름 변경에 취약
  - 수정: L1에서 전략1(gfn_transaction)+전략2(fetch) 모두 실패하여 saved_count=0이면 BGF 서버 거부로 판단, L2/L3 진입 차단
  - 영향: ordYn 폼 변수 유무와 무관하게 **서버 응답 기반**으로 안정적 차단

### Previous Attempts (참고)
- **batch_grid_input.py**: BGF 서버 필수 필드 누락으로 발주 무시되던 문제 수정
  - JIP_ITEM_CD, ITEM_CHK, PYUN_ID 등 추가 → Batch Grid 경로 정상화

- **direct_api_saver.py**: form_not_available 시 fetch() 폴백 차단
  - ordYn 불가 시 fetch 폴백 없이 즉시 실패 반환

- **order_executor.py**: `_check_order_availability()` 로깅 강화
  - INFO 레벨 로깅 추가 (ordYn/ordClose/available 값 출력)

- **order_prep_collector.py**: 그리드 셀 활성화 실패 시 조기 반환
  - 원인: 셀 활성화 실패 후 ActionChains 입력 → 홈 화면 검색창에 상품코드 입력되는 부작용
  - 수정: `result`가 에러이면 즉시 `{success: False}` 반환

---

## [기능] 샌드위치/햄버거 Cap 입고예정 차감 (2026-03-26)

### Added
- **food_daily_cap.py**: 004(샌드위치)/005(햄버거) 카테고리 한정 입고예정(pending) Cap 차감
  - 원인: 유통기한 2일 이상 카테고리에서 입고예정 수량이 Cap에 반영되지 않아 과잉 발주
  - 수정: `_get_pending_by_midcd()` 신규 함수로 어제+오늘 발주의 remaining_qty 합계 조회, `adjusted_cap`에서 차감
  - 대상: `PENDING_CAP_DEDUCT_CATEGORIES = ['004', '005']` (001/002/003/012는 유통기한 1일이라 미적용)
  - 영향: 예) Cap=5, 입고예정=2 → 발주 3개로 제한

---

## [기능] 발주 저장 검증 강화 + 발주현황 재수집 정합성 검증 (2026-03-26)

### Fixed
- **direct_api_saver.py (Layer 1)**: `_verify_grid_after_save()` missing>50% + 빈 그리드 시 실패 처리
  - 원인: gfn_transaction 후 matched=0, missing=89(100%)인데 grid_replaced 추정으로 성공 처리 → false positive
  - 수정: `missing_ratio > 50%` AND `grid_count<=1 + sample 빈값` → 무조건 실패, Selenium 폴백
  - 영향: Direct API false positive 완전 차단

- **direct_api_saver.py (Layer 2)**: ordYn 빈값 발주 불가 처리
  - 원인: 비정상 폼 상태에서 ordYn='' → available=true로 판단 → 허공에 발주
  - 수정: JS에서 `!ordYn || ordYn.trim() === ''` → `available=false`, Python에서 즉시 SaveResult(success=False) 반환
  - 영향: 비정상 폼 상태에서 Direct API 시도 자체 차단

### Added
- **order_status_collector.py (Layer 3-a)**: `collect_yesterday_orders()` 신규 메서드
  - BGF 발주현황 dsResult에서 어제 날짜 발주만 필터링하여 반환
  - order_tracking 정합성 검증의 기준 데이터 소스

- **order_tracking_repo.py (Layer 3-b)**: `reconcile_with_bgf_orders()` + `get_confirmed_pending()` 신규
  - BGF에 있는 건 → `pending_confirmed=1` 마킹 (미입고 보정 근거)
  - BGF에 없는 건 → `remaining_qty=0, status='invalidated'` (false positive 제거)
  - `get_confirmed_pending()`: BGF 확인된 미입고 수량 조회 (adjuster 보정용)

- **daily_job.py (Layer 3-c)**: Phase 1.96 발주정합성 검증 추가
  - Phase 1.95 (pending sync) 직후, 어제 발주현황 재수집 + order_tracking 양방향 대조
  - BGF 확인 → pending_confirmed, BGF 미접수 → 무효화

- **auto_order.py (Layer 3-d)**: adjuster 호출 전 confirmed pending 보정
  - order_prep_collector가 pending=0으로 잘못 반환한 경우, BGF 확인 pending으로 보정
  - 단품별 발주 화면의 dsOrderSale 범위 한계를 발주현황 데이터로 보완

---

## [수정] 푸드류 Cap 재적용 — 미입고 조정 후 총량 상한 보장 (2026-03-26)

### Fixed
- **auto_order.py**: 미입고 조정(order_adjuster) 이후 푸드류 Cap 재적용 추가
  - 원인: prediction에서 order_qty=1로 예측 → Cap(14) 이내로 통과 → order_adjuster의 미입고 조정에서 safety_stock 재계산(pred+safe-stk-pnd)으로 qty가 3~4배 증가 → Cap을 무력화하여 주먹밥 25개 과잉발주 (일평균 판매 8개)
  - 수정: execute()에서 `_apply_pending_and_stock_to_order_list()` + `_deduct_manual_food_orders()` 이후, 발주 실행 전에 `apply_food_daily_cap()`을 재적용. get_recommendations()에서 사용한 Cap 파라미터(site_order_counts, floor_protected_codes, target_date, 3day_protected)를 `_last_cap_params`에 보존하여 동일 조건으로 재절삭
  - 영향: 마평로드점 주먹밥(002) 발주 25개 → Cap(14) 이내로 제한, 전 매장 푸드류(001~005,012) 과잉발주 방지

---

## [수정] DirectAPI 템플릿 캡처 실패 — 3-layer 인터셉터 + 재시도 강화 (2026-03-25)

### Fixed
- **direct_api_fetcher.py**: `capture_request_template()` 인터셉터를 3-layer로 강화
  - 원인: 넥사크로가 내부 통신 시 일반 `fetch()`/`XMLHttpRequest` 프로토타입 패치를 우회하여 XHR 요청이 인터셉트되지 않음 → 템플릿 캡처 실패 → 전 상품 Selenium 폴백 (3초/건 → 수백 건 소요)
  - 수정: Layer 1 (fetch 패치) + Layer 2 (XHR 프로토타입) + Layer 3 (넥사크로 CommunicationManager + HttpRequest 후킹) 3중 인터셉터
  - 영향: DirectAPI 재고수집 성공률 향상, Selenium 폴백 최소화

- **direct_api_fetcher.py**: `reset_interceptor()` 메서드 신규 추가
  - 캡처 실패 시 인터셉터를 강제 재설치할 수 있도록 지원

- **order_prep_collector.py**: `_collect_via_direct_api()` 캡처 재시도 로직 강화 (1회 → 2회)
  - 원인: Selenium 1건 검색 후 캡처 실패 시 즉시 Selenium 전체 폴백으로 전환되어 복구 불가
  - 수정: 최대 2회 시도, 2번째 시도 시 `reset_interceptor()` 후 재설치
  - 영향: 간헐적 캡처 실패 복구율 향상

- **daily_job.py**: Phase 1.68 DirectAPI Stock Refresh — Selenium 폴백 제거 + 탭 정리 추가
  - 원인: DirectAPI 실패 시 Selenium 폴백으로 단품별 발주 화면을 열고 닫지 않음 → Phase 2 자동발주 화면과 데이터셋 충돌 → `no dataset` 에러로 전 상품 발주 실패
  - 수정: Selenium 폴백 제거 (Phase 2에서 수집 위임) + `finally: close_menu()` 추가로 탭 반드시 정리
  - 영향: Phase 2 자동발주 정상화, `no dataset` 에러 해소

---

## [수정] 샌드위치/햄버거 pending 무시 버그 수정 — 중복발주 방지 (2026-03-24)

### Fixed
- **4파일 (order_status_collector, improved_predictor×2, auto_order)**: `FOOD_SAME_DAY_MID_CDS`에서 004/005 제거
  - 원인: `{'001','002','003','004','005'}` 일괄 pending 무시 → 유통기한 2~3일인 004(샌드위치)/005(햄버거)도 pending이 차감되지 않아 매일 중복발주
  - 수정: `{'001','002','003'}` (유통기한 1일만 당일배송 처리)
  - 영향: 004/005 중복발주 방지, 001~003 변경 없음, 207개 관련 테스트 통과

---

## [수정] 푸드류 Cap 수량 기반 전환 — 행사 부스트 과잉발주 방지 (2026-03-22)

### Fixed
- **food_daily_cap.py**: Cap 비교를 품목수(`len`) → 수량합(`sum(qty)`)으로 변경
  - 원인: 행사 부스트로 품목당 qty=2~3인 경우, `len(items)=4 <= cap=5`로 Cap 미발동 → 총 14개 과잉발주 (46704 도시락)
  - 수정: `current_qty_sum = sum(final_order_qty)` 비교 + `_trim_qty_to_cap()` 2차 절삭 함수 신규 추가
  - 영향: 푸드류(001~005,012) 전체. 기존 qty=1 케이스는 sum(qty)=len(items)이므로 하위 호환 유지

### Added
- **food_daily_cap.py**: `_trim_qty_to_cap()` 함수 — 선별 후 수량합이 cap 초과 시 후순위부터 절삭
  - 후순위(explore/new) → 선순위(exploit/proven) 역순 절삭
  - qty=0이면 품목 제거, 최종 sum(qty) ≤ cap 보장

### Changed
- **food-daily-cap.md**: 스킬 문서 3건 업데이트
  - Cap 비교 기준: "상품 수 <= cap" → "수량합 <= cap"
  - 버퍼 공식: waste_buffer=3 → 20% 동적 버퍼 (2026-03-13 변경 반영)
  - 함수 흐름: `_trim_qty_to_cap` 2차 절삭 단계 추가

### Verified
- 테스트: 70개 PASS (food_daily_cap + cap 관련)
- Gap Analysis: Match Rate 67.5% → 98%
- 시뮬레이션: 46704 도시락 14개 → ≤5개로 정확 제한

---

## [수정] DemandClassifier sparse-fix 초저회전 상품 오분류 방지 (2026-03-21)

### Fixed
- **demand_classifier.py**: sparse-fix 로직에 window_ratio 하한 가드 추가
  - 원인: 60일 중 2일만 판매된 상품(window_ratio=3.3%)이 data_ratio=100%로 오분류 → FREQUENT
  - 케이스: 릴하이브리드3누아르블랙(mid_cd=073) → TobaccoStrategy safety_stock=9.35 → 과발주
  - 수정: `SPARSE_FIX_MIN_WINDOW_RATIO = 0.05` 상수 추가, 조건 강화 (data_ratio ≥ 40% AND window_ratio ≥ 5%)
  - 영향: 초저회전 상품 SLOW 정상 분류 → 안전재고 1~2개 → 과발주 방지

### Changed
- **test_demand_classifier.py**: 10개 신규 테스트 + 1개 기존 테스트 수정
  - 경계값 테스트: window_ratio 3%, 5%, 6% / data_ratio 34%, 40%, 45%
  - 혼합 조건 테스트: 각 조합별 PASS/FAIL 케이스
  - Result: 37/37 PASS (0회 반복)

### Verified
- 7-area conflict check: DemandClassifier callers, DemandPattern refs, BasePredictor WMA, TobaccoStrategy, FORCE_ORDER, predict_batch+ROP, Phase 1.61 → 모두 SAFE
- Regression: 3,705개 테스트 모두 PASS

---

## [수정] DemandClassifier 수집 갭 오분류 버그 수정 (2026-03-20)

### Fixed
- **demand_classifier.py**: 데이터 수집 갭으로 인한 수요 패턴 오분류 (SLOW→FREQUENT)
  - 원인: total_days < 14일 때 window_ratio(sell_days/60) 만으로 분류 → 수집 빈도 낮은 상품이 SLOW로 오분류 → prediction=0 → 발주 0
  - 수정: data_ratio(sell_days/total_days) >= 40%이면 수집 갭으로 판단하여 FREQUENT로 분류
  - 영향: 미에로사이다 등 수집 갭 있는 활성 상품의 발주 누락 해결
- **base_predictor.py**: slow 분류 안전장치 추가
  - 원인: DemandClassifier에서 slow로 분류되면 무조건 prediction=0 → 안전장치 없음
  - 수정: data_sufficient=False + actual_ratio >= 30%이면 WMA 폴백으로 최소 예측 보장
  - 영향: 이중 안전망으로 유사 오분류 방지

### Changed
- **test_demand_classifier.py**: 3개 테스트 케이스를 수집 갭 보정 동작에 맞게 업데이트

---

## [개선] ML 증분학습 윈도우 30→90일 + 3매장 스키마 통일 (2026-03-18)

### Changed
- **ml_training_flow.py**: 증분학습 윈도우 30일→90일 변경
  - 원인: 30일 윈도우 증분학습이 매일 5/6 그룹 MAE 게이트 FAIL (악화 26~55%) → 모델 갱신 불가
  - 수정: `incremental=True` 시 `days = 90`으로 변경 (전체학습과 동일 데이터 범위)
  - 결과: 증분학습 5/5 PASS, 0 롤백 (MAE 변화 ±1% 이내)

### Fixed
- **47863.db**: ml_training_logs 구 스키마(9컬럼) → 신규 스키마(16컬럼) 재생성
  - 원인: `CREATE TABLE IF NOT EXISTS`로 인해 구 스키마 테이블이 갱신되지 않음 → INSERT silent 실패 → 학습 로그 0건
  - 수정: DROP TABLE + 다음 학습 시 자동 재생성, schema_version 테이블+v53 추가
  - 영향: 47863 학습 지표 정상 기록 (5건)
- **46704.db, 47863.db**: new_product_items에 `mid_cd` 컬럼 누락 → ALTER TABLE ADD COLUMN
  - 원인: 46513 이후 추가된 컬럼이 다른 매장 DB에 반영되지 않음
  - 영향: 0행 테이블이라 데이터 손실 없음
- **46704.db, 47863.db**: eval_outcomes_new 테이블 누락 → 46513 DDL 복제 생성
  - 영향: 미사용 테이블 (46513도 0행), 스키마 일관성 확보
- **diff_feedback.py**: mid_cd=022(마른안주류) DiffFeedback penalty 면제 추가
  - 원인: 품절 반복 특성상 점주 수동 제거 빈도 높음 → penalty 적용 시 과소발주
  - 수정: `get_removal_penalty(item_cd, mid_cd=None)` 파라미터 추가, mid_cd="022" 시 return 1.0

### Added
- 3매장 ML 전체학습(90일) 재실행 — 15/15 그룹 PASS
- 4개 신규 테스트: TestMidCdExemption (diff_feedback mid_cd 면제)

### Verified
- 3매장 스키마 완전 일치: 45 테이블
- diff_feedback 테스트: 20/20 PASS

---

## [버그수정] site 기발주 상품 auto 발주 덮어쓰기 방지 (2026-03-17)

### Fixed
- **order_filter.py**: `exclude_site_ordered_items()` 메서드 추가 — site(사용자) 기발주 상품을 auto Phase 2에서 제외
  - 원인: Phase 1.95에서 site 발주를 order_tracking에 저장 → Phase 2에서 같은 상품에 auto 발주 제출 → BGF saveOrd가 UPSERT이므로 site 수량이 auto 예측값으로 덮어씌워짐
  - 수정: `_exclude_filtered_items()` 직후에 site 기발주 필터 호출, cancel_smart 면제
  - 영향: 46704 기준 44건(site 108개→auto 73개, -35개 과소발주) 방지
- **auto_order.py**: `_exclude_site_ordered_items()` 래퍼 + 2개소 호출 삽입 (개선/기존 예측기 경로)
- **order_tracking_repo.py**: `get_site_ordered_items()` 메서드 추가 (order_date+order_source='site' 조회)
- **order_exclusion_repo.py**: `ExclusionType.SITE_ORDERED` 상수 추가

---

## [버그수정] pending 교차검증 로직 수정 (2026-03-16)

### 문제
- BGF dsOrderSale 갱신 지연 시 RI=0 반환
- 교차검증 조건 `pending_qty > 0` 이 FALSE → OT 무시 → 중복 발주 발생

### 1차 수정 문제
- ot < ri 케이스를 낮추는 방향으로 추가
- 수동 발주(BGF 직접) 시 OT=0, RI=32 → pending=0 → 중복 발주 재발

### 최종 수정 (inventory_resolver.py L138-161)
- ot > ri → OT값 사용 (ot_fill): BGF 갱신 지연 보완
- ot < ri → RI값 유지 (ri_fresh): 수동 발주 보호
- 근거: 수동 발주는 OT에 없지만 RI(BGF dsOrderSale)에 이미 포함됨

### 테스트
- 24/24 통과
- 신규 케이스: RI=8/OT=3→8, RI=32/OT=0→32 확인

### 수정 파일
- src/prediction/inventory_resolver.py
- tests/test_pending_cross_validation.py

---

## [2026-03-14] - cancel_smart 버그 3건 수정 (Selenium/수동차감/Cap)

### Fixed
- **order_executor.py** (L1071): Selenium 경로 `max(1,...)` 가 cancel_smart(target_qty=0)를 multiplier=1로 강제
  - 원인: `input_product()`는 target_qty만 받아 cancel_smart 플래그 확인 불가, `max(1,...)` 무조건 적용
  - 수정: `if target_qty == 0: actual_multiplier = 0` 분기 추가
  - 영향: L3 Selenium 폴백에서 cancel_smart 상품이 1배수(qty=6 등) 발주됨 → 0배수로 정상 전송
- **order_filter.py** (L231): `deduct_manual_food_orders`에서 cancel_smart 푸드 항목이 수동발주 차감으로 제거
  - 원인: cancel_smart(qty=0) 푸드 → `adjusted_qty=max(0,0-manual)=0 < min_order_qty=1` → 목록에서 제거
  - 수정: for 루프 최상단에 `if item.get("cancel_smart"): append+continue` 바이패스 추가
  - 영향: 수동발주와 겹치는 cancel_smart 푸드 상품이 BGF에 전송되지 않음 → 스마트 취소 실패
- **food_daily_cap.py** (L477-503): Cap 품목수 계산에 cancel_smart 항목이 포함되어 슬롯 차지
  - 원인: `len(items)` → cancel_smart도 품목수에 포함, Cap 초과 시 `select_items_with_cap`에서 잘릴 수 있음
  - 수정: cancel_items/non_cancel 분리, `current_count=len(non_cancel)`, 결과에 `cancel_items` 항상 병합
  - 영향: cancel_smart가 Cap 슬롯을 차지하여 정상 상품이 불필요하게 제외됨

### Added
- 테스트 9건 (TestCancelSmartBugFixes): Selenium multiplier=0(3), deduct_manual 바이패스(3), Cap 제외(3)

---

## [2026-03-14] - SmartOverride qty=0 취소 로직

### Added
- **auto_order.py**: qty=0 스마트발주 취소 주입 (`cancel_smart=True` 플래그)
  - 예측 qty=0 상품을 발주 목록에 포함하여 BGF 단품별발주로 전송
  - BGF 서버가 PYUN_QTY=0을 "단품별(채택)"으로 처리 → 스마트 자동발주 차단
  - 라이브 검증 (2026-03-14): 기네스캔440ml, 발주현황에 "단품별(채택)" qty=0 확인
- **direct_api_saver.py**: `_calc_multiplier()` cancel_smart 분기 추가 (return 0)
- **batch_grid_input.py**: cancel_smart 주문 multiplier=0 허용 분기 추가
- **order_executor.py**: `group_orders_by_date()` + Selenium 경로 cancel_smart 통과 허용

### Changed
- 로그 요약에 `취소(qty=0)=N개` 카운트 추가 (기존 `스킵` → `취소`로 구분)
- 기존 테스트 3건 기대값 업데이트 (cancel_smart 도입 반영)
- 신규 테스트 8건 추가 (TestCancelSmartQtyZero 클래스)

---

## [2026-03-14] - SmartOverride 버그 4건 추가 수정 (C-3, C-4, B-1, D-1)

### Fixed
- **auto_order.py** (`_inject_smart_order_items`): `_exclusion_records` 체크 누락 (C-3)
  - 원인: missing 필터에서 `_cut_items`+`_unavailable_items`만 확인하고 `_exclusion_records` 미확인
  - 수정: `excluded_cds` 집합 추출 + missing 필터 조건 추가 + 사유별 제외 로그
  - 영향: 영구제외/발주정지/CONFIRMED_STOP 상품이 스마트 오버라이드로 재추가 → BGF Alert 방지
- **auto_order.py** (`_inject_smart_order_items`): `predict_batch` 전체 실패 시 폴백 없음 (C-4)
  - 원인: predict_batch 예외 → 외부 try/except에서 통째로 캐치 → missing 전부 누락
  - 수정: predict_batch 자체를 try/except 감싸고 실패 시 pred_map={} 유지 (qty=0 안전 폴백)
  - 판단: qty=0 선택 — 과발주→폐기 리스크 > 누락 리스크, SMART_OVERRIDE_MIN_QTY로 사용자 제어 가능

### Changed
- **auto_order.py** (`_inject_smart_order_items`): summary 로그 명확화 (B-1)
  - 원인: EXCLUDE_SMART=False + OVERRIDE=True 조합 시 이중 처리 로그 혼란
  - 수정: `[SmartOverride:OVERRIDE모드]` 접두어 + CUT/미취급/제외 카운트 분리 표시
- **order_filter.py** (`deduct_manual_food_orders`): smart_override 태그 추가 (D-1)
  - 원인: 수동발주 차감 로그에서 smart_override 상품인지 식별 불가
  - 수정: `item.get("smart_override")` 체크 후 `[스마트→수동전환]` 태그 추가 (로그+exclusion_records)

### Quality Metrics
- Test Results: 3,692 passing, smart order **27개** 전부 통과 (기존 23 + 신규 4)
- 신규 테스트: exclusion_records 단일/복합 타입 2개, predict_batch 실패 폴백 2개 (MIN_QTY=0/1)
- 기존 실패 12개 변동 없음 (pre-existing)
- 영향 범위: Smart inject (L980), 수동발주 차감 (L1359)

---

## [2026-03-14] - SmartOverride 버그 2건 수정

### Fixed
- **auto_order.py** (`_inject_smart_order_items`): `_unavailable_items` 체크 누락 (C-1)
  - 원인: missing 필터에서 `_cut_items`만 확인하고 `_unavailable_items` 미확인
  - 수정: L509 missing 필터에 `and s["item_cd"] not in unavailable` 조건 추가 + 제외 로그
  - 영향: is_available=0 상품이 스마트 오버라이드로 재주입 → BGF Alert + 발주 실패 방지
- **auto_order.py** (`_inject_smart_order_items`): `order_unit_qty` 필드 누락 (C-2)
  - 원인: order_entry dict에 `order_unit_qty` 키 없음 → Floor/Cap 단계에서 unit=1 폴백
  - 수정: L550-555 `_product_detail_cache` 패턴으로 product_details에서 조회 (try/except 방어)
  - 영향: 스마트 주입 상품의 배수 정렬 오류 방지, Floor 보충 시 정확한 unit 참조
- **test_smart_order_override.py**: fixture에 `_product_repo` mock + `_unavailable_items` 초기화 추가

### Quality Metrics
- Test Results: 3,692 passing, smart order 23개 전부 통과
- 기존 실패 12개 변동 없음 (pre-existing)
- 영향 범위: Smart inject (L955) — Floor/CUT/Cap 이전 단계

---

## [2026-03-14] - order_unit_qty 최종 보정 통합 (_finalize_order_unit_qty)

### Changed
- **auto_order.py**: `_finalize_order_unit_qty()` 신규 메서드 추가 (order_executor._refetch 대체)
  - 배경: 기존 `_refetch_order_unit_qty`는 order_executor에서 건건 조회 + unit=1만 대상
  - 개선 1: **배치 조회** — 500개씩 청크 분할 IN 쿼리 (건건 N회→배치 ceil(N/500)회)
  - 개선 2: **전품목 비교** — unit=1뿐 아니라 DB와 불일치하는 모든 상품 보정 (superset)
  - 개선 3: **삽입 위치 이동** — order_executor(L1/L2/L3 공통) → auto_order(execute_orders 직전)
  - 동작: order_unit_qty만 갱신, final_order_qty는 변경하지 않음 (downstream _calc_multiplier가 재계산)
  - 호출 위치: `get_recommendations()` 내 `_ensure_clean_screen_state()` 직후
  - 영향: 발주 실행 직전 — 전품목 order_unit_qty 정합성 보장

### Removed
- **order_executor.py**: `_refetch_order_unit_qty()` 메서드 제거 (50줄)
  - 호출부 (execute_orders 내) 2줄 제거
  - 메서드 본체 50줄 제거
  - auto_order._finalize_order_unit_qty로 완전 대체

### Quality Metrics
- Test Results: 3,692 passing, 22개 multiplier cap 테스트 전부 통과 (+5 순증)
- 기존 실패 12개 변동 없음 (pre-existing)
- 영향 범위: 발주 실행 직전 (auto_order.py 1개소)

---

## [2026-03-14] - order_unit_qty 재조회 방어 + AUDIT 로그 배수 계산 수정

### Fixed
- **order_executor.py (`_refetch_order_unit_qty`)**: unit=1 상품 재조회 조건 완화 + DB 소스 수정
  - 원인 1: `qty <= 5` 가드로 인해 소량 발주 상품(qty≤5)은 unit=1이어도 재조회 스킵
  - 원인 2: `product_collector.get_from_db()` → `get_connection()` → legacy `bgf_sales.db` 참조 가능 (common.db 대신)
  - 증상: 카스캔500ml(unit=1,qty=72) → PYUN_QTY=72, ORD_UNIT_QTY=1 → BGF 서버가 72×24=1,728개로 해석 가능
  - 수정 1: `qty <= 5` 조건 제거 → `if unit > 1: continue` (unit=1인 모든 상품 재조회)
  - 수정 2: `product_collector.get_from_db()` → `DBRouter.get_connection(table="product_details")` 직접 SQL 조회
  - 영향: 발주 실행 직전 — 박스단위 과발주 방지 (→ _finalize_order_unit_qty로 통합됨)

- **order_executor.py (AUDIT 로그 PYUN_QTY 계산)**: `_calc_multiplier`와 불일치하는 계산 수정 (2개소)
  - 원인: L1(Direct API) / L2(Batch Grid) AUDIT 블록에서 `unit=1`이면 `mult=1` 고정 → 실제 전송값 `mult=12`와 불일치
  - 증상: AUDIT 로그에 `PYUN_QTY=1, ORD_UNIT_QTY=1, TOT_QTY=12`로 기록되지만, 실제 BGF에는 `PYUN_QTY=12` 전송
  - 수정: `mult = max(1, (qty + unit - 1) // unit) if qty > 0 else 0` 통일 (L1 + L2 동일 적용)
  - 영향: 발주 감사 로그 — 실제 전송값과 AUDIT 기록 정합성 확보

### Quality Metrics
- Test Results: 3,687 passing (17개 multiplier cap 테스트 전부 통과, 228개 발주 관련 테스트 통과)
- 기존 실패 12개 변동 없음 (pre-existing: beer, dessert_dashboard, diff_feedback 등)
- 영향 범위: 발주 실행 직전 (_refetch) + 감사 로그 (AUDIT L1/L2)

---

## [2026-03-14] - slow 패턴 + 대형 배수 과잉발주 방지

### Fixed
- **improved_predictor.py**: 데이터 부족(data_days<7) 상품이 ROP(1) → 대형 배수(10,12,16...) 올림으로 과잉발주
  - 원인 1: ROP 로직이 `data_days` 무관하게 `sell_day_ratio<0.3 && stock==0 → order_qty=1` 발동
  - 원인 2: `_round_to_order_unit()` Branch D-else에서 `order_qty=1` → `ceil_qty=order_unit`(10,12,16) 올림
  - 증상: 판매 데이터 0~2건인 상품 475개가 3/13 하루에 1354개 발주 (대부분 demand_pattern=slow)
  - 수정 1: ROP 발동에 `data_days >= DATA_MIN_DAYS_FOR_LARGE_UNIT(7)` 조건 추가 (신제품 예외)
  - 수정 2: `_round_to_order_unit()` 진입부에 대형배수(≥10) + 데이터부족(<7일) + qty≤unit 가드 추가 → 1배수 제한
  - 상수: `DATA_MIN_DAYS_FOR_LARGE_UNIT = 7`, `LARGE_ORDER_UNIT_THRESHOLD = 10`
  - 예외: detected_new_products 등록 상품은 기존 로직 유지 (유사상품 기반 보정 활용)
  - 영향: Step 5 (ROP), Step 6 (Round) — slow 패턴 + order_unit_qty ≥ 10 상품

---

## [2026-03-14] - store_val UnboundLocalError 수정

### Fixed
- **promotion_manager.py**: 행사 통계 갱신 시 `store_val` UnboundLocalError
  - 원인: `store_val = self.store_id or DEFAULT_STORE_ID`가 `if normal_sales:` 블록 안에서만 정의 → 비행사 판매 없는 상품(normal_sales=[])에서 행사 통계 저장 시 `store_val` 미정의
  - 증상: `cannot access local variable 'store_val' where it is not associated with a value` (3/14 기준 5건)
  - 수정: `store_val` 할당을 `if normal_sales:` 블록 바깥으로 이동 (L512→L513)
  - 영향: 행사 중인 상품의 통계 누적 누락 → 행사 종료 판정(promo A-ending) 정확도에 간접 영향

---

## [2026-03-13] - surplus 취소 days_cover 조건 추가

### Fixed
- **improved_predictor.py**: `_round_to_order_unit()` surplus 취소 시 대형 배수 상품 발주 누락
  - 원인: `order_unit_qty`(16,20,30 등)가 `need_qty`보다 클 때 `floor=0` → `surplus`가 항상 크게 나와 무조건 발주 0 판정
  - 예시: 까르보불닭큰컵(unit=16, need=7) → surplus=9 >= safety=6.48 → 발주 취소 → 재고 1.7일분인데도 미발주
  - 수정: surplus 취소 조건에 `surplus_days_cover >= SURPLUS_MIN_DAYS_COVER(1.0)` 추가 — 현재 재고가 1일치 미만이면 취소 안 함
  - 적용 위치: 3곳 (Branch A-max_stock L2499, Branch B-default L2547, legacy enhanced L3037)
  - 상수: `SURPLUS_MIN_DAYS_COVER = 1.0` (improved_predictor.py 모듈 레벨)
  - ZeroDivisionError 방지: `max(adjusted_prediction, 0.1)` 패턴
  - 영향: Step 6 (Round) — order_unit_qty ≥ need_qty인 모든 상품
- **improved_predictor.py**: Branch B-default 로깅 elif가 후속 분기 차단 → `None` 반환 버그
  - 원인: 로깅용 `elif`가 `needs_ceil/floor_qty/else` 체인에 끼어들어 매칭 시 후속 분기 전부 스킵
  - 조건: `is_default_category` + `surplus >= safety_stock` + `days_cover < 1.0` 동시 충족 시 발생
  - 수정: `elif` → 독립 `if`로 분리 (로그 후 정상적으로 needs_ceil/floor/else 도달)
  - Branch A는 폴백 return이 있어 정상, Location 3도 else 블록 내 독립 if라 정상

---

## [2026-03-13] - Floor/CUT 발주 정합성 강화 (order PDCA GAP 4건)

### Fixed
- **category_demand_forecaster.py**: Floor(mid) 후보 SQL에 `is_cut_item` 필터 누락
  - 원인: LEFT JOIN ri에 is_available 필터만 있고 is_cut_item 체크 없음
  - 수정: `AND COALESCE(ri.is_cut_item, 0) = 0` 추가
  - 영향: Step 11 (Floor mid) - CUT 상품이 보충 후보에서 제외됨
- **large_category_forecaster.py**: Floor(large) 후보 SQL에 동일 `is_cut_item` 필터 누락
  - 수정: 동일 패턴 적용
  - 영향: Step 12 (Floor large)
- **cut_replacement.py**: target_mid_cds에 빵(012) 누락
  - 원인: FOOD_CATEGORIES에 012 포함이나 CUT 대체 대상에서 제외됨
  - 수정: `["001","002","003","004","005"]` → `["001","002","003","004","005","012"]`
  - 영향: Step 13 (CUT 보충) - 빵 카테고리 CUT 손실 수요도 대체 보충

### Added
- **3파일**: `order_unit_qty` 필드 추가 + 배수 정렬(ceil) 적용
  - category_demand_forecaster.py: common DB 배치조회(products+product_details), 새 항목 dict에 order_unit_qty, 배수 정렬
  - large_category_forecaster.py: 동일 패턴
  - cut_replacement.py: 기존 product_details 쿼리에 order_unit_qty 추가, 배수 정렬
  - 기존 항목 수량 증가 시에도 배수 재정렬 적용
  - COALESCE(order_unit_qty, 1) NULL 방지, max(1, qty) qty=0 방지

### Quality Metrics
- Test Results: 3,686 passing (기존 8개 실패 변동 없음)
- Floor/CUT 관련 74개 테스트 전부 통과
- 영향 파이프라인 단계: Step 11 (Floor mid), Step 12 (Floor large), Step 13 (CUT 보충)

---

## [2026-03-13] - Branch A/B 행사 과발주 방지 (Fix B 확장)

### Fixed
- **improved_predictor.py**: Branch A(행사 종료 임박) / Branch B(행사 시작 임박)에 재고 체크 추가
  - 원인: Branch C에만 Fix B(stock≥demand 스킵)가 적용, A/B는 pred=0이어도 promo_adjuster 호출 → promo_min(2) → round_to_unit(6) 과발주
  - 수정: 각 브랜치에 `if promo_status.promo_avg > 0: promo_daily_demand = promo_avg * weekday_coef; if stock+pending >= demand: skip` 가드 추가
  - 안전장치: promo_avg=0이면 가드 미작동 (기존 adjuster 로직 유지)
  - 영향: Step 7 (Promo 조정) — 17개 과발주 케이스 (3/13 실제 발주) 방지

### Added
- **test_promo_unit_guard.py**: Branch A/B Fix B 테스트 6개 추가
  - TC-B4 (Case A 재고 충분 스킵), TC-B4b (Case A 재고 부족 → adjuster 호출), TC-B4c (Case A promo_avg=0 가드 스킵)
  - TC-B7 (Case B 재고 충분 스킵), TC-B7b (Case B 재고 부족 → adjuster 호출), TC-B7c (Case B promo_avg=0 가드 스킵)

### Quality Metrics
- Test Results: 3,686 passing, 21개 프로모션 테스트 전부 통과
- 기존 실패 12개 변동 없음 (pre-existing)
- 영향 파이프라인 단계: Step 7 (Promo 조정)

---

## [2026-03-13] - 발주 파이프라인 5개 항목 개선

### Fixed
- **auto_order.py**: `_get_site_order_counts_by_midcd()` COUNT(*)→SUM(order_qty) 단위 통일
  - 원인: site 발주를 품목수(COUNT)로 집계 → Floor의 수량(qty) 기반 비교와 단위 불일치
  - 수정: `COALESCE(SUM(ot.order_qty), 0)` 로 변경, docstring "건수"→"수량" 업데이트
  - 영향: Step 10 (site_order_counts) → Floor/Cap 수량 정합성 개선

### Changed
- **prediction_config.py + category_demand_forecaster.py**: 빵(012) Floor(mid) target_mid_cds에 추가
  - 기존: ["001","002","003","004","005"] (신선식품만)
  - 변경: ["001","002","003","004","005","012"] (빵 포함)
  - 영향: Step 11 (Floor mid) — 빵 품목 로테이션에도 카테고리 총량 보충 적용
- **category_demand_forecaster.py + large_category_forecaster.py**: Floor 추가 품목에 data_days 필드 전달
  - SQL appear_days → candidate → distribute_shortage → order_list item의 data_days 필드로 전파
  - 영향: Step 11-12 (Floor mid/large) → Cap에서 "proven" 정확 분류 (기존: data_days=0 → "new" 오분류)
- **food_daily_cap.py**: Cap의 qty vs count 차원 설계 주석 문서화
  - total_cap(qty 기반) vs len(items)(count 기반) 비교는 의도적 근사 (food qty≈1)
- **food_daily_cap.py + food_waste_calibrator.py + api_waste.py**: waste_buffer deprecated 표시
  - 이전 세션에서 effective_buffer=20%×category_total로 대체 완료, config/calibrator/API에 미사용 표시 추가

### Quality Metrics
- Test Results: 3,681 passing (144개 관련 테스트 전부 통과)
- 영향 파이프라인 단계: Step 10, 11, 12, 14

---

## [2026-03-06] - Order Exception Handling Hardening

### Added
- Exception handling for 8 critical function calls in auto_order.py (load_unavailable_from_db, load_cut_items_from_db, load_auto_order_items, prefetch_pending_quantities, get_order_candidates, _save_to_order_tracking, _update_eval_order_results)
- Math guard using math.isfinite() to prevent OverflowError on NaN/Inf values in FORCE order cap logic
- 42 new unit tests covering None-safety patterns, NaN/Inf handling, and negative value clamps
- None-safety guards with `or default` pattern in order_adjuster.py (10 dict operations)
- Negative value clamps using max(0, ...) for predicted_sales and safety_stock calculations

### Changed
- order_adjuster.py: Enhanced apply_pending_and_stock() and recalculate_need_qty() with defensive programming patterns
- auto_order.py: Added try/except blocks around all external function calls in daily order processing

### Fixed
- TypeError when stock_data contains None values (bug fix #1)
- OverflowError when FORCE cap calculation yields infinite values (bug fix #2)
- Under-ordering when ML prediction returns negative values (bug fix #3)

### Quality Metrics
- Design Match Rate: 100% (21/21 design items implemented)
- Test Results: 3,367 total passing (42 new tests added)
- Feature: order (예외 처리 강화) PDCA completion
- Report: docs/04-report/order.report.md

---

## [2026-03-04] - force-order-fix FORCE_ORDER 오판 수정

### Fixed
- **src/order/auto_order.py:799** — FORCE 보충 생략 조건 강화
  - 변경: `r.pending_qty > 0 and r.current_stock + r.pending_qty > 0` → `r.current_stock + r.pending_qty > 0`
  - 원인: pending_qty > 0 조건으로 인해 재고만 있는 경우를 필터링하지 못함
  - 영향: 재고 있는 상품의 불필요한 FORCE 강제 발주 제거 (버그 원인: host 8804624073530 재고10개 → FORCE 발주 1개)

### Added
- **src/infrastructure/database/repos/order_exclusion_repo.py:30** — ExclusionType.FORCE_SUPPRESSED 추적용 enum 추가
- **src/order/auto_order.py:805-814** — FORCE 보충 생략 시 _exclusion_records 기록 (감사 로그)
- **tests/test_force_order_fix.py** — 16개 테스트 케이스 (5 클래스)
  - TestForceSkipWithStockOnly (3) — stock>0, pending=0 버그 케이스
  - TestForceSkipWithPendingOnly (2) — stock=0, pending>0 정상 케이스
  - TestForceOrderGenuineStockout (2) — stock=0, pending=0 정상 (FORCE 발주 확인)
  - TestForceSupplementIntegration (5) — 통합 시뮬레이션 (혼합 상품)
  - TestOldVsNewCondition (4) — 기존/수정 조건 수학적 증명

### Quality Metrics
- **Design Match Rate**: 95% (11/11 필수, 0/2 선택적 보류)
- **Test Coverage**: 16/16 PASS (320% of plan)
- **Code Quality**: 0 regressions (기존 2936 tests 전부 통과)
- **Production Ready**: ✅ YES

---

## [2026-03-01] - ml-improvement ML 모델 실효성 검증 + 적응형 블렌딩 + 피처 정리

### Added
- **Phase A: ML 기여도 로깅** (improved_predictor.py)
  - ctx에 6개 필드 추가: ml_delta, ml_abs_delta, ml_changed_final, ml_weight, ml_rule_order, ml_pred_sale
  - rule-only vs ML-blended 비교 가능 (로그/API/ctx)

- **Phase B: 적응형 블렌딩** (improved_predictor.py)
  - _get_ml_weight() 신규 함수: MAE 기반 동적 가중치 (0.1~0.5)
  - 공식: max(0.1, min(0.5, 0.5 - (mae - 0.5) * 0.267))
  - 경계 조건: data_days < 30 → 0.0, < 60 → ×0.6 감쇄, meta 없음 → 0.15

- **Phase C: 피처 정리** (feature_builder.py)
  - FEATURE_NAMES 41→31 (10개 원핫 제거)
  - 제거: 카테고리 그룹 원핫(5) + large_cd 슈퍼그룹 원핫(5)
  - 유지: get_category_group(), get_large_cd_supergroup() 함수

- **Phase D: Quantile alpha 도메인 정합** (trainer.py)
  - food: 0.60→0.45 (보수적, 유통기한 짧음)
  - perishable: 0.55→0.48 (약간 보수적)
  - tobacco: 0.50→0.55 (상향, 품절 이탈 방지)
  - alcohol: 0.55→0.55 (유지)
  - general: 0.45→0.50 (중립)

- **Phase E: Accuracy@1/2 메트릭 + 성능 게이트** (trainer.py)
  - accuracy_at_1/2 메트릭 계산 (model_meta.json + ml_training_logs 테이블)
  - 성능 게이트 확장: MAE 20% 악화 OR Accuracy@1 5%p 하락 시 거부

### Changed
- **src/prediction/improved_predictor.py**: +70줄 (Phase A, B)
- **src/prediction/ml/feature_builder.py**: -10줄 (Phase C, 10개 원핫 제거)
- **src/prediction/ml/trainer.py**: +100줄 (Phase D, E)

### Tests
- 신규: 33개 테스트 (TestFeatureCleanup 7, TestQuantileAlpha 6, TestAdaptiveBlending 7, TestMLContributionLogging 2, TestAccuracyMetric 7, TestIntegration 4)
- 기존: 2794개 테스트 무손상 (100% 통과)
- 총 2827/2827 (100% pass rate)

### Quality Metrics
- **Design Match Rate**: 100% (7/7 success criteria PASS)
- **Gap Analysis**: 0 items missing
- **Code Quality**: 0 regressions
- **Architecture Compliance**: 100%

### PDCA Completion
- ✅ Plan: docs/01-plan/features/ml-improvement.plan.md
- ✅ Design: Inline (Plan 기반)
- ✅ Check: docs/03-analysis/ml-improvement.analysis.md (100% match)
- ✅ Act: docs/04-report/ml-improvement.report.md

---

## [2026-03-01] - category-total-prediction-largecd 대분류 기반 카테고리 총량 예측 정밀화

### Added
- **src/prediction/large_category_forecaster.py**: LargeCategoryForecaster 클래스 (340줄)
  - large_cd별 WMA 총량 예측 → mid_cd 비율 배분 → floor 보충
  - DB(mid_categories.large_cd) + 상수(LARGE_CD_TO_MID_CD) 2중 폴백
  - 기존 CategoryDemandForecaster(mid_cd level) 보완하는 상위 계층
- **src/settings/constants.py**: LARGE_CD_TO_MID_CD 매핑 (18개 large_cd → mid_cd)
- **src/prediction/prediction_config.py**: large_category_floor 설정 블록
- **tests/test_large_category_forecaster.py**: 19개 테스트

### Changed
- **src/order/auto_order.py**: LargeCategoryForecaster 통합
  - import 추가, __init__에서 인스턴스 생성
  - get_recommendations()에서 CategoryDemandForecaster 뒤에 실행

### Tests
- 19개 신규 테스트 전부 통과
- 기존 category_demand_forecaster 15개 테스트 무영향

---

## [2026-03-01] - ml-feature-largecd ML 대분류 슈퍼그룹 피처 추가

### Added
- **src/prediction/ml/feature_builder.py**: large_cd 기반 5개 슈퍼그룹 원핫 피처 추가
  - LARGE_CD_SUPERGROUPS: 18종 large_cd -> 5개 그룹 (food/snack/grocery/beverage/non_food)
  - get_large_cd_supergroup(): 매핑 함수 (zfill 패딩, NULL 안전)
  - FEATURE_NAMES: 36 -> 41개 (is_lcd_food/snack/grocery/beverage/non_food)
  - build_features(): large_cd 파라미터 추가, 원핫 인코딩 로직

### Changed
- **src/prediction/data_provider.py**: get_product_info() SQL에 pd.large_cd 추가
- **src/prediction/ml/data_pipeline.py**: get_items_meta()에 large_cd 포함
- **src/prediction/ml/trainer.py**: 학습 데이터/build_features 호출에 large_cd 전달
- **src/prediction/improved_predictor.py**: ML 앙상블에서 large_cd=product.get("large_cd") 전달
- **tests/test_ml_predictor.py**: 피처 수 36->41 반영 (3건)

### Tests
- **tests/test_ml_feature_largecd.py**: 31개 신규 테스트 (매핑/피처/배치/모델/데이터)
- 전체 ML 테스트 69개 통과

---

## [2026-03-01] - category-drilldown 카테고리 3단계 드릴다운 API

### Added
- **src/web/routes/api_category.py**: 카테고리 드릴다운 REST API Blueprint 신규 생성
  - `GET /api/categories/tree` -- 대분류->중분류->소분류 전체 트리 구조
  - `GET /api/categories/<level>/<code>/summary` -- 매출/폐기/재고 요약 (large/mid/small)
  - `GET /api/categories/<level>/<code>/products` -- 상품 목록 (페이지네이션+정렬)
  - SQL injection 방지 (ALLOWED_SORT_COLUMNS 화이트리스트)
  - level 유효성 검증 (400 반환)

- **tests/test_api_category.py**: 12개 테스트 케이스
  - tree: 빈 DB, 응답 구조, 집계 정확성
  - summary: large/mid/small 레벨, 잘못된 level, 없는 코드
  - products: 기본 목록, 페이지네이션, 정렬, 잘못된 level

### Changed
- **src/web/routes/__init__.py**: category_bp Blueprint 등록 추가

---

## [2026-02-27] - order-promo-fix 단품별 발주 행사 정보 수집 수정

### Fixed
- **src/collectors/order_prep_collector.py**: 행사 컬럼 인덱스 오류
  - 원인: gdList 컬럼 getColID(11)=ORD_UNIT(발주단위명), getColID(12)=ORD_UNIT_QTY를 행사로 착각
  - 결과: 행사 컬럼 getColID(34)=MONTH_EVT, getColID(35)=NEXT_MONTH_EVT로 수정
  - 영향: 발주단위명('낱개','묶음','BOX') 오염 30,822건 → 0건 정리, PromotionAdjuster 재활성화

- **src/infrastructure/database/repos/promotion_repo.py**: 저장 전 유효성 검증 추가
  - _is_valid_promo_type() 함수 추가, save_monthly_promo() early return 게이트
  - 무효값('낱개','BOX' 등, 순수숫자) 저장 방지, warning 로그 기록

- **scripts/clean_promo_data.py**: 오염 데이터 정리 스크립트 생성
  - promotions DELETE 8,558건(46513), 6,578건(46704)
  - promotion_changes DELETE 4,277건, 3,289건
  - daily_sales/product_details NULL 처리 3,041건, 3,054건, 2,025건
  - 유효 레코드 8건 보존(1+1: 5, 2+1: 3, 할인: 3)
  - Dry-run 안전 모드(--execute 필수)

### Added
- **tests/test_promo_validation.py**: 행사 유효성 검증 테스트
  - _is_valid_promo, _is_valid_promo_type 파라미터화 테스트 (32건)
  - Valid: "1+1","2+1","할인","덤", Invalid: "낱개","묶음","BOX","1","12"
  - Source code inspection: MONTH_EVT/NEXT_MONTH_EVT 존재확인, getColID(11/12) 부재확인
  - Mock test: repo 무효값 거부, 유효값 수락

---

## [2026-02-26] - new-product-lifecycle 신제품 초기 모니터링 및 라이프사이클 관리

### Added
- **NewProductMonitor service** — 신제품 감지 후 14일 초기 모니터링 기간 자동 관리
  - 일별 판매/재고/발주 데이터 자동 수집 (daily_sales + realtime_inventory + order_tracking)
  - 6-state lifecycle machine: detected→monitoring→(stable/no_demand/slow_start)→normal
  - 유사상품(같은 mid_cd) 일평균 자동 계산 (중위값, 30일 윈도우)
  - 모니터링 상태 자동 전환 (14일 경과 + 판매 추이 기반)

- **NewProductDailyTrackingRepository** — 일별 추적 데이터 저장소
  - new_product_daily_tracking 테이블 (item_cd, tracking_date, sales_qty, stock_qty, order_qty)
  - UPSERT 저장, 판매일수 집계, 총판매수량 계산

- **ImprovedPredictor order boost** — 신제품 초기 발주량 보정
  - monitoring 상태 + data_days < 7 → 보정 적용
  - 공식: max(similar_item_avg * 0.7, base_prediction)
  - 캐시 기반 로딩 (per prediction run)

- **Web API endpoints** (2개)
  - GET /api/receiving/new-products/monitoring — 모니터링 중 상품 목록 + 상태별 요약
  - GET /api/receiving/new-products/<item_cd>/tracking — 일별 추이 차트 데이터

- **DB Schema v46**
  - detected_new_products에 7개 컬럼 추가:
    - lifecycle_status, monitoring_start_date, monitoring_end_date
    - total_sold_qty, sold_days, similar_item_avg, status_changed_at
  - new_product_daily_tracking 신규 테이블 (+ 2 인덱스)
  - STORE_SCHEMA + STORE_INDEXES 반영

- **Phase 1.35 scheduler integration**
  - Phase 1.3 (NewProductCollector) 뒤, Phase 1.5 (EvalCalibrator) 앞
  - 일일 07:00 자동 실행 (daily_job.py)
  - 모니터링 통계 로깅 (active_items, tracking_saved, status_changes)

- **Test Coverage** — 20개 신규 테스트
  - Monitor tests: 8개 (상태 전환, 데이터 수집, 유사상품 계산)
  - Booster tests: 5개 (보정 적용, 캐시, 조건부 skip)
  - Repository tests: 4개 (lifecycle 쿼리, tracking UPSERT, summary)
  - API+Schema tests: 3개 (endpoints, schema v46 migration)

### Changed
- **src/application/services/new_product_monitor.py** (신규, 253줄)
- **src/infrastructure/database/repos/np_tracking_repo.py** (신규, 166줄)
- **detected_new_product_repo.py**: get_by_lifecycle_status, update_lifecycle, get_monitoring_summary 추가
- **improved_predictor.py**: _apply_new_product_boost + _load_new_product_cache 추가 (cache dict)
- **daily_job.py**: Phase 1.35 블록 삽입 (lines 326-341)
- **api_receiving.py**: 2개 endpoint 추가 (monitoring + tracking)
- **constants.py**: DB_SCHEMA_VERSION 45→46
- **models.py**: SCHEMA_MIGRATIONS[46] 추가 (7 ALTER + CREATE TABLE + CREATE INDEX)
- **schema.py**: STORE_SCHEMA, STORE_INDEXES 업데이트

### Files Added
```
+ src/application/services/new_product_monitor.py     # 신규 (253줄) — NewProductMonitor 서비스
+ src/infrastructure/database/repos/np_tracking_repo.py # 신규 (166줄) — 추적 저장소
+ tests/test_new_product_lifecycle.py                  # 신규 (700줄) — 20개 테스트
```

### Quality Metrics
- **Design Match Rate**: 97% (96 check items)
  - Exact match: 90 items (93.8%)
  - Changed (trivial): 3 items (3.1%) — design underspecification
  - Added (bonus): 2 items (2.1%) — performance indexes
  - Bugs found: 1 item (1.0%) — auto_order_items table ref → fixed
  - Missing: 0 items
- **Test Coverage**: 20/20 passing (100%)
- **Total Test Suite**: 2,274 → 2,294 tests (all passing)
- **Backward Compatibility**: 0 broken tests
- **Architecture Compliance**: 100% (correct layer placement)
- **Convention Compliance**: 100% (naming, docstring, no hardcoded values)

### Bug Found & Fixed
- **auto_order_items → order_tracking** (MEDIUM severity)
  - Design referenced `auto_order_items.order_qty`, but table has no order_date/order_qty columns
  - Fixed: _get_order_map() now queries order_tracking table
  - Impact: Tracking records now properly capture daily order quantities
  - Tests: All 20 tests validate this data path

### Verification
- Gap Analysis: 97% Match Rate (PASS, ≥90% required)
- Test Execution: 2,294/2,294 passing (100%)
- Iteration Count: 0 (no Act phase iteration needed)
- PDCA Completion: Plan → Design → Do → Check → Report (same day)

### PDCA Completion
- ✅ Plan: `docs/01-plan/features/new-product-lifecycle.plan.md`
- ✅ Design: `docs/02-design/features/new-product-lifecycle.design.md`
- ✅ Check: `docs/03-analysis/new-product-lifecycle.analysis.md` (97% match)
- ✅ Act: `docs/04-report/features/new-product-lifecycle.report.md` (completion report)

### Next Steps
1. Production deployment (schema v46 migration)
2. Monitor lifecycle state transitions in real data
3. Tune boost factor (0.7×) based on actual performance
4. Consider per-category monitoring periods (currently fixed at 14 days)

---

## [2026-02-26] - category-level-prediction 카테고리 총량 기반 신선식품 과소발주 보정

### Added
- **Category Demand Forecaster** — 신선식품(001~005) 카테고리 총량 예측 기반 발주 보정
  - 카테고리 일별 총매출 시계열에서 WMA 계산
  - 개별 예측 합 vs 카테고리 총량 비교 (threshold=0.7)
  - 부족분 자동 분배 (최근 판매 빈도순, 최대 +1개/품목)
- **WMA None-day Imputation** — 신선식품 레코드 부재일(stock_qty is None)도 품절로 취급
  - 기존: stock_qty is None → sale_qty=0 (수요 없음 취급)
  - 변경: 신선식품 001~005에만 None일도 imputation 대상 포함
  - 예상 개선: WMA 0.27 → 0.85 (215% 증가)
- **Test Coverage** — 20개 신규 테스트
  - test_category_demand_forecaster.py: 15개 (WMA 계산, 부족분 보충, 필터링, 분배)
  - test_wma_none_imputation.py: 5개 (None imputation, 신선식품/비식품 분기)

### Changed
- **src/prediction/improved_predictor.py** (Line 854-894)
  - calculate_weighted_average() 메서드: 신선식품 None-day imputation 로직 추가
  - mid_cd 파라미터 추가, fresh_food_mids 조건 분기
- **src/order/auto_order.py** (Line 65, 136, 992-1007)
  - CategoryDemandForecaster import 추가 (Line 65)
  - __init()__에서 _category_forecaster 인스턴스 생성 (Line 136)
  - get_recommendations() 마지막에 supplement_orders() 호출 (Line 992-1007)
  - 보충 전/후 수량 로깅 + exception wrapper로 안정성 강화
- **src/prediction/prediction_config.py** (Line 506-513)
  - category_floor 설정 블록 추가 (enabled, target_mid_cds, threshold, max_add_per_item, wma_days, min_candidate_sell_days)

### Files Added
```
+ src/prediction/category_demand_forecaster.py    # 신규 (287줄) — CategoryDemandForecaster 클래스
+ tests/test_category_demand_forecaster.py        # 신규 (350줄) — 15개 테스트
+ tests/test_wma_none_imputation.py               # 신규 (120줄) — 5개 테스트
```

### Performance Impact
- **WMA 개선** (신선식품): 0.27 → 0.85 (215% 증가)
- **발주량 증가** (예상): 주먹밥 4개 → 10개 (150%)
- **재고보유율** (예상): 31~39% → 55~70% (안정화)
- **Query 추가**: 카테고리 총 매출 집계 2개 쿼리 (매장 DB, 인덱스 활용)

### Verification
- Gap Analysis: 97% Match Rate (48개 항목 중 47개 정확히 일치, 1개 사소한 변경)
- Test Coverage: 20/20 신규 테스트 통과 + 기존 2216개 무손상 (총 2236개 100% 통과)
- Architecture Compliance: 100% (신선식품만 영향, 비식품 무영향)

### Risk & Mitigation
- **위험**: 과발주로 폐기 증가
  - 완화: threshold=0.7 (보수적), max_add_per_item=1 (상한), enabled 설정으로 즉시 롤백 가능
- **위험**: 기존 예측 로직 회귀
  - 완화: 신선식품만 대상 (mid_cd 조건), 비식품 기존 로직 유지, try/except 예외 격리

### Next Steps
1. 실운영 모니터링 (threshold, max_add 최적화)
2. 매장별/카테고리별 차등 파라미터 적용 검토
3. 비식품 카테고리 대상 확대 검토 (과자, 음료 등)

---

## [2026-02-25] - inventory-ttl-dashboard 대시보드 완성

### Added
- **Inventory TTL Dashboard** — 재고 수명 시각화 (P1 우선순위 기능)
  - API 2개 엔드포인트: GET /api/inventory/ttl-summary, GET /api/inventory/batch-expiry
  - 요약 카드 4개: 총 상품, 스테일 경고, 오늘 만료, TTL 분포
  - Chart.js 차트 3개: 신선도 도넛, TTL 분포 바, 배치 만료 타임라인
  - 스테일 상품 테이블 (검색/정렬)
  - 매장별 데이터 격리 + 인증 보호

### Changed
- **src/web/routes/__init__.py**: inventory_bp Blueprint 등록
- **src/web/templates/index.html**: 재고 서브탭 추가 (analytics 탭 내 6번째 위치)
- **src/web/static/css/dashboard.css**: .inventory-status-badge 스타일 추가
- **src/web/static/js/app.js**: 매장 변경 시 inventory dashboard 새로고침

### Files Added
```
+ src/web/routes/api_inventory.py            # 신규 (180줄) — 2 API endpoints
+ src/web/static/js/inventory.js             # 신규 (220줄) — 차트 렌더링
+ tests/test_inventory_ttl_dashboard.py      # 신규 (280줄) — 10 tests
```

### Quality Metrics
- **Design Match Rate**: 98% (1 minor store-change event gap → fixed post-analysis)
- **Test Coverage**: 10 new tests (all passing)
- **Total Test Suite**: 2169 → 2179 tests
- **Gap Found/Fixed**: store-change event not triggering refresh (resolved via JavaScript event binding)

### PDCA Completion
- ✅ Plan: `docs/01-plan/features/inventory-ttl-dashboard.plan.md`
- ✅ Design: `docs/02-design/features/inventory-ttl-dashboard.design.md`
- ✅ Do: 7 files implemented (3 new + 4 modified)
- ✅ Check: 98% match rate, gap fixed
- ✅ Report: `docs/04-report/features/inventory-ttl-dashboard.report.md`

---

## [2026-02-25] - health-check-alert 시스템 구현 완료

### Added
- **Custom Exception Hierarchy** (`src/core/exceptions.py`)
  - AppException 부모클래스 (context parameter 지원)
  - 7개 도메인별 예외: DBException, ScrapingException, ValidationException, PredictionException, OrderException, ConfigException, AlertException
  - 문법: `raise DBException("message", store_id=123, item_cd="8800123")`
- **Health Check API** (`src/web/routes/api_health.py`)
  - `/api/health` — 외부 모니터링용 간단 상태 (status, timestamp, version, uptime_seconds)
  - `/api/health/detail` — 내부 진단용 상세 정보 (DB, scheduler, disk, recent_errors, cloud_sync)
  - 상태 로직: healthy/degraded/unhealthy
- **Error Alerting Handler** (`src/utils/alerting.py`)
  - logging.Handler 상속, ERROR 레벨만 처리
  - 중복 억제: 동일 메시지 300초 내 재발송 방지
  - 시간당 제한: 최대 20개 알림/시간
  - 파일 로깅: `logs/alerts.log`
  - Kakao 알림 (config/kakao_token.json 존재 시 자동 연동)
  - Factory 패턴: `create_alerting_handler()` 함수
- **SHA256 Database Backup Verification** (`scripts/sync_to_cloud.py`)
  - `CloudSyncer.compute_sha256()` static method — 8192바이트 청크 기반 계산
  - 업로드 시 SHA256 해시 반환 및 로깅
  - 무결성 검증용 파일 손상 감지 가능

### Changed
- **src/utils/logger.py**: AlertingHandler 자동 통합 (setup_logger 끝)
- **src/web/routes/__init__.py**: health_bp Blueprint 등록 (url_prefix="/api/health")
- **scripts/sync_to_cloud.py**: SHA256 계산 + result dict에 "sha256" 필드 추가

### Test Coverage
- **Total Tests**: 2139 → 2159 (+20)
- **test_health_check_alert.py**: 20개 테스트 (모두 passing)
  - Custom exceptions: 5개
  - Health endpoints: 5개 (simple 3 + detail 2)
  - AlertingHandler duplication: 4개
  - AlertingHandler rate limit: 2개
  - SHA256 computation: 2개
  - sync_all SHA256 integration: 2개

### Quality Metrics
- **Design Match Rate**: 100% (73/73 items matched)
- **Gap Items**: 0 missing, 0 changed, 8 positive enhancements
- **Architecture Compliance**: 100% (correct layer placement)
- **Convention Compliance**: 100% (naming + docstring + no hardcoded secrets)

### Documentation
- Plan: `docs/01-plan/features/health-check-alert.plan.md`
- Design: `docs/02-design/features/health-check-alert.design.md`
- Analysis: `docs/03-analysis/features/health-check-alert.analysis.md`
- Report: `docs/04-report/features/health-check-alert.report.md`

### Files Modified/Created
```
+ src/core/exceptions.py                           # 신규 (68줄) — AppException + 7 subclass
+ src/web/routes/api_health.py                     # 신규 (233줄) — 2 endpoints + 5 checkers
+ src/utils/alerting.py                            # 신규 (127줄) — AlertingHandler + helpers
+ tests/test_health_check_alert.py                 # 신규 (419줄) — 20 tests
~ src/core/__init__.py                             # 수정 (+5줄) — Exception imports
~ src/utils/logger.py                              # 수정 (+6줄) — AlertingHandler integration
~ src/web/routes/__init__.py                       # 수정 (+2줄) — health_bp registration
~ scripts/sync_to_cloud.py                         # 수정 (+15줄) — SHA256 compute_sha256()
```

**Total LOC**: 868줄 (+68 new, +23 modified)

### PDCA Completion
- ✅ Plan: Approved (4 goals + integration rationale)
- ✅ Design: Draft (8 sections, 73 items detailed)
- ✅ Do: Complete (7 files implemented, 0 rework)
- ✅ Check: Pass (100% match rate, gap-detector verified)
- ✅ Act: Complete (lessons learned + retrospective)

---

## [2026-02-14] - 문서-코드 정합성 정리

### Removed (Dead Code)
- **auto_order.py**: `clear_old_inventory_data()` — 정의만 되고 호출되지 않는 dead code 제거
- **order_prep_collector.py**: `clear_old_data()` — 동일 이유
- **inventory_repo.py**: `clear_old()` — 위 2개의 기반 메서드, 외부 호출 없음
- **prediction_config.py**: `__main__` 테스트 블록 560줄 제거 (운영에서 미실행)

### Fixed
- **prediction_config.py**: deprecated 경고를 제거하고 역할 명확화
  - 원인: deprecated 표시했으면서 계절계수 등 신규 기능 계속 추가하는 모순
  - 수정: 경고 제거, 파일 역할(파라미터, 패턴분석, 계절/요일계수) 명시

### Changed (문서 업데이트)
- **bgf-database.md**: 스키마 버전 v18 → v27 업데이트, DB 경로 common.db+stores/ 반영, 마이그레이션 이력 v19~v27 추가
- **web-dashboard.md**: 파일명 수정 (home.py → api_home.py 등), api_prediction.py 추가
- **bgf-order-flow.md**: 적응형 블렌딩(4-0), 계절계수(6-1) 섹션 추가, ML Feature 25개 목록 추가
- **CLAUDE.md**: 핵심 플로우 설명 보강 (4단계 → WMA→블렌딩→계절→트렌드→ML앙상블)

---

## [2026-02-14] - 기간대비 예측 개선 (Phase A+B)

### Added
- **prediction_config.py**: 7개 카테고리 그룹별 월간 계절 계수 테이블 (`SEASONAL_COEFFICIENTS`)
  - beverage(여름 1.30/겨울 0.80), frozen(여름 1.50/겨울 0.60), food(안정 0.95~1.05)
  - beer(여름 1.35), soju(겨울 1.15), ramen(겨울 1.20), snack(겨울 1.08)
  - `get_seasonal_coefficient(mid_cd, month)` 함수
- **feature_builder.py**: ML Feature 22개 → 25개 확장
  - `lag_7`: 7일 전 판매량 (일평균 대비 비율 정규화)
  - `lag_28`: 28일 전 판매량 (일평균 대비 비율 정규화)
  - `week_over_week`: 전주 대비 변화율 (클리핑 -1.0~3.0)
- **trainer.py**: 학습 데이터에 lag 계산 로직 (date_to_idx 맵 기반)
- **improved_predictor.py**: predict() 흐름에 3개 단계 추가
  - `4-0. [기간대비]`: WMA + FeatureCalculator(EWM+동요일평균) 블렌딩 (품질별 10~40%)
  - `6-1. [계절계수]`: 카테고리별 월간 계절 계수 적용
  - `6-2. [트렌드조정]`: ±8~15% 트렌드 계수 적용 (7일 vs 28일 비교)

### Fixed
- **improved_predictor.py**: `feat_result` 변수 미초기화 → `feat_result = None` 초기화 추가
  - 원인: try 블록 내에서만 정의되어 except 시 ML 앙상블 단계에서 NameError 가능
- **improved_predictor.py**: `mid_cd` UnboundLocalError
  - 원인: 계절계수에서 `mid_cd` 사용했으나, 해당 변수는 함수 후반부에서 정의
  - 수정: `product["mid_cd"]`로 직접 참조

### Changed
- **test_ml_predictor.py**: feature shape 22 → 25 반영 (5개소)

### Verified (시뮬레이션 검증)
- 38개 활성 상품 대상, 2026-02-15(일요일) 예측 비교 (WMA only vs 블렌딩+계절+트렌드)
- **맥주(049)**: 평균 -22.3% (겨울 계절계수 0.78 반영)
- **탄산음료(044)**: 평균 -16.5% (겨울 계절계수 0.82 반영)
- **라면/면류(032)**: 평균 +7.6% (겨울 계절계수 1.10 반영)
- **캔디(020)**: 평균 +13.5% (겨울 1.05 + 강한 상승트렌드)
- 최대 변화: 팔리아멘트아쿠아5mg +35.7% (strong_up), 카스라이트캔500ml -34.6% (계절+strong_down)
- 계절계수가 가장 큰 영향, 트렌드 조정은 ±8~15% 범위 내 적용 확인

---

## [2026-02-14] - CUT 필터 순서 버그 수정

### Fixed
- **auto_order.py**: CUT 상품이 발주 목록에서 제외되지 않는 버그
  - 원인: `_exclude_filtered_items()` (메인 CUT 필터)가 `prefetch_pending_quantities()` (BGF 사이트 실시간 CUT 감지) **이전**에 실행됨
  - 수정: prefetch 이후 `[CUT 재필터]` 블록 추가 (Path A + Path B 양쪽)
  - 영향: 스케줄 실행 전 단품발주 화면에서 CUT 감지된 상품이 발주에 포함되던 문제 해결
- **sales_repo.py**: `_upsert_daily_sale()`에서 `is_cut_item` 명시적 처리
  - 원인: INSERT 시 `is_cut_item` 컬럼 미포함 → 기본값 0으로 삽입, ON CONFLICT에서 덮어쓰기 가능
  - 수정: INSERT에 `is_cut_item=0` 명시, ON CONFLICT에서 `is_cut_item` 업데이트 제외

---

## [2026-02-14] - 폐기추적 모듈 store_id 누락 수정

### Fixed
- **receiving_collector.py**: `update_order_tracking()` 내 2건 store_id 누락
  - `get_receiving_by_date()` 호출에 `store_id=self.store_id` 추가
  - `update_order_tracking_receiving()` 호출에 `store_id=self.store_id` 추가
  - 원인: 직접 SQL은 store_filter 적용했으나 Repository 위임 호출 시 전달 누락
  - 영향: 멀티매장 환경에서 다른 매장 입고 데이터와 혼합될 수 있었음

---

## [2026-02-04] - flow-tab 완료

### Added
- **흐름도 탭 (flow-tab)**: 대시보드에 "흐름도" 탭 추가
  - 7개 Phase 세로 타임라인 레이아웃
  - Phase 0: 07:00 스케줄러 트리거
  - Phase 1: 데이터 수집 (로그인, 판매데이터, DB저장)
  - Phase 1.5: 평가 보정 (자동보정, 리포트)
  - 카카오톡 수집 리포트 발송
  - Phase 2: 자동 발주 (예측, 카테고리별 로직, 사전평가, 미입고, 발주실행)
  - Phase 3: 실패 사유 수집 (조건부: fail_count > 0)
  - 결과 출력 (카카오 알림, 대시보드)

- **flow.js**: Step 호버 시 툴팁 표시
  - `initFlowTooltips()` 함수로 동적 생성
  - `data-file` (파일 경로) / `data-desc` (설명) / `data-time` (스케줄) 지원
  - `escapeHtml()` XSS 방지 함수 포함

- **CSS flow-* 클래스** (232줄 추가):
  - `.flow-timeline`: 세로 타임라인 컨테이너
  - `.flow-phase`: Phase 카드 + 좌측 4px 색상바
  - `.flow-phase-header`: Phase 제목 + 아이콘
  - `.flow-phase-trigger`: Phase 0 (트리거) 별도 색상
  - `.flow-step`: 세부 단계
  - `.flow-step-sub`: Phase 2 하위 단계 들여쓰기
  - `.flow-connector`: 연결선 + 화살표 (6개)
  - `.flow-condition-diamond`: 조건부 분기 (다이아몬드)
  - 색상 체계: Gray(트리거) / Blue(수집) / Indigo(보정) / Green(발주) / Orange(카카오) / Red(실패) / Purple(결과)

- **web-dashboard.md**: 프론트엔드 아키텍처 기술 문서 (350줄)
  - 탭 구조 및 SPA 패턴
  - CSS 네이밍 규칙 및 변수 매핑
  - JS 파일 역할 분담
  - API 엔드포인트 20개 정리
  - 새 탭 추가 체크리스트
  - 모듈별 색상 배정표

- **반응형 디자인**: 모바일 (max-width 768px)에서 타임 표시 숨김
- **다크/라이트 모드**: CSS 변수 활용 (하드코딩 없음)

### Changed
- **index.html**: nav-tabs에 "흐름도" 탭 추가 (line 29)
  ```html
  <a href="#" class="nav-tab" data-tab="flow">흐름도</a>
  ```

- **dashboard.css**: flow 관련 CSS 232줄 추가 (lines 1599-1830)

### Quality Metrics
- **Design Match Rate**: 97% (PASS 기준: 90%)
- **HTML 검증**: 100% (nav-tab 추가, 7개 phase 렌더링)
- **CSS 검증**: 100% (180줄+, flow- prefix, 색상 체계)
- **JS 검증**: 87% (경미한 gap: .flow-tooltip-title CSS 미사용)
- **반응형/다크모드**: 100%

### Files Modified/Created
```
+ src/web/static/js/flow.js                    # 신규 (48줄)
~ src/web/templates/index.html                 # 수정 (+207줄)
~ src/web/static/css/dashboard.css             # 수정 (+232줄)
+ .claude/skills/web-dashboard.md              # 신규 (350줄)
+ docs/03-analysis/flow-tab.analysis.md        # 신규 분석 문서
+ docs/04-report/flow-tab.report.md            # 신규 완료 보고서
```

**Total LOC**: 837줄

### PDCA Completion
- ✅ Plan: 구두 요청 (별도 문서 없음)
- ✅ Design: web-dashboard.design.md 참조
- ✅ Do: 전체 구현 완료
- ✅ Check: Gap Analysis 97% Match
- ✅ Act: Completion Report 작성

---

## Future Releases

### v1.1 (계획)
- [ ] flow-tab.plan.md 추가
- [ ] flow-tab.design.md 분리 (web-dashboard에서 독립)
- [ ] CSS/JS 모듈 분리 (dashboard.css 대규모 리팩토링)
- [ ] 자동 탭 검증 도구 (lint-flow-tabs.js)
- [ ] 색상 팔레트 config.json 중앙화

### v2.0 (계획)
- [ ] Vue/React 컴포넌트화
- [ ] 동적 Phase 추가/편집 UI
- [ ] 실시간 Phase 상태 업데이트 (WebSocket)
- [ ] 흐름도 내보내기 (PNG/SVG)

---

## [2026-02-02] - web-dashboard 기본 구현

### Added
- Flask 기반 웹 대시보드 서버
- 발주 컨트롤 탭 (파라미터 조정, 예측 실행, 결과 테이블)
- 리포트 탭 (일일/주간/카테고리/영향도)
- REST API 엔드포인트 (발주 5개, 리포트 5개)
- 다크 테마 CSS (base.html 기반)
- Chart.js 차트 통합

### Files Added
```
+ src/web/
+ src/web/__init__.py
+ src/web/app.py
+ src/web/routes/
+ src/web/routes/__init__.py
+ src/web/routes/pages.py
+ src/web/routes/api_order.py
+ src/web/routes/api_report.py
+ src/web/templates/index.html
+ src/web/static/css/dashboard.css
+ src/web/static/js/app.js
+ src/web/static/js/order.js
+ src/web/static/js/report.js
+ scripts/run_dashboard.pyw
```

### API Endpoints
- GET `/` - 메인 대시보드
- GET/POST `/api/order/params` - 파라미터 조회/저장
- POST `/api/order/predict` - 예측 실행
- POST `/api/order/adjust` - 발주량 수동 조정
- GET `/api/order/categories` - 카테고리 목록
- GET `/api/report/daily` - 일일 발주 데이터
- GET `/api/report/weekly` - 주간 트렌드
- GET `/api/report/category/<mid_cd>` - 카테고리 분석
- GET `/api/report/impact` - 영향도 비교
- POST `/api/report/baseline` - Baseline 저장

---

## Notes

- 모든 변경사항은 PDCA 사이클에 따라 문서화됨
- Design Match Rate는 Check phase의 Gap Analysis 결과
- 추가 개선사항은 "Future Releases"에서 추적
