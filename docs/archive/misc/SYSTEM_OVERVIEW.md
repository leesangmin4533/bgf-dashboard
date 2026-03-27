# BGF 리테일 자동 발주 시스템 — 구현 로직 요약

> 최종 업데이트: 2026-02-14

---

## 1. 시스템 개요

CU 편의점 다매장 자동 발주 시스템.
BGF 리테일 사이트에 자동 로그인하여 판매 데이터를 수집하고,
카테고리별 맞춤 알고리즘으로 발주량을 예측하여 실제 발주까지 자동 실행한다.

```
기술 스택: Python 3.12 / Selenium / SQLite / Flask / Schedule / sklearn
운영 매장: 46513(CU 동양대점), 46704(CU 호반점) — 병렬 실행
```

---

## 2. 일일 자동 발주 전체 흐름 (매일 07:00)

```
run_scheduler.py (07:00 트리거)
  └─ DailyCollectionJob.run_optimized() — 매장별 병렬 실행
       │
       ├─ [Phase 1] 데이터 수집
       │    SalesCollector → BGF 사이트 로그인
       │    → 전날+당일 판매 데이터 수집 (51개 중분류)
       │    → daily_sales 테이블 저장
       │
       ├─ [Phase 1.1] 입고 데이터 수집
       │    ReceivingCollector → 센터매입 조회/확정
       │    → receiving_history 저장
       │
       ├─ [Phase 1.2] 발주 제외 상품 수집
       │    OrderStatusCollector → 자동발주/스마트발주 제외 목록
       │    → auto_order_items, smart_order_items 저장
       │
       ├─ [Phase 1.5] 예측 평가 보정
       │    EvalCalibrator → 어제 예측 vs 오늘 실적 비교
       │    → 파라미터 자동 조정 (인기도 가중치, 품절 임계값 등)
       │
       ├─ [Phase 1.6] 예측 실적 소급
       │    AccuracyTracker → prediction_logs.actual_qty 채우기
       │
       ├─ [Phase 1.7] 예측 로깅
       │    ImprovedPredictor.predict_and_log()
       │    → prediction_logs 기록 (자동발주와 독립)
       │
       ├─ [Phase 2] 자동 발주 실행
       │    AutoOrderSystem → 예측 → 사전평가 → 필터 → 조정 → 입력
       │    → BGF 발주 화면에서 상품코드+수량 Selenium 입력
       │    → 저장 버튼 클릭
       │    → OrderDiffTracker.save_snapshot() → 발주 스냅샷 저장
       │
       └─ [Phase 3] 실패 사유 수집
            FailReasonCollector → 발주 실패 상품 사유 기록
```

---

## 3. 예측 알고리즘

### 3.1 기본 예측 공식

```
발주량 = (일평균판매 x 요일계수 + 안전재고) x 비용계수 - 현재고 - 미입고
```

### 3.2 상세 처리 단계

**(1) 데이터 준비**
- 최근 31일 판매 데이터 조회
- 품절일 제외 (비품절일 평균으로 대체)
- 일평균 판매량 산출

**(2) 카테고리별 Strategy (15개)**

각 카테고리별 고유 로직으로 안전재고와 요일계수를 적용:

| Strategy | 대상 | 핵심 로직 |
|----------|------|----------|
| **FoodStrategy** | 도시락,주먹밥,김밥,샌드위치,햄버거,빵 | 유통기한별 안전재고 차등 (0.5~2일치) |
| **BeerStrategy** | 맥주 | 금/토 2.5배 증량, 주말 3일분 안전재고 |
| **SojuStrategy** | 소주 | 금/토 증량, 맥주와 유사하나 별도 계수 |
| **TobaccoStrategy** | 담배, 전자담배 | 보루 단위 소진 패턴, max 30개 |
| **RamenStrategy** | 조리면, 면류 | 회전율 기반, max 5일치 |
| **BeverageStrategy** | 음료류 (5개 중분류) | 기온/계절 기반 계수 적용 |
| **PerishableStrategy** | 유제품, 반찬, 신선식품 | 요일별 폐기율 반영 보수적 예측 |
| **FrozenIceStrategy** | 냉동식품, 아이스크림 | 계절 가중치 (여름 증가, 겨울 감소) |
| **InstantMealStrategy** | 즉석밥/국/조리 | 유통기한 그룹별 분기 |
| **DessertStrategy** | 디저트 | 유통기한 + 회전율 조정 |
| **SnackConfectionStrategy** | 과자/간식 (8개 중분류) | 프로모션 민감도 높음 |
| **AlcoholGeneralStrategy** | 양주, 와인 | 주말 집중, 보수적 |
| **DailyNecessityStrategy** | 생활/세탁/위생용품 | 품절 방지 최우선 |
| **GeneralMerchandiseStrategy** | 잡화/비식품 | 최소 재고 유지 |
| **DefaultStrategy** | 소모품, 미분류 | 기본 예측 |

