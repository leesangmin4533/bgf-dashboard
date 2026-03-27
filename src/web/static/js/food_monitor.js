/* 푸드 발주 최적화 모니터링 대시보드 - 쉬운 버전 */

var _fmLoaded = false;
var _fmCharts = {};

async function loadFoodMonitor() {
    if (_fmLoaded) return;
    _fmLoaded = true;
    await loadFoodOverview();
    await loadLogCounts();
}

// === 매장 변경 시 리로드 ===
function resetFoodMonitor() {
    _fmLoaded = false;
    Object.values(_fmCharts).forEach(c => { if (c) c.destroy(); });
    _fmCharts = {};
}

// === 판정 함수 ===
function judgeStockout(v) {
    if (v == null) return { emoji: '⏳', text: '데이터 없음', cls: 'fm-gray' };
    if (v <= 40) return { emoji: '😊', text: '좋아요!', cls: 'fm-good' };
    if (v <= 60) return { emoji: '😐', text: '주의', cls: 'fm-warn' };
    return { emoji: '😰', text: '위험!', cls: 'fm-bad' };
}
function judgeWaste(v) {
    if (v == null) return { emoji: '⏳', text: '데이터 없음', cls: 'fm-gray' };
    if (v <= 15) return { emoji: '😊', text: '좋아요!', cls: 'fm-good' };
    if (v <= 25) return { emoji: '😐', text: '주의', cls: 'fm-warn' };
    return { emoji: '😰', text: '위험!', cls: 'fm-bad' };
}
function judgeBias(v) {
    if (v == null) return { emoji: '⏳', text: '데이터 없음', cls: 'fm-gray' };
    if (v > -0.15) return { emoji: '😊', text: '정확해요!', cls: 'fm-good' };
    if (v >= -0.30) return { emoji: '😐', text: '주의', cls: 'fm-warn' };
    return { emoji: '😰', text: '많이 부족!', cls: 'fm-bad' };
}
function judgeConflict(v) {
    if (v == null) return { emoji: '⏳', text: '데이터 없음', cls: 'fm-gray' };
    if (v < 20) return { emoji: '😊', text: '좋아요!', cls: 'fm-good' };
    if (v <= 40) return { emoji: '😐', text: '주의', cls: 'fm-warn' };
    return { emoji: '😰', text: '위험!', cls: 'fm-bad' };
}

function changeArrow(before, after, lowerIsBetter) {
    if (before == null || after == null) return '';
    var diff = after - before;
    if (Math.abs(diff) < 0.5) return '<span class="fm-flat">→ 변화없음</span>';
    var improved = lowerIsBetter ? diff < 0 : diff > 0;
    if (improved) return '<span class="fm-improve">▼ ' + Math.abs(diff).toFixed(1) + '%p 개선</span>';
    return '<span class="fm-worse">▲ ' + Math.abs(diff).toFixed(1) + '%p 악화</span>';
}

// === 카테고리 아이콘 ===
var catIcons = {
    '001': '🍱', '002': '🍙', '003': '🍣',
    '004': '🥪', '005': '🍔', '012': '🍞',
};

// === 메인 데이터 로드 ===
async function loadFoodOverview() {
    var container = document.getElementById('fmContent');
    if (!container) return;
    container.innerHTML = '<div class="fm-loading"><div class="fm-spinner"></div><p>데이터를 불러오는 중...</p></div>';

    try {
        var data = await api('/api/food-monitor/overview' + storeParam());
        renderFoodDashboard(container, data);
    } catch (e) {
        container.innerHTML = '<div class="fm-error">데이터를 불러올 수 없습니다. 잠시 후 다시 시도해주세요.</div>';
    }
}

async function loadLogCounts() {
    try {
        var data = await api('/api/food-monitor/log-counts');
        renderLogCounts(data);
    } catch (e) {
        // silent fail
    }
}

