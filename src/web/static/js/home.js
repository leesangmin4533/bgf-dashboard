/* BGF 발주 시스템 - 홈 대시보드 */

// === 시간 포맷 유틸 ===
function formatTimeAgo(isoStr) {
    if (!isoStr) return '-';
    var d;
    if (isoStr.indexOf('T') > -1) {
        d = new Date(isoStr);
    } else {
        d = new Date(isoStr.replace(' ', 'T'));
    }
    if (isNaN(d.getTime())) return isoStr;

    var now = new Date();
    var diff = Math.floor((now - d) / 1000);

    if (diff < 0) return '방금 전';
    if (diff < 60) return diff + '초 전';
    if (diff < 3600) return Math.floor(diff / 60) + '분 전';
    if (diff < 86400) return Math.floor(diff / 3600) + '시간 전';
    return Math.floor(diff / 86400) + '일 전';
}

// === 카드 데이터 리셋 (로딩 상태) ===
function resetHomeCards() {
    // 스켈레톤 로딩 상태
    var skeletonValue = '<div class="skeleton skeleton-value"></div>';
    var skeletonText = '<div class="skeleton skeleton-text"></div>';

    // 발주 카드
    var orderVal = document.getElementById('homeOrderValue');
    var orderSub = document.getElementById('homeOrderSub');
    if (orderVal) orderVal.innerHTML = skeletonValue;
    if (orderSub) orderSub.innerHTML = skeletonText;

    // 폐기 위험 카드
    var expiryVal = document.getElementById('homeExpiryValue');
    var expirySub = document.getElementById('homeExpirySub');
    var expiryCard = document.getElementById('homeCardExpiry');
    if (expiryVal) expiryVal.innerHTML = skeletonValue;
    if (expirySub) expirySub.innerHTML = skeletonText;
    if (expiryCard) expiryCard.classList.remove('active', 'danger');

    // 스케줄러 카드
    var schedVal = document.getElementById('homeSchedulerValue');
    if (schedVal) schedVal.innerHTML = skeletonValue;

    window._expiryItems = [];
}

// === 데이터 로드 ===
async function loadHomeData() {
    try {
        var data = await api('/api/home/status?store_id=' + _currentStoreId);
        renderSchedulerCard(data.scheduler);
        renderOrderCard(data.last_order, data.today_summary);
        renderExpiryCard(data.expiry_risk);
        renderExpiryInline(data.expiry_risk);
        renderSalesCard(data.today_summary);
        renderPipeline(data.pipeline);
        renderFailReasons(data.fail_reasons);
        // 7일 스파크라인 차트
        if (data.order_trend_7d) {
            renderSparkline('homeOrderSpark', data.order_trend_7d);
            renderSparklineChart('sparkOrderChart', data.order_trend_7d, '#6366f1');
        }
        if (data.sales_trend_7d) {
            renderSparklineChart('sparkSalesChart', data.sales_trend_7d, '#22c55e');
            var last = data.sales_trend_7d[data.sales_trend_7d.length - 1];
            var sparkVal = document.getElementById('sparkSalesValue');
            if (sparkVal && last != null) sparkVal.textContent = fmt(last);
        }
        if (data.waste_trend_7d) {
            renderSparklineChart('sparkWasteChart', data.waste_trend_7d, '#ef4444');
            var last = data.waste_trend_7d[data.waste_trend_7d.length - 1];
            var sparkVal = document.getElementById('sparkWasteValue');
            if (sparkVal && last != null) sparkVal.textContent = fmt(last);
        }
        if (data.order_trend_7d) {
            var last = data.order_trend_7d[data.order_trend_7d.length - 1];
            var sparkVal = document.getElementById('sparkOrderValue');
            if (sparkVal && last != null) sparkVal.textContent = fmt(last);
        }
    } catch (e) {
        console.error('Home data load failed:', e);
    }
}

