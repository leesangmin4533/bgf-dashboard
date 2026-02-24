/* BGF 발주 시스템 - 발주 컨트롤 탭 */
// _currentStoreId는 app.js에서 글로벌로 관리

// === 예측 실행 ===
async function runPredict() {
    var btn = document.getElementById('btnPredict');
    var spinner = document.getElementById('predictSpinner');
    var status = document.getElementById('predictStatus');
    btn.disabled = true;
    spinner.style.display = '';
    status.textContent = '예측 실행 중...';

    try {
        var maxItems = parseInt(document.getElementById('maxItems').value) || 0;
        var data = await api('/api/order/predict?store_id=' + _currentStoreId, { method: 'POST', body: { max_items: maxItems } });

        if (data.error) {
            status.textContent = '오류: ' + data.error;
            status.className = 'status-text error';
            return;
        }

        status.textContent = '완료! ' + data.summary.ordered_count + '건 발주 대상';
        status.className = 'status-text';

        // 요약 카드
        renderCards('order-summary', [
            { value: fmt(data.summary.total_items), label: '총 상품수' },
            { value: fmt(data.summary.ordered_count), label: '발주 대상', type: 'good' },
            { value: fmt(data.summary.skipped_count), label: '스킵', type: 'warn' },
            { value: fmt(data.summary.total_order_qty), label: '총 발주량', type: 'accent' },
            { value: data.summary.category_count, label: '카테고리수' },
            { value: data.summary.avg_safety_stock, label: '평균 안전재고' },
        ]);

        // 카테고리 차트
        renderOrderCategoryChart(data.category_data);
        document.getElementById('order-chart-panel').style.display = '';

        // 발주 테이블
        renderOrderTable(data.items);
        document.getElementById('order-table-panel').style.display = '';
        initTableSearch('orderSearch', 'orderTable');
        initTableSort('orderTable');
        initPagination('orderTable', 25);

        // 스킵 테이블
        renderSkipTable(data.skipped);
        if (data.skipped.length > 0) {
            document.getElementById('skip-panel').style.display = '';
        }

        // 엑셀 다운로드 버튼 표시
        showExcelButtons();
    } catch (e) {
        status.textContent = '오류: ' + e.message;
        status.className = 'status-text error';
    } finally {
        btn.disabled = false;
        spinner.style.display = 'none';
    }
}

// === 카테고리 차트 ===
function renderOrderCategoryChart(catData) {
    getOrCreateChart('orderCategoryChart', {
        type: 'bar',
        data: {
            labels: catData.labels,
            datasets: [{
                label: '발주량',
                data: catData.order_qty,
                backgroundColor: getChartColors().blueA,
                hoverBackgroundColor: getChartColors().blue,
                borderRadius: 4,
            }]
        },
        options: {
            indexAxis: catData.labels.length > 10 ? 'y' : 'x',
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { color: getChartColors().grid } },
                y: { grid: { color: getChartColors().grid } }
            }
        }
    });
}

// === 발주 테이블 ===
function renderOrderTable(items) {
    var tbody = document.getElementById('orderTableBody');
    tbody.innerHTML = items.map(function(it) {
        return '<tr>' +
            '<td>' + it.item_nm + '</td>' +
            '<td>' + it.category + '</td>' +
            '<td class="text-right">' + it.daily_avg + '</td>' +
            '<td class="text-right">' + it.weekday_coef + '</td>' +
            '<td class="text-right">' + it.safety_stock + '</td>' +
            '<td class="text-right">' + fmt(it.current_stock) + '</td>' +
            '<td class="text-right">' + fmt(it.pending_qty) + '</td>' +
            '<td class="text-right"><input type="number" class="editable-qty" ' +
                'value="' + it.order_qty + '" data-item="' + it.item_cd + '" ' +
                'data-orig="' + it.order_qty + '" min="0"></td>' +
            '<td class="text-center">' + badgeHtml(it.confidence) + '</td>' +
        '</tr>';
    }).join('');

    // 수정 감지
    tbody.querySelectorAll('.editable-qty').forEach(function(input) {
        input.addEventListener('change', function() {
            if (parseInt(this.value) !== parseInt(this.dataset.orig)) {
                this.classList.add('modified');
            } else {
                this.classList.remove('modified');
            }
        });
    });
}

