# BGF 리테일 자동 발주 시스템 - 프로젝트 현황 보고서

> **작성일**: 2026-03-01
> **DB 스키마 버전**: v49
> **테스트**: 2,838개 전체 통과
> **프로젝트 위치**: `bgf_auto/`

---

## 1. 프로젝트 개요

BGF 리테일(CU 편의점) **다매장 자동 발주 시스템**. 매일 07:00에 판매/재고 데이터를 수집하고, 카테고리별 맞춤 알고리즘으로 수요를 예측한 뒤, BGF 넥사크로 시스템에 자동 발주를 실행한다.

### 핵심 플로우
```
[매일 07:00 — 매장별 병렬 실행]

Phase 1: 데이터 수집 + 분석 (17개 서브페이즈)
  1.00  판매 데이터 수집 (DailyCollectionJob)
  1.04  시간대별 매출 수집 (STMB010 Direct API)
  1.1   입고 확정 데이터 수집 (센터매입조회)
  1.15  폐기 전표 수집 + 검증 (통합 전표 조회)
  1.16  폐기→매출 동기화 (realtime_inventory 차감)
  1.2   제외 품목 수집 (자동/스마트/수동 발주)
  1.3   신상품 도입 현황 수집
  1.35  신제품 라이프사이클 모니터링 (14일)
  1.5   사전 발주 평가 보정 (EvalCalibrator)
  1.55  폐기 원인 분석 (WasteCauseAnalyzer)
  1.56  폐기율 자동 보정 (FoodWasteRateCalibrator)
  1.57  베이지안 파라미터 최적화 (주간)
  1.6   예측 실적 업데이트
  1.61  수요 패턴 분류 (DemandClassifier)
  1.65  유령 재고 정리
  1.7   예측 로깅 (PredictionLogger)
  1.95  발주현황→OT 동기화

Phase 2: 자동 발주 실행
  Direct API → Batch Grid → Selenium (3단계 폴백)

Phase 3: 실패 사유 수집
```

---

## 2. 코드베이스 규모

### 전체 통계

| 항목 | 수치 |
|------|------|
| **전체 Python 파일** | 542개 |
| **전체 코드 라인** | 181,810 LOC |
| **소스 코드 (src/)** | 283개 파일 / 94,887 LOC |
| **테스트 코드 (tests/)** | 115개 파일 / 44,447 LOC |
| **스크립트 (scripts/)** | 138개 파일 |
| **문서 (docs/)** | 268개 파일 |
| **코드:테스트 비율** | 2.13:1 |

### 모듈별 파일 수

| 모듈 | 파일 수 | 역할 |
|------|---------|------|
| `src/prediction/` | 63 | 예측 엔진 (카테고리별 Strategy, Feature, ML) |
| `src/infrastructure/` | 43 | DB, BGF 사이트, 수집기, 외부 API |
| `src/domain/` | 31 | 순수 비즈니스 로직 (I/O 없음) |
| `src/collectors/` | 21 | 데이터 수집기 20개 |
| `src/application/` | 21 | Use Case, Service, Scheduler |
| `src/web/` | 19 | Flask 웹 대시보드 |
| `src/analysis/` | 13 | 데이터 분석 |
| `src/presentation/` | 11 | CLI 인터페이스 |
| 기타 | 61 | alert, config, utils, order, report, ml 등 |

---

## 3. 아키텍처

### 계층 구조

```
┌───────────────────────────────────────┐
│         Presentation 계층              │
│   CLI (argparse) + Flask Dashboard    │
│   13개 Blueprint, 40+ API 엔드포인트    │
└────────────────┬──────────────────────┘
                 │
┌────────────────▼──────────────────────┐
│         Application 계층               │
│   8개 Use Case + 9개 Service           │
│   MultiStoreRunner (병렬 실행)          │
└────────────────┬──────────────────────┘
                 │
    ┌────────────┼────────────┐
    │            │            │
┌───▼──────┐ ┌──▼────────┐ ┌─▼──────────┐
│  Domain  │ │ Infra-    │ │  Settings  │
│ 15 전략   │ │ structure │ │ 상수/설정   │
│ 순수 로직  │ │ 33 Repo   │ │ StoreCtx   │
└──────────┘ │ 20 수집기  │ └────────────┘
             │ DBRouter   │
             └────────────┘
```

