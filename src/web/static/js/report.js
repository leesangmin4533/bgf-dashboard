/* BGF 발주 시스템 - 리포트 탭 */

// === 차트 색상 팔레트 (CSS 변수에서 런타임 로드) ===
function getCOLORS() { return getChartPalette(); }

// 로드 상태 (중복 방지)
window._weeklyLoaded = false;
var _categoryCache = {};

// ========================================
// 일일 발주 리포트
// ========================================
async function loadDailyReport() {
    try {
        var data = await api('/api/report/daily?store_id=' + _currentStoreId);
        if (data.error) return;

        // 요약 카드
        renderCards('dailySummaryCards', [
            { value: fmt(data.summary.total_items), label: '총 상품수' },
            { value: fmt(data.summary.ordered_count), label: '발주 대상', type: 'good' },
            { value: fmt(data.summary.skipped_count), label: '스킵', type: 'warn' },
            { value: fmt(data.summary.total_order_qty), label: '총 발주량', type: 'accent' },
            { value: data.summary.category_count, label: '카테고리수' },
            { value: data.summary.avg_safety_stock, label: '평균 안전재고' },
        ]);

        // 카테고리 바 차트
        getOrCreateChart('dailyCategoryChart', {
            type: 'bar',
            data: {
                labels: data.category_data.labels,
                datasets: [{
                    label: '발주량',
                    data: data.category_data.order_qty,
                    backgroundColor: getChartColors().blueA,
                    borderRadius: 4,
                }]
            },
            options: {
                indexAxis: data.category_data.labels.length > 10 ? 'y' : 'x',
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: getChartColors().grid } },
                    y: { grid: { color: getChartColors().grid } }
                }
            }
        });

        // 안전재고 히스토그램
        getOrCreateChart('dailySafetyHist', {
            type: 'bar',
            data: {
                labels: data.safety_dist.labels,
                datasets: [{
                    label: '상품 수',
                    data: data.safety_dist.counts,
                    backgroundColor: getChartColors().green,
                    borderRadius: 4,
                }]
            },
            options: {
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: getChartColors().grid } },
                    y: { grid: { color: getChartColors().grid }, beginAtZero: true }
                }
            }
        });

        // 상세 테이블
        var tbody = document.getElementById('dailyTableBody');
        tbody.innerHTML = data.items.map(function(it) {
            return '<tr>' +
                '<td data-label="상품명">' + it.item_nm + '</td>' +
                '<td data-label="카테고리">' + it.category + '</td>' +
                '<td data-label="일평균" class="text-right">' + it.daily_avg + '</td>' +
                '<td data-label="안전재고" class="text-right">' + it.safety_stock + '</td>' +
                '<td data-label="현재고" class="text-right">' + fmt(it.current_stock) + '</td>' +
                '<td data-label="발주량" class="text-right">' + fmt(it.order_qty) + '</td>' +
                '<td data-label="신뢰도" class="text-center">' + badgeHtml(it.confidence) + '</td>' +
            '</tr>';
        }).join('');
        initTableSearch('dailySearch', 'dailyTable');
        initTableSort('dailyTable');
        initPagination('dailyTable', 25);
    } catch (e) {
        console.error('일일 리포트 로드 실패:', e);
    }
}