// === 스킵 테이블 ===
function renderSkipTable(skipped) {
    var tbody = document.getElementById('skipTableBody');
    tbody.innerHTML = skipped.map(function(s) {
        return '<tr>' +
            '<td>' + s.item_nm + '</td>' +
            '<td>' + s.category + '</td>' +
            '<td class="text-right">' + fmt(s.current_stock) + '</td>' +
            '<td class="text-right">' + fmt(s.pending_qty) + '</td>' +
            '<td class="text-right">' + s.safety_stock + '</td>' +
            '<td>' + s.reason + '</td>' +
        '</tr>';
    }).join('');
}

// === 발주 확정 ===
async function confirmOrder() {
    var modified = document.querySelectorAll('.editable-qty.modified');
    if (modified.length === 0) {
        showToast('수정된 발주량이 없습니다.', 'warning');
        return;
    }

    var adjustments = [];
    modified.forEach(function(input) {
        adjustments.push({
            item_cd: input.dataset.item,
            order_qty: parseInt(input.value) || 0
        });
    });

    var result = await api('/api/order/adjust?store_id=' + _currentStoreId, { method: 'POST', body: { adjustments: adjustments } });
    if (result.status === 'ok') {
        showToast(result.adjusted_count + '건 조정 완료. 총 발주량: ' + fmt(result.new_total_qty), 'success');
        modified.forEach(function(input) {
            input.dataset.orig = input.value;
            input.classList.remove('modified');
        });
    }
}

// === 발주 제외 ===
async function loadExclusions() {
    var data = await api('/api/order/exclusions?store_id=' + _currentStoreId);
    if (!data) return;

    // 점포 ID 표시
    setText('excludeStoreId', data.store_id || _currentStoreId);

    // 자동발주
    var auto = data.auto_order;
    var chkAuto = document.getElementById('chkAutoOrder');
    if (chkAuto) chkAuto.checked = auto.enabled;
    setText('autoOrderCount', auto.count + '건');
    setText('autoOrderInfo', auto.last_updated
        ? '갱신: ' + auto.last_updated
        : '캐시 없음');
    renderExcludeItems('autoOrderItems', auto.items || []);

    // 스마트발주
    var smart = data.smart_order;
    var chkSmart = document.getElementById('chkSmartOrder');
    if (chkSmart) chkSmart.checked = smart.enabled;
    setText('smartOrderCount', smart.count + '건');
    setText('smartOrderInfo', smart.last_updated
        ? '갱신: ' + smart.last_updated
        : '캐시 없음');
    renderExcludeItems('smartOrderItems', smart.items || []);

    // 총 건수
    var total = (auto.enabled ? auto.count : 0) + (smart.enabled ? smart.count : 0);
    setText('excludeTotalCount', total);
}

function renderExcludeItems(containerId, items) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (items.length === 0) {
        el.innerHTML = '<div class="empty-state">등록된 상품 없음</div>';
        return;
    }
    el.innerHTML = '<table class="exclude-table"><thead><tr>' +
        '<th>상품코드</th><th>상품명</th><th>중분류</th>' +
        '</tr></thead><tbody>' +
        items.map(function(item) {
            return '<tr><td>' + escapeHtml(item.item_cd) +
                '</td><td>' + escapeHtml(item.item_nm || '-') +
                '</td><td>' + escapeHtml(item.mid_cd || '-') + '</td></tr>';
        }).join('') + '</tbody></table>';
}

function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
}

// === 발주 컨트롤 서브탭 전환 ===
function initOrderSubTabs() {
    document.querySelectorAll('.order-sub-tab').forEach(function(tab) {
        tab.addEventListener('click', function(e) {
            e.preventDefault();
            var viewId = this.dataset.orderView;

            document.querySelectorAll('.order-sub-tab').forEach(function(t) { t.classList.remove('active'); });
            document.querySelectorAll('.order-view').forEach(function(v) { v.classList.remove('active'); });

            this.classList.add('active');
            var target = document.getElementById(viewId);
            if (target) target.classList.add('active');
        });
    });
}

// === 스크립트 실행 ===
var _scriptPollTimer = null;