### 핵심 설계 패턴

| 패턴 | 적용 위치 | 설명 |
|------|----------|------|
| **Strategy** | 15개 카테고리 예측 | mid_cd별 맞춤 예측 알고리즘 라우팅 |
| **Repository** | 33개 DB 접근 클래스 | BaseRepository 상속, `db_type` 자동 라우팅 |
| **DBRouter** | DB 연결 | common.db vs stores/{id}.db 자동 분기 |
| **Orchestrator** | DailyOrderFlow | 수집→예측→필터→실행→추적 단일 플로우 |
| **StoreContext** | frozen dataclass | 멀티스레드 안전한 매장 컨텍스트 |
| **3단계 폴백** | 발주 실행 | Direct API → Batch Grid → Selenium |

---

## 4. 데이터베이스

### DB 분할 구조

| DB | 위치 | 크기 | 내용 |
|----|------|------|------|
| **common.db** | `data/common.db` | ~4 MB | 상품 마스터, 중분류, 외부 요인, 앱 설정, 매장 목록 |
| **매장별 DB** | `data/stores/{id}.db` | ~55 MB/매장 | 일별 판매, 발주 추적, 재고, 예측 로그, 프로모션 |
| **order_analysis.db** | `data/order_analysis.db` | ~7 MB | 재고 불일치 진단, 분석 데이터 |
| **bgf_sales.db** | `data/bgf_sales.db` | ~72 MB | 레거시 통합 DB (백업) |

### 스키마 버전 이력 (주요)

| 버전 | 추가 내용 |
|------|----------|
| v49 | `substitution_events` (소분류 잠식 감지) |
| v48 | `food_waste_calibration` small_cd 세분화 |
| v46 | 신제품 lifecycle + tracking |
| v45 | `detected_new_products` (입고 시 자동 감지) |
| v44 | `manual_order_items` (수동 발주 수집) |
| v43 | `order_exclusions` (제외 품목 관리) |
| v42 | `demand_pattern` (수요 패턴 4단계 분류) |
| v37 | `stopped_items` (발주 정지 관리) |
| v33 | `waste_slips`, `waste_verification_log` |
| v32 | `food_waste_calibration` |
| v28 | 신상품 도입 현황 3개 테이블 |
| v26 | 15개 테이블 store_id 마이그레이션 |

### 테이블 분류 (45+ 테이블)

**Common DB (8개)**:
`products`, `mid_categories`, `product_details`, `external_factors`, `app_settings`, `schema_version`, `stores`, `store_eval_params`

**Store DB (20+ 테이블)**:
`daily_sales`, `order_tracking`, `order_history`, `inventory_batches`, `realtime_inventory`, `prediction_logs`, `eval_outcomes`, `promotions`, `receiving_history`, `auto_order_items`, `smart_order_items`, `collection_logs`, `order_fail_reasons`, `calibration_history`, `hourly_sales`, `waste_slips`, `waste_verification_log`, `new_product_status`, `new_product_items`, `new_product_monthly`, `manual_order_items`, `order_exclusions`, `detected_new_products`, `substitution_events` 등

---

## 5. 예측 파이프라인

### 전체 흐름

```
입력 데이터
  ↓
수요 패턴 분류 (DemandClassifier)
  ├─ daily/frequent/exempt → WMA(7일) → Feature 블렌딩
  ├─ intermittent → Croston/TSB → forecast = demand_size × probability
  └─ slow → 예측=0 (ROP에서 1개 보장)
  ↓
계수 적용
  ├─ food/dessert: 곱셈 (base × holiday × weather × weekday × season × assoc × trend)
  └─ 비면제: 덧셈 AdditiveAdjuster (delta_sum → clamp → base × (1+δ))
  ↓
안전재고 계산
  ├─ slow/intermittent: Croston safety_stock
  └─ 나머지: 카테고리별 Strategy (15개)
  ↓
후처리 파이프라인
  발주규칙 → ROP → 프로모션 보정 → ML 앙상블
  → DiffFeedback → 폐기원인 피드백 → Substitution 보정
  → CategoryDemandForecaster(mid_cd) → LargeCategoryForecaster(large_cd)
  → 카테고리 max cap
```

### 15개 카테고리 Strategy