**(3) Feature Engineering (22개)**
- Lag Features: 7일전, 14일전 판매량
- Rolling Features: 7일 이동평균, 표준편차, 추세
- EWM (지수가중이동평균) 예측
- 동일요일 4주 평균
- 유통기한, 폐기율, 마진율, 기온

**(4) CostOptimizer — 마진x회전율 2D 매트릭스**

마진율(high/mid/low)과 회전율(high/mid/low)의 조합으로 발주 계수를 결정:

```
margin_multiplier: 0.80 ~ 1.35  (고마진+고회전 → 적극 발주)
disuse_modifier:   0.80 ~ 1.20  (저마진+저회전 → 보수적)
skip_offset:      -0.70 ~ 0.70  (SKIP 판정 완화/강화)
```

판매비중 보너스: 중분류 내 30일 매출 비중 10% 이상이면 +0.05

**(5) 사전 발주 평가 (PreOrderEvaluator)**

각 상품별 5단계 결정:

| 결정 | 의미 | 기준 |
|------|------|------|
| **FORCE_ORDER** | 강제 발주 | 재고 0 + 인기도 상위 |
| **URGENT_ORDER** | 긴급 발주 | 노출일수 < 1일 |
| **NORMAL_ORDER** | 일반 발주 | 표준 조건 충족 |
| **PASS** | 보류 (최소 1~3개) | 재고 여유 있으나 보충 필요 |
| **SKIP** | 제외 | 재고 충분 또는 저회전 |

평가 지표: 품절빈도, 노출일수(재고/일평균), 인기도(백분위), 7일/30일 트렌드

**(6) 발주량 조정**
- 미입고 수량 차감
- 행사 상품 최소 3개 보장
- 푸드류 일일상한선: cap = 일평균 + 3
- 배수 올림 (주문 단위), max_stock 초과 시 내림
- PASS 상품도 최소 1개 보장 (전량 소멸 방지)

---

## 4. BGF 사이트 발주 입력 (Phase 2 최적화)

BGF 리테일은 넥사크로 기반으로 일반 Selenium 선택자가 작동하지 않음.
JS DOM 조작(input.value=)도 불가 → Selenium ActionChains(실제 키보드 이벤트)로 처리:

```
1. 상품코드 입력: Ctrl+A → Delete → send_keys(바코드) → Enter
2. 로딩 대기: 상품 정보 표시 확인
3. 수량 입력:  Ctrl+A → Delete → send_keys(배수) → Enter
4. 다음 상품으로 반복 (상품당 ~3~4초)
5. 전체 입력 완료 후 저장 버튼 클릭
```

---

## 5. 발주 차이 분석 & 피드백 (DiffFeedback)

자동발주 예측량과 사용자 확정발주 간 차이를 추적하여, 다음 예측에 피드백하는 학습 루프.

```
[Day N] 발주 실행 → OrderDiffTracker.save_snapshot() → order_snapshots 저장
[Day N+1] 입고 수집 → OrderDiffTracker.compare_and_save() → order_diffs + summary 저장
[Day N+2~] 예측 시 → DiffFeedbackAdjuster → 제거 페널티 / 추가 부스트 적용
```

**차이 분류 (diff_type)**:
- `unchanged`: 시스템 발주 = 사용자 확정 (정상)
- `qty_changed`: 사용자가 수량 변경
- `added`: 사용자가 상품 추가
- `removed`: 사용자가 상품 삭제
- `receiving_diff`: 확정 수량 ≠ 실제 입고량

**피드백 적용** (14일 rolling window):
- 제거 3회 이상 → 수량 30% 감소 (penalty=0.7)
- 제거 6회 이상 → 수량 50% 감소 (penalty=0.5)
- 제거 10회 이상 → 수량 70% 감소 (penalty=0.3)
- 추가 3회 이상 → 발주 후보 자동 주입

**DB**: `data/order_analysis.db` (운영 DB와 완전 분리)

---

## 6. 스케줄 작업 전체 목록

