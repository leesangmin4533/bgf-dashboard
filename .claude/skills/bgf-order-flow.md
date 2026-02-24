# BGF 발주 플로우 스킬

## When to Use

- 발주 로직을 수정하거나 새 카테고리 예측기를 추가할 때
- 발주량 계산 공식, 안전재고, 요일계수를 확인할 때
- `AutoOrderSystem.execute()` 흐름을 이해해야 할 때
- 폐기 알림 스케줄이나 배송 차수 정보가 필요할 때
- Feature Engineering (lag, rolling, EWM) 파라미터를 조정할 때

## Common Pitfalls

- ❌ `get_recommendations()`를 여러 번 호출 → 예측기 중복 실행으로 성능 저하
- ✅ 1회 호출 후 `_apply_pending_and_stock_to_order_list()`로 직접 조정

- ❌ `OrderPrepCollector` 조회 후 바로 발주 실행 → 화면 상태 오염
- ✅ `_ensure_clean_screen_state()` 호출로 화면 초기화 후 발주

- ❌ 미입고 수량 미반영 → 중복 발주
- ✅ 공식: `발주량 = 예측 + 안전재고 - 현재재고 - 미입고수량`

- ❌ 카테고리 로직 없이 일괄 예측 → 맥주 금요일 과소발주, 푸드 폐기 증가
- ✅ `src/prediction/categories/` 의 카테고리별 모듈 사용

- ❌ 드라이버를 모듈별로 새로 생성 → 중복 로그인, 세션 충돌
- ✅ `SalesAnalyzer.login()` 1회 후 드라이버 공유

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| 발주량이 0으로 나옴 | 현재재고+미입고 >= 예측량 | `_apply_pending_and_stock_to_order_list()` 로직 확인 |
| 금요일 맥주 발주 부족 | 요일계수 미적용 | `beer.py`의 금요일 계수 2.54 확인 |
| 푸드 폐기율 높음 | 안전재고 과다 | `food.py`의 유통기한별 safety_days 확인 |
| 담배 30개 초과 발주 | 상한선 체크 누락 | `tobacco.py`의 `skip_order` 로직 확인 |
| 발주 실행 시 화면 오류 | 이전 메뉴 탭 미닫힘 | `_ensure_clean_screen_state()` 호출 확인 |
| 행사 상품 발주량 이상 | 행사 배율 미적용 | `promotion_adjuster.py` 연동 확인 |

---

## 전체 흐름 (07:00 스케줄러)

```
run_scheduler.py
  └─ DailyCollectionJob.run_optimized()
       │
       ├─ Phase 1: 데이터 수집 (SalesCollector)
       │    └─ 매출분석 > 시간대별 매출 화면 (STAJ001_M0)
       │         └─ 중분류별 상품 판매 데이터 → daily_sales 테이블
       │
       ├─ Phase 2: 자동 발주 (AutoOrderSystem)
       │    │
       │    ├─ Step 1: 발주 추천 목록 생성 (get_recommendations)
       │    │    └─ ImprovedPredictor.predict() → 카테고리별 예측
       │    │
       │    ├─ Step 2: 미입고/유통기한/행사 조회 (OrderPrepCollector)
       │    │    └─ 발주 > 단품별 발주 화면 (STBJ030_M0)
       │    │         ├─ 미입고 수량 (dsOrderSale)
       │    │         ├─ 유통기한 (dsItem.EXPIRE_DAY)
       │    │         └─ 행사 정보 (gdList 컬럼 11, 12)
       │    │
       │    ├─ Step 3: 미입고/재고 반영하여 발주량 조정
       │    │    └─ _apply_pending_and_stock_to_order_list()
       │    │
       │    ├─ Step 4: 화면 상태 초기화
       │    │    └─ _ensure_clean_screen_state()
       │    │
       │    └─ Step 5: 발주 실행 (OrderExecutor)
       │         └─ 단품별 발주 화면에서 상품코드 입력 → 배수 입력 → 저장
       │
       └─ Phase 3: 알림 (KakaoNotifier)
            ├─ 폐기 위험 알림
            ├─ 일일 리포트
            └─ 행사 변경 알림
```

