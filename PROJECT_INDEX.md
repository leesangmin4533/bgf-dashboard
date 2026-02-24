# Project Index: BGF 리테일 자동 발주 시스템

Generated: 2026-02-04

## 📁 Project Structure

```
bgf_auto/
├── config.py                    # 전역 설정 (URL, 브라우저)
├── run_scheduler.py             # 메인 스케줄러 진입점
├── pytest.ini                   # 테스트 설정
│
├── config/                      # 런타임 설정
│   ├── kakao_token.json         # 카카오 API 토큰
│   └── eval_params.json         # 평가 파라미터
│
├── data/
│   ├── bgf_sales.db             # SQLite 메인 DB (Schema v11)
│   ├── logs/                    # 발주 평가 로그
│   ├── screenshots/             # 디버그 스크린샷
│   └── reports/                 # 리포트 출력
│
├── scripts/                     # CLI 실행 스크립트 (12개)
├── tests/                       # 테스트 (20개 파일)
│
└── src/                         # 소스 코드 (90+ 파일)
    ├── config/                  # 중앙 설정 (timing, constants, ui_config)
    ├── db/                      # DB 레이어 (models, repository)
    ├── collectors/              # 데이터 수집기 (7개)
    ├── prediction/              # 예측 엔진 (30+ 파일)
    │   ├── categories/          # 카테고리별 로직 (15개)
    │   ├── features/            # Feature Engineering
    │   ├── promotion/           # 행사 기반 조정
    │   └── accuracy/            # 정확도 추적
    ├── order/                   # 발주 실행 (3개)
    ├── alert/                   # 폐기/행사 알림 (4개)
    ├── analysis/                # 분석/리포트 (4개)
    ├── report/                  # HTML 리포트 (5개)
    ├── notification/            # 카카오톡 알림
    ├── web/                     # 웹 대시보드 (Flask)
    ├── core/                    # 상태 관리 (StateGuard)
    ├── scheduler/               # 일일 스케줄러
    └── utils/                   # 공유 유틸 (logger, screenshot, nexacro)
```

## 🚀 Entry Points

| 진입점 | 경로 | 설명 |
|--------|------|------|
| **스케줄러** | `run_scheduler.py` | 매일 07:00 자동 실행 (--now, --weekly-report, --expiry) |
| **전체 플로우** | `scripts/run_full_flow.py` | 수집+예측+발주 통합 실행 |
| **자동 발주** | `scripts/run_auto_order.py` | 발주만 실행 (--preview) |
| **드라이 오더** | `scripts/dry_order.py` | 발주 시뮬레이션 |
| **폐기 알림** | `scripts/run_expiry_alert.py` | 폐기 위험 알림 (--send) |
| **리포트** | `scripts/run_report.py` | HTML 리포트 생성 |
| **웹 대시보드** | `src/web/app.py` | Flask 웹 서버 |

## 📦 Core Modules

### 데이터 수집 (collectors/)
| 모듈 | 역할 |
|------|------|
| `sales_collector.py` | BGF 판매 데이터 수집 → DB 저장 |
| `order_prep_collector.py` | 미입고/유통기한/행사 수집 |
| `promotion_collector.py` | 행사 정보 (1+1, 2+1) 수집 |
| `receiving_collector.py` | 입고 데이터 수집 |
| `product_info_collector.py` | 상품 상세 정보 |
| `calendar_collector.py` | 공휴일/이벤트 캘린더 |
| `weather_collector.py` | 날씨 데이터 |
| `order_status_collector.py` | 발주 상태 수집 |

### 예측 엔진 (prediction/)
| 모듈 | 역할 |
|------|------|
| `improved_predictor.py` | **메인 예측기** - 일평균→요일계수→안전재고→재고차감 |
| `pre_order_evaluator.py` | 사전 발주 평가 (분포 적응형 임계값) |
| `eval_config.py` | 평가 파라미터 중앙 관리 (JSON) |
| `eval_calibrator.py` | 사후 검증 + 자동 보정 (피드백 루프) |
| `eval_reporter.py` | 일일 보정 리포트 |
| `cost_optimizer.py` | 비용 최적화 |
| `prediction_config.py` | 예측 설정 |

### 카테고리별 예측 로직 (prediction/categories/)
| 모듈 | 카테고리 | 특수 로직 |
|------|---------|----------|
| `food.py` | 도시락/주먹밥/김밥/샌드위치/햄버거/빵 | 유통기한 1-3일, 총량상한 |
| `food_daily_cap.py` | 푸드류 총량 관리 | 요일별 cap = 요일평균+3 |
| `beer.py` | 맥주 (049) | 요일 패턴 기반 |
| `soju.py` | 소주 (050) | 요일 패턴 기반 |
| `tobacco.py` | 담배/전자담배 (072,073) | 보루/소진 패턴 |
| `ramen.py` | 조리면/면류 (006,032) | 회전율 기반 |
| `beverage.py` | 음료 | 계절/온도 반영 |
| `snack_confection.py` | 과자/제과 | - |
| `frozen_ice.py` | 냉동/아이스 | 계절성 |
| `perishable.py` | 신선식품 | 유통기한 민감 |
| `daily_necessity.py` | 생활용품 | 안정적 수요 |
| `general_merchandise.py` | 잡화 | - |
| `alcohol_general.py` | 주류(일반) | - |
| `instant_meal.py` | 즉석식품 | - |
| `default.py` | 기본 | 폴백 로직 |