| Strategy | 대상 mid_cd | 특징 |
|----------|-----------|------|
| FoodStrategy | 001~005, 012 | 유통기한 1~3일, 동적 폐기계수, 기온 교차 |
| BeerStrategy | 049 | 요일 패턴, 주말 가중 |
| SojuStrategy | 050 | 요일 패턴, 보루 단위 |
| TobaccoStrategy | 072, 073 | 보루/소진 패턴, 전자담배 포함 |
| RamenStrategy | 006, 032 | 발주가능요일 기반 안전재고 |
| BeverageStrategy | 010, 039, 043, 045, 048 | 온도/계절 영향 |
| PerishableStrategy | 013, 026, 046 | 요일 가중 |
| FrozenIceStrategy | 021, 034, 100 | 계절성 |
| InstantMealStrategy | 027, 028, 031, 033, 035 | 비상 수요 패턴 |
| DessertStrategy | (빵 파생) | FoodStrategy 변형 |
| SnackConfectionStrategy | 014~020, 029, 030 | 발주가능요일, 프로모션 민감 |
| AlcoholGeneralStrategy | 052, 053 | 저회전 |
| DailyNecessityStrategy | 036, 037, 056, 057, 086 | 안정 수요 |
| GeneralMerchandiseStrategy | 054~071 | 안정 수요 |
| DefaultStrategy | 기타 | 기본 예측 |

### ML 모델

| 항목 | 상세 |
|------|------|
| **알고리즘** | Random Forest + Gradient Boosting 앙상블 |
| **피처 수** | 35개 (기본 26 + 입고 패턴 5 + 그룹 컨텍스트 4) |
| **모델 구조** | 이중 모델 (개별 + 그룹), data_confidence 블렌딩 |
| **학습 주기** | 매일 23:45 증분(30일) + 일요일 03:00 전체(90일) |
| **성능 보호** | MAE 20% 악화 또는 Accuracy@1 5%p 하락 시 거부 + 롤백 |
| **블렌딩** | MAE 기반 적응형 가중치 (0.1~0.5) |
| **Quantile alpha** | food 0.45 / tobacco 0.55 / alcohol 0.55 / perishable 0.48 / general 0.50 |

---

## 6. 웹 대시보드

### API Blueprint 구성 (13개)

| Blueprint | 경로 | 주요 엔드포인트 |
|-----------|------|---------------|
| `auth_bp` | `/api/auth` | 로그인, 로그아웃, 세션, 사용자 CRUD |
| `home_bp` | `/api/home` | 파이프라인 상태, 최근 이벤트, 실패 사유 |
| `order_bp` | `/api/order` | 발주 이력, 상세, 추적 |
| `prediction_bp` | `/api/prediction` | 예측 로그, 정확도 메트릭 |
| `report_bp` | `/api/report` | 주간/폐기/매출 리포트 |
| `waste_bp` | `/api/waste` | 폐기 원인, 보정, 워터폴, 피드백 |
| `inventory_bp` | `/api/inventory` | 재고 TTL, 배치, 신선도 |
| `receiving_bp` | `/api/receiving` | 입고 이력, 지연 분석 |
| `logs_bp` | `/api/logs` | 운영 로그, 세션, 에러 |
| `health_bp` | `/api/health` | 시스템 헬스 체크 (인증 불필요) |
| `new_product_bp` | `/api/new-product` | 신상품 도입 현황, 라이프사이클 |
| `category_bp` | `/api/categories` | 카테고리 드릴다운 (tree/summary/products) |
| `pages_bp` | `/` | HTML 페이지 렌더링 |

### 보안

| 항목 | 구현 |
|------|------|
| 인증 | 세션 기반 (Flask session), werkzeug.security 해싱 |
| 역할 | admin (전체 매장, 모든 기능) / viewer (자기 매장, 조회만) |
| 브루트포스 | IP당 5회/5분 제한 |
| Rate Limit | 60 req/min |
| 보안 헤더 | CSP, X-Frame-Options, XSS Protection, Referrer-Policy |
| 세션 | 8시간, HTTPONLY, SameSite=Lax |

### 배포