## 주요 모듈 관계

```
src/
├─ collectors/
│   ├─ sales_collector.py         # 판매 데이터 수집
│   ├─ order_prep_collector.py    # 미입고/유통기한/행사 수집
│   └─ product_info_collector.py  # 상품 상세 정보 (lazy loading)
│
├─ prediction/
│   ├─ improved_predictor.py      # 메인 예측기 (13개 카테고리 elif 체인)
│   ├─ pre_order_evaluator.py     # 사전 발주 평가 (분포 적응형 임계값)
│   ├─ eval_config.py             # 평가 파라미터 중앙 관리 (JSON)
│   ├─ eval_calibrator.py         # 사후 검증 + 자동 보정 (피드백 루프)
│   ├─ eval_reporter.py           # 일일 보정 리포트
│   ├─ prediction_config.py       # (deprecated) 구 예측 설정
│   ├─ categories/                # 카테고리별 로직 (15개)
│   │   ├─ beer.py                # 맥주 (049) - 요일 패턴
│   │   ├─ soju.py                # 소주 (050) - 요일 패턴
│   │   ├─ tobacco.py             # 담배 (072, 073) - 보루/소진 패턴
│   │   ├─ ramen.py               # 라면 (006, 032) - 회전율 기반
│   │   ├─ food.py                # 푸드 (001~005, 012) - 유통기한 기반
│   │   ├─ food_daily_cap.py      # 푸드 총량 상한 (cap=요일평균+3)
│   │   ├─ perishable.py          # 신선식품 (013, 026, 046) - 유통기한 민감
│   │   ├─ beverage.py            # 음료 (010, 039, 043, 045, 048) - 계절/온도
│   │   ├─ frozen_ice.py          # 냉동/아이스 (021, 034, 100) - 계절성
│   │   ├─ instant_meal.py        # 즉석식품 (027, 028, 031, 033, 035)
│   │   ├─ snack_confection.py    # 과자/제과 (014, 017, 018, 029, 030)
│   │   ├─ alcohol_general.py     # 주류일반 (052, 053) - 주말 패턴
│   │   ├─ daily_necessity.py     # 생활용품 (036, 037, 056, 057, 086)
│   │   ├─ general_merchandise.py # 잡화 (054~071) - 최소 재고 유지
│   │   └─ default.py             # 기본 폴백 (나머지 카테고리)
│   ├─ promotion/                 # 행사 기반 발주 조정
│   │   ├─ promotion_manager.py   # 행사 상태 조회
│   │   └─ promotion_adjuster.py  # 발주량 조정
│   ├─ features/                  # Feature Engineering
│   │   ├─ lag_features.py        # Lag Features
│   │   ├─ rolling_features.py    # Rolling 통계, EWM
│   │   └─ feature_calculator.py  # 통합 계산기
│   └─ accuracy/                  # 정확도 추적
│       ├─ tracker.py             # 정확도 메트릭 계산
│       └─ reporter.py            # 일일/주간 정확도 리포트
│
├─ order/
│   ├─ auto_order.py              # 자동 발주 시스템 (통합)
│   ├─ order_executor.py          # 발주 화면 조작
│   └─ order_unit.py              # 발주 단위 변환
│
├─ alert/
│   ├─ expiry_checker.py          # 폐기 위험 감지
│   └─ promotion_alert.py         # 행사 변경 알림
│
└─ scheduler/
    └─ daily_job.py               # 일일 작업 스케줄러
```

## 발주량 계산 공식

```
발주량 = (일평균 × 요일계수) + 안전재고 - 현재재고 - 미입고수량
       → 발주단위(입수)로 올림
       → 최소 1, 최대 99 배수
```

## 미입고 수량 계산 방식

미입고 수량은 발주했지만 아직 입고되지 않은 수량으로, 중복 발주를 방지하기 위해 발주량에서 차감됩니다.

### 복잡한 방식 (기본값, 교차날짜 보정 포함)

**위치**: `order_prep_collector.py::_calculate_pending_complex()`

