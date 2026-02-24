# Design: 웹 대시보드 (발주 컨트롤 + 리포트)

> **Feature**: web-dashboard
> **Plan Reference**: `docs/01-plan/features/web-dashboard.plan.md`
> **Created**: 2026-02-02
> **Status**: Draft

---

## 1. 파일 구조

```
bgf_auto/
├── src/
│   └── web/                              # ★ 신규 패키지
│       ├── __init__.py                   # 패키지 초기화
│       ├── app.py                        # Flask 앱 생성 + 설정
│       ├── routes/
│       │   ├── __init__.py               # Blueprint 등록
│       │   ├── pages.py                  # GET / → index.html
│       │   ├── api_order.py              # 발주 컨트롤 API (5개)
│       │   └── api_report.py             # 리포트 데이터 API (5개)
│       ├── templates/
│       │   └── index.html                # 단일 SPA HTML
│       └── static/
│           ├── css/
│           │   └── dashboard.css         # 다크 테마 CSS
│           └── js/
│               ├── app.js               # SPA 라우터 + 공통 유틸
│               ├── order.js             # 발주 컨트롤 탭
│               └── report.js            # 리포트 탭
│
├── scripts/
│   ├── run_dashboard.pyw                 # 서버 시작 + 브라우저 (pythonw)
│   └── install_desktop.py               # 기존 수정: 아이콘 → run_dashboard.pyw
│
└── requirements.txt                      # flask>=3.0 추가
```

**총 신규 파일: 13개**

---

## 2. 백엔드 상세 설계

### 2-1. `src/web/__init__.py`

```python
"""웹 대시보드 패키지"""
```

### 2-2. `src/web/app.py` — Flask 앱 팩토리

```python
"""Flask 앱 생성"""
import os
import sys
from pathlib import Path
from flask import Flask

# 프로젝트 경로 설정
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = str(PROJECT_ROOT / "data" / "bgf_sales.db")

def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["DB_PATH"] = DB_PATH
    app.config["PROJECT_ROOT"] = str(PROJECT_ROOT)
    app.config["SECRET_KEY"] = "bgf-dashboard-local"

    # 발주 파라미터 임시 저장소 (세션 수준)
    app.config["TEMP_PARAMS"] = None
    # 마지막 예측 결과 캐시
    app.config["LAST_PREDICTIONS"] = None

    from .routes import register_blueprints
    register_blueprints(app)

    return app
```

### 2-3. `src/web/routes/__init__.py` — Blueprint 등록

```python
"""라우트 Blueprint 등록"""
from flask import Flask

def register_blueprints(app: Flask):
    from .pages import pages_bp
    from .api_order import order_bp
    from .api_report import report_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(order_bp, url_prefix="/api/order")
    app.register_blueprint(report_bp, url_prefix="/api/report")
```

### 2-4. `src/web/routes/pages.py` — 페이지 라우트

```python
"""페이지 라우팅"""
from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)

@pages_bp.route("/")
def index():
    return render_template("index.html")
```

---

### 2-5. `src/web/routes/api_order.py` — 발주 컨트롤 API

```python
"""발주 컨트롤 REST API"""
from flask import Blueprint, request, jsonify, current_app

order_bp = Blueprint("order", __name__)
```

#### API 엔드포인트 상세

**GET `/api/order/params`** — 현재 파라미터 조회

```
Response 200:
{
    "shelf_life": {
        "ultra_short": {"min_days":1, "max_days":3, "safety_stock_days":0.3, ...},
        "short": {...},
        "medium": {...},
        "long": {...},
        "ultra_long": {...}
    },
    "turnover": {
        "high_turnover": {"min_daily_avg":5.0, "multiplier":1.0, ...},
        "medium_turnover": {...},
        "low_turnover": {...}
    },
    "volatility": [
        {"cv_min":0, "cv_max":0.3, "multiplier":1.0, "label":"안정"},
        {"cv_min":0.3, "cv_max":0.5, "multiplier":1.1, "label":"보통"},
        {"cv_min":0.5, "cv_max":0.8, "multiplier":1.2, "label":"높음"},
        {"cv_min":0.8, "cv_max":999, "multiplier":1.3, "label":"매우높음"}
    ],
    "is_modified": false
}
```

