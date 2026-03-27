# CU 디저트 발주 유지/정지 판단 시스템 — 구현 명세서

**버전 1.2 | 2026-03-04**
**설계 원본**: `bgf_auto/docs/dessert_order_decision_logic.md`

---

## Context

BGF리테일 보고서에 따르면 편의점 인기 상품 PLC가 22개월→4개월로 급감. 디저트(014) 174개 상품의 발주 유지/정지를 카테고리별 차등 주기로 자동 판단하는 시스템 필요.

---

## 1. 신규 DB 테이블 (v52, store DB)

### dessert_decisions 테이블 — 판단 이벤트 기록

```sql
CREATE TABLE IF NOT EXISTS dessert_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    item_cd TEXT NOT NULL,
    item_nm TEXT,
    mid_cd TEXT DEFAULT '014',             -- 향후 카테고리 확장 대비
    dessert_category TEXT NOT NULL,        -- A/B/C/D
    expiration_days INTEGER,
    small_nm TEXT,
    lifecycle_phase TEXT NOT NULL,         -- new/growth_decline/established
    first_receiving_date TEXT,
    first_receiving_source TEXT,           -- detected_new_products/daily_sales/products
    weeks_since_intro INTEGER DEFAULT 0,
    judgment_period_start TEXT NOT NULL,
    judgment_period_end TEXT NOT NULL,
    total_order_qty INTEGER DEFAULT 0,     -- 발주수량 (판매율 계산용)
    total_sale_qty INTEGER DEFAULT 0,
    total_disuse_qty INTEGER DEFAULT 0,
    sale_amount INTEGER DEFAULT 0,
    disuse_amount INTEGER DEFAULT 0,
    sell_price INTEGER DEFAULT 0,
    sale_rate REAL DEFAULT 0.0,            -- 판매수량 ÷ (판매수량 + 폐기수량)
    category_avg_sale_qty REAL DEFAULT 0.0,
    prev_period_sale_qty INTEGER DEFAULT 0,
    sale_trend_pct REAL DEFAULT 0.0,
    consecutive_low_weeks INTEGER DEFAULT 0,
    consecutive_zero_months INTEGER DEFAULT 0,
    decision TEXT NOT NULL,                -- KEEP/WATCH/STOP_RECOMMEND
    decision_reason TEXT,
    is_rapid_decline_warning INTEGER DEFAULT 0,
    operator_action TEXT,                  -- CONFIRMED_STOP/OVERRIDE_KEEP/null
    operator_note TEXT,
    action_taken_at TEXT,
    judgment_cycle TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(store_id, item_cd, judgment_period_end)
);
```

### 변경 사항 (v1.0 대비)
- `mid_cd` 컬럼 추가 — 향후 간편식/음료 등 확장 시 테이블 재생성 불필요
- `total_order_qty` 컬럼 추가 — 판매율 계산의 분모 명시

### 수정 파일

| 파일 | 변경 |
|---|---|
| `src/settings/constants.py:241` | `DB_SCHEMA_VERSION = 52` |
| `src/infrastructure/database/schema.py` | STORE_SCHEMA에 DDL + 인덱스 추가 |
| `src/infrastructure/database/connection.py:32` | STORE_TABLES에 `'dessert_decisions'` 추가 |
| `src/db/models.py` | SCHEMA_MIGRATIONS[52] 추가 |

---

## 2. 신규 파일 구조 (8개)

```
src/prediction/categories/dessert_decision/    # Domain 패키지
├── __init__.py
├── enums.py          # DessertCategory(A/B/C/D), DessertLifecycle, DessertDecisionType
├── models.py         # DessertItemContext, DessertSalesMetrics, DessertDecisionResult
├── classifier.py     # classify_dessert_category(small_nm, expiration_days) → A/B/C/D
├── lifecycle.py      # determine_lifecycle(first_date, ref_date, category) → phase, weeks
└── judge.py          # judge_category_a/b/c/d() 순수 판단 함수

src/infrastructure/database/repos/dessert_decision_repo.py   # Repository
src/application/services/dessert_decision_service.py         # 오케스트레이션
src/application/use_cases/dessert_decision_flow.py           # Use Case (스케줄러용)
src/web/routes/api_dessert_decision.py                       # Flask Blueprint
```