**알고리즘**:
```python
# 전체 발주/입고 이력 집계
과거발주총량 = sum(발주 for 날짜 < 오늘)
과거입고총량 = sum(입고 for 날짜 < 오늘)
오늘발주총량 = sum(발주 for 날짜 == 오늘)
오늘입고총량 = sum(입고 for 날짜 == 오늘)

미입고 = max(0, 과거발주 - 과거입고) + max(0, 오늘발주 - 오늘입고)
```

**특징**:
- 모든 발주/입고 이력 집계 (보통 7~14개 날짜)
- 교차날짜 패턴 보정 (발주일 ≠ 입고일 처리)
- 디버그 로그 상세 (`data/logs/pending_debug_*.txt`)
- 코드 복잡도 높음 (~50줄)

**장점**: 교차날짜 패턴에서 정확도 높음
**단점**: 코드 복잡, 유지보수 어려움

### 단순화 방식 (실험적)

**위치**: `order_prep_collector.py::_calculate_pending_simplified()`

**알고리즘**:
```python
# 최근 3일 이내 발주 중 가장 최근 건만 확인
recent_orders = [h for h in history
                 if h['date'] >= today-3 and h['ord_qty'] > 0]
most_recent = max(recent_orders, key='date')
미입고 = max(0, most_recent['발주량'] - most_recent['입고량'])
```

**특징**:
- 최근 1건만 추적 (lookback_days=3, 기본값)
- 교차날짜 보정 없음 (단순 차감)
- 코드 간결 (~20줄)
- 성능 향상 (10-20% 예상)

**장점**: 가독성 높음, 유지보수 용이, 빠름
**단점**: 교차날짜 패턴에서 정확도 낮을 수 있음

### 전환 방법

**설정 플래그**: `src/config/constants.py`
```python
USE_SIMPLIFIED_PENDING = False  # 기본: 복잡한 방식
PENDING_LOOKBACK_DAYS = 3       # 단순화 방식 조회 기간
```

**마이그레이션 계획**:
1. Week 1-2: 병렬 추적 (두 방식 비교 로깅)
2. Week 3: A/B 테스트 (50% 상품)
3. Week 4: 전체 적용 (`USE_SIMPLIFIED_PENDING = True`)
4. Week 5+: 안정화 (2주 후 기존 코드 삭제 고려)

**롤백**: `USE_SIMPLIFIED_PENDING = False` 변경 (< 5분)

**테스트**: `tests/test_pending_simplified.py` (12개 단위 테스트)

## 안전재고 계산

### 유통기한별 기본값

| 유통기한 | 안전재고 일수 |
|---------|-------------|
| 1~3일 (초단기) | 0.5일 |
| 4~7일 (단기) | 0.5일 |
| 8~30일 (중기) | 1.0일 |
| 31~90일 (장기) | 1.5일 |
| 91일+ (초장기) | 2.0일 |

### 회전율 배수

| 일평균 판매량 | 배수 |
|-------------|------|
| 5개 이상 | ×1.5 |
| 2~5개 | ×1.2 |
| 2개 미만 | ×0.8 |

## 요일계수 (카테고리별)

### 맥주 (049)

| 월 | 화 | 수 | 목 | 금 | 토 | 일 |
|---|---|---|---|---|---|---|
| 1.15 | 1.22 | 1.21 | 1.37 | **2.54** | **2.37** | 0.97 |

### 소주 (050)

| 월 | 화 | 수 | 목 | 금 | 토 | 일 |
|---|---|---|---|---|---|---|
| 0.83 | 0.89 | 0.90 | 1.09 | **1.18** | **1.19** | 0.91 |

### 담배 (072)

| 월 | 화 | 수 | 목 | 금 | 토 | 일 |
|---|---|---|---|---|---|---|
| 0.99 | 1.15 | 1.21 | 1.24 | 1.44 | 1.47 | 1.24 |

## 카테고리별 특수 로직

### 담배 (072, 073)