**구현**: `default.py`에서 SHELF_LIFE_CONFIG, SAFETY_STOCK_MULTIPLIER 직접 읽기. feature_calculator.py의 CV 구간은 하드코딩 JSON으로 반환.

---

**POST `/api/order/params`** — 파라미터 임시 업데이트

```
Request Body:
{
    "shelf_life": {"ultra_short": {"safety_stock_days": 0.5}, ...},
    "turnover": {"high_turnover": {"multiplier": 1.2}, ...},
    "volatility": [{"cv_max": 0.3, "multiplier": 1.0}, ...]
}

Response 200:
{"status": "ok", "message": "파라미터 임시 저장 완료"}
```

**구현**: `current_app.config["TEMP_PARAMS"]`에 저장. 서버 재시작 시 초기화. 실제 파일 변경은 하지 않음.

---

**POST `/api/order/predict`** — 예측 실행

```
Request Body:
{
    "max_items": 50   // 0 = 전체
}

Response 200:
{
    "summary": {
        "total_items": 150,
        "ordered_count": 120,
        "skipped_count": 30,
        "total_order_qty": 1580,
        "category_count": 25,
        "avg_safety_stock": 2.3
    },
    "category_data": {
        "labels": ["맥주", "탄산음료", ...],
        "order_qty": [280, 150, ...],
        "count": [15, 12, ...]
    },
    "items": [
        {
            "item_cd": "8801234567890",
            "item_nm": "카스 500ml",
            "category": "맥주",
            "mid_cd": "049",
            "daily_avg": 5.2,
            "weekday_coef": 1.10,
            "adjusted_qty": 5.7,
            "safety_stock": 3.5,
            "current_stock": 12,
            "pending_qty": 0,
            "order_qty": 5,
            "confidence": "high",
            "data_days": 28
        },
        ...
    ],
    "skipped": [
        {
            "item_cd": "...",
            "item_nm": "...",
            "category": "...",
            "current_stock": 30,
            "pending_qty": 10,
            "safety_stock": 2.0,
            "reason": "재고+미입고 충분"
        },
        ...
    ],
    "safety_dist": {
        "labels": ["0~0.5", "0.5~1.0", "1.0~1.5", "1.5~2.0", "2.0~2.5", "2.5~3.0", "3.0+"],
        "counts": [15, 42, 38, 20, 10, 5, 3]
    }
}
```

**구현**: `DailyOrderReport`의 `_calc_summary()`, `_group_by_category()`, `_build_item_table()`, `_build_skipped_list()`, `_build_safety_distribution()` 로직을 재활용하여 JSON 반환.

---

**POST `/api/order/adjust`** — 발주량 수동 조정

```
Request Body:
{
    "adjustments": [
        {"item_cd": "8801234567890", "order_qty": 10},
        {"item_cd": "8809876543210", "order_qty": 0}
    ]
}

Response 200:
{
    "status": "ok",
    "adjusted_count": 2,
    "new_total_qty": 1595
}
```

**구현**: `current_app.config["LAST_PREDICTIONS"]`에 저장된 결과를 수정.

---

**GET `/api/order/categories`** — 카테고리 목록

```
Response 200:
{
    "categories": [
        {"code": "001", "name": "도시락"},
        {"code": "049", "name": "맥주"},
        ...
    ]
}
```

---

### 2-6. `src/web/routes/api_report.py` — 리포트 데이터 API

```python
"""리포트 데이터 REST API"""
from flask import Blueprint, request, jsonify, current_app

report_bp = Blueprint("report", __name__)
```

#### API 엔드포인트 상세

**GET `/api/report/daily`** — 일일 발주 데이터

```
Response 200:
// /api/order/predict 와 동일한 구조
// LAST_PREDICTIONS 캐시가 있으면 재활용, 없으면 새로 실행
```

