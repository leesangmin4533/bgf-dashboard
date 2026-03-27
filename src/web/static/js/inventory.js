/* BGF 발주 시스템 - 재고 수명(TTL) 대시보드 */
console.log('[INV] inventory.js loaded');

var _invLoaded = false;

async function loadInventoryDashboard() {
    if (_invLoaded) return;
    console.log('[INV] loadInventoryDashboard called');

    try {
        var results = await Promise.all([
            api('/api/inventory/ttl-summary?store_id=' + _currentStoreId),
            api('/api/inventory/batch-expiry?store_id=' + _currentStoreId)
        ]);
        var summary = results[0];
        var batches = results[1];

        renderInventorySummaryCards(summary, batches);
        renderFreshnessChart(summary.freshness_distribution);
        renderTtlDistChart(summary.ttl_distribution);
        renderBatchTimeline(batches);
        renderStaleTable(summary.stale_items_list);
        initInvSearch();

        _invLoaded = true;
    } catch (e) {
        console.error('[INV] load failed:', e);
    }
}

// === 요약 카드 ===
function renderInventorySummaryCards(summary, batches) {
    var el;

    el = document.getElementById('invTotalItems');
    if (el) el.textContent = fmt(summary.total_items || 0);

    el = document.getElementById('invStaleItems');
    if (el) {
        var stale = summary.stale_items || 0;
        el.textContent = fmt(stale);
        if (stale > 0) el.style.color = 'var(--danger)';
    }
    el = document.getElementById('invStaleSub');
    if (el) el.textContent = '유령재고 ' + fmt(summary.stale_stock_qty || 0) + '개';

    el = document.getElementById('invExpiringToday');
    if (el) {
        var todayBatch = (batches.batches || []).find(function(b) { return b.label === '오늘'; });
        el.textContent = todayBatch ? fmt(todayBatch.total_qty) : '0';
    }

    el = document.getElementById('invFreshRate');
    if (el) {
        var total = summary.total_items || 0;
        var fresh = (summary.freshness_distribution || {}).fresh || 0;
        var pct = total > 0 ? Math.round(fresh / total * 100) : 0;
        el.textContent = pct + '%';
    }
}

// === 신선도 도넛 차트 ===
function renderFreshnessChart(dist) {
    var canvas = document.getElementById('inventoryFreshnessChart');
    if (!canvas || !dist) return;

    var colors = getChartColors();
    var data = [dist.fresh || 0, dist.warning || 0, dist.stale || 0];
    var total = data.reduce(function(a, b) { return a + b; }, 0);

    getOrCreateChart('inventoryFreshnessChart', {
        type: 'doughnut',
        data: {
            labels: ['신선 (Fresh)', '주의 (Warning)', '스테일 (Stale)'],
            datasets: [{
                data: data,
                backgroundColor: [colors.green, colors.yellow, colors.red],
                borderWidth: 0,
            }]
        },
        options: {
            cutout: '60%',
            responsive: true,
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            var pct = total > 0 ? Math.round(ctx.raw / total * 100) : 0;
                            return ctx.label + ': ' + fmt(ctx.raw) + ' (' + pct + '%)';
                        }
                    }
                }
            }
        }
    });
}

// === TTL 분포 바 차트 ===
function renderTtlDistChart(dist) {
    var canvas = document.getElementById('inventoryTtlChart');
    if (!canvas || !dist) return;

    var colors = getChartColors();
    var labels = ['18h (1일)', '36h (2일)', '54h (3일)', '기본'];
    var keys = ['18h', '36h', '54h', 'default'];
    var data = keys.map(function(k) { return dist[k] || 0; });
    var barColors = [colors.red, colors.blue, colors.green, colors.slate];

    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    getOrCreateChart('inventoryTtlChart', {
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

// === 배치 만료 타임라인 ===
function renderBatchTimeline(batchData) {
    var canvas = document.getElementById('inventoryBatchChart');
    if (!canvas) return;

    var batches = (batchData && batchData.batches) || [];
    if (batches.length === 0) {
        var ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#999';
        ctx.textAlign = 'center';
        ctx.font = '14px sans-serif';
        ctx.fillText('만료 예정 배치 없음', canvas.width / 2, canvas.height / 2);
        return;
    }

    var labels = batches.map(function(b) { return b.label + ' (' + b.expiry_date.substring(5) + ')'; });
    var itemCounts = batches.map(function(b) { return b.item_count || 0; });
    var totalQtys = batches.map(function(b) { return b.total_qty || 0; });
    var colors = getChartColors();
    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    getOrCreateChart('inventoryBatchChart', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '만료 수량',
                    data: totalQtys,
                    backgroundColor: colors.redA,
                    borderColor: colors.red,
                    borderWidth: 1,
                    borderRadius: 4,
                    yAxisID: 'y',
                },
                {
                    label: '상품 종류',
                    data: itemCounts,
                    backgroundColor: colors.blueA,
                    borderColor: colors.blue,
                    borderWidth: 1,
                    borderRadius: 4,
                    yAxisID: 'y1',
                }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'top' } },
            scales: {
                y: {
                    type: 'linear', position: 'left',
                    beginAtZero: true,
                    grid: { color: gridColor },
                    title: { display: true, text: '수량' }
                },
                y1: {
                    type: 'linear', position: 'right',
                    beginAtZero: true,
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: '종류' }
                }
            }
        }
    });
}

// === 스테일 상품 테이블 ===
function renderStaleTable(items) {
    var tbody = document.getElementById('invStaleTableBody');
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="padding:24px;color:var(--text-muted)">스테일 재고 없음</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(function(item) {
        var overRatio = item.ttl_hours > 0 ? (item.hours_since_query / item.ttl_hours) : 999;
        var badgeClass = overRatio >= 1 ? 'stale' : overRatio >= 0.5 ? 'warning' : 'fresh';
        var badgeText = overRatio >= 1 ? '만료' : '주의';

        return '<tr>'
            + '<td data-label="상품명">' + _invEsc(item.item_nm || item.item_cd) + '</td>'
            + '<td data-label="재고" class="text-right">' + fmt(item.stock_qty) + '</td>'
            + '<td data-label="경과" class="text-right">' + item.hours_since_query + 'h</td>'
            + '<td data-label="TTL" class="text-right">' + item.ttl_hours + 'h</td>'
            + '<td data-label="카테고리">' + _invEsc(item.mid_cd || '-') + '</td>'
            + '<td data-label="상태"><span class="inventory-status-badge ' + badgeClass + '">' + badgeText + '</span></td>'
            + '</tr>';
    }).join('');
}

// === 검색 필터 ===
function initInvSearch() {
    var searchInput = document.getElementById('invStaleSearch');
    if (!searchInput) return;

    searchInput.addEventListener('input', function() {
        var query = this.value.toLowerCase();
        var rows = document.querySelectorAll('#invStaleTableBody tr');
        rows.forEach(function(row) {
            var text = row.textContent.toLowerCase();
            row.style.display = text.includes(query) ? '' : 'none';
        });
    });
}

// === 유틸 ===
function _invEsc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