| 시간 | 작업 | 설명 |
|------|------|------|
| 06:30 | 카카오 토큰 갱신 | access_token 사전 갱신 |
| **07:00** | **일일 수집 + 자동 발주** | **전체 파이프라인 (Phase 1~3)** |
| 07:30 | 배송 확인 (2차) | 2차 배송 도착 후 배치 동기화 |
| 08:00 | 수동 발주 감지 | 입고 데이터 기반 수동 발주 탐지 |
| 09:00 | 사전 수집 (10시 폐기용) | 판매 데이터 수집 |
| 09:30 | 폐기 알림 (10시) | 10:00 만료 상품 알림 |
| 11:00 | 벌크 상품 정보 수집 | 유통기한 미등록 상품 일괄 수집 |
| 13:00 | 사전 수집 (14시 폐기용) | 판매 데이터 수집 |
| 13:30 | 폐기 알림 (14시) | 14:00 만료 상품 알림 |
| 20:30 | 배송 확인 (1차) | 1차 배송 도착 후 배치 동기화 |
| 21:00 | 사전 수집 (22시 폐기용) + 수동 발주 감지 | 판매 수집 + 탐지 |
| 21:30 | 폐기 알림 (22시) | 22:00 만료 상품 알림 |
| 22:00 | 사전 수집 (빵 자정 만료용) | 판매 데이터 수집 |
| 23:00 | 폐기 보고서 (엑셀) + 빵 알림 | 일일 폐기 현황 엑셀 생성 |
| 23:30 | 배치 만료 처리 | 유통기한 지난 배치 expired 처리 |
| 월 01:00 | 사전 수집 (02시 폐기용) | 판매 데이터 수집 |
| 월 01:30 | 폐기 알림 (02시) | 02:00 만료 상품 알림 |
| 월 03:00 | ML 모델 재학습 | RF+GB 앙상블 주간 학습 |
| 월 08:00 | 주간 종합 리포트 | 카테고리+트렌드+정확도 리포트 |

---

## 7. 폐기 추적 시스템

### 6.1 FIFO 배치 관리
- 입고 시점에 `inventory_batches` 에 배치 등록 (입고일 + 유통기한)
- 판매 시 FIFO 순서로 소비 (가장 오래된 배치부터 차감)
- 유통기한 만료 시 잔여수량을 폐기 기록으로 전환

### 6.2 폐기 알림
- 폐기 시간 30분 전 카카오톡 알림 발송
- 푸드류: 시간 기반 (차수별 폐기 시간: 10시, 14시, 22시, 02시)
- 빵: 자정 만료 → 23:00 알림
- 알림 레벨: CRITICAL (1시간 이내) / WARNING (2시간 이내) / INFO

### 6.3 폐기 보고서
- 매일 23:00 엑셀 파일 자동 생성
- 상품별 폐기 건수, 수량, 금액, 폐기율
- 카테고리별 집계, 주간 트렌드 차트

---

## 8. 데이터베이스 구조

### 공통 DB (`data/common.db`)
상품 마스터, 중분류, 상품 상세(원가/마진/유통기한), 날씨, 앱 설정, 매장 목록

### 매장별 DB (`data/stores/{store_id}.db`)
판매, 발주, 재고, 입고, 행사, 예측, 평가 — 총 15개 테이블 (매장별 완전 격리)

### 분석 DB (`data/order_analysis.db`)
자동발주 스냅샷, 사용자 수정 차이(diff), 일별 요약 — 3개 테이블 (운영 DB와 완전 분리)

### 자동 라우팅
```python
# 테이블 종류에 따라 자동으로 적절한 DB 연결
conn = DBRouter.get_connection(store_id="46513", table="daily_sales")  # → stores/46513.db
conn = DBRouter.get_connection(table="products")                        # → common.db
```

---

## 9. 웹 대시보드

Flask 기반, `http://0.0.0.0:5000`

| 페이지 | 기능 |
|--------|------|
| 홈 대시보드 | 스케줄러 상태, 마지막 발주 결과, 요약 통계, 폐기 위험 상품 |
| 발주 관리 | 예측 실행, 카테고리별 필터, 제외 상품 관리, 파라미터 조정 |
| 예측 분석 | 적중률 요약, 일일 MAPE 추이, 카테고리별 정확도 |
| 리포트 | 정확도 리포트, 폐기 현황, 주간 종합 리포트 |
| 아키텍처 | 시스템 구조도, 데이터 흐름도 |

캐싱: `/status` 5초, `/categories` 60초

---

