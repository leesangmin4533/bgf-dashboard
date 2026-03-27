/* BGF 발주 시스템 - 발주 성적표 (prediction-dashboard 개선) */
console.log('[PRED] prediction.js loaded');

// === Lazy load 플래그 ===
window._predAccuracyLoaded = false;

// === 점수 변환 유틸 ===
function toScore(v) { return Math.round(Math.max(0, Math.min(100, v || 0))); }

function toSignal(score) {
    if (score >= 80) return { cls: 'signal-good', label: '좋음' };
    if (score >= 65) return { cls: 'signal-warn', label: '보통' };
    return { cls: 'signal-bad', label: '주의' };
}

function esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// === 요약 카드 로드 ===
async function loadPredSummary() {
    console.log('[PRED] loadPredSummary called');
    try {
        var data = await api('/api/prediction/summary?store_id=' + _currentStoreId);
        renderScoreHero(data.qty_accuracy, data.eval_accuracy);
        renderAlerts(data.alerts || []);
        // admin 전용 카드
        renderPredMLStatus(data.ml_status);
        renderPredModelMix(data.model_type_dist);
    } catch (e) {
        console.error('Prediction summary load failed:', e);
    }
}

// === 섹션 1: 종합 성적표 ===
function renderScoreHero(qty, eval_acc) {
    var ringEl = document.getElementById('predScoreRing');
    var sumEl = document.getElementById('predScoreSummary');
    if (!ringEl || !sumEl) return;

    var score = toScore(qty ? qty.accuracy_within_2 : 0);
    var sig = toSignal(score);
    var total = qty ? qty.total : 0;

    if (total === 0) {
        ringEl.innerHTML = '<div style="text-align:center;color:var(--text-muted)">-</div>';
        sumEl.innerHTML = '<div style="color:var(--text-muted)">검증 데이터 없음</div>';
        return;
    }

    // 프로그레스 링
    var radius = 46;
    var circumference = 2 * Math.PI * radius;
    var offset = circumference - (score / 100) * circumference;

    ringEl.innerHTML =
        '<div class="score-ring-container">' +
        '<svg class="score-ring-svg" width="108" height="108" viewBox="0 0 108 108">' +
        '<circle class="score-ring-bg" cx="54" cy="54" r="' + radius + '"/>' +
        '<circle class="score-ring-fill ' + sig.cls + '-stroke" cx="54" cy="54" r="' + radius + '" ' +
        'stroke-dasharray="' + circumference + '" stroke-dashoffset="' + offset + '"/>' +
        '</svg>' +
        '<div class="score-ring-text">' +
        '<span class="score-ring-num">' + score + '</span>' +
        '<span class="score-ring-unit">점</span>' +
        '</div>' +
        '</div>';

    // 요약 텍스트
    var overCount = qty ? Math.round(total * (qty.over_rate || 0) / 100) : 0;
    var underCount = qty ? Math.round(total * (qty.under_rate || 0) / 100) : 0;

    sumEl.innerHTML =
        '<span class="signal-badge ' + sig.cls + '">' + sig.label + '</span>' +
        '<p class="score-desc">최근 7일간 발주 <strong>' + fmt(total) + '건</strong> 중 ' +
        '<strong>' + fmt(Math.round(total * score / 100)) + '건</strong>이 ' +
        '실제 판매량과 <strong>2개 이내</strong>로 맞았습니다.</p>' +
        '<div class="score-stats">' +
        '<span class="score-stat-over">많이 시킨 상품 ' + fmt(overCount) + '건</span>' +
        '<span class="score-stat-under">적게 시킨 상품 ' + fmt(underCount) + '건</span>' +
        '</div>';
}

// === 적중률 상세 서브뷰 ===
async function loadAccuracyDetail() {
    if (window._predAccuracyLoaded) return;

    try {
        var data = await api('/api/prediction/accuracy-detail?store_id=' + _currentStoreId);
        renderScoreChart(data.daily_mape_trend);
        renderTrendChange(data.daily_mape_trend);
        renderCategorySignals(data.category_accuracy);
        renderAttentionItems(data.worst_items, data.best_items);
        window._predAccuracyLoaded = true;
    } catch (e) {
        console.error('Accuracy detail load failed:', e);
    }
}