| 환경 | URL | 용도 |
|------|-----|------|
| PythonAnywhere | `kanura.pythonanywhere.com` | 대시보드 전용 (24/7) |
| Cloudflare Tunnel | 동적 URL | 로컬 PC 기동 시 외부 접근 |
| DB 동기화 | 자동 | 발주 완료 후 PythonAnywhere로 자동 업로드 |

---

## 7. 스케줄러

### 정기 작업 (11개)

| 시간 | 작업 | 빈도 | 비고 |
|------|------|------|------|
| 00:00 | 발주단위 자동 수집 | 매일 | STBJ070_M0 전체 조회 |
| 06:30 | 토큰 갱신 + 카카오 인증 | 매일 | |
| **07:00** | **데이터 수집 + 자동 발주** | **매일** | **메인 플로우, 병렬** |
| 08:00 | 수동 발주 감지 | 매일 | |
| 08:00 (월) | 주간 리포트 | 주간 | |
| 11:00 | 상품 상세 대량 수집 | 매일 | |
| 21:00 | 수동 발주 감지 (2차) | 매일 | |
| 23:00 | 폐기 리포트 | 매일 | |
| 23:30 | 배치 만료 처리 | 매일 | |
| **23:45** | **ML 증분 학습 (30일)** | **매일** | |
| **03:00 (일)** | **ML 전체 재학습 (90일)** | **주간** | 성능 게이트 적용 |

### CLI 옵션

```bash
python run_scheduler.py                  # 전체 스케줄 시작
python run_scheduler.py --now            # 즉시 실행
python run_scheduler.py --weekly-report  # 주간 리포트
python run_scheduler.py --multi-store    # 다매장 병렬
python run_scheduler.py --order-date 2026-03-01  # 특정 배송일만
python run_scheduler.py --collect-order-unit     # 발주단위 수집
python run_scheduler.py --sync-cloud             # 클라우드 동기화
```

---

## 8. 데이터 수집기 (20개)

| 수집기 | 데이터 소스 | 방식 |
|--------|-----------|------|
| SalesCollector | 판매현황 | Selenium |
| ReceivingCollector | 센터매입조회 | Selenium |
| PromotionCollector | 행사 현황 | Selenium |
| FailReasonCollector | 발주 실패 사유 | Selenium |
| WasteSlipCollector | 폐기 전표 | Selenium |
| NewProductCollector | 신상품 도입 | Selenium |
| OrderStatusCollector | 발주 현황 | Selenium |
| ManualOrderDetector | 수동 발주 감지 | Selenium |
| WeatherCollector | 기온 예보 | BGF TopFrame |
| CalendarCollector | 공휴일 | holidays 라이브러리 |
| HourlySalesCollector | 시간대별 매출 | **Direct API** |
| DirectApiFetcher | 발주 준비 조회 | **Direct API** |
| DirectPopupFetcher | 상품 상세 팝업 | **Direct API** |
| DirectFrameFetcher | 프레임 데이터 | **Direct API** |
| DirectSalesFetcher | 판매 데이터 | **Direct API** |
| ProductInfoCollector | 상품 마스터 | Selenium |
| ProductDetailBatchCollector | 상품 상세 대량 | Direct API |
| OrderPrepCollector | 발주 준비 | Selenium |
| + 2개 보조 수집기 | | |

> Direct API: 넥사크로 XHR 인터셉터 기반, Selenium 대비 30배 빠름 (3초/개 → 0.1초/개)

---

## 9. 테스트 현황

### 통계

| 항목 | 수치 |
|------|------|
| **전체 테스트** | 2,838개 |
| **테스트 파일** | 115개 |
| **통과율** | 100% |
| **파일당 평균** | ~24.7개 |
| **테스트 코드** | 44,447 LOC |

### 주요 테스트 영역

| 영역 | 테스트 수 (대략) |
|------|----------------|
| 카테고리 Strategy (15종) | ~300 |
| DB Repository | ~200 |
| 예측 파이프라인 | ~250 |
| 발주 실행 | ~150 |
| 웹 API | ~200 |
| ML 모듈 | ~100 |
| 데이터 수집기 | ~150 |
| 보안/인증 | ~50 |
| 분석/리포트 | ~100 |
| 통합 테스트 | ~100 |
| 기타 (utils, config, 버그 수정) | ~1,238 |

---

## 10. 완료된 PDCA 사이클 (40+)