// === 전체 대시보드 렌더링 ===
function renderFoodDashboard(container, data) {
    var cats = data.categories || [];
    var daysSince = data.days_since || 0;
    var deployDate = data.deploy_date || '2026-03-03';

    // 종합 점수 계산
    var totalScore = calcTotalScore(cats);

    var html = '';

    // ① 상단 종합 카드
    html += renderSummaryHeader(daysSince, deployDate, totalScore, cats);

    // ② "이게 뭔가요?" 설명 카드
    html += renderExplanation();

    // ③ 카테고리별 성적표
    html += '<h3 class="fm-section-title">📋 카테고리별 성적표</h3>';
    html += '<div class="fm-card-grid">';
    cats.forEach(function(cat) {
        html += renderCategoryCard(cat);
    });
    html += '</div>';

    // ④ 전후 비교 차트
    html += '<h3 class="fm-section-title">📊 배포 전후 비교</h3>';
    html += renderComparisonCharts(cats);

    // ⑤ 수익 시뮬레이션
    if (data.simulation) {
        html += '<h3 class="fm-section-title">💰 수익 분석</h3>';
        html += renderSimulation(data.simulation);
    }

    // ⑥ 로그 카운트 (placeholder)
    html += '<h3 class="fm-section-title">🔧 시스템 작동 현황</h3>';
    html += '<div class="fm-log-counts" id="fmLogCounts"><div class="fm-loading"><div class="fm-spinner"></div></div></div>';

    // ⑦ 종합 판정
    html += renderVerdict(cats, data.store_id);

    container.innerHTML = html;

    // 차트 그리기
    setTimeout(function() { drawComparisonCharts(cats); }, 100);
}

// === 종합 점수 ===
function calcTotalScore(cats) {
    var scores = [];
    cats.forEach(function(cat) {
        var a = cat.after;
        if (a.stockout != null) scores.push(a.stockout <= 40 ? 100 : a.stockout <= 60 ? 60 : 20);
        if (a.waste != null) scores.push(a.waste <= 15 ? 100 : a.waste <= 25 ? 60 : 20);
        if (a.bias != null) scores.push(a.bias > -0.15 ? 100 : a.bias >= -0.30 ? 60 : 20);
    });
    if (scores.length === 0) return null;
    return Math.round(scores.reduce(function(a, b) { return a + b; }, 0) / scores.length);
}

// === ① 종합 헤더 ===
function renderSummaryHeader(daysSince, deployDate, score, cats) {
    var scoreEmoji = score == null ? '⏳' : score >= 80 ? '🎉' : score >= 50 ? '💪' : '⚠️';
    var scoreText = score == null ? '데이터 수집 중' : score >= 80 ? '아주 좋아요!' : score >= 50 ? '노력 중이에요' : '개선이 필요해요';
    var scoreColor = score == null ? 'fm-gray' : score >= 80 ? 'fm-good' : score >= 50 ? 'fm-warn' : 'fm-bad';

    // 데이터 충분성
    var dataNote = '';
    if (daysSince < 3) {
        dataNote = '<div class="fm-data-warning">⚠️ 아직 ' + daysSince + '일밖에 안 됐어요. 최소 3일은 지나야 정확한 판단이 가능해요!</div>';
    } else if (daysSince < 7) {
        dataNote = '<div class="fm-data-info">📝 ' + daysSince + '일째 모니터링 중. 7일이 되면 더 정확한 분석을 할 수 있어요.</div>';
    }

    return '<div class="fm-summary-card">'
        + '<div class="fm-summary-top">'
        + '<div class="fm-summary-score ' + scoreColor + '">'
        + '<span class="fm-big-emoji">' + scoreEmoji + '</span>'
        + '<span class="fm-score-number">' + (score != null ? score + '점' : '-') + '</span>'
        + '<span class="fm-score-label">' + scoreText + '</span>'
        + '</div>'
        + '<div class="fm-summary-info">'
        + '<div class="fm-summary-title">푸드 발주 최적화 모니터링</div>'
        + '<div class="fm-summary-meta">'
        + '<span class="fm-badge">배포일: ' + deployDate + '</span>'
        + '<span class="fm-badge fm-badge-accent">' + daysSince + '일 경과</span>'
        + '</div>'
        + '<p class="fm-summary-desc">품절은 줄이고, 버리는 건 줄이고, 수익은 늘리는 게 목표예요!</p>'
        + dataNote
        + '</div>'
        + '</div>'
        + '</div>';
}