function runScript(mode) {
    // 실제 발주는 확인 필수
    if (mode === 'real-order') {
        if (!confirm('실제 발주가 BGF 시스템에서 실행됩니다.\n정말 실행하시겠습니까?')) return;
    }

    var maxItems = parseInt(document.getElementById('testMaxItems').value) || 0;
    var minQty = parseInt(document.getElementById('testMinQty').value) || 0;

    // 실행 중 버튼 비활성화
    setTestButtonsDisabled(true);

    var logEl = document.getElementById('scriptLog');
    logEl.textContent = '[실행 시작] 모드: ' + mode + '\n';

    api('/api/order/run-script', {
        method: 'POST',
        body: { mode: mode, max_items: maxItems, min_qty: minQty, store_id: _currentStoreId }
    }).then(function(res) {
        if (res.error) {
            logEl.textContent += '[오류] ' + res.error + '\n';
            setTestButtonsDisabled(false);
            return;
        }
        logEl.textContent += '[PID ' + res.pid + '] 스크립트 시작됨\n';
        document.getElementById('btnStopScript').disabled = false;
        startPolling();
    }).catch(function(err) {
        logEl.textContent += '[오류] ' + err.message + '\n';
        setTestButtonsDisabled(false);
    });
}

function startPolling() {
    if (_scriptPollTimer) clearInterval(_scriptPollTimer);
    _scriptPollTimer = setInterval(pollScriptStatus, 2000);
}

function pollScriptStatus() {
    api('/api/order/script-status').then(function(res) {
        var logEl = document.getElementById('scriptLog');
        var elapsedEl = document.getElementById('scriptElapsed');

        if (res.output) {
            logEl.textContent = res.output;
            logEl.scrollTop = logEl.scrollHeight;
        }
        if (res.elapsed) {
            elapsedEl.textContent = res.elapsed + '초 경과';
        }

        if (!res.running) {
            clearInterval(_scriptPollTimer);
            _scriptPollTimer = null;
            setTestButtonsDisabled(false);
            document.getElementById('btnStopScript').disabled = true;

            if (res.exit_code !== null) {
                var status = res.exit_code === 0 ? '정상 완료' : '오류 종료 (코드: ' + res.exit_code + ')';
                logEl.textContent += '\n[' + status + '] (소요: ' + res.elapsed + '초)\n';
                logEl.scrollTop = logEl.scrollHeight;
            }
        }
    });
}

function stopScript() {
    api('/api/order/stop-script', { method: 'POST' }).then(function(res) {
        if (res.status === 'stopped') {
            var logEl = document.getElementById('scriptLog');
            logEl.textContent += '\n[사용자에 의해 중단됨]\n';
            logEl.scrollTop = logEl.scrollHeight;
        }
    });
}

function clearLog() {
    document.getElementById('scriptLog').textContent = '';
    document.getElementById('scriptElapsed').textContent = '';
}

function setTestButtonsDisabled(disabled) {
    document.querySelectorAll('#order-test .test-card .btn').forEach(function(btn) {
        btn.disabled = disabled;
    });
    // 부분 발주 버튼도 함께 제어
    var btnPreview = document.getElementById('btnPartialPreview');
    var btnExecute = document.getElementById('btnPartialExecute');
    if (btnPreview) btnPreview.disabled = disabled;
    if (btnExecute) btnExecute.disabled = disabled;
}

// === 부분 발주: 카테고리 선택 ===

var _partialSummaryTimer = null;

async function loadPartialCategories() {
    var grid = document.getElementById('partialCategoryGrid');
    if (!grid) return;
    try {
        var data = await api('/api/order/categories?store_id=' + _currentStoreId);
        var cats = data.categories || [];

        if (cats.length === 0) {
            grid.innerHTML = '<div class="hint">카테고리 없음</div>';
            return;
        }

        grid.innerHTML = cats.map(function(cat) {
            return '<div class="partial-order-item">' +
                '<label>' +
                    '<input type="checkbox" class="partial-cat-check" ' +
                        'value="' + cat.code + '" checked>' +
                    '<span class="cat-code">' + cat.code + '</span>' +
                    '<span>' + cat.name + '</span>' +
                '</label>' +
            '</div>';
        }).join('');

        // 체크박스 이벤트
        grid.querySelectorAll('.partial-cat-check').forEach(function(cb) {
            cb.addEventListener('change', function() {
                syncSelectAll();
                debouncePartialSummary();
            });
        });

        // 전체 선택 이벤트
        document.getElementById('partialSelectAll').addEventListener('change', toggleSelectAll);

        // 초기 요약
        debouncePartialSummary();

    } catch (e) {
        grid.innerHTML = '<div class="hint">카테고리 로드 실패</div>';
    }
}

