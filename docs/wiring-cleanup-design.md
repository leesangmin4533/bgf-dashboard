# BGF 리테일 프로젝트 배선 정리 설계서

> 작성일: 2026-03-28 | 대상: bgf_auto/ (121,036줄, 344파일)
> 원칙: "v2 없음, 현재 구조 안에서 점진적 배선 정리"

---

## 1. 현재 구조 진단 요약

### 1.1 아키텍처 설계 vs 현실 괴리

| 항목 | CLAUDE.md 설계 | 실측 |
|------|---------------|------|
| 계층 수 | 5개 (Settings/Domain/Infrastructure/Application/Presentation) | 18개 폴더 (src/ 직하 루트 포함) |
| Domain 계층 | "순수 비즈니스 로직 (I/O 없음)" | 1,202줄, 얇은 re-export 셸 + 23개 역의존 |
| Infrastructure | "모든 I/O" | collectors/(최상위)와 infrastructure/collectors/ 이중 존재 |
| 실질 비즈니스 | Domain에 있어야 | prediction/(34,800줄), order/(9,645줄)에 집중 |

### 1.2 핵심 문제 4가지

**문제 1: "Hollow Domain"** -- domain/ 계층이 빈 껍데기
- domain/ 전체 1,202줄 (전체의 1%)
- domain/prediction/strategies/의 15개 파일은 전부 src/prediction/categories/의 re-export 래퍼 (파일당 평균 40줄)
- domain/evaluation/__init__.py는 src/prediction/의 3개 클래스를 re-export만 수행
- domain/prediction/features/__init__.py도 src/prediction/features/를 re-export만 수행
- 실질 비즈니스 로직: prediction/ (34,800줄) + order/ (9,645줄) = 44,445줄이 domain 밖에 존재

**문제 2: 폴더 중복/분산**
- `src/collectors/` (18,547줄, 22파일) vs `src/infrastructure/collectors/` (re-export __init__.py만)
- `src/web/` (6,503줄) vs `src/presentation/web/` (re-export만)
- `src/scheduler/` (1,815줄) vs `src/application/scheduler/`
- `src/config/` vs `src/settings/` -- 둘 다 설정
- `src/db/` (2,382줄, legacy) vs `src/infrastructure/database/` (16,539줄, 현행)

**문제 3: God Class 23개**

| 순위 | 파일 | 줄 | 역할 | 분해 난이도 |
|------|------|-----|------|------------|
| 1 | prediction/improved_predictor.py | 3,891 | 예측 Facade (이미 4개 추출) | 중 (추가분해) |
| 2 | order/order_executor.py | 2,810 | 발주 실행 (Selenium+API) | 상 (외부I/O 밀착) |
| 3 | order/auto_order.py | 2,781 | 발주 오케스트레이션 (이미 4개 추출) | 중 |
| 4 | collectors/order_status_collector.py | 2,400 | 발주현황 수집 | 중 |
| 5 | collectors/product_info_collector.py | 1,982 | 상품정보 수집 | 중 |
| 6 | collectors/order_prep_collector.py | 1,957 | 발주준비 수집 | 중 |
| 7 | db/models.py | 1,866 | 레거시 ORM (폐기 대상) | 하 (제거) |
| 8 | scheduler/daily_job.py | 1,815 | 일일 작업 오케스트레이션 | 상 (Phase 의존) |
| 9 | order/direct_api_saver.py | 1,766 | Direct API 발주 저장 | 중 |
| 10 | sales_analyzer.py | 1,660 | BGF 로그인/메뉴 (루트 방치) | 하 (이동) |
| 11 | prediction/pre_order_evaluator.py | 1,592 | 사전평가 | 중 |
| 12 | prediction/prediction_config.py | 1,515 | 설정 상수 모음 | 하 (분할) |
| 13 | infrastructure/database/schema.py | 1,514 | DB 스키마 | 하 (자동생성) |
| 14 | collectors/receiving_collector.py | 1,433 | 입고 수집 | 중 |
| 15 | analysis/trend_report.py | 1,409 | 트렌드 리포트 | 하 |
| 16 | infrastructure/database/repos/inventory_batch_repo.py | 1,392 | 배치 재고 저장소 | 중 |
| 17 | prediction/categories/food.py | 1,270 | 푸드 카테고리 | 중 |
| 18 | collectors/new_product_collector.py | 1,163 | 신제품 수집 | 중 |
| 19 | prediction/food_waste_calibrator.py | 1,133 | 폐기율 보정 | 하 |
| 20 | infrastructure/database/repos/inventory_repo.py | 1,071 | 재고 저장소 | 하 |
| 21 | prediction/coefficient_adjuster.py | 1,025 | 계수 조정기 | 하 |
| 22 | collectors/direct_frame_fetcher.py | 1,016 | 프레임 페칭 | 하 |
| 23 | alert/expiry_checker.py | 1,012 | 유통기한 체커 | 하 |