---

**GET `/api/report/weekly?end_date=2026-02-01`** — 주간 트렌드

```
Response 200:
{
    "weekly_summary": {
        "total_items": 350,
        "total_sales": 12500,
        "total_orders": 8200,
        "total_disuse": 45,
        "prev_sales": 11800,
        "growth_pct": 5.9
    },
    "daily_trend": {
        "labels": ["01-26", "01-27", ...],
        "datasets": [
            {"label": "맥주", "data": [120, 135, ...], "borderColor": "#00d2ff"},
            ...
        ]
    },
    "heatmap": {
        "categories": ["맥주", "탄산음료", ...],
        "weekdays": ["월", "화", "수", "목", "금", "토", "일"],
        "data": [[4.5, 3.2, ...], ...]
    },
    "top_items": {
        "top_sellers": [
            {"item_cd": "...", "item_nm": "...", "category": "...", "total_sales": 85},
            ...
        ]
    },
    "accuracy": {
        "dates": ["01-26", ...],
        "mape_values": [15.2, ...],
        "accuracy_values": [82.3, ...]
    }
}
```

**구현**: `WeeklyTrendReportHTML`의 5개 쿼리 메서드 재활용.

---

**GET `/api/report/category/<mid_cd>`** — 카테고리 분석

```
Response 200:
{
    "mid_cd": "049",
    "cat_name": "맥주",
    "overview": {
        "item_count": 15, "total_sales": 3200,
        "data_days": 28, "daily_avg": 114.3, "avg_per_item": 7.6
    },
    "weekday_coefs": {
        "labels": ["월","화","수","목","금","토","일"],
        "config": [0.85, 0.90, 0.90, 0.95, 1.10, 1.30, 1.20],
        "default": [0.95, 1.00, 1.00, 0.95, 1.05, 1.10, 1.00]
    },
    "turnover_dist": {
        "labels": ["고회전 (5+/일)", "중회전 (2~5/일)", "저회전 (<2/일)"],
        "counts": [5, 6, 4]
    },
    "sparklines": [
        {"item_cd": "...", "item_nm": "...", "data": [5,3,7,4,6,8,5], "total": 38, "avg": 5.4, "max_val": 8},
        ...
    ],
    "safety_config": {
        "shelf_life": [{"group": "...", "range": "...", "safety_days": 0.3}, ...],
        "turnover": [{"level": "...", "min_daily": 5.0, "multiplier": 1.0}, ...]
    }
}
```

**구현**: `CategoryDetailReport`의 5개 메서드 재활용.

---

**GET `/api/report/impact?baseline=path`** — 영향도 데이터

```
Response 200:
{
    "change_date": "2026-02-02",
    "summary": {
        "total_items": 120, "total_old": 350.5, "total_new": 280.2,
        "total_change_pct": -20.1, "decreased_count": 95,
        "increased_count": 10, "unchanged_count": 15
    },
    "comparisons": [...],
    "cat_summary": {"labels": [...], "pct_changes": [...]},
    "stockout_trend": {"labels": [...], "counts": [...], "change_date": "..."}
}
```

---

**POST `/api/report/baseline`** — Baseline 저장

```
Request Body:
{"max_items": 0}

Response 200:
{"status": "ok", "path": "data/reports/impact/baseline_2026-02-02.json", "item_count": 150}
```

---

## 3. 프론트엔드 상세 설계

### 3-1. `templates/index.html` — SPA 구조

