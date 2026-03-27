/* BGF 발주 시스템 - 입고 지연 영향 분석 대시보드 */
console.log('[RCV] receiving.js loaded');

var _rcvLoaded = false;

async function loadReceivingDashboard() {
    if (_rcvLoaded) return;
    console.log('[RCV] loadReceivingDashboard called');

    try {
        var results = await Promise.all([
            api('/api/receiving/summary?store_id=' + _currentStoreId),
            api('/api/receiving/trend?store_id=' + _currentStoreId + '&days=30'),
            api('/api/receiving/slow-items?store_id=' + _currentStoreId + '&limit=20')
        ]);
        var summary = results[0];
        var trend = results[1];
        var slowItems = results[2];

        renderRcvSummaryCards(summary);
        renderRcvTrendChart(trend);
        renderRcvPendingChart(summary.pending_age_distribution);
        renderRcvSlowTable(slowItems.items || []);
        initRcvSearch();

        _rcvLoaded = true;
    } catch (e) {
        console.error('[RCV] load failed:', e);
    }
}

// === 요약 카드 ===
function renderRcvSummaryCards(summary) {
    var el;

    el = document.getElementById('rcvAvgLeadTime');
    if (el) el.textContent = (summary.avg_lead_time || 0).toFixed(1) + '일';

    el = document.getElementById('rcvMaxLeadTime');
    if (el) {
        var maxLt = summary.max_lead_time || 0;
        el.textContent = maxLt.toFixed(1) + '일';
        if (maxLt > 5) el.style.color = 'var(--danger)';
    }

    el = document.getElementById('rcvShortRate');
    if (el) {
        var rate = summary.short_delivery_rate || 0;
        el.textContent = (rate * 100).toFixed(1) + '%';
        if (rate > 0.15) el.style.color = 'var(--danger)';
    }

    el = document.getElementById('rcvPendingCount');
    if (el) {
        var cnt = summary.pending_items_count || 0;
        el.textContent = fmt(cnt) + '건';
        if (cnt > 10) el.style.color = 'var(--warning)';
    }
}

// === 리드타임 추이 라인차트 (듀얼 Y축) ===
function renderRcvTrendChart(trend) {
    var canvas = document.getElementById('receivingTrendChart');
    if (!canvas) return;

    var dates = trend.dates || [];
    var avgLts = trend.avg_lead_times || [];
    var counts = trend.delivery_counts || [];

    if (dates.length === 0) {
        var ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#999';
        ctx.textAlign = 'center';
        ctx.font = '14px sans-serif';
        ctx.fillText('입고 데이터 없음', canvas.width / 2, canvas.height / 2);
        return;
    }

    var colors = getChartColors();
    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    var labels = dates.map(function(d) { return d.substring(5); });

    getOrCreateChart('receivingTrendChart', {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '평균 리드타임(일)',
                    data: avgLts,
                    borderColor: colors.blue,
                    backgroundColor: colors.blueA,
                    fill: true,
                    tension: 0.3,
                    yAxisID: 'y',
                },
                {
                    label: '입고 건수',
                    data: counts,
                    borderColor: colors.green,
                    backgroundColor: 'transparent',
                    borderDash: [4, 4],
                    tension: 0.3,
                    yAxisID: 'y1',
                }
            ]
        },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'bottom' }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: gridColor },
                    title: { display: true, text: '리드타임(일)' }
                },
                y1: {
                    beginAtZero: true,
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: '건수' }
                },
                x: {
                    grid: { display: false }
                }
            }
        }
    });
}

// === 미입고 경과일 분포 바차트 ===
function renderRcvPendingChart(dist) {
    var canvas = document.getElementById('receivingPendingChart');
    if (!canvas || !dist) return;

    var colors = getChartColors();
    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    var labels = ['0-1일', '2-3일', '4-7일', '8일+'];
    var keys = ['0-1', '2-3', '4-7', '8+'];
    var data = keys.map(function(k) { return dist[k] || 0; });
    var barColors = [colors.green, colors.blue, colors.yellow, colors.red];

    getOrCreateChart('receivingPendingChart', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: '상품 수',
                data: data,
                backgroundColor: barColors,
                borderRadius: 4,
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: gridColor },
                    title: { display: true, text: '상품 수' }
                },
                x: {
                    grid: { display: false }
                }
            }
        }
    });
}

// === 지연 상품 테이블 ===
function renderRcvSlowTable(items) {
    var tbody = document.getElementById('rcvSlowBody');
    if (!tbody) return;

    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">지연 상품 없음</td></tr>';
        return;
    }

    var html = '';
    for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var ageClass = item.pending_age >= 8 ? 'color:var(--danger)' :
                       item.pending_age >= 4 ? 'color:var(--warning)' : '';
        html += '<tr>'
            + '<td title="' + item.item_cd + '">' + (item.item_nm || item.item_cd) + '</td>'
            + '<td style="' + ageClass + '">' + item.pending_age + '일</td>'
            + '<td>' + (item.lead_time_avg || 0).toFixed(1) + '일</td>'
            + '<td>' + ((item.short_delivery_rate || 0) * 100).toFixed(0) + '%</td>'
            + '</tr>';
    }
    tbody.innerHTML = html;
}

// === 테이블 검색 ===
function initRcvSearch() {
    var input = document.getElementById('rcvSlowSearch');
    if (!input) return;
    input.addEventListener('input', function() {
        var q = input.value.toLowerCase();
        var rows = document.querySelectorAll('#rcvSlowBody tr');
        rows.forEach(function(row) {
            var text = row.textContent.toLowerCase();
            row.style.display = text.includes(q) ? '' : 'none';
        });
    });
}