배치 근거: `src/domain/` 디렉토리가 실제 없음 → 기존 `src/prediction/categories/` 하위에 패키지 생성

---

## 3. 구현 단계 (Step-by-Step)

### Step 1: Domain 순수 로직 (I/O 없음)

#### enums.py — 4개 Enum

```python
class DessertCategory(str, Enum):  # A(냉장디저트), B(상온≤20일), C(상온>20일), D(젤리/푸딩+냉동)
class DessertLifecycle(str, Enum): # new, growth_decline, established
class DessertDecisionType(str, Enum): # KEEP, WATCH, STOP_RECOMMEND
class JudgmentCycle(str, Enum):    # weekly, biweekly, monthly
```

#### classifier.py — 카테고리 분류 (순수 함수)

**[v1.1 변경] 소분류명 1차 → 유통기한 2차 분류 방식으로 수정**

실제 데이터 분석 결과, 소분류 "냉장디저트"에 유통기한 9~24일 상품이 20개 존재. 유통기한만으로 분류하면 오분류 발생. 따라서 소분류명을 1차 기준으로 사용.

```python
# 분류 우선순위:
# 1차: small_nm (소분류명) 기반
#   - "냉장디저트" → A (유통기한 무관)
#   - "냉장젤리,푸딩" → D
#   - "냉동디저트" → D (유통기한 80일+ 동등 취급)
#   - "상온디저트" → 2차 판단으로
# 2차: expiration_days 기반 (상온디저트만)
#   - ≤ 20일 → B
#   - > 20일 → C
# 3차: 안전장치 — 소분류와 유통기한 불일치 보정
#   - 분류 결과가 C 또는 D인데, expiration_days ≤ 20 → A로 상향
#   - 분류 결과가 C 또는 D인데, expiration_days 21~79 → B로 상향
#   - WARNING 로그 기록

SMALL_NM_CATEGORY_MAP = {
    "냉장디저트": DessertCategory.A,
    "냉장젤리,푸딩": DessertCategory.D,  # 실제 소분류명에 쉼표 포함
    "냉동디저트": DessertCategory.D,
}
ROOM_TEMP_THRESHOLD = 20  # 상온디저트 B/C 분류 기준일
SAFETY_SHORT_THRESHOLD = 20   # 안전장치: 이 이하면 A로 상향
SAFETY_MEDIUM_THRESHOLD = 79  # 안전장치: 이 이하면 B로 상향

def classify_dessert_category(small_nm: str, expiration_days: int | None) -> DessertCategory:
    """
    소분류명 1차, 유통기한 2차, 안전장치 3차 기준으로 카테고리 분류.
    
    3차 안전장치: C/D로 분류되었는데 유통기한이 짧으면 상향 조정
    - C/D + 유통기한 ≤20일 → A (폐기 리스크 최대로 간주)
    - C/D + 유통기한 21~79일 → B
    - 현재 데이터에 해당 케이스 없음, 미래 신상품 대비
    
    예외 처리:
    - small_nm이 None/빈값 → expiration_days 기반 폴백
    - expiration_days가 None/0 → 소분류명 기반 분류, 둘 다 없으면 A (보수적)
    """
```

#### lifecycle.py — 생애주기 판별 (순수 함수)

| 생애주기 | 카테고리 A | 카테고리 B | 카테고리 C/D |
|---|---|---|---|
| new | 4주 이내 | 3주 이내 | 4주 이내 |
| growth_decline | 4~8주 | 3~8주 | 4~8주 |
| established | 8주+ | 8주+ | 8주+ |

#### judge.py — 카테고리별 판단 규칙 (순수 함수)

| 카테고리 | 판단 기준 |
|---|---|
| A (weekly) | 신상품=유지+급락경고(주간 하락 50%↓), 성장기=판매율 50% 미만 2주 연속→정지, 정착기=카테고리 평균 30% 미만→정지, **공통: 폐기금액>판매금액→즉시정지** |
| B (biweekly) | 신상품(3주)=유예, 이후=판매율 40% 미만 3주 연속→정지 |
| C (monthly) | 신상품(4주)=유예, 이후=카테고리 평균 20% 미만→정지 |
| D (monthly) | 월판매 0개 2개월 연속→정지 |