```html
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BGF 발주 시스템</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/dashboard.css') }}">
</head>
<body>
    <!-- 상단 네비게이션 -->
    <nav class="top-nav">
        <div class="nav-brand">BGF 발주 시스템</div>
        <div class="nav-tabs">
            <a href="#" class="nav-tab active" data-tab="order">발주 컨트롤</a>
            <a href="#" class="nav-tab" data-tab="report">리포트</a>
        </div>
        <div class="nav-status" id="serverStatus">● 연결됨</div>
    </nav>

    <!-- 발주 컨트롤 탭 -->
    <main id="tab-order" class="tab-content active">
        <!-- 파라미터 패널 -->
        <section class="panel" id="params-panel">
            <h2 class="panel-title">안전재고 파라미터</h2>
            <div class="param-grid" id="shelfLifeParams">
                <!-- JS에서 동적 생성: 5개 그룹별 슬라이더 -->
            </div>
            <div class="param-grid" id="turnoverParams">
                <!-- JS에서 동적 생성: 3개 레벨별 입력 -->
            </div>
            <div class="param-grid" id="volatilityParams">
                <!-- JS에서 동적 생성: 4개 CV 구간별 입력 -->
            </div>
            <div class="param-actions">
                <button id="btnResetParams" class="btn btn-secondary">초기화</button>
                <span id="paramStatus" class="status-text"></span>
            </div>
        </section>

        <!-- 예측 실행 컨트롤 -->
        <section class="panel" id="predict-panel">
            <div class="predict-controls">
                <label>최대 상품수:
                    <input type="number" id="maxItems" value="0" min="0" class="input-sm">
                    <span class="hint">(0=전체)</span>
                </label>
                <button id="btnPredict" class="btn btn-primary">예측 실행</button>
                <div id="predictSpinner" class="spinner" style="display:none"></div>
            </div>
        </section>

        <!-- 요약 카드 -->
        <section id="order-summary" class="card-grid" style="display:none">
            <!-- JS에서 동적 생성 -->
        </section>

        <!-- 카테고리 차트 -->
        <section class="panel" id="order-chart-panel" style="display:none">
            <h2 class="panel-title">카테고리별 발주량</h2>
            <div class="chart-box">
                <canvas id="orderCategoryChart"></canvas>
            </div>
        </section>

        <!-- 발주 상세 테이블 -->
        <section class="panel" id="order-table-panel" style="display:none">
            <h2 class="panel-title">
                상품별 발주 상세
                <button id="btnConfirmOrder" class="btn btn-accent btn-sm" style="float:right">발주 확정</button>
            </h2>
            <input type="text" id="orderSearch" class="search-box" placeholder="상품명, 카테고리 검색...">
            <div class="table-wrapper">
                <div class="table-scroll">
                    <table id="orderTable">
                        <thead>
                            <tr>
                                <th data-sort="text">상품명</th>
                                <th data-sort="text">카테고리</th>
                                <th data-sort="num" class="text-right">일평균</th>
                                <th data-sort="num" class="text-right">요일계수</th>
                                <th data-sort="num" class="text-right">안전재고</th>
                                <th data-sort="num" class="text-right">현재고</th>
                                <th data-sort="num" class="text-right">미입고</th>
                                <th data-sort="num" class="text-right">발주량</th>
                                <th data-sort="text" class="text-center">신뢰도</th>
                            </tr>
                        </thead>
                        <tbody id="orderTableBody">
                            <!-- JS에서 동적 생성 -->
                        </tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- 스킵 목록 -->
        <section class="panel" id="skip-panel" style="display:none">
            <h2 class="panel-title">발주 스킵 목록</h2>
            <div class="table-wrapper">
                <div class="table-scroll">
                    <table id="skipTable">
                        <thead>
                            <tr>
                                <th>상품명</th>
                                <th>카테고리</th>
                                <th class="text-right">현재고</th>
                                <th class="text-right">미입고</th>
                                <th class="text-right">안전재고</th>
                                <th>사유</th>
                            </tr>
                        </thead>
                        <tbody id="skipTableBody"></tbody>
                    </table>
                </div>
            </div>
        </section>
    </main>

    <!-- 리포트 탭 -->
    <main id="tab-report" class="tab-content">
        <!-- 리포트 서브 네비게이션 -->
        <nav class="sub-nav">
            <a href="#" class="sub-tab active" data-report="daily">일일 발주</a>
            <a href="#" class="sub-tab" data-report="weekly">주간 트렌드</a>
            <a href="#" class="sub-tab" data-report="category">카테고리 분석</a>
            <a href="#" class="sub-tab" data-report="impact">안전재고 영향도</a>
        </nav>

        <!-- 일일 발주 리포트 -->
        <div id="report-daily" class="report-view active">
            <!-- /api/order/predict 결과 재활용 -->
            <div id="dailySummaryCards" class="card-grid"></div>
            <div class="panel"><canvas id="dailyCategoryChart"></canvas></div>
            <div class="panel" id="dailyTablePanel"></div>
            <div class="panel"><canvas id="dailySafetyHist"></canvas></div>
        </div>

        <!-- 주간 트렌드 리포트 -->
        <div id="report-weekly" class="report-view">
            <div id="weeklySummaryCards" class="card-grid"></div>
            <div class="panel"><h2 class="panel-title">카테고리별 판매 추이</h2><canvas id="weeklyTrendChart" height="320"></canvas></div>
            <div class="panel"><h2 class="panel-title">예측 정확도</h2><canvas id="weeklyAccuracyChart" height="250"></canvas></div>
            <div class="panel"><h2 class="panel-title">요일별 판매 패턴</h2><div id="weeklyHeatmap" class="table-wrapper"></div></div>
            <div class="panel"><h2 class="panel-title">판매 상위 TOP 10</h2><div id="weeklyTopItems" class="table-wrapper"></div></div>
        </div>

        <!-- 카테고리 분석 리포트 -->
        <div id="report-category" class="report-view">
            <div class="panel">
                <label>카테고리 선택:
                    <select id="categorySelect" class="input-select"></select>
                </label>
                <button id="btnLoadCategory" class="btn btn-primary btn-sm">분석</button>
            </div>
            <div id="categoryContent" style="display:none">
                <div id="catSummaryCards" class="card-grid"></div>
                <div class="panel"><h2 class="panel-title">요일 계수 비교</h2><canvas id="catRadarChart" height="280"></canvas></div>
                <div class="panel-row">
                    <div class="panel"><h2 class="panel-title">회전율 분포</h2><canvas id="catDoughnutChart"></canvas></div>
                    <div class="panel"><h2 class="panel-title">안전재고 설정</h2><div id="catSafetyTable"></div></div>
                </div>
                <div class="panel"><h2 class="panel-title">상품별 7일 추이</h2><div id="catSparklines" class="table-wrapper"></div></div>
            </div>
        </div>

        <!-- 안전재고 영향도 리포트 -->
        <div id="report-impact" class="report-view">
            <div class="panel">
                <button id="btnSaveBaseline" class="btn btn-secondary btn-sm">현재 Baseline 저장</button>
                <span id="baselineStatus" class="status-text"></span>
            </div>
            <div id="impactContent" style="display:none">
                <div id="impactSummaryCards" class="card-grid"></div>
                <div class="panel"><h2 class="panel-title">카테고리별 변화율</h2><canvas id="impactCatChart"></canvas></div>
                <div class="panel"><h2 class="panel-title">상품별 비교</h2><div id="impactTable" class="table-wrapper"></div></div>
                <div class="panel"><h2 class="panel-title">품절 추이</h2><canvas id="impactStockoutChart"></canvas></div>
            </div>
        </div>
    </main>

    <!-- 스크립트 -->
    <script src="{{ url_for('static', filename='js/app.js') }}"></script>
    <script src="{{ url_for('static', filename='js/order.js') }}"></script>
    <script src="{{ url_for('static', filename='js/report.js') }}"></script>
</body>
</html>
```

