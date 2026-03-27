/* BGF 발주 시스템 - 폐기 분석 탭 */

// === 원인 라벨/색상 매핑 ===
var CAUSE_CONFIG = {
    'DEMAND_DROP':          { label: '수요 감소', color: '#f59e0b' },
    'OVER_ORDER':           { label: '과잉 발주', color: '#ef4444' },
    'EXPIRY_MISMANAGEMENT': { label: '유통기한 관리', color: '#BE185D' },
    'MIXED':                { label: '복합 원인', color: '#6b7280' }
};

var _wasteDays = 7;
var _wasteAllCauses = [];

// ========================================
// 메인 로드 함수
// ========================================
async function loadWasteAnalysis(days) {
    _wasteDays = days || 7;

    // 기간 버튼 활성화 갱신
    var btns = document.querySelectorAll('.waste-period-btn');
    btns.forEach(function(btn) {
        btn.classList.toggle('active', parseInt(btn.getAttribute('data-days')) === _wasteDays);
    });

    try {
        // 3개 API 병렬 호출
        var results = await Promise.all([
            api('/api/waste/summary?store_id=' + _currentStoreId + '&days=' + _wasteDays),
            api('/api/waste/waterfall?store_id=' + _currentStoreId + '&days=' + _wasteDays + '&limit=10'),
            api('/api/waste/causes?store_id=' + _currentStoreId + '&days=' + _wasteDays)
        ]);

        var summary = results[0];
        var waterfall = results[1];
        var causes = results[2];

        if (summary && !summary.error) {
            renderWasteSummaryCards(summary);
            renderWastePieChart(summary);
            renderWasteCauseBarChart(summary);
        }
        if (waterfall && !waterfall.error) {
            renderWaterfallChart(waterfall.items || []);
        }
        if (causes && !causes.error) {
            _wasteAllCauses = causes.causes || [];
            renderWasteTable(_wasteAllCauses);
        }
    } catch (e) {
        console.error('폐기 분석 로드 실패:', e);
    }
}

// ========================================
// 요약 카드
// ========================================
function renderWasteSummaryCards(summary) {
    var byCause = summary.by_cause || {};
    var overOrder = byCause['OVER_ORDER'] || { count: 0, total_qty: 0 };
    var demandDrop = byCause['DEMAND_DROP'] || { count: 0, total_qty: 0 };
    var expiryMgmt = byCause['EXPIRY_MISMANAGEMENT'] || { count: 0, total_qty: 0 };

    renderCards('wasteSummaryCards', [
        { value: fmt(summary.total_qty || 0), label: '총 폐기 수량', type: 'danger' },
        { value: fmt(overOrder.total_qty), label: '과잉 발주', type: 'warn' },
        { value: fmt(demandDrop.total_qty), label: '수요 감소', type: 'accent' },
        { value: fmt(expiryMgmt.total_qty), label: '유통기한 관리' }
    ]);
}

// ========================================
// 파이 차트 (Doughnut)
// ========================================
function renderWastePieChart(summary) {
    var byCause = summary.by_cause || {};
    var causes = Object.keys(byCause);
    if (causes.length === 0) {
        // 빈 데이터
        getOrCreateChart('wasteCausePieChart', {
            type: 'doughnut',
            data: { labels: ['데이터 없음'], datasets: [{ data: [1], backgroundColor: ['#374151'], borderWidth: 0 }] },
            options: { cutout: '60%', plugins: { legend: { display: false } } }
        });
        return;
    }

    var data = causes.map(function(c) { return byCause[c].total_qty; });
    var colors = causes.map(function(c) { return (CAUSE_CONFIG[c] || CAUSE_CONFIG['MIXED']).color; });
    var labels = causes.map(function(c) { return (CAUSE_CONFIG[c] || CAUSE_CONFIG['MIXED']).label; });

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

// ========================================
// 원인별 수량 바 차트
// ========================================
function renderWasteCauseBarChart(summary) {
    var byCause = summary.by_cause || {};
    var causes = Object.keys(byCause);
    if (causes.length === 0) return;

    var labels = causes.map(function(c) { return (CAUSE_CONFIG[c] || CAUSE_CONFIG['MIXED']).label; });
    var qtyData = causes.map(function(c) { return byCause[c].total_qty; });
    var countData = causes.map(function(c) { return byCause[c].count; });
    var colors = causes.map(function(c) { return (CAUSE_CONFIG[c] || CAUSE_CONFIG['MIXED']).color; });

    getOrCreateChart('wasteCauseBarChart', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '폐기 수량',
                    data: qtyData,
                    backgroundColor: colors,
                    borderRadius: 4
                },
                {
                    label: '건수',
                    data: countData,
                    backgroundColor: colors.map(function(c) { return c + '60'; }),
                    borderRadius: 4
                }
            ]
        },
        options: {
            plugins: { legend: { position: 'top' } },
            scales: {
                x: { grid: { color: getChartColors().grid } },
                y: { grid: { color: getChartColors().grid }, beginAtZero: true }
            }
        }
    });
}

