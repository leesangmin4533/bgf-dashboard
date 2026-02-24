# Plan: HTML 리포트 대시보드 시스템

> **Feature**: html-report-dashboard
> **Created**: 2026-02-02
> **Status**: Draft
> **Level**: Dynamic

---

## 1. 목적 및 배경

### 문제 정의
- 현재 리포트가 텍스트(콘솔/파일), 카카오톡(텍스트), Excel로 분산되어 있음
- 안전재고 파라미터 변경(2026-02-02) 등 설정 변경의 영향도를 시각적으로 추적할 수단이 없음
- 카테고리별 판매/재고/발주 트렌드를 한눈에 파악하기 어려움
- 기존 리포트(accuracy reporter, eval reporter, trend report, waste report)의 데이터가 이미 충분히 있으나 통합 뷰가 없음

### 목표
- **단일 HTML 파일**로 생성되는 통합 대시보드 리포트 시스템 구축
- 브라우저에서 바로 열 수 있고, 서버 불필요
- 기존 데이터(SQLite)와 예측 결과(PredictionResult)를 시각화
- 스케줄러에 통합하여 매일/주간 자동 생성

---

## 2. 리포트 종류 (4개)

### 2-1. 일일 발주 대시보드 (`daily_order_report.html`)
**생성 시점**: 매일 발주 완료 후 자동 생성
**용도**: 오늘 발주 결과 한눈에 확인

**섹션 구성**:
| # | 섹션 | 내용 | 데이터 소스 |
|---|------|------|-------------|
| 1 | 요약 카드 | 총 발주건수, 스킵건수, 총 발주수량, 카테고리수 | PredictionResult 집계 |
| 2 | 카테고리별 발주 요약 | 카테고리별 건수/수량 (bar chart) | PredictionResult.mid_cd 그룹 |
| 3 | 상품별 발주 상세 | 테이블: 상품명, 일평균, 안전재고, 현재고, 미입고, 발주량 | PredictionResult 전체 |
| 4 | 발주 스킵 목록 | 스킵 상품 + 사유 (상한선 초과, 재고 충분 등) | order_qty=0 필터 |
| 5 | 안전재고 분포 | 카테고리별 안전재고 일수 분포 (box plot / histogram) | safety_stock, daily_avg |

### 2-2. 안전재고 변경 영향도 리포트 (`safety_stock_impact_report.html`)
**생성 시점**: 수동 실행 또는 파라미터 변경 후
**용도**: 안전재고 파라미터 변경 전/후 비교

**섹션 구성**:
| # | 섹션 | 내용 | 데이터 소스 |
|---|------|------|-------------|
| 1 | 변경 요약 | 변경된 파라미터 목록 (before/after 테이블) | 설정 딕셔너리 비교 |
| 2 | 상품별 영향도 | 안전재고 변화량 테이블 (delta, %변화) | baseline JSON vs current |
| 3 | 카테고리별 감소율 | 카테고리별 평균 안전재고 감소율 (bar chart) | 집계 |
| 4 | 품절 추적 | 일별 품절 발생 건수 트렌드 (line chart, 변경일 마커) | daily_sales stock_qty=0 |
| 5 | 폐기 추적 | 일별 폐기량 트렌드 (line chart) | inventory_batches |

### 2-3. 주간 트렌드 리포트 (`weekly_trend_report.html`)
**생성 시점**: 매주 월요일 08:00 (기존 --weekly-report 확장)
**용도**: 주간 판매/예측 트렌드 분석

**섹션 구성**:
| # | 섹션 | 내용 | 데이터 소스 |
|---|------|------|-------------|
| 1 | 주간 요약 카드 | 총판매량, 전주대비 증감, 예측정확도(MAPE) | daily_sales, accuracy |
| 2 | 카테고리별 판매 추이 | 7일 line chart (카테고리별) | daily_sales 그룹 |
| 3 | 예측 정확도 | 예측 vs 실제 scatter plot + 정확도 구간별 비율 | eval_outcomes |
| 4 | 회전율 변동 상품 | 고→저 / 저→고 전환 상품 하이라이트 | daily_avg 비교 |
| 5 | 폐기 위험 Top 10 | 폐기율 상위 상품 + 잔여 유통기한 | inventory_batches |
| 6 | 요일별 패턴 | 요일별 판매량 히트맵 (카테고리 × 요일) | daily_sales |