---

### 3-2. `static/css/dashboard.css` — 다크 테마

기존 `base.html`의 CSS를 그대로 추출하되 다음을 추가:

```css
/* === 추가 컴포넌트 === */

/* 상단 네비게이션 */
.top-nav {
    display: flex; align-items: center;
    background: #0a0a14; padding: 0 24px; height: 56px;
    border-bottom: 1px solid #1e3054; position: sticky; top: 0; z-index: 100;
}
.nav-brand { font-size: 1.2em; font-weight: 700; color: #fff; margin-right: 32px; }
.nav-tabs { display: flex; gap: 4px; }
.nav-tab {
    padding: 8px 24px; color: #999; text-decoration: none;
    border-radius: 8px 8px 0 0; font-weight: 600; font-size: 0.95em;
    transition: all 0.2s;
}
.nav-tab:hover { color: #fff; background: rgba(255,255,255,0.05); }
.nav-tab.active { color: #00d2ff; background: #16213e; border-bottom: 2px solid #00d2ff; }
.nav-status { margin-left: auto; font-size: 0.82em; color: #69f0ae; }

/* 탭 컨텐츠 */
.tab-content { display: none; padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
.tab-content.active { display: block; }

/* 리포트 서브 네비게이션 */
.sub-nav {
    display: flex; gap: 8px; margin-bottom: 20px;
    border-bottom: 1px solid #1e3054; padding-bottom: 8px;
}
.sub-tab {
    padding: 6px 16px; color: #888; text-decoration: none;
    border-radius: 6px; font-size: 0.9em;
}
.sub-tab:hover { color: #ccc; background: rgba(255,255,255,0.04); }
.sub-tab.active { color: #00d2ff; background: #16213e; }

.report-view { display: none; }
.report-view.active { display: block; }

/* 패널 */
.panel {
    background: #16213e; border-radius: 10px; padding: 20px;
    border: 1px solid #1e3054; margin-bottom: 16px;
}
.panel-title {
    font-size: 1.1em; font-weight: 600; color: #ccc; margin-bottom: 14px;
    padding-left: 10px; border-left: 3px solid #00d2ff;
}
.panel-row { display: flex; gap: 16px; }
.panel-row > .panel { flex: 1; }

/* 파라미터 그리드 */
.param-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 16px; }
.param-card {
    background: #0f0f1a; border-radius: 8px; padding: 12px 16px;
    border: 1px solid #1e3054;
}
.param-card label { display: block; color: #999; font-size: 0.82em; margin-bottom: 4px; }
.param-card .param-value { font-size: 1.3em; font-weight: 700; color: #ffd600; }
.param-card input[type="range"] { width: 100%; accent-color: #00d2ff; }
.param-card input[type="number"] {
    width: 80px; background: #16213e; border: 1px solid #1e3054;
    color: #ffd600; padding: 4px 8px; border-radius: 4px;
    font-size: 1.1em; font-weight: 600; text-align: center;
}

/* 버튼 */
.btn {
    padding: 10px 24px; border: none; border-radius: 8px;
    font-weight: 600; cursor: pointer; font-size: 0.95em; transition: all 0.2s;
}
.btn-primary { background: #00d2ff; color: #000; }
.btn-primary:hover { background: #33dfff; }
.btn-secondary { background: #1e3054; color: #ccc; }
.btn-accent { background: #69f0ae; color: #000; }
.btn-sm { padding: 6px 14px; font-size: 0.85em; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* 인라인 편집 */
.editable-qty {
    width: 60px; background: #0f0f1a; border: 1px solid #1e3054;
    color: #69f0ae; padding: 4px 6px; border-radius: 4px;
    text-align: center; font-weight: 700; font-size: 0.95em;
}
.editable-qty:focus { border-color: #00d2ff; outline: none; }
.editable-qty.modified { border-color: #ffd600; color: #ffd600; }

/* 로딩 */
.spinner {
    width: 20px; height: 20px; border: 3px solid #1e3054;
    border-top-color: #00d2ff; border-radius: 50%;
    animation: spin 0.8s linear infinite; display: inline-block; vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 입력 */
.input-sm {
    width: 80px; background: #0f0f1a; border: 1px solid #1e3054;
    color: #e0e0e0; padding: 6px 8px; border-radius: 6px; text-align: center;
}
.input-select {
    background: #0f0f1a; border: 1px solid #1e3054; color: #e0e0e0;
    padding: 8px 12px; border-radius: 6px; font-size: 0.9em; min-width: 200px;
}
.hint { color: #666; font-size: 0.82em; }
.status-text { color: #69f0ae; font-size: 0.85em; margin-left: 12px; }
```