**문제 4: src/ 루트 방치 파일**
- `sales_analyzer.py` (1,660줄) -- BGF 로그인/메뉴 이동, infrastructure/bgf_site에 속해야
- `bgf_connector.py` (229줄) -- BGF 연결기
- `nexacro_helper.py` (179줄) -- 넥사크로 헬퍼 (utils/nexacro_helpers.py와 중복?)

### 1.3 의존성 위반 상세 (23개)

| 유형 | 위반 소스 (domain/) | 대상 (domain 외부) | 성격 |
|------|--------------------|--------------------|------|
| re-export | strategies/food.py 외 14개 | prediction/categories/*.py | 순수 래핑 |
| re-export | evaluation/__init__.py | prediction/pre_order_evaluator 등 3개 | 순수 래핑 |
| re-export | prediction/features/__init__.py | prediction/features/ 3개 | 순수 래핑 |
| re-export | prediction/prediction_params.py | prediction/prediction_config.py 2개 | 순수 래핑 |
| logger | order/order_adjuster.py 등 4개 | utils/logger | 유틸리티 |

핵심 발견: **23개 위반 중 20개는 re-export용 __init__.py 또는 thin wrapper**이다. domain/이 "계층"이 아니라 "import 리디렉션 디렉토리"로 기능하고 있다.

---

## 2. CLAUDE.md 수정 방향

### 2.1 계층 설명 현실화

**현재 CLAUDE.md의 거짓말:**
- "Domain: 순수 비즈니스 로직 (I/O 없음)" -- 실제로는 re-export 셸
- "Infrastructure: 모든 I/O" -- 실제로는 collectors/가 밖에 있음
- 아키텍처 다이어그램이 5계층인데 실제는 18폴더

**수정 방향:**

```
현재(실제) 구조를 있는 그대로 기술:
- prediction/ : 예측 엔진 (비즈니스 로직 핵심, 34,800줄)
- order/      : 발주 엔진 (9,645줄)
- collectors/ : BGF 사이트 데이터 수집 (18,547줄)
- infrastructure/database/ : DB 계층 (16,539줄)
- web/        : Flask 대시보드 (6,503줄)
- scheduler/  : 일일 작업 오케스트레이션 (1,815줄)
- analysis/   : 분석/리포트 (8,465줄)
- application/: 유스케이스/서비스 (8,004줄)
- domain/     : 인터페이스 정의 + re-export (1,202줄) ← 향후 정리 대상
```

### 2.2 CLAUDE.md 구조 변경

| 섹션 | 현재 | 변경 |
|------|------|------|
| 아키텍처 다이어그램 | 이상적 5계층 | 실제 구조 반영 + "목표 구조" 별도 표기 |
| 디렉토리 구조 | re-export 관계 암묵적 | re-export 여부 명시 (예: `domain/prediction/strategies/ → re-export from prediction/categories/`) |
| 코딩 규칙 | 없음 | God Class 임계값(800줄), 신규 파일 배치 규칙 추가 |
| 의존성 규칙 | 암묵적 | import 방향 규칙 명시 |

### 2.3 추가할 코딩 규칙

```markdown
## 코딩 규칙 (신규)

### 파일 크기
- 경고 임계값: 800줄
- 하드 리미트: 1,500줄 (넘기면 분해 필수)
- 예외: schema.py, prediction_config.py (설정 데이터 파일)

### 신규 파일 배치
- DB I/O → infrastructure/database/repos/
- BGF 사이트 접근 → collectors/
- 순수 계산 로직 → prediction/ 또는 해당 카테고리
- 발주 로직 → order/
- 웹 API → web/routes/
- 오케스트레이션 → application/use_cases/

### Import 방향
- prediction/ → infrastructure/database/ (OK)
- prediction/ → domain/ (OK, 인터페이스)
- domain/ → prediction/ (금지, 현재 위반 23개 → 정리 예정)
- collectors/ → infrastructure/database/ (OK)
- web/ → application/ → prediction/ + order/ (OK)
```

---

## 3. 계층 구조 정리 계획 (현재 18개 폴더 → 목표 구조)

### 3.1 전략: "re-export 셸 제거 + 실체 있는 폴더만 유지"

현재 18개 폴더 중:
- **실체 있는 폴더 (로직 보유)**: prediction, order, collectors, infrastructure, web, scheduler, analysis, application, alert, ml, report, utils, notification, settings, validation, core
- **re-export 셸 (제거 대상)**: domain, presentation, config(src/)

### 3.2 목표 구조 (Phase별)

#### Phase A: re-export 셸 정리 (domain/ 해체)

**핵심 결정: domain/ 폴더를 제거한다.**

근거:
- 1,202줄 중 실질 로직은 models.py(91줄) + base_strategy.py(~60줄) + order_adjuster.py(319줄) + order_filter.py(386줄) = ~856줄
- 나머지 346줄은 순수 re-export __init__.py + thin wrapper
- 15개 strategy wrapper는 각 40줄 = 600줄이지만, prediction/categories/에 이미 실체가 있음

**이동 계획:**

| 현재 위치 | 이동 대상 | 이유 |
|-----------|----------|------|
| domain/models.py | src/models.py (유지) 또는 prediction/models.py | 값 객체는 어디서든 import 가능 |
| domain/prediction/base_strategy.py | prediction/base_strategy.py | 인터페이스는 구현체 옆에 |
| domain/prediction/strategy_registry.py | prediction/strategy_registry.py | 레지스트리는 전략과 함께 |
| domain/prediction/strategies/ (15개) | **삭제** | re-export만 하므로, 직접 prediction/categories/ 사용 |
| domain/prediction/features/ | **삭제** | re-export만 |
| domain/prediction/prediction_params.py | **삭제** | re-export만 |
| domain/evaluation/ | **삭제** | re-export만 |
| domain/order/order_adjuster.py | order/order_adjuster.py (이미 존재 여부 확인 필요) |
| domain/order/order_filter.py | order/order_filter.py |
| domain/new_product/ | prediction/new_product/ 또는 analysis/new_product/ |

**Import 변경 영향 추정:**
- `from src.domain.prediction.strategies.food import FoodStrategy` → `from src.prediction.categories.food import FoodStrategy` 등
- `from src.domain.models import PredictionResult` → `from src.models import PredictionResult`
- 예상 변경 파일: ~30-50개 (grep으로 정확히 산출)
- 호환 shim: domain/__init__.py에 re-export 유지 (6개월 후 제거)

#### Phase B: 최상위 폴더 통합

| 현재 | 목표 | 변경 |
|------|------|------|
| src/config/ | src/settings/ 흡수 | settings/에 constants.py, timing.py 이미 존재 |
| src/notification/ | src/alert/ 흡수 | kakao_notifier.py를 alert/로 이동 |
| src/core/ | src/utils/ 흡수 | exceptions.py 등 유틸 |
| src/presentation/ | **삭제** | web/에 re-export만, CLI는 src/cli/로 |
| src/data/ | **삭제** (비어 있으면) | 확인 필요 |

**목표 폴더 구조 (Phase B 완료 후):**

```
src/
├── settings/          # 설정 (constants, timing, ui_config, store_context)
├── models.py          # 공유 값 객체 (PredictionResult, OrderItem 등)
│
├── prediction/        # 예측 엔진 (비즈니스 핵심)
│   ├── categories/    # 카테고리별 전략 (15개)
│   ├── features/      # 피처 엔지니어링
│   ├── ml/            # ML 모델
│   ├── accuracy/      # 정확도 추적
│   ├── promotion/     # 행사 처리
│   ├── association/   # 연관 분석
│   ├── rules/         # 발주 규칙
│   └── utils/         # 예측 유틸
│
├── order/             # 발주 엔진
├── collectors/        # BGF 사이트 데이터 수집
│
├── infrastructure/    # DB + 외부 연동
│   ├── database/      # DB 라우팅, 스키마, Repository 19개
│   ├── bgf_site/      # BGF 넥사크로 기반 인터페이스
│   └── external/      # 외부 API (카카오 등)
│
├── application/       # 오케스트레이션
│   ├── use_cases/     # 유스케이스 7개
│   ├── services/      # 서비스 5개
│   └── scheduler/     # 스케줄러
│
├── scheduler/         # daily_job.py (application/scheduler와 통합 검토)
├── analysis/          # 분석/리포트
├── alert/             # 알림 (expiry_checker + kakao_notifier)
├── web/               # Flask 대시보드
├── ml/                # ML 유틸 (prediction/ml과 통합 검토)
├── report/            # 리포트 생성
├── validation/        # 검증 규칙
├── utils/             # 공유 유틸리티
└── db/                # 레거시 ORM (제거 예정)
```

**결과: 18개 → 14개 (domain, presentation, config, notification 제거)**

더 공격적으로 줄이려면 Phase C에서:
- scheduler/ → application/scheduler 통합 (→ 13개)
- ml/ → prediction/ml 통합 (→ 12개)
- report/ → analysis/reports 통합 (→ 11개)

하지만 "고통이 리팩토링을 이끌어야 한다" 원칙에 따라, Phase B까지만 계획하고 Phase C는 고통이 발생하면 진행.

---

## 4. 의존성 방향 수정 계획 (23개 역의존)

### 4.1 위반 분류 및 해법

| # | 위반 유형 | 개수 | 해법 | 작업량 |
|---|----------|------|------|--------|
| A | domain/strategies/ → prediction/categories/ | 15 | domain/ 해체 시 자동 해소 (Phase A) | 0 (삭제) |
| B | domain/evaluation/ → prediction/ | 3 | domain/ 해체 시 자동 해소 | 0 (삭제) |
| C | domain/features/ → prediction/features/ | 3 | domain/ 해체 시 자동 해소 | 0 (삭제) |
| D | domain/prediction_params → prediction_config | 2 | domain/ 해체 시 자동 해소 | 0 (삭제) |
| **합계** | | **23** | **domain/ 해체로 전부 해소** | |

**핵심 통찰**: 23개 역의존은 모두 domain/이 prediction/을 re-export하면서 발생한 것이다. domain/을 해체하면 의존성 위반 자체가 존재하지 않게 된다.

### 4.2 해체 후 새로운 의존성 규칙

```
[허용 방향]
web/ → application/ → prediction/ + order/
application/ → infrastructure/database/
prediction/ → infrastructure/database/
order/ → infrastructure/database/ + prediction/
collectors/ → infrastructure/database/
alert/ → infrastructure/database/ + prediction/

[금지 방향]
infrastructure/database/ → prediction/ (DB는 비즈니스 모름)
prediction/ → order/ (예측은 발주 모름, 단방향)
prediction/ → collectors/ (예측은 수집 모름)
infrastructure/ → web/ (인프라는 UI 모름)
```

### 4.3 domain/ 해체 마이그레이션 스크립트

```python
# scripts/migrate_domain_imports.py (1회성)
REPLACEMENTS = {
    "from src.domain.prediction.strategies.": "from src.prediction.categories.",
    "from src.domain.prediction.base_strategy": "from src.prediction.base_strategy",
    "from src.domain.prediction.strategy_registry": "from src.prediction.strategy_registry",
    "from src.domain.prediction.features.": "from src.prediction.features.",
    "from src.domain.prediction.prediction_params": "from src.prediction.prediction_config",
    "from src.domain.evaluation": "from src.prediction",  # evaluator 직접 import
    "from src.domain.models": "from src.models",
    "from src.domain.order.order_adjuster": "from src.order.order_adjuster",
    "from src.domain.order.order_filter": "from src.order.order_filter",
}
```

예상 변경 파일 수: ~40개
호환 shim: domain/__init__.py에 DeprecationWarning + re-export 유지 (3개월)

---

## 5. God Class 분해 계획 (우선순위 + 방법)

### 5.1 우선순위 매트릭스

| 순위 | 대상 | 줄수 | 고통도 | 분해 난이도 | 이유 |
|------|------|------|--------|------------|------|
| **1** | prediction_config.py | 1,515 | 높음 | **하** | 순수 데이터, 분할만 하면 됨 |
| **2** | daily_job.py | 1,815 | 높음 | 중 | Phase 추가 시마다 수정, 단일 책임 위반 |
| **3** | improved_predictor.py | 3,891 | 중 | 중 | 이미 4개 추출했지만 아직 3,891줄 |
| **4** | db/models.py | 1,866 | 중 | **하** | 레거시, schema.py로 대체 완료, 삭제 가능 |
| ~~5~~ | order_executor.py | 2,810 | 낮음 | 상 | 외부 I/O 밀착, 분해 위험 > 이익 |
| ~~6~~ | auto_order.py | 2,781 | 낮음 | 상 | 이미 4개 추출, Facade 안정 |

### 5.2 분해 계획 상세

#### 작업 1: prediction_config.py 분할 (1,515줄 → 4파일)

현재: 모든 예측 관련 상수/설정이 한 파일에 혼재
```
prediction_config.py (1,515줄)
├── PREDICTION_PARAMS (카테고리별 파라미터)
├── ORDER_ADJUSTMENT_RULES (발주 조정 규칙)
├── WEATHER_COEFFICIENTS (기상 계수)
├── SEASON_COEFFICIENTS (계절 계수)
├── CATEGORY_CONFIGS (카테고리 설정)
├── ML_CONFIG (ML 설정)
└── 기타 (FOOD_*, DELIVERY_*, HOLIDAY_* 등)
```

목표:
```
prediction/config/
├── __init__.py              # re-export (호환)
├── category_params.py       # PREDICTION_PARAMS, CATEGORY_CONFIGS (~400줄)
├── order_rules.py           # ORDER_ADJUSTMENT_RULES (~200줄)
├── coefficients.py          # WEATHER/SEASON/HOLIDAY/WEEKDAY 계수 (~500줄)
└── ml_config.py             # ML_CONFIG, FEATURE_NAMES (~200줄)
```

기존 import 호환: prediction_config.py를 re-export shim으로 유지
예상 변경: prediction_config.py 1파일만 수정, 외부 0파일 변경

#### 작업 2: daily_job.py 분해 (1,815줄 → Phase별 모듈)

현재: Phase 1.0~3.0까지 모든 단계가 한 파일에 선형 나열
```
daily_job.py (1,815줄)
├── Phase 1.0: 데이터 수집
├── Phase 1.1~1.6: 분석/보정
├── Phase 2.0: 예측
├── Phase 2.5: 발주 준비
└── Phase 3.0: 발주 실행
```

목표:
```
scheduler/
├── daily_job.py             # 오케스트레이터 (~300줄, Phase 호출만)
├── phases/
│   ├── collection_phase.py  # Phase 1.0~1.2 (~400줄)
│   ├── analysis_phase.py    # Phase 1.3~1.6 (~400줄)
│   ├── prediction_phase.py  # Phase 2.0~2.5 (~350줄)
│   └── order_phase.py       # Phase 3.0 (~350줄)
```

#### 작업 3: improved_predictor.py 추가 분해 (3,891줄 → 목표 2,500줄)

이미 BasePredictor, CoefficientAdjuster, InventoryResolver, PredictionCacheManager를 추출했다.
추가 추출 후보:
- `_apply_food_pipeline()` 계열 (~400줄) → prediction/categories/food_pipeline.py
- `_apply_post_processing()` (~300줄) → prediction/post_processor.py
- DiffFeedback 적용 로직 (~200줄) → 이미 diff_feedback.py에 있으므로 위임 강화

예상 결과: 3,891 → ~2,800줄 (28% 감소)

#### 작업 4: db/models.py 제거 (1,866줄 → 0줄)

infrastructure/database/schema.py가 이미 대체. models.py를 import하는 파일 목록을 추출하고, 하나씩 schema.py로 전환. 최종 삭제.

### 5.3 분해하지 않는 God Class (의도적 보류)

| 파일 | 줄 | 보류 이유 |
|------|-----|----------|
| order_executor.py | 2,810 | Selenium/API I/O 밀착, 분해 시 트랜잭션 일관성 깨짐 위험. 현재 안정. |
| auto_order.py | 2,781 | 이미 4개 추출 완료, Facade로서 적절한 크기. |
| collectors/* (6개) | 1,000~2,400 | 각각 단일 BGF 화면 매핑, 분해 시 넥사크로 컨텍스트 분산 |
| schema.py | 1,514 | DDL 정의 파일, 분해 의미 없음 |
| inventory_batch_repo.py | 1,392 | Repository는 쿼리 집합, 분해보다 쿼리 최적화가 우선 |

---

## 6. 테스트 구조화 계획

### 6.1 현황

```
tests/
├── 164개 .py 파일 (루트 혼재)
├── e2e/        (빈 __init__.py만)
├── integration/ (빈 __init__.py만)
├── golden/     (스냅샷 테스트 8파일)
└── conftest.py
```

### 6.2 분류 기준

| 분류 | 기준 | 예시 |
|------|------|------|
| **unit/** | DB/네트워크 없이 실행 가능, mock 사용 | test_additive_adjuster, test_croston_predictor |
| **integration/** | DB 사용 (SQLite in-memory 또는 fixture) | test_db_models, test_inventory_batch_repo |
| **e2e/** | Selenium 또는 실제 BGF 사이트 필요 | test_automation_phase2, test_direct_api_* |
| **golden/** | 스냅샷 비교 (이미 존재) | golden_*.json |

### 6.3 실행 계획

**방법: 점진적 이동 (한 번에 다 하지 않음)**

1. conftest.py에 마커 기반 분류 추가:
```python
# conftest.py
def pytest_collection_modifyitems(items):
    for item in items:
        if "selenium" in item.fixturenames or "driver" in item.fixturenames:
            item.add_marker(pytest.mark.e2e)
        elif "db_conn" in item.fixturenames or "test_db" in item.fixturenames:
            item.add_marker(pytest.mark.integration)
```

2. pytest.ini에 마커 등록:
```ini
[pytest]
markers =
    unit: Unit tests (no DB, no network)
    integration: Integration tests (DB required)
    e2e: End-to-end tests (Selenium required)
```

3. 파일 이동은 **고통이 생길 때만**: 현재 루트에 164개가 있어도 `pytest tests/` 한 줄로 전부 실행되므로 즉각적 고통이 없다. 대신 **마커 기반 선택 실행**이 더 실용적:
```bash
pytest -m "not e2e"           # 빠른 로컬 테스트
pytest -m "e2e"               # Selenium 테스트만
pytest -m "integration"       # DB 테스트만
```

4. 신규 테스트는 해당 폴더에 생성 (unit/, integration/, e2e/)
5. 기존 테스트는 관련 PDCA 작업 시 같이 이동

**예상 효과**: 파일 이동 0개로 시작하되, 마커로 분류 실행 가능. 점진적으로 이동.

---

## 7. 설정 통합 계획

### 7.1 현황

설정이 5곳에 분산:
```
1. bgf_auto/config.py              (115줄, URL/브라우저)
2. src/config/constants.py         (306줄, 비즈니스 상수)
3. src/config/timing.py            (105줄, 타이밍)
4. src/settings/ (5파일)           (위 3개의 re-export + store_context)
5. src/prediction/prediction_config.py (1,515줄, 예측 설정)
6. config/*.json (8개 파일)        (런타임 설정)
```

### 7.2 목표

```
src/settings/                      # 정적 설정 (코드 상수)
├── app_config.py                  # URL, 브라우저, 타이밍 (기존 유지)
├── constants.py                   # 비즈니스 상수 (기존 유지)
├── timing.py                      # 타이밍 상수 (기존 유지)
├── ui_config.py                   # 넥사크로 UI ID (기존 유지)
└── store_context.py               # StoreContext (기존 유지)

src/prediction/config/             # 예측 전용 설정 (분할, 작업 1에서)
├── category_params.py
├── order_rules.py
├── coefficients.py
└── ml_config.py

config/                            # 런타임 설정 (JSON, 변경 가능)
├── stores.json                    # 매장 목록
├── eval_params.json               # 평가 파라미터
└── ...                            # 기존 유지
```

### 7.3 변경 사항

- src/config/ 폴더 → src/settings/로 통합 후 src/config/는 re-export shim 유지 (3개월)
- prediction_config.py → prediction/config/ 분할 (작업 1과 동시)
- bgf_auto/config.py → src/settings/app_config.py 흡수 (이미 존재, 확인 필요)

---

## 8. 레거시 제거 계획

### 8.1 db/models.py (1,866줄) 제거

**전제 조건**: infrastructure/database/schema.py (1,514줄)가 완전 대체

**단계:**
1. `grep -r "from src.db.models" src/` → 사용처 목록 추출
2. 각 사용처를 schema.py 또는 해당 Repository로 교체
3. `grep -r "from src.db import" src/` → db/__init__.py 경유 사용처 교체
4. models.py 삭제
5. db/ 폴더에 남는 파일: repository.py (43줄, shim), store_query.py (53줄)

**예상 변경**: ~15-20개 파일

### 8.2 src/ 루트 방치 파일 3개 이동

| 파일 | 줄 | 목표 위치 | 이유 |
|------|-----|----------|------|
| sales_analyzer.py | 1,660 | infrastructure/bgf_site/sales_analyzer.py | BGF 로그인/메뉴 = infrastructure |
| bgf_connector.py | 229 | infrastructure/bgf_site/bgf_connector.py | BGF 연결 |
| nexacro_helper.py | 179 | utils/nexacro_helpers.py 통합 또는 infrastructure/bgf_site/ | 넥사크로 헬퍼 |

**호환 shim**: 원래 위치에 import redirect 파일 유지 (3개월)

### 8.3 src/db/repository.py (43줄, shim) 제거

이미 infrastructure/database/repos/에 실체 Repository가 있으므로, 43줄 shim 사용처를 전환 후 제거.

---

## 9. 매장 확장 대비 (3개 → 10개)

### 9.1 현재 구조의 확장성 평가

| 항목 | 현재 상태 | 10개 매장 시 문제 |
|------|----------|-----------------|
| DB 분할 | stores/{id}.db 패턴 | 문제 없음 (파일 추가만) |
| StoreContext | frozen dataclass | 문제 없음 |
| stores.json | 3개 매장 정의 | 항목 추가만 |
| ThreadPoolExecutor | 병렬 실행 | max_workers 조정 필요 (3→5-7) |
| Selenium 세션 | 매장당 1개 | 메모리 문제 가능 (1세션 ~500MB) |
| ML 모델 | 매장별 학습 | 학습 시간 선형 증가 (현재 ~10분/매장) |
| 로그 | 매장 구분 있음 | 문제 없음 |

### 9.2 필요 변경

**즉시 (0개 매장 추가 전):**
- `config/stores.json` 스키마에 매장 그룹 개념 추가 (운영자 1인이 관리 가능한 매장 묶음)
- Selenium 세션 풀링 (동시 최대 3-4개, 나머지 큐잉)

**5개 매장 도달 시:**
- DB 백업 전략 변경 (개별 → 차등 백업)
- 대시보드 매장 선택 UI 개선 (드롭다운 → 검색형)

**10개 매장 도달 시:**
- 병렬 실행 전략 변경 (ThreadPoolExecutor → 매장 그룹별 순차+그룹 내 병렬)
- ML 전체 학습 시간 관리 (우선순위 학습: 주요 매장 먼저)
- 모니터링 대시보드 필요 (현재 로그 기반 → 중앙 상태 테이블)

### 9.3 코드 변경 불필요 항목 (이미 확장 가능)
- DBRouter: store_id 기반 자동 라우팅 → 매장 수 무관
- BaseRepository: db_type="store" → 자동 매장 DB 선택
- store_filter(): SQL WHERE 절 자동 생성
- ATTACH 패턴: 매장 DB에서 common.db 참조 → 매장 수 무관

---

## 10. 실행 로드맵 (주 단위)

### 전제
- 1인 개발, 주 5일 기준
- 매일 07:00 운영 중단 불가
- 각 작업은 독립 브랜치, 테스트 통과 후 병합

### Week 1: prediction_config.py 분할 + CLAUDE.md 수정

| 일 | 작업 | 예상 시간 |
|----|------|----------|
| 1 | prediction_config.py 구조 분석, 4파일 분할 설계 | 2h |
| 2 | category_params.py + order_rules.py 추출 | 3h |
| 3 | coefficients.py + ml_config.py 추출, re-export shim | 3h |
| 4 | 전체 테스트 실행 + 호환성 검증 | 2h |
| 5 | CLAUDE.md 현실 반영 수정 | 2h |

**의존관계**: 없음 (독립 작업)
**리스크**: 낮음 (re-export shim으로 외부 변경 0)
**검증**: 3,700+ 테스트 전체 통과

### Week 2: domain/ 해체 (Phase A)

| 일 | 작업 | 예상 시간 |
|----|------|----------|
| 1 | domain/ 사용처 전수 조사 (grep), 마이그레이션 스크립트 작성 | 3h |
| 2 | base_strategy.py + strategy_registry.py → prediction/ 이동 | 2h |
| 3 | domain/order/ → order/ 이동, import 수정 | 2h |
| 4 | domain/prediction/ re-export 삭제, 호환 shim 배치 | 3h |
| 5 | 전체 테스트 실행 + 호환성 검증 | 2h |

**의존관계**: Week 1 완료 후 (prediction_config 분할이 먼저)
**리스크**: 중 (import 경로 변경 40+파일)
**검증**: 3,700+ 테스트 + `from src.domain` grep 잔여 0건

### Week 3: daily_job.py 분해 + 레거시 정리 시작

| 일 | 작업 | 예상 시간 |
|----|------|----------|
| 1 | daily_job.py Phase 경계 분석, 4파일 분할 설계 | 2h |
| 2 | collection_phase.py + analysis_phase.py 추출 | 3h |
| 3 | prediction_phase.py + order_phase.py 추출 | 3h |
| 4 | src/ 루트 방치 파일 3개 이동 + 호환 shim | 2h |
| 5 | 전체 테스트 실행 | 2h |

**의존관계**: 없음 (Week 2와 병렬 가능하지만 1인이므로 순차)
**리스크**: 중 (daily_job의 Phase 간 상태 전달 주의)

### Week 4: 폴더 통합 (Phase B) + 테스트 마커

| 일 | 작업 | 예상 시간 |
|----|------|----------|
| 1 | src/config/ → src/settings/ 흡수 | 2h |
| 2 | src/notification/ → src/alert/ 이동 | 1h |
| 3 | src/presentation/ 삭제, src/core/ → src/utils/ | 2h |
| 4 | 테스트 마커 (conftest.py + pytest.ini) 설정 | 2h |
| 5 | CLAUDE.md 최종 업데이트 + 전체 테스트 | 3h |

**의존관계**: Week 2 (domain/ 해체) 완료 후
**리스크**: 낮음 (re-export shim 전략)

### Week 5 (선택): db/models.py 제거 + improved_predictor 추가 분해

| 일 | 작업 | 예상 시간 |
|----|------|----------|
| 1-2 | db/models.py 사용처 전환 (~15파일) | 4h |
| 3 | db/models.py 삭제 + db/ 폴더 정리 | 2h |
| 4-5 | improved_predictor.py food_pipeline + post_processor 추출 | 4h |

**의존관계**: Week 3 이후
**리스크**: 중 (models.py 참조 범위에 따라)

### 의존 관계 다이어그램

```
Week 1 ─── Week 2 ─── Week 4
  │                       │
  └──── Week 3 ──── Week 5 (선택)
```

---

## 11. 성공 기준 (정량적 Exit Criteria)

### Phase A 완료 (Week 2 후)

| 지표 | 현재 | 목표 | 측정 방법 |
|------|------|------|----------|
| domain/ → prediction/ 역의존 | 23개 | 0개 | `grep -r "from src.domain" src/ \| grep -v "shim"` |
| domain/ 실질 코드 줄 | 1,202 | 0 (shim만) | `wc -l src/domain/**/*.py` |
| 테스트 통과 | 3,700+ | 3,700+ (동일) | `pytest tests/` |
| re-export shim 수 | 0 | ~5개 (임시) | domain/ 내 파일 수 |

### Phase B 완료 (Week 4 후)

| 지표 | 현재 | 목표 | 측정 방법 |
|------|------|------|----------|
| src/ 직하 폴더 수 | 18 | 14 | `ls -d src/*/` |
| src/ 루트 .py 파일 | 3 (방치) | 0 | `ls src/*.py` |
| prediction_config.py 줄수 | 1,515 | ~50 (re-export) | `wc -l` |
| daily_job.py 줄수 | 1,815 | ~300 | `wc -l` |
| 테스트 통과 | 3,700+ | 3,700+ | `pytest tests/` |

### 전체 완료 (Week 5 후)

| 지표 | 현재 | 목표 | 측정 방법 |
|------|------|------|----------|
| God Class (1,000줄+) | 23개 | 20개 이하 | 스크립트 |
| db/models.py | 1,866줄 | 삭제됨 | 파일 존재 여부 |
| CLAUDE.md | 현실 괴리 | 실측 반영 | 수동 검토 |
| 테스트 마커 | 미분류 | unit/integration/e2e 구분 | `pytest --co -m unit` |

---

## 12. 리스크와 대비책

### 리스크 1: Import 경로 변경으로 인한 런타임 오류

- **확률**: 중 (특히 domain/ 해체 시)
- **영향**: 07:00 자동 실행 실패
- **대비책**:
  - 호환 shim으로 기존 import 유지 (DeprecationWarning만)
  - 각 작업 완료 후 `python -c "from src.scheduler.daily_job import DailyJob; print('OK')"` 스모크 테스트
  - 배포 전 `--dry-run` 모드로 전체 플로우 검증
  - 실패 시 git revert (브랜치 전략)

### 리스크 2: 테스트에서 발견 못한 런타임 의존

- **확률**: 낮 (3,700+ 테스트 커버)
- **영향**: 특정 카테고리/매장에서만 발생
- **대비책**:
  - 각 Phase 완료 후 3매장 전체 dry-run (`--no-collect --max-items 3`)
  - 1주 모니터링 기간 (shim 유지)

### 리스크 3: prediction_config 분할 시 상수 참조 누락

- **확률**: 낮
- **영향**: NameError 런타임 발생
- **대비책**:
  - prediction_config.py를 re-export shim으로 유지 (기존 import 100% 호환)
  - 신규 코드만 새 경로 사용
  - `python -c "from src.prediction.prediction_config import *"` 검증

### 리스크 4: daily_job.py 분해 시 Phase 간 상태 전달 깨짐

- **확률**: 중
- **영향**: 특정 Phase 스킵/실패
- **대비책**:
  - Phase 간 데이터를 dict/dataclass로 명시적 전달 (현재 지역변수 공유)
  - 각 Phase를 독립 함수로 먼저 추출 (같은 파일 내) → 테스트 통과 확인 → 파일 분리
  - 2단계 분리: 함수 추출 → 파일 분리

### 리스크 5: 1인 개발로 5주 연속 리팩토링 집중 어려움

- **확률**: 높음
- **영향**: 계획 지연
- **대비책**:
  - 각 Week는 독립적, 중간 중단 가능
  - 기능 개발 PDCA와 리팩토링을 교대 (1주 기능 / 1주 배선)
  - "고통이 리팩토링을 이끈다" 원칙 유지 -- Week 1-2만 필수, 나머지는 필요 시

---

## 부록: 작업별 변경 파일 수 추정

| 작업 | 신규 파일 | 수정 파일 | 삭제 파일 | 줄 변화 |
|------|----------|----------|----------|---------|
| prediction_config 분할 | 4 | 1 (shim) | 0 | +50 (shim) |
| domain/ 해체 | 2 (이동) | ~40 (import 수정) | ~20 (re-export) | -1,200 |
| daily_job 분해 | 4 | 1 | 0 | +100 (boilerplate) |
| 루트 파일 이동 | 0 (이동) | ~15 (import) | 0 | 0 |
| 폴더 통합 | 0 | ~10 (import) | ~5 (re-export) | -200 |
| db/models.py 제거 | 0 | ~15 | 1 | -1,866 |
| CLAUDE.md 수정 | 0 | 1 | 0 | ~-100 (현실화) |
| 테스트 마커 | 0 | 2 (conftest, pytest.ini) | 0 | +30 |
| **합계** | 10 | ~85 | ~26 | **~-3,186** |
