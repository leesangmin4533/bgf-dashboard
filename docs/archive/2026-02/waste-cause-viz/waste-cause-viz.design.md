# Design: 폐기 원인 시각화 강화

> **Feature**: waste-cause-viz
> **Plan Reference**: `docs/01-plan/features/waste-cause-viz.plan.md`
> **Created**: 2026-02-25
> **Status**: Draft

---

## 1. 파일 구조

```
bgf_auto/src/web/
├── templates/
│   └── index.html                  # [수정] 서브탭 + 폐기 분석 뷰 HTML 추가
├── static/
│   ├── css/
│   │   └── dashboard.css           # [수정] 워터폴 차트 + 폐기 분석 스타일
│   └── js/
│       └── waste.js                # [신규] 폐기 분석 탭 전체 로직
└── routes/
    └── api_waste.py                # [수정] /api/waste/waterfall 엔드포인트 추가
```

**신규 파일: 1개** (`waste.js`)
**수정 파일: 3개** (`index.html`, `dashboard.css`, `api_waste.py`)

---

## 2. 백엔드 변경

### 2-1. 신규 엔드포인트: `/api/waste/waterfall`

기존 `/api/waste/causes`의 상품별 레코드에서 워터폴 차트용 집계 데이터를 반환합니다.

**api_waste.py 추가:**

```python
@waste_bp.route("/waterfall", methods=["GET"])
def get_waste_waterfall():
    """상품별 발주→판매→폐기 워터폴 데이터

    Query params:
        store_id: 매장 코드 (기본: DEFAULT_STORE_ID)
        days: 조회 기간 (기본: 14)
        limit: 반환할 상품 수 (기본: 10, 폐기량 내림차순)
    """
    store_id = request.args.get("store_id", DEFAULT_STORE_ID)
    days = int(request.args.get("days", 14))
    limit = int(request.args.get("limit", 10))

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    repo = WasteCauseRepository(store_id=store_id)
    causes = repo.get_causes_for_period(start_date, end_date, store_id=store_id)

    # 상품별 집계
    item_agg = {}
    for c in causes:
        key = c["item_cd"]
        if key not in item_agg:
            item_agg[key] = {
                "item_cd": key,
                "item_nm": c.get("item_nm", key),
                "order_qty": 0,
                "sold_qty": 0,
                "waste_qty": 0,
                "primary_cause": c.get("primary_cause", "MIXED"),
            }
        item_agg[key]["order_qty"] += c.get("order_qty") or 0
        item_agg[key]["sold_qty"] += c.get("actual_sold_qty") or 0
        item_agg[key]["waste_qty"] += c.get("waste_qty") or 0

    # 폐기량 내림차순 정렬 → 상위 limit개
    items = sorted(item_agg.values(), key=lambda x: x["waste_qty"], reverse=True)[:limit]

    return jsonify({
        "store_id": store_id,
        "days": days,
        "items": items,
    })
```

**응답 예시:**
```json
{
  "store_id": "46513",
  "days": 14,
  "items": [
    {
      "item_cd": "8801234567890",
      "item_nm": "삼각김밥 참치마요",
      "order_qty": 120,
      "sold_qty": 95,
      "waste_qty": 25,
      "primary_cause": "OVER_ORDER"
    }
  ]
}
```

---

## 3. 프론트엔드 상세 설계

### 3-1. index.html 변경

매출/분석 탭의 서브탭 선택기에 "폐기 분석" 버튼 추가:

```html
<!-- 기존 서브탭 뒤에 추가 -->
<button class="analytics-tab-btn" data-analytics="waste">폐기 분석</button>
```

폐기 분석 뷰 HTML:

```html
<!-- 서브뷰 5: 폐기 분석 -->
<div id="analytics-waste" class="analytics-view">
    <!-- 기간 선택기 -->
    <div class="waste-period-selector">
        <button class="waste-period-btn active" data-days="7">7일</button>
        <button class="waste-period-btn" data-days="14">14일</button>
        <button class="waste-period-btn" data-days="30">30일</button>
    </div>

    <!-- 요약 카드 -->
    <div class="report-summary-cards" id="wasteSummaryCards"></div>

    <!-- 차트 영역: 파이차트 + 워터폴 -->
    <div class="report-charts-grid">
        <!-- 원인별 비율 파이 차트 -->
        <div class="report-chart-card">
            <div class="chart-card-header">
                <h3 class="chart-title">원인별 폐기 비율</h3>
            </div>
            <div class="chart-card-body">
                <canvas id="wasteCausePieChart"></canvas>
            </div>
        </div>

        <!-- 원인별 수량 바 차트 -->
        <div class="report-chart-card">
            <div class="chart-card-header">
                <h3 class="chart-title">원인별 폐기 수량</h3>
            </div>
            <div class="chart-card-body">
                <canvas id="wasteCauseBarChart"></canvas>
            </div>
        </div>
    </div>

    <!-- 워터폴 차트 (full-width) -->
    <div class="report-chart-card full-width">
        <div class="chart-card-header">
            <h3 class="chart-title">상품별 발주 → 판매 → 폐기 흐름</h3>
        </div>
        <div class="chart-card-body">
            <canvas id="wasteWaterfallChart" height="350"></canvas>
        </div>
    </div>

    <!-- 상세 테이블 -->
    <div class="report-table-card">
        <div class="table-card-header">
            <h3 class="chart-title">폐기 상세 내역</h3>
            <input type="text" id="wasteSearch" class="report-search"
                   placeholder="상품명 검색...">
        </div>
        <div class="table-card-body">
            <div class="report-table-wrapper">
                <table class="report-table">
                    <thead>
                        <tr>
                            <th data-sort="text">상품명</th>
                            <th data-sort="text">원인</th>
                            <th data-sort="num" class="text-right">발주량</th>
                            <th data-sort="num" class="text-right">판매량</th>
                            <th data-sort="num" class="text-right">폐기량</th>
                            <th data-sort="num" class="text-right">신뢰도</th>
                            <th data-sort="text">날짜</th>
                        </tr>
                    </thead>
                    <tbody id="wasteTableBody"></tbody>
                </table>
            </div>
        </div>
    </div>
</div>
```

### 3-2. waste.js (신규)

```
파일: src/web/static/js/waste.js
역할: 폐기 분석 서브탭의 전체 렌더링 로직
의존: app.js (api, fmt, getOrCreateChart, getChartColors, renderCards)
```

**주요 함수:**

| 함수명 | 역할 |
|--------|------|
| `loadWasteAnalysis(days)` | 진입점. summary + waterfall + causes API 병렬 호출 |
| `renderWasteSummaryCards(summary)` | 요약 카드 4개 (총 폐기, 과잉발주, 수요감소, 유통관리) |
| `renderWastePieChart(summary)` | Doughnut 차트: by_cause 비율 |
| `renderWasteCauseBarChart(summary)` | Horizontal Bar: 원인별 수량 비교 |
| `renderWaterfallChart(items)` | Stacked Bar: 상품별 판매(green) + 폐기(red) = 발주(전체) |
| `renderWasteTable(causes)` | 상세 내역 테이블 |
| `initWasteTab()` | 이벤트 바인딩 (기간 선택, 검색) |

**원인 라벨/색상 매핑:**

```javascript
var CAUSE_CONFIG = {
    'DEMAND_DROP':          { label: '수요 감소', color: '#f59e0b' },  // amber
    'OVER_ORDER':           { label: '과잉 발주', color: '#ef4444' },  // red
    'EXPIRY_MISMANAGEMENT': { label: '유통기한 관리', color: '#8b5cf6' },  // purple
    'MIXED':                { label: '복합 원인', color: '#6b7280' },  // gray
};
```

**파이 차트 (Doughnut):**

```javascript
function renderWastePieChart(summary) {
    var causes = Object.keys(summary.by_cause);
    var data = causes.map(function(c) { return summary.by_cause[c].total_qty; });
    var colors = causes.map(function(c) { return CAUSE_CONFIG[c].color; });
    var labels = causes.map(function(c) { return CAUSE_CONFIG[c].label; });

    getOrCreateChart('wasteCausePieChart', {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{ data: data, backgroundColor: colors, borderWidth: 0 }]
        },
        options: {
            cutout: '60%',
            plugins: {
                legend: { position: 'bottom', labels: { padding: 16, usePointStyle: true } },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
                            var pct = total > 0 ? Math.round(ctx.raw / total * 100) : 0;
                            return ctx.label + ': ' + fmt(ctx.raw) + '개 (' + pct + '%)';
                        }
                    }
                }
            }
        }
    });
}
```