### 2-4. 카테고리 심층 분석 (`category_detail_report.html`)
**생성 시점**: 수동 실행 (특정 카테고리 지정)
**용도**: 개별 카테고리 drill-down 분석

**섹션 구성**:
| # | 섹션 | 내용 | 데이터 소스 |
|---|------|------|-------------|
| 1 | 카테고리 개요 | 상품수, 총판매량, 평균회전율, 안전재고 설정 | products + daily_sales |
| 2 | 요일 계수 비교 | 학습값 vs 기본값 히트맵 | _learn_weekday_pattern() |
| 3 | 회전율 분포 | 고/중/저 비율 (donut chart) | daily_avg 분류 |
| 4 | 상품별 sparkline | 각 상품의 7일 판매 미니 트렌드 | daily_sales |
| 5 | 계절 패턴 | 월별 계절 계수 (frozen_ice 등) | DEFAULT_SEASONAL_COEF |

---

## 3. 기술 스택

| 항목 | 선택 | 이유 |
|------|------|------|
| 템플릿 엔진 | **Jinja2** | Python 생태계 표준, pip install 1개 |
| 차트 라이브러리 | **Chart.js** (CDN) | 단일 HTML에 인라인 가능, 가볍고 반응형 |
| CSS 프레임워크 | **없음** (자체 minimal CSS) | 의존성 최소화, 인라인 style |
| 테이블 정렬/검색 | 간단한 vanilla JS | 외부 라이브러리 불필요 |
| 출력 형태 | **단일 self-contained HTML** | 서버 불필요, 브라우저에서 바로 열기 |

### 의존성 추가
```
jinja2>=3.1.0   # 유일한 신규 의존성
```
Chart.js는 CDN `<script>` 태그로 포함 (오프라인 시 인라인 번들 옵션).

---

## 4. 아키텍처

### 디렉토리 구조

```
bgf_auto/
├── src/
│   └── report/                      # ★ 신규 모듈
│       ├── __init__.py
│       ├── base_report.py           # 공통 리포트 생성기 (Jinja2 렌더링)
│       ├── daily_order_report.py    # 일일 발주 리포트
│       ├── safety_impact_report.py  # 안전재고 영향도 리포트
│       ├── weekly_trend_report.py   # 주간 트렌드 리포트
│       ├── category_detail_report.py # 카테고리 심층 분석
│       └── templates/               # Jinja2 HTML 템플릿
│           ├── base.html            # 공통 레이아웃 (CSS, Chart.js CDN)
│           ├── daily_order.html     # 일일 발주
│           ├── safety_impact.html   # 안전재고 영향도
│           ├── weekly_trend.html    # 주간 트렌드
│           └── category_detail.html # 카테고리 심층
│
├── data/
│   └── reports/                     # ★ 신규 - 생성된 HTML 저장
│       ├── daily/                   # daily_order_2026-02-02.html
│       ├── weekly/                  # weekly_trend_2026-W05.html
│       ├── impact/                  # safety_impact_2026-02-02.html
│       └── category/               # category_049_맥주_2026-02-02.html
│
├── scripts/
│   └── run_report.py               # ★ 신규 - 리포트 CLI 진입점
```

### 모듈 관계

```
[scripts/run_report.py]  ← CLI 진입점
        │
        ▼
[src/report/base_report.py]  ← Jinja2 환경 설정, 공통 렌더링
        │
        ├── [daily_order_report.py]     → DB 조회 + PredictionResult → daily_order.html
        ├── [safety_impact_report.py]   → baseline JSON + 현재 결과 비교 → safety_impact.html
        ├── [weekly_trend_report.py]    → DB 7일 집계 + accuracy → weekly_trend.html
        └── [category_detail_report.py] → DB + 카테고리 설정 → category_detail.html
                                               │
                                               ▼
                                    [data/reports/] → HTML 파일 저장
```

---

## 5. 데이터 흐름

### 일일 발주 리포트 데이터 흐름
```
[improved_predictor.py]
   │  PredictionResult[] (list of dataclass)
   ▼
[daily_order_report.py]
   │  1. PredictionResult → dict 변환
   │  2. 카테고리별 집계 (건수, 수량, 스킵)
   │  3. Chart.js용 JSON 데이터 생성
   ▼
[Jinja2 렌더링] → daily_order.html 템플릿
   │
   ▼
[data/reports/daily/daily_order_2026-02-02.html]
```

