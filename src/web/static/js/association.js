/* 연관 패턴 효과 분석 대시보드 */

var _assocLoaded = false;
var _assocCharts = {};
var _assocPollTimer = null;

async function loadAssociation() {
    if (_assocLoaded) return;
    _assocLoaded = true;
    await checkAssocStatus();
}

function resetAssociation() {
    _assocLoaded = false;
    Object.values(_assocCharts).forEach(function(c) { if (c) c.destroy(); });
    _assocCharts = {};
    if (_assocPollTimer) { clearInterval(_assocPollTimer); _assocPollTimer = null; }
}

// === 상태 확인 ===
async function checkAssocStatus() {
    var container = document.getElementById('assocContent');
    if (!container) return;

    try {
        var data = await api('/api/association/status' + storeParam());
        if (data.ready) {
            await renderAssocAnalysis(container);
        } else {
            renderAssocCollecting(container, data);
            startAssocPolling();
        }
    } catch (e) {
        container.innerHTML = '<div class="assoc-error">데이터를 불러올 수 없습니다.</div>';
    }
}

// === 수집 중 화면 ===
function renderAssocCollecting(container, data) {
    var daysPct = Math.min(100, Math.round(data.days_collected / data.required.days * 100));
    var boostPct = Math.min(100, Math.round(data.boost_applied_count / data.required.boost_applied * 100));
    var noPct = Math.min(100, Math.round(data.no_boost_count / data.required.no_boost * 100));

    var storesHtml = '';
    if (data.stores && data.stores.length > 0) {
        storesHtml = '<div class="assoc-stores-grid">';
        data.stores.forEach(function(s) {
            var maxRec = Math.max.apply(null, data.stores.map(function(x) { return x.records; })) || 1;
            var pct = Math.round(s.records / maxRec * 100);
            storesHtml += '<div class="assoc-store-row">' +
                '<span class="assoc-store-name">' + escHtml(s.name || s.store_id) + '</span>' +
                '<div class="assoc-bar-track"><div class="assoc-bar-fill" style="width:' + pct + '%"></div></div>' +
                '<span class="assoc-store-cnt">' + s.records.toLocaleString() + '건</span>' +
                '</div>';
        });
        storesHtml += '</div>';
    }

    var remaining = Math.max(0, data.required.days - data.days_collected);

    container.innerHTML =
        '<div class="assoc-collecting">' +
            '<div class="assoc-collect-icon">' +
                '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5">' +
                    '<circle cx="12" cy="12" r="10"/>' +
                    '<polyline points="12 6 12 12 16 14"/>' +
                '</svg>' +
            '</div>' +
            '<h2 class="assoc-collect-title">연관 패턴 데이터를 수집하고 있습니다</h2>' +
            '<p class="assoc-collect-sub">충분한 데이터가 쌓이면 자동으로 분석 화면으로 전환됩니다.</p>' +

            '<div class="assoc-progress-section">' +
                assocProgressBar('수집 기간', data.days_collected, data.required.days, '일', daysPct) +
                assocProgressBar('부스트 적용', data.boost_applied_count, data.required.boost_applied, '건', boostPct) +
                assocProgressBar('비교군', data.no_boost_count, data.required.no_boost, '건', noPct) +
            '</div>' +

            '<div class="assoc-stores-section">' +
                '<h3 class="assoc-section-title">매장별 수집 현황</h3>' +
                storesHtml +
            '</div>' +

            (remaining > 0 ?
                '<p class="assoc-estimate">예상 분석 시작: 약 ' + remaining + '일 후</p>' : '') +
        '</div>';
}

function assocProgressBar(label, current, total, unit, pct) {
    return '<div class="assoc-prog-row">' +
        '<div class="assoc-prog-label">' + label + '</div>' +
        '<div class="assoc-prog-track"><div class="assoc-prog-fill' +
            (pct >= 100 ? ' assoc-prog-done' : '') +
            '" style="width:' + pct + '%"></div></div>' +
        '<div class="assoc-prog-val">' + current.toLocaleString() + ' / ' + total.toLocaleString() + unit + '</div>' +
    '</div>';
}

