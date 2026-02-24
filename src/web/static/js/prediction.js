/* BGF 발주 시스템 - 예측분석 탭 */
console.log('[PRED] prediction.js loaded');

// === Lazy load 플래그 ===
window._predAccuracyLoaded = false;
window._predBlendingLoaded = false;

// === 요약 카드 스켈레톤 로딩 ===
function resetPredCards() {
    var skeletonValue = '<div class="skeleton skeleton-value"></div>';
    var ids = ['predHitRateValue', 'predQtyAccuracyValue', 'predMLStatusValue', 'predModelMixValue'];
    ids.forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.innerHTML = skeletonValue;
    });
}

// === 요약 카드 로드 ===
async function loadPredSummary() {
    console.log('[PRED] loadPredSummary called');
    resetPredCards();
    try {
        var data = await api('/api/prediction/summary?store_id=' + _currentStoreId);
        console.log('[PRED] summary data:', data);
        renderPredHitRate(data.eval_accuracy);
        renderPredQtyAccuracy(data.qty_accuracy);
        renderPredMLStatus(data.ml_status);
        renderPredModelMix(data.model_type_dist);

        // ML 그리드도 summary 데이터로 렌더 (추가 API 불필요)
        renderMLGrid(data.ml_status);
    } catch (e) {
        console.error('Prediction summary load failed:', e);
    }
}

// === 요약 카드 렌더링 ===
function renderPredHitRate(acc) {
    var val = document.getElementById('predHitRateValue');
    var sub = document.getElementById('predHitRateSub');
    if (!val || !acc) return;

    if (acc.total === 0) {
        val.textContent = '-';
        sub.textContent = '검증 데이터 없음';
        return;
    }

    // Progress Ring SVG
    var pct = acc.accuracy || 0;
    var radius = 32;
    var circumference = 2 * Math.PI * radius;
    var offset = circumference - (pct / 100) * circumference;
    val.innerHTML = '<div class="progress-ring-container">' +
        '<svg class="progress-ring-svg" width="76" height="76">' +
        '<circle class="progress-ring-bg" cx="38" cy="38" r="' + radius + '"/>' +
        '<circle class="progress-ring-fill" cx="38" cy="38" r="' + radius + '" ' +
        'stroke-dasharray="' + circumference + '" stroke-dashoffset="' + offset + '"/>' +
        '</svg>' +
        '<span class="progress-ring-text">' + pct + '%</span>' +
        '</div>';

    var subText = '검증 ' + fmt(acc.total) + '건 | 과잉 ' + fmt(acc.overs) + '건';
    if (acc.pending > 0) {
        subText += ' | 미평가 ' + fmt(acc.pending) + '건';
    }
    sub.textContent = subText;
}

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
    sub.textContent = 'MAPE ' + acc.mape + '% | ' + fmt(acc.total) + '건';
}

function renderPredMLStatus(ml) {
    var val = document.getElementById('predMLStatusValue');
    var sub = document.getElementById('predMLStatusSub');
    if (!val || !ml) return;

    val.textContent = ml.active_groups + '/' + ml.total_groups;
    if (ml.latest_training) {
        sub.textContent = '최근 학습: ' + ml.latest_training.substring(0, 10);
    } else {
        sub.textContent = ml.available ? '모델 활성' : '모델 없음';
    }
}

function renderPredModelMix(dist) {
    var val = document.getElementById('predModelMixValue');
    var sub = document.getElementById('predModelMixSub');
    if (!val || !dist) return;

    var total = dist.total || 0;
    if (total === 0) {
        val.textContent = '-';
        sub.textContent = '예측 기록 없음';
        return;
    }

    var mlCount = (dist.ensemble_30 || 0) + (dist.ensemble_50 || 0);
    var mlPct = Math.round(mlCount / total * 100);
    val.textContent = 'ML ' + mlPct + '%';
    sub.textContent = '규칙 ' + fmt(dist.rule || 0) + ' | E30 ' + fmt(dist.ensemble_30 || 0) + ' | E50 ' + fmt(dist.ensemble_50 || 0);
}

