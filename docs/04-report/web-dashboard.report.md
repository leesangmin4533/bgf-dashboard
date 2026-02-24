# PDCA Completion Report: web-dashboard

> **Feature**: web-dashboard (웹 대시보드 - 발주 컨트롤 + 리포트)
> **Date**: 2026-02-02
> **Match Rate**: 93%
> **Status**: Completed

---

## 1. Executive Summary

BGF 리테일 자동 발주 시스템에 Flask 기반 웹 대시보드를 추가하여, 바탕화면 아이콘 더블클릭만으로 브라우저에서 발주 컨트롤과 리포트 기능을 사용할 수 있도록 구현하였습니다.

### Key Metrics

| 지표 | 값 |
|------|-----|
| PDCA 단계 | Plan → Design → Do → Check (93%) |
| 신규 파일 | 13개 |
| 수정 파일 | 1개 (install_desktop.py) |
| API 엔드포인트 | 11개 (order 5 + report 5 + page 1) |
| 프론트엔드 차트 | 6종 (Bar, Line, Radar, Doughnut, Heatmap, Sparkline) |
| Gap 발견 | 1건 (CSS 색상 오류, 수정 완료) |
| 추가 의존성 | flask>=3.0 (1개) |

---

## 2. Plan Phase Summary

**문서**: `docs/01-plan/features/web-dashboard.plan.md`

### 목적
기존 HTML 리포트(파일 생성 → 브라우저 열기)를 대체하여, 실시간 웹 대시보드로 발주 컨트롤과 리포트를 통합 제공.

### 핵심 결정사항

| 결정 | 선택 | 이유 |
|------|------|------|
| 백엔드 프레임워크 | Flask | 경량, 기존 프로젝트에 추가 용이, 추가 의존성 1개 |
| 프론트엔드 | 단일 HTML + Chart.js + Vanilla JS | 기존 CSS 재활용, 빌드 도구 불필요 |
| 데이터 통신 | REST API (JSON) | 기존 리포트 모듈의 데이터 처리 로직 재활용 |
| 실행 방식 | pythonw + .lnk 바로가기 | 콘솔 창 없이 실행 |

### 메뉴 구성 (2탭 + 4서브탭)

```
발주 컨트롤
  ├── 안전재고 파라미터 패널 (슬라이더/입력)
  ├── 예측 실행 + 결과 요약
  ├── 발주 상세 테이블 (인라인 편집)
  └── 발주 확정

리포트
  ├── 일일 발주 (카테고리 차트 + 안전재고 분포 + 테이블)
  ├── 주간 트렌드 (7일 추이 + 히트맵 + 정확도 + TOP10)
  ├── 카테고리 분석 (Radar + Doughnut + Sparkline)
  └── 안전재고 영향도 (Baseline 비교 + 품절 추이)
```

---

## 3. Design Phase Summary

**문서**: `docs/02-design/features/web-dashboard.design.md` (950 lines)

### 아키텍처

```
bgf_auto/
├── src/web/                    # 신규 웹 패키지
│   ├── app.py                  # Flask factory + config
│   ├── routes/
│   │   ├── pages.py            # GET / → SPA
│   │   ├── api_order.py        # 발주 컨트롤 5개 API
│   │   └── api_report.py       # 리포트 5개 API
│   ├── templates/index.html    # 단일 SPA
│   └── static/{css,js}/        # 다크 테마 + 3개 JS 모듈
├── scripts/run_dashboard.pyw   # pythonw 런처
└── scripts/install_desktop.py  # 바탕화면 바로가기 (수정)
```

### API 설계

| 그룹 | 엔드포인트 | Method | 설명 |
|------|-----------|--------|------|
| Order | `/api/order/params` | GET | 현재 파라미터 (3그룹: shelf_life, turnover, volatility) |
| Order | `/api/order/params` | POST | 파라미터 임시 저장 (메모리) |
| Order | `/api/order/predict` | POST | ImprovedPredictor 실행 → JSON |
| Order | `/api/order/adjust` | POST | 발주량 수동 조정 |
| Order | `/api/order/categories` | GET | 카테고리 목록 (DB) |
| Report | `/api/report/daily` | GET | 일일 발주 데이터 (캐시 재활용) |
| Report | `/api/report/weekly` | GET | 주간 트렌드 5개 데이터셋 |
| Report | `/api/report/category/<mid_cd>` | GET | 카테고리 분석 5개 데이터셋 |
| Report | `/api/report/impact` | GET | 영향도 비교 4개 데이터셋 |
| Report | `/api/report/baseline` | POST | Baseline 저장 |
| Page | `/` | GET | SPA index.html |

### 기존 코드 재활용