// === 30초 폴링 (탭 활성 시만) ===
function startAssocPolling() {
    if (_assocPollTimer) return;
    _assocPollTimer = setInterval(async function() {
        if (document.visibilityState !== 'visible') return;
        var tab = document.getElementById('tab-association');
        if (!tab || !tab.classList.contains('active')) return;

        try {
            var data = await api('/api/association/status' + storeParam());
            if (data.ready) {
                clearInterval(_assocPollTimer);
                _assocPollTimer = null;
                var container = document.getElementById('assocContent');
                if (container) await renderAssocAnalysis(container);
            } else {
                var container = document.getElementById('assocContent');
                if (container) renderAssocCollecting(container, data);
            }
        } catch (e) { /* silent */ }
    }, 30000);
}

// === 분석 화면 ===
async function renderAssocAnalysis(container) {
    container.innerHTML = '<div class="assoc-loading"><div class="fm-spinner"></div><p>분석 결과를 불러오는 중...</p></div>';

    try {
        var data = await api('/api/association/analysis' + storeParam());
        var s = data.summary;

        var html =
            // 요약 카드 3개
            '<div class="assoc-cards">' +
                assocCard('부스트 적용', s.boost_applied_count.toLocaleString() + '건', 'boost') +
                assocCard('평균 부스트', '+' + ((s.avg_boost_value - 1) * 100).toFixed(1) + '%', 'avg') +
                assocCard('오차 개선율', (s.improvement_pct > 0 ? '+' : '') + s.improvement_pct + '%', s.improvement_pct > 0 ? 'good' : 'bad') +
            '</div>' +

            // MAE 비교 차트
            '<div class="assoc-row">' +
                '<div class="assoc-panel assoc-panel-half">' +
                    '<h3 class="assoc-panel-title">예측 오차 비교 (MAE)</h3>' +
                    '<div class="assoc-chart-wrap"><canvas id="assocMaeChart"></canvas></div>' +
                '</div>' +
                '<div class="assoc-panel assoc-panel-half">' +
                    '<h3 class="assoc-panel-title">부스트 계수 분포</h3>' +
                    '<div class="assoc-chart-wrap"><canvas id="assocDistChart"></canvas></div>' +
                '</div>' +
            '</div>' +

            // 일별 트렌드
            '<div class="assoc-panel">' +
                '<h3 class="assoc-panel-title">일별 부스트 적용 추이</h3>' +
                '<div class="assoc-chart-wrap assoc-chart-wide"><canvas id="assocTrendChart"></canvas></div>' +
            '</div>' +

            // 매장별 비교 테이블
            '<div class="assoc-panel">' +
                '<h3 class="assoc-panel-title">매장별 효과 비교</h3>' +
                renderStoreTable(data.by_store) +
            '</div>' +

            // 상위 연관 규칙
            '<div class="assoc-panel">' +
                '<h3 class="assoc-panel-title">상위 연관 규칙 Top 10</h3>' +
                renderRulesTable(data.top_rules) +
            '</div>';

        container.innerHTML = html;

        // 차트 렌더
        renderMaeChart(s);
        renderDistChart(data.boost_distribution);
        renderTrendChart(data.daily_trend);

    } catch (e) {
        container.innerHTML = '<div class="assoc-error">분석 데이터를 불러올 수 없습니다.</div>';
    }
}

function assocCard(label, value, type) {
    return '<div class="assoc-card assoc-card-' + type + '">' +
        '<div class="assoc-card-label">' + label + '</div>' +
        '<div class="assoc-card-value">' + value + '</div>' +
    '</div>';
}

// === 차트 렌더링 ===

function renderMaeChart(summary) {
    var ctx = document.getElementById('assocMaeChart');
    if (!ctx) return;
    if (_assocCharts.mae) _assocCharts.mae.destroy();

    _assocCharts.mae = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['부스트 있음', '부스트 없음'],
            datasets: [{
                data: [summary.boosted_mae, summary.non_boosted_mae],
                backgroundColor: ['rgba(99,102,241,0.7)', 'rgba(148,163,184,0.5)'],
                borderRadius: 6,
                barThickness: 48,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, title: { display: true, text: 'MAE', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.1)' } },
                x: { ticks: { color: '#94a3b8' }, grid: { display: false } }
            }
        }
    });
}

function renderDistChart(dist) {
    var ctx = document.getElementById('assocDistChart');
    if (!ctx) return;
    if (_assocCharts.dist) _assocCharts.dist.destroy();

    var labels = Object.keys(dist);
    var values = Object.values(dist);

    _assocCharts.dist = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: 'rgba(99,102,241,0.6)',
                borderRadius: 4,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, title: { display: true, text: '건수', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.1)' } },
                x: { ticks: { color: '#94a3b8', font: { size: 11 } }, grid: { display: false } }
            }
        }
    });
}