// === ② 설명 카드 ===
function renderExplanation() {
    return '<div class="fm-explain-card">'
        + '<div class="fm-explain-toggle" onclick="toggleExplain()">'
        + '<span>🤔 이게 뭔가요? (클릭해서 알아보기)</span>'
        + '<span class="fm-chevron" id="fmChevron">▼</span>'
        + '</div>'
        + '<div class="fm-explain-body" id="fmExplainBody" style="display:none">'
        + '<div class="fm-explain-item">'
        + '<div class="fm-explain-icon">🏪</div>'
        + '<div><strong>품절률</strong> = 손님이 사려고 했는데 상품이 없었던 비율<br>'
        + '<em>낮을수록 좋아요! 40% 이하가 목표</em></div>'
        + '</div>'
        + '<div class="fm-explain-item">'
        + '<div class="fm-explain-icon">🗑️</div>'
        + '<div><strong>폐기율</strong> = 팔리지 않고 버려진 상품의 비율<br>'
        + '<em>낮을수록 좋아요! 15% 이하가 목표</em></div>'
        + '</div>'
        + '<div class="fm-explain-item">'
        + '<div class="fm-explain-icon">🎯</div>'
        + '<div><strong>예측 편향</strong> = 컴퓨터가 예측한 수량과 실제 판매량의 차이<br>'
        + '<em>0에 가까울수록 정확해요! -0.15 이상이 목표</em></div>'
        + '</div>'
        + '<div class="fm-explain-item">'
        + '<div class="fm-explain-icon">⚡</div>'
        + '<div><strong>동시발생</strong> = 같은 날 인기상품은 품절인데 비인기상품은 버린 날<br>'
        + '<em>이런 날이 적을수록 발주를 잘하고 있는 거예요!</em></div>'
        + '</div>'
        + '</div>'
        + '</div>';
}

function toggleExplain() {
    var body = document.getElementById('fmExplainBody');
    var chevron = document.getElementById('fmChevron');
    if (body.style.display === 'none') {
        body.style.display = 'block';
        chevron.textContent = '▲';
    } else {
        body.style.display = 'none';
        chevron.textContent = '▼';
    }
}

// === ③ 카테고리 카드 ===
function renderCategoryCard(cat) {
    var icon = catIcons[cat.mid_cd] || '📦';
    var a = cat.after;
    var b = cat.before;

    var so = judgeStockout(a.stockout);
    var wa = judgeWaste(a.waste);
    var bi = judgeBias(a.bias);

    return '<div class="fm-cat-card">'
        + '<div class="fm-cat-header">'
        + '<span class="fm-cat-icon">' + icon + '</span>'
        + '<span class="fm-cat-name">' + cat.name + '</span>'
        + '</div>'
        + '<div class="fm-cat-metrics">'
        // 품절률
        + '<div class="fm-metric-row">'
        + '<div class="fm-metric-label">🏪 품절률</div>'
        + '<div class="fm-metric-value ' + so.cls + '">'
        + (a.stockout != null ? a.stockout + '%' : '-') + ' ' + so.emoji
        + '</div>'
        + '<div class="fm-metric-change">' + changeArrow(b.stockout, a.stockout, true) + '</div>'
        + '</div>'
        // 폐기율
        + '<div class="fm-metric-row">'
        + '<div class="fm-metric-label">🗑️ 폐기율</div>'
        + '<div class="fm-metric-value ' + wa.cls + '">'
        + (a.waste != null ? a.waste + '%' : '-') + ' ' + wa.emoji
        + '</div>'
        + '<div class="fm-metric-change">' + changeArrow(b.waste, a.waste, true) + '</div>'
        + '</div>'
        // 예측 편향
        + '<div class="fm-metric-row">'
        + '<div class="fm-metric-label">🎯 예측 정확도</div>'
        + '<div class="fm-metric-value ' + bi.cls + '">'
        + (a.bias != null ? a.bias.toFixed(2) : '-') + ' ' + bi.emoji
        + '</div>'
        + '</div>'
        // 동시발생
        + '<div class="fm-metric-row">'
        + '<div class="fm-metric-label">⚡ 동시발생</div>'
        + '<div class="fm-metric-value ' + judgeConflict(a.conflict_pct).cls + '">'
        + (a.conflict_pct != null ? a.conflict_pct + '%' : '-') + ' ' + judgeConflict(a.conflict_pct).emoji
        + '</div>'
        + '</div>'
        + '</div>'
        + '</div>';
}

// === ④ 비교 차트 ===
function renderComparisonCharts(cats) {
    return '<div class="fm-chart-row">'
        + '<div class="fm-chart-box"><canvas id="fmChartStockout"></canvas></div>'
        + '<div class="fm-chart-box"><canvas id="fmChartWaste"></canvas></div>'
        + '</div>';
}