### 최근 주요 완료 기능 (2026-02-18 ~ 2026-03-01)

| 기능 | Match Rate | 테스트 | 핵심 내용 |
|------|-----------|--------|----------|
| **food-waste-unify** | 97% | 21개 | 4중 폐기 감량 → 단일 통합 계수 |
| **prediction-redesign** | - | 70개 | 수요 패턴 4단계 + Croston/TSB + 덧셈계수 |
| **direct-api-prefetch** | - | 43개 | Phase 2 prefetch 30배 속도 향상 |
| **direct-api-order** | - | 27개 | Direct API 발주 저장 (라이브 검증 완료) |
| **hourly-sales-api** | 97% | 28개 | 시간대별 매출 API + 배송차수 동적 비율 |
| **ml-improvement** | 100% | 33개 | 적응형 블렌딩 + 피처 정리 41→31 |
| **food-ml-dual-model** | 97% | 27개 | 이중 모델 + 콜드스타트 해결 |
| **orderable-day-all** | - | 34개 | 전품목 발주가능요일 + BGF 실시간 검증 |
| **health-check-alert** | 100% | 20개 | 커스텀 예외 7종 + 헬스체크 + 알림 |
| **new-product-lifecycle** | 97% | 20개 | 신제품 14일 모니터링 + 라이프사이클 |
| **ml-daily-training** | 99% | 11개 | 매일 증분학습 + 성능 보호 게이트 |
| **substitution-detect** | 100% | 24개 | 소분류 잠식 감지 + 예측 보정 |
| **waste-rate-smallcd** | 100% | 28개 | 폐기율 보정 small_cd 세분화 |
| **ml-feature-largecd** | 100% | 31개 | ML large_cd 슈퍼그룹 5개 피처 |
| **category-drilldown** | 100% | 12개 | 카테고리 드릴다운 API |
| **order-promo-fix** | 100% | 32개 | 행사 수집 버그 + 오염 30,822건 정리 |
| **manual-order-food-deduction** | 100% | 19개 | 수동 발주 수집 + 푸드 차감 |
| **date-filter-order** | 100% | 11개 | 특정 배송일 발주 CLI |
| **cost-optimizer-activation** | 99% | 16개 | CostOptimizer 활성화 |
| **보안 강화** | 95% | 20개 | OWASP Top 10 준수 |

---

## 11. 기술 스택

| 기술 | 버전 | 용도 |
|------|------|------|
| Python | 3.12 | 메인 언어 |
| Selenium | 4.33.0 | 넥사크로 웹 스크래핑 |
| SQLite | 내장 | 매장별 DB |
| Flask | 3.1.1 | 웹 대시보드 |
| scikit-learn | 1.7.1 | ML 모델 (RF+GB) |
| pandas | 2.2.2 | 데이터 처리 |
| NumPy | 2.0.1 | 수치 연산 |
| Schedule | 1.2.2 | 작업 스케줄링 |
| Requests | 2.32.3 | HTTP 클라이언트 |
| Waitress | 3.0.2 | WSGI 서버 (Windows) |
| holidays | 0.77 | 공휴일 캘린더 |
| Chart.js | 4.x | 대시보드 차트 |

---

## 12. 운영 환경

### 매장 구성

| 매장 ID | 매장명 | DB 파일 |
|---------|--------|---------|
| 46513 | CU 동양대점 | `data/stores/46513.db` (~55 MB) |
| 46704 | CU 호반점 | `data/stores/46704.db` |

### 외부 연동

| 시스템 | 연동 방식 | 용도 |
|--------|----------|------|
| BGF 리테일 사이트 | Selenium + Direct API | 데이터 수집 + 발주 실행 |
| 카카오톡 | REST API | 폐기 위험/일일 리포트/행사 알림 |
| PythonAnywhere | Files API | DB 자동 동기화 |
| Cloudflare | Quick Tunnel | 로컬 대시보드 외부 접근 |

### 알림 체계

| 알림 | 채널 | 트리거 |
|------|------|--------|
| 폐기 위험 | 카카오톡 | 유통기한 임박 상품 감지 |
| 일일 리포트 | 카카오톡 | 발주 완료 후 요약 |
| 행사 변경 | 카카오톡 | 프로모션 변동 감지 |
| ERROR 알림 | 파일 + 카카오톡 | AlertingHandler (5분 쿨다운) |

