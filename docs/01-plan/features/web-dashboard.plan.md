# Plan: 웹 대시보드 (발주 컨트롤 + 리포트)

> **Feature**: web-dashboard
> **Created**: 2026-02-02
> **Status**: Draft

---

## 1. 목적

바탕화면 아이콘 더블클릭 → 브라우저에서 웹 대시보드가 바로 열리고,
**발주 컨트롤**과 **리포트** 두 가지 메뉴를 선택하여
모든 운영 기능을 HTML 화면에서 수행한다.

---

## 2. 사용자 플로우

```
[바탕화면 아이콘 더블클릭]
    ↓
[Python: Flask 서버 자동 시작 (localhost:8050)]
    ↓
[브라우저 자동 열림]
    ↓
┌─────────────────────────────────────────────────┐
│  BGF 발주 시스템    [발주 컨트롤]  [리포트]       │
├─────────────────────────────────────────────────┤
│                                                 │
│     선택한 탭 내용이 표시됨                        │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## 3. 메뉴 구성

### 3-1. 발주 컨트롤

| 섹션 | 기능 | 설명 |
|------|------|------|
| **파라미터 패널** | 안전재고 일수 조정 | SHELF_LIFE_CONFIG 5개 그룹별 슬라이더 |
| | 회전율 배수 조정 | SAFETY_STOCK_MULTIPLIER 3개 레벨별 입력 |
| | 변동성 배수 조정 | CV 구간별 4개 배수 입력 |
| **예측 실행** | 예측 실행 버튼 | 현재 파라미터로 발주 예측 실행 |
| | max_items 설정 | 최대 상품수 (테스트용) |
| **결과 테이블** | 상품별 발주 목록 | 검색/정렬/필터 가능한 테이블 |
| | 수동 수량 조정 | 개별 상품 발주량 인라인 편집 |
| | 발주 확정 | 조정된 발주량으로 확정 (dry_run 옵션) |
| **요약 카드** | 통계 | 총 발주건수, 총 수량, 카테고리수, 스킵건수 |

### 3-2. 리포트

| 섹션 | 기능 | 설명 |
|------|------|------|
| **일일 발주 대시보드** | 카테고리 차트 | 카테고리별 발주량 Bar Chart |
| | 안전재고 분포 | 히스토그램 |
| | 상세 테이블 | 전체 상품 목록 |
| **주간 트렌드** | 7일 추이 | 카테고리별 판매량 Line Chart |
| | 요일 히트맵 | 카테고리 × 요일 히트맵 |
| | 예측 정확도 | MAPE/정확도 듀얼 축 차트 |
| | TOP 10 | 판매 상위 상품 |
| **카테고리 분석** | 카테고리 선택 | 드롭다운으로 카테고리 선택 |
| | 요일 계수 | Radar Chart (설정 vs 기본) |
| | 회전율 분포 | Doughnut Chart |
| | 상품 sparkline | 7일 판매 미니 차트 |
| **안전재고 영향도** | Baseline 비교 | 파라미터 변경 전후 비교 |
| | 카테고리별 변화 | 수평 Bar Chart |
| | 품절 추이 | Line Chart |

---

## 4. 기술 스택

| 구분 | 선택 | 이유 |
|------|------|------|
| **백엔드** | Flask | 경량, 학습곡선 낮음, 기존 프로젝트에 추가 용이 |
| **프론트엔드** | 단일 HTML + Chart.js + Vanilla JS | 기존 리포트 템플릿/CSS 재활용, 빌드 도구 불필요 |
| **스타일** | 기존 다크 테마 CSS 재활용 | base.html의 CSS 그대로 사용 |
| **데이터** | REST API (JSON) | Flask endpoint → JSON 응답 → JS fetch |
| **DB** | SQLite (기존) | 변경 없음 |
| **실행** | 바탕화면 .lnk 바로가기 | pythonw로 서버 시작 + 브라우저 자동 열기 |

### 추가 의존성

```
flask>=3.0
```

기존 jinja2는 Flask에 포함. 추가 설치 1개뿐.

---

## 5. 아키텍처

```
bgf_auto/
├── src/
│   ├── web/                          # ★ 신규 패키지
│   │   ├── __init__.py
│   │   ├── app.py                    # Flask app 생성 + 설정
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── pages.py              # 페이지 렌더링 (/ → 대시보드)
│   │   │   ├── api_order.py          # 발주 컨트롤 API
│   │   │   └── api_report.py         # 리포트 데이터 API
│   │   ├── static/
│   │   │   ├── css/
│   │   │   │   └── dashboard.css     # 다크 테마 (base.html에서 추출)
│   │   │   └── js/
│   │   │       ├── app.js            # 메인 SPA 라우터 + 공통
│   │   │       ├── order.js          # 발주 컨트롤 탭 로직
│   │   │       └── report.js         # 리포트 탭 로직
│   │   └── templates/
│   │       └── index.html            # 단일 HTML (SPA)
│   │
│   ├── report/                       # 기존 리포트 모듈 (재활용)
│   ├── prediction/                   # 기존 예측 모듈
│   └── order/                        # 기존 발주 모듈
│
├── scripts/
│   └── run_dashboard.py              # 서버 시작 + 브라우저 열기
│
└── data/bgf_sales.db                 # 기존 DB
```

---

## 6. API 엔드포인트 (개요)

### 발주 컨트롤

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/order/params` | 현재 파라미터 조회 |
| POST | `/api/order/params` | 파라미터 업데이트 (임시) |
| POST | `/api/order/predict` | 예측 실행 → 결과 반환 |
| POST | `/api/order/adjust` | 개별 상품 발주량 수동 조정 |
| GET | `/api/order/categories` | 카테고리 목록 |