| 기존 모듈 | 재활용 메서드 | 용도 |
|-----------|-------------|------|
| `DailyOrderReport` | `_calc_summary`, `_group_by_category`, `_build_item_table`, `_build_skipped_list`, `_build_safety_distribution` | 발주 예측 결과 JSON 변환 |
| `WeeklyTrendReportHTML` | `_query_weekly_summary`, `_query_daily_category_sales`, `_query_weekday_heatmap`, `_query_top_items`, `_query_accuracy` | 주간 트렌드 DB 쿼리 |
| `CategoryDetailReport` | `_query_overview`, `_get_weekday_comparison`, `_query_turnover_distribution`, `_query_sparklines`, `_get_safety_config` | 카테고리 분석 데이터 |
| `SafetyImpactReport` | `save_baseline`, `_build_comparisons`, `_aggregate_by_category`, `_calc_overall_summary`, `_query_stockout_trend` | 영향도 비교 |
| `ImprovedPredictor` | `get_order_candidates(min_order_qty=0)` | 발주 예측 실행 |

---

## 4. Do Phase Summary (Implementation)

### 생성된 파일 (13개)

| # | 파일 | 크기 | 역할 |
|---|------|------|------|
| 1 | `src/web/__init__.py` | 1줄 | 패키지 초기화 |
| 2 | `src/web/app.py` | 36줄 | Flask factory + DB/config 설정 |
| 3 | `src/web/routes/__init__.py` | 13줄 | 3개 Blueprint 등록 |
| 4 | `src/web/routes/pages.py` | 10줄 | GET / → index.html |
| 5 | `src/web/routes/api_order.py` | 150줄 | 발주 컨트롤 API 5개 |
| 6 | `src/web/routes/api_report.py` | 152줄 | 리포트 데이터 API 5개 + 캐시 헬퍼 |
| 7 | `src/web/templates/index.html` | 247줄 | SPA (2탭 + 4서브탭) |
| 8 | `src/web/static/css/dashboard.css` | 217줄 | 다크 테마 CSS (30+ 컴포넌트) |
| 9 | `src/web/static/js/app.js` | 131줄 | 탭 전환, fetch, 테이블, Chart.js |
| 10 | `src/web/static/js/order.js` | 301줄 | 파라미터 패널, 예측, 발주 확정 |
| 11 | `src/web/static/js/report.js` | 506줄 | 일일/주간/카테고리/영향도 리포트 |
| 12 | `scripts/run_dashboard.pyw` | 36줄 | 서버 시작 + 브라우저 자동 열기 |
| 13 | `scripts/install_desktop.py` | 수정 | 바로가기 타겟 변경 |

**총 코드량**: ~1,799줄 (Python 397 + HTML 247 + CSS 217 + JS 938)

### 구현 특징

1. **기존 모듈 직접 재활용**: 리포트 클래스의 private 메서드를 직접 호출하여 JSON 반환으로 변환. 데이터 처리 로직 중복 없음.

2. **예측 결과 캐싱**: `app.config["LAST_PREDICTIONS"]`에 마지막 예측 결과 저장. 발주 컨트롤과 리포트 탭 간 데이터 공유.

3. **파라미터 안전성**: 파라미터 변경은 `app.config["TEMP_PARAMS"]`에만 저장. 서버 재시작 시 자동 초기화. 실제 코드 파일 변경 없음.

4. **차트 재생성 방지**: `getOrCreateChart(canvasId, config)` 유틸로 기존 차트 인스턴스 destroy 후 재생성.

5. **ES5 호환**: 모든 JS 코드가 `var`/`function` 사용. IE11 미지원이지만 구형 Chrome에서도 동작.

### 테스트 결과

| 테스트 | 결과 |
|--------|------|
| Flask 앱 생성 | OK |
| 11개 라우트 등록 | OK |
| `GET /` | 200 (247줄 HTML) |
| `GET /api/order/params` | 200 (5 shelf_life + 3 turnover + 4 volatility) |
| `GET /api/order/categories` | 200 (68 categories) |
| `GET /api/report/weekly` | 200 (datasets + heatmap + top_items) |
| `GET /api/report/category/049` | 200 (63 items + sparklines) |
| Desktop shortcut 설치 | OK |

---

## 5. Check Phase Summary (Gap Analysis)

**문서**: `docs/03-analysis/web-dashboard.analysis.md`

### Match Rate: 93% (보수적) / 98.6% (항목 기준)

| 카테고리 | 항목 수 | 일치 | 비율 |
|----------|--------|------|------|
| 파일 구조 | 13 | 13 | 100% |
| 백엔드 API | 30 | 30 | 100% |
| 프론트엔드 HTML | 25 | 25 | 100% |
| CSS | 22 | 21 | 95% |
| JS (app.js) | 10 | 10 | 100% |
| JS (order.js) | 14 | 14 | 100% |
| JS (report.js) | 18 | 17 | 94% |
| 런처/설치 | 8 | 8 | 100% |
| **합계** | **140** | **138** | **98.6%** |

### 발견된 Gap