// ========================================
// 주간 트렌드 리포트
// ========================================
async function loadWeeklyReport() {
    if (window._weeklyLoaded) return;
    try {
        var data = await api('/api/report/weekly?store_id=' + _currentStoreId);
        if (data.error) return;
        window._weeklyLoaded = true;

        // 요약 카드
        var ws = data.weekly_summary;
        renderCards('weeklySummaryCards', [
            { value: fmt(ws.total_sales), label: '총 판매량' },
            { value: ws.growth_pct + '%', label: '전주 대비', type: ws.growth_pct >= 0 ? 'good' : 'warn' },
            { value: fmt(ws.total_orders), label: '총 발주량' },
            { value: ws.total_items, label: '판매 상품수' },
            { value: fmt(ws.total_disuse), label: '폐기 수량', type: ws.total_disuse > 0 ? 'warn' : '' },
        ]);

        // 판매 추이 멀티라인
        getOrCreateChart('weeklyTrendChart', {
            type: 'line',
            data: {
                labels: data.daily_trend.labels,
                datasets: data.daily_trend.datasets
            },
            options: {
                plugins: { legend: { position: 'top' } },
                scales: {
                    x: { grid: { color: getChartColors().grid } },
                    y: { grid: { color: getChartColors().grid }, beginAtZero: true }
                }
            }
        });

        // 정확도 듀얼 축
        if (data.accuracy.dates.length > 0) {
            var cc = getChartColors();
            getOrCreateChart('weeklyAccuracyChart', {
                type: 'line',
                data: {
                    labels: data.accuracy.dates,
                    datasets: [
                        {
                            label: 'MAPE (%)',
                            data: data.accuracy.mape_values,
                            borderColor: cc.red,
                            backgroundColor: 'transparent',
                            yAxisID: 'y1',
                            tension: 0.3,
                        },
                        {
                            label: '정확도 (%)',
                            data: data.accuracy.accuracy_values,
                            borderColor: cc.green,
                            backgroundColor: 'transparent',
                            yAxisID: 'y2',
                            tension: 0.3,
                        }
                    ]
                },
                options: {
                    scales: {
                        y1: { type: 'linear', position: 'left', grid: { color: cc.grid },
                              title: { display: true, text: 'MAPE (%)' } },
                        y2: { type: 'linear', position: 'right', grid: { drawOnChartArea: false },
                              title: { display: true, text: '정확도 (%)' }, min: 0, max: 100 },
                        x: { grid: { color: cc.grid } }
                    }
                }
            });
        }

        // 요일 히트맵 테이블
        renderHeatmapTable(data.heatmap);

        // TOP 10 테이블
        renderTopItemsTable(data.top_items);
    } catch (e) {
        console.error('주간 리포트 로드 실패:', e);
    }
}

