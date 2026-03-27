# Design: prediction-accuracy-fix (예측정확도 지표 개선)

## 1. 수정 파일 목록

| # | 파일 | 변경 유형 | 내용 |
|---|------|----------|------|
| 1 | `src/prediction/accuracy/tracker.py` | 수정 | AccuracyMetrics에 smape/wmape 추가, SQL MAPE 통일 |
| 2 | `src/web/routes/api_prediction.py` | 수정 | API 응답에 smape/wmape 포함 |
| 3 | `src/web/static/js/prediction.js` | 수정 | UI 표시 개선 (MAE 메인, SMAPE 보조) |
| 4 | `src/prediction/eval_calibrator.py` | 수정 | NORMAL_ORDER 리드타임 유예 (선택) |
| 5 | `tests/test_accuracy_tracker.py` | 신규 | AccuracyTracker 테스트 |

## 2. tracker.py 변경 상세

### 2-1. AccuracyMetrics 필드 추가

```python
@dataclass
class AccuracyMetrics:
    """정확도 지표"""
    period_start: str
    period_end: str
    total_predictions: int

    # 오차 지표
    mape: float                    # MAPE (%) - actual>0 기준
    mae: float                     # MAE (개수)
    rmse: float                    # RMSE
    smape: float                   # SMAPE (%) - 대칭 MAPE [신규]
    wmape: float                   # wMAPE (%) - 가중 MAPE [신규]

    # 적중률 (기존 유지)
    accuracy_exact: float
    accuracy_within_1: float
    accuracy_within_2: float
    accuracy_within_3: float

    # 편향 분석 (기존 유지)
    over_prediction_rate: float
    under_prediction_rate: float
    avg_over_amount: float
    avg_under_amount: float
```

**핵심**: `smape`와 `wmape` 2개 필드 추가. 기존 `mape` 필드는 하위 호환을 위해 유지.

### 2-2. calculate_metrics() SMAPE/wMAPE 계산 추가

`calculate_metrics()` 메서드의 for 루프에 SMAPE 계산 로직 추가:

```python
# --- 기존 변수에 추가할 새 변수 ---
smape_errors = []         # SMAPE 개별 값
sum_abs_errors = 0        # wMAPE 분자: SUM(|pred - actual|)
sum_actuals = 0           # wMAPE 분모: SUM(actual)

# --- for 루프 내부에 추가 ---
for m in matched:
    pred = m["predicted"]
    actual = m["actual"]
    error = pred - actual
    abs_error = abs(error)

    # ... 기존 코드 유지 ...

    # SMAPE 계산 (pred와 actual 둘 다 0이면 SMAPE=0)
    denom = (abs(pred) + abs(actual)) / 2.0
    if denom > 0:
        smape_errors.append(abs_error / denom * 100)
    else:
        smape_errors.append(0.0)  # 둘 다 0 → 완벽히 맞음

    # wMAPE 누적
    sum_abs_errors += abs_error
    sum_actuals += actual

# --- 계산 부분에 추가 ---
smape = sum(smape_errors) / len(smape_errors) if smape_errors else 0
wmape = (sum_abs_errors / sum_actuals * 100) if sum_actuals > 0 else 0
```

**SMAPE 공식**: `|pred - actual| / ((|pred| + |actual|) / 2) * 100`
- actual=0, pred=0 → SMAPE=0% (둘 다 0이면 정확)
- actual=0, pred=1 → SMAPE=200% (명확한 과대)
- actual=1, pred=2 → SMAPE=66.7% (MAPE 100%보다 합리적)

**wMAPE 공식**: `SUM(|pred - actual|) / SUM(actual) * 100`
- 대량 판매 상품에 더 큰 가중치 → 소량 상품의 극단 MAPE 왜곡 해소

### 2-3. AccuracyMetrics 반환에 smape/wmape 포함