// === hex → rgba 변환 유틸 ===
function hexToRgba(hex, alpha) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    var r = parseInt(hex.substring(0, 2), 16);
    var g = parseInt(hex.substring(2, 4), 16);
    var b = parseInt(hex.substring(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
}

// === 섹션 2: 주간 추이 바 차트 ===
function renderScoreChart(trend) {
    var canvas = document.getElementById('predScoreChart');
    if (!canvas || !trend || trend.length === 0) return;

    var styles = getComputedStyle(document.documentElement);
    var signalColors = {
        good: styles.getPropertyValue('--success').trim() || '#22c55e',
        warn: styles.getPropertyValue('--warning').trim() || '#eab308',
        bad:  styles.getPropertyValue('--danger').trim()  || '#ef4444'
    };

    var labels = trend.map(function(t) { return t.date ? t.date.substring(5) : ''; });
    var scores = trend.map(function(t) { return toScore(100 - (t.smape || 0)); });
    var colors = scores.map(function(s) {
        var sig = toSignal(s);
        if (sig.cls === 'signal-good') return hexToRgba(signalColors.good, 0.7);
        if (sig.cls === 'signal-warn') return hexToRgba(signalColors.warn, 0.7);
        return hexToRgba(signalColors.bad, 0.7);
    });

    var gridColor = styles.getPropertyValue('--chart-grid').trim();

    getOrCreateChart('predScoreChart', {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: '점수',
                data: scores,
                backgroundColor: colors,
                borderRadius: 4,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                annotation: {
                    annotations: {
                        baseline: {
                            type: 'line', yMin: 80, yMax: 80,
                            borderColor: hexToRgba(signalColors.good, 0.5), borderWidth: 2, borderDash: [6, 3],
                            label: { content: '80점', display: true, position: 'start', font: { size: 11 } },
                        },
                    },
                },
            },
            scales: {
                y: {
                    min: 0, max: 100,
                    title: { display: true, text: '점수' },
                    grid: { color: gridColor },
                },
                x: { grid: { display: false } },
            },
        },
    });
}

// === 섹션 2: 전주 대비 변화 텍스트 ===
function renderTrendChange(trend) {
    var el = document.getElementById('predTrendChange');
    if (!el || !trend || trend.length < 2) { if (el) el.textContent = ''; return; }

    var mid = Math.floor(trend.length / 2);
    var firstHalf = trend.slice(0, mid);
    var secondHalf = trend.slice(mid);

    function avgScore(arr) {
        if (arr.length === 0) return 0;
        var sum = 0;
        arr.forEach(function(t) { sum += toScore(100 - (t.smape || 0)); });
        return Math.round(sum / arr.length);
    }

    var prevAvg = avgScore(firstHalf);
    var currAvg = avgScore(secondHalf);
    var diff = currAvg - prevAvg;

    if (diff > 0) {
        el.innerHTML = '<span style="color:var(--success)">지난주 대비 ' + diff + '점 상승</span>';
    } else if (diff < 0) {
        el.innerHTML = '<span style="color:var(--danger)">지난주 대비 ' + Math.abs(diff) + '점 하락</span>';
    } else {
        el.textContent = '지난주와 동일';
    }
}

// === 섹션 3: 카테고리별 신호등 카드 ===
function getGrade(score) {
    if (score >= 80) return 'good';
    if (score >= 65) return 'normal';
    return 'warning';
}

function renderCategorySignals(cats) {
    var el = document.getElementById('predCategorySignals');
    if (!el) return;

    if (!cats || cats.length === 0) {
        el.innerHTML = '<div style="padding:16px;color:var(--text-muted)">카테고리 데이터 없음</div>';
        return;
    }

    // grade 그룹핑: warning → normal → good, 같은 grade 내 API 순서 유지
    var gradeOrder = { warning: 0, normal: 1, good: 2 };
    var sorted = cats.slice().map(function(c, idx) {
        var score = toScore(c.accuracy_within_2 || 0);
        return { data: c, score: score, grade: getGrade(score), origIdx: idx };
    });
    sorted.sort(function(a, b) {
        var ga = gradeOrder[a.grade] !== undefined ? gradeOrder[a.grade] : 2;
        var gb = gradeOrder[b.grade] !== undefined ? gradeOrder[b.grade] : 2;
        if (ga !== gb) return ga - gb;
        return a.origIdx - b.origIdx;
    });

    var html = '';
    sorted.forEach(function(item) {
        var c = item.data;
        var score = item.score;
        var sig = toSignal(score);
        var isWarning = item.grade === 'warning';
        var cardCls = 'category-signal-card ' + sig.cls + '-card';
        if (isWarning) cardCls += ' category-signal-warning';

        html += '<div class="' + cardCls + '"' +
            (isWarning ? ' onclick="toggleCategoryDetail(this)" style="cursor:pointer"' : '') + '>';
        html += '<div class="category-signal-name">' + esc(c.mid_nm || c.mid_cd);
        if (isWarning) html += ' <span class="category-toggle">▸</span>';
        html += '</div>';
        html += '<div class="category-signal-score">' + score + '<small>점</small></div>';
        html += '<span class="signal-badge ' + sig.cls + '">' + sig.label + '</span>';
        if (isWarning) {
            var detail = esc(c.mid_nm || c.mid_cd) + ' 정확도가 ' + score + '점으로 낮습니다. 해당 카테고리 재고를 확인해주세요.';
            html += '<div class="category-detail" style="display:none">' + detail + '</div>';
        }
        html += '</div>';
    });
    el.innerHTML = html;
}