// ========================================
// 워터폴 차트 (Stacked Horizontal Bar)
// ========================================
function renderWaterfallChart(items) {
    if (!items || items.length === 0) {
        getOrCreateChart('wasteWaterfallChart', {
            type: 'bar',
            data: { labels: ['데이터 없음'], datasets: [{ data: [0] }] },
            options: { indexAxis: 'y' }
        });
        return;
    }

    var labels = items.map(function(it) {
        var nm = it.item_nm || it.item_cd;
        return nm.length > 12 ? nm.substring(0, 12) + '..' : nm;
    });
    var soldData = items.map(function(it) { return it.sold_qty || 0; });
    var wasteData = items.map(function(it) { return it.waste_qty || 0; });
    var otherData = items.map(function(it) {
        return Math.max(0, (it.order_qty || 0) - (it.sold_qty || 0) - (it.waste_qty || 0));
    });

    getOrCreateChart('wasteWaterfallChart', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '판매',
                    data: soldData,
                    backgroundColor: '#22c55e',
                    borderRadius: 0
                },
                {
                    label: '폐기',
                    data: wasteData,
                    backgroundColor: '#ef4444',
                    borderRadius: 0
                },
                {
                    label: '잔여재고',
                    data: otherData,
                    backgroundColor: '#6b728040',
                    borderRadius: { topLeft: 4, topRight: 4 }
                }
            ]
        },
        options: {
            indexAxis: 'y',
            scales: {
                x: { stacked: true, grid: { color: getChartColors().grid }, title: { display: true, text: '수량' } },
                y: { stacked: true, grid: { display: false } }
            },
            plugins: {
                legend: { position: 'top' },
                tooltip: {
                    callbacks: {
                        afterBody: function(ctx) {
                            var idx = ctx[0].dataIndex;
                            var item = items[idx];
                            return '발주 합계: ' + fmt(item.order_qty || 0) + '개';
                        }
                    }
                }
            }
        }
    });
}

// ========================================
// 상세 테이블
// ========================================
function renderWasteTable(causes) {
    var tbody = document.getElementById('wasteTableBody');
    if (!tbody) return;

    if (!causes || causes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-tertiary);padding:32px;">폐기 분석 데이터가 없습니다</td></tr>';
        return;
    }

    tbody.innerHTML = causes.map(function(c) {
        var causeLabel = (CAUSE_CONFIG[c.primary_cause] || CAUSE_CONFIG['MIXED']).label;
        var causeColor = (CAUSE_CONFIG[c.primary_cause] || CAUSE_CONFIG['MIXED']).color;
        var confidence = c.confidence != null ? Math.round(c.confidence * 100) + '%' : '-';

        return '<tr>' +
            '<td data-label="상품명">' + (c.item_nm || c.item_cd || '-') + '</td>' +
            '<td data-label="원인"><span class="waste-cause-badge" style="background:' + causeColor + '20;color:' + causeColor + '">' + causeLabel + '</span></td>' +
            '<td data-label="발주량" class="text-right">' + fmt(c.order_qty || 0) + '</td>' +
            '<td data-label="판매량" class="text-right">' + fmt(c.actual_sold_qty || 0) + '</td>' +
            '<td data-label="폐기량" class="text-right">' + fmt(c.waste_qty || 0) + '</td>' +
            '<td data-label="신뢰도" class="text-right">' + confidence + '</td>' +
            '<td data-label="날짜">' + (c.waste_date || '-') + '</td>' +
            '</tr>';
    }).join('');
}

// ========================================
// 초기화 + 이벤트 바인딩
// ========================================
function initWasteTab() {
    // 기간 선택 버튼
    var periodBtns = document.querySelectorAll('.waste-period-btn');
    periodBtns.forEach(function(btn) {
        btn.addEventListener('click', function() {
            var days = parseInt(this.getAttribute('data-days'));
            loadWasteAnalysis(days);
        });
    });

    // 상품명 검색
    var searchInput = document.getElementById('wasteSearch');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            var q = this.value.toLowerCase();
            var filtered = _wasteAllCauses.filter(function(c) {
                return (c.item_nm || '').toLowerCase().indexOf(q) >= 0 ||
                       (c.item_cd || '').toLowerCase().indexOf(q) >= 0;
            });
            renderWasteTable(filtered);
        });
    }
}

// DOM 로드 시 초기화
document.addEventListener('DOMContentLoaded', initWasteTab);