```python
return AccuracyMetrics(
    period_start=period_start,
    period_end=period_end,
    total_predictions=total,
    mape=round(mape, 2),
    mae=round(mae, 2),
    rmse=round(rmse, 2),
    smape=round(smape, 2),        # 신규
    wmape=round(wmape, 2),        # 신규
    accuracy_exact=round(exact_count / total * 100, 1),
    accuracy_within_1=round(within_1 / total * 100, 1),
    accuracy_within_2=round(within_2 / total * 100, 1),
    accuracy_within_3=round(within_3 / total * 100, 1),
    over_prediction_rate=round(over_rate, 1),
    under_prediction_rate=round(under_rate, 1),
    avg_over_amount=round(avg_over, 2),
    avg_under_amount=round(avg_under, 2)
)
```

### 2-4. _empty_metrics() 수정

```python
def _empty_metrics(self) -> AccuracyMetrics:
    return AccuracyMetrics(
        period_start="", period_end="",
        total_predictions=0,
        mape=0, mae=0, rmse=0,
        smape=0, wmape=0,             # 신규
        accuracy_exact=0, accuracy_within_1=0,
        accuracy_within_2=0, accuracy_within_3=0,
        over_prediction_rate=0, under_prediction_rate=0,
        avg_over_amount=0, avg_under_amount=0
    )
```

### 2-5. get_daily_mape_trend() SQL 버그 수정 + SMAPE 추가

**현재 (버그)**: actual_qty=0일 때 `ELSE 0`으로 MAPE=0 포함 → 97.9%가 0이라 MAPE가 1.6%

**수정**: actual_qty>0인 행만 MAPE 계산, SMAPE도 함께 반환

```python
def get_daily_mape_trend(self, days: int = 14) -> List[Dict[str, Any]]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    conn = self._get_connection()
    cursor = conn.cursor()

    store_filter = "AND store_id = ?" if self.store_id else ""
    store_params = (self.store_id,) if self.store_id else ()
    cursor.execute(f"""
        SELECT
            prediction_date,
            AVG(CASE WHEN actual_qty > 0
                THEN ABS(predicted_qty - actual_qty) * 100.0 / actual_qty
                END) as mape,
            AVG(CASE
                WHEN (ABS(predicted_qty) + ABS(actual_qty)) > 0
                THEN ABS(predicted_qty - actual_qty) * 200.0
                     / (ABS(predicted_qty) + ABS(actual_qty))
                ELSE 0 END) as smape,
            COUNT(*) as total_count,
            SUM(CASE WHEN actual_qty > 0 THEN 1 ELSE 0 END) as sold_count
        FROM prediction_logs
        WHERE prediction_date >= ?
        AND prediction_date <= ?
        AND actual_qty IS NOT NULL
        {store_filter}
        GROUP BY prediction_date
        ORDER BY prediction_date
    """, (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")) + store_params)

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "date": date,
            "mape": round(mape, 1) if mape is not None else 0,
            "smape": round(smape, 1) if smape is not None else 0,
            "count": total_count,
            "sold_count": sold_count or 0,
        }
        for date, mape, smape, total_count, sold_count in rows
    ]
```

**핵심 변경**:
- `ELSE 0 END` → `END` (actual_qty=0인 행을 AVG에서 **제외**, NULL로 처리)
- SMAPE SQL 계산 추가
- `sold_count` 추가 (실제 판매 있는 건수 → UI에서 "전체 N건 중 판매 M건" 표시 가능)

### 2-6. get_worst_items() / get_best_items() SQL 통일

두 메서드 모두 동일한 변경:

```sql
-- 변경 전 (버그)
AVG(CASE WHEN pl.actual_qty > 0
    THEN ABS(pl.predicted_qty - pl.actual_qty) * 100.0 / pl.actual_qty
    ELSE 0 END) as mape,

-- 변경 후 (actual=0 제외)
AVG(CASE WHEN pl.actual_qty > 0
    THEN ABS(pl.predicted_qty - pl.actual_qty) * 100.0 / pl.actual_qty
    END) as mape,
```