function drawComparisonCharts(cats) {
    var labels = cats.map(function(c) { return c.name; });
    var beforeStockout = cats.map(function(c) { return c.before.stockout || 0; });
    var afterStockout = cats.map(function(c) { return c.after.stockout || 0; });
    var beforeWaste = cats.map(function(c) { return c.before.waste || 0; });
    var afterWaste = cats.map(function(c) { return c.after.waste || 0; });

    var styles = getComputedStyle(document.documentElement);
    var textColor = styles.getPropertyValue('--text-secondary').trim() || '#94a3b8';
    var gridColor = styles.getPropertyValue('--chart-grid').trim() || 'rgba(255,255,255,0.06)';

    // 품절률 차트
    var ctx1 = document.getElementById('fmChartStockout');
    if (ctx1) {
        if (_fmCharts.stockout) _fmCharts.stockout.destroy();
        _fmCharts.stockout = new Chart(ctx1, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: '배포 전', data: beforeStockout, backgroundColor: 'rgba(248,113,113,0.6)', borderRadius: 6 },
                    { label: '배포 후', data: afterStockout, backgroundColor: 'rgba(74,222,128,0.6)', borderRadius: 6 },
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    title: { display: true, text: '🏪 품절률 비교 (낮을수록 좋아요)', color: textColor, font: { size: 14 } },
                    legend: { labels: { color: textColor } },
                    annotation: {
                        annotations: {
                            target: { type: 'line', yMin: 40, yMax: 40, borderColor: '#eab308', borderWidth: 2, borderDash: [6,3], label: { display: true, content: '목표 40%', color: '#eab308', position: 'end' } }
                        }
                    }
                },
                scales: {
                    y: { beginAtZero: true, max: 100, ticks: { color: textColor, callback: function(v) { return v + '%'; } }, grid: { color: gridColor } },
                    x: { ticks: { color: textColor }, grid: { display: false } },
                }
            }
        });
    }

    // 폐기율 차트
    var ctx2 = document.getElementById('fmChartWaste');
    if (ctx2) {
        if (_fmCharts.waste) _fmCharts.waste.destroy();
        _fmCharts.waste = new Chart(ctx2, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: '배포 전', data: beforeWaste, backgroundColor: 'rgba(248,113,113,0.6)', borderRadius: 6 },
                    { label: '배포 후', data: afterWaste, backgroundColor: 'rgba(74,222,128,0.6)', borderRadius: 6 },
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    title: { display: true, text: '🗑️ 폐기율 비교 (낮을수록 좋아요)', color: textColor, font: { size: 14 } },
                    legend: { labels: { color: textColor } },
                },
                scales: {
                    y: { beginAtZero: true, ticks: { color: textColor, callback: function(v) { return v + '%'; } }, grid: { color: gridColor } },
                    x: { ticks: { color: textColor }, grid: { display: false } },
                }
            }
        });
    }
}

// === ⑤ 수익 시뮬레이션 ===
function renderSimulation(sim) {
    var before = sim.before || {};
    var after = sim.after || {};

    var html = '<div class="fm-sim-card">'
        + '<p class="fm-sim-desc">💡 <strong>최적 발주량</strong>이란? 수익을 가장 많이 내면서 폐기를 최소로 하는 발주 수량이에요.</p>'
        + '<div class="report-table-wrapper"><table class="report-table fm-sim-table">'
        + '<thead><tr>'
        + '<th>카테고리</th>'
        + '<th class="text-right">일평균 수요</th>'
        + '<th class="text-right">실제 발주</th>'
        + '<th class="text-right">최적 발주</th>'
        + '<th class="text-right">실제 수익/일</th>'
        + '<th class="text-right">최적 수익/일</th>'
        + '<th class="text-right">차이</th>'
        + '</tr></thead><tbody>';

    var totalGap = 0;

    // Use 'before' data (더 많은 기간이므로)
    var data = Object.keys(before).length > 0 ? before : after;

    Object.keys(CATEGORY_PRICES_JS).forEach(function(mid_cd) {
        var d = data[mid_cd];
        if (!d) return;
        var icon = catIcons[mid_cd] || '';
        var gapCls = d.profit_gap > 0 ? 'fm-worse' : d.profit_gap < -100 ? 'fm-improve' : '';
        totalGap += d.profit_gap;

        html += '<tr>'
            + '<td>' + icon + ' ' + d.name + '</td>'
            + '<td class="text-right">' + d.avg_demand + '</td>'
            + '<td class="text-right">' + d.avg_order + '</td>'
            + '<td class="text-right"><strong>' + d.opt_qty + '</strong></td>'
            + '<td class="text-right">' + d.actual_profit.toLocaleString() + '원</td>'
            + '<td class="text-right">' + d.opt_profit.toLocaleString() + '원</td>'
            + '<td class="text-right ' + gapCls + '">'
            + (d.profit_gap >= 0 ? '+' : '') + d.profit_gap.toLocaleString() + '원</td>'
            + '</tr>';
    });

    html += '</tbody></table></div>';

    if (totalGap !== 0) {
        var msg = totalGap > 0
            ? '최적 발주를 하면 하루에 약 <strong>' + totalGap.toLocaleString() + '원</strong> 더 벌 수 있어요!'
            : '현재 발주가 최적보다 하루에 약 <strong>' + Math.abs(totalGap).toLocaleString() + '원</strong> 더 벌고 있어요! 잘하고 있어요!';
        html += '<div class="fm-sim-summary">' + (totalGap > 0 ? '📈' : '🎉') + ' ' + msg + '</div>';
    }

    html += '</div>';
    return html;
}

