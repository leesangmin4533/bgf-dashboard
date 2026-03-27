# BGF Retail Project - Integrated Code Review Report

> 2026-03-01 | 4-Agent Parallel Review | PDCA Check Phase

---

## Overall Score

| Review Area | Agent | Score | Grade |
|-------------|-------|:-----:|:-----:|
| Code Quality | code-analyzer | 62/100 | D+ |
| Security | security-architect | 68/100 | C |
| Architecture | gap-detector | 79/100 | B- |
| QA / Testing | qa-strategist | 72/100 | B- |
| **Weighted Average** | | **70/100** | **C+** |

---

## Critical Findings (Must Fix)

### 1. God Class: ImprovedPredictor (3,470 lines, 65+ methods)
- **Area**: Code Quality
- **File**: `src/prediction/improved_predictor.py`
- **Impact**: 유지보수 불가, 변경 시 side-effect 위험
- **Recommendation**: 5-7개 클래스로 분리 (SafetyStockCalculator, MLEnsembler, WeatherAdjuster, HolidayAdjuster, OrderRuleEngine, BatchCache, PredictionFacade)

### 2. God Class: AutoOrderSystem (2,609 lines)
- **Area**: Code Quality
- **File**: `src/order/auto_order.py`
- **Impact**: 예측 호출, 제외 필터, 신제품 주입, 발주 실행이 혼재
- **Recommendation**: OrderFilterService, OrderExecutionService 등으로 분리

### 3. .env 내 실제 자격증명 노출
- **Area**: Security (OWASP A02)
- **File**: `.env`
- **Impact**: BGF 비밀번호, Kakao API 키, 개인 이메일/비밀번호 평문 저장
- **Recommendation**: 자격증명 즉시 교체, .env.example 분리, 비밀번호 DB 컬럼 제거

### 4. CSRF 보호 없음
- **Area**: Security (OWASP A04)
- **File**: `src/web/app.py`
- **Impact**: 인증된 관리자의 브라우저에서 악의적 요청 가능
- **Recommendation**: Flask-WTF 또는 double-submit cookie 패턴 추가

### 5. Presentation Layer가 Application Layer 우회 (12/13 파일)
- **Area**: Architecture
- **File**: `src/web/routes/api_*.py`
- **Impact**: 계층 격리 위반, Repository 직접 접근
- **Recommendation**: Application Service 생성 후 Route → Service → Repo 구조로 전환

---

## High Priority Findings

### Code Quality
| # | Issue | Location | Effort |
|---|-------|----------|--------|
| 1 | `_compute_safety_and_order()` 470줄 15-branch if-elif | improved_predictor.py | High |
| 2 | 30개 prediction 파일이 직접 `sqlite3.connect()` 호출 | src/prediction/ | Medium |
| 3 | 133개 bare `except Exception:` 에러 삼킴 | 57개 파일 | Low (반복작업) |
| 4 | 19개 파일 `sys.path.insert()` 해킹 | 각종 스크립트 | Medium |
| 5 | PredictionResult 45+ 필드 bloat | improved_predictor.py | Medium |

### Security
| # | Issue | Location | Effort |
|---|-------|----------|--------|
| 1 | api_order.py 8개 엔드포인트 `@admin_required` 누락 | api_order.py | Low |
| 2 | CSP `unsafe-inline` + `unsafe-eval` | app.py:82 | Low |
| 3 | 비밀번호 최소 4자 | api_auth.py:67 | Low |
| 4 | Rate limiting 인메모리 (서버 재시작 시 초기화) | api_auth.py:20 | Medium |

### Architecture
| # | Issue | Location | Effort |
|---|-------|----------|--------|
| 1 | `_get_store_db_path()` 5곳 중복 | web/routes 5개 파일 | Low |
| 2 | Domain Layer 간접 I/O (food.py → DB 접근) | domain/prediction/strategies/ | Medium |
| 3 | Legacy 파일 153개 vs Formal Layer 109개 | src/ 루트 | High |
| 4 | app_settings 테이블 이중 배치 (common + store) | schema.py | Low |

### QA / Testing
| # | Issue | Location | Effort |
|---|-------|----------|--------|
| 1 | conftest.py product_details 컬럼 누락 → 15개 테스트 실패 | conftest.py:123 | **30분** |
| 2 | daily_job.py DailyCollectionJob 테스트 0건 | - | High |
| 3 | sales_collector.py 테스트 0건 | - | Medium |
| 4 | auto_order.py execute() 통합 테스트 0건 | - | Medium |
| 5 | Collector 4개 테스트 0건 | - | Medium |

---

## Positive Findings

| Area | Finding |
|------|---------|
| SQL Injection | 25+ Repository 전부 parameterized queries (OWASP A03 Low) |
| N+1 Query | predict_batch()에서 배치 캐시 잘 구현 |
| Repository Pattern | 33개 repos 전부 BaseRepository 상속 (97% 일관성) |
| Strategy Pattern | 15개 카테고리 전략 100% 일관 |
| Naming Convention | 90-98% 준수 |
| Test Suite | 2800+ 테스트, 좋은 격리 (in-memory SQLite) |
| Domain Layer | src/domain/ I/O 의존 없음 (깨끗) |
| Audit Trail | 설정 변경, 인증 이벤트, 관리자 작업 로깅 완비 |
| Credential Management | 환경변수 기반 (하드코딩 없음) |

---

## Action Plan (Priority Order)

### Immediate (이번 주)
1. `.env` 자격증명 교체 + `.env.example` 분리
2. `conftest.py` product_details 컬럼 추가 (15개 테스트 복구, 30분)
3. `api_order.py` 8개 엔드포인트에 `@admin_required` 추가
4. CSP `unsafe-eval` 제거, 비밀번호 최소 8자

### Short-term (2주 내)
5. `_get_store_db_path()` 중복 제거 → `DBRouter.get_store_db_path()`
6. CSRF 토큰 추가 (Flask-WTF)
7. `test_daily_collection_job.py` 작성
8. bare `except Exception:` → 로깅 추가 (57개 파일)

### Mid-term (1개월)
9. ImprovedPredictor 분리 (SafetyStock, MLEnsemble, Weather 등)
10. Web routes → Application Service 패턴 전환
11. `test_auto_order_execute.py` + `test_sales_collector.py` 작성
12. pytest --cov 설정 + Coverage baseline

### Long-term (분기)
13. AutoOrderSystem 분리
14. Legacy 파일 → Formal Layer 마이그레이션
15. CI/CD 파이프라인 (GitHub Actions)
16. Domain Strategy → 메인 파이프라인 연결

---

## Detailed Reports

| Report | Path |
|--------|------|
| Code Quality | `docs/03-analysis/project-code-review.analysis.md` |
| Security | `docs/03-analysis/project-security-review.analysis.md` |
| Architecture | `docs/03-analysis/project-architecture-review.analysis.md` |
| QA Strategy | `docs/03-analysis/project-qa-review.analysis.md` |