```python
# 보루 패턴: 하루 10개+ AND 10의 배수
if sale_qty >= 10 and sale_qty % 10 == 0:
    carton_buffer = +5 또는 +10

# 전량 소진: 판매량 == 전일재고
if sale_qty == prev_stock and sale_qty > 0:
    sellout_multiplier = ×1.2 ~ ×1.5

# 상한선: 30개
if current_stock + pending >= 30:
    skip_order = True
```

### 맥주/소주 (049, 050)

```python
# 금/토 발주 시 안전재고 3일, 나머지 2일
if weekday in [4, 5]:  # 금, 토
    safety_days = 3
else:
    safety_days = 2

# 최대 재고: 일평균 × 7일
if current_stock + pending >= daily_avg * 7:
    skip_order = True
```

### 푸드류 (001~005, 012)

```python
# 유통기한별 안전재고
if expiration_days == 1:
    safety_days = 0.3  # 당일 폐기
elif expiration_days <= 3:
    safety_days = 0.5
elif expiration_days <= 7:
    safety_days = 1.0

# 폐기율 계수
if disuse_rate >= 0.20:
    disuse_coef = 0.5
elif disuse_rate >= 0.15:
    disuse_coef = 0.65
```

## Feature Engineering

| 파일 | 기능 | 주요 특징 |
|------|------|----------|
| `lag_features.py` | Lag Features | lag_1, lag_7, lag_14, lag_28, lag_365, 동일요일 평균 |
| `rolling_features.py` | Rolling 통계 | mean, std, min, max (7/14/28/90일), EWM |
| `feature_calculator.py` | 통합 계산기 | 적응형 가중 블렌딩, 트렌드 조정 |

### 적응형 블렌딩 (Step 4-0)

`feature_calculator.py`의 `FeatureCalculator.calculate()` 결과를 WMA 예측에 블렌딩:
- 데이터 품질별 가중치: high(14일+)=EWM 40%+동요일 40%, medium(7~13일)=EWM 35%, low(<7일)=단순평균 50%
- 블렌딩 비율: high=40%, medium=25%, low=10%, 나머지=기존 WMA

### 계절 계수 (Step 6-1)

`prediction_config.py`의 `SEASONAL_COEFFICIENTS` — 7개 카테고리 그룹별 월간 계수:

| 그룹 | 여름 피크 | 겨울 피크 | 예시 |
|------|----------|----------|------|
| beer | 1.35 (7월) | 0.78 (1월) | 맥주 049 |
| beverage | 1.30 (7월) | 0.82 (1월) | 음료 040~047 |
| frozen | 1.50 (7월) | 0.60 (1월) | 아이스크림 034 |
| soju | 0.90 (7월) | 1.15 (12월) | 소주 050 |
| ramen | 0.85 (7월) | 1.20 (12월) | 면류 032 |
| snack | 0.95 (7월) | 1.08 (12월) | 과자 015~020 |
| food | 0.95~1.05 | 안정 | 도시락/김밥 001~005 |

### 변동성 기반 안전재고 조정

| CV (변동계수) | 레벨 | 안전재고 배수 |
|--------------|------|-------------|
| < 0.3 | stable | ×1.0 |
| 0.3 ~ 0.5 | normal | ×1.2 |
| 0.5 ~ 0.8 | volatile | ×1.5 |
| > 0.8 | highly_volatile | ×2.0 |

### 트렌드 조정 계수 (Step 6-2)

| 트렌드 | 조정 |
|-------|-----|
| 강한 상승 (+20%+) | ×1.15 |
| 상승 (+10~20%) | ×1.08 |
| 안정 | ×1.0 |
| 하락 (-10~20%) | ×0.92 |
| 강한 하락 (-20%-) | ×0.85 |

### ML Feature (25개)

`ml/feature_builder.py`의 `FEATURE_NAMES`:
- 기본 통계 (5): daily_avg_7/14/31, stock_qty, pending_qty
- 트렌드 (2): trend_score, cv
- 시간 (5): weekday_sin/cos, month_sin/cos, is_weekend
- 행사 (1): promo_active
- 카테고리 원핫 (5): is_food/alcohol/tobacco/perishable/general
- 비즈니스 (4): expiration_days, disuse_rate, margin_rate, temperature
- 시계열 (3): lag_7, lag_28, week_over_week