// === 오늘 매출 카드 ===
function renderSalesCard(summary) {
    var val = document.getElementById('homeSalesValue');
    var sub = document.getElementById('homeSalesSub');
    if (!val) return;
    if (summary && summary.total_qty > 0) {
        val.textContent = fmt(summary.total_qty) + '개';
        if (sub) sub.textContent = fmt(summary.order_items || 0) + '건';
    } else {
        val.textContent = '-';
        if (sub) sub.textContent = '';
    }
}

// === 폐기 인라인 리스트 (상위 5개) ===
function renderExpiryInline(ex) {
    var body = document.getElementById('homeExpiryListBody');
    if (!body) return;
    var items = (ex && ex.items) ? ex.items.slice(0, 5) : [];
    if (items.length === 0) {
        body.innerHTML = '<div class="hint">폐기 주의 상품이 없습니다</div>';
        return;
    }
    var now = new Date();
    var html = '<div class="expiry-inline-list">';
    items.forEach(function(it) {
        var timeText = '-';
        if (it.expiry_date) {
            var exp = new Date(it.expiry_date.replace(' ', 'T'));
            var diffMs = exp - now;
            if (diffMs <= 0) timeText = '만료됨';
            else {
                var h = Math.floor(diffMs / 3600000);
                if (h < 24) timeText = h + '시간 남음';
                else timeText = Math.floor(h / 24) + '일 남음';
            }
        }
        var urgencyClass = '';
        if (it.expiry_date) {
            var exp = new Date(it.expiry_date.replace(' ', 'T'));
            var diffH = (exp - now) / 3600000;
            if (diffH <= 0) urgencyClass = 'expiry-inline-danger';
            else if (diffH <= 6) urgencyClass = 'expiry-inline-critical';
            else if (diffH <= 12) urgencyClass = 'expiry-inline-warning';
        }
        html += '<div class="expiry-inline-item ' + urgencyClass + '">';
        html += '<span class="expiry-inline-name">' + (it.item_nm || '-') + '</span>';
        html += '<span class="expiry-inline-qty">' + (it.remaining_qty || 0) + '개</span>';
        html += '<span class="expiry-inline-time">' + timeText + '</span>';
        html += '</div>';
    });
    html += '</div>';
    body.innerHTML = html;
}

// === 7일 스파크라인 미니 차트 (Chart.js) ===
function renderSparklineChart(canvasId, values, color) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !values || values.length === 0) return;
    if (_charts[canvasId]) _charts[canvasId].destroy();
    var ctx = canvas.getContext('2d');
    _charts[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: values.map(function(_, i) { return ''; }),
            datasets: [{
                data: values,
                borderColor: color,
                backgroundColor: color + '20',
                borderWidth: 2,
                fill: true,
                pointRadius: 0,
                tension: 0.4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: {
                x: { display: false },
                y: { display: false }
            },
            animation: false,
        }
    });
}

// === 스케줄러 카드 ===
function renderSchedulerCard(s) {
    var card = document.getElementById('homeCardScheduler');
    var val = document.getElementById('homeSchedulerValue');
    var sub = document.getElementById('homeSchedulerSub');

    card.classList.remove('active', 'danger');
    if (s.running) {
        card.classList.add('active');
        val.textContent = '동작중';
        sub.innerHTML = 'PID: ' + s.pid + ' <a href="#" onclick="event.stopPropagation(); schedulerStop(); return false;" style="color:var(--danger);text-decoration:underline;margin-left:6px">중지</a>';
    } else {
        card.classList.add('danger');
        val.textContent = '정지됨';
        sub.innerHTML = '<a href="#" onclick="event.stopPropagation(); schedulerStart(); return false;" style="color:var(--success);text-decoration:underline">시작</a>';
    }
}