function getSelectedCategories() {
    var checks = document.querySelectorAll('.partial-cat-check:checked');
    var codes = [];
    checks.forEach(function(cb) { codes.push(cb.value); });
    return codes;
}

function toggleSelectAll() {
    var allChecked = document.getElementById('partialSelectAll').checked;
    document.querySelectorAll('.partial-cat-check').forEach(function(cb) {
        cb.checked = allChecked;
    });
    debouncePartialSummary();
}

function syncSelectAll() {
    var all = document.querySelectorAll('.partial-cat-check');
    var checked = document.querySelectorAll('.partial-cat-check:checked');
    document.getElementById('partialSelectAll').checked = (all.length === checked.length);
}

function debouncePartialSummary() {
    if (_partialSummaryTimer) clearTimeout(_partialSummaryTimer);
    _partialSummaryTimer = setTimeout(updatePartialSummary, 300);
}

async function updatePartialSummary() {
    var categories = getSelectedCategories();
    var catCount = document.getElementById('partialCatCount');
    var itemCount = document.getElementById('partialItemCount');
    var qtyTotal = document.getElementById('partialQtyTotal');
    var summaryEl = document.getElementById('partialSummary');

    catCount.textContent = categories.length;

    if (categories.length === 0) {
        itemCount.textContent = '0';
        qtyTotal.textContent = '0';
        summaryEl.classList.add('empty');
        return;
    }

    summaryEl.classList.remove('empty');

    try {
        var data = await api('/api/order/partial-summary', {
            method: 'POST',
            body: { categories: categories, store_id: _currentStoreId }
        });

        if (data.error) {
            itemCount.textContent = '-';
            qtyTotal.textContent = '-';
            return;
        }

        itemCount.textContent = data.item_count + '/' + data.total_items;
        qtyTotal.textContent = fmt(data.total_qty) + '/' + fmt(data.all_qty);
    } catch (e) {
        itemCount.textContent = '-';
        qtyTotal.textContent = '-';
    }
}

function runPartialOrder(mode) {
    var categories = getSelectedCategories();
    if (categories.length === 0) {
        showToast('카테고리를 1개 이상 선택하세요.', 'warning');
        return;
    }

    // 실제 발주 확인
    if (mode === 'real-order') {
        var msg = '선택된 ' + categories.length + '개 카테고리로 실제 발주를 실행합니다.\n' +
                  '카테고리: ' + categories.join(', ') + '\n\n정말 실행하시겠습니까?';
        if (!confirm(msg)) return;
    }

    var maxItems = parseInt(document.getElementById('testMaxItems').value) || 0;
    var minQty = parseInt(document.getElementById('testMinQty').value) || 0;

    setTestButtonsDisabled(true);

    var logEl = document.getElementById('scriptLog');
    var modeLabel = mode === 'real-order' ? '부분 실제 발주' : '부분 드라이런';
    logEl.textContent = '[실행 시작] ' + modeLabel + ' (' + categories.length + '개 카테고리)\n';

    var statusEl = document.getElementById('partialStatus');
    statusEl.textContent = '실행중...';
    statusEl.className = 'status-text';

    api('/api/order/run-script', {
        method: 'POST',
        body: {
            mode: mode,
            max_items: maxItems,
            min_qty: minQty,
            categories: categories,
            store_id: _currentStoreId
        }
    }).then(function(res) {
        if (res.error) {
            logEl.textContent += '[오류] ' + res.error + '\n';
            statusEl.textContent = res.error;
            statusEl.className = 'status-text error';
            setTestButtonsDisabled(false);
            return;
        }
        logEl.textContent += '[PID ' + res.pid + '] 스크립트 시작됨\n';
        statusEl.textContent = '실행중 (PID: ' + res.pid + ')';
        document.getElementById('btnStopScript').disabled = false;
        startPolling();
    }).catch(function(err) {
        logEl.textContent += '[오류] ' + err.message + '\n';
        statusEl.textContent = err.message;
        statusEl.className = 'status-text error';
        setTestButtonsDisabled(false);
    });
}