function toggleCategoryDetail(card) {
    var detail = card.querySelector('.category-detail');
    var toggle = card.querySelector('.category-toggle');
    if (!detail) return;
    if (detail.style.display === 'none') {
        detail.style.display = 'block';
        if (toggle) toggle.textContent = '▾';
    } else {
        detail.style.display = 'none';
        if (toggle) toggle.textContent = '▸';
    }
}

// === 섹션 4: 주의 필요 상품 + 잘 맞는 상품 ===
function renderAttentionItems(worst, best) {
    var el = document.getElementById('predAttentionItems');
    if (!el) return;

    var html = '';

    // Worst 상품
    if (worst && worst.length > 0) {
        html += '<div class="attention-section">';
        html += '<div class="attention-header attention-header-warn">이 상품들의 발주량을 점검해 주세요</div>';
        var top5 = worst.slice(0, 5);
        top5.forEach(function(w, i) {
            var avgPred = w.avg_predicted != null ? Math.round(w.avg_predicted) : '?';
            var avgAct = w.avg_actual != null ? Math.round(w.avg_actual) : '?';
            var diff = (w.bias || 0);
            var direction = diff > 0 ? '과잉' : '부족';
            var dirClass = diff > 0 ? 'over' : 'under';

            html += '<div class="attention-item">' +
                '<div class="attention-item-rank">' + (i + 1) + '</div>' +
                '<div class="attention-item-info">' +
                '<div class="attention-item-name">' + esc(w.item_nm || w.item_cd) + '</div>' +
                '<div class="attention-item-qty">예측 ' + avgPred + '개 &rarr; 실제 판매 ' + avgAct + '개</div>' +
                '</div>' +
                '<span class="attention-item-tag ' + dirClass + '">' + Math.abs(Math.round(diff)) + '개 ' + direction + '</span>' +
                '</div>';
        });
        html += '</div>';
    } else {
        html += '<div style="padding:16px;color:var(--text-muted)">주의 상품 없음</div>';
    }

    // Best 상품
    if (best && best.length > 0) {
        html += '<div class="attention-section" style="margin-top:16px">';
        html += '<div class="attention-header attention-header-good">잘 맞는 상품</div>';
        var top3 = best.slice(0, 3);
        var names = top3.map(function(b) { return esc(b.item_nm || b.item_cd); });
        html += '<div class="attention-best-list">' + names.join(', ') + '</div>';
        html += '</div>';
    }

    el.innerHTML = html;
}

// === 섹션 5: 알림 ===
function inferAlertType(text) {
    if (text.indexOf('위험') >= 0 || text.indexOf('주의') >= 0 || text.charAt(0) === '!')
        return 'warning';
    return 'info';
}

function renderAlerts(alerts) {
    var el = document.getElementById('predAlerts');
    if (!el) return;

    if (!alerts || alerts.length === 0) {
        el.style.display = 'block';
        el.innerHTML = '<div class="pred-alert-empty">✓ 현재 특이사항이 없습니다</div>';
        return;
    }

    el.style.display = 'block';
    var alertMap = {
        'MAPE': '전체 예측 정확도가 기준 이하입니다.',
        '과대예측': '재고가 쌓이고 있습니다. 발주량을 줄이는 것을 검토하세요.',
        '과소예측': '품절 위험이 있습니다. 발주량을 늘리는 것을 검토하세요.',
    };

    var html = '<div class="pred-alert-title">📋 시스템 알림</div>';
    var shown = alerts.slice(0, 5);
    shown.forEach(function(a) {
        // 1단계: raw text로 타입 추론
        var type = inferAlertType(a);
        var icon = type === 'warning' ? '⚠' : 'ℹ';
        var cls = type === 'warning' ? 'pred-alert-warn' : 'pred-alert-info';

        // 2단계: alertMap으로 friendly text 변환 (기존 로직 유지)
        var friendly = null;
        for (var key in alertMap) {
            if (a.indexOf(key) >= 0) { friendly = alertMap[key]; break; }
        }
        // alertMap 미매칭 시 "!" 접두사만 제거
        if (!friendly) {
            friendly = a.replace(/^!\s*/, '');
        }

        // 3단계: 타입별 스타일로 렌더링
        html += '<div class="pred-alert-card ' + cls + '">' +
            '<span class="pred-alert-icon">' + icon + '</span> ' +
            esc(friendly) + '</div>';
    });
    el.innerHTML = html;
}

// === Admin 전용 카드 (기존 유지) ===
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