// === 스케줄러 시작/중지 ===
async function schedulerStart() {
    var sub = document.getElementById('homeSchedulerSub');
    var btn = document.getElementById('btnSchedulerStart');

    // 카드 로딩 표시
    if (sub) {
        sub.innerHTML = '<span class="spinner"></span> 시작중...';
    }

    // 버튼 로딩 표시
    if (btn) {
        var originalHTML = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span><span>시작중...</span>';
    }

    try {
        var res = await api('/api/home/scheduler/start', { method: 'POST' });
        if (res.error) {
            if (sub) sub.innerHTML = '<span style="color:var(--danger)">' + res.error + '</span>';
            showToast('시작 실패: ' + res.error, 'error');
        } else {
            await loadHomeData();
            // 모달이 열려있으면 자동 닫기
            var modal = document.getElementById('schedulerModal');
            if (modal && modal.style.display !== 'none') {
                closeSchedulerModal();
            }
        }
    } catch (e) {
        if (sub) sub.innerHTML = '<span style="color:var(--danger)">시작 실패</span>';
        showToast('시작 실패: 서버 오류', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        }
    }
}

async function schedulerStop() {
    var sub = document.getElementById('homeSchedulerSub');
    var btn = document.getElementById('btnSchedulerStop');

    // 카드 로딩 표시
    if (sub) {
        sub.innerHTML = '<span class="spinner"></span> 중지중...';
    }

    // 버튼 로딩 표시
    if (btn) {
        var originalHTML = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span><span>중지중...</span>';
    }

    try {
        var res = await api('/api/home/scheduler/stop', { method: 'POST' });
        if (res.error) {
            if (sub) sub.innerHTML = '<span style="color:var(--danger)">' + res.error + '</span>';
            showToast('중지 실패: ' + res.error, 'error');
        } else {
            await loadHomeData();
            // 모달이 열려있으면 자동 닫기
            var modal = document.getElementById('schedulerModal');
            if (modal && modal.style.display !== 'none') {
                closeSchedulerModal();
            }
        }
    } catch (e) {
        if (sub) sub.innerHTML = '<span style="color:var(--danger)">중지 실패</span>';
        showToast('중지 실패: 서버 오류', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        }
    }
}

// === 마지막 발주 카드 ===
function renderOrderCard(o, summary) {
    var card = document.getElementById('homeCardOrder');
    var val = document.getElementById('homeOrderValue');
    var sub = document.getElementById('homeOrderSub');
    var label = document.getElementById('homeOrderLabel');

    // 오늘 발주한 건(내일 입고) 우선 표시, 없으면 오늘 입고 예정 표시
    var hasOrdered = summary && summary.ordered_items > 0;
    var hasIncoming = summary && summary.order_items > 0;

    if (hasOrdered) {
        // 오늘 발주한 건 (메인)
        if (label) label.textContent = '오늘 발주';
        animateValue(val, summary.ordered_items, 600, '개');
        var parts = ['총 ' + fmt(summary.ordered_qty) + '개'];
        if (o.success_count > 0) parts.push(o.success_count + '건 성공');
        if (o.fail_count > 0) parts.push(o.fail_count + '건 실패');
        if (hasIncoming) {
            parts.push('입고 ' + fmt(summary.order_items) + '건');
        }
        sub.textContent = parts.join(' | ');
    } else if (hasIncoming) {
        // 오늘 입고 예정만 있는 경우
        if (label) label.textContent = '오늘 입고';
        animateValue(val, summary.order_items, 600, '개');
        var parts = ['총 ' + fmt(summary.total_qty) + '개 (입고)'];
        if (o.success_count > 0) parts.push(o.success_count + '건 성공');
        if (o.fail_count > 0) parts.push(o.fail_count + '건 실패');
        sub.textContent = parts.join(' | ');
    } else if (o.date) {
        val.textContent = o.date;
        var parts = [];
        if (o.time) parts.push(o.time);
        if (o.success_count > 0 || o.fail_count > 0) {
            parts.push(o.success_count + '/' + (o.success_count + o.fail_count) + ' 성공');
        }
        sub.textContent = parts.join(' | ');
    } else {
        val.textContent = '기록 없음';
        sub.textContent = '';
    }
}

