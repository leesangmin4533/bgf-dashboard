# BGF 리테일 자동 발주 시스템

## 프로젝트 개요

BGF 리테일(CU 편의점) 다매장 자동 발주 시스템
- **입력**: BGF 스토어 시스템의 판매/재고 데이터
- **처리**: 카테고리별 맞춤 예측 알고리즘 (Strategy 패턴, 15개 카테고리)
- **출력**: 최적 발주량 계산 및 자동 발주 실행
- **다매장**: 매장별 DB 분리 + 병렬 실행 (StoreContext)

### 핵심 플로우

```
[매일 07:00 자동 실행 — 매장별 병렬]

1. 데이터 수집 (DailyCollectionJob)
   └─ BGF 로그인 → 판매현황 조회 → 매장별 DB 저장

2. 발주량 예측 (PredictionService → ImprovedPredictor)
   └─ WMA예측 → Feature블렌딩 → 휴일/기온계수 → 요일계수
      → 계절계수(7그룹) → 트렌드조정(±8~15%) → 안전재고 → 재고차감 → ML앙상블

3. 자동 발주 (OrderService → AutoOrderSystem)
   └─ 미입고/행사 조회 → 발주량 조정 → 상품코드 입력 → 배수 입력 → 저장

4. 알림 발송 (KakaoNotifier)
   └─ 폐기 위험 알림 → 일일 리포트 → 행사 변경 알림
```

### 기술 스택

- **Python 3.12** / **Selenium** (넥사크로 기반 웹 스크래핑) / **SQLite** (매장별 DB 분리) / **Flask** (대시보드) / **Schedule**

---

## 활성 이슈 (이슈 체인 갱신 시 같이 갱신)