## 폐기 알림 시스템

### 배송 차수

| 차수 | 도착 시간 | 상품명 끝자리 |
|-----|---------|-------------|
| 1차 | 익일 07:00 | "1" |
| 2차 | 당일 20:00 | "2" |

### 폐기 시간

| 카테고리 | 폐기 시간 |
|---------|---------|
| 도시락/김밥 (001, 002) | 14:00, 02:00 |
| 샌드위치/햄버거 (003, 004) | 10:00, 22:00 |

### 알림 스케줄

| 시간 | 알림 내용 |
|------|---------|
| 01:30 | 02:00 폐기 상품 |
| 09:30 | 10:00 폐기 상품 |
| 13:30 | 14:00 폐기 상품 |
| 21:30 | 22:00 폐기 상품 |

## 드라이버 재사용

```python
# 한 번 로그인으로 수집 + 발주 처리
from src.sales_analyzer import SalesAnalyzer

analyzer = SalesAnalyzer()
analyzer.login()
driver = analyzer.driver  # 로그인된 드라이버

# 수집기에 드라이버 전달
collector = SalesCollector(driver)
collector.collect()

# 발주 시스템에 드라이버 전달
system = AutoOrderSystem(driver=driver)
system.execute(dry_run=False)
```

## 메뉴 상태 관리

- `_menu_navigated`: 메뉴 이동 완료 여부 플래그
- `_date_selected`: 날짜 선택 완료 여부 플래그
- `close_menu()`: 현재 탭 닫기 + 플래그 초기화
- 메뉴 전환 시 반드시 이전 메뉴 닫기 → 새 메뉴 이동

## AutoOrderSystem.execute() 흐름

```python
def execute(order_list=None, dry_run=True, prefetch_pending=True):
    # 1. get_recommendations() - 1회만 호출
    #    └─ ImprovedPredictor.predict() per item
    #    └─ apply_food_daily_cap() 1차 적용
    # 2. prefetch_pending_quantities() - 미입고/재고 조회
    # 3. _apply_pending_and_stock_to_order_list() - 직접 반영
    #    └─ apply_food_daily_cap() 2차 적용
    # 4. _exclude_filtered_items() - 미취급/CUT/자동발주/스마트발주 제외
    # 5. _ensure_clean_screen_state() - 화면 초기화
    # 6. executor.execute_orders() - 실제 발주
```

## 사전 평가 + 사후 보정 (피드백 루프)

```
[매일 07:00]
  1. pre_order_evaluator.py   → 품절위험/노출일수/인기도 기반 필터링
  2. improved_predictor.py    → 카테고리별 예측 + 발주량 산출
  3. auto_order.py            → 발주 실행

[매일 다음날 07:00]
  4. eval_calibrator.py       → 실제 판매 vs 예측 비교 (사후 검증)
     └─ eval_outcomes 테이블 업데이트 (CORRECT/UNDER_ORDER/OVER_ORDER)
     └─ 적중률 < 임계값이면 eval_params.json 자동 조정
  5. eval_reporter.py         → 보정 리포트 생성 + 카카오 알림
```

## 새 카테고리 모듈 추가 패턴

`category_expansion_design.md` 참조. 각 카테고리 모듈은 동일 구조:

```python
# 1. 상수 정의
DEFAULT_WEEKDAY_COEF = {0: 1.0, 1: 1.0, ...}
SAFETY_STOCK_CONFIG = {...}

# 2. dataclass 결과
@dataclass
class XxxPatternResult:
    item_cd: str
    mid_cd: str
    ...

# 3. 카테고리 판별
def is_xxx_category(mid_cd: str) -> bool:

# 4. 요일 패턴 학습
def _learn_weekday_pattern(mid_cd, db_path, min_data_days=14):

# 5. 메인 분석
def analyze_xxx_pattern(item_cd, mid_cd, ...) -> XxxPatternResult:

# 6. 안전재고 계산
def get_safety_stock_with_xxx_pattern(mid_cd, daily_avg, ...) -> float:
```

improved_predictor.py의 `predict()` 메서드에 elif 분기 추가 필요.
