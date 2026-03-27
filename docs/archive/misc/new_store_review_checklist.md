# BGF 자동 발주 시스템 - 새 점포 투입 전 검토 문서

> 작성일: 2026-03-04
> 대상: 새로운 CU 편의점 점포에 자동 발주 시스템을 투입하기 전 검토 사항

---

## 1. 시스템 개요

### 1.1 핵심 플로우

```
[매일 07:00 자동 실행 — 매장별 병렬]

Phase 1: 데이터 수집 (DailyCollectionJob)
  1.00  BGF 로그인 → 판매현황 조회 → 매장별 DB 저장
  1.04  시간대별 매출 수집 (STMB010 Direct API)
  1.1   입고 데이터 수집 (센터매입 조회/확정)
  1.15  폐기 전표 수집 (통합 전표 조회)
  1.16  폐기전표 → daily_sales.disuse_qty 동기화
  1.2   발주 제외 상품 수집 (자동/스마트)
  1.3   신상품 도입 현황 수집
  1.35  수요패턴 분류 (DemandClassifier)
  1.5   평가 보정 (EvalCalibrator)
  1.55  폐기 원인 분석 (WasteCauseAnalyzer)
  1.56  푸드 폐기율 보정 (FoodWasteRateCalibrator)
  1.6   예측 로깅 (PredictionLogger)
  1.61  DemandClassifier DB 연결

Phase 2: 자동 발주 (AutoOrderSystem)
  2.1   발주 추천 목록 생성 (ImprovedPredictor.predict)
  2.2   미입고/유통기한/행사 조회 (prefetch)
  2.3   미입고/재고 반영 발주량 조정
  2.4   CUT/미취급/자동발주/스마트발주 필터
  2.5   Direct API 발주 저장 (3단계 폴백: API → Batch → Selenium)

Phase 3: 후처리
  3.1   발주 실패 사유 수집
  3.2   일일 리포트 / 카카오 알림
  3.3   PythonAnywhere DB 동기화
```

### 1.2 기술 스택