**[v1.1 추가] 판매율 계산식 정의**

```python
def calc_sale_rate(sale_qty: int, disuse_qty: int) -> float:
    """
    판매율 = 판매수량 ÷ (판매수량 + 폐기수량)
    
    - 분모가 0이면 (판매도 폐기도 없음) → 0.0 반환
    - 이 방식을 쓰는 이유: daily_sales에 발주수량 컬럼이 없을 수 있고,
      입고수량 ≈ 판매+폐기+재고이므로 판매/(판매+폐기)가 가장 실용적
    """
    total = sale_qty + disuse_qty
    if total == 0:
        return 0.0
    return round(sale_qty / total, 4)
```

**[v1.1 추가] 연속 미달 판단 방식 변경**

```python
# 기존: dessert_decisions 이전 레코드의 consecutive_low_weeks를 읽어서 +1
# 변경: 매번 판단 시점에 daily_sales를 직접 조회하여 최근 N주 역산
#
# 이유: 스케줄러 누락/서비스 다운 시 연속 카운트가 끊기는 문제 방지
# 방법: _aggregate_metrics()에서 최근 N주 데이터를 각각 집계한 뒤,
#       각 주의 판매율이 기준 미달인지 판단하여 연속 횟수 직접 계산

def count_consecutive_low_weeks(
    weekly_sale_rates: list[float],  # 최근 N주의 판매율 (최신→과거 순)
    threshold: float                  # 기준 판매율
) -> int:
    """최신 주부터 거꾸로 세면서 threshold 미만인 연속 주 수 반환"""
    count = 0
    for rate in weekly_sale_rates:
        if rate < threshold:
            count += 1
        else:
            break
    return count
```

---

### Step 2: DB 스키마 마이그레이션

| 파일 | 변경 |
|---|---|
| `constants.py` | `DB_SCHEMA_VERSION` 51→52, `DESSERT_DECISION_ENABLED = True` |
| `schema.py` | STORE_SCHEMA += dessert_decisions DDL + 4개 인덱스 |
| `connection.py` | STORE_TABLES += `'dessert_decisions'` |
| `models.py` | SCHEMA_MIGRATIONS[52] |

---

### Step 3: Repository

`dessert_decision_repo.py` (BaseRepository, db_type="store")

| 메서드 | 설명 |
|---|---|
| `save_decisions_batch()` | 배치 UPSERT |
| `get_latest_decisions()` | 상품별 최신 결과 |
| `get_confirmed_stop_items()` | **[v1.1 변경]** `operator_action='CONFIRMED_STOP'`인 것만 반환 (OrderFilter용) |
| `get_stop_recommended_items()` | STOP_RECOMMEND 상태 전체 (대시보드 표시용) |
| `update_operator_action()` | 운영자 확인 기록 |

**[v1.1 변경]** `get_consecutive_low_weeks()`, `get_consecutive_zero_months()` 메서드 삭제 — judge.py에서 daily_sales 직접 역산 방식으로 변경되었으므로 불필요

---

### Step 4: Application 서비스

`dessert_decision_service.py` — 오케스트레이션

| 메서드 | 설명 |
|---|---|
| `_load_dessert_items()` | common.db products+product_details WHERE mid_cd='014' |
| `_resolve_first_receiving_date()` | 3소스 우선순위 (detected_new_products > MIN(daily_sales) > products.created_at) |
| `_classify()` | **[v1.1]** `classifier.py` 호출 — `small_nm` 1차, `expiration_days` 2차 |
| `_determine_lifecycle()` | `lifecycle.py` 호출 |
| `_aggregate_metrics()` | daily_sales에서 기간별 집계 (판매율, 폐기금액, 추세) |
| `_aggregate_weekly_rates()` | **[v1.1 신규]** 최근 N주 각각의 판매율 집계 → 연속 미달 역산용 |
| `_judge()` | `judge.py` 호출 |
| `_save()` | DessertDecisionRepository |

**[v1.1 추가] 예외 처리 규칙**