function renderHeatmapTable(heatmap) {
    var el = document.getElementById('weeklyHeatmap');
    var maxVal = 0;
    heatmap.data.forEach(function(row) {
        row.forEach(function(v) { if (v > maxVal) maxVal = v; });
    });

    // CSS 변수에서 히트맵 색상 토큰 읽기
    var s = getComputedStyle(document.documentElement);
    var baseR = parseInt(s.getPropertyValue('--heatmap-base-r')) || 60;
    var baseG = parseInt(s.getPropertyValue('--heatmap-base-g')) || 80;
    var baseB = parseInt(s.getPropertyValue('--heatmap-base-b')) || 160;
    var rangeR = parseInt(s.getPropertyValue('--heatmap-range-r')) || 52;
    var rangeG = parseInt(s.getPropertyValue('--heatmap-range-g')) || 64;
    var rangeB = parseInt(s.getPropertyValue('--heatmap-range-b')) || 95;
    var alphaMin = parseFloat(s.getPropertyValue('--heatmap-alpha-min')) || 0.15;
    var alphaRange = parseFloat(s.getPropertyValue('--heatmap-alpha-range')) || 0.45;

    var html = '<table><thead><tr><th>카테고리</th>';
    heatmap.weekdays.forEach(function(d) { html += '<th class="text-center">' + d + '</th>'; });
    html += '</tr></thead><tbody>';
    for (var i = 0; i < heatmap.categories.length; i++) {
        html += '<tr><td>' + heatmap.categories[i] + '</td>';
        for (var j = 0; j < heatmap.data[i].length; j++) {
            var val = heatmap.data[i][j];
            var intensity = maxVal > 0 ? val / maxVal : 0;
            var r = Math.round(baseR + intensity * rangeR);
            var g = Math.round(baseG + intensity * rangeG);
            var b = Math.round(baseB + intensity * rangeB);
            var bg = 'rgba(' + r + ',' + g + ',' + b + ',' + (alphaMin + intensity * alphaRange) + ')';
            html += '<td class="heatmap-cell" style="background:' + bg + '">' + val + '</td>';
        }
        html += '</tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;
}

function renderTopItemsTable(topItems) {
    var el = document.getElementById('weeklyTopItems');
    var sellers = topItems.top_sellers;
    var html = '<table><thead><tr><th>#</th><th>상품명</th><th>카테고리</th>' +
               '<th class="text-right">판매량</th></tr></thead><tbody>';
    for (var i = 0; i < sellers.length; i++) {
        var s = sellers[i];
        html += '<tr><td>' + (i + 1) + '</td><td>' + s.item_nm + '</td>' +
                '<td>' + s.category + '</td><td class="text-right">' + fmt(s.total_sales) + '</td></tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;
}

// ========================================
// 카테고리 분석 리포트
// ========================================
async function loadCategoryList() {
    var data = await api('/api/order/categories?store_id=' + _currentStoreId);
    var select = document.getElementById('categorySelect');
    select.innerHTML = '<option value="">선택하세요</option>';
    data.categories.forEach(function(c) {
        select.innerHTML += '<option value="' + c.code + '">' + c.code + ' - ' + c.name + '</option>';
    });
}

async function loadCategoryReport(midCd) {
    if (!midCd) return;
    try {
        var data = await api('/api/report/category/' + midCd + '?store_id=' + _currentStoreId);
        if (data.error) return;
        document.getElementById('categoryContent').style.display = '';

        // 개요 카드
        var ov = data.overview;
        renderCards('catSummaryCards', [
            { value: ov.item_count, label: '상품수' },
            { value: fmt(ov.total_sales), label: '총 판매량' },
            { value: ov.data_days + '일', label: '데이터 기간' },
            { value: ov.daily_avg, label: '일평균', type: 'accent' },
            { value: ov.avg_per_item, label: '상품당 평균' },
        ]);

        // Radar 차트 (요일 계수)
        var cc = getChartColors();
        getOrCreateChart('catRadarChart', {
            type: 'radar',
            data: {
                labels: data.weekday_coefs.labels,
                datasets: [
                    {
                        label: '설정값',
                        data: data.weekday_coefs.config,
                        borderColor: cc.blueA,
                        backgroundColor: cc.blueA.replace('0.7', '0.12'),
                        pointBackgroundColor: cc.blueA,
                    },
                    {
                        label: '기본값',
                        data: data.weekday_coefs.default,
                        borderColor: cc.slate,
                        backgroundColor: cc.slateA.replace('0.7', '0.08'),
                        pointBackgroundColor: cc.slate,
                        borderDash: [5, 5],
                    }
                ]
            },
            options: {
                scales: {
                    r: {
                        grid: { color: cc.grid },
                        angleLines: { color: cc.grid },
                        suggestedMin: 0,
                    }
                }
            }
        });

        // Doughnut 차트 (회전율)
        getOrCreateChart('catDoughnutChart', {
            type: 'doughnut',
            data: {
                labels: data.turnover_dist.labels,
                datasets: [{
                    data: data.turnover_dist.counts,
                    backgroundColor: [cc.green, cc.yellow, cc.red],
                }]
            },
            options: {
                plugins: { legend: { position: 'bottom' } }
            }
        });

        // 안전재고 설정 테이블
        renderSafetyConfigTable(data.safety_config);

        // Sparkline 테이블
        renderSparklineTable(data.sparklines);
    } catch (e) {
        console.error('카테고리 리포트 로드 실패:', e);
    }
}

function renderSafetyConfigTable(config) {
    var el = document.getElementById('catSafetyTable');
    var html = '<table style="font-size:0.85em"><thead><tr><th>유통기한 그룹</th><th>범위</th><th class="text-right">안전재고 일수</th></tr></thead><tbody>';
    config.shelf_life.forEach(function(s) {
        html += '<tr><td>' + s.group + '</td><td>' + s.range + '일</td><td class="text-right">' + s.safety_days + '</td></tr>';
    });
    html += '</tbody></table>';
    html += '<table style="font-size:0.85em;margin-top:12px"><thead><tr><th>회전율</th><th class="text-right">최소 일평균</th><th class="text-right">배수</th></tr></thead><tbody>';
    config.turnover.forEach(function(t) {
        html += '<tr><td>' + t.level + '</td><td class="text-right">' + t.min_daily + '</td><td class="text-right">' + t.multiplier + '</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

function renderSparklineTable(sparklines) {
    var el = document.getElementById('catSparklines');
    var html = '<table><thead><tr><th>상품명</th><th>7일 추이</th><th class="text-right">합계</th><th class="text-right">평균</th></tr></thead><tbody>';
    sparklines.forEach(function(s) {
        var maxVal = s.max_val || 1;
        var bars = s.data.map(function(d) {
            var h = Math.max(Math.round(d / maxVal * 28), 2);
            return '<div class="spark-col" style="height:' + h + 'px" title="' + d + '"></div>';
        }).join('');
        html += '<tr><td>' + s.item_nm + '</td>' +
                '<td><div class="spark-bar">' + bars + '</div></td>' +
                '<td class="text-right">' + fmt(s.total) + '</td>' +
                '<td class="text-right">' + s.avg + '</td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

// ========================================
// 안전재고 영향도 리포트
// ========================================
async function saveBaseline() {
    var status = document.getElementById('baselineStatus');
    status.textContent = '저장 중...';
    try {
        var result = await api('/api/report/baseline?store_id=' + _currentStoreId, { method: 'POST', body: {} });
        if (result.status === 'ok') {
            status.textContent = 'Baseline 저장 완료 (' + result.item_count + '개 상품)';
            status.className = 'status-text';
        } else {
            status.textContent = '오류: ' + (result.error || '알 수 없음');
            status.className = 'status-text error';
        }
    } catch (e) {
        status.textContent = '오류: ' + e.message;
        status.className = 'status-text error';
    }
}

async function loadImpactReport() {
    var status = document.getElementById('baselineStatus');
    status.textContent = '분석 중...';
    try {
        var data = await api('/api/report/impact?store_id=' + _currentStoreId);
        if (data.error) {
            status.textContent = data.error;
            status.className = 'status-text error';
            return;
        }
        status.textContent = '';
        document.getElementById('impactContent').style.display = '';

        // 요약 카드
        var s = data.summary;
        renderCards('impactSummaryCards', [
            { value: s.total_items, label: '비교 상품수' },
            { value: s.total_change_pct + '%', label: '전체 변화율', type: s.total_change_pct < 0 ? 'good' : 'warn' },
            { value: s.decreased_count, label: '감소', type: 'good' },
            { value: s.increased_count, label: '증가', type: 'warn' },
            { value: s.unchanged_count, label: '변동 없음' },
        ]);

        // 카테고리별 변화율 수평 바
        var cs = data.cat_summary;
        var ccI = getChartColors();
        var barColors = cs.pct_changes.map(function(v) { return v < 0 ? ccI.green : ccI.red; });
        getOrCreateChart('impactCatChart', {
            type: 'bar',
            data: {
                labels: cs.labels,
                datasets: [{
                    label: '변화율 (%)',
                    data: cs.pct_changes,
                    backgroundColor: barColors,
                    borderRadius: 4,
                }]
            },
            options: {
                indexAxis: 'y',
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: ccI.grid } },
                    y: { grid: { color: ccI.grid } }
                }
            }
        });

        // 상품별 비교 테이블
        var cmp = data.comparisons;
        var thtml = '<div class="table-scroll"><table id="impactCompTable"><thead><tr>' +
            '<th data-sort="text">상품명</th><th data-sort="text">카테고리</th>' +
            '<th data-sort="num" class="text-right">변경 전</th>' +
            '<th data-sort="num" class="text-right">변경 후</th>' +
            '<th data-sort="num" class="text-right">변화율(%)</th>' +
            '<th data-sort="num" class="text-right">발주 전</th>' +
            '<th data-sort="num" class="text-right">발주 후</th>' +
        '</tr></thead><tbody>';
        cmp.forEach(function(c) {
            var cls = c.pct_change < -0.01 ? 'positive' : (c.pct_change > 0.01 ? 'negative' : 'neutral');
            thtml += '<tr><td>' + c.item_nm + '</td><td>' + c.category + '</td>' +
                '<td class="text-right">' + c.old_safety + '</td>' +
                '<td class="text-right">' + c.new_safety + '</td>' +
                '<td class="text-right ' + cls + '">' + c.pct_change + '%</td>' +
                '<td class="text-right">' + c.old_order + '</td>' +
                '<td class="text-right">' + c.new_order + '</td></tr>';
        });
        thtml += '</tbody></table></div>';
        document.getElementById('impactTable').innerHTML = thtml;
        initTableSearch('impactSearch', 'impactCompTable');
        initTableSort('impactCompTable');
        initPagination('impactCompTable', 25);

        // 품절 추이 라인 차트
        var st = data.stockout_trend;
        if (st.labels.length > 0) {
            var ccS = getChartColors();
            var pointColors = st.labels.map(function(d) {
                return d === st.change_date ? ccS.yellow : ccS.red;
            });
            getOrCreateChart('impactStockoutChart', {
                type: 'line',
                data: {
                    labels: st.labels,
                    datasets: [{
                        label: '품절 건수',
                        data: st.counts,
                        borderColor: ccS.red,
                        backgroundColor: ccS.redA.replace('0.7', '0.1'),
                        fill: true,
                        tension: 0.3,
                        pointBackgroundColor: pointColors,
                        pointRadius: st.labels.map(function(d) { return d === st.change_date ? 6 : 3; }),
                    }]
                },
                options: {
                    scales: {
                        x: { grid: { color: ccS.grid } },
                        y: { grid: { color: ccS.grid }, beginAtZero: true }
                    }
                }
            });
        }
    } catch (e) {
        status.textContent = '오류: ' + e.message;
        status.className = 'status-text error';
    }
}

// ========================================
// 이벤트 바인딩
// ========================================
document.addEventListener('DOMContentLoaded', function() {
    // 카테고리 목록 로드
    loadCategoryList();

    // 카테고리 분석 버튼
    document.getElementById('btnLoadCategory').addEventListener('click', function() {
        var midCd = document.getElementById('categorySelect').value;
        if (midCd) loadCategoryReport(midCd);
    });

    // Baseline 저장
    var btnSaveBaseline = document.getElementById('btnSaveBaseline');
    if (btnSaveBaseline) btnSaveBaseline.addEventListener('click', saveBaseline);

    // 영향도 분석 로드
    var btnLoadImpact = document.getElementById('btnLoadImpact');
    if (btnLoadImpact) btnLoadImpact.addEventListener('click', loadImpactReport);

    // 리포트 탭 클릭 시 일일 리포트 로드
    document.querySelectorAll('.nav-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            if (tab.dataset.tab === 'report') {
                loadDailyReport();
            }
        });
    });

    // 리포트 타입 선택 버튼
    document.querySelectorAll('.report-type-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var reportType = btn.dataset.report;

            // 버튼 활성화 상태 변경
            document.querySelectorAll('.report-type-btn').forEach(function(b) {
                b.classList.remove('active');
            });
            btn.classList.add('active');

            // 리포트 뷰 전환
            document.querySelectorAll('.report-view').forEach(function(view) {
                view.classList.remove('active');
            });
            document.getElementById('report-' + reportType).classList.add('active');

            // 데이터 로드
            if (reportType === 'daily') {
                loadDailyReport();
            } else if (reportType === 'weekly') {
                if (!window._weeklyLoaded) {
                    loadWeeklyReport();
                    window._weeklyLoaded = true;
                }
            }
        });
    });
});
