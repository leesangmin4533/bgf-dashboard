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

## 상세 문서

| 문서 | 내용 |
|------|------|
| [넥사크로 스크래핑 가이드](.claude/skills/nexacro-scraping.md) | execute_script 패턴, 데이터셋 조회, 그리드 클릭, 입력 처리 |
| [DB 스키마 및 Repository](.claude/skills/bgf-database.md) | 테이블 정의, Repository 클래스, 마이그레이션 |
| [발주 플로우 및 알고리즘](.claude/skills/bgf-order-flow.md) | 모듈 관계, 발주 공식, 카테고리별 로직, 폐기 알림 |
| [BGF 화면 매핑](.claude/skills/bgf-screen-map.md) | 프레임 ID, 데이터셋, 그리드 컬럼 인덱스 |
| [푸드류 총량 상한](.claude/skills/food-daily-cap.md) | 요일별 cap 공식, 탐색/활용 선별, 설계 원칙, 변경 금지 항목 |
| [웹 대시보드](.claude/skills/web-dashboard.md) | Flask 대시보드 구조, API 엔드포인트, 라우트 설정 |

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
    │   │   └── repos/                # ★ 19개 Repository 파일로 분할
    │   │       ├── sales_repo.py             # SalesRepository (stores DB)
    │   │       ├── product_detail_repo.py    # ProductDetailRepository (common DB)
    │   │       ├── order_repo.py             # OrderRepository (stores DB)
    │   │       ├── prediction_repo.py        # PredictionRepository (stores DB)
    │   │       ├── order_tracking_repo.py    # OrderTrackingRepository (stores DB)
    │   │       ├── receiving_repo.py         # ReceivingRepository (stores DB)
    │   │       ├── inventory_repo.py         # RealtimeInventoryRepo (stores DB)
    │   │       ├── inventory_batch_repo.py   # InventoryBatchRepository (stores DB)
    │   │       ├── promotion_repo.py         # PromotionRepository (stores DB)
    │   │       ├── eval_outcome_repo.py      # EvalOutcomeRepository (stores DB)
    │   │       ├── calibration_repo.py       # CalibrationRepository (stores DB)
    │   │       ├── auto_order_item_repo.py   # AutoOrderItemRepository (stores DB)
    │   │       ├── smart_order_item_repo.py  # SmartOrderItemRepository (stores DB)
    │   │       ├── app_settings_repo.py      # AppSettingsRepository (common DB)
    │   │       ├── fail_reason_repo.py       # FailReasonRepository (stores DB)
    │   │       ├── validation_repo.py        # ValidationRepository (common DB)
    │   │       ├── external_factor_repo.py   # ExternalFactorRepository (common DB)
    │   │       ├── store_repo.py             # StoreRepository (common DB)
    │   │       └── collection_log_repo.py    # CollectionLogRepository (stores DB)
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
python run_scheduler.py                     # 전체 스케줄 시작 (07:00 자동)
python run_scheduler.py --now               # 즉시 실행 (수집 + 발주)
python run_scheduler.py --weekly-report     # 주간 리포트
python run_scheduler.py --expiry 14         # 폐기 알림 테스트
python run_scheduler.py --multi-store       # 다매장 병렬 실행
```

### 개별 실행

```bash
python scripts/run_full_flow.py --no-collect --max-items 3       # 테스트 모드
python scripts/run_full_flow.py --run --no-collect --max-items 3  # 실제 발주
python scripts/run_auto_order.py --preview                        # 예측만
python scripts/run_expiry_alert.py --send                         # 폐기 알림만
```

### CLI (새 진입점)

```bash
python -m src.presentation.cli.main order --now --dry-run        # 드라이런 발주
python -m src.presentation.cli.main predict --store 46513        # 예측만
python -m src.presentation.cli.main report --weekly              # 주간 리포트
python -m src.presentation.cli.main alert --expiry --days 14     # 폐기 알림
python -m src.presentation.cli.main store --list                 # 매장 목록
```

### 프로그래밍

```python
# 새 API (Application 계층)
from src.application.services.prediction_service import PredictionService
from src.application.use_cases.daily_order_flow import DailyOrderFlow

# 예측
service = PredictionService(store_id="46513")
predictions = service.predict_all(min_order_qty=1)

# 전체 플로우
flow = DailyOrderFlow(store_id="46513", driver=driver)
result = flow.run(dry_run=True, max_items=10)

# 기존 API (호환 유지)
from src.prediction.improved_predictor import ImprovedPredictor
from src.order.auto_order import AutoOrderSystem
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

> **참고**: `food_daily_cap`은 독립 카테고리가 아닌, food 카테고리에 적용되는 총량 상한 로직 (cap=요일평균+3)

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

### 버그 수정 문서화 규칙