// === 이벤트 바인딩 ===
document.addEventListener('DOMContentLoaded', function() {
    initOrderSubTabs();

    // 카테고리 + 제외목록 로드 (매장은 app.js에서 글로벌 관리)
    loadPartialCategories();
    loadExclusions();

    var btnPredict = document.getElementById('btnPredict');
    if (btnPredict) btnPredict.addEventListener('click', runPredict);

    var btnConfirmOrder = document.getElementById('btnConfirmOrder');
    if (btnConfirmOrder) btnConfirmOrder.addEventListener('click', confirmOrder);

    // 자동발주 제외 체크박스 토글
    var chkAutoOrder = document.getElementById('chkAutoOrder');
    if (chkAutoOrder) {
        chkAutoOrder.addEventListener('change', function() {
            api('/api/order/exclusions/toggle', {
                method: 'POST',
                body: { kind: 'auto', enabled: this.checked, store_id: _currentStoreId }
            });
        });
    }

    // 스마트발주 제외 체크박스 토글
    var chkSmartOrder = document.getElementById('chkSmartOrder');
    if (chkSmartOrder) {
        chkSmartOrder.addEventListener('change', function() {
            api('/api/order/exclusions/toggle', {
                method: 'POST',
                body: { kind: 'smart', enabled: this.checked, store_id: _currentStoreId }
            });
        });
    }

    // 발주 모드 전환
    var btnModePartial = document.getElementById('btnModePartial');
    var btnModeFull = document.getElementById('btnModeFull');
    if (btnModePartial) btnModePartial.addEventListener('click', switchOrderMode);
    if (btnModeFull) btnModeFull.addEventListener('click', switchOrderMode);

    // 부분발주 버튼
    var btnPartialPreview = document.getElementById('btnPartialPreview');
    var btnPartialExecute = document.getElementById('btnPartialExecute');
    if (btnPartialPreview) btnPartialPreview.addEventListener('click', function() { runPartialOrder('preview'); });
    if (btnPartialExecute) btnPartialExecute.addEventListener('click', function() { runPartialOrder('real-order'); });

    // 전체발주 버튼
    var btnFullPreview = document.getElementById('btnFullPreview');
    var btnFullExecute = document.getElementById('btnFullExecute');
    if (btnFullPreview) btnFullPreview.addEventListener('click', function() { runFullOrder('preview'); });
    if (btnFullExecute) btnFullExecute.addEventListener('click', function() { runFullOrder('real-order'); });

});

// === 발주 모드 전환 ===
function switchOrderMode(e) {
    var mode = e.currentTarget.dataset.mode;

    // 버튼 활성화 상태 변경
    document.querySelectorAll('.order-mode-btn').forEach(function(btn) {
        btn.classList.remove('active');
    });
    e.currentTarget.classList.add('active');

    // 섹션 표시/숨김
    var sectionPartial = document.getElementById('sectionPartial');
    var sectionFull = document.getElementById('sectionFull');

    if (mode === 'partial') {
        sectionPartial.style.display = 'block';
        sectionFull.style.display = 'none';
    } else {
        sectionPartial.style.display = 'none';
        sectionFull.style.display = 'block';
    }
}