// === 폐기 위험 카드 ===
function renderExpiryCard(ex) {
    var card = document.getElementById('homeCardExpiry');
    var val = document.getElementById('homeExpiryValue');
    var sub = document.getElementById('homeExpirySub');

    window._expiryItems = ex.items || [];

    card.classList.remove('active', 'danger');
    animateValue(val, ex.count, 600, '개');
    if (ex.count > 0) {
        card.classList.add('danger');
        var names = ex.items.slice(0, 3).map(function(i) { return i.item_nm; });
        sub.textContent = names.join(', ');
    } else {
        sub.textContent = '위험 상품 없음';
    }
}

// === 폐기 위험 모달 ===
function openExpiryModal() {
    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    var modal = document.getElementById('expiryModal');
    var body = document.getElementById('expiryModalBody');
    var items = window._expiryItems || [];

    if (items.length === 0) {
        body.innerHTML = '<div class="hint" style="text-align:center;padding:32px 0">폐기 위험 상품이 없습니다</div>';
        modal.style.display = 'flex';
        return;
    }

    var now = new Date();

    // 긴급도별 그룹핑 (더 직관적)
    var expired = [];      // 이미 만료됨
    var critical = [];     // 6시간 이내
    var warning = [];      // 12시간 이내
    var caution = [];      // 24시간 이내
    var normal = [];       // 그 외

    items.forEach(function(it) {
        if (!it.expiry_date) {
            normal.push(it);
            return;
        }
        var exp = new Date(it.expiry_date.replace(' ', 'T'));
        var diffMs = exp - now;
        var diffHours = diffMs / 3600000;

        if (diffMs <= 0) expired.push(it);
        else if (diffHours <= 6) critical.push(it);
        else if (diffHours <= 12) warning.push(it);
        else if (diffHours <= 24) caution.push(it);
        else normal.push(it);
    });

    // HTML 생성 - 긴급도 순서대로
    var html = '';

    // 요약 통계
    html += '<div class="expiry-summary">';
    html += '<div class="expiry-stat expiry-stat-danger"><span class="expiry-stat-num">' + expired.length + '</span><span>만료됨</span></div>';
    html += '<div class="expiry-stat expiry-stat-critical"><span class="expiry-stat-num">' + critical.length + '</span><span>6시간 이내</span></div>';
    html += '<div class="expiry-stat expiry-stat-warning"><span class="expiry-stat-num">' + warning.length + '</span><span>12시간 이내</span></div>';
    html += '<div class="expiry-stat expiry-stat-caution"><span class="expiry-stat-num">' + caution.length + '</span><span>24시간 이내</span></div>';
    html += '</div>';

    // 1. 만료된 상품 (빨강)
    if (expired.length > 0) {
        html += '<div class="expiry-group expiry-group-danger">';
        html += '<div class="expiry-group-header"><span class="expiry-badge expiry-badge-danger">만료됨</span><span>' + expired.length + '개 상품</span></div>';
        html += '<div class="expiry-items">';
        expired.forEach(function(it) {
            var qty = it.remaining_qty || 0;
            html += '<div class="expiry-item">';
            html += '<div class="expiry-item-name">' + esc(it.item_nm || '-') + '</div>';
            html += '<div class="expiry-item-info">';
            html += '<span class="expiry-item-cat">' + esc(it.cat_name || '-') + '</span>';
            html += '<span class="expiry-item-qty">' + qty + '개</span>';
            html += '</div>';
            html += '<div class="expiry-item-time expiry-item-time-danger">즉시 폐기 필요</div>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    // 2. 긴급 (6시간 이내, 빨강)
    if (critical.length > 0) {
        html += '<div class="expiry-group expiry-group-critical">';
        html += '<div class="expiry-group-header"><span class="expiry-badge expiry-badge-critical">긴급</span><span>' + critical.length + '개 상품</span></div>';
        html += '<div class="expiry-items">';
        critical.forEach(function(it) {
            var exp = new Date(it.expiry_date.replace(' ', 'T'));
            var diffMs = exp - now;
            var h = Math.floor(diffMs / 3600000);
            var m = Math.floor((diffMs % 3600000) / 60000);
            var qty = it.remaining_qty || 0;
            html += '<div class="expiry-item">';
            html += '<div class="expiry-item-name">' + esc(it.item_nm || '-') + '</div>';
            html += '<div class="expiry-item-info">';
            html += '<span class="expiry-item-cat">' + esc(it.cat_name || '-') + '</span>';
            html += '<span class="expiry-item-qty">' + qty + '개</span>';
            html += '</div>';
            html += '<div class="expiry-item-time expiry-item-time-critical">' + h + '시간 ' + m + '분 남음</div>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    // 3. 주의 (12시간 이내, 주황)
    if (warning.length > 0) {
        html += '<div class="expiry-group expiry-group-warning">';
        html += '<div class="expiry-group-header"><span class="expiry-badge expiry-badge-warning">주의</span><span>' + warning.length + '개 상품</span></div>';
        html += '<div class="expiry-items">';
        warning.forEach(function(it) {
            var exp = new Date(it.expiry_date.replace(' ', 'T'));
            var diffMs = exp - now;
            var h = Math.floor(diffMs / 3600000);
            var m = Math.floor((diffMs % 3600000) / 60000);
            var qty = it.remaining_qty || 0;
            html += '<div class="expiry-item">';
            html += '<div class="expiry-item-name">' + esc(it.item_nm || '-') + '</div>';
            html += '<div class="expiry-item-info">';
            html += '<span class="expiry-item-cat">' + esc(it.cat_name || '-') + '</span>';
            html += '<span class="expiry-item-qty">' + qty + '개</span>';
            html += '</div>';
            html += '<div class="expiry-item-time expiry-item-time-warning">' + h + '시간 ' + m + '분 남음</div>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    // 4. 확인 필요 (24시간 이내, 노랑)
    if (caution.length > 0) {
        html += '<div class="expiry-group expiry-group-caution">';
        html += '<div class="expiry-group-header"><span class="expiry-badge expiry-badge-caution">확인 필요</span><span>' + caution.length + '개 상품</span></div>';
        html += '<div class="expiry-items">';
        caution.forEach(function(it) {
            var exp = new Date(it.expiry_date.replace(' ', 'T'));
            var diffMs = exp - now;
            var h = Math.floor(diffMs / 3600000);
            var qty = it.remaining_qty || 0;
            html += '<div class="expiry-item">';
            html += '<div class="expiry-item-name">' + esc(it.item_nm || '-') + '</div>';
            html += '<div class="expiry-item-info">';
            html += '<span class="expiry-item-cat">' + esc(it.cat_name || '-') + '</span>';
            html += '<span class="expiry-item-qty">' + qty + '개</span>';
            html += '</div>';
            html += '<div class="expiry-item-time expiry-item-time-caution">' + h + '시간 남음</div>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    // 5. 일반 (24시간 이후)
    if (normal.length > 0) {
        html += '<div class="expiry-group expiry-group-normal">';
        html += '<div class="expiry-group-header"><span class="expiry-badge expiry-badge-normal">일반</span><span>' + normal.length + '개 상품</span></div>';
        html += '<div class="expiry-items">';
        normal.forEach(function(it) {
            var exp = it.expiry_date ? new Date(it.expiry_date.replace(' ', 'T')) : null;
            var diffMs = exp ? (exp - now) : 0;
            var h = exp ? Math.floor(diffMs / 3600000) : 0;
            var qty = it.remaining_qty || 0;
            html += '<div class="expiry-item">';
            html += '<div class="expiry-item-name">' + esc(it.item_nm || '-') + '</div>';
            html += '<div class="expiry-item-info">';
            html += '<span class="expiry-item-cat">' + esc(it.cat_name || '-') + '</span>';
            html += '<span class="expiry-item-qty">' + qty + '개</span>';
            html += '</div>';
            html += '<div class="expiry-item-time expiry-item-time-normal">' + (h > 0 ? h + '시간 남음' : '날짜 미상') + '</div>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    body.innerHTML = html;
    modal.style.display = 'flex';
    trapFocus(modal);
}

function closeExpiryModal(event) {
    if (event && event.target && event.target.id !== 'expiryModal' && !event.target.classList.contains('modal-close')) return;
    document.getElementById('expiryModal').style.display = 'none';
}


// === 스케줄러 작업 목록 모달 ===
async function openSchedulerModal() {
    var modal = document.getElementById('schedulerModal');
    var timeline = document.getElementById('schedulerTimeline');
    var statusBadge = document.getElementById('schedulerStatusBadge');
    var pidText = document.getElementById('schedulerPidText');
    var btnStart = document.getElementById('btnSchedulerStart');
    var btnStop = document.getElementById('btnSchedulerStop');

    modal.style.display = 'flex';
    timeline.innerHTML = '<div class="hint" style="text-align:center;padding:32px 0"><span class="spinner"></span> 로딩중...</div>';

    try {
        var data = await api('/api/home/scheduler/jobs');

        // 상태 업데이트
        if (data.running) {
            statusBadge.textContent = '동작중';
            statusBadge.className = 'status-badge running';
            pidText.textContent = 'PID: ' + data.pid;
            btnStart.style.display = 'none';
            btnStop.style.display = 'flex';
        } else {
            statusBadge.textContent = '정지됨';
            statusBadge.className = 'status-badge stopped';
            pidText.textContent = '스케줄러가 정지되어 있습니다';
            btnStart.style.display = 'flex';
            btnStop.style.display = 'none';
        }

        // 스케줄 렌더링 (오늘 스케줄만, 시간순)
        renderScheduleTimeline(data.jobs);
        trapFocus(modal);

    } catch (e) {
        timeline.innerHTML = '<div class="hint" style="text-align:center;padding:32px 0;color:var(--danger)">데이터 로드 실패</div>';
        console.error('Scheduler modal error:', e);
    }
}

// === 스케줄 타임라인 렌더링 ===
function renderScheduleTimeline(jobs) {
    var timeline = document.getElementById('schedulerTimeline');

    if (!jobs || jobs.length === 0) {
        timeline.innerHTML = '<div class="hint" style="text-align:center;padding:32px 0">등록된 스케줄이 없습니다</div>';
        return;
    }

    // 현재 시각
    var now = new Date();
    var nowHM = now.getHours() * 60 + now.getMinutes(); // 분 단위 변환

    // 오늘 실행 스케줄만 필터링 (매일 + 오늘이 월요일이면 주간)
    var isMonday = now.getDay() === 1;
    var todayJobs = jobs.filter(function(j) {
        if (j.freq === '매일') return true;
        if (j.freq === '매주 월' && isMonday) return true;
        return false;
    });

    // 시간순 정렬 (00:00 ~ 23:59)
    todayJobs.sort(function(a, b) {
        var aTime = parseTime(a.time);
        var bTime = parseTime(b.time);
        return aTime - bTime;
    });

    // HTML 생성
    var html = '';
    todayJobs.forEach(function(job) {
        var jobTime = parseTime(job.time);
        var isDone = jobTime < nowHM;
        var statusClass = isDone ? 'done' : 'upcoming';

        html += '<div class="schedule-item ' + statusClass + '">'
            + '<div class="schedule-time">' + job.time + '</div>'
            + '<div class="schedule-info">'
            + '<div class="schedule-name">' + job.name + '</div>'
            + '<div class="schedule-desc">' + job.desc + '</div>'
            + '</div>'
            + '<div class="schedule-freq">' + job.freq + '</div>'
            + '</div>';
    });

    timeline.innerHTML = html;
}

// === 시간 파싱 헬퍼 (HH:MM -> 분 단위) ===
function parseTime(timeStr) {
    var parts = timeStr.split(':');
    return parseInt(parts[0]) * 60 + parseInt(parts[1]);
}

function closeSchedulerModal(event) {
    if (event && event.target && event.target.id !== 'schedulerModal' && !event.target.classList.contains('modal-close')) return;
    document.getElementById('schedulerModal').style.display = 'none';
}

// === 미니 스파크라인 렌더링 ===
function renderSparkline(containerId, values) {
    var el = document.getElementById(containerId);
    if (!el || !values || values.length === 0) return;
    var max = Math.max.apply(null, values) || 1;
    var html = '';
    values.forEach(function(v, i) {
        var h = Math.max(Math.round((v / max) * 24), 2);
        var cls = i === values.length - 1 ? 'metric-spark-bar' : 'metric-spark-bar';
        html += '<div class="' + cls + '" style="height:' + h + 'px" title="' + v + '"></div>';
    });
    el.innerHTML = html;
}

// === 파이프라인 상태 ===
function renderPipeline(p) {
    if (!p) return;
    var steps = [
        { id: 'pipeCollect', timeId: 'pipeCollectTime', val: p.last_collection },
        { id: 'pipePrediction', timeId: 'pipePredictionTime', val: p.last_prediction },
        { id: 'pipeOrder', timeId: 'pipeOrderTime', val: p.last_order },
        { id: 'pipeEval', timeId: 'pipeEvalTime', val: p.last_evaluation },
    ];
    steps.forEach(function(s) {
        var el = document.getElementById(s.id);
        var timeEl = document.getElementById(s.timeId);
        if (!el || !timeEl) return;
        el.classList.remove('done', 'running');
        if (s.val) {
            el.classList.add('done');
            timeEl.textContent = formatTimeAgo(s.val);
        } else {
            timeEl.textContent = '-';
        }
    });
    // running 상태
    if (p.stage === 'running') {
        var collectEl = document.getElementById('pipeCollect');
        if (collectEl) {
            collectEl.classList.remove('done');
            collectEl.classList.add('running');
        }
    }
}

// === 최근 이벤트 ===
function renderEvents(events) {
    var body = document.getElementById('homeEventsBody');
    if (!body) return;
    if (!events || events.length === 0) {
        body.innerHTML = '<div class="hint">이벤트 없음</div>';
        return;
    }
    var html = '<div class="event-list">';
    events.forEach(function(ev) {
        var dotClass = ev.status === 'success' ? 'success' : (ev.status === 'warning' ? 'warning' : 'error');
        html += '<div class="event-item">';
        html += '<div class="event-dot ' + dotClass + '"></div>';
        html += '<div class="event-msg">' + ev.message + '</div>';
        html += '<div class="event-time">' + formatTimeAgo(ev.time) + '</div>';
        html += '</div>';
    });
    html += '</div>';
    body.innerHTML = html;
}

// === 발주 실패 사유 ===
function renderFailReasons(fr) {
    var panel = document.getElementById('homeFailPanel');
    if (!panel) return;
    if (!fr || fr.checked_count === 0) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = '';
    var countEl = document.getElementById('homeFailCount');
    if (countEl) countEl.textContent = fr.checked_count;

    var tbody = document.getElementById('homeFailTableBody');
    if (!tbody) return;
    var html = '';
    fr.items.forEach(function(it) {
        html += '<tr>';
        html += '<td>' + (it.item_nm || it.item_cd) + '</td>';
        html += '<td>' + (it.mid_cd || '-') + '</td>';
        html += '<td>' + (it.stop_reason || '-') + '</td>';
        html += '<td>' + (it.orderable_status || '-') + '</td>';
        html += '</tr>';
    });
    tbody.innerHTML = html;
    initPagination('homeFailTable', 25);
}

// ESC 키로 모달 닫기 → app.js 전역 ESC 핸들러로 통합

// === 초기 로드 ===
document.addEventListener('DOMContentLoaded', function() {
    console.log('[HOME] DOMContentLoaded fired');

    var homeTab = document.getElementById('tab-home');
    if (homeTab && homeTab.classList.contains('active')) {
        console.log('[HOME] Loading home data...');
        loadHomeData();
    } else {
        console.log('[HOME] Home tab not active or not found');
    }
});

// 강제 실행 (디버깅용)
setTimeout(function() {
    var homeTab = document.getElementById('tab-home');
    if (homeTab && homeTab.classList.contains('active')) {
        console.log('[HOME] Fallback: Loading home data after 1 second...');
        loadHomeData();
    }
}, 1000);