| 기술 | 용도 |
|------|------|
| Python 3.12 | 메인 언어 |
| Selenium | 넥사크로 기반 BGF 스토어 시스템 스크래핑 |
| SQLite | 매장별 DB 분리 (common.db + stores/*.db) |
| Flask | 웹 대시보드 |
| Schedule | 작업 스케줄링 |
| sklearn (RF+GB) | ML 앙상블 예측 |

---

## 2. 새 점포 투입 전 필수 체크리스트

### 2.1 환경 설정 (필수)

- [ ] **stores.json에 점포 등록**
  - 위치: `config/stores.json`
  - 필수 필드: `store_id`, `store_name`, `location`, `is_active: true`
  ```json
  {
    "store_id": "XXXXX",
    "store_name": "CU XXX점",
    "location": "경기 XX시",
    "type": "일반점",
    "is_active": true,
    "description": "N호점",
    "added_date": "2026-MM-DD"
  }
  ```

- [ ] **환경변수 설정 (.env)**
  - `BGF_USER_ID_{store_id}`: BGF 리테일 로그인 ID
  - `BGF_PASSWORD_{store_id}`: BGF 리테일 로그인 비밀번호
  - `KAKAO_REST_API_KEY`: 카카오 알림 API 키 (기존 공유 가능)

- [ ] **매장별 DB 자동 생성 확인**
  - `data/stores/{store_id}.db` 파일이 첫 실행 시 자동 생성됨
  - `init_store_db(store_id)` 호출로 모든 테이블 스키마 생성
  - DB 스키마 버전: **v52** (최신 마이그레이션 자동 적용)

- [ ] **BGF 리테일 사이트 로그인 테스트**
  - 넥사크로 기반이므로 일반 Selenium 선택자 불가
  - JavaScript 직접 실행으로 넥사크로 객체 접근 필요
  - 로그인 후 메뉴 이동 가능 여부 확인

### 2.2 데이터 수집 초기화 (필수)

- [ ] **상품 마스터 수집**
  - `python scripts/collect_all_product_details.py`
  - common.db의 `products`, `product_details` 테이블에 저장
  - 상품코드, 상품명, 중분류(mid_cd), 원가, 마진, 유통기한 등

- [ ] **발주 단위 수집**
  - `python run_scheduler.py --collect-order-unit`
  - 실제 BGF 배수(6,10,12,24개) → `product_details.order_unit_qty` 저장
  - 이거 안 하면 대부분 1로 저장되어 예측 vs 실제 +98% 갭 발생

- [ ] **초기 판매 데이터 축적** (최소 7일, 권장 14일)
  - 매일 수집 실행: `python run_scheduler.py --now`
  - 또는 백필: `python scripts/run_full_flow.py --backfill --days 14`
  - 데이터 7일 미만 시: WMA 예측 정확도 저하, 요일 계수 미반영

- [ ] **날씨/캘린더 데이터 확인**
  - `external_factors` 테이블에 기온, 공휴일 자동 저장
  - BGF TopFrame에서 날씨 예보 자동 수집

### 2.3 예측 파이프라인 검증 (필수)

- [ ] **카테고리별 Strategy 매핑 확인**
  - 15개 카테고리 Strategy 자동 매핑 (strategy_registry.py)
  - 해당 점포의 주력 카테고리 확인 (푸드/담배/주류 비중)

  | 카테고리 | Strategy | 핵심 로직 |
  |---------|----------|-----------|
  | 도시락/주먹밥/김밥/샌드위치/햄버거/빵 (001~005,012) | FoodStrategy | 유통기한 기반 안전재고, 폐기율 계수, 요일 계수 |
  | 맥주 (049) | BeerStrategy | 금/토 안전재고 3일, 요일 계수(금 2.54), 최대 7일분 |
  | 소주 (050) | SojuStrategy | 금/토 안전재고 3일, 요일 패턴 |
  | 담배 (072,073) | TobaccoStrategy | 보루 패턴, 전량 소진, 상한 30개 |
  | 라면 (006,032) | RamenStrategy | 회전율 기반 |
  | 음료 (010,039,043,045,048) | BeverageStrategy | 기온/계절 영향 |
  | 냉동/아이스크림 (021,034,100) | FrozenIceStrategy | 여름 1.50, 겨울 0.60 |
  | 유제품/신선 (013,026,046) | PerishableStrategy | 유통기한 민감, 요일 가중 |
  | 과자/제과 (014~020,029,030) | SnackConfectionStrategy | 프로모션 민감 |
  | 즉석식품 (027,028,031,033,035) | InstantMealStrategy | 비상 수요 패턴 |
  | 주류일반 (052,053) | AlcoholGeneralStrategy | 저회전 |
  | 생활용품 (036,037,056,057,086) | DailyNecessityStrategy | 안정 수요 |
  | 잡화 (054~071) | GeneralMerchandiseStrategy | 최소 재고 유지 |
  | 기타 | DefaultStrategy | 기본 WMA 예측 |

- [ ] **예측 공식 이해**
  ```
  기본 예측 = WMA(7일) → Feature블렌딩(EWM+동요일) → 트렌드 조정(+-8~15%)

  계수 적용:
  - 푸드/디저트: 곱셈 방식 (base x holiday x weather x weekday x season x trend)
  - 비면제(daily/frequent): 덧셈 방식 (base x (1 + delta_sum))
  - intermittent: Croston/TSB 모델
  - slow: 예측=0 (ROP에서 1개 보장)

  최종 발주량 = 예측 + 안전재고 - 현재재고 - 미입고수량
              → 발주단위(입수)로 올림 → 최소 1, 최대 99 배수
  ```

- [ ] **후처리 파이프라인 확인**
  ```
  발주규칙 → ROP → 프로모션 → ML앙상블 → DiffFeedback →
  폐기원인피드백 → Substitution보정 → CategoryDemandForecaster(mid_cd) →
  LargeCategoryForecaster(large_cd) → 카테고리 max cap
  ```

### 2.4 발주 실행 검증 (필수)

- [ ] **드라이런 테스트** (실제 발주 안 함)
  ```bash
  python scripts/run_full_flow.py --no-collect --max-items 3
  ```
  - 예측→목록생성→미입고조회→발주량조정 전 과정을 dry-run 확인
  - 발주량이 0인 상품, 과대 발주 상품 없는지 점검

- [ ] **소규모 실제 발주 테스트**
  ```bash
  python scripts/run_full_flow.py --run --no-collect --max-items 3
  ```
  - 3개 상품만 실제 발주 실행
  - Direct API 저장 확인 (gfn_transaction)
  - 발주 후 BGF 시스템에서 발주 내역 확인

- [ ] **발주 저장 방식 확인**
  - 3단계 폴백: Direct API → Batch Grid → Selenium
  - Direct API: `gfn_transaction('save', 'stbjz00/saveOrd', inDS=':U', outDS=dsGeneralGrid)`
  - 배치 분할: 50개 초과 시 50개 단위로 청크 분할

- [ ] **발주 가능 요일 확인**
  - 기본: 전체 요일 가능 ("일월화수목금토")
  - 스낵류(015~030): 일요일 제외 ("월화수목금토")
  - 라면(006,032): 일요일 제외 ("월화수목금토")
  - BGF 검증: 실시간 발주가능요일 확인 + DB 교정

### 2.5 푸드류 총량 상한 검증 (중요)

- [ ] **food_daily_cap 동작 확인**
  ```
  cap = round(요일별_평균_판매량) + waste_buffer(3)
  ```
  - 매장 규모 자동 적응 (DB에서 해당 매장의 실제 판매량 조회)
  - 소형(일평균5개): cap=8, 중형(14개): cap=17, 대형(35개): cap=38
  - **어떤 매장이든 폐기가 최대 ~3개로 일정**

- [ ] **초기 데이터 부족 시 폴백**
  - 21일 데이터 미만: 전체 일평균 사용 (요일 구분 없이)
  - 전체 데이터 없음: `fallback_daily_avg=15` 사용
  - 2주 이상 데이터 축적 후 정상 작동

### 2.6 안전재고 / 과잉발주 방지 (중요)

- [ ] **유통기한별 안전재고**
  | 유통기한 | 안전재고 일수 | 적용 카테고리 |
  |---------|-------------|-------------|
  | 1~3일 | 0.3~0.5일 | 도시락, 김밥, 주먹밥, 빵 |
  | 4~7일 | 0.5일 | 조리면, 유제품 |
  | 8~30일 | 1.0일 | 즉석밥 |
  | 31~90일 | 1.5일 | 아이스크림 |
  | 91일+ | 2.0일 | 장기 보관 상품 |

- [ ] **과잉발주 방지 메커니즘**
  - PASS 상품 상한: 3개 (`PASS_MAX_ORDER_QTY`)
  - FORCE_ORDER 상한: 일평균 x 1.5일 (`FORCE_MAX_DAYS`)
  - 담배 상한: 30개 (`TOBACCO_MAX_STOCK`)
  - 맥주/소주 상한: 일평균 x 7일분
  - 간헐수요(판매빈도 50% 미만) FORCE_ORDER 억제

- [ ] **유통기한 1일 이하 재고 무시 발주**
  - 푸드류(001~005,012) 유통기한 1일 이하 → `effective_stock_for_need = 0`
  - 유령 재고(팔리지 않을 잔량) 때문에 발주 안 하는 문제 방지

### 2.7 ML 모델 (권장, 데이터 축적 후)

- [ ] **ML 학습 스케줄**
  - 매일 23:45: 증분학습 (30일 데이터)
  - 매주 일요일 03:00: 전체 학습 (90일 데이터)
  - 성능 보호: MAE 20% 초과 시 롤백

- [ ] **ML Feature 35개**
  - 기본 통계 5 + 트렌드 2 + 시간 5 + 행사 1 + 카테고리 5
  - 비즈니스 4 + 시계열 3 + 입고패턴 5 + 그룹컨텍스트 4
  - 이중 모델: 개별 + 그룹(small_cd/mid_cd)
  - data_confidence 블렌딩: data_days/60 기반

- [ ] **ML 콜드스타트 대응**
  - 데이터 부족 시: 규칙 기반 예측만 사용 (ML 가중치 0)
  - 데이터 14일+: ML 적응형 블렌딩 시작 (0.1~0.5)
  - small_cd → mid_cd 폴백 (유사 상품 그룹 모델)

---

## 3. 핵심 비즈니스 로직 상세

### 3.1 수요 패턴 분류 (Phase 1.61)

```
DemandClassifier: 60일 sell_day_ratio 기반

daily    (≥70%): WMA → Feature블렌딩 → 곱셈/덧셈 계수
frequent (40~69%): WMA → Feature블렌딩 → 덧셈 계수
intermittent (15~39%): Croston/TSB → 덧셈 계수
slow     (<15%): 예측=0, ROP에서 1개 보장

면제: food(001~005,012)/dessert → 기존 곱셈 파이프라인 유지
```

### 3.2 계수 체계

#### 곱셈 계수 (푸드/디저트)
- **휴일 계수**: 공휴일/연휴 기간 수요 변동
- **날씨 계수**: 기온 구간별 조정 (혹한→도시락↑, 폭염→전체↓)
- **강수 계수**: 비/눈 예보 시 카테고리별 감소
- **요일 계수**: DB 기반 4주 평균 (0.80~1.25)
- **계절 계수**: 7개 카테고리 그룹별 월간 (맥주 여름 1.35, 겨울 0.78)
- **트렌드 조정**: 최근 판매 추이 (+-8~15%)

#### 덧셈 계수 (비면제 카테고리)
```
AdditiveAdjuster: delta_sum → clamp → base x (1 + delta)
  daily:        clamp(-0.5, +0.8)
  frequent:     clamp(-0.4, +0.5)
  intermittent: clamp(-0.3, +0.3)
  slow:         0 (계수 적용 안 함)
```

### 3.3 발주 전 평가 (Pre-Order Evaluation)

```
품절위험 / 노출일수 / 인기도 기반 판정:
  FORCE_ORDER : 즉시 발주 필요 (품절 임박)
  NORMAL_ORDER: 정상 발주
  PASS        : 발주 불필요 (재고 충분)
  NO_HISTORY  : 판매 이력 없음
```

- EvalCalibrator: 매일 다음날 실제 판매 vs 예측 비교 → 자동 보정
- eval_params.json: 매장별 평가 파라미터 (매장별 분리 가능)

### 3.4 폐기 관리 시스템

#### 정밀 폐기 3단계 (10분 간격)
```
[폐기 10분 전] 판매 수집 → 최신 재고 확보
[폐기 정시]   만료 배치 판정 → 상품별 stock 스냅샷
[폐기 10분 후] 추가 판매 반영 → 실제 폐기량 확정
```

#### 폐기 시간대
| 시간 | 대상 카테고리 |
|------|-------------|
| 02:00 | 도시락/김밥 |
| 10:00 | 샌드위치/햄버거 |
| 14:00 | 도시락/김밥 |
| 22:00 | 샌드위치/햄버거 |
| 00:00 | 빵 |

#### 폐기 원인 분석
- DEMAND_DROP: 외부 요인 수요 감소 (7일 선형 감쇄)
- OVER_ORDER: 과잉 발주 (피드백 승수 0.75, 14일 유지)
- EXPIRY_MISMANAGEMENT: 유통기한 관리 (승수 0.85, 21일)

### 3.5 신상품 도입 관리

```
목표: BGF 상생지원제도 지원금 극대화 (월 최대 160,000원)

종합점수 = 도입 점수(0~80) + 달성 점수(0~20)
  95~100점: 160,000원  |  88~94점: 150,000원
  84~87점: 120,000원   |  72~83점: 110,000원
```

- 수집: STBJ460 팝업 클릭 (ActionChains 필수)
- 3일발주: 대상기간 3등분 → 3회 발주 계획
- 현재: MODULE_ENABLED=True, AUTO_INTRO_ENABLED=False

---

## 4. DB 구조

### 4.1 분할 구조

```
data/
├── common.db              # 공통 데이터 (전 매장 공유)
│   ├── products           # 상품 마스터 (item_cd, item_nm, mid_cd)
│   ├── product_details    # 상품 상세 (원가, 마진, 유통기한, order_unit_qty)
│   ├── mid_categories     # 중분류 마스터
│   ├── external_factors   # 날씨, 공휴일
│   ├── app_settings       # 전역 설정
│   └── stores             # 매장 목록
│
└── stores/
    └── {store_id}.db      # 매장별 운영 데이터
        ├── daily_sales    # 일별 판매 (sell_qty, disuse_qty)
        ├── order_tracking # 발주 추적
        ├── order_history  # 발주 이력
        ├── realtime_inventory  # 실시간 재고
        ├── inventory_batches   # FIFO 배치 (유통기한 관리)
        ├── prediction_logs     # 예측 로그
        ├── eval_outcomes       # 평가 결과
        ├── promotions          # 프로모션 정보
        ├── receiving_history   # 입고 이력
        ├── hourly_sales        # 시간대별 매출
        └── ... (총 20+ 테이블)
```

### 4.2 DB 라우팅 (자동)

```python
# 매장별 DB → WHERE store_id = ? 불필요 (DB 자체가 격리)
conn = DBRouter.get_store_connection("XXXXX")
cursor.execute("SELECT * FROM daily_sales")

# 교차 참조 (매장 DB + 공통 DB JOIN)
conn = DBRouter.get_store_connection_with_common("XXXXX")
cursor.execute("""
    SELECT ds.*, p.item_nm
    FROM daily_sales ds
    JOIN common.products p ON ds.item_cd = p.item_cd
""")
```

### 4.3 새 매장 DB 초기화 과정

1. `stores.json`에 매장 등록
2. 첫 실행 시 `init_store_db(store_id)` 자동 호출
3. `data/stores/{store_id}.db` 파일 생성 + 전체 스키마 적용
4. 마이그레이션 자동 실행 (v1 → v52)

---

## 5. 스케줄 일정

| 시간 | 작업 | 비고 |
|------|------|------|
| 00:00 | 발주단위 수집 | 전체 품목 order_unit_qty |
| 01:30 | 02:00 폐기 알림 | 도시락/김밥 |
| 07:00 | **메인 플로우** (수집+발주) | 가장 중요 |
| 07:30 | 2차 배송 배치 동기화 | |
| 08:00 | 주간 리포트 (월요일) | 카테고리+트렌드+정확도 |
| 09:30 | 10:00 폐기 알림 | 샌드위치/햄버거 |
| 11:00 | 벌크 상품 상세 수집 | 유통기한+행사 |
| 13:30 | 14:00 폐기 알림 | 도시락/김밥 |
| 20:30 | 1차 배송 배치 동기화 | |
| 21:30 | 22:00 폐기 알림 | 샌드위치/햄버거 |
| 23:00 | 폐기 보고서 + 빵 폐기 알림 | |
| 23:30 | 배치 유통기한 만료 처리 | 폴백 |
| 23:45 | ML 증분학습 | 30일 데이터 |

---

## 6. 점포 투입 시 예상되는 문제와 대응

### 6.1 콜드스타트 기간 (1~2주)

| 문제 | 원인 | 대응 |
|------|------|------|
| 예측 정확도 낮음 | 판매 데이터 부족 | 7일간은 수동 발주 병행 권장 |
| 요일 계수 미반영 | 요일별 데이터 1주 미만 | fallback_daily_avg 사용됨 |
| 푸드 cap 과다 | 실제 평균 미산출 | fallback=15 적용 (소형 매장은 과다 가능) |
| ML 미작동 | 학습 데이터 없음 | 규칙 기반만 사용, 14일 후 자동 활성화 |

**권장 대응**: 처음 7일은 `--max-items 10~20` 으로 제한 발주, 결과 확인 후 점진 확대

### 6.2 매장 특성별 주의사항

| 매장 유형 | 주의점 | 조정 포인트 |
|-----------|--------|------------|
| 학교 근처 | 스낵/음료 비중 높음, 방학 시 급감 | 계절 계수 모니터링 |
| 오피스 밀집 | 도시락/커피 주중 집중, 주말 급감 | 요일 계수 검증 |
| 주거지역 | 안정적, 주말 약간 증가 | 기본 설정 적합 |
| 유흥가 | 주류 금/토 급증 | BeerStrategy 금 2.54 확인 |
| 역세권 | 출퇴근 시간 집중 | 시간대별 매출 비율 확인 |

### 6.3 기존 매장과 다를 수 있는 점

- **배송 차수**: 1차(익일 07:00), 2차(당일 20:00) — 매장마다 배송 시간 다를 수 있음
- **취급 상품 범위**: 매장마다 취급 중분류가 다름 → strategy 자동 매핑되므로 문제 없음
- **발주 가능 요일**: BGF 시스템에서 상품별로 다를 수 있음 → 자동 검증+교정
- **스마트발주 설정**: 매장마다 스마트발주 대상 상품이 다름 → Phase 1.2에서 수집

---

## 7. 모니터링 포인트

### 7.1 투입 직후 매일 확인

```bash
# 오늘 실행 결과 확인
python scripts/log_analyzer.py --summary

# Phase별 소요시간
python scripts/log_analyzer.py --timeline

# 에러 확인
python scripts/log_analyzer.py --errors --last 24h
```

### 7.2 핵심 지표

| 지표 | 정상 범위 | 이상 시 |
|------|-----------|--------|
| 예측 정확도 (Accuracy@1) | 60%+ (2주 후) | EvalCalibrator 자동 보정 |
| 폐기율 (푸드) | 15~25% | food_daily_cap 조정 |
| 발주 성공률 | 95%+ | order_fail_reasons 확인 |
| Phase 2 소요시간 | 5~25분 | Direct API 정상 여부 |
| ML MAE | 20% 이내 | 초과 시 자동 롤백 |

### 7.3 웹 대시보드

```bash
python -m src.web.app  # Flask 대시보드 시작
```

- 홈: 파이프라인 상태, 최근 이벤트, 실패 사유
- 발주: 발주 이력, 추천 목록
- 예측: 카테고리별 정확도, 드릴다운
- 폐기: 원인 분석, 폐기율 추이
- 재고: 재고 수명 (TTL), 배치 만료 타임라인
- 설정: eval_params 편집, 기능 토글

---

## 8. 투입 절차 요약 (Step by Step)

```
Day 0: 사전 준비
  ├─ stores.json 등록
  ├─ .env 환경변수 설정
  ├─ BGF 로그인 테스트
  └─ 발주단위 수집 (--collect-order-unit)

Day 1~7: 데이터 축적 + 관찰
  ├─ 매일 run_scheduler.py --now 실행 (수집만)
  ├─ 드라이런으로 예측 결과 확인
  ├─ 수동 발주 병행
  └─ 로그 분석 (에러, Phase 소요시간)

Day 8~14: 제한적 자동 발주
  ├─ --max-items 10~20 으로 시작
  ├─ 발주 결과 vs 실제 판매 비교
  ├─ 과잉/과소 발주 패턴 확인
  ├─ EvalCalibrator 보정 결과 모니터링
  └─ 필요시 eval_params.json 매장별 조정

Day 15~: 전체 자동 발주 전환
  ├─ max-items 제한 해제
  ├─ ML 모델 자동 학습 시작
  ├─ 주간 리포트 검토
  ├─ 폐기율/정확도 지표 안정화 확인
  └─ 정상 운영 모드 전환
```

---

## 9. 긴급 대응

### 발주 중단이 필요할 때
```bash
# stores.json에서 해당 매장 비활성화
"is_active": false

# 또는 스케줄러 중지
taskkill /PID {PID} /F
```

### 잘못된 발주가 실행됐을 때
- BGF 리테일 사이트에서 직접 발주 취소 (자동 취소 기능 없음)
- `order_tracking` 테이블에서 발주 이력 확인

### 데이터 오류 시
```bash
# 매장 DB 초기화 (주의: 모든 이력 삭제)
rm data/stores/{store_id}.db
# → 다음 실행 시 자동 재생성
```

---

## 10. 설정 파일 구조

```
config/
├── stores.json              # 매장 목록 (필수)
├── eval_params.json         # 기본 평가 파라미터
├── stores/
│   └── {store_id}_eval_params.json  # 매장별 평가 파라미터 (선택)
├── kakao_tokens.json        # 카카오 알림 토큰
└── .env                     # 환경변수 (인증 정보)
```

---

> **핵심 요약**: 새 점포 투입은 `stores.json 등록 → .env 설정 → 7일 데이터 축적 → 제한적 자동발주 → 전체 전환` 순서로 진행합니다.
> 시스템은 매장 규모에 자동 적응하도록 설계되어 있으므로, 카테고리 Strategy와 food_daily_cap이 데이터 축적 후 자동으로 해당 매장에 맞춰집니다.