추가로 SMAPE 컬럼도 반환:

```sql
AVG(CASE
    WHEN (ABS(pl.predicted_qty) + ABS(pl.actual_qty)) > 0
    THEN ABS(pl.predicted_qty - pl.actual_qty) * 200.0
         / (ABS(pl.predicted_qty) + ABS(pl.actual_qty))
    ELSE 0 END) as smape,
```

반환 dict에 `smape` 키 추가:

```python
results.append({
    "item_cd": item_cd,
    "item_nm": item_nm,
    "mid_cd": mid_cd,
    "mape": round(mape, 1) if mape is not None else 0,
    "smape": round(smape, 1),    # 신규
    "mae": round(mae, 2),
    "bias": round(bias, 2),
    "sample_count": count
})
```

## 3. api_prediction.py 변경 상세

### 3-1. _get_qty_accuracy() smape/wmape 추가

```python
def _get_qty_accuracy(store_id=None):
    try:
        from src.prediction.accuracy.tracker import AccuracyTracker
        db_path = _get_store_db_path(store_id)
        tracker = AccuracyTracker(db_path=db_path, store_id=store_id)
        metrics = tracker.get_accuracy_by_period(days=7)

        return {
            "total": metrics.total_predictions,
            "mape": round(metrics.mape, 1),
            "mae": round(metrics.mae, 2),
            "smape": round(metrics.smape, 1),        # 신규
            "wmape": round(metrics.wmape, 1),         # 신규
            "accuracy_within_2": round(metrics.accuracy_within_2, 1),
            "over_rate": round(metrics.over_prediction_rate, 1),
            "under_rate": round(metrics.under_prediction_rate, 1),
        }
    except Exception as e:
        logger.warning(f"예측 정확도 조회 실패: {e}")
        return {
            "total": 0, "mape": 0, "mae": 0,
            "smape": 0, "wmape": 0,                   # 신규
            "accuracy_within_2": 0, "over_rate": 0, "under_rate": 0,
        }
```

### 3-2. accuracy_detail() category_accuracy에 smape 추가

```python
data["category_accuracy"] = [
    {
        "mid_cd": c.mid_cd,
        "mid_nm": c.mid_nm,
        "mape": round(c.metrics.mape, 1),
        "smape": round(c.metrics.smape, 1),     # 신규
        "mae": round(c.metrics.mae, 2),
        "accuracy_within_2": round(c.metrics.accuracy_within_2, 1),
        "total": c.metrics.total_predictions,
    }
    for c in cat_list
]
```

### 3-3. accuracy_detail() daily_mape_trend 이미 tracker에서 smape 반환

`tracker.get_daily_mape_trend(14)` 반환값에 이미 `smape` 포함 → 별도 수정 불필요.

## 4. prediction.js 변경 상세

### 4-1. renderPredQtyAccuracy() - 요약 카드 표시 변경

**현재**: 메인=accuracy_within_2, 서브=MAPE
**변경**: 메인=accuracy_within_2 (유지), 서브=MAE + SMAPE

```javascript
function renderPredQtyAccuracy(acc) {
    var val = document.getElementById('predQtyAccuracyValue');
    var sub = document.getElementById('predQtyAccuracySub');
    if (!val || !acc) return;

    if (acc.total === 0) {
        val.textContent = '-';
        sub.textContent = '예측 데이터 없음';
        return;
    }
    val.textContent = acc.accuracy_within_2 + '%';
    // 변경: MAPE → MAE + SMAPE
    sub.textContent = 'MAE ' + acc.mae + '개 | SMAPE ' + (acc.smape || 0) + '% | ' + fmt(acc.total) + '건';
}
```

### 4-2. renderMapeChart() - 차트에 SMAPE 라인 추가