---

## 13. 문서 체계

### PDCA 문서 구조

```
docs/
├── 01-plan/features/          # 기능 기획서 (19개)
├── 02-design/features/        # 기술 설계서 (12개)
├── 03-analysis/               # 갭 분석 + 검증 결과
├── 04-report/                 # 완료 보고서 + changelog
│   ├── changelog.md           # 전체 변경 이력
│   ├── features/              # 기능별 PDCA 완료 보고서
│   └── PDCA_COMPLETION_CHECKLIST.md
└── archive/2026-02~03/        # 완료된 PDCA 사이클 아카이브 (40+)
```

### 기술 가이드 (`.claude/skills/`)

| 문서 | 내용 |
|------|------|
| `nexacro-scraping.md` | 넥사크로 execute_script 패턴, 데이터셋 조회 |
| `bgf-database.md` | 테이블 정의, Repository, 마이그레이션 |
| `bgf-order-flow.md` | 발주 공식, 카테고리별 로직 |
| `bgf-screen-map.md` | 프레임 ID, 데이터셋, 그리드 컬럼 |
| `food-daily-cap.md` | 푸드 총량 상한 공식 |
| `web-dashboard.md` | Flask 대시보드 구조 |

---

## 14. 비활성/레거시 항목

| 항목 | 상태 | 비고 |
|------|------|------|
| `bgf_sales.db` | 레거시 | 매장별 DB 분리 완료, 백업용 유지 |
| `NEW_PRODUCT_AUTO_INTRO_ENABLED` | `False` | 유통기한 매핑 미구현 |
| `DEBUG_SCREENSHOT_ENABLED` | `False` | 개발 전용 |
| `BaseRepository db_type="legacy"` | 과도기 | 제거 검토 중 |
| `src/prediction/config.py` | Deprecated | `src/settings/constants.py`로 이관 |

---

## 15. 향후 과제

### 즉시 개선 가능

| 항목 | 현황 |
|------|------|
| 대시보드 예측 정확도 시각화 | AccuracyTracker 구현 완료, UI 미반영 |
| 신상품 자동 도입 완성 | 수집+모니터링 완료, 자동 발주 판단 미구현 |

### 중기 확장

| 항목 | 설명 |
|------|------|
| Bayesian 파라미터 최적화 | eval_params 매장별 자동 탐색 |
| ML Feature 고도화 | 날씨 교차항, 점포 특성, STL 시계열 분해 |
| 규칙 엔진 DB화 | constants.py 하드코딩 → rules 테이블 + 웹 CRUD |

---

## 16. 프로젝트 성숙도 요약

| 영역 | 성숙도 | 근거 |
|------|--------|------|
| **아키텍처** | ★★★★★ | 5계층 분리, Strategy/Repository/Orchestrator 패턴 |
| **예측 정확도** | ★★★★☆ | 15개 카테고리 전략 + ML 앙상블 + 자동 보정 |
| **자동화** | ★★★★★ | 17개 서브페이즈 완전 자동, 11개 스케줄 작업 |
| **테스트** | ★★★★★ | 2,838개 100% 통과, 코드:테스트 2.13:1 |
| **보안** | ★★★★☆ | OWASP 준수, 세션/역할/Rate Limit |
| **모니터링** | ★★★★☆ | 헬스체크, 알림, 로그 분석 CLI |
| **문서화** | ★★★★★ | PDCA 40+ 사이클, 기술 가이드 6개, changelog |
| **성능** | ★★★★☆ | Direct API 30배 속도 향상, DB 분할, 병렬 실행 |
| **확장성** | ★★★★☆ | 다매장 병렬, 카테고리 Strategy 플러그인 |
| **운영 안정성** | ★★★★☆ | 3단계 폴백, ML 롤백, 에러 격리 |

> **종합 평가**: 프로덕션 레벨의 완성도를 갖춘 자동 발주 시스템. 181,810 LOC 규모에 2,838개 테스트, 40개 이상의 PDCA 사이클을 거쳐 안정화됨. Direct API 전환으로 실행 속도를 대폭 개선했으며, ML 이중 모델과 적응형 블렌딩으로 예측 정확도를 지속 향상 중.