### 리포트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/report/daily` | 일일 발주 데이터 |
| GET | `/api/report/weekly?end_date=` | 주간 트렌드 데이터 |
| GET | `/api/report/category/<mid_cd>` | 카테고리 분석 데이터 |
| GET | `/api/report/impact` | 영향도 데이터 |
| POST | `/api/report/baseline` | Baseline 저장 |

### 페이지

| Method | Path | 설명 |
|--------|------|------|
| GET | `/` | 메인 대시보드 (SPA) |

---

## 7. 데이터 흐름

### 발주 컨트롤 흐름

```
[사용자] 파라미터 조정 (슬라이더/입력)
    ↓ JS fetch POST /api/order/params
[Flask] 파라미터 임시 저장 (세션/메모리)
    ↓
[사용자] "예측 실행" 클릭
    ↓ JS fetch POST /api/order/predict
[Flask] ImprovedPredictor.get_order_candidates() 호출
    ↓ JSON 응답
[브라우저] 결과 테이블 렌더링
    ↓
[사용자] 발주량 수동 조정 (인라인 편집)
    ↓
[사용자] "발주 확정" 클릭
    ↓ JS fetch POST /api/order/adjust
[Flask] 조정된 발주량 기록
```

### 리포트 흐름

```
[사용자] 리포트 탭 선택
    ↓
[브라우저] JS fetch GET /api/report/{type}
    ↓
[Flask] 기존 리포트 모듈 호출 또는 DB 직접 쿼리
    ↓ JSON 응답
[브라우저] Chart.js + 테이블 렌더링
```

---

## 8. 기존 코드 재활용 계획

| 기존 모듈 | 재활용 방식 |
|-----------|------------|
| `src/report/base_report.py` | CSS 테마, Jinja2 필터 참조 |
| `src/report/daily_order_report.py` | `_calc_summary`, `_group_by_category`, `_build_item_table` 로직 → API 전환 |
| `src/report/weekly_trend_report.py` | DB 쿼리 메서드 → API 전환 |
| `src/report/category_detail_report.py` | DB 쿼리 + 요일계수 비교 로직 → API 전환 |
| `src/report/safety_impact_report.py` | baseline 비교 로직 → API 전환 |
| `src/prediction/improved_predictor.py` | `get_order_candidates()` 직접 호출 |
| `src/prediction/categories/default.py` | 파라미터 읽기/표시 |

**핵심**: 기존 리포트 모듈의 **데이터 처리 로직**은 그대로 활용하되,
HTML 렌더링 대신 **JSON 반환**으로 변환한다.

---

## 9. 프론트엔드 구조

### 단일 HTML (SPA) 구조

```html
<body>
    <!-- 상단 네비게이션 -->
    <nav>
        <a href="#order">발주 컨트롤</a>
        <a href="#report">리포트</a>
    </nav>

    <!-- 발주 컨트롤 탭 -->
    <section id="tab-order" style="display:none">
        <div id="params-panel">...</div>
        <div id="predict-controls">...</div>
        <div id="order-results">...</div>
    </section>

    <!-- 리포트 탭 -->
    <section id="tab-report" style="display:none">
        <div id="report-nav">
            일일 | 주간 | 카테고리 | 영향도
        </div>
        <div id="report-content">...</div>
    </section>
</body>
```

### JS 구조

```
app.js      → 탭 전환, fetch 헬퍼, 공통 유틸
order.js    → 파라미터 패널, 예측 실행, 결과 테이블, 수동 조정
report.js   → 일일/주간/카테고리/영향도 서브 탭, Chart.js 렌더링
```

---

## 10. 구현 순서

| Phase | 파일 | 설명 |
|-------|------|------|
| **1. 서버 기반** | `src/web/app.py` | Flask 앱 생성 |
| | `src/web/__init__.py` | 패키지 초기화 |
| | `scripts/run_dashboard.py` | 서버 시작 + 브라우저 |
| **2. 프론트엔드 뼈대** | `templates/index.html` | SPA 레이아웃 |
| | `static/css/dashboard.css` | 다크 테마 CSS |
| | `static/js/app.js` | 탭 전환 + fetch |
| **3. 리포트 API + UI** | `routes/api_report.py` | 리포트 JSON API 4개 |
| | `static/js/report.js` | 리포트 탭 차트/테이블 |
| **4. 발주 컨트롤 API + UI** | `routes/api_order.py` | 발주 JSON API 5개 |
| | `static/js/order.js` | 파라미터 조정 + 예측 |
| **5. 바탕화면 연동** | `install_desktop.py` 수정 | 아이콘 → 서버 시작 |

---

## 11. 리스크 및 대응

| 리스크 | 심각도 | 대응 |
|--------|--------|------|
| 예측 실행 시간 (전체 상품 수백개) | 중 | max_items 제한 + 로딩 인디케이터 |
| 파라미터 변경이 영구 반영 | 높 | 세션 내 임시 적용만, "저장" 별도 확인 |
| Flask 서버 포트 충돌 | 낮 | 포트 사용 중이면 +1 자동 탐색 |
| 브라우저 미설치 | 낮 | webbrowser.open() 기본 처리 |

---

## 12. 검증 기준

- [ ] 바탕화면 아이콘 더블클릭 → 브라우저에 대시보드 표시
- [ ] 발주 컨트롤: 파라미터 조정 → 예측 실행 → 결과 테이블 표시
- [ ] 발주 컨트롤: 개별 상품 발주량 수동 수정 가능
- [ ] 리포트: 일일/주간/카테고리/영향도 4개 서브 탭 전환
- [ ] 리포트: Chart.js 차트 + 검색/정렬 테이블 정상 동작
- [ ] Flask 서버 종료 시 정상 종료 (Ctrl+C 또는 브라우저 닫기)
- [ ] 콘솔 창 없이 실행 (pythonw)