// === 적중률 상세 서브뷰 ===
async function loadAccuracyDetail() {
    if (window._predAccuracyLoaded) return;

    try {
        var data = await api('/api/prediction/accuracy-detail?store_id=' + _currentStoreId);
        renderMapeChart(data.daily_mape_trend);
        renderCategoryAccuracyTable(data.category_accuracy);
        renderBestWorst(data.best_items, data.worst_items);
        window._predAccuracyLoaded = true;
    } catch (e) {
        console.error('Accuracy detail load failed:', e);
    }
}

function renderMapeChart(trend) {
    var canvas = document.getElementById('predMapeChart');
    if (!canvas || !trend || trend.length === 0) return;

    var labels = trend.map(function(t) { return t.date ? t.date.substring(5) : ''; });
    var mapeData = trend.map(function(t) { return t.mape || 0; });
    var countData = trend.map(function(t) { return t.count || 0; });

    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    getOrCreateChart('predMapeChart', {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'MAPE (%)',
                    data: mapeData,
                    borderColor: getChartColors().blue,
                    backgroundColor: getChartColors().blueA.replace('0.7', '0.1'),
                    fill: true,
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
                    title: { display: true, text: 'MAPE (%)' },
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

function renderCategoryAccuracyTable(cats) {
    var tbody = document.getElementById('predCategoryTableBody');
    if (!tbody) return;

    if (!cats || cats.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center" style="padding:24px;color:var(--text-muted)">데이터 없음</td></tr>';
        return;
    }

    tbody.innerHTML = cats.map(function(c) {
        var mapeClass = c.mape > 50 ? 'color:var(--danger)' : c.mape > 30 ? 'color:var(--warning)' : '';
        return '<tr>'
            + '<td data-label="카테고리">' + esc(c.mid_nm || c.mid_cd) + '</td>'
            + '<td data-label="MAPE" class="text-right" style="' + mapeClass + '">' + c.mape + '%</td>'
            + '<td data-label="MAE" class="text-right">' + c.mae + '</td>'
            + '<td data-label="정확도" class="text-right">' + c.accuracy_within_2 + '%</td>'
            + '<td data-label="건수" class="text-right">' + fmt(c.total) + '</td>'
            + '</tr>';
    }).join('');

    initTableSort('predCategoryTable');
    initPagination('predCategoryTable', 25);
}

function renderBestWorst(best, worst) {
    var body = document.getElementById('predBestWorstBody');
    if (!body) return;

    var html = '';

    // Worst 상품
    html += '<div style="margin-bottom:16px">';
    html += '<div style="font-weight:600;margin-bottom:8px;color:var(--danger)">Worst (높은 MAPE)</div>';
    if (worst && worst.length > 0) {
        html += '<div class="report-table-wrapper"><table class="report-table"><thead><tr>'
            + '<th>상품명</th><th class="text-right">MAPE</th><th class="text-right">편향</th><th class="text-right">건수</th>'
            + '</tr></thead><tbody>';
        worst.forEach(function(w) {
            html += '<tr>'
                + '<td>' + esc(w.item_nm || w.item_cd) + '</td>'
                + '<td class="text-right" style="color:var(--danger)">' + (w.mape != null ? Math.round(w.mape) + '%' : '-') + '</td>'
                + '<td class="text-right">' + (w.bias != null ? (w.bias > 0 ? '+' : '') + w.bias.toFixed(1) : '-') + '</td>'
                + '<td class="text-right">' + (w.sample_count || 0) + '</td>'
                + '</tr>';
        });
        html += '</tbody></table></div>';
    } else {
        html += '<div class="hint" style="padding:8px;color:var(--text-muted)">데이터 없음</div>';
    }
    html += '</div>';

    // Best 상품
    html += '<div>';
    html += '<div style="font-weight:600;margin-bottom:8px;color:var(--success)">Best (낮은 MAPE)</div>';
    if (best && best.length > 0) {
        html += '<div class="report-table-wrapper"><table class="report-table"><thead><tr>'
            + '<th>상품명</th><th class="text-right">MAPE</th><th class="text-right">편향</th><th class="text-right">건수</th>'
            + '</tr></thead><tbody>';
        best.forEach(function(b) {
            html += '<tr>'
                + '<td>' + esc(b.item_nm || b.item_cd) + '</td>'
                + '<td class="text-right" style="color:var(--success)">' + (b.mape != null ? Math.round(b.mape) + '%' : '-') + '</td>'
                + '<td class="text-right">' + (b.bias != null ? (b.bias > 0 ? '+' : '') + b.bias.toFixed(1) : '-') + '</td>'
                + '<td class="text-right">' + (b.sample_count || 0) + '</td>'
                + '</tr>';
        });
        html += '</tbody></table></div>';
    } else {
        html += '<div class="hint" style="padding:8px;color:var(--text-muted)">데이터 없음</div>';
    }
    html += '</div>';

    body.innerHTML = html;
}

// === ML 모델 그리드 ===
function renderMLGrid(mlStatus) {
    var grid = document.getElementById('predMLGrid');
    if (!grid || !mlStatus) return;

    var groups = mlStatus.groups || {};
    var groupNames = {
        "food_group": "푸드",
        "alcohol_group": "주류",
        "tobacco_group": "담배",
        "perishable_group": "신선",
        "general_group": "일반",
    };

    var keys = Object.keys(groupNames);
    if (keys.length === 0) {
        grid.innerHTML = '<div class="hint" style="padding:32px;text-align:center">ML 모델 정보 없음</div>';
        return;
    }

    var html = '';
    keys.forEach(function(key) {
        var g = groups[key] || {};
        var hasModel = g.has_model || false;
        var statusClass = hasModel ? 'pred-ml-card-active' : 'pred-ml-card-inactive';
        var statusText = hasModel ? '활성' : '미학습';
        var statusColor = hasModel ? 'var(--success)' : 'var(--text-muted)';
        var modified = g.modified ? g.modified.substring(0, 10) : '-';

        html += '<div class="pred-ml-card ' + statusClass + '">'
            + '<div class="pred-ml-card-header">'
            + '<span class="pred-ml-card-name">' + (groupNames[key] || key) + '</span>'
            + '<span class="pred-ml-card-status" style="color:' + statusColor + '">' + statusText + '</span>'
            + '</div>'
            + '<div class="pred-ml-card-body">'
            + '<div class="pred-ml-stat"><span class="pred-ml-stat-label">그룹</span><span class="pred-ml-stat-value">' + key.replace('_group', '') + '</span></div>'
            + '<div class="pred-ml-stat"><span class="pred-ml-stat-label">최근 학습</span><span class="pred-ml-stat-value">' + modified + '</span></div>'
            + '</div>'
            + '</div>';
    });

    grid.innerHTML = html;
}

// === 블렌딩 상세 서브뷰 ===
async function loadBlendingDetail() {
    if (window._predBlendingLoaded) return;

    try {
        var data = await api('/api/prediction/blending-detail?store_id=' + _currentStoreId);
        renderBlendDiagram(data.blending_rules);
        renderDonutChart(data.model_type_dist);
        renderEvalConfigTable(data.eval_config_params);
        renderCalibrationTable(data.calibration_history);
        window._predBlendingLoaded = true;
    } catch (e) {
        console.error('Blending detail load failed:', e);
    }
}

function renderBlendDiagram(rules) {
    var el = document.getElementById('predBlendDiagram');
    if (!el || !rules) return;

    var html = '<div class="pred-blend-list">';
    var ruleKeys = ['rule', 'ensemble_30', 'ensemble_50'];

    ruleKeys.forEach(function(key) {
        var r = rules[key];
        if (!r) return;
        var mlW = Math.round((r.ml_weight || 0) * 100);
        var ruleW = 100 - mlW;

        html += '<div class="pred-blend-item">'
            + '<div class="pred-blend-label">' + esc(r.label) + '</div>'
            + '<div class="pred-blend-condition">' + esc(r.condition) + '</div>'
            + '<div class="pred-blend-bar">'
            + '<div class="pred-blend-bar-rule" style="width:' + ruleW + '%"><span>' + (ruleW > 15 ? 'Rule ' + ruleW + '%' : '') + '</span></div>'
            + '<div class="pred-blend-bar-ml" style="width:' + mlW + '%"><span>' + (mlW > 15 ? 'ML ' + mlW + '%' : '') + '</span></div>'
            + '</div>'
            + '</div>';
    });

    html += '</div>';
    el.innerHTML = html;
}

function renderDonutChart(dist) {
    var canvas = document.getElementById('predDonutChart');
    if (!canvas || !dist) return;

    var total = dist.total || 0;
    if (total === 0) return;

    getOrCreateChart('predDonutChart', {
        type: 'doughnut',
        data: {
            labels: ['규칙 기반', '앙상블 30%', '앙상블 50%'],
            datasets: [{
                data: [dist.rule || 0, dist.ensemble_30 || 0, dist.ensemble_50 || 0],
                backgroundColor: [getChartColors().blue, getChartColors().green, getChartColors().yellow],
                borderWidth: 0,
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            var val = ctx.parsed || 0;
                            var pct = total > 0 ? Math.round(val / total * 100) : 0;
                            return ctx.label + ': ' + fmt(val) + '건 (' + pct + '%)';
                        },
                    },
                },
            },
        },
    });
}

function renderEvalConfigTable(params) {
    var tbody = document.getElementById('predEvalConfigBody');
    if (!tbody) return;

    if (!params || params.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center" style="padding:24px;color:var(--text-muted)">파라미터 없음</td></tr>';
        return;
    }

    tbody.innerHTML = params.map(function(p) {
        var changed = p.value != null && p.default != null && p.value !== p.default;
        var style = changed ? 'font-weight:600;color:var(--warning)' : '';
        return '<tr>'
            + '<td>' + esc(p.name) + '</td>'
            + '<td class="text-right" style="' + style + '">' + (p.value != null ? p.value : '-') + '</td>'
            + '<td class="text-right">' + (p.default != null ? p.default : '-') + '</td>'
            + '<td style="font-size:12px;color:var(--text-secondary)">' + esc(p.desc || '') + '</td>'
            + '</tr>';
    }).join('');
}

function renderCalibrationTable(history) {
    var tbody = document.getElementById('predCalibrationBody');
    if (!tbody) return;

    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center" style="padding:24px;color:var(--text-muted)">보정 이력 없음</td></tr>';
        return;
    }

    tbody.innerHTML = history.map(function(h) {
        return '<tr>'
            + '<td>' + esc(h.date || '-') + '</td>'
            + '<td>' + esc(h.param || '-') + '</td>'
            + '<td class="text-right">' + (h.old != null ? h.old.toFixed(3) : '-') + '</td>'
            + '<td class="text-right">' + (h.new != null ? h.new.toFixed(3) : '-') + '</td>'
            + '<td style="font-size:12px;color:var(--text-secondary)">' + esc(h.reason || '-') + '</td>'
            + '</tr>';
    }).join('');
}

// === 서브탭 전환 ===
document.querySelectorAll('.pred-type-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
        var target = btn.dataset.predView;

        // 버튼 active 토글
        document.querySelectorAll('.pred-type-btn').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');

        // 뷰 active 토글
        document.querySelectorAll('.pred-view').forEach(function(v) { v.classList.remove('active'); });
        var view = document.getElementById('pred-view-' + target);
        if (view) view.classList.add('active');

        // lazy load
        if (target === 'accuracy' && !window._predAccuracyLoaded) {
            loadAccuracyDetail();
        }
        if (target === 'blending' && !window._predBlendingLoaded) {
            loadBlendingDetail();
        }
    });
});

// === HTML 이스케이프 ===
function esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