function renderTrendChart(trend) {
    var ctx = document.getElementById('assocTrendChart');
    if (!ctx) return;
    if (_assocCharts.trend) _assocCharts.trend.destroy();

    var labels = trend.map(function(d) { return d.date.slice(5); });
    var counts = trend.map(function(d) { return d.boost_count; });
    var boosts = trend.map(function(d) { return ((d.avg_boost - 1) * 100).toFixed(1); });

    _assocCharts.trend = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '적용 건수',
                    data: counts,
                    borderColor: 'rgba(99,102,241,0.9)',
                    backgroundColor: 'rgba(99,102,241,0.1)',
                    fill: true,
                    tension: 0.3,
                    yAxisID: 'y',
                },
                {
                    label: '평균 부스트 (%)',
                    data: boosts,
                    borderColor: 'rgba(251,191,36,0.9)',
                    borderDash: [5, 3],
                    tension: 0.3,
                    yAxisID: 'y1',
                    pointRadius: 2,
                }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#94a3b8' } } },
            scales: {
                y: { beginAtZero: true, position: 'left', title: { display: true, text: '건수', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.1)' } },
                y1: { beginAtZero: true, position: 'right', title: { display: true, text: '부스트 %', color: '#fbbf24' }, ticks: { color: '#fbbf24' }, grid: { drawOnChartArea: false } },
                x: { ticks: { color: '#94a3b8' }, grid: { display: false } }
            }
        }
    });
}

// === 테이블 렌더링 ===

function renderStoreTable(stores) {
    if (!stores || stores.length === 0) return '<p class="assoc-empty">매장 데이터 없음</p>';

    var html = '<table class="assoc-table"><thead><tr>' +
        '<th>매장</th><th class="text-right">부스트 건수</th><th class="text-right">평균 부스트</th>' +
        '<th class="text-right">부스트 MAE</th><th class="text-right">일반 MAE</th><th class="text-right">개선율</th>' +
        '</tr></thead><tbody>';

    stores.forEach(function(s) {
        var cls = s.improvement_pct > 0 ? 'assoc-good' : (s.improvement_pct < 0 ? 'assoc-bad' : '');
        html += '<tr>' +
            '<td>' + escHtml(s.name || s.store_id) + '</td>' +
            '<td class="text-right">' + s.boost_count.toLocaleString() + '</td>' +
            '<td class="text-right">+' + ((s.avg_boost - 1) * 100).toFixed(1) + '%</td>' +
            '<td class="text-right">' + s.boosted_mae.toFixed(2) + '</td>' +
            '<td class="text-right">' + s.non_boosted_mae.toFixed(2) + '</td>' +
            '<td class="text-right ' + cls + '">' + (s.improvement_pct > 0 ? '+' : '') + s.improvement_pct + '%</td>' +
            '</tr>';
    });

    return html + '</tbody></table>';
}

function renderRulesTable(rules) {
    if (!rules || rules.length === 0) return '<p class="assoc-empty">연관 규칙 없음</p>';

    var html = '<table class="assoc-table"><thead><tr>' +
        '<th>선행</th><th></th><th>후행</th><th>레벨</th>' +
        '<th class="text-right">Lift</th><th class="text-right">Confidence</th>' +
        '</tr></thead><tbody>';

    rules.forEach(function(r) {
        var levelBadge = r.level === 'mid'
            ? '<span class="assoc-badge assoc-badge-mid">MID</span>'
            : '<span class="assoc-badge assoc-badge-item">ITEM</span>';

        html += '<tr>' +
            '<td class="assoc-rule-item">' + escHtml(r.antecedent) + '</td>' +
            '<td class="assoc-arrow">&rarr;</td>' +
            '<td class="assoc-rule-item">' + escHtml(r.consequent) + '</td>' +
            '<td>' + levelBadge + '</td>' +
            '<td class="text-right assoc-lift">' + r.lift.toFixed(2) + '</td>' +
            '<td class="text-right">' + (r.confidence * 100).toFixed(0) + '%</td>' +
            '</tr>';
    });

    return html + '</tbody></table>';
}

// === HTML 이스케이프 ===
function escHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