var CATEGORY_PRICES_JS = {
    '001': { name: '도시락' }, '002': { name: '삼각김밥' }, '003': { name: '김밥' },
    '004': { name: '샌드위치' }, '005': { name: '햄버거' }, '012': { name: '빵' },
};

// === ⑥ 로그 카운트 ===
function renderLogCounts(data) {
    var container = document.getElementById('fmLogCounts');
    if (!container) return;

    var items = [
        { key: 'exempt', icon: '🛡️', label: '폐기계수 면제', desc: '품절 심한 상품의 폐기 감량을 풀어준 횟수' },
        { key: 'relax', icon: '🔓', label: '폐기계수 완화', desc: '폐기 감량을 약하게 적용한 횟수' },
        { key: 'floor', icon: '📏', label: '최종 하한 적용', desc: '발주량이 너무 적을 때 최소량을 보장한 횟수' },
        { key: 'boost', icon: '🚀', label: '품절 부스트', desc: '품절이 잦은 상품의 발주를 늘린 횟수' },
    ];

    var html = '<div class="fm-log-grid">';
    items.forEach(function(item) {
        var count = data[item.key] || 0;
        html += '<div class="fm-log-item">'
            + '<div class="fm-log-icon">' + item.icon + '</div>'
            + '<div class="fm-log-info">'
            + '<div class="fm-log-label">' + item.label + '</div>'
            + '<div class="fm-log-count">' + count + '회</div>'
            + '<div class="fm-log-desc">' + item.desc + '</div>'
            + '</div>'
            + '</div>';
    });
    html += '</div>';

    if (Object.values(data).every(function(v) { return v === 0; })) {
        html += '<div class="fm-data-info">ℹ️ 아직 시스템이 작동하지 않았거나, 배포 후 발주가 아직 실행되지 않았어요.</div>';
    }

    container.innerHTML = html;
}

// === ⑦ 종합 판정 ===
function renderVerdict(cats, storeId) {
    var issues = [];
    var goods = [];

    cats.forEach(function(cat) {
        var a = cat.after;
        if (a.stockout != null && a.stockout > 60)
            issues.push(cat.name + ' 품절률이 ' + a.stockout + '%로 높아요 → 발주량을 늘려야 해요');
        if (a.waste != null && a.waste > 25)
            issues.push(cat.name + ' 폐기율이 ' + a.waste + '%로 높아요 → 발주량을 줄여야 해요');
        if (a.bias != null && a.bias < -0.30)
            issues.push(cat.name + ' 예측이 많이 부족해요 (편향: ' + a.bias.toFixed(2) + ') → 부스트 배율 상향 검토');
        if (a.stockout != null && a.stockout <= 40)
            goods.push(cat.name + ' 품절률이 목표 이내에요!');
        if (a.waste != null && a.waste <= 15)
            goods.push(cat.name + ' 폐기율이 목표 이내에요!');
    });

    var overallEmoji = issues.length === 0 ? '✅' : issues.length <= 2 ? '⚠️' : '🚨';
    var overallText = issues.length === 0 ? '정상' : issues.length <= 2 ? '주의' : '조치 필요';
    var overallCls = issues.length === 0 ? 'fm-good' : issues.length <= 2 ? 'fm-warn' : 'fm-bad';

    var html = '<div class="fm-verdict-card">'
        + '<h3 class="fm-section-title">📝 종합 판정</h3>'
        + '<div class="fm-verdict-header ' + overallCls + '">'
        + '<span class="fm-big-emoji">' + overallEmoji + '</span>'
        + '<span class="fm-verdict-text">매장 ' + storeId + ': <strong>' + overallText + '</strong></span>'
        + '</div>';

    if (issues.length > 0) {
        html += '<div class="fm-verdict-section fm-verdict-issues">'
            + '<h4>🔴 개선이 필요한 부분</h4><ul>';
        issues.forEach(function(i) { html += '<li>' + i + '</li>'; });
        html += '</ul></div>';
    }

    if (goods.length > 0) {
        html += '<div class="fm-verdict-section fm-verdict-goods">'
            + '<h4>🟢 잘하고 있는 부분</h4><ul>';
        goods.forEach(function(i) { html += '<li>' + i + '</li>'; });
        html += '</ul></div>';
    }

    html += '</div>';
    return html;
}