```javascript
function renderMapeChart(trend) {
    var canvas = document.getElementById('predMapeChart');
    if (!canvas || !trend || trend.length === 0) return;

    var labels = trend.map(function(t) { return t.date ? t.date.substring(5) : ''; });
    var mapeData = trend.map(function(t) { return t.mape || 0; });
    var smapeData = trend.map(function(t) { return t.smape || 0; });
    var countData = trend.map(function(t) { return t.count || 0; });

    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    getOrCreateChart('predMapeChart', {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'SMAPE (%)',
                    data: smapeData,
                    borderColor: getChartColors().blue,
                    backgroundColor: getChartColors().blueA.replace('0.7', '0.1'),
                    fill: true,
                    tension: 0.3,
                    yAxisID: 'y',
                },
                {
                    label: 'MAPE (%)',
                    data: mapeData,
                    borderColor: getChartColors().orange || getChartColors().amber || '#f59e0b',
                    backgroundColor: 'transparent',
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y',
                },
                {
                    label: '건수',
                    data: countData,
                    borderColor: getChartColors().slate,
                    backgroundColor: getChartColors().slateA.replace('0.7', '0.1'),
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: {
                    type: 'linear', position: 'left',
                    title: { display: true, text: 'SMAPE / MAPE (%)' },
                    grid: { color: gridColor },
                },
                y1: {
                    type: 'linear', position: 'right',
                    title: { display: true, text: '건수' },
                    grid: { drawOnChartArea: false },
                },
            },
            plugins: { legend: { position: 'top' } },
        },
    });
}
```

**핵심 변경**:
- SMAPE를 메인(실선/fill), MAPE를 보조(점선) 으로 표시
- Y축 레이블 "SMAPE / MAPE (%)" 으로 변경

### 4-3. renderCategoryAccuracyTable() - SMAPE 컬럼 추가

```javascript
function renderCategoryAccuracyTable(cats) {
    var tbody = document.getElementById('predCategoryTableBody');
    if (!tbody) return;

    if (!cats || cats.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="padding:24px;color:var(--text-muted)">데이터 없음</td></tr>';
        return;
    }

    tbody.innerHTML = cats.map(function(c) {
        var smapeClass = (c.smape || 0) > 100 ? 'color:var(--danger)' : (c.smape || 0) > 60 ? 'color:var(--warning)' : '';
        return '<tr>'
            + '<td data-label="카테고리">' + esc(c.mid_nm || c.mid_cd) + '</td>'
            + '<td data-label="SMAPE" class="text-right" style="' + smapeClass + '">' + (c.smape || 0) + '%</td>'
            + '<td data-label="MAPE" class="text-right">' + c.mape + '%</td>'
            + '<td data-label="MAE" class="text-right">' + c.mae + '</td>'
            + '<td data-label="정확도" class="text-right">' + c.accuracy_within_2 + '%</td>'
            + '<td data-label="건수" class="text-right">' + fmt(c.total) + '</td>'
            + '</tr>';
    }).join('');

    initTableSort('predCategoryTable');
    initPagination('predCategoryTable', 25);
}
```

**핵심 변경**:
- SMAPE 컬럼 추가 (MAPE 앞에 배치)
- 색상 기준: SMAPE>100% 빨강, >60% 경고
- colspan 5→6

> **HTML 헤더 변경 필요**: `predCategoryTable` 테이블 헤더에 SMAPE 컬럼 추가 (dashboard.html 내)

### 4-4. renderBestWorst() - SMAPE 추가 표시