```python
# 유통기한 NaN 또는 0인 상품:
#   - small_nm으로 분류 가능하면 → small_nm 기준 분류
#   - small_nm도 없으면 → 카테고리 A (보수적, 가장 엄격한 기준 적용)
#   - 로그에 WARNING 기록

# 판매가 0인 상품:
#   - 폐기금액 > 판매금액 판단 시 제외 (판매가 미등록 상품)
#   - 판매율 기반 판단만 수행
```

`dessert_decision_flow.py` — Use Case 래퍼

```python
class DessertDecisionFlow:
    def __init__(self, store_id, **kwargs): ...
    def run(self, target_categories=None, **kwargs) -> dict: ...
```

---

### Step 5: OrderFilter 연동

**[v1.1 변경] 운영자 확인 후에만 발주 차단**

설계서에서 "사람 최종 확인" 단계를 명시했으므로, STOP_RECOMMEND만으로는 발주를 막지 않습니다. 운영자가 CONFIRMED_STOP으로 확인한 상품만 OrderFilter에서 제외합니다.

```python
# order_filter.py 삽입 위치: 기존 stopped_items 필터 뒤
#
# 흐름:
# 1. dessert_decisions에서 decision='STOP_RECOMMEND' → 대시보드에 표시
# 2. 운영자가 API로 CONFIRMED_STOP 처리
# 3. OrderFilter에서 CONFIRMED_STOP인 상품만 제외
#
# 운영자가 OVERRIDE_KEEP으로 처리한 상품은 제외하지 않음
```

| 수정 파일 | 변경 |
|---|---|
| `src/order/order_filter.py` | dessert stop 필터 블록 추가 — `get_confirmed_stop_items()` 조회 |
| `src/infrastructure/database/repos/order_exclusion_repo.py` | `ExclusionType.DESSERT_STOP = "DESSERT_STOP"` 추가 |

---

### Step 6: 스케줄러 등록

수정 파일: `run_scheduler.py`

```python
schedule.every().monday.at("22:00").do(dessert_weekly)     # Cat A
schedule.every().monday.at("22:15").do(dessert_biweekly)   # Cat B (격주 처리 필요)
schedule.every().day.at("22:30").do(dessert_monthly)       # Cat C/D (매월 1일만)
```

**[v1.1 추가] 격주(biweekly) 처리 방법**

```python
def dessert_biweekly():
    """Cat B 격주 실행 — ISO 주차 기반 짝수주 판단"""
    current_week = datetime.now().isocalendar()[1]
    if current_week % 2 != 0:
        logger.info("홀수주 — Cat B 판단 스킵")
        return
    # 실제 판단 실행
    DessertDecisionFlow(store_id).run(target_categories=["B"])
```

수정 파일: `src/application/scheduler/job_definitions.py` — dessert_decision_weekly JobDefinition 추가

---

### Step 7: Web API

`api_dessert_decision.py` (Flask Blueprint, url_prefix="/api/dessert-decision")

| 엔드포인트 | 설명 |
|---|---|
| `GET /latest` | 최신 판단 결과 (decision별 필터 가능) |
| `GET /history/<item_cd>` | 상품별 판단 이력 |
| `GET /summary` | 카테고리별 집계 — KEEP/WATCH/STOP_RECOMMEND 건수 (대시보드용) |
| `POST /action/<decision_id>` | 운영자 확인 — CONFIRMED_STOP 또는 OVERRIDE_KEEP |
| `POST /run` | 수동 실행 (target_categories 파라미터) |

수정 파일: `src/web/routes/__init__.py` — Blueprint 등록

---

## 4. 수정 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `src/settings/constants.py` | DB_SCHEMA_VERSION=52, DESSERT_DECISION_ENABLED |
| `src/infrastructure/database/schema.py` | STORE_SCHEMA += dessert_decisions DDL (mid_cd, total_order_qty 포함) |
| `src/infrastructure/database/connection.py` | STORE_TABLES += 'dessert_decisions' |
| `src/db/models.py` | SCHEMA_MIGRATIONS[52] |
| `src/infrastructure/database/repos/__init__.py` | DessertDecisionRepository export |
| `src/infrastructure/database/repos/order_exclusion_repo.py` | ExclusionType.DESSERT_STOP |
| `src/order/order_filter.py` | dessert stop 필터 블록 추가 (CONFIRMED_STOP만) |
| `src/application/scheduler/job_definitions.py` | JobDefinition 추가 |
| `run_scheduler.py` | 3개 스케줄 래퍼 등록 (biweekly ISO주차 체크 포함) |
| `src/web/routes/__init__.py` | Blueprint 등록 |