**워터폴 차트 (Stacked Bar):**

발주량을 전체 높이로, 판매(green)와 폐기(red)를 stacked bar로 표현합니다.

```javascript
function renderWaterfallChart(items) {
    var labels = items.map(function(it) {
        var nm = it.item_nm || it.item_cd;
        return nm.length > 10 ? nm.substring(0, 10) + '...' : nm;
    });
    var soldData = items.map(function(it) { return it.sold_qty; });
    var wasteData = items.map(function(it) { return it.waste_qty; });
    var otherData = items.map(function(it) {
        // 발주 - 판매 - 폐기 = 기타(재고 등)
        return Math.max(0, it.order_qty - it.sold_qty - it.waste_qty);
    });

    getOrCreateChart('wasteWaterfallChart', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '판매',
                    data: soldData,
                    backgroundColor: '#22c55e',  // green
                    borderRadius: 0,
                },
                {
                    label: '폐기',
                    data: wasteData,
                    backgroundColor: '#ef4444',  // red
                    borderRadius: 0,
                },
                {
                    label: '잔여재고',
                    data: otherData,
                    backgroundColor: '#6b728040',  // gray translucent
                    borderRadius: { topLeft: 4, topRight: 4 },
                }
            ]
        },
        options: {
            indexAxis: 'y',
            scales: {
                x: { stacked: true, grid: { color: getChartColors().grid } },
                y: { stacked: true, grid: { display: false } }
            },
            plugins: {
                legend: { position: 'top' },
                tooltip: {
                    callbacks: {
                        afterBody: function(ctx) {
                            var idx = ctx[0].dataIndex;
                            var item = items[idx];
                            return '발주 합계: ' + fmt(item.order_qty) + '개';
                        }
                    }
                }
            }
        }
    });
}
```

### 3-3. dashboard.css 추가 스타일

```css
/* 폐기 분석 기간 선택기 */
.waste-period-selector {
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
}

.waste-period-btn {
    padding: 6px 16px;
    border-radius: var(--radius-full);
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 0.85rem;
    transition: all var(--transition-fast);
}

.waste-period-btn.active {
    background: var(--primary);
    color: white;
    border-color: var(--primary);
}
```

### 3-4. index.html script 태그

```html
<script src="{{ url_for('static', filename='js/waste.js') }}?v=1"></script>
```

기존 탭 전환 로직(`app.js`의 analytics 서브탭 핸들러)에서 `data-analytics="waste"` 선택 시 `loadWasteAnalysis(7)` 호출.

---

## 4. 구현 순서

| # | 작업 | 파일 |
|---|------|------|
| 1 | `/api/waste/waterfall` 엔드포인트 추가 | `api_waste.py` |
| 2 | index.html에 서브탭 버튼 + 뷰 HTML 추가 | `index.html` |
| 3 | waste.js 신규 작성 (파이+워터폴+테이블) | `waste.js` |
| 4 | dashboard.css에 기간 선택기 스타일 추가 | `dashboard.css` |
| 5 | app.js 탭 전환에 waste 분기 추가 | `app.js` |

---

## 5. 데이터 흐름

```
[사용자: 폐기 분석 탭 클릭]
        │
        ▼
loadWasteAnalysis(days=7)
        │
        ├─ GET /api/waste/summary?days=7
        │        → renderWasteSummaryCards()
        │        → renderWastePieChart()
        │        → renderWasteCauseBarChart()
        │
        ├─ GET /api/waste/waterfall?days=7&limit=10
        │        → renderWaterfallChart()
        │
        └─ GET /api/waste/causes?days=7
                 → renderWasteTable()
```

---

## 6. 테스트 계획

| # | 테스트 대상 | 방법 |
|---|------------|------|
| 1 | `/api/waste/waterfall` 응답 형식 | pytest (unit) |
| 2 | 빈 데이터 시 차트 빈 상태 표시 | 수동 확인 |
| 3 | 기간 전환 (7/14/30일) | 수동 확인 |
| 4 | 다크/라이트 테마 차트 색상 | 수동 확인 |
| 5 | 상품명 검색 필터 | 수동 확인 |