<!-- ISSUE_TABLE_START -->
| 상태 | 우선순위 | 이슈 | 파일 | 비고 |
|:---:|:---:|------|------|------|
| WATCHING | - | action_proposals v70 컬럼 매장 DB 미적용 | scheduling.md | 내일(04-06) 07:00 로그에서 [Verify] executed_a |
| WATCHING | - | 과도기 알림 누락 가능성 | expiry-tracking.md | 4/7 Gap 분석에서 누락 건수 확인 |
| PLANNED | P2 | CLEAR_GHOST_STOCK 자동실행 승격 검토 | scheduling.md | integrity_checks 2주 누적 데이터에서 food_ghost_ |
| PLANNED | P2 | ML is_payday DB 반영 효과 검증 | prediction.md | f0657a8 커밋 반영 후 최소 2주 운영 데이터 필요 |
| PLANNED | P2 | 폐기 알림 OT 폴백 완전 제거 | expiry-tracking.md | (밀도 승격) [WATCHING] 과도기 알림 누락 → [RESOLVED] 전환 후 |
| PLANNED | P2 | 하네스 엔지니어링 Week 3 — AI 요약 서비스 | scheduling.md | executed_at 검증 완료 (WATCHING 이슈 해결) |
| PLANNED | P2 | 행사 종료 임박 상품 발주 감량 자동화 | order-execution.md | promotions 테이블의 promo_end_date 정확성 확인 (p |
| PLANNED | P3 | PaydayAnalyzer 결과를 ML 학습 데이터에도 반영 | prediction.md | P2 효과 검증 완료 후 양수 효과 확인 시 |
| PLANNED | P3 | hourly 시간대별 판매 소급 수집 안정화 | data-collection.md | 없음 (독립 작업) |
<!-- ISSUE_TABLE_END -->

> 이 테이블은 `python scripts/sync_issue_table.py`로 자동 갱신됩니다.
> 이슈 체인 파일(docs/05-issues/) 변경 커밋 시 PostToolUse hook이 자동 실행합니다.

---

## 현재 진행 단계 (2026-04)

> DB 안정화가 최우선. 아래 순서로 진행 중.

| 단계 | 상태 | 내용 |
|------|------|------|
| 1단계 | **완료** | DB 오류 감지 (DataIntegrityService — 6개 체크) |
| 2단계 | **완료** | DB 오류 자동 보정 (AutoExecutor — 5고리 자전 시스템) |
| 3단계 | **진행 중** | DB 안정 확인 후 → 발주 품질 개선 |

### 판단 기준

1. **새 기능 요청이 와도 DB 값 신뢰성이 먼저** — 예측/발주 로직 개선보다 데이터 정합성 우선
2. **sqlite3.connect 직접 호출 금지** → `DBRouter` 또는 `get_conn()` 만 사용 (WAL + busy_timeout 보장)
3. **자동 실행은 신뢰도 높은 것만** (HIGH: `CLEAR_EXPIRED_BATCH`), 나머지는 승인 요청 (LOW)
4. **CLEAR_GHOST_STOCK은 LOW(승인 필요)** — 2주 오탐률 확인 후 HIGH 승격 검토

### 하지 말아야 할 것

- `sqlite3.connect(db_path)` 직접 호출 (→ `DBRouter.get_store_connection(store_id)` 사용)
- `_get_db_path()` 신규 사용 (deprecated → `get_conn()` 사용)
- 자전 시스템에서 실물 확인 없이 `stock_qty = 0` 자동 보정 (GHOST_STOCK은 LOW 유지)
- 파이프라인 단계 수정 시 후행 덮어쓰기 체크 생략 (CLAUDE.md 체크리스트 참조)
- `CHECK_DELIVERY_TYPE`에서 `order_source='site'` 발주 경고 (정상 데이터, 과감지)

### 자전 시스템 5고리 (Phase 1.67)

```
감지 → 판단 → 보고 → 실행 → 검증
 │       │       │       │       │
 │       │       │       │       └─ ExecutionVerifier (다음날 Phase 1.67a)
 │       │       │       └─ AutoExecutor (Phase 1.67c)
 │       │       └─ KakaoNotifier (DataIntegrityService 내부)
 │       └─ ActionProposalService (중복 방지 적용)
 └─ DataIntegrityService.run_all_checks() (Phase 1.67b)
```

---

## 아키텍처

```
                ┌─────────────────────────────┐
                │    Presentation 계층         │
                │  (CLI, Web Dashboard)        │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │    Application 계층          │
                │  (Use Cases, Services,       │
                │   Scheduler)                 │
                └──────────┬──────────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
┌─────────▼──────┐ ┌──────▼──────┐ ┌───────▼──────┐
│  Domain 계층   │ │Infrastructure│ │  Settings    │
│ (Strategy,     │ │ (DB, BGF,   │ │ (Config,     │
│  Models,       │ │  Collectors, │ │  Constants,  │
│  Evaluation)   │ │  External)  │ │  StoreContext)│
└────────────────┘ └─────────────┘ └──────────────┘
```

### 계층별 역할

| 계층 | 패키지 | 역할 |
|------|--------|------|
| **Settings** | `src/settings/` | 전역 설정, 상수, StoreContext (매장 컨텍스트) |
| **Domain** | `src/domain/` | 순수 비즈니스 로직 (I/O 없음) — Strategy 패턴, 값 객체, 필터/조정 |
| **Infrastructure** | `src/infrastructure/` | 모든 I/O — DB, BGF 사이트, 수집기, 외부 API |
| **Application** | `src/application/` | 오케스트레이션 — Use Case, Service, Scheduler |
| **Presentation** | `src/presentation/` | 사용자 인터페이스 — CLI, Flask 웹 대시보드 |

---

---

## 디렉토리 구조

```
bgf_auto/
├── CLAUDE.md                 # [현재 파일] 프로젝트 개요
├── config.py                 # 전역 설정 (URL, 브라우저 옵션)
├── .env.example              # 환경변수 템플릿
├── run_scheduler.py          # 메인 스케줄러 진입점
│
├── .claude/skills/           # 상세 기술 가이드 (6개 문서)
├── config/                   # 카카오 토큰, eval_params.json, stores.json
│
├── data/                     # ★ DB 분할 구조
│   ├── common.db             # 공통 DB (products, mid_categories, external_factors 등)
│   ├── stores/               # 매장별 DB
│   │   ├── 46513.db          # CU 동양대점
│   │   └── 46704.db          # CU 호반점
│   ├── bgf_sales.db          # 레거시 통합 DB (과도기 백업)
│   ├── logs/                 # 사전 발주 평가 로그
│   ├── models/               # ML 모델
│   ├── reports/              # 리포트 출력
│   └── screenshots/          # 디버그 스크린샷
│
├── scripts/                  # 실행 스크립트
│   ├── run_auto_order.py     # 핵심: 자동 발주 실행
│   ├── run_full_flow.py      # 핵심: 전체 플로우 (수집→발주)
│   ├── run_expiry_alert.py   # 핵심: 폐기 알림
│   ├── split_db.py           # 핵심: DB 분할 마이그레이션
│   ├── collect_all_product_details.py  # 상품 정보 대량 수집
│   ├── dry_order.py          # 드라이런 테스트
│   └── archive/              # 나머지 유틸 스크립트 보관
│
└── src/
    ├── settings/             # ★ 설정 통합 (5곳 → 1곳)
    │   ├── app_config.py     # config.py + constants + timing 통합 진입점
    │   ├── constants.py      # 비즈니스 상수 (re-export)
    │   ├── timing.py         # 타이밍 상수 (re-export)
    │   ├── ui_config.py      # 넥사크로 UI ID (re-export)
    │   └── store_context.py  # StoreContext (frozen dataclass)
    │
    ├── domain/               # ★ 순수 비즈니스 로직 (I/O 없음)
    │   ├── models.py         # PredictionResult, OrderItem, CostInfo, EvalDecision, FlowResult
    │   ├── prediction/
    │   │   ├── base_strategy.py      # ABC: CategoryStrategy 인터페이스
    │   │   ├── strategy_registry.py  # mid_cd → Strategy 매핑 + create_default_registry()
    │   │   ├── strategies/           # 카테고리별 Strategy 클래스 (15개)
    │   │   │   ├── beer.py, soju.py, tobacco.py, ramen.py
    │   │   │   ├── food.py, perishable.py, beverage.py
    │   │   │   ├── frozen_ice.py, instant_meal.py, dessert.py
    │   │   │   ├── snack_confection.py, alcohol_general.py
    │   │   │   ├── daily_necessity.py, general_merchandise.py
    │   │   │   └── default.py
    │   │   ├── features/             # 피처 엔지니어링 (re-export)
    │   │   └── prediction_params.py  # 예측 하이퍼파라미터 (re-export)
    │   ├── evaluation/               # 사전 평가 (re-export)
    │   └── order/
    │       ├── order_adjuster.py     # 순수 함수: round_to_order_unit, apply_order_rules 등
    │       └── order_filter.py       # 순수 함수: filter_unavailable, filter_cut_items 등
    │
    ├── infrastructure/       # ★ 모든 I/O
    │   ├── database/
    │   │   ├── connection.py         # DBRouter: common.db vs stores/{id}.db 자동 라우팅
    │   │   ├── schema.py             # COMMON_SCHEMA + STORE_SCHEMA 분리
    │   │   ├── store_query.py        # store_filter() 헬퍼 (레거시 호환)
    │   │   ├── base_repository.py    # BaseRepository (DB 라우팅 내장)
    │   │   └── repos/                # ★ 19개 Repository (테이블별 1파일)
    │   ├── bgf_site/                 # BGF 넥사크로 상호작용 (re-export)
    │   ├── collectors/               # 데이터 수집기 (re-export)
    │   └── external/                 # 외부 API — KakaoNotifier (re-export)
    │
    ├── application/          # ★ 오케스트레이션
    │   ├── use_cases/
    │   │   ├── daily_order_flow.py       # 유일한 메인 오케스트레이터 (수집→예측→필터→실행→추적)
    │   │   ├── predict_only.py           # 예측만 (API/미리보기용)
    │   │   ├── expiry_alert_flow.py      # 유통기한 알림
    │   │   ├── batch_collect_flow.py     # 대량 상품정보 수집
    │   │   ├── weekly_report_flow.py     # 주간 리포트
    │   │   ├── waste_report_flow.py      # 폐기 리포트
    │   │   └── ml_training_flow.py       # ML 모델 학습
    │   ├── services/
    │   │   ├── prediction_service.py     # ImprovedPredictor 래핑
    │   │   ├── order_service.py          # 주문 조립+필터+조정+실행
    │   │   ├── inventory_service.py      # 재고/입고/배치 통합 조회
    │   │   ├── store_service.py          # 매장 CRUD + DB 자동 생성
    │   │   └── dashboard_service.py      # 웹 대시보드 데이터
    │   └── scheduler/
    │       ├── job_scheduler.py          # MultiStoreRunner (ThreadPoolExecutor 병렬)
    │       └── job_definitions.py        # SCHEDULED_JOBS 레지스트리 (6개 작업)
    │
    ├── presentation/         # ★ 사용자 인터페이스
    │   ├── web/
    │   │   └── (re-export → src/web/)
    │   └── cli/
    │       ├── main.py               # 단일 CLI 진입점 (argparse subcommands)
    │       └── commands/             # order, predict, report, alert, store, collect
    │
    ├── # ─── 기존 모듈 (호환 유지) ───
    ├── sales_analyzer.py     # BGF 로그인/메뉴 이동
    ├── config/               # 중앙 설정 (constants, timing, ui_config)
    ├── db/                   # 데이터베이스 레이어
    │   ├── models.py         # 스키마 정의 (v26, 마이그레이션)
    │   ├── repository.py     # 17개 Repository 클래스 (호환 shim → infrastructure/database/repos/)
    │   └── store_query.py    # store_filter 헬퍼
    ├── collectors/           # 데이터 수집기 (10개)
    ├── prediction/           # 예측 엔진 (improved_predictor, categories/, features/)
    ├── order/                # 발주 실행 (auto_order, order_executor)
    ├── alert/                # 폐기/행사 알림
    ├── web/                  # Flask 대시보드
    ├── report/               # 리포트 생성 (BaseReport 상속)
    ├── analysis/             # 데이터 분석
    ├── ml/                   # ML 모듈 (RF+GB 앙상블)
    ├── utils/                # 공유 유틸리티 (logger, screenshot, nexacro)
    ├── scheduler/            # daily_job.py
    └── notification/         # kakao_notifier.py
```

---

## DB 분할 구조

### 공통 DB (`data/common.db`)

매장 무관 전역 참조 데이터:

| 테이블 | 용도 |
|--------|------|
| `products` | 상품 마스터 (item_cd, item_nm, mid_cd) |
| `mid_categories` | 중분류 마스터 |
| `product_details` | 상품 상세 (원가, 마진, 유통기한) |
| `external_factors` | 날씨, 공휴일 |
| `app_settings` | 앱 전역 설정 |
| `schema_version` | DB 버전 관리 |
| `stores` | 매장 목록 |
| `store_eval_params` | 매장별 평가 파라미터 |

### 매장별 DB (`data/stores/{store_id}.db`)

매장 고유 운영 데이터:

| 테이블 | 용도 |
|--------|------|
| `daily_sales` | 일별 판매 |
| `order_tracking` | 발주 추적 |
| `order_history` | 발주 이력 |
| `inventory_batches` | FIFO 배치 |
| `realtime_inventory` | 실시간 재고 |
| `prediction_logs` | 예측 로그 |
| `eval_outcomes` | 평가 결과 |
| `promotions` | 프로모션 |
| `receiving_history` | 입고 이력 |
| `auto_order_items` / `smart_order_items` | 발주 항목 |
| `collection_logs` | 수집 로그 |
| `order_fail_reasons` | 발주 실패 사유 |
| `calibration_history` | 보정 이력 |

### DB 라우팅

```python
from src.infrastructure.database.connection import DBRouter

# 자동 라우팅: 테이블 종류에 따라 common.db 또는 stores/{id}.db 연결
conn = DBRouter.get_connection(store_id="46513", table="daily_sales")  # → stores/46513.db
conn = DBRouter.get_connection(table="products")                        # → common.db

# 교차 참조 필요 시: ATTACH DATABASE
conn.execute(f"ATTACH DATABASE '{DBRouter.get_common_db_path()}' AS common")
cursor.execute("SELECT ds.*, p.item_nm FROM daily_sales ds JOIN common.products p ON ...")
```

---

## 핵심 설계 패턴

### 1. CategoryStrategy (카테고리별 예측)

```python
from src.domain.prediction.strategy_registry import create_default_registry

registry = create_default_registry()  # 15개 Strategy 자동 등록
strategy = registry.get_strategy(mid_cd="049")  # → BeerStrategy
safety_stock, details = strategy.calculate_safety_stock(
    mid_cd, daily_avg, expiration_days, item_cd, current_stock, pending_qty, store_id
)
```

### 2. StoreContext (매장 컨텍스트)

```python
from src.settings.store_context import StoreContext

ctx = StoreContext(store_id="46513", store_name="CU 동양대점")
# frozen dataclass → 멀티스레드 안전, 명시적 값 전달
```

### 3. DBRouter (자동 DB 라우팅)

```python
# 매장별 DB이므로 WHERE store_id = ? 불필요
# Before: cursor.execute("SELECT * FROM daily_sales WHERE store_id = ?", (store_id,))
# After:  conn = DBRouter.get_connection(store_id=ctx.store_id)
#         cursor.execute("SELECT * FROM daily_sales")
```

### 4. DailyOrderFlow (유일한 오케스트레이터)

```python
from src.application.use_cases.daily_order_flow import DailyOrderFlow

flow = DailyOrderFlow(store_id="46513", driver=driver)
result = flow.run(dry_run=False, max_items=50)
# 수집→예측→필터→실행→추적 을 단일 플로우로 통합
```

### 5. MultiStoreRunner (병렬 실행)

```python
from src.application.scheduler.job_scheduler import MultiStoreRunner

runner = MultiStoreRunner(store_ids=["46513", "46704"])
results = runner.run_parallel(task_fn=my_task, task_name="daily_order")
```

---

## 사용법

### 스케줄러

```bash
python run_scheduler.py                           # 전체 스케줄 시작 (07:00 자동, 3매장 병렬)
python run_scheduler.py --now                     # 즉시 실행 (전체 매장, 수집→발주 전체 플로우)
python run_scheduler.py --now --store 46513       # ★ 한 점포만 즉시 실행 (07시 스케줄과 100% 동일)
python run_scheduler.py --weekly-report           # 주간 리포트
python run_scheduler.py --expiry 14               # 폐기 알림 테스트
```

> **주의**: `scripts/run_auto_order.py`는 Phase 2(발주)만 실행합니다.
> 수집/보정/재고갱신(Phase 1)이 빠져있어 07시 스케줄과 다릅니다.
> **테스트/재실행 시 반드시 `run_scheduler.py --now --store {점포코드}` 를 사용하세요.**

### 프로그래밍 API

```python
# 예측
from src.application.services.prediction_service import PredictionService
service = PredictionService(store_id="46513")
predictions = service.predict_all(min_order_qty=1)

# 전체 플로우
from src.application.use_cases.daily_order_flow import DailyOrderFlow
flow = DailyOrderFlow(store_id="46513", driver=driver)
result = flow.run(dry_run=True, max_items=10)
```

---

## 중분류 코드 참조

| 코드 | 카테고리 | Strategy | 비고 |
|------|---------|----------|------|
| 001 | 도시락 | FoodStrategy | 유통기한 1일 |
| 002 | 주먹밥 | FoodStrategy | 유통기한 1일 |
| 003 | 김밥 | FoodStrategy | 유통기한 1일 |
| 004 | 샌드위치 | FoodStrategy | 유통기한 2일 |
| 005 | 햄버거 | FoodStrategy | 유통기한 1일 |
| 006 | 조리면 | RamenStrategy | 회전율 기반 |
| 010 | 음료 | BeverageStrategy | 온도/계절 영향 |
| 012 | 빵 | FoodStrategy | 유통기한 3일 |
| 013 | 유제품 | PerishableStrategy | 요일 가중 |
| 014~020 | 과자/제과류 | SnackConfectionStrategy | 프로모션 민감 |
| 021 | 냉동식품 | FrozenIceStrategy | 계절성 |
| 026 | 반찬 | PerishableStrategy | 요일 가중 |
| 027,028 | 즉석밥/국 | InstantMealStrategy | 비상 수요 패턴 |
| 029,030 | 간식류 | SnackConfectionStrategy | 프로모션 민감 |
| 031,033,035 | 즉석조리 | InstantMealStrategy | 비상 수요 패턴 |
| 032 | 면류 | RamenStrategy | 회전율 기반 |
| 034 | 아이스크림 | FrozenIceStrategy | 계절성 |
| 036,037 | 세면/위생용품 | DailyNecessityStrategy | 안정 수요 |
| 039,043,045,048 | 음료류 | BeverageStrategy | 온도/계절 영향 |
| 046 | 신선식품 | PerishableStrategy | 요일 가중 |
| 049 | 맥주 | BeerStrategy | 요일 패턴 |
| 050 | 소주 | SojuStrategy | 요일 패턴 |
| 052 | 양주 | AlcoholGeneralStrategy | 저회전 |
| 053 | 와인 | AlcoholGeneralStrategy | 저회전 |
| 054~071 | 잡화/비식품 | GeneralMerchandiseStrategy | 안정 수요 |
| 056,057 | 주방/생활용품 | DailyNecessityStrategy | 안정 수요 |
| 072 | 담배 | TobaccoStrategy | 보루/소진 패턴 |
| 073 | 전자담배 | TobaccoStrategy | 담배와 동일 |
| 086 | 세탁용품 | DailyNecessityStrategy | 안정 수요 |
| 100 | 냉동/기타 | FrozenIceStrategy | 계절성 |
| 900 | 소모품 | DefaultStrategy | 기본 예측 |
| 기타 | 미분류 | DefaultStrategy | 기본 예측 |

> **참고**: `food_daily_cap`은 독립 카테고리가 아닌, food 카테고리에 적용되는 총량 상한 로직 (cap=요일평균+20%버퍼, 수량합 기반)

---

## 문서 관리 규칙

### 3계층 구조

```
CLAUDE.md              ← 프로젝트 개요 + 규칙 (AI가 매 세션마다 읽음)
.claude/skills/        ← 기술 가이드 (7개, 코드 작업 시 참조)
docs/
  ├── active/          ← 현재 진행중인 작업 문서만 (5개 이내 유지)
  └── archive/         ← 완료된 문서 (참조용, 평소 안 읽음)
```

### 원칙
- **active/에는 진행중인 작업만** — 완료 시 archive로 이동 또는 삭제
- **변경 이력은 git이 관리** — 문서에 수동으로 이력 기록하지 않음
- **장애/작업은 GitHub Issues** — `gh issue create/close`
- **새 문서 생성 최소화** — 기존 문서 수정 우선, 꼭 필요할 때만 신규 생성

### skills/ 목록 (현행)
| 문서 | 핵심 내용 |
|------|---------|
| nexacro-scraping.md | 넥사크로 JS 패턴, 데이터셋, 그리드 |
| bgf-database.md | DB 스키마, Repository, 마이그레이션 |
| bgf-order-flow.md | 발주 파이프라인, 카테고리별 로직 |
| bgf-screen-map.md | 프레임 ID, 데이터셋, 컬럼 인덱스 |
| bgf-delivery-guide.md | 1차/2차 배송, 도착시간, 폐기 스케줄 |
| food-daily-cap.md | 푸드 총량 상한, 요일별 cap 공식 |
| web-dashboard.md | Flask 대시보드, API 엔드포인트 |

---

## Git 커밋 규칙 (자동 적용)

> **핵심**: 코드를 수정하거나 변경할 때 **반드시 git 커밋 + push**를 수행한다. 사용자가 요청하지 않아도 자동으로 진행한다.

### 커밋 타이밍
- **코드 수정/변경 완료 시** → 즉시 커밋
- **버그 수정 시** → 수정 완료 후 즉시 커밋
- **새 기능 추가 시** → 기능 완성 후 즉시 커밋
- **설정 변경 시** → 변경 후 즉시 커밋
- 단순 탐색/조회/분석만 한 경우 → 커밋 불필요

### 커밋 메시지 형식
```
{type}({scope}): {한줄 요약}

[문제] 무엇이 문제였는지 (버그 수정 시)
[수정] 어떻게 수정했는지
[영향] 영향받는 파일/기능

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

### 타입 (type)
| 타입 | 용도 | 예시 |
|------|------|------|
| `fix` | 버그 수정 | `fix(order): ordYn 오탐 수정` |
| `feat` | 새 기능 | `feat(prediction): 계절계수 7그룹 추가` |
| `refactor` | 구조 변경 (동작 동일) | `refactor(db): Repository 19개 분할` |
| `docs` | 문서 변경 | `docs: CLAUDE.md 커밋 규칙 추가` |
| `chore` | 설정/빌드 변경 | `chore: .gitignore 업데이트` |

### 스코프 (scope)
| 스코프 | 대상 |
|--------|------|
| `order` | 발주 관련 (order_executor, auto_order, direct_api_saver) |
| `prediction` | 예측 관련 (improved_predictor, strategies) |
| `collector` | 수집기 (sales, receiving, waste, order_prep) |
| `db` | 데이터베이스 (schema, repos, migration) |
| `alert` | 알림 (kakao, expiry) |
| `dashboard` | 웹 대시보드 |
| `scheduler` | 스케줄러 (daily_job, run_scheduler) |

### push 규칙
- 커밋 후 `git push origin main` 자동 실행
- push 실패 시 경고 로그 출력 (네트워크 문제 등)

### GitHub Issue 활용
- **운영 장애 발생 시** → Issue 생성 (`gh issue create`)
- **수정 완료 시** → Issue 닫기 (`gh issue close`)
- 커밋 메시지에 `fixes #이슈번호` 포함하면 자동 닫힘

---

## 이슈 체인 규칙 (버그 수정 + 기능 계획 통합 관리)

> **`docs/05-issues/`에 기능 영역별 이슈 체인 문서가 있다. 버그 수정/구조 변경/기능 계획 시 반드시 참조하고 갱신한다.**

### 수정 전
1. **해당 영역의 이슈 체인 문서 읽기** — `docs/05-issues/{영역}.md`
   - 이전에 같은 문제를 시도한 적 있는지 확인
   - 이전 시도가 왜 실패했는지 확인
   - **설계 의도** 확인 — 원래 이 기능이 달성하려던 핵심 원칙
   - 같은 접근을 반복하지 않기 위함
2. 이슈 체인 문서가 없으면 `docs/05-issues/_TEMPLATE.md`를 복사하여 새로 생성

### 역할 구분 (중복 금지)
- **커밋 메시지** = "뭘 바꿨는지" (변경 사항, 영향 범위, 테스트 결과)
- **이슈 체인** = "왜 이렇게 됐는지" (판단 근거, 시도 간 인과관계, 교훈, 검증)
- 이슈 체인에서 커밋 해시로 참조만. diff나 변경 내용을 복사하지 않는다.

### 양방향 링크
- **커밋 → 이슈 체인**: 커밋 메시지 footer에 트레일러 추가
  ```
  Issue-Chain: {영역}#{이슈-kebab-case}
  ```
  예: `Issue-Chain: expiry-tracking#remaining_qty-미갱신`
- **이슈 체인 → 커밋**: 시도 N에 커밋 해시 기록 (기존)
- 시도 번호(N)는 이슈 체인 문서에서만 관리. 커밋에는 번호 안 넣음.
- `git log --grep="Issue-Chain: expiry"` 로 특정 이슈의 모든 커밋 검색 가능

### 수정 후
3. **이슈 체인 문서 갱신** — 아래 형식으로 기록:
   ```
   ### 시도 N: {한줄 요약} ({커밋해시}, {날짜})
   - **왜**: {이 접근을 선택한 판단 근거}
   - **결과**: ✗ {왜 안 됐는지} / ✓
   - **실패 패턴**: #{태그}
   ```
4. 상태 태그 (5종):
   - `[PLANNED]` 계획됨 — 목표와 우선순위만 정의, 미착수. 우선순위: P1(긴급) > P2(중요) > P3(개선)
   - `[OPEN]` 착수함 — 작업 중이지만 미해결
   - `[WATCHING]` 모니터링 — 수정 완료, 검증 대기
   - `[RESOLVED]` 해결 완료 — 검증까지 끝남
   - `[DEFERRED]` 보류 — 의도적으로 미룸 (사유 필수)
   - **상태 변경 시 CLAUDE.md "활성 이슈" 테이블도 갱신** ([PLANNED]/[OPEN]/[WATCHING] 추가, [RESOLVED] 삭제)
5. **교훈 섹션** 필수 — 다음 수정자가 같은 삽질을 피하도록
6. **검증 체크포인트** 필수 — 해결 후 확인 일정을 체크리스트로 기록
   - 스케줄 태스크 연동 시: `(스케줄: {태스크ID})` 형식으로 ID 기록
7. **연관 이슈 링크** — 다른 영역에 영향 있으면 `→ {파일}#{이슈명}` 형식으로 연결

### 의도 점검 (시도 3회 이상 실패 시 필수)
- 같은 이슈에서 시도가 3번 이상 실패하면 **의도 점검** 수행:
  1. 원래 **설계 의도**를 아직 따르고 있는가?
  2. 패치가 아닌 **구조 재설계**가 필요한가?
  3. 현재 접근의 **근본 가정**이 여전히 유효한가?
- 점검 결과를 이슈 체인 문서에 기록 (유지 / 의도 전환)
- 의도 전환 시 이슈 블록 상단의 **설계 의도**를 갱신

### 스케줄 태스크 연동
- 검증 체크포인트에 스케줄 태스크를 연결한 경우:
  - **태스크 실행 후** 해당 이슈 체인의 체크포인트를 `[x]`로 갱신
  - 결과 요약을 체크포인트 옆에 추가 (예: `(완료: 04-07, matched 95%)`)
  - 문제 발견 시 새 이슈 블록 `[OPEN]`으로 추가
- 태스크 prompt에 반드시 포함: `"이슈 체인 docs/05-issues/{영역}.md 검증 체크포인트 갱신"`

### 실패 패턴 태그 (공통, 3회 이상 반복 시에만 새 태그 추가)
`#stale-data` `#data-format` `#no-consumption-tracking` `#timing-gap` `#cross-contamination` `#one-shot-fix` `#patch-on-patch`

### 영역 분류
| 파일 | 대상 |
|------|------|
| `expiry-tracking.md` | 폐기 추적, 유통기한 알림, inventory_batches |
| `prediction.md` | 수요 예측, ML 앙상블, 계수 조정 |
| `order-execution.md` | 발주 실행, Direct API, Selenium 저장 |
| `data-collection.md` | 판매/입고/폐기전표 수집, BGF 사이트 접속 |
| `scheduling.md` | 스케줄러, Phase 순서, 타이밍 충돌 |

새 영역은 이슈 3건 이상 시 분리. 템플릿: `docs/05-issues/_TEMPLATE.md`

---

## 고영향 변경 대상 (수정 전 impact_check.py 실행 필수)

> `python scripts/impact_check.py --all` 실행 후 변경사항 없음을 확인한 뒤 수정

| 파일 | 수정하면 뭐가 바뀌나 |
|------|---------------------|
| `demand_classifier.py` DEMAND_PATTERN_EXEMPT_MIDS | **전 카테고리** 수요 분류 |
| `demand_classifier.py` DEMAND_PATTERN_THRESHOLDS | **전 카테고리** 수요 분류 |
| `demand_classifier.py` sell_days 쿼리 | **전 매장** 판매일 계산 |
| `constants.py` PREDICTION_PARAMS["moving_avg_days"] | **전체** WMA 예측값 |
| `base_predictor.py` _get_sell_ratio 쿼리 | sell_day_ratio 전체 |

---

## 코딩 규칙

### 기본 규칙
- 함수: `snake_case` / 클래스: `PascalCase` / 상수: `UPPER_SNAKE`
- 한글 주석, 함수 docstring 필수
- 예외 처리: `except Exception as e:` + `logger.warning(f"...: {e}")` — 비즈니스 로직에서 silent pass 금지
  - Selenium cleanup 패턴은 예외: `except Exception as e: logger.debug(f"...: {e}")` 허용
  - `bare except:` 금지 — SystemExit/KeyboardInterrupt 전파 보장

### DB 접근 규칙
- 모든 DB 작업은 Repository 클래스 통해서만 수행 (try/finally로 커넥션 보호)
- **새 코드**: `src.infrastructure.database.repos` 사용 권장
- **기존 코드**: `src.db.repository` 호환 유지 (내부적으로 새 repos를 호출)
- 매장별 DB에서는 `store_filter` 불필요 (DB 자체가 매장별 격리)
- 레거시 통합 DB 쿼리 시: `from src.db.store_query import store_filter` → `sf, sp = store_filter("ds", store_id)`

### 설정/상수 규칙
- 비즈니스 상수: `src/settings/constants.py` 또는 `src/config/constants.py` (동일 값)
- 타이밍 상수: `src/settings/timing.py` 또는 `src/config/timing.py` (동일 값)
- UI 식별자: `src/settings/ui_config.py` 또는 `src/config/ui_config.py` (동일 값)
- 매직 넘버 하드코딩 금지

### 모듈별 규칙
- 로깅: `from src.utils.logger import get_logger` 사용 (`print()` 사용 금지, `__main__` 블록 제외)
- 인증 정보: 환경변수 사용 (`BGF_USER_ID`, `BGF_PASSWORD`, `KAKAO_REST_API_KEY`), 하드코딩 금지
- 넥사크로 클릭: `src/utils/nexacro_helpers.py`의 공유 함수 사용 (인라인 JS 중복 금지)
- 스크린샷: `from src.utils.screenshot import save_screenshot` 사용 (`data/screenshots/` 날짜별 저장)
- DB 스키마 변경 시: `constants.py`의 `DB_SCHEMA_VERSION` 증가 + `models.py`의 `SCHEMA_MIGRATIONS`에 추가

### 새 모듈 작성 가이드
- **순수 로직**: `src/domain/` 에 배치 (I/O 없음, Strategy 패턴 또는 순수 함수)
- **DB/외부 I/O**: `src/infrastructure/` 에 배치
- **오케스트레이션**: `src/application/use_cases/` 에 배치
- **새 카테고리**: `src/domain/prediction/strategies/` 에 Strategy 클래스 추가 + `strategy_registry.py` 등록
- **store_id**: 새 모듈에서 StoreContext 또는 store_id 파라미터 포함 여부 리뷰 필수

## 발주 함수 수정 시 필수 체크리스트

> **대상**: `get_recommendations()`, `_get_supplement_candidates()`, `_get_candidates()`, `apply_food_daily_cap()`, `supplement_orders()` 등 발주량에 직접 영향을 주는 모든 함수

### 수정 전
1. 이 함수의 **입력/출력 단위** 확인 (수량 qty vs 품목수 count)
2. 이 함수가 파이프라인 **몇 번째 단계**에서 실행되는지 확인 (Floor→CUT→Cap 순서)
3. 이 함수 **이후에 실행되는 단계**가 현재 수정의 의도를 덮어쓸 수 있는지 확인

### 수정 시
4. `is_available = 0` 상품이 후보에 포함되지 않는지 확인
5. `is_cut_item = 1` 상품이 후보에 포함되지 않는지 확인
6. LEFT JOIN 시 NULL 처리: `COALESCE(ri.is_available, 1)` 패턴 사용
7. site_order_counts와의 단위 일치 확인 (COUNT vs SUM)

### 수정 시 (추가)
11. 반환/비교값이 **"품목수(count)"인지 "수량합(sum qty)"인지** 변수명으로 명시
    → 잘못: `len(selected) >= cap` (cap이 qty 기준인데 count로 비교)
    → 올바름: `total_qty >= cap_qty` (변수명에 단위 포함)
    참고: food-cap-qty-fix — count→sum(qty) 혼동으로 과다발주
12. **sell_qty=0을 수요=0으로 단정하기 전에 stock_qty=0(품절)인지 확인**
    → 품절로 못 판 것 ≠ 수요 없음. `if sell_qty == 0 and stock_qty > 0`일 때만 무수요
    → 위반 시 SLOW 오분류 → 발주 0 → 연속 품절 악순환
    참고: food-stockout-misclassify, milk-demand-classifier-fix
13. **행사 종료 임박 상품**이 정상 발주량으로 포함되어 있지 않은가?
    → promo_end_date - today <= 5일이면 발주량 감소 또는 0
    → 위반 시 행사 종료 후 재고 폐기
    참고: 냉장고 사진 토론(2026-03-30) — "1+1 종료 5일 전 감소" 교훈

### 수정 후
8. 관련 테스트 전체 실행 (`pytest tests/ -k "관련키워드"`)
9. 파이프라인 16단계 중 영향받는 단계 목록 기록
10. CLAUDE.md 변경 이력 테이블 + changelog.md 업데이트

> **변경 이력**: `git log --oneline -- src/order/` 로 확인 (CLAUDE.md 수동 관리 불필요)

---

## 신상품 도입 현황 모듈 [부분 활성]

> **활성**: `NEW_PRODUCT_MODULE_ENABLED = True` — 수집 + 3일발주 후속 관리
> **보류**: `NEW_PRODUCT_AUTO_INTRO_ENABLED = False` — 미도입 자동 발주 (유통기한 매핑 미구현)

### 목적
BGF 상생지원제도의 **신상품 도입 지원금** 극대화. 월별 도입률+달성률 점수 기반 지원금 지급.

### 점수 계산
```
종합점수 = 도입 점수 (0~80, 도입률 기반) + 달성 점수 (0~20, 3일발주 달성률 기반)
```

| 종합점수 | 지원금 |
|---------|--------|
| 95~100 | 160,000원 |
| 88~94 | 150,000원 |
| 84~87 | 120,000원 |
| 72~83 | 110,000원 |
| 56~71 | 80,000원 |
| 40~55 | 40,000원 |
| 0~39 | 0원 |

### 핵심 플로우
```
수집 (Collector) -> DB 저장 -> 분석/우선순위 -> 자동발주 통합
   |                  |             |               |
   |  BGF STBJ460     |  3 tables   |  Domain 로직   |  DailyOrderFlow
   |  팝업 클릭 수집   |             |               |  에서 qty 합산
```

### DB 테이블 (3개, store DB)
- `new_product_status`: 주차별 도입 현황 (도입률, 달성률, 점수)
- `new_product_items`: 미도입/미달성 개별 상품 (item_cd, ord_pss_nm, is_ordered)
- `new_product_monthly`: 월별 합계 (종합 점수, 지원금)

### 핵심 알고리즘
- **우선순위** (`replacement_strategy.py`): 소분류별 라운드로빈 배치 (카테고리 다양성 확보)
- **3일발주** (`convenience_order_scheduler.py`): 대상기간 3등분 → 3회 발주 계획, 과발주 방지
- **교체전략** (`replacement_strategy.py`): 동일 소분류 비인기상품(일평균<0.3) 정지 대상 선정
- **점수 계산** (`score_calculator.py`): 비율→점수 환산, 시뮬레이션, 필요 상품수 계산

### 설정 상수 (`constants.py`)
```python
NEW_PRODUCT_TARGET_TOTAL_SCORE = 95        # 목표 종합점수
NEW_PRODUCT_INTRO_ORDER_QTY = 1            # 신상품 도입 발주 수량
NEW_PRODUCT_DS_MIN_ORDERS = 3              # 3일발주 달성 기준 (3회)
NEW_PRODUCT_DS_OVERSTOCK_RATIO = 1.5       # 과발주 방지 비율
```

### BGF 사이트 특이사항
- **넥사크로 팝업**: JS `fireEvent()` 불가 → Selenium ActionChains 실제 클릭 필수
  - 좌표 획득: `grid._getBodyCellElem(row, col)._element_node.getBoundingClientRect()`
- **ORD_PSS_NM**: "발주가능"이 아닌 **"가능"** 으로 반환 → `IN ('발주가능', '가능')` 비교 필수
- **DOIP_ITEM_CNT**: BGF의 조정대상 상품수 (`ITEM_AD_CNT`, 90% 적용) ≠ `ITEM_CNT` 합산
- **팝업 프레임**: `SS_STBJ460_M0.STBJ460_P0.form.dsDetail`
- **그리드 ID**: `gdList` (도입률), `gdList2` (3일발주) — `div2.form` 하위

---

## 변경 이력

> **Git으로 관리**: `git log --oneline` 으로 전체 이력 확인
>
> CLAUDE.md에 수동으로 변경 이력을 기록하지 않는다. 모든 변경은 git 커밋 메시지에 기록된다.
>
> ```bash
> git log --oneline -20                    # 최근 20건
> git log --oneline -- src/order/          # 발주 관련만
> git log --oneline -- src/prediction/     # 예측 관련만
> git show <커밋해시>                       # 특정 커밋 상세
> gh issue list                            # 장애/작업 목록
> ```