### 발주 실행 (order/)
| 모듈 | 역할 |
|------|------|
| `auto_order.py` | 자동 발주 시스템 (메인 오케스트레이터) |
| `order_executor.py` | BGF 발주 화면 조작 (넥사크로) |
| `order_unit.py` | 발주 단위 변환 (낱개↔박스) |

### 웹 대시보드 (web/)
| 모듈 | 역할 |
|------|------|
| `app.py` | Flask 앱 팩토리 |
| `routes/pages.py` | 페이지 라우트 |
| `routes/api_home.py` | 홈 API |
| `routes/api_order.py` | 발주 API |
| `routes/api_report.py` | 리포트 API |
| `templates/index.html` | SPA 메인 템플릿 |
| `static/js/` | 프론트엔드 (home, order, report, flow, arch) |

### 알림 시스템 (alert/ + notification/)
| 모듈 | 역할 |
|------|------|
| `expiry_checker.py` | 폐기 위험 감지 (FIFO 배치) |
| `promotion_alert.py` | 행사 변경 알림 |
| `delivery_utils.py` | 배송 유틸 |
| `kakao_notifier.py` | 카카오톡 알림 발송 |

### DB 레이어 (db/)
| 모듈 | 역할 |
|------|------|
| `models.py` | 스키마 정의 (v11) - 판매, 재고, 발주, 행사, 폐기, 보정 |
| `repository.py` | CRUD (48+ 메서드, try/finally 커넥션 보호) |

### 설정 (config/)
| 모듈 | 역할 |
|------|------|
| `timing.py` | 타이밍/재시도 상수 (149개) |
| `constants.py` | 비즈니스 상수 (50+개) |
| `ui_config.py` | BGF 프레임ID, 데이터셋 경로, 메뉴 텍스트 |

## 🔧 Configuration

| 파일 | 용도 |
|------|------|
| `config.py` | BGF URL, 브라우저 옵션, 넥사크로 설정 |
| `.env` | 인증 정보 (BGF_USER_ID, BGF_PASSWORD, KAKAO_*) |
| `config/eval_params.json` | 예측 평가 파라미터 |
| `config/kakao_token.json` | 카카오 API 토큰 |
| `pytest.ini` | 테스트 설정 (markers: unit, db) |

## 🧪 Test Coverage

- **테스트 파일**: 20개 (tests/)
- **카테고리별**: beer, soju, tobacco, ramen, food, beverage, snack, perishable, frozen_ice, daily_necessity, general_merchandise, alcohol_general, instant_meal, default
- **인프라**: db_models, repository, utils, eval_config
- **markers**: `@pytest.mark.unit` (순수 로직), `@pytest.mark.db` (in-memory SQLite)

## 🔗 Key Dependencies

| 패키지 | 용도 |
|--------|------|
| `selenium` | 넥사크로 기반 웹 스크래핑 |
| `sqlite3` | 데이터 저장 (내장) |
| `schedule` | 작업 스케줄링 |
| `flask` | 웹 대시보드 |
| `requests` | 카카오 API, 날씨 API |
| `openpyxl` | Excel 리포트 |

## 🔑 Key Algorithms

1. **발주량 = (일평균판매 × 요일계수 + 안전재고) - 현재재고 - 미입고수량**
2. **안전재고** = 카테고리별 상이 (식품: 최소화, 담배: 보루 단위, 주류: 요일 패턴)
3. **사전 평가** = 품절/노출/인기도 기반 필터링 (분포 적응형 임계값)
4. **사후 보정** = 실제 판매 vs 예측 비교 → 파라미터 자동 조정 (피드백 루프)
5. **푸드 총량 상한** = cap = 요일평균+3, 탐색/활용 구분

## 📝 Quick Start

```bash
cd bgf_auto

# 1. 환경 설정
cp .env.example .env   # BGF_USER_ID, BGF_PASSWORD 입력

# 2. 스케줄러 실행 (07:00 자동)
python run_scheduler.py

# 3. 즉시 테스트
python run_scheduler.py --now

# 4. 드라이런 (실제 발주 없이)
python scripts/run_full_flow.py --no-collect --max-items 3

# 5. 테스트 실행
pytest tests/ -v
```

## 📊 Statistics

| 항목 | 수량 |
|------|------|
| Python 소스 파일 | 90+ |
| 테스트 파일 | 20 |
| 실행 스크립트 | 12 |
| 카테고리 모듈 | 15 |
| DB 테이블 | 10+ (Schema v11) |
| 기술 문서 | 5 (.claude/skills/) |
| PDCA 문서 | 15+ (docs/) |