## 10. ML 모듈

- **모델**: RandomForest + GradientBoosting 앙상블
- **카테고리 그룹**: food / alcohol / tobacco / perishable / general
- **Feature**: 22개 (lag, rolling, EWM, 유통기한, 폐기율, 마진, 기온 등)
- **손실 함수**: 비대칭 quantile loss (카테고리별 alpha — 폐기보다 품절이 더 큰 손실)
- **학습 주기**: 매주 월요일 03:00
- **데이터 파이프라인**: `src/ml/data_pipeline.py` → `feature_builder.py` → `model.py` → `trainer.py`

---

## 11. 자동 보정 시스템 (EvalCalibrator)

매일 아침 자동 실행:
1. 어제의 예측 결정(FORCE/URGENT/NORMAL/PASS/SKIP)을 오늘 실적과 비교
2. 누적 적중률 계산
3. 50건 이상 누적 시 자동 파라미터 조정:
   - 인기도 가중치 (상관계수 비례 재배분)
   - 노출시간 임계값 (SKIP 후 품절 분포 기반)
   - 품절빈도 임계값
4. `eval_params.json` 에 보정 결과 반영

---

## 12. 다매장 병렬 실행

```python
# MultiStoreRunner (ThreadPoolExecutor)
# stores.json에 등록된 모든 활성 매장을 병렬 실행
_runner = MultiStoreRunner(max_workers=4)
_runner.run_parallel(task_fn=my_task, task_name="daily_order")
```

- 매장별 독립 DB → 데이터 충돌 없음
- StoreContext (frozen dataclass) → 멀티스레드 안전
- 12개 스케줄 작업 전부 매장별 병렬화
- 토큰 갱신, ML 학습은 글로벌 (1회 실행)

---

## 13. 알림 시스템

카카오톡 알림 (KakaoNotifier):
- **발주 완료 알림**: 성공/실패 건수, 발주 총액
- **폐기 위험 알림**: 만료 임박 상품 목록 (시간대별)
- **일일 리포트**: 매출 요약, 전일 대비 변동
- **행사 변경 알림**: 프로모션 시작/종료/변경
- **주간 리포트**: 카테고리 트렌드, TOP 급등/급락 상품, 예측 정확도

토큰 관리: 매일 06:30 자동 갱신, 실패 시 Selenium 재인증

---

## 14. 실행 방법

```bash
# 스케줄러 시작 (07:00 자동 실행, 모든 작업 포함)
python run_scheduler.py

# 즉시 실행 (수집 + 발주)
python run_scheduler.py --now

# 특정 매장만 즉시 실행
python run_scheduler.py --now --store 46513

# 폐기 알림 테스트
python run_scheduler.py --expiry 22

# 주간 리포트
python run_scheduler.py --weekly-report

# 웹 대시보드
python -m src.web.app
```

---

## 15. 핵심 성과 지표 — 푸드 카테고리 전후 비교

자동발주 최초 기록: 2026-01-26 (order_tracking 기준)

| 지표 | Before (30일: 12/27~01/25) | After (18일: 01/26~02/12) | 변화 |
|------|:---:|:---:|:---:|
| 일평균 판매량 | 34.5개 | 40.4개 | **+17.3%** |
| 일평균 발주량 | 8.6개 | 18.0개 | +109.3% |
| 일평균 입고량 | 16.5개 | 24.4개 | +48.1% |
| 폐기율 (폐기/발주) | 5.81% | 4.63% | **-1.18%p** |
| 폐기 수량 (전체 기간) | 15개 | 15개 | 동일 |

> **참고**: 푸드 카테고리(도시락/주먹밥/김밥/샌드위치/햄버거/빵) 한정.
> After 기간이 18일로 짧아 계절/요일 편향 가능. 발주량이 2배 증가했으나 폐기 절대량은 동일하여 긍정적.
> 주먹밥(002) 폐기율 10.6% → 5.4%로 가장 큰 개선. 도시락(001)은 소폭 악화 (0% → 10.7%).

---

## 16. 테스트

```bash
# 전체 테스트 실행 (1,078개)
python -m pytest tests/ -x -q

# 주요 테스트 파일
tests/test_improved_predictor.py     # 예측 엔진
tests/test_pre_order_evaluator.py    # 사전 평가
tests/test_store_isolation.py        # 매장별 DB 격리
tests/test_web_api.py                # 웹 API
tests/test_reports.py                # 리포트 생성
tests/test_ml_predictor.py           # ML 모델
```