코드에서 **버그를 발견하고 수정**했을 때, 아래를 반드시 수행:

1. **`docs/04-report/changelog.md`** 에 항목 추가 (최상단에 최신순):
   ```markdown
   ## [YYYY-MM-DD] - {제목}

   ### Fixed
   - **{파일}**: {1줄 요약}
     - 원인: {root cause}
     - 수정: {fix 내용}
     - 영향: {영향 범위}
   ```

2. **심각도 기준** (어떤 항목을 기록할 것인가):
   - **필수 기록**: 운영 장애, 데이터 오류, 로직 버그 (예: store_id 누락, CUT 필터 순서 오류, UnboundLocalError)
   - **선택 기록**: 로그 미출력, UI 표시 오류, 성능 개선 등 사소한 변경
   - **기록 안 함**: 단순 리팩터링, 주석 변경, import 정리

3. **기능 추가/개선** 시에도 changelog에 `### Added` 또는 `### Changed` 항목 추가

4. 수정 완료 후 테스트 통과 확인 → changelog 기록 → CLAUDE.md `변경 이력` 테이블 업데이트 (한 줄 요약)

> **핵심**: changelog.md는 "무엇이 왜 바뀌었는지"를 기록하는 곳. 나중에 동일 문제 재발 시 참조할 수 있어야 한다.

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

## 변경 이력 (최근)

| 날짜 | 주요 변경 |
|------|---------|
| 2026-02-17 | **신상품 3일발주 후속 관리 활성화** — MODULE_ENABLED/AUTO_INTRO_ENABLED 스위치 분리, `_process_3day_follow_orders()` 별도 메서드(mids 후속 발주), `_check_item_sold_after_receiving()` 입고→판매 확인, 수집 활성화 |
| 2026-02-15 | **신상품 도입 현황 모듈 완성** — 10단계 구현, 수집기(ActionChains 팝업), DB v28(3테이블), 도메인 로직(점수/우선순위/3일발주/교체), 자동발주 통합, 웹 API, 1128 테스트, CLAUDE.md 문서화 |
| 2026-02-14 | **문서-코드 정합성 정리** — dead code 제거(clear_old 3건, __main__ 560줄), prediction_config deprecated 모순 해소, 스킬문서 3건 업데이트(DB v27, 웹라우트, Feature블렌딩/계절계수/ML25), CLAUDE.md 플로우 보강 |
| 2026-02-14 | **기간대비 예측 개선** — Feature 블렌딩(EWM+동요일), 트렌드조정(+-8~15%), 계절계수(7그룹), ML Feature 25개(+lag_7,lag_28,wow) |
| 2026-02-14 | **CUT 필터 순서 버그 수정** — prefetch 이후 [CUT 재필터] 추가, 폐기추적 store_id 누락 2건 수정 |
| 2026-02-13 | **프로젝트 전체 재구성 (9 Phase)** — 계층형 아키텍처 도입 (Settings/Domain/Infrastructure/Application/Presentation), 매장별 DB 분리 (common.db + stores/*.db), DBRouter 자동 라우팅, Repository 19개 파일 분할, CategoryStrategy 패턴 (15개), DailyOrderFlow 오케스트레이터, MultiStoreRunner 병렬 실행, CLI 진입점, 기존 코드 호환 shim 유지, 1038 테스트 통과 |
| 2026-02-10 | 9개 테이블 store_id 마이그레이션 v26 — 15개 테이블 전체 store_id 완료 |
| 2026-02-10 | 입고→폐기 추적 버그 6건 일괄 수정, DEFAULT_STORE_ID 상수화 |
| 2026-02-09 | 발주 전량 소멸 방지 5건 수정, Phase 2 넥사크로 호환 수정 |
| 2026-02-09 | CostOptimizer 2D 매트릭스 통합 (마진x회전율, 판매비중 보너스) |
| 2026-02-08 | 멀티 매장 전체 병렬화, store_id 누락 전체 수정 (~60 SQL), 격리 테스트 |
| 2026-02-08 | 5단계 종합 완성 계획 전체 완료 (코드 품질, 대시보드, ML, 테스트, 에러 핸들링) |
| 2026-02-08 | 예측 모델 종합 검토 기반 개선 8항목 (CostOptimizer, 동적 폐기계수, ML Feature 22개 등) |
| 2026-02-04 | 코드 품질 개선 — except pass 31건 변환, CLAUDE.md/skills 전면 보강 |
| 2026-01-31 | 카카오 토큰 자동 재인증, 폐기 추적 시스템, 매직넘버 전면 제거 |
| 2026-01-30 | 자동 보정 시스템 (eval_calibrator), 스크린샷 통합 관리, 사전 발주 평가 |
| 2026-01-29 | 푸드류 총량 상한, 실시간 재고 동기화, 행사 시스템, 카테고리별 모듈 분리 |