```javascript
// Worst 테이블 헤더
'<th>상품명</th><th class="text-right">SMAPE</th><th class="text-right">MAE</th><th class="text-right">편향</th><th class="text-right">건수</th>'

// Worst 행 렌더링
worst.forEach(function(w) {
    html += '<tr>'
        + '<td>' + esc(w.item_nm || w.item_cd) + '</td>'
        + '<td class="text-right" style="color:var(--danger)">' + (w.smape != null ? Math.round(w.smape) + '%' : '-') + '</td>'
        + '<td class="text-right">' + (w.mae != null ? w.mae.toFixed(1) : '-') + '</td>'
        + '<td class="text-right">' + (w.bias != null ? (w.bias > 0 ? '+' : '') + w.bias.toFixed(1) : '-') + '</td>'
        + '<td class="text-right">' + (w.sample_count || 0) + '</td>'
        + '</tr>';
});

// Best 테이블도 동일 구조 (색상만 --success)
```

**핵심 변경**: MAPE 컬럼 → SMAPE 컬럼으로 교체, MAE 컬럼 추가

## 5. eval_calibrator.py 변경 상세 (선택)

### 5-1. NORMAL_ORDER 판정 개선

**현재** (line 407-413):
```python
elif decision == "NORMAL_ORDER":
    if actual_sold > 0:
        return "CORRECT"
    if was_stockout:
        return "UNDER_ORDER"
    return "OVER_ORDER"
```

**변경**: 리드타임 기반 유예 → PENDING 판정 지원

```python
elif decision == "NORMAL_ORDER":
    if actual_sold > 0:
        return "CORRECT"
    if was_stockout:
        return "UNDER_ORDER"
    # 리드타임 내 판매 가능성: 발주 후 2일 이내면 PENDING
    days_since_order = record.get("days_since_order", 1)
    if days_since_order <= 1:
        return "PENDING"  # 아직 판매 시간 부족 → 보류
    return "OVER_ORDER"
```

**주의**:
- `PENDING` outcome이 추가되므로, `_get_eval_accuracy()` SQL에서 `outcome IS NOT NULL AND outcome != 'PENDING'`으로 필터해야 정확도 계산에서 제외
- 기존 `outcome IS NOT NULL` 필터는 유지하되, PENDING은 accuracy 통계에서 제외
- 이 수정은 **우선순위 낮음** - 수정 1~4 완료 후 별도 판단

> **구현 시 주의**: `days_since_order` 값이 record에 포함되려면 `_backfill_outcomes()`에서 날짜 차이를 계산해 전달해야 함. 현재 `_judge_outcome()` 호출부를 확인하여 해당 값이 전달 가능한지 검증 필요.

## 6. dashboard.html 변경 (추가 발견)

카테고리 테이블 헤더에 SMAPE 컬럼 추가가 필요합니다.

**파일**: `src/web/templates/dashboard.html` (예측정확도 상세 섹션)

기존 테이블 헤더:
```html
<th>카테고리</th><th>MAPE</th><th>MAE</th><th>정확도</th><th>건수</th>
```

변경:
```html
<th>카테고리</th><th>SMAPE</th><th>MAPE</th><th>MAE</th><th>정확도</th><th>건수</th>
```

## 7. 테스트 계획

### 7-1. 테스트 파일: `tests/test_accuracy_tracker.py`

```python
"""AccuracyTracker SMAPE/wMAPE 및 MAPE 통일 테스트"""

# 테스트 케이스 목록:

# --- SMAPE 계산 정확성 ---
# 1. test_smape_both_zero: actual=0, pred=0 → SMAPE=0
# 2. test_smape_actual_zero_pred_one: actual=0, pred=1 → SMAPE=200
# 3. test_smape_one_to_two: actual=1, pred=2 → SMAPE=66.7
# 4. test_smape_symmetric: |pred-actual| 동일하면 방향 무관 SMAPE 동일
# 5. test_smape_perfect: actual=pred → SMAPE=0

# --- wMAPE 계산 정확성 ---
# 6. test_wmape_basic: 간단한 케이스 계산 검증
# 7. test_wmape_heavy_weight: 대량판매 상품이 가중치 지배 확인
# 8. test_wmape_all_zero_actual: SUM(actual)=0일 때 wMAPE=0

# --- MAPE 통일 검증 ---
# 9. test_mape_excludes_zero_actual: actual=0인 건 MAPE 계산에서 제외
# 10. test_daily_mape_trend_excludes_zero: SQL에서 actual=0 제외 확인
# 11. test_worst_items_excludes_zero: worst SQL에서 actual=0 제외

# --- API 응답 검증 ---
# 12. test_qty_accuracy_has_smape_wmape: smape, wmape 키 존재
# 13. test_accuracy_detail_has_smape: daily_mape_trend에 smape 존재
# 14. test_category_accuracy_has_smape: category_accuracy에 smape 존재

# --- 경계 케이스 ---
# 15. test_empty_predictions: 빈 데이터 → 모든 지표 0
# 16. test_all_actual_zero: 전부 actual=0 → MAPE=0, SMAPE 계산됨
# 17. test_mixed_zero_nonzero: 혼합 데이터 정확성
```