기존 base.html의 `.card-grid`, `.card`, `.table-wrapper`, `.table-scroll`, `table`, `.search-box`, `.badge-*`, `.positive`, `.negative`, `.text-right`, `.text-center` CSS를 그대로 포함.

---

### 3-3. `static/js/app.js` — SPA 라우터 + 공통

```javascript
/* SPA 탭 전환, fetch 헬퍼, 공통 유틸 */

// 탭 전환
document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', e => {
        e.preventDefault();
        const target = tab.dataset.tab;
        // 탭 버튼 active
        document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        // 컨텐츠 전환
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab-' + target).classList.add('active');
    });
});

// 리포트 서브탭 전환
document.querySelectorAll('.sub-tab').forEach(tab => {
    tab.addEventListener('click', e => {
        e.preventDefault();
        const target = tab.dataset.report;
        document.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.report-view').forEach(v => v.classList.remove('active'));
        document.getElementById('report-' + target).classList.add('active');
    });
});

// fetch 헬퍼
async function api(url, options = {}) {
    const res = await fetch(url, {
        headers: {'Content-Type': 'application/json'},
        ...options,
        body: options.body ? JSON.stringify(options.body) : undefined,
    });
    return res.json();
}

// 숫자 포맷
function fmt(n) { return n == null ? '0' : Number(n).toLocaleString('ko-KR'); }

// 카드 생성 헬퍼
function renderCards(containerId, cards) {
    // cards = [{value, label, type?}]
    const el = document.getElementById(containerId);
    el.innerHTML = cards.map(c =>
        `<div class="card ${c.type || ''}">
            <div class="value">${c.value}</div>
            <div class="label">${c.label}</div>
        </div>`
    ).join('');
    el.style.display = '';
}

// 테이블 검색
function initTableSearch(inputId, tableId) { /* 기존 base.html 로직 동일 */ }

// 테이블 정렬
function initTableSort(tableId) { /* 기존 base.html 로직 동일 */ }

// Chart.js 다크 테마 기본값
Chart.defaults.color = '#ccc';
Chart.defaults.borderColor = '#2a2a4a';
Chart.defaults.plugins.legend.labels.boxWidth = 12;
```