// === 전체발주 실행 ===
async function runFullOrder(mode) {
    var isExecute = (mode === 'real-order');
    var btn = isExecute ? document.getElementById('btnFullExecute') : document.getElementById('btnFullPreview');

    if (isExecute) {
        var confirmed = confirm('전체 상품을 대상으로 실제 발주를 실행합니다.\n정말 실행하시겠습니까?');
        if (!confirmed) return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span>처리중...</span>';

    try {
        var result = await api('/api/order/predict?store_id=' + _currentStoreId, {
            method: 'POST',
            body: {
                max_items: 0,
                dry_run: !isExecute
            }
        });

        if (result.error) {
            showToast('오류: ' + result.error, 'error');
            return;
        }

        showOrderResult(result, isExecute);

    } catch (e) {
        showToast('발주 ' + (isExecute ? '실행' : '예측') + ' 실패: ' + e.message, 'error');
    } finally {
        var icon = isExecute ?
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>' :
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
        btn.disabled = false;
        btn.innerHTML = icon + '<span>' + (isExecute ? '발주 실행' : '미리보기') + '</span>';
    }
}

// === 발주 결과 표시 ===
function showOrderResult(result, isExecuted) {
    var modal = document.getElementById('orderResultModal');
    var title = document.getElementById('orderResultTitle');
    var summary = document.getElementById('orderResultSummary');
    var tbody = document.getElementById('orderTableBody');

    title.textContent = isExecuted ? '발주 실행 결과' : '발주 예측 결과';

    // 요약 통계
    var stats = result.summary || {};
    summary.innerHTML =
        '<div class="result-stat"><div class="result-stat-label">총 상품수</div><div class="result-stat-value">' + (stats.total_items || 0) + '</div></div>' +
        '<div class="result-stat"><div class="result-stat-label">발주 상품</div><div class="result-stat-value">' + (stats.order_items || 0) + '</div></div>' +
        '<div class="result-stat"><div class="result-stat-label">총 발주량</div><div class="result-stat-value">' + (stats.total_qty || 0) + '</div></div>' +
        '<div class="result-stat"><div class="result-stat-label">스킵</div><div class="result-stat-value">' + (stats.skip_items || 0) + '</div></div>';

    // 테이블 데이터
    var items = result.items || [];
    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">발주 내역이 없습니다</td></tr>';
    } else {
        tbody.innerHTML = items.map(function(item) {
            return '<tr>' +
                '<td>' + escapeHtml(item.item_name) + '</td>' +
                '<td>' + escapeHtml(item.cat_name) + '</td>' +
                '<td class="text-right">' + (item.daily_avg || 0).toFixed(1) + '</td>' +
                '<td class="text-right">' + (item.safety_stock || 0) + '</td>' +
                '<td class="text-right">' + (item.current_stock || 0) + '</td>' +
                '<td class="text-right">' + (item.order_qty || 0) + '</td>' +
                '<td class="text-center">' + (item.confidence || 'N/A') + '</td>' +
                '</tr>';
        }).join('');
    }

    modal.style.display = 'flex';
    showExcelButtons();
}

function closeOrderResultModal(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('orderResultModal').style.display = 'none';
}

// === 엑셀 다운로드 ===
async function exportPreviewExcel() {
    var btn = document.getElementById('btnExportExcel');
    var btnPartial = document.getElementById('btnPartialExcel');
    var btnFull = document.getElementById('btnFullExcel');

    // 버튼 비활성화 + 로딩 표시
    [btn, btnPartial, btnFull].forEach(function(b) {
        if (b) { b.disabled = true; b.style.opacity = '0.6'; }
    });
    if (btn) btn.textContent = '생성중...';

    try {
        var response = await fetch('/api/order/export-excel?store_id=' + _currentStoreId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });

        if (!response.ok) {
            var errData = await response.json().catch(function() { return {}; });
            showToast('엑셀 생성 실패: ' + (errData.error || response.statusText), 'error');
            return;
        }

        // Blob으로 다운로드
        var blob = await response.blob();
        var contentDisposition = response.headers.get('Content-Disposition') || '';
        var filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        var filename = filenameMatch ? filenameMatch[1].replace(/['"]/g, '') : 'order_preview.xlsx';

        var url = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

    } catch (e) {
        showToast('엑셀 다운로드 오류: ' + e.message, 'error');
    } finally {
        [btn, btnPartial, btnFull].forEach(function(b) {
            if (b) { b.disabled = false; b.style.opacity = ''; }
        });
        if (btn) {
            btn.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;margin-right:6px;">' +
                '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
                '<polyline points="7 10 12 15 17 10"/>' +
                '<line x1="12" y1="15" x2="12" y2="3"/>' +
                '</svg> 엑셀 다운로드';
        }
    }
}

// 미리보기 완료 후 엑셀 버튼 표시
function showExcelButtons() {
    var btnPartial = document.getElementById('btnPartialExcel');
    var btnFull = document.getElementById('btnFullExcel');
    if (btnPartial) btnPartial.style.display = '';
    if (btnFull) btnFull.style.display = '';
}

// Helper function
function escapeHtml(text) {
    if (!text) return '';
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