### 7-2. 테스트 데이터 패턴

```python
# 편의점 전형적 데이터 (actual=0이 대다수)
TYPICAL_DATA = [
    {"predicted": 0, "actual": 0},   # 무판매 정확
    {"predicted": 1, "actual": 0},   # 과대 (가장 흔한 오류)
    {"predicted": 0, "actual": 0},   # 무판매 정확
    {"predicted": 0, "actual": 0},   # 무판매 정확
    {"predicted": 1, "actual": 1},   # 정확
    {"predicted": 0, "actual": 0},   # 무판매 정확
    {"predicted": 2, "actual": 1},   # 과대
    {"predicted": 0, "actual": 0},   # 무판매 정확
    {"predicted": 0, "actual": 1},   # 과소
    {"predicted": 0, "actual": 0},   # 무판매 정확
]
# 이 데이터에서:
# actual>0: 3건 → MAPE = (0% + 100% + 100%) / 3 = 66.7%
# SMAPE 전체 10건 평균: (0+200+0+0+0+0+66.7+0+200+0)/10 = 46.7%
# wMAPE: SUM(|err|)=4 / SUM(actual)=2 = 200%
```

## 8. 구현 순서

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 1 | AccuracyMetrics smape/wmape 필드 추가 | tracker.py | 없음 |
| 2 | calculate_metrics() SMAPE/wMAPE 계산 | tracker.py | 1 |
| 3 | _empty_metrics() 수정 | tracker.py | 1 |
| 4 | get_daily_mape_trend() SQL 버그 수정 | tracker.py | 없음 |
| 5 | get_worst/best_items() SQL 통일 | tracker.py | 없음 |
| 6 | 테스트 작성 및 실행 | tests/ | 1-5 |
| 7 | API 응답 수정 | api_prediction.py | 1-5 |
| 8 | 프론트엔드 UI 수정 | prediction.js | 7 |
| 9 | HTML 테이블 헤더 수정 | dashboard.html | 8 |
| 10 | (선택) NORMAL_ORDER 판정 | eval_calibrator.py | 별도 |

## 9. 예상 결과

| 지표 | 변경 전 | 변경 후 (예상) |
|------|---------|--------------|
| 요약 카드 MAPE | 149% | 유지 (하위호환) |
| 일별 차트 MAPE | 1.6% | 실판매 기준 ~149% |
| 요약 카드 서브텍스트 | "MAPE 149%" | "MAE 0.36개 \| SMAPE 42%" |
| 일별 차트 메인 라인 | MAPE | SMAPE |
| Worst/Best 기준 | MAPE(0포함) | SMAPE + MAE |

## 10. 하위 호환성

- `AccuracyMetrics.mape` 필드 유지 → 기존 참조 코드 영향 없음
- API 응답에 `smape`, `wmape` **추가** (기존 필드 삭제 없음)
- 프론트엔드만 표시 우선순위 변경 (MAPE→SMAPE)
- eval_outcomes 테이블 스키마 변경 없음