---

### 3-4. `static/js/order.js` — 발주 컨트롤 탭

주요 함수:

```javascript
/* 발주 컨트롤 탭 로직 */

// 페이지 로드 시 파라미터 불러오기
async function loadParams() {
    const data = await api('/api/order/params');
    renderShelfLifeParams(data.shelf_life);     // 5개 슬라이더
    renderTurnoverParams(data.turnover);         // 3개 입력
    renderVolatilityParams(data.volatility);     // 4개 입력
}

// 슬라이더 변경 시 서버에 임시 저장
async function saveParams() {
    const params = collectParamValues();
    await api('/api/order/params', {method:'POST', body: params});
}

// 예측 실행 버튼
async function runPredict() {
    // 로딩 표시
    // POST /api/order/predict
    // 결과 → 요약 카드 + 카테고리 차트 + 테이블 렌더링
}

// 결과 테이블 렌더링 (발주량 인라인 편집 가능)
function renderOrderTable(items) {
    // 각 행의 발주량 셀을 <input type="number" class="editable-qty">로 렌더
    // 변경 시 .modified 클래스 추가
}

// 발주 확정 버튼
async function confirmOrder() {
    // 수정된 발주량 수집 → POST /api/order/adjust
}

// 초기화
document.addEventListener('DOMContentLoaded', loadParams);
document.getElementById('btnPredict').addEventListener('click', runPredict);
document.getElementById('btnConfirmOrder').addEventListener('click', confirmOrder);
document.getElementById('btnResetParams').addEventListener('click', resetParams);
```

---

### 3-5. `static/js/report.js` — 리포트 탭

주요 함수:

```javascript
/* 리포트 탭 로직 */

// 주간 트렌드 로드
async function loadWeeklyReport() {
    const data = await api('/api/report/weekly');
    renderCards('weeklySummaryCards', [
        {value: fmt(data.weekly_summary.total_sales), label: '총 판매량'},
        {value: data.weekly_summary.growth_pct + '%', label: '전주 대비',
         type: data.weekly_summary.growth_pct >= 0 ? 'good' : 'warn'},
        {value: fmt(data.weekly_summary.total_orders), label: '총 발주량'},
        {value: data.weekly_summary.total_items, label: '판매 상품수'},
        {value: fmt(data.weekly_summary.total_disuse), label: '폐기 수량',
         type: data.weekly_summary.total_disuse > 0 ? 'warn' : ''},
    ]);
    renderWeeklyTrendChart(data.daily_trend);    // Multi-line
    renderAccuracyChart(data.accuracy);           // Dual-axis
    renderHeatmapTable(data.heatmap);             // CSS 배경색 테이블
    renderTopItemsTable(data.top_items);           // TOP 10
}

// 카테고리 분석 로드
async function loadCategoryReport(midCd) {
    const data = await api('/api/report/category/' + midCd);
    // 개요 카드 + Radar + Doughnut + Sparkline 테이블 + 안전재고 설정 테이블
}

// 서브탭 전환 시 자동 로드 (lazy load, 캐시)
// weekly → loadWeeklyReport() (최초 1회)
// category → select 변경 시 loadCategoryReport()
```

---

## 4. `scripts/run_dashboard.pyw` — 서버 시작

```python
"""Flask 대시보드 서버 시작 + 브라우저 자동 열기"""
import sys, os, webbrowser, threading, socket
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.chdir(str(PROJECT_ROOT / "src"))

from web.app import create_app

def find_free_port(start=8050, end=8060):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return start

def open_browser(port):
    webbrowser.open(f'http://127.0.0.1:{port}')

if __name__ == '__main__':
    app = create_app()
    port = find_free_port()
    threading.Timer(1.5, open_browser, args=[port]).start()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
```

---

## 5. `scripts/install_desktop.py` 수정

기존 바로가기의 target을 `report_launcher.pyw` → `run_dashboard.pyw`로 변경.

---

## 6. 구현 순서 체크리스트

| # | 파일 | 의존성 |
|---|------|--------|
| 0 | `pip install flask` | - |
| 1 | `src/web/__init__.py` | - |
| 2 | `src/web/app.py` | #1 |
| 3 | `src/web/routes/__init__.py` | #2 |
| 4 | `src/web/routes/pages.py` | #3 |
| 5 | `src/web/static/css/dashboard.css` | - |
| 6 | `src/web/templates/index.html` | #5 |
| 7 | `src/web/static/js/app.js` | - |
| 8 | `src/web/routes/api_report.py` | #3 |
| 9 | `src/web/static/js/report.js` | #7, #8 |
| 10 | `src/web/routes/api_order.py` | #3 |
| 11 | `src/web/static/js/order.js` | #7, #10 |
| 12 | `scripts/run_dashboard.pyw` | #2 |
| 13 | `scripts/install_desktop.py` 수정 | #12 |

---

## 7. 검증 기준

- [ ] `python scripts/run_dashboard.pyw` → 브라우저에 대시보드 표시
- [ ] 발주 컨트롤 탭: 파라미터 슬라이더 조정 가능
- [ ] 발주 컨트롤 탭: 예측 실행 → 요약 + 차트 + 테이블 표시
- [ ] 발주 컨트롤 탭: 테이블에서 발주량 인라인 편집 가능
- [ ] 리포트 탭 > 일일: 카테고리 차트 + 안전재고 분포
- [ ] 리포트 탭 > 주간: 7일 추이 + 히트맵 + TOP10
- [ ] 리포트 탭 > 카테고리: 드롭다운 선택 → Radar + Doughnut + Sparkline
- [ ] 리포트 탭 > 영향도: Baseline 저장 + 비교 차트
- [ ] 바탕화면 아이콘 더블클릭 → 서버 시작 + 브라우저 열림
- [ ] 콘솔 창 없이 실행