---

## 5. 기존 시스템과의 관계

| 대상 | 관계 |
|---|---|
| `src/prediction/categories/dessert.py` (기존) | **변경 없음** — 안전재고 계산용으로 계속 사용 |
| `detected_new_products` | 1순위 첫입고일 소스로 **읽기만** (변경 없음) |
| `daily_sales` | 판매/폐기 집계 + 연속 미달 역산용으로 **읽기만** (변경 없음) |
| `OrderFilter` | DESSERT_STOP 필터 추가 — **CONFIRMED_STOP만 반영** (기존 필터 체인 뒤에 삽입) |
| `AppSettings` | DESSERT_DECISION_ENABLED 토글로 기능 on/off |

---

## 6. 검증 방법

| 구분 | 수량 | 대상 |
|---|---|---|
| 단위 테스트 | ~30개 | classifier (소분류명 기반 분류 + NaN 폴백), lifecycle, judge, calc_sale_rate, count_consecutive_low_weeks |
| Repository 테스트 | ~10개 | CRUD, get_confirmed_stop_items vs get_stop_recommended_items 구분 |
| 통합 테스트 | ~15개 | DessertDecisionService 전체 플로우 (mock DB) |
| OrderFilter 테스트 | ~5개 | CONFIRMED_STOP만 제외되는지, OVERRIDE_KEEP은 통과하는지 |
| API 테스트 | ~8개 | Flask Blueprint 엔드포인트 |
| 스케줄러 테스트 | ~3개 | biweekly ISO주차 판단, monthly 1일 판단 |
| 수동 검증 | - | `python run_scheduler.py`에서 dessert decision 수동 트리거 확인 |
| 전체 회귀 | - | 기존 테스트 2936개 + 신규 ~71개 전부 통과 확인 |

---

## 7. 변경 이력

### v1.0 → v1.1

| # | 변경 | 이유 |
|---|---|---|
| 1 | classifier: 유통기한 단독 → **소분류명 1차 + 유통기한 2차** | 냉장디저트 중 유통기한 9~24일 상품 20개 오분류 방지 |
| 2 | 판매율 계산식 명시: `판매수량 ÷ (판매수량 + 폐기수량)` | 계산식 미정의로 인한 구현 혼선 방지 |
| 3 | 연속 미달 판단: 이전 레코드 참조 → **daily_sales 직접 역산** | 스케줄러 누락 시 연속 카운트 끊김 방지 |
| 4 | biweekly 스케줄: ISO 주차 기반 짝수주 판단 로직 명시 | schedule 라이브러리 격주 미지원 대응 |
| 5 | OrderFilter: STOP_RECOMMEND → **CONFIRMED_STOP만 반영** | 설계서 "사람 최종 확인" 원칙 준수 |
| 6 | 냉동디저트(1개) → D 카테고리 명시 | 소분류 "냉동디저트" 처리 누락 방지 |
| 7 | 유통기한 NaN / 판매가 0 예외 처리 규칙 추가 | 실제 데이터 결측치 대응 |
| 8 | `dessert_decisions` 테이블에 `mid_cd`, `total_order_qty` 컬럼 추가 | 카테고리 확장 대비 + 판매율 분모 명시 |

### v1.1 → v1.2

| # | 변경 | 이유 |
|---|---|---|
| 9 | classifier에 **3차 안전장치** 추가: C/D + 유통기한≤20일→A, 21~79일→B로 상향 | 향후 소분류와 유통기한 불일치 신상품 대비 (현재 데이터에는 해당 없음 확인 완료) |
| 10 | `SAFETY_SHORT_THRESHOLD=20`, `SAFETY_MEDIUM_THRESHOLD=79` 상수 추가 | 안전장치 기준값 명시 |