| # | 심각도 | 내용 | 상태 |
|---|--------|------|------|
| G-1 | Low | CSS `.btn-accent:hover` 색상값 공백 (`#8fff c4`) | **수정 완료** |
| U-1 | Minor | `report.js` `_categoryCache` 미사용 변수 | 잔존 (기능 영향 없음) |

### 합리적 개선 (7건, Gap 아님)

- `app.py`: sys.path 설정 추가 (필수)
- `index.html`: 파라미터 소제목, predictStatus, btnLoadImpact, impactSearch, dailyTable 등 UX 개선
- `app.js`: badgeHtml(), getOrCreateChart() 공유 유틸 추가
- `order.js`: SHELF_LABELS/TURNOVER_LABELS 한글 매핑
- `run_dashboard.pyw`: import 경로 `src.web.app` (더 명확)
- `dashboard.css`: chart-box, param-actions, status-text.error 추가
- `report.js`: 품절 change_date 포인트 하이라이트

---

## 6. PDCA Cycle Overview

```
[Plan] -----> [Design] -----> [Do] -----> [Check] -----> [Report]
  OK            OK             OK        93% (Pass)       OK

Duration: 1 day (2026-02-02)
Iterations: 0 (Check >= 90%, no Act phase needed)
```

### Phase Timeline

| Phase | 산출물 | 상태 |
|-------|--------|------|
| Plan | `docs/01-plan/features/web-dashboard.plan.md` | Completed |
| Design | `docs/02-design/features/web-dashboard.design.md` (950줄) | Completed |
| Do | 13개 파일, ~1,799줄 코드 | Completed |
| Check | `docs/03-analysis/web-dashboard.analysis.md` (Match Rate 93%) | Completed |
| Act | N/A (93% >= 90% 기준 충족) | Skipped |
| Report | `docs/04-report/web-dashboard.report.md` (현재 문서) | Completed |

---

## 7. Deliverables

### 코드 산출물

```
bgf_auto/
├── src/web/
│   ├── __init__.py
│   ├── app.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── pages.py
│   │   ├── api_order.py
│   │   └── api_report.py
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── css/dashboard.css
│       └── js/
│           ├── app.js
│           ├── order.js
│           └── report.js
├── scripts/
│   ├── run_dashboard.pyw
│   └── install_desktop.py (수정)
```

### 문서 산출물

```
bgf_auto/docs/
├── 01-plan/features/web-dashboard.plan.md
├── 02-design/features/web-dashboard.design.md
├── 03-analysis/web-dashboard.analysis.md
└── 04-report/web-dashboard.report.md
```

---

## 8. Usage Guide

### 실행

```bash
# 서버 시작 + 브라우저 열기
python scripts/run_dashboard.pyw

# 또는 바탕화면 "BGF 발주 시스템" 아이콘 더블클릭
```

### 발주 컨트롤 워크플로우

1. 파라미터 패널에서 안전재고 일수/회전율 배수/변동성 배수 조정
2. "예측 실행" 클릭 → 요약 카드 + 카테고리 차트 + 상세 테이블 확인
3. 필요시 개별 상품 발주량 인라인 수정
4. "발주 확정" 클릭

### 리포트 워크플로우

1. 리포트 탭 클릭 → 일일 발주 자동 로드
2. 주간 트렌드: 서브탭 클릭 (lazy load)
3. 카테고리 분석: 드롭다운에서 카테고리 선택 → "분석" 클릭
4. 안전재고 영향도: "현재 Baseline 저장" → "영향도 분석" 클릭

---

## 9. Known Limitations

| 항목 | 설명 | 대응 |
|------|------|------|
| 파라미터 영속성 | 메모리 저장, 서버 재시작 시 초기화 | 의도적 설계 (안전성) |
| 동시 사용자 | 단일 사용자 가정 (localhost) | 편의점 1인 운영 환경 |
| 예측 실행 시간 | 전체 상품 수백개 시 수 초 소요 | max_items 제한 + 스피너 표시 |
| 미사용 변수 | `_categoryCache` in report.js | 기능 영향 없음 |

---

## 10. Lessons Learned

1. **기존 모듈 직접 재활용**: 리포트 클래스의 private 메서드를 직접 호출하는 방식은 코드 중복을 완전히 제거하지만, 향후 리포트 모듈 인터페이스 변경 시 API 라우트도 함께 수정해야 하는 커플링 존재.

2. **설계 문서의 가치**: 950줄 상세 설계 → 13개 파일 구현에서 Match Rate 93%. 설계가 상세할수록 구현-검증 속도 향상.

3. **SPA vs MPA**: 단일 HTML SPA 방식은 빌드 도구 없이 즉시 배포 가능하지만, JS 파일 크기가 커지면 모듈 분리 한계. 현재 규모(938줄)에서는 적절.

4. **Chart.js 인스턴스 관리**: `getOrCreateChart()` 패턴으로 Canvas 재사용 오류를 방지. 차트 라이브러리 사용 시 인스턴스 생명주기 관리 필수.