### 안전재고 영향도 리포트 데이터 흐름
```
[run_full_flow.py --no-collect --save-baseline]   ← 변경 전 실행
   │  → data/reports/impact/baseline_2026-02-02.json
   │
[파라미터 변경 후 동일 실행]
   │  → 현재 PredictionResult[]
   ▼
[safety_impact_report.py]
   │  1. baseline JSON 로드
   │  2. 현재 결과와 상품별 diff 계산
   │  3. 카테고리별 집계
   ▼
[data/reports/impact/safety_impact_2026-02-02.html]
```

---

## 6. CLI 인터페이스

```bash
# 일일 발주 리포트 생성
python scripts/run_report.py --daily

# 안전재고 영향도 리포트 (baseline 필요)
python scripts/run_report.py --impact --baseline data/reports/impact/baseline.json

# 주간 트렌드 리포트
python scripts/run_report.py --weekly

# 카테고리 심층 분석 (특정 카테고리)
python scripts/run_report.py --category 049

# 전체 리포트 일괄 생성
python scripts/run_report.py --all

# baseline 저장 (안전재고 변경 전)
python scripts/run_report.py --save-baseline
```

---

## 7. 스케줄러 통합

```python
# daily_job.py에 추가
schedule.every().day.at("08:30").do(generate_daily_order_report)   # 발주 후 30분
schedule.every().monday.at("08:30").do(generate_weekly_trend_report)  # 주간 리포트
```

- 기존 카카오 알림에 "HTML 리포트 생성 완료" 메시지 + 파일 경로 추가
- 기존 `--weekly-report` 커맨드에 HTML 생성 옵션 추가

---

## 8. 구현 순서 (우선순위)

| 순서 | 모듈 | 우선순위 | 이유 |
|------|------|----------|------|
| 1 | `base_report.py` + `base.html` 템플릿 | **필수** | 모든 리포트의 공통 기반 |
| 2 | `daily_order_report.py` + 템플릿 | **높음** | 매일 사용, 가장 실용적 |
| 3 | `safety_impact_report.py` + 템플릿 | **높음** | 안전재고 변경 검증 급선무 |
| 4 | `weekly_trend_report.py` + 템플릿 | **중간** | 기존 weekly report 확장 |
| 5 | `category_detail_report.py` + 템플릿 | **낮음** | 필요 시 수동 실행 |
| 6 | `run_report.py` CLI | **높음** | 2번과 동시 구현 |
| 7 | 스케줄러 통합 | **중간** | 안정화 후 |

---

## 9. 기존 코드 연동 포인트

| 기존 모듈 | 연동 방법 | 변경 수준 |
|-----------|----------|-----------|
| `improved_predictor.py` | `get_recommendations()` 결과를 report에 전달 | 변경 없음 (읽기만) |
| `auto_order.py` | 발주 완료 후 daily report 호출 추가 | 1줄 추가 |
| `daily_job.py` | 스케줄 등록 | 3-5줄 추가 |
| `run_scheduler.py` | `--report` CLI 옵션 추가 | 5-10줄 추가 |
| `accuracy/reporter.py` | 주간 리포트에서 accuracy 데이터 참조 | 변경 없음 (읽기만) |
| `trend_report.py` | 주간 리포트에서 트렌드 데이터 참조 | 변경 없음 (읽기만) |

---

## 10. 리스크 및 제약

| 리스크 | 영향 | 대응 |
|--------|------|------|
| Jinja2 미설치 환경 | 리포트 생성 불가 | `pip install jinja2` 안내 + graceful fallback |
| Chart.js CDN 오프라인 | 차트 미표시 | 인라인 번들 옵션 (base.html에 조건부 포함) |
| 대량 상품 (500+개) | HTML 파일 크기 증가 | 테이블 페이지네이션 (JS), 상위 100개 기본 표시 |
| DB 조회 성능 | 주간 리포트 생성 시 느릴 수 있음 | 인덱스 확인, 쿼리 최적화 |

---

## 11. 성공 기준

- [ ] `python scripts/run_report.py --daily` 로 HTML 생성 → 브라우저에서 정상 표시
- [ ] 일일 리포트에 카테고리별 발주 요약 차트 포함
- [ ] 안전재고 영향도 리포트에서 변경 전/후 비교 가능
- [ ] 주간 리포트에 예측 정확도 차트 포함
- [ ] 단일 HTML 파일 (외부 의존 없이 열 수 있음)
- [ ] 기존 스케줄러에 통합 가능한 구조
